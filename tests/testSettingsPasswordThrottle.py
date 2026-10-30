#!/usr/bin/python
"""The settings forms that ask for the current password are metered like /login.

Changing the username and changing the password both check the current password, and
neither counted a wrong one. Whoever held a stolen or copied session could guess at the
password there without limit, while /login stopped them after LOGIN_ATTEMPT_LIMIT tries
(issue #101), and a password learnt that way may open more than this account.
"""

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from mcritweb import db
from mcritweb.db import UserInfo

PASSWORD = "correct horse battery staple"
HERE = {"REMOTE_ADDR": "203.0.113.7"}


@pytest.fixture
def alice(app):
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "alice"
        user_info.password = generate_password_hash(PASSWORD)
        user_info.role = "contributor"
        user_info.apitoken = "apitoken-alice"
        user_info.saveToDb()
    return "alice"


def signed_in(app):
    client = app.test_client()
    response = client.post("/login", data={"username": "alice", "inputPassword": PASSWORD}, environ_base=HERE)
    assert response.status_code == 302 and "/login" not in response.headers["Location"]
    return client


def change_password(client, current):
    return client.post("/admin/change_password", environ_base=HERE, follow_redirects=True, data={
        "inputPassword2": current, "inputPassword3": "a new password, long enough", "inputPassword4": "a new password, long enough",
    }).get_data(as_text=True)


def change_username(client, current):
    return client.post("/admin/change_username", environ_base=HERE, follow_redirects=True, data={
        "username": "alice2", "inputPassword1": current,
    }).get_data(as_text=True)


FORMS = {"change_password": change_password, "change_username": change_username}


def failures(app, username="alice"):
    with app.app_context():
        return db.count_recent_login_failures(HERE["REMOTE_ADDR"], username)


def alice_now(app):
    with app.app_context():
        return UserInfo.fromDb(username="alice")


@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_wrong_current_password_counts_as_a_failed_login(app, alice, form):
    client = signed_in(app)
    page = FORMS[form](client, "not the password")
    assert "Incorrect" in page
    assert failures(app) == 1


@pytest.mark.parametrize("form", sorted(FORMS))
def test_guessing_here_spends_the_budget_login_has(app, alice, form):
    client = signed_in(app)
    for _ in range(db.LOGIN_ATTEMPT_LIMIT):
        FORMS[form](client, "not the password")
    # the next guess is refused before the password is looked at, the right one included
    assert "Too many failed attempts" in FORMS[form](client, PASSWORD)
    user_info = alice_now(app)
    assert user_info is not None and check_password_hash(user_info.password, PASSWORD)
    assert user_info.apitoken == "apitoken-alice"
    # and the shared budget stops /login from that address too
    login = app.test_client().post("/login", data={"username": "alice", "inputPassword": PASSWORD},
                                   environ_base=HERE, follow_redirects=True).get_data(as_text=True)
    assert "Too many failed attempts" in login


@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_spent_budget_from_login_also_closes_the_settings_forms(app, alice, form):
    client = signed_in(app)
    for _ in range(db.LOGIN_ATTEMPT_LIMIT):
        app.test_client().post("/login", data={"username": "alice", "inputPassword": "not the password"}, environ_base=HERE)
    assert "Too many failed attempts" in FORMS[form](client, PASSWORD)
    user_info = alice_now(app)
    assert user_info is not None and check_password_hash(user_info.password, PASSWORD)


@pytest.mark.parametrize("form", sorted(FORMS))
def test_the_right_password_clears_the_accounts_failures_as_a_login_does(app, alice, form):
    client = signed_in(app)
    FORMS[form](client, "not the password")
    assert failures(app) == 1
    assert "successfully changed" in FORMS[form](client, PASSWORD)
    assert failures(app) == 0
