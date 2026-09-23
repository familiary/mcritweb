#!/usr/bin/python
"""Whose address the /login throttle actually meters, behind a reverse proxy.

The throttle added for issue #101 keys on `request.remote_addr`. Behind a proxy - and
the recommended deployment, docker-mcrit, puts NGINX in front - that is the proxy's
address on every single request, so every caller in the world shares one bucket: ten
failed logins from anywhere refuse the next login for everybody, and nothing is metered
per attacker. `TRUSTED_PROXY_COUNT` is the operator's statement of how many proxies are
in front of the app; werkzeug's ProxyFix then rewrites REMOTE_ADDR from
`X-Forwarded-For`.

Both directions of getting this wrong are failures, so both are pinned here:

* trusting nothing behind a proxy is the lockout above;
* trusting `X-Forwarded-For` without being told to is worse - the header is
  client-supplied until a proxy we trust has appended to it, so an attacker sends a
  fresh value per request and the throttle meters nothing at all, while a chosen value
  lands the failures on somebody else's address.

Hence the default of 0, and hence the counting-from-the-right test: ProxyFix takes the
Nth value from the *end* of the header, which is the only part of it a trusted proxy
wrote. The values to its left came from the client and are ignored.
"""

import logging

import pytest
from flask import request
from werkzeug.security import generate_password_hash

from mcritweb import MAX_TRUSTED_PROXY_COUNT, create_app, db
from mcritweb.db import ServerInfo, UserInfo, init_db

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PASSWORD = "correct horse battery staple"

#: The only peer a proxied deployment ever has. `request.remote_addr` is this on every
#: request, whoever sent it.
PROXY = "10.0.0.1"

ATTACKER = "203.0.113.7"
INNOCENT = "198.51.100.9"


def build_app(tmp_path, fake_mcrit, **overrides):
    """An app on a throwaway database, so a config value can be varied per test.

    Not the `app` fixture from conftest, because every test here needs a different
    TRUSTED_PROXY_COUNT and the fixture bakes its config in.
    """
    instance_path = tmp_path / "instance"
    instance_path.mkdir(exist_ok=True)
    config = {
        "DATABASE": str(tmp_path / "mcritweb.sqlite"),
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "WTF_CSRF_ENABLED": False,
        # a successful login redirects to the index, which queries the backend
        "MCRIT_CLIENT_FACTORY": lambda **kwargs: fake_mcrit,
        "MCRIT_SERVER_PROBE": lambda: True,
    }
    config.update(overrides)
    application = create_app(config, instance_path=str(instance_path))
    with application.app_context():
        init_db()
        server_info = ServerInfo()
        server_info.url = "http://127.0.0.1:8000"
        server_info.operation_mode = "multi"
        server_info.registration_token = ""
        server_info.server_token = ""
        server_info.server_uuid = "test-uuid"
        server_info.server_version = "test"
        server_info.saveToDb()
        user_info = UserInfo()
        user_info.username = "alice"
        user_info.password = generate_password_hash(PASSWORD)
        user_info.role = "visitor"
        user_info.apitoken = "apitoken-alice"
        user_info.saveToDb()
    return application


@pytest.fixture
def proxied(tmp_path, fake_mcrit):
    """What docker-mcrit runs: exactly one proxy, and the operator has said so."""
    return build_app(tmp_path, fake_mcrit, TRUSTED_PROXY_COUNT=1)


@pytest.fixture
def direct(tmp_path, fake_mcrit):
    """The shipped default: no proxy declared, so nothing about the request is trusted."""
    return build_app(tmp_path, fake_mcrit)


def attempt(client, password, forwarded_for=None, peer=PROXY, username="alice"):
    """One POST to /login, arriving from `peer` and carrying `forwarded_for`."""
    environ = {"REMOTE_ADDR": peer}
    if forwarded_for is not None:
        environ["HTTP_X_FORWARDED_FOR"] = forwarded_for
    return client.post(
        "/login",
        data={"username": username, "inputPassword": password},
        environ_base=environ,
        follow_redirects=True,
    ).get_data(as_text=True)


