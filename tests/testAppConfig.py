#!/usr/bin/python
"""Two request-size ceilings that `create_app` sets, and one cookie flag.

`SESSION_COOKIE_SECURE` keeps the cookie that is the entire proof of identity off a
plaintext connection. It is on unless the app is in debug, because `flask_env.sh` sets
FLASK_DEBUG=1 for a local run over plain HTTP, where a secure-only cookie is never sent
back and login fails with nothing to point at.

`MAX_CONTENT_LENGTH` makes Werkzeug reject an oversized body with 413 *before* buffering
it. Its value is deliberately generous - it applies to every route uniformly, and
`/data/import` takes whole-corpus exports - so `QUERY_UPLOAD_LIMITS` carries the
per-role cap that issue #19 asked to make configurable. That cap used to be a literal
`1 * 2**20` in `analyze.query`, written twice: once in the comparison and once in the
message.

Blind spot worth stating: Werkzeug's test client does not withhold a `Secure` cookie
over http, so the rest of the suite passes whether the flag is set or not. Only
`test_the_flag_reaches_the_set_cookie_header` actually proves it lands on the wire.
"""

import io
import logging

import pytest

from mcritweb import create_app
from mcritweb.db import ServerInfo, init_db

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The shipped default for the one role the default mapping caps.
VISITOR_LIMIT = 1 * 2**20


def build_app(tmp_path, **overrides):
    """A minimal app, so a config default can be read without the fixture stack."""
    instance_path = tmp_path / "instance"
    instance_path.mkdir(exist_ok=True)
    config = {
        "DATABASE": str(tmp_path / "mcritweb.sqlite"),
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "MCRIT_SERVER_PROBE": lambda: True,
    }
    config.update(overrides)
    application = create_app(config, instance_path=str(instance_path))
    # `load_logged_in_user` runs as a before_request on every route, so an uninitialised
    # database fails ahead of whatever the test is actually about
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
    return application


# --- the session cookie ----------------------------------------------------------------


def test_the_session_cookie_is_https_only_by_default(tmp_path):
    assert build_app(tmp_path).config["SESSION_COOKIE_SECURE"] is True


