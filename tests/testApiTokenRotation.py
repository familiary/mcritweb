#!/usr/bin/python
"""How an API token is generated, and how it is replaced.

Generation used to be `md5(uuid4().bytes)`. The entropy was UUID4's rather than
MD5's, so tokens were never weak - but MD5 in an authentication path is a finding
every auditor writes up, and there was no reason to keep it.

Replacement did not exist at all. The settings page showed the token with a copy
button, and deleting the account was the only way to retire one - which is not
something you can ask of a person whose token has leaked. Issue #100.
"""

import logging
import re
import unittest

import pytest
from werkzeug.security import generate_password_hash

from mcritweb.db import APITOKEN_BYTES, UserInfo, generate_apitoken, get_user_by_apitoken

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


def _user(app, role="contributor", username="rotator"):
    with app.app_context():
        user_info = UserInfo()
        user_info.username = username
        user_info.password = generate_password_hash("password")
        user_info.role = role
        user_info.apitoken = generate_apitoken()
        user_info.saveToDb()
        return UserInfo.fromDb(username=username)


# --- generation ------------------------------------------------------------------

def test_a_generated_token_is_hex_of_the_declared_length():
    token = generate_apitoken()

    assert re.fullmatch(r"[0-9a-f]+", token), "hex, so it survives a header and a URL"
    assert len(token) == APITOKEN_BYTES * 2


def test_two_generated_tokens_differ():
    assert len({generate_apitoken() for _ in range(64)}) == 64


def test_a_registration_issues_a_token_of_the_new_shape(client, app):
    """The whole point is that the *route* stopped using MD5, not just the helper."""
    # an existing account, so this is an ordinary registration rather than the
    # first-user one, which also wants the whole server configuration in the form
    _user(app, role="admin", username="theadmin")

    response = client.post("/register", data={
        "username": "tokenuser",
        "inputPassword1": "a-password",
        "inputPassword2": "a-password",
        "registrationToken": "",
    })
    assert response.status_code == 302, response.get_data(as_text=True)

    with app.app_context():
        user_info = UserInfo.fromDb(username="tokenuser")

    assert user_info is not None
    assert len(user_info.apitoken) == APITOKEN_BYTES * 2, "an md5 hexdigest would be 32"


# --- rotation --------------------------------------------------------------------

def test_rotating_replaces_the_callers_token(client, app):
    before = _user(app)
    with client.session_transaction() as session:
        session["user_id"] = before.user_id

    response = client.post("/admin/regenerate_apitoken")

    assert response.status_code == 302
    with app.app_context():
        after = UserInfo.fromDb(user_id=before.user_id)
    assert after.apitoken != before.apitoken
    assert len(after.apitoken) == APITOKEN_BYTES * 2


def test_the_old_token_stops_authenticating(client, app):
    """Rotation that leaves the old token working is not rotation."""
    before = _user(app)
    with client.session_transaction() as session:
        session["user_id"] = before.user_id
    client.post("/admin/regenerate_apitoken")

    with app.app_context():
        assert get_user_by_apitoken(before.apitoken) is None
        after = UserInfo.fromDb(user_id=before.user_id)
        assert get_user_by_apitoken(after.apitoken) == before.user_id


def test_rotating_does_not_touch_anybody_else(client, app):
    """The user id comes from the session, so there is nothing in the request that can
    point at another account - this is the test that says so.

    It has to actually try. The first version posted an empty body and then checked the
    other account was untouched, which proves nothing about what the request *could*
    point at: rewrite the view as `request.form.get("user_id", get_session_user_id())`
    - a real IDOR - and an empty post still leaves the other row alone. So this sends
    every field name the view might plausibly read.
    """
    mine = _user(app, username="mine")
    theirs = _user(app, username="theirs")
    with client.session_transaction() as session:
        session["user_id"] = mine.user_id

    client.post("/admin/regenerate_apitoken", data={
        "user_id": theirs.user_id,
        "id": theirs.user_id,
        "username": "theirs",
        "apitoken": theirs.apitoken,
    })

    with app.app_context():
        assert UserInfo.fromDb(user_id=theirs.user_id).apitoken == theirs.apitoken, \
            "a request field steered the rotation at another account"
        assert UserInfo.fromDb(user_id=mine.user_id).apitoken != mine.apitoken, \
            "and the caller's own token still rotated"


@pytest.mark.parametrize("role", ["visitor", "contributor", "admin"])
def test_everyone_whose_token_works_can_see_and_replace_it(client, app, role):
    """token_required admits visitor, contributor and admin. The settings page used to
    show the token to the last two only, so a visitor whose token leaked - and it works
    against every read route - had no way to retire it short of deleting the account,
    which is exactly what this PR exists to stop being the only option."""
    user = _user(app, username=f"{role}user", role=role)
    with client.session_transaction() as session:
        session["user_id"] = user.user_id

    page = client.get("/settings").get_data(as_text=True)

    assert user.apitoken in page, f"a {role} cannot see the token the API accepts"
    assert "regenerate_apitoken" in page, f"a {role} cannot replace it"


def test_a_pending_user_is_shown_no_token(client, app):
    """The other side: token_required refuses pending, so there is nothing to show."""
    user = _user(app, username="pendinguser", role="pending")
    with client.session_transaction() as session:
        session["user_id"] = user.user_id

    page = client.get("/settings").get_data(as_text=True)

    assert user.apitoken not in page
    assert "regenerate_apitoken" not in page


def test_a_get_cannot_rotate_a_token(client, app):
    """POST-only, per AGENTS.md: a GET that writes can be fired by an <img> tag."""
    before = _user(app)
    with client.session_transaction() as session:
        session["user_id"] = before.user_id

    response = client.get("/admin/regenerate_apitoken")

    assert response.status_code == 405
    with app.app_context():
        assert UserInfo.fromDb(user_id=before.user_id).apitoken == before.apitoken


def test_an_anonymous_caller_cannot_rotate_anything(client, app):
    before = _user(app)

    response = client.post("/admin/regenerate_apitoken")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    with app.app_context():
        assert UserInfo.fromDb(user_id=before.user_id).apitoken == before.apitoken


def test_a_token_issued_by_an_older_version_still_authenticates(app):
    """Tokens already in an existing database are 32-char md5 hexdigests. Nothing
    validates a token's shape - the lookup is an equality match - so they keep
    working, and this is the test that stops someone adding a length check later."""
    legacy = "0123456789abcdef0123456789abcdef"
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "legacyholder"
        user_info.password = generate_password_hash("password")
        user_info.role = "contributor"
        user_info.apitoken = legacy
        user_info.saveToDb()

        assert get_user_by_apitoken(legacy) == UserInfo.fromDb(username="legacyholder").user_id


def test_the_settings_page_offers_the_button(client, app):
    before = _user(app)
    with client.session_transaction() as session:
        session["user_id"] = before.user_id

    page = client.get("/settings").get_data(as_text=True)

    assert "/admin/regenerate_apitoken" in page


if __name__ == "__main__":
    unittest.main()