def burn_the_budget(client, forwarded_for=None, peer=PROXY, rotate=False):
    """Spend exactly the allowance, so the next attempt is the first refused one.

    `rotate` gives every attempt a different X-Forwarded-For, which is what an attacker
    does the moment the header is trusted without a proxy having written it.
    """
    for index in range(db.LOGIN_ATTEMPT_LIMIT):
        value = "203.0.113.%d" % index if rotate else forwarded_for
        attempt(client, "not the password", value, peer)


def recorded_addresses(application):
    with application.app_context():
        rows = db.get_db().execute(
            "SELECT remote_addr, COUNT(*) AS n FROM login_attempt GROUP BY remote_addr"
        ).fetchall()
    return {row["remote_addr"]: row["n"] for row in rows}


# --- (a) behind a proxy, with the hop count configured ---------------------------------


def test_the_failures_are_recorded_against_the_client_not_the_proxy(proxied):
    """The measurement this fix is about: ten failures put ten rows on the proxy's
    address and none on any real client."""
    burn_the_budget(proxied.test_client(), ATTACKER)

    assert recorded_addresses(proxied) == {ATTACKER: db.LOGIN_ATTEMPT_LIMIT}


def test_one_clients_failures_do_not_refuse_another_client(proxied):
    """The severe half: any internet user could otherwise lock the whole instance out of
    login for the length of the window, for ten requests."""
    burn_the_budget(proxied.test_client(), ATTACKER)

    page = attempt(proxied.test_client(), PASSWORD, INNOCENT)

    assert "Too many failed attempts" not in page, "an unrelated client was locked out"


def test_the_client_that_spent_the_budget_is_still_refused(proxied):
    """The other half: the throttle has to still throttle somebody."""
    client = proxied.test_client()
    burn_the_budget(client, ATTACKER)

    assert "Too many failed attempts" in attempt(client, "not the password", ATTACKER)


def test_rotating_the_forged_left_hand_entries_does_not_evade_the_throttle(proxied):
    """With one trusted proxy, everything left of the last value came from the client.
    An attacker prepending a fresh value per request must still meter as one address."""
    client = proxied.test_client()
    for index in range(db.LOGIN_ATTEMPT_LIMIT):
        attempt(client, "not the password", "192.0.2.%d, %s" % (index, ATTACKER))

    assert recorded_addresses(proxied) == {ATTACKER: db.LOGIN_ATTEMPT_LIMIT}
    assert "Too many failed attempts" in attempt(
        client, "not the password", "192.0.2.99, %s" % ATTACKER)


# --- the Nth value from the RIGHT, which is the whole point ----------------------------


def test_one_trusted_hop_takes_the_last_value(proxied):
    """Verified rather than assumed: with x_for=1 the address used is the rightmost one,
    the only entry the single trusted proxy wrote."""
    attempt(proxied.test_client(), "not the password", "192.0.2.1, 192.0.2.2, 203.0.113.7")

    assert recorded_addresses(proxied) == {"203.0.113.7": 1}


def test_two_trusted_hops_take_the_second_from_the_right(tmp_path, fake_mcrit):
    """Two proxies chained means the last entry is the inner proxy's view of the outer
    one, and the client is one place further left."""
    application = build_app(tmp_path, fake_mcrit, TRUSTED_PROXY_COUNT=2)

    attempt(application.test_client(), "not the password", "192.0.2.1, 203.0.113.7, 10.0.0.2")

    assert recorded_addresses(application) == {"203.0.113.7": 1}


def test_a_header_shorter_than_the_configured_hops_falls_back_to_the_peer(tmp_path, fake_mcrit):
    """Setting the count too high is a misconfiguration, and this is what it does: with
    fewer values than trusted, ProxyFix leaves REMOTE_ADDR alone, so the throttle is
    back to metering the proxy - the lockout, again. Pinned because the README says so."""
    application = build_app(tmp_path, fake_mcrit, TRUSTED_PROXY_COUNT=3)

    attempt(application.test_client(), "not the password", ATTACKER)

    assert recorded_addresses(application) == {PROXY: 1}


# --- (b) the default: trust nothing ----------------------------------------------------


def test_the_default_is_to_trust_no_proxy_at_all(tmp_path, fake_mcrit):
    assert build_app(tmp_path, fake_mcrit).config["TRUSTED_PROXY_COUNT"] == 0


