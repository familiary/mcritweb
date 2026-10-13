#!/usr/bin/python
"""A password change ends the account's other sessions.

The session is Flask's signed cookie, and nothing on the server tied it to the password
it was issued for. A copy of the cookie - from a shared machine, a proxy log, a browser
profile - kept working after its owner changed the password, for as long as Flask accepts
the signature (PERMANENT_SESSION_LIFETIME, 31 days). Logging out clears only the browser's
own copy, so that is still the case for logout: there is no server-side session to end.
"""

import hashlib

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from mcritweb.db import UserInfo

OLD_PASSWORD = "old password, long enough"
NEW_PASSWORD = "new password, long enough"


@pytest.fixture
def carol(app, make_user):
    make_user(role="admin", username="boss")
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "carol"
        user_info.password = generate_password_hash(OLD_PASSWORD)
        user_info.role = "visitor"
        user_info.apitoken = "apitoken-carol"
        user_info.saveToDb()
        return UserInfo.fromDb(username="carol").user_id


def logged_in(app, password=OLD_PASSWORD):
    client = app.test_client()
    response = client.post("/login", data={"username": "carol", "inputPassword": password})
    assert response.status_code == 302 and "/login" not in response.headers["Location"]
    return client


def copy_of(app, client):
    copy = app.test_client()
    copy.set_cookie("session", client.get_cookie("session").value)
    return copy


def is_logged_in(client):
    response = client.get("/settings")
    return response.status_code == 200 and "carol" in response.get_data(as_text=True)


def change_password(client):
    return client.post("/admin/change_password", data={
        "inputPassword2": OLD_PASSWORD, "inputPassword3": NEW_PASSWORD, "inputPassword4": NEW_PASSWORD,
    })


def test_a_copied_session_ends_when_the_password_changes(app, carol):
    owner = logged_in(app)
    copy = copy_of(app, owner)
    assert is_logged_in(copy)

    change_password(owner)

    assert not is_logged_in(copy)


def test_another_login_ends_when_the_password_changes(app, carol):
    laptop = logged_in(app)
    phone = logged_in(app)

    change_password(laptop)

    assert not is_logged_in(phone)
    # and logging in again with the new password works there
    assert is_logged_in(logged_in(app, NEW_PASSWORD))


def test_the_session_that_changed_the_password_stays_logged_in(app, carol):
    owner = logged_in(app)

    response = change_password(owner)

    assert response.status_code == 302
    assert is_logged_in(owner)
    with app.app_context():
        assert check_password_hash(UserInfo.fromDb(username="carol").password, NEW_PASSWORD)


def test_a_session_from_before_the_mark_is_marked_and_then_ends_too(app, carol):
    """A cookie issued before this change carries only user_id. It keeps working - every
    user would otherwise be logged out by the upgrade - and is marked on its next request,
    so a later password change ends it like any other."""
    older = app.test_client()
    with older.session_transaction() as old_session:
        old_session["user_id"] = carol
    assert is_logged_in(older)

    change_password(logged_in(app))

    assert not is_logged_in(older)


def test_the_cookie_carries_nothing_to_test_password_guesses_against(app, carol):
    """The cookie is signed, not encrypted: anyone holding it can read it."""
    owner = logged_in(app)
    with owner.session_transaction() as owner_session:
        mark = owner_session.get("password_mark")
    with app.app_context():
        stored_hash = UserInfo.fromDb(username="carol").password

    assert mark
    assert stored_hash not in mark
    assert mark != hashlib.sha256(stored_hash.encode()).hexdigest()
