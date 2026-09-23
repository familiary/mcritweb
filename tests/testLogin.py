#!/usr/bin/python
"""What a failed login is allowed to reveal.

`/login` is unauthenticated, so anything it says about *why* a login failed is
something a caller can ask for - now at a metered rate (see testLoginThrottle.py), but
still far more often than any oracle should be answerable. Saying "Incorrect
username." for one case and "Incorrect password." for the other answers "does this
account exist?" one request at a time. Issue #101.
"""

import logging
import unittest

import pytest
from werkzeug.security import generate_password_hash

from mcritweb.db import UserInfo

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PASSWORD = "correct horse battery staple"


@pytest.fixture
def registered_user(app):
    """An account to fail to log into, so the app is past first-user registration."""
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "alice"
        user_info.password = generate_password_hash(PASSWORD)
        user_info.role = "visitor"
        user_info.apitoken = "apitoken-alice"
        user_info.saveToDb()
    return "alice"


def attempt(client, username, password):
    return client.post("/login", data={"username": username, "inputPassword": password},
                       follow_redirects=True).get_data(as_text=True)


def test_a_wrong_password_and_an_unknown_user_give_the_same_message(client, registered_user):
    wrong_password = attempt(client, registered_user, "not the password")
    unknown_user = attempt(client, "nobody-by-that-name", "not the password")

    assert "Incorrect username or password." in wrong_password
    assert "Incorrect username or password." in unknown_user


def test_neither_message_names_which_half_was_wrong(client, registered_user):
    """The old strings, spelled out so this fails if either comes back."""
    for page in (attempt(client, registered_user, "not the password"),
                 attempt(client, "nobody-by-that-name", "not the password")):
        assert "Incorrect username." not in page
        assert "Incorrect password." not in page


def test_a_correct_login_still_works(client, registered_user):
    """The point of the above is not to make logging in harder."""
    response = client.post("/login", data={"username": registered_user, "inputPassword": PASSWORD})

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session.get("user_id") is not None


def test_an_unknown_username_costs_a_password_check(client, registered_user, monkeypatch):
    """The message alone does not close the hole.

    Password hashing is deliberately slow, so a branch that never reaches it answers
    measurably sooner and the timing says what the message will not. Asserting on the
    clock would be a flaky test; asserting that the hash function is *called* is the
    same claim without the flake.
    """
    from mcritweb.views import authentication

    checked = []
    real_check = authentication.check_password_hash
    monkeypatch.setattr(authentication, "check_password_hash",
                        lambda pwhash, password: checked.append(password) or real_check(pwhash, password))

    attempt(client, "nobody-by-that-name", "not the password")

    assert checked == ["not the password"], "no password check ran for an absent user"


def test_the_dummy_hash_never_admits_anyone(app, registered_user):
    """It is a hash of a random secret, so nothing should verify against it - least of
    all the empty password, which is what an unauthenticated probe would send."""
    from werkzeug.security import check_password_hash

    from mcritweb.views import authentication

    with app.app_context():
        authentication._spend_a_password_check("")

    assert authentication._ABSENT_USER_PASSWORD_HASH is not None
    for guess in ("", " ", "password", "admin"):
        assert not check_password_hash(authentication._ABSENT_USER_PASSWORD_HASH, guess)


def test_the_dummy_costs_what_the_stored_passwords_cost(app):
    """The message is not the only tell, and neither is the mere fact of hashing.

    check_password_hash costs whatever the *stored* hash asks for. Werkzeug's default
    has moved across the versions this app has been pinned to, and on werkzeug 3.1.8
    one check costs 66 ms for pbkdf2:sha256:260000, 150 ms for pbkdf2:sha256:600000 and
    93 ms for scrypt:32768:8:1. A dummy built with today's default would leave a ~30%
    gap on any database whose rows predate the last upgrade - which is to say, on the
    accounts an attacker most wants to find.

    Asserting on the clock would be flaky. Asserting that the dummy carries the same
    method as the stored hashes is the same claim without the flake, because the method
    is exactly what determines the cost.
    """
    from mcritweb.views import authentication

    legacy_method = "pbkdf2:sha256:260000"
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "legacy"
        user_info.password = generate_password_hash(PASSWORD, method=legacy_method)
        user_info.role = "visitor"
        user_info.apitoken = "apitoken-legacy"
        user_info.saveToDb()

        authentication._ABSENT_USER_PASSWORD_HASH = None
        authentication._ABSENT_USER_HASH_METHOD = None
        authentication._spend_a_password_check("whatever")

    assert authentication._ABSENT_USER_PASSWORD_HASH.split("$", 1)[0] == legacy_method


