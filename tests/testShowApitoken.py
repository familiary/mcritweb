#!/usr/bin/python
"""The API token is fetched when it is asked for, not printed into /settings.

The settings page printed the token, a secret that is the same in every response, so the
page was sent uncompressed (a BREACH attack reads such a secret out of a compressed
response's length) and with `Cache-Control: no-store`. That kept 40 KB and more of HTML
uncompressed on every visit, for a value most visits never look at. The page now holds a
"Show token" button, which POSTs to admin.show_apitoken, and only that small answer
carries the token.

CSRF protection is switched back on in this module, as in testCsrf.py: the route has to
refuse a request without this session's token, so that another site cannot have the
browser fetch it.
"""

import gzip
import logging
import re

import pytest

from mcritweb.csrf import ENABLED_KEY

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

ROUTE = "/admin/show_apitoken"
META_TOKEN = re.compile(r'<meta name="csrf-token" content="([0-9a-f]+)">')


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def app(app):
    app.config[ENABLED_KEY] = True
    return app


def page_csrf_token(client, path="/settings"):
    """The masked token a page hands its scripts, as the browser would read it."""
    page = client.get(path).get_data(as_text=True)
    return META_TOKEN.search(page).group(1)


def show(client, token, **kwargs):
    return client.post(ROUTE, headers={"X-CSRFToken": token, **kwargs.pop("headers", {})}, **kwargs)


# --- the page ------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["pending", "visitor", "contributor", "admin"])
def test_the_settings_page_holds_no_token(client, as_role, role):
    """The token was printed into the page, which kept the page uncompressed and
    out of every cache for as long as the token was on it."""
    as_role(role)
    page = client.get("/settings")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert f"apitoken-{role}" not in body
    if role != "pending":
        assert 'data-show-apitoken="/admin/show_apitoken"' in body
        assert re.search(r'<button type="button"[^>]*data-show-apitoken', body)


def test_the_settings_page_is_compressed_and_may_be_stored(client, as_role):
    """With nothing secret left on it, it is gzipped like every other page and loses
    the no-store that only a page with a secret needs."""
    as_role("contributor")
    page = client.get("/settings", headers={"Accept-Encoding": "gzip"})
    assert page.headers.get("Content-Encoding") == "gzip"
    assert "no-store" not in page.headers.get("Cache-Control", "")
    assert b"Show token" in gzip.decompress(page.get_data())


# --- the route -----------------------------------------------------------------------

def test_it_answers_the_callers_own_token(client, as_role, make_user):
    make_user(role="admin", username="someoneelse")
    as_role("contributor")
    response = show(client, page_csrf_token(client))
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.get_json() == {"apitoken": "apitoken-contributor"}
    assert b"apitoken-admin" not in response.get_data()


def test_it_answers_each_caller_with_their_own(app, make_user):
    """The user comes from the session, never from the request, so no caller can name
    another account."""
    from conftest import log_in_as
    visitor_id = make_user(role="visitor")
    admin_id = make_user(role="admin")
    for user_id, expected in ((visitor_id, "apitoken-visitor"), (admin_id, "apitoken-admin")):
        client = app.test_client()
        log_in_as(client, user_id)
        response = show(client, page_csrf_token(client), data={"user_id": visitor_id + admin_id - user_id})
        assert response.get_json() == {"apitoken": expected}


def test_it_is_stored_nowhere_and_never_compressed(client, as_role):
    as_role("contributor")
    response = show(client, page_csrf_token(client), headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers.get("Content-Encoding") is None


@pytest.mark.parametrize("token", [None, "", "a" * 64, "0" * 256])
def test_it_refuses_a_missing_or_wrong_csrf_token(client, as_role, token):
    """Without the check, a page on another site could have the browser POST here with
    the session cookie; the answer is not readable cross-origin, but a form posting
    into a frame of ours would show it."""
    as_role("contributor")
    client.get("/settings")
    response = client.post(ROUTE) if token is None else show(client, token)
    assert response.status_code == 400
    assert b"apitoken-contributor" not in response.get_data()


def test_it_never_answers_a_get(client, as_role):
    """A GET carries no CSRF token, and is what a link, an <img> or a prefetch makes."""
    as_role("contributor")
    response = client.get(ROUTE)
    assert response.status_code == 405
    assert b"apitoken-contributor" not in response.get_data()


def test_it_refuses_a_signed_out_caller(client, make_user):
    make_user(role="admin")
    response = show(client, page_csrf_token(client, "/login"))
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    assert b"apitoken-admin" not in response.get_data()


def test_it_refuses_an_account_that_cannot_use_the_api(client, as_role):
    """A pending account was never shown its token, and the API refuses it."""
    as_role("pending")
    response = show(client, page_csrf_token(client))
    assert response.status_code == 403
    assert b"apitoken-pending" not in response.get_data()


# --- a new token is shown at once ----------------------------------------------------

def test_a_regenerated_token_is_shown_on_the_page_it_leads_to(app, client, as_role):
    """The new token used to be on the settings page the regenerate button leads back to.
    That page now asks for it as soon as it has loaded, and only that once."""
    from mcritweb.db import UserInfo
    user_id = as_role("contributor")
    response = client.post("/admin/regenerate_apitoken", data={"csrf_token": page_csrf_token(client)})
    assert response.status_code == 302
    after = client.get("/settings").get_data(as_text=True)
    with app.app_context():
        new_token = UserInfo.fromDb(user_id=user_id).apitoken
    assert new_token != "apitoken-contributor"
    assert new_token not in after
    assert "data-show-now" in after
    assert "data-show-now" not in client.get("/settings").get_data(as_text=True)
    assert show(client, page_csrf_token(client)).get_json() == {"apitoken": new_token}