def test_a_forged_header_does_not_evade_the_throttle_by_default(direct):
    """A directly served instance must not be made worse by this change. If
    X-Forwarded-For were trusted out of the box, a new value per request would be a new
    bucket and the throttle would stop existing."""
    client = direct.test_client()
    burn_the_budget(client, peer=ATTACKER, rotate=True)

    page = attempt(client, "not the password", "203.0.113.99", peer=ATTACKER)

    assert "Too many failed attempts" in page, "a forged header bought a fresh budget"
    assert recorded_addresses(direct) == {ATTACKER: db.LOGIN_ATTEMPT_LIMIT}


def test_a_forged_header_does_not_poison_another_address_by_default(direct):
    """The same forgery aimed the other way: pick a victim's address, spend their budget
    for them. The rows must land on the peer that actually connected."""
    burn_the_budget(direct.test_client(), forwarded_for=INNOCENT, peer=ATTACKER)

    assert INNOCENT not in recorded_addresses(direct)
    assert recorded_addresses(direct) == {ATTACKER: db.LOGIN_ATTEMPT_LIMIT}

    page = attempt(direct.test_client(), PASSWORD, forwarded_for=None, peer=INNOCENT)
    assert "Too many failed attempts" not in page, "the victim was throttled by a forged header"


#: What a request looks like once it has been through the trusted proxy: exactly one
#: X-Forwarded-Proto value, because NGINX *replaces* that header (`$scheme`) where it
#: *appends* to X-Forwarded-For (`$proxy_add_x_forwarded_for`). The two headers do not
#: have the same depth, which is why one hop count cannot drive both.
HTTPS = "https"


def scheme_seen_by_the_app(application, forwarded_proto=None, forwarded_for=None):
    """`request.scheme`, which is what every `url_for(..., _external=True)` is built on.

    admin_server.html builds the registration link that way, so a scheme that stays
    `http` behind a TLS-terminating proxy hands the admin an http:// invitation link.
    """
    @application.route("/testing/scheme-probe")
    def scheme_probe():
        return request.scheme

    environ = {"REMOTE_ADDR": PROXY}
    if forwarded_proto is not None:
        environ["HTTP_X_FORWARDED_PROTO"] = forwarded_proto
    if forwarded_for is not None:
        environ["HTTP_X_FORWARDED_FOR"] = forwarded_for
    return application.test_client().get(
        "/testing/scheme-probe", environ_base=environ).get_data(as_text=True)


# --- X-Forwarded-Proto is one value deep whatever the hop count ------------------------


def test_one_trusted_hop_takes_the_scheme_from_the_header(tmp_path, fake_mcrit):
    application = build_app(tmp_path, fake_mcrit, TRUSTED_PROXY_COUNT=1)

    assert scheme_seen_by_the_app(application, HTTPS) == "https"


def test_two_trusted_hops_still_take_the_scheme_from_the_header(tmp_path, fake_mcrit):
    """The defect this pins: X-Forwarded-Proto is replaced by each proxy, not appended,
    so it carries one value however long the chain is. Driving x_proto with the hop
    count asks for a second value that a correctly configured NGINX never writes, and
    ProxyFix answers by leaving the scheme alone - silently http, behind TLS."""
    application = build_app(tmp_path, fake_mcrit, TRUSTED_PROXY_COUNT=2)

    assert scheme_seen_by_the_app(application, HTTPS) == "https"


def test_the_scheme_header_is_ignored_when_no_proxy_is_trusted(tmp_path, fake_mcrit):
    """Same rule as the address: a directly served instance believes no header."""
    application = build_app(tmp_path, fake_mcrit)

    assert scheme_seen_by_the_app(application, HTTPS) == "http"


def test_trusting_the_scheme_does_not_loosen_the_address(tmp_path, fake_mcrit):
    """x_proto being pinned at one value must not quietly become the count for x_for:
    at two hops the client is still the second value from the right."""
    application = build_app(tmp_path, fake_mcrit, TRUSTED_PROXY_COUNT=2)

    attempt(application.test_client(), "not the password", "192.0.2.1, 203.0.113.7, 10.0.0.2")

    assert recorded_addresses(application) == {"203.0.113.7": 1}


