#!/usr/bin/python
"""An empty password is refused when it is set and when it is used.

Registration refused an empty password, but a password change only compared the new
password with its confirmation: pressing Confirm with both empty set the account's password
to the empty string, and from then on anyone who knew the name signed in with an empty
password field.
"""

import re

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from mcritweb.db import UserInfo
from mcritweb.views import authentication

OLD_PASSWORD = "old password, long enough"


@pytest.fixture
def carol(app):
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "carol"
        user_info.password = generate_password_hash(OLD_PASSWORD)
        user_info.role = "contributor"
        user_info.apitoken = "apitoken-carol"
        user_info.saveToDb()
        return UserInfo.fromDb(username="carol").user_id


def signed_in(app, password=OLD_PASSWORD, username="carol"):
    client = app.test_client()
    response = client.post("/login", data={"username": username, "inputPassword": password})
    return client, response.status_code == 302 and "/login" not in response.headers.get("Location", "/login")


def test_an_empty_new_password_is_refused_and_nothing_changes(app, carol):
    client, ok = signed_in(app)
    assert ok
    response = client.post("/admin/change_password", data={"inputPassword2": OLD_PASSWORD, "inputPassword3": "", "inputPassword4": ""},
                           follow_redirects=True)
    assert "Password is required." in response.get_data(as_text=True)
    with app.app_context():
        user_info = UserInfo.fromDb(username="carol")
    assert check_password_hash(user_info.password, OLD_PASSWORD), "the password was changed"
    assert user_info.apitoken == "apitoken-carol", "the API token was replaced"
    assert signed_in(app, password="")[1] is False


def test_an_account_holding_an_empty_password_does_not_sign_in_with_it(app, carol):
    """An account a password change emptied before it was refused."""
    with app.app_context():
        user_info = UserInfo.fromDb(username="carol")
        user_info.password = generate_password_hash("")
        user_info.saveToDb()
    client, ok = signed_in(app, password="")
    assert not ok
    assert client.get("/settings").status_code != 200


def test_an_empty_password_costs_the_check_an_unknown_name_does(app, carol, monkeypatch):
    """So the time the refusal takes does not tell a name that exists from one that does not."""
    spent = []
    monkeypatch.setattr(authentication, "_spend_a_password_check", lambda password: spent.append(password))
    for username in ("carol", "nobody"):
        assert signed_in(app, password="", username=username)[1] is False
    assert spent == ["", ""]


def test_the_new_password_fields_are_required(client, as_role):
    as_role("contributor")
    page = client.get("/settings").get_data(as_text=True)
    for field in ("inputPassword2", "inputPassword3", "inputPassword4"):
        tag = re.search(rf'<input[^>]*id="{field}"[^>]*>', page).group(0)
        assert re.search(r"\srequired[\s>]", tag), tag