def test_a_login_moves_an_old_hash_onto_the_current_method(app, client):
    """One dummy cannot match two methods, so a table carrying a mix keeps a gap the
    dummy cannot close. Rehashing on a password we have just verified is what makes it
    converge - and it is safe precisely because the plaintext was just checked."""
    from mcritweb.views import authentication

    with app.app_context():
        user_info = UserInfo()
        user_info.username = "legacy"
        user_info.password = generate_password_hash(PASSWORD, method="pbkdf2:sha256:260000")
        user_info.role = "visitor"
        user_info.apitoken = "apitoken-legacy"
        user_info.saveToDb()

    response = client.post("/login", data={"username": "legacy", "inputPassword": PASSWORD})
    assert response.status_code == 302, "the old hash must still let its owner in"

    with app.app_context():
        stored = UserInfo.fromDb(username="legacy")
        assert stored.password.split("$", 1)[0] == authentication._current_hash_method()
        from werkzeug.security import check_password_hash
        assert check_password_hash(stored.password, PASSWORD), "the rewritten hash must still verify"


def test_a_current_hash_is_left_alone(app, client, registered_user):
    """The rewrite has to be conditional - rehashing on every login is a write per
    request and would churn the row for no gain."""
    with app.app_context():
        before = UserInfo.fromDb(username=registered_user).password

    client.post("/login", data={"username": registered_user, "inputPassword": PASSWORD})

    with app.app_context():
        assert UserInfo.fromDb(username=registered_user).password == before




# --- the user table is not always uniform, or even well-formed ----------------

def _user_with_hash(app, username, password_hash, role="visitor"):
    with app.app_context():
        user_info = UserInfo()
        user_info.username = username
        user_info.password = password_hash
        user_info.role = role
        user_info.apitoken = f"apitoken-{username}"
        user_info.saveToDb()


def test_the_dummy_matches_the_method_most_of_the_table_uses(app):
    """One dummy cannot match two methods, so a mixed table has no exact answer. The
    majority leaves the smallest set of accounts distinguishable; an arbitrary row leaves
    whichever set that row is not in - and on an instance upgraded from werkzeug 2.2 the
    oldest row is the legacy one, so `LIMIT 1` would have picked the method that every
    account created since the upgrade does *not* use.
    """
    from mcritweb.views import authentication

    legacy = "pbkdf2:sha256:260000"
    _user_with_hash(app, "oldest", generate_password_hash(PASSWORD, method=legacy))
    for index in range(3):
        _user_with_hash(app, f"newer{index}", generate_password_hash(PASSWORD, method="scrypt:32768:8:1"))

    with app.app_context():
        authentication._ABSENT_USER_PASSWORD_HASH = None
        authentication._ABSENT_USER_HASH_METHOD = None
        authentication._spend_a_password_check("whatever")

    assert authentication._ABSENT_USER_PASSWORD_HASH.split("$", 1)[0] == "scrypt:32768:8:1"


@pytest.mark.parametrize(
    "stored",
    [
        "md5$salt$hash",                          # a werkzeug hash from long ago
        "sha1$salt$hash",
        "nodollarsign",
        "5f4dcc3b5aa765d61d8327deb882cf99",       # a bare md5, from an import
        "$salt$hash",                             # empty method
    ],
)
def test_a_hash_this_werkzeug_cannot_generate_does_not_500_the_login(app, client, stored):
    """The method is read off a stored hash and handed to generate_password_hash, which
    raises ValueError for anything it cannot *produce* - even where it could verify it.

    Letting that escape would make /login 500 for absent usernames *only*, while an
    existing username still logged in. That is both a denial of service on one branch
    and a perfect existence oracle: strictly worse than the leak this whole change
    exists to close.
    """
    _user_with_hash(app, "imported", stored)

    response = client.post("/login", data={"username": "definitely-not-a-user",
                                           "inputPassword": "whatever"})

    assert response.status_code != 500
    assert response.status_code == 200, "a failed login re-renders the form"


def test_such_a_table_still_answers_the_same_way_for_both_cases(app, client):
    """The point of the fallback: the two branches must still look alike."""
    _user_with_hash(app, "imported", "md5$salt$hash")
    _user_with_hash(app, "alice", generate_password_hash(PASSWORD))

    absent = attempt(client, "definitely-not-a-user", "whatever")
    wrong_password = attempt(client, "alice", "whatever")

    assert "Incorrect username or password." in absent
    assert "Incorrect username or password." in wrong_password


def test_an_empty_user_table_has_no_method_to_match(app):
    from mcritweb import db
    from mcritweb.views import authentication

    with app.app_context():
        assert db.get_stored_password_hash_method() is None
        authentication._ABSENT_USER_PASSWORD_HASH = None
        authentication._ABSENT_USER_HASH_METHOD = None
        authentication._spend_a_password_check("whatever")

    assert authentication._ABSENT_USER_PASSWORD_HASH is not None


if __name__ == "__main__":
    unittest.main()
