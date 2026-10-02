#!/usr/bin/python
"""The pre-1.5.0 (32-character) API tokens are visible, movable and retirable.

1.5.0 left the tokens issued before it valid: the entropy was always UUID4's, so
they were never weak. But nothing ever moved them on - a 32-character token stayed
valid until its owner happened to press regenerate, nothing told them to, and an
admin could not see how many were left without querying the user table. Issue #250.

The length is the discriminator: `md5(uuid4().bytes)` is always 32 hex characters,
`secrets.token_hex(APITOKEN_BYTES)` always 64. No migration, no extra column.
"""

import logging
import re

import pytest
from werkzeug.security import generate_password_hash

from mcritweb.db import APITOKEN_BYTES, UserInfo, generate_apitoken, is_legacy_apitoken

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

LEGACY_TOKEN = "0123456789abcdef0123456789abcdef"  # what md5(uuid4().bytes) produced


def _user(app, role="contributor", username="tokenuser", apitoken=None):
    """Create an account with a specific token and return its id."""
    with app.app_context():
        user_info = UserInfo()
        user_info.username = username
        user_info.password = generate_password_hash("password")
        user_info.role = role
        user_info.apitoken = apitoken if apitoken is not None else generate_apitoken()
        user_info.saveToDb()
        return UserInfo.fromDb(username=username).user_id


def _token_of(app, username):
    with app.app_context():
        return UserInfo.fromDb(username=username).apitoken


def _login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id


# --- the discriminator -----------------------------------------------------------

def test_a_32_hex_character_token_is_legacy():
    assert is_legacy_apitoken(LEGACY_TOKEN)


def test_a_generated_token_is_not_legacy():
    assert not is_legacy_apitoken(generate_apitoken())


@pytest.mark.parametrize("not_a_token", [
    None,
    "",                      # the header's default when it is absent
    "no login",              # the placeholder a fresh row is inserted with
    "apitoken-admin",        # what the test fixtures invent
    "0123456789ABCDEF0123456789ABCDEF",  # md5 hexdigests are lowercase
    "0123456789abcdef0123456789abcdez",  # not hex
])
def test_something_that_was_never_issued_is_not_legacy(not_a_token):
    assert not is_legacy_apitoken(not_a_token)


# --- the API gate ----------------------------------------------------------------

def _verdict(call):
    """403 when authorization refused, ALLOWED otherwise.

    Anything that gets past the gate dies downstream: the fakes return plain values
    where handle_raw_response wants a real requests.Response. That death is the
    success case, as in testApiTokens.py.
    """
    try:
        response = call()
    except Exception:
        return "allowed"
    return response.status_code if response.status_code == 403 else "allowed"


def test_a_legacy_token_is_accepted_by_default(client, app):
    _user(app, apitoken=LEGACY_TOKEN)
    assert _verdict(lambda: client.get("/api/version", headers={"apitoken": LEGACY_TOKEN})) == "allowed"


def test_a_legacy_token_is_refused_with_an_explanation_once_retired(client, app):
    _user(app, apitoken=LEGACY_TOKEN)
    app.config["ACCEPT_LEGACY_APITOKENS"] = False
    response = client.get("/api/version", headers={"apitoken": LEGACY_TOKEN})

    assert response.status_code == 403
    message = response.get_json()["error"]
    assert "1.5.0" in message and "regenerate" in message.lower()


def test_an_unknown_token_still_gets_the_bare_refusal(client, app):
    """The explanation is for an owner whose token authenticates; it must not
    confirm to a stranger which token shapes the deployment understands."""
    _user(app, apitoken=LEGACY_TOKEN)
    app.config["ACCEPT_LEGACY_APITOKENS"] = False
    response = client.get("/api/version", headers={"apitoken": "f" * 32})

    assert response.status_code == 403
    assert response.get_json() is None, "the bare 403 an unknown token always got"


def test_a_fresh_token_still_works_after_the_cutover(client, app):
    _user(app, apitoken=generate_apitoken())
    app.config["ACCEPT_LEGACY_APITOKENS"] = False
    assert _verdict(lambda: client.get("/api/version", headers={"apitoken": _token_of(app, "tokenuser")})) == "allowed"


# --- the owner is told -----------------------------------------------------------

def test_the_settings_page_warns_about_a_legacy_token(client, app):
    _login(client, _user(app, role="admin", username="legacyowner", apitoken=LEGACY_TOKEN))
    response = client.get("/settings")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "issued before 1.5.0" in body
    assert "regenerate" in body.lower()


def test_the_settings_page_stays_quiet_about_a_fresh_token(client, app):
    _login(client, _user(app, role="admin", username="freshowner"))
    response = client.get("/settings")

    assert response.status_code == 200
    assert "issued before 1.5.0" not in response.get_data(as_text=True)


# --- the admin is told -----------------------------------------------------------

def test_the_users_page_counts_and_marks_legacy_holders(client, app):
    _login(client, _user(app, role="admin", username="theadmin"))
    _user(app, role="visitor", username="legacyholder", apitoken=LEGACY_TOKEN)
    _user(app, role="visitor", username="freshholder")

    response = client.get("/admin/users/")
    body = response.get_data(as_text=True)

    assert "1 account still holds a pre-1.5.0 (32-character) API token" in body
    # a per-row marker on the holder's row, keyed to the account so the admin can
    # tell which one without reading tokens
    assert body.count("fa-solid fa-key") >= 1


def test_the_users_page_is_quiet_when_nobody_holds_one(client, app):
    _login(client, _user(app, role="admin", username="theadmin"))
    _user(app, role="visitor", username="freshholder")

    response = client.get("/admin/users/")
    assert "pre-1.5.0 (32-character)" not in response.get_data(as_text=True)


# --- the admin can retire them ----------------------------------------------------

def test_regenerating_legacy_tokens_moves_only_those(client, app):
    _login(client, _user(app, role="admin", username="theadmin"))
    _user(app, role="visitor", username="legacyholder", apitoken=LEGACY_TOKEN)
    fresh_token = generate_apitoken()
    _user(app, role="visitor", username="freshholder", apitoken=fresh_token)

    response = client.post("/admin/regenerate_legacy_apitokens", follow_redirects=True)
    body = response.get_data(as_text=True)

    assert "1 pre-1.5.0 API token(s) were regenerated" in body
    rotated = _token_of(app, "legacyholder")
    assert len(rotated) == APITOKEN_BYTES * 2, "the legacy token was replaced"
    assert re.fullmatch(r"[0-9a-f]+", rotated)
    assert _token_of(app, "freshholder") == fresh_token, "a current token was left alone"


def test_regenerating_with_no_legacy_holders_says_so(client, app):
    _login(client, _user(app, role="admin", username="theadmin"))
    _user(app, role="visitor", username="freshholder")

    response = client.post("/admin/regenerate_legacy_apitokens", follow_redirects=True)
    assert "No account holds a pre-1.5.0 API token" in response.get_data(as_text=True)


def test_a_non_admin_cannot_rotate_everyones_token(client, app):
    """Rotating tokens is an outage for whoever uses them - admin only."""
    _login(client, _user(app, role="contributor", username="nottheadmin"))
    _user(app, role="visitor", username="legacyholder", apitoken=LEGACY_TOKEN)

    response = client.post("/admin/regenerate_legacy_apitokens")

    assert response.status_code == 403
    assert _token_of(app, "legacyholder") == LEGACY_TOKEN, "the token survived"
