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
import sqlite3
import threading
import unittest

import pytest
from conftest import hold_each_write_for_the_other
from werkzeug.security import generate_password_hash

from mcritweb.db import ServerInfo, UserInfo, get_db

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


# --- two first registrations at once ------------------------------------------------
#
# Whether a registration is the first one is decided before the view runs, and the
# password is hashed before anything is written. Two registrations on an instance nobody
# has set up yet could both pass the check, and both used to be saved as admin, with the
# server settings of whichever wrote last.

def first_user_form(username, url, registration_token=""):
    return {
        "username": username, "inputPassword1": PASSWORD, "inputPassword2": PASSWORD,
        "url": url, "operationMode": "multi", "setRegistrationToken": registration_token, "mcritServerToken": "",
    }


def register_both_at_once(app, monkeypatch, forms):
    """Post `forms` from one thread each, all held in the password hash until every one
    has got there, so that every request has passed the first-user check before any of
    them writes.

    A check the view makes again is held too, for up to a second, until the other
    requests have reached it as well. A view that asks again without a lock is then
    answered "still empty" in every thread; one that holds the lock keeps the others
    out, and goes on once the second has passed."""
    from mcritweb import db
    from mcritweb.views import authentication

    barrier = threading.Barrier(len(forms), timeout=10)

    def hash_once_all_are_here(password):
        barrier.wait()
        return generate_password_hash(password)

    is_first_user = db.is_first_user
    checks = threading.local()
    second_asks = threading.Barrier(len(forms), timeout=1)

    def is_first_user_held_on_the_second_ask():
        checks.count = getattr(checks, "count", 0) + 1
        answer = is_first_user()
        if checks.count == 2:
            try:
                second_asks.wait()
            except threading.BrokenBarrierError:
                # the others are kept out by a lock, or already gave up waiting here
                pass
        return answer

    monkeypatch.setattr(authentication, "generate_password_hash", hash_once_all_are_here)
    monkeypatch.setattr(db, "is_first_user", is_first_user_held_on_the_second_ask)
    responses = {}

    def post(form):
        client = app.test_client()
        responses[form["username"]] = (client, client.post("/register", data=form))

    threads = [threading.Thread(target=post, args=(form,)) for form in forms]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return responses


def test_two_first_registrations_make_one_admin(app, monkeypatch):
    responses = register_both_at_once(app, monkeypatch, [
        first_user_form("operator", "http://127.0.0.1:8001", "token-operator"),
        first_user_form("other", "http://127.0.0.1:8002", "token-other"),
    ])

    with app.app_context():
        users = [(row["username"], row["role"]) for row in get_db().execute("SELECT username, role FROM user")]
        server_url = ServerInfo.fromDb().url
    assert len(users) == 1, users
    admin = users[0][0]
    assert users[0][1] == "admin"
    # the settings are the admin's own, not the other request's
    assert server_url == {"operator": "http://127.0.0.1:8001", "other": "http://127.0.0.1:8002"}[admin]

    loser = "other" if admin == "operator" else "operator"
    assert "/login" in responses[admin][1].headers["Location"]
    # the other request is sent back to /register, which now shows the ordinary form,
    # asking for the registration token the admin just set, instead of the setup form
    loser_client, loser_response = responses[loser]
    assert loser_response.status_code == 302 and loser_response.headers["Location"].endswith("/register")
    page = loser_client.get("/register").get_data(as_text=True)
    assert "set up by another registration" in page
    assert 'name="registrationToken"' in page or "name='registrationToken'" in page
    assert "Define Server Details" not in page


def test_the_server_settings_are_kept_only_with_their_admin(app, client, monkeypatch):
    """Both rows are written under the one lock, and committed together: settings
    committed on their own would let the lock go before the admin row exists."""
    def refuse(self, withPassword=False):
        raise sqlite3.IntegrityError("refused")

    monkeypatch.setattr(UserInfo, "saveToDb", refuse)
    client.post("/register", data=first_user_form("operator", "http://127.0.0.1:8001"))

    with app.app_context():
        assert ServerInfo.fromDb().url == "http://127.0.0.1:8000"


def test_the_instance_can_still_be_set_up_after_a_failed_first_registration(app, client, monkeypatch):
    """A failed write of the server settings gives up the lock it was taken under."""
    def fail(*args, **kwargs):
        raise ValueError("refused")

    monkeypatch.setattr(ServerInfo, "saveToDb", fail)
    failed = client.post("/register", data=first_user_form("operator", "http://127.0.0.1:8001"))
    assert "Server values invalid" in failed.get_data(as_text=True)
    monkeypatch.undo()

    assert client.post("/register", data=first_user_form("operator", "http://127.0.0.1:8001")).status_code == 302
    with app.app_context():
        assert UserInfo.fromDb(username="operator").role == "admin"


# --- two registrations of one name in two cases -------------------------------------

def test_two_registrations_cannot_take_one_name_in_two_cases(app, monkeypatch, open_instance):
    """A name is refused when it differs from a stored one only in case, and that lookup
    was all that enforced it: two registrations of "Eve" and "eve" at once could both
    find the name free, and both be written."""
    hold_each_write_for_the_other(monkeypatch, "INSERT INTO user (")

    register_both_at_once(app, monkeypatch, [
        {"username": username, "inputPassword1": PASSWORD, "inputPassword2": PASSWORD, "registrationToken": ""}
        for username in ("Eve", "eve")
    ])

    with app.app_context():
        names = [row["username"] for row in get_db().execute("SELECT username FROM user")]
    assert len([name for name in names if name.lower() == "eve"]) == 1, names


def test_a_failed_write_lets_go_of_the_lock_it_took(app, open_instance):
    """A new name is checked and written under one write lock. A write that fails after
    taking it must not leave the lock held, nor its transaction open for the next write
    on the connection to commit."""
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "mallory"
        user_info.password = None
        user_info.role = "pending"

        with pytest.raises(sqlite3.IntegrityError):
            user_info.saveToDb()

        assert not get_db().in_transaction
        other = sqlite3.connect(app.config["DATABASE"], timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE;")
            other.rollback()
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
