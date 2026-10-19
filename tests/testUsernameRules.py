#!/usr/bin/python
"""Which names registration accepts.

The format check ended in $, which in Python also matches before a final newline, so
"admin\\n" passed it and, compared whole, was not in the reserved list either. And the
UNIQUE column compares exactly, so "Alice" could be registered next to "alice". Both put
lookalike names into the approval list and onto labels. The rename side is in
tests/testSettingsPages.py.
"""

import pytest
from werkzeug.security import generate_password_hash

from mcritweb.db import ServerInfo, UserInfo, get_db

PASSWORD = "correct horse battery staple"


@pytest.fixture
def open_instance(app):
    """A configured instance with one account, and registration open to anyone."""
    with app.app_context():
        server_info = ServerInfo.fromDb()
        server_info.registration_token = ""
        server_info.saveToDb()

        user_info = UserInfo()
        user_info.username = "alice"
        user_info.password = generate_password_hash(PASSWORD)
        user_info.role = "visitor"
        user_info.apitoken = "apitoken-alice"
        user_info.saveToDb()


def register(client, username, **kwargs):
    return client.post("/register", data={
        "username": username, "inputPassword1": PASSWORD,
        "inputPassword2": PASSWORD, "registrationToken": "",
    }, **kwargs)


def usernames(app):
    with app.app_context():
        return [row["username"] for row in get_db().execute("SELECT username FROM user ORDER BY id")]


@pytest.mark.parametrize("username", ["bob\n", "admin\n", "alice\n"])
def test_a_name_with_a_trailing_newline_is_refused(client, app, open_instance, username):
    page = register(client, username, follow_redirects=True).get_data(as_text=True)

    assert "wrong format" in page
    assert usernames(app) == ["alice"]


def test_a_name_that_differs_only_in_case_is_treated_as_taken(client, app, open_instance):
    taken = register(client, "Alice")
    free = register(client, "bob")

    assert usernames(app) == ["alice", "bob"]
    # answered exactly like any other taken name, so it does not tell who exists
    assert (taken.status_code, taken.headers.get("Location")) == (free.status_code, free.headers.get("Location"))


@pytest.mark.parametrize("username", ["bob", "b.o_b", "a1b", "x" * 20, "Alice.Smith"])
def test_well_formed_names_are_still_registered(client, app, open_instance, username):
    register(client, username)

    assert usernames(app) == ["alice", username]


@pytest.mark.parametrize("username", ["ab", "x" * 21, ".bob", "bob_", "b..ob", "b_.ob", "bob\r", "\nbob",
                                      "bo b", "b\u00f8b"])
def test_malformed_names_are_still_refused(client, app, open_instance, username):
    page = register(client, username, follow_redirects=True).get_data(as_text=True)

    assert "wrong format" in page
    assert usernames(app) == ["alice"]
