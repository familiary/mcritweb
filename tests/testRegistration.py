#!/usr/bin/python
"""What a failed registration is allowed to reveal.

`/login` was stopped from confirming which accounts exist (see tests/testLogin.py), but
`/register` is just as anonymous and just as unthrottled, and it answered the same
question directly and by name: "User alice is already registered." for a taken name,
against a redirect to /login for a free one. Status code and body both differed, so a
caller could enumerate the user table one POST at a time from the other door.

Both outcomes now leave by the same door with the same message. Nothing is granted by
staying quiet: a new account is created `pending` and cannot be used until an
administrator approves it, so "submitted, wait for approval" is the honest instruction
either way. Issue #101.
"""

import logging
import unittest

import pytest
from werkzeug.security import generate_password_hash

from mcritweb.db import ServerInfo, UserInfo

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

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
    return "alice"


def register(client, username):
    return client.post("/register", data={
        "username": username,
        "inputPassword1": PASSWORD,
        "inputPassword2": PASSWORD,
        "registrationToken": "",
    })


def test_a_taken_username_answers_exactly_like_a_free_one(client, open_instance):
    taken = register(client, open_instance)
    free = register(client, "bob")

    assert taken.status_code == free.status_code == 302
    assert taken.headers["Location"] == free.headers["Location"]


def test_neither_answer_names_the_account(client, open_instance):
    taken = register(client, open_instance).get_data(as_text=True)
    free = register(client, "bob").get_data(as_text=True)

    for body in (taken, free):
        assert "already registered" not in body
        assert open_instance not in body


def test_the_flashed_message_is_the_same_either_way(client, open_instance):
    """The flash is what the next page shows, so it is part of the answer."""
    taken = client.post("/register", data={
        "username": open_instance, "inputPassword1": PASSWORD,
        "inputPassword2": PASSWORD, "registrationToken": "",
    }, follow_redirects=True).get_data(as_text=True)
    free = client.post("/register", data={
        "username": "bob", "inputPassword1": PASSWORD,
        "inputPassword2": PASSWORD, "registrationToken": "",
    }, follow_redirects=True).get_data(as_text=True)

    assert "An administrator has to approve" in taken
    assert "An administrator has to approve" in free


def test_a_free_username_really_does_create_the_account(client, app, open_instance):
    """Staying quiet must not turn into doing nothing."""
    register(client, "bob")

    with app.app_context():
        created = UserInfo.fromDb(username="bob")
        assert created is not None
        assert created.role == "pending"


def test_a_taken_username_does_not_touch_the_existing_account(client, app, open_instance):
    with app.app_context():
        before = UserInfo.fromDb(username=open_instance)
        before_password, before_role = before.password, before.role

    register(client, open_instance)

    with app.app_context():
        after = UserInfo.fromDb(username=open_instance)
        assert after.password == before_password
        assert after.role == before_role


def test_the_form_still_rejects_what_it_should(client, open_instance):
    """Only account existence is hidden. A malformed request is still told why - none
    of these answers depend on whether the username is in the table."""
    mismatch = client.post("/register", data={
        "username": "carol", "inputPassword1": PASSWORD,
        "inputPassword2": "something else", "registrationToken": "",
    }, follow_redirects=True).get_data(as_text=True)
    assert "passwords do not match" in mismatch

    bad_format = client.post("/register", data={
        "username": "x", "inputPassword1": PASSWORD,
        "inputPassword2": PASSWORD, "registrationToken": "",
    }, follow_redirects=True).get_data(as_text=True)
    assert "wrong format" in bad_format


if __name__ == "__main__":
    unittest.main()