def test_debug_relaxes_it_so_a_local_run_can_log_in(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_DEBUG", "1")
    assert build_app(tmp_path).config["SESSION_COOKIE_SECURE"] is False


def test_an_explicit_setting_wins(tmp_path):
    """For an operator serving plain HTTP behind something we did not anticipate."""
    assert build_app(tmp_path, SESSION_COOKIE_SECURE=False).config["SESSION_COOKIE_SECURE"] is False


def test_the_flag_reaches_the_set_cookie_header(client, make_user):
    """A config value is only worth having if it lands on the wire. `as_role` writes the
    session directly, so this logs in for real to get a Set-Cookie to look at."""
    make_user("visitor", username="cookieuser")
    response = client.post("/login", data={"username": "cookieuser", "inputPassword": "password"})

    cookies = [value for header, value in response.headers if header == "Set-Cookie"]
    assert cookies, "logging in set no cookie, so this test is watching nothing"
    assert all("Secure" in cookie for cookie in cookies), cookies


# --- the request body ceiling ----------------------------------------------------------


def test_a_request_body_ceiling_is_set(tmp_path):
    ceiling = build_app(tmp_path).config["MAX_CONTENT_LENGTH"]
    assert ceiling is not None and ceiling > 0


def test_the_ceiling_clears_a_realistic_corpus_export(tmp_path):
    """/data/import takes a whole-corpus export. A ceiling that blocks one is a
    regression dressed as hardening."""
    assert build_app(tmp_path).config["MAX_CONTENT_LENGTH"] >= 512 * 2**20


def test_an_oversized_body_is_refused_before_it_is_read(tmp_path):
    """413 from Werkzeug, rather than a 500 or a silently buffered gigabyte."""
    app = build_app(tmp_path, MAX_CONTENT_LENGTH=1024, WTF_CSRF_ENABLED=False)
    response = app.test_client().post("/login", data={"payload": "x" * 4096})
    assert response.status_code == 413


# --- the per-role query cap ------------------------------------------------------------


def post_query(client, size):
    """POST to the query dropzone the way the browser does: one file part plus the
    fields the `sending` handler copies in beside it."""
    return client.post(
        "/analyze/query",
        data={
            "options": "unmapped",
            "file": (io.BytesIO(b"M" * size), "sample.exe"),
        },
        content_type="multipart/form-data",
    )


def test_a_visitor_upload_over_the_configured_cap_is_refused(client, as_role):
    as_role("visitor")
    assert post_query(client, VISITOR_LIMIT + 1).status_code == 403


def test_a_visitor_upload_under_the_cap_is_not_refused_by_it(client, as_role):
    as_role("visitor")
    assert post_query(client, 128).status_code != 403


@pytest.mark.parametrize("role", ["contributor", "admin"])
def test_a_role_absent_from_the_mapping_is_uncapped(client, as_role, role):
    """The default mapping names `visitor` only, so nothing else hits a per-role cap."""
    as_role(role)
    assert post_query(client, VISITOR_LIMIT + 1).status_code != 403


def test_the_cap_is_the_config_value_and_not_a_literal(app, client, as_role):
    """The point of #19: an operator raising the number changes the behaviour. Two
    literals used to encode it - the comparison and the flashed message."""
    app.config["QUERY_UPLOAD_LIMITS"] = {"visitor": 64}
    as_role("visitor")

    assert post_query(client, 128).status_code == 403
    assert post_query(client, 32).status_code != 403


def test_removing_the_mapping_uncaps_every_role(app, client, as_role):
    app.config["QUERY_UPLOAD_LIMITS"] = {}
    as_role("visitor")
    assert post_query(client, VISITOR_LIMIT + 1).status_code != 403


# --- the same cap on the API's query routes --------------------------------------------

#: Every router branch that hands an uploaded body to a backend query.
API_QUERY_PATHS = ["query/binary", "query/binary/mapped/4194304", "query/function"]


def post_api_query(client, path, size, role):
    """POST a body to the API the way a script holding `role`'s token does. Past the
    gate the strict fake answers plain values, which `handle_raw_response` cannot read,
    so a request that was let through may end in an exception - that counts as 'not
    refused', exactly as in testApiTokens.py."""
    try:
        return client.post(f"/api/{path}", data=b"M" * size, headers={"apitoken": f"apitoken-{role}"}).status_code
    except Exception:
        return "dispatched"


@pytest.mark.parametrize("path", API_QUERY_PATHS)
def test_a_visitor_token_over_the_cap_is_refused_by_the_api_too(client, make_user, fake_mcrit, path):
    """The API used to have no such check, so the same account that the web form
    refused at 1 MiB could queue a query of up to MAX_CONTENT_LENGTH with its token."""
    make_user("visitor")

    assert post_api_query(client, path, VISITOR_LIMIT + 1, "visitor") == 403
    assert not [call for call in fake_mcrit.calls if "Matches" in call[0]], "the oversized query reached the backend"


@pytest.mark.parametrize("path", API_QUERY_PATHS)
def test_a_visitor_token_under_the_cap_is_not_refused_by_it(client, make_user, path):
    make_user("visitor")
    assert post_api_query(client, path, 128, "visitor") != 403


@pytest.mark.parametrize("path", API_QUERY_PATHS)
def test_a_contributor_token_is_not_capped_by_the_api(client, make_user, path):
    make_user("contributor")
    assert post_api_query(client, path, VISITOR_LIMIT + 1, "contributor") != 403


def test_an_oversized_declared_body_is_refused_without_being_read(client, make_user, monkeypatch):
    """The cost of a refusal is the body it read. A request that declares its length
    is turned away on the header alone."""
    from werkzeug.wrappers import Request

    reads = []
    real_get_data = Request.get_data
    monkeypatch.setattr(Request, "get_data", lambda self, *args, **kwargs: reads.append(1) or real_get_data(self, *args, **kwargs))
    make_user("visitor")

    assert post_api_query(client, "query/binary", VISITOR_LIMIT + 1, "visitor") == 403
    assert not reads, "the body was read before the cap was applied"


def test_a_body_with_no_declared_length_is_measured_by_reading_it(client, make_user):
    """A chunked body declares nothing, so there is no header to go by. The test client
    always declares a length, so the declaration is blanked in the environ."""
    make_user("visitor")
    chunked = {"CONTENT_LENGTH": "", "HTTP_TRANSFER_ENCODING": "chunked", "wsgi.input_terminated": True}

    over = client.post("/api/query/binary", data=b"M" * (VISITOR_LIMIT + 1),
                       headers={"apitoken": "apitoken-visitor"}, environ_overrides=chunked)

    assert over.status_code == 403


def test_a_body_with_no_declared_length_is_not_read_past_the_cap(client, make_user):
    """Measuring a chunked body must not mean reading all of it: up to MAX_CONTENT_LENGTH
    would land in memory before the answer. Where the stream stops is how far it was read."""
    import io

    make_user("visitor")
    body = io.BytesIO(b"M" * (8 * VISITOR_LIMIT))
    chunked = {"CONTENT_LENGTH": "", "HTTP_TRANSFER_ENCODING": "chunked", "wsgi.input_terminated": True,
               "wsgi.input": body}

    over = client.post("/api/query/binary", headers={"apitoken": "apitoken-visitor"}, environ_overrides=chunked)

    assert over.status_code == 403
    assert body.tell() <= 2 * VISITOR_LIMIT, f"read {body.tell()} bytes of a capped body"
