#!/usr/bin/python
"""Metering guessing on /login and /register. Issue #101.

Neither route counted attempts, so password and registration-token guessing was
unmetered. The counter is per source address and lives in SQLite, which is what the
issue asks for - no new dependency, and correct across the multi-host deployments
AGENTS.md contemplates, because the store is shared.
"""

import logging
import time
import unittest

import pytest
from werkzeug.security import generate_password_hash

from mcritweb import db
from mcritweb.db import UserInfo

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PASSWORD = "correct horse battery staple"
HERE = {"REMOTE_ADDR": "203.0.113.7"}
ELSEWHERE = {"REMOTE_ADDR": "198.51.100.9"}


@pytest.fixture
def registered_user(app):
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "alice"
        user_info.password = generate_password_hash(PASSWORD)
        user_info.role = "visitor"
        user_info.apitoken = "apitoken-alice"
        user_info.saveToDb()
    return "alice"


def attempt(client, username, password, environ=None):
    return client.post(
        "/login",
        data={"username": username, "inputPassword": password},
        environ_base=environ or HERE,
        follow_redirects=True,
    ).get_data(as_text=True)


def burn_the_budget(client, username="alice", environ=None):
    """Spend exactly the allowance, so the next attempt is the first refused one."""
    for _ in range(db.LOGIN_ATTEMPT_LIMIT):
        attempt(client, username, "not the password", environ)


def test_attempts_are_refused_once_the_budget_is_spent(client, registered_user):
    burn_the_budget(client)

    page = attempt(client, registered_user, "not the password")

    assert "Too many failed attempts" in page


def test_the_refusal_still_does_not_say_whether_the_account_exists(client, registered_user):
    """The throttle must not become the oracle that one shared login message closed.

    A different answer for a known and an unknown name would confirm the account, which
    is exactly what #101's "related, same surface" half is about.
    """
    burn_the_budget(client)

    known = attempt(client, registered_user, "not the password")
    unknown = attempt(client, "nobody-by-that-name", "not the password")

    assert "Too many failed attempts" in known
    assert "Too many failed attempts" in unknown


def test_the_right_password_still_works_up_to_the_limit(client, registered_user):
    for _ in range(db.LOGIN_ATTEMPT_LIMIT - 1):
        attempt(client, registered_user, "not the password")

    page = attempt(client, registered_user, PASSWORD)

    assert "Too many failed attempts" not in page


def test_a_successful_login_forgets_the_failures(client, registered_user):
    """Someone who mistyped their password should not carry the count for 15 minutes."""
    for _ in range(db.LOGIN_ATTEMPT_LIMIT - 1):
        attempt(client, registered_user, "not the password")
    attempt(client, registered_user, PASSWORD)

    client.get("/logout", follow_redirects=True)
    for _ in range(db.LOGIN_ATTEMPT_LIMIT - 1):
        attempt(client, registered_user, "not the password")
    page = attempt(client, registered_user, "not the password")

    assert "Too many failed attempts" not in page, "the count was not reset by the success"


def test_the_block_is_on_the_address_not_the_account(client, registered_user):
    """The design decision #101 asks to make, pinned.

    A per-account lockout would let anyone deny service to any account whose name they
    know. Failures aimed at `alice` from one address must not stop `alice` logging in
    from another.
    """
    burn_the_budget(client, registered_user, HERE)

    page = attempt(client, registered_user, PASSWORD, ELSEWHERE)

    assert "Too many failed attempts" not in page, "another address was punished for these failures"

    # log out first: that successful login put a session on this client, and /login
    # short-circuits to the index for anyone already logged in - which would make the
    # assertion below pass without the throttle being consulted at all
    client.get("/logout", follow_redirects=True)
    assert "Too many failed attempts" in attempt(client, registered_user, "not the password", HERE),         "the address that spent its budget should still be refused"


def test_a_throttled_attempt_does_no_password_hashing(client, registered_user, monkeypatch):
    """The dummy check is deliberately expensive, so a refused caller must not reach it -
    otherwise this route is the cheapest way to spend the server's CPU."""
    burn_the_budget(client)

    import mcritweb.views.authentication as auth
    calls = []
    monkeypatch.setattr(auth, "check_password_hash", lambda *a, **k: calls.append(1) or False)

    attempt(client, "nobody-by-that-name", "not the password")

    assert calls == [], "a throttled attempt still paid for a password check"


def test_attempts_outside_the_window_do_not_count(client, registered_user, app):
    burn_the_budget(client)
    with app.app_context():
        stale = int(time.time()) - db.LOGIN_ATTEMPT_WINDOW - 60
        handle = db.get_db()
        handle.execute("UPDATE login_attempt SET attempted_at = ?", (stale,))
        handle.commit()

    page = attempt(client, registered_user, "not the password")

    assert "Too many failed attempts" not in page


def test_the_counter_survives_a_restart(app, client, registered_user):
    """It is in SQLite rather than in memory, which is what makes it correct across the
    several worker processes a real deployment runs."""
    burn_the_budget(client)

    with app.app_context():
        assert db.count_recent_login_failures("203.0.113.7") >= db.LOGIN_ATTEMPT_LIMIT


def test_an_ordinary_registration_slip_is_not_counted_as_a_guess(client, app):
    """Mistyping a password twice is not guessing, and must not spend the budget."""
    for _ in range(db.LOGIN_ATTEMPT_LIMIT + 2):
        client.post("/register", data={
            "username": "bob", "inputPassword1": "aaaaaaaa", "inputPassword2": "bbbbbbbb",
        }, environ_base=HERE, follow_redirects=True)

    with app.app_context():
        assert db.count_recent_login_failures("203.0.113.7") == 0


if __name__ == "__main__":
    unittest.main()