# --- a setting that is not a hop count ---------------------------------------------------


@pytest.mark.parametrize("setting", ["one", None, -1, "", "2.0", 1.9, 0.5, True, False])
def test_a_setting_that_is_not_a_hop_count_trusts_nothing(tmp_path, fake_mcrit, setting):
    """A typo in instance/config.py must fail closed. Falling back to ProxyFix's own
    default here would be the opposite: it trusts one hop unless told otherwise.

    `True` is the one worth naming - an operator answering "yes, I am behind a proxy"
    writes it, and `int(True)` is 1, so a bare int() check lands on exactly the blind
    one-hop default. A bool says nothing about how many proxies there are.
    """
    application = build_app(tmp_path, fake_mcrit, TRUSTED_PROXY_COUNT=setting)

    assert application.config["TRUSTED_PROXY_COUNT"] == 0

    attempt(application.test_client(), "not the password", ATTACKER)
    assert recorded_addresses(application) == {PROXY: 1}


@pytest.mark.parametrize("setting", ["1", " 1 "])
def test_a_count_given_as_a_string_of_digits_still_counts(tmp_path, fake_mcrit, setting):
    """Environment-driven config arrives as text, and "1" plainly means one hop.
    Deliberate leniency: unlike a bool or a float, this value is unambiguous."""
    application = build_app(tmp_path, fake_mcrit, TRUSTED_PROXY_COUNT=setting)

    attempt(application.test_client(), "not the password", ATTACKER)
    assert recorded_addresses(application) == {ATTACKER: 1}


# --- a count too large to be a real proxy chain ------------------------------------------


@pytest.mark.parametrize("setting", [MAX_TRUSTED_PROXY_COUNT + 1, 10**9])
def test_an_absurd_count_is_refused_rather_than_installed(tmp_path, fake_mcrit, setting):
    """`x_for=1000000000` installs happily and then never matches a header, so every
    request falls back to the proxy address: the global lockout this branch exists to
    fix, restored silently. Refusing it puts the reason in the log instead."""
    application = build_app(tmp_path, fake_mcrit, TRUSTED_PROXY_COUNT=setting)

    assert application.config["TRUSTED_PROXY_COUNT"] == 0


def test_the_ceiling_still_clears_any_real_chain(tmp_path, fake_mcrit):
    """A CDN in front of a load balancer in front of NGINX is three. The ceiling is a
    guard against a typo, not a limit anyone should meet."""
    application = build_app(tmp_path, fake_mcrit, TRUSTED_PROXY_COUNT=MAX_TRUSTED_PROXY_COUNT)

    assert application.config["TRUSTED_PROXY_COUNT"] == MAX_TRUSTED_PROXY_COUNT
    assert MAX_TRUSTED_PROXY_COUNT >= 4


# --- a header werkzeug cannot parse ------------------------------------------------------


def test_an_unparseable_forwarded_header_falls_back_to_the_peer(proxied):
    """Accepted and documented rather than fixed.

    Werkzeug parses X-Forwarded-For with `parse_list_header`, not a split, so an
    unterminated quote makes the whole header parse to nothing and REMOTE_ADDR stays the
    proxy. The blast radius is the sender's own: they spend the proxy's bucket, which
    only ever refuses other senders of unparseable headers - a well-formed request
    resolves to its own address and is untouched, so this is not the lockout. Fixing it
    would mean re-parsing the header ourselves, in front of werkzeug, which is a worse
    trade than a documented footnote. Pinned so it stays a decision.
    """
    client = proxied.test_client()
    for _ in range(db.LOGIN_ATTEMPT_LIMIT):
        attempt(client, "not the password", '"evil, %s' % ATTACKER)

    assert recorded_addresses(proxied) == {PROXY: db.LOGIN_ATTEMPT_LIMIT}
    assert "Too many failed attempts" in attempt(client, "not the password", '"evil, %s' % ATTACKER)

    # and the honest client alongside them is not caught by it
    page = attempt(proxied.test_client(), PASSWORD, INNOCENT)
    assert "Too many failed attempts" not in page
