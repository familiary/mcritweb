#!/usr/bin/python
"""A signed-in user can delete their own account from /settings.

Only an admin could delete an account, from the users page, so anyone who wanted their
username, password hash, API token and preferences gone had to ask for it. The privacy
guide asks for an easy manual flow for deleting your own data. The route deletes the
account the way the admin's delete does, through the same code, and only by POST, with
the CSRF token and the account's password typed again: the password check is a password
guess like one at /login, so it is metered by the same throttle.

The first account registered is the one the instance keeps as its admin: the users page
can neither demote it nor delete it, and it cannot delete itself either. That is a
convention about one row's id, which editing the database can undo, so the one admin
left and the one account left stay as well, and the check is made under the write lock
that the deletion itself holds.
"""

import logging
import sqlite3
import unittest

import pytest
from werkzeug.security import generate_password_hash

from mcritweb import db
from mcritweb.csrf import ENABLED_KEY
from mcritweb.db import UserColumnSettings, UserFilters, UserInfo

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PASSWORD = "carol's password, long enough"
TARGET = "/delete_account"
HERE = {"REMOTE_ADDR": "203.0.113.7"}


def _rows(app, statement, parameters=()):
    connection = sqlite3.connect(app.config["DATABASE"])
    try:
        return connection.execute(statement, parameters).fetchall()
    finally:
        connection.close()


@pytest.fixture
def boss(make_user):
    """The first account, which registration made the instance's admin."""
    return make_user(role="admin", username="boss")


def _make_carol(app, role):
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "carol"
        user_info.password = generate_password_hash(PASSWORD)
        user_info.role = role
        user_info.apitoken = "apitoken-carol"
        user_info.saveToDb()
        user_id = UserInfo.fromDb(username="carol").user_id
        # what the settings page creates on a first visit
        UserFilters.fromDict(user_id, {}).saveToDb()
        UserColumnSettings.fromDict(user_id, {}).saveToDb()
        return user_id


@pytest.fixture
def carol(app, boss):
    return _make_carol(app, "visitor")


def logged_in(app, username="carol", password=PASSWORD):
    client = app.test_client()
    response = client.post("/login", data={"username": username, "inputPassword": password}, environ_base=HERE)
    assert response.status_code == 302 and "/login" not in response.headers["Location"]
    return client


def copy_of(app, client):
    name = app.config["SESSION_COOKIE_NAME"]
    copy = app.test_client()
    copy.set_cookie(name, client.get_cookie(name).value)
    return copy


def is_logged_in(client, username="carol"):
    response = client.get("/settings")
    return response.status_code == 200 and username in response.get_data(as_text=True)


def delete_account(client, password=PASSWORD, **kwargs):
    return client.post(TARGET, data={"password": password}, environ_base=HERE, **kwargs)


def flashes(client):
    with client.session_transaction() as test_session:
        return [message for _category, message in test_session.get("_flashes", [])]


def account_rows(app, user_id):
    return {
        "user": _rows(app, "SELECT id FROM user WHERE id = ?", (user_id,)),
        "user_filters": _rows(app, "SELECT user_id FROM user_filters WHERE user_id = ?", (user_id,)),
        "user_column_settings": _rows(app, "SELECT user_id FROM user_column_settings WHERE user_id = ?", (user_id,)),
    }


def failures(app, username="carol"):
    with app.app_context():
        return db.count_recent_login_failures(HERE["REMOTE_ADDR"], username)


# --- the happy path ---------------------------------------------------------------

def test_deleting_the_account_removes_it_and_its_settings(app, carol):
    owner = logged_in(app)

    response = delete_account(owner)

    assert response.status_code == 302
    assert account_rows(app, carol) == {"user": [], "user_filters": [], "user_column_settings": []}


def test_deleting_the_account_signs_out_and_lands_on_the_login_page(app, carol):
    owner = logged_in(app)

    response = delete_account(owner)

    assert response.headers["Location"].endswith("/login")
    page = owner.get("/login").get_data(as_text=True)
    assert "Your account was deleted" in page
    assert "Please sign in" in page
    assert not is_logged_in(owner)


def test_the_api_token_goes_with_the_account(app, carol):
    owner = logged_in(app)

    delete_account(owner)

    with app.app_context():
        assert db.get_user_by_apitoken("apitoken-carol") is None
    assert app.test_client().get("/api/version", headers={"apitoken": "apitoken-carol"}).status_code == 403


@pytest.mark.parametrize("role", ["pending", "visitor", "contributor", "admin"])
def test_every_role_can_delete_their_own_account(app, boss, role):
    """The settings page hides the username and password tabs from visitors and pending
    accounts, but their data is theirs as much as anyone's. An admin other than the
    first account can go too: that one stays an admin."""
    user_id = _make_carol(app, role)
    owner = logged_in(app)

    assert f'action="{TARGET}"' in owner.get("/settings").get_data(as_text=True)
    delete_account(owner)

    assert account_rows(app, user_id)["user"] == []
    assert _rows(app, "SELECT role FROM user WHERE id = ?", (boss,)) == [("admin",)]


def test_another_account_is_left_alone(app, boss, carol):
    boss_session = logged_in(app, "boss", "password")

    delete_account(logged_in(app))

    assert account_rows(app, boss)["user"] == [(boss,)]
    assert is_logged_in(boss_session, "boss")


# --- what the deletion refuses ----------------------------------------------------

def test_a_wrong_password_deletes_nothing(app, carol):
    owner = logged_in(app)

    response = delete_account(owner, password="not the password")

    assert response.status_code == 302 and response.headers["Location"].endswith("/settings")
    assert flashes(owner) == ["Incorrect password. Your account was not deleted."]
    assert account_rows(app, carol) == {"user": [(carol,)], "user_filters": [(carol,)],
                                        "user_column_settings": [(carol,)]}
    assert is_logged_in(owner)


def test_a_wrong_password_is_counted_as_a_guess(app, carol):
    """Someone holding a stolen session does not know the password. Unmetered, this
    route would let them guess at it as often as they liked."""
    owner = logged_in(app)

    delete_account(owner, password="not the password")
    delete_account(owner, password="")

    assert failures(app) == 2


def test_the_guesses_spend_the_same_budget_as_logins(app, carol):
    owner = logged_in(app)
    for _ in range(db.LOGIN_ATTEMPT_LIMIT):
        delete_account(owner, password="not the password")

    response = delete_account(owner)

    assert flashes(owner)[-1] == "Too many failed attempts from this address. Please wait a few minutes and try again."
    assert response.headers["Location"].endswith("/settings")
    assert account_rows(app, carol)["user"] == [(carol,)]
    # and /login, from the same address, is refused as well
    page = app.test_client().post("/login", data={"username": "carol", "inputPassword": PASSWORD},
                                  environ_base=HERE, follow_redirects=True).get_data(as_text=True)
    assert "Too many failed attempts" in page


def test_a_throttled_attempt_does_no_password_hashing(app, carol, monkeypatch):
    owner = logged_in(app)
    for _ in range(db.LOGIN_ATTEMPT_LIMIT):
        delete_account(owner, password="not the password")

    import mcritweb.views.authentication as auth
    calls = []
    monkeypatch.setattr(auth, "check_password_hash", lambda *a, **k: calls.append(1) or False)
    delete_account(owner)

    assert calls == []


def test_the_deletion_forgets_the_mistyped_attempts_before_it(app, carol):
    """As a login does: the account's own mistakes from this address are not left behind."""
    owner = logged_in(app)
    delete_account(owner, password="not the password")

    delete_account(owner)

    assert failures(app) == 0


def test_a_get_cannot_delete_the_account(app, carol):
    owner = logged_in(app)

    assert owner.get(TARGET).status_code == 405
    assert account_rows(app, carol)["user"] == [(carol,)]


def test_a_signed_out_caller_is_sent_to_log_in(app, carol):
    response = delete_account(app.test_client())

    assert response.status_code == 302 and "/login" in response.headers["Location"]
    assert account_rows(app, carol)["user"] == [(carol,)]


def test_the_post_needs_the_csrf_token(app, carol):
    """A form on another site can post here with the session cookie attached, and with a
    password it guessed; the token is what it cannot supply."""
    owner = logged_in(app)
    app.config[ENABLED_KEY] = True

    response = delete_account(owner)

    assert response.status_code == 400
    assert account_rows(app, carol)["user"] == [(carol,)]


def test_the_settings_form_carries_what_the_route_needs(app, carol):
    """With protection on, the form as the settings page renders it deletes the account."""
    owner = logged_in(app)
    app.config[ENABLED_KEY] = True
    page = owner.get("/settings").get_data(as_text=True)
    form = page[page.index(f'action="{TARGET}"'):]
    form = form[:form.index("</form>")]
    token = form.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    assert 'type="password" name="password" id="delete_account_password"' in form
    assert '<label for="delete_account_password"' in form
    assert 'autocomplete="current-password"' in form and "required" in form
    response = owner.post(TARGET, data={"csrf_token": token, "password": PASSWORD}, environ_base=HERE)

    assert response.status_code == 302 and response.headers["Location"].endswith("/login")
    assert account_rows(app, carol)["user"] == []


def test_the_first_account_cannot_delete_itself(app, boss, carol):
    """The first account is the one the users page will neither demote nor delete, which
    is what keeps the server an admin however the others change. The right password does
    not delete it either."""
    owner = logged_in(app, "boss", "password")

    page = owner.get("/settings").get_data(as_text=True)
    assert f'action="{TARGET}"' not in page
    assert "This account cannot be deleted" in page

    response = delete_account(owner, password="password")

    assert response.headers["Location"].endswith("/settings")
    assert flashes(owner) == ["This account cannot be deleted: it is the first one registered here, "
                              "which stays an administrator so that the instance always has one."]
    assert _rows(app, "SELECT role FROM user WHERE id = ?", (boss,)) == [("admin",)]
    assert is_logged_in(owner, "boss")
    # refused before the password is checked, so it costs no guess
    delete_account(owner, password="not the password")
    assert failures(app, "boss") == 0


def _demote_root(app, boss):
    """What editing the database by hand can do: id 1 is no longer an admin."""
    connection = sqlite3.connect(app.config["DATABASE"])
    try:
        connection.execute("UPDATE user SET role = 'contributor' WHERE id = ?", (boss,))
        connection.commit()
    finally:
        connection.close()


def test_the_only_admin_cannot_delete_itself(app, boss):
    """The root refusal rests on id 1 being an admin. If that row was demoted by hand,
    the one admin left could delete itself and leave the instance without any, so the
    count of admins is what decides, and the password is not asked for."""
    _demote_root(app, boss)
    user_id = _make_carol(app, "admin")
    owner = logged_in(app)

    response = delete_account(owner)

    assert response.headers["Location"].endswith("/settings")
    assert flashes(owner) == ["This account cannot be deleted: it is the only administrator, and the "
                              "instance needs one. Make another account an administrator first."]
    assert account_rows(app, user_id) == {"user": [(user_id,)], "user_filters": [(user_id,)],
                                          "user_column_settings": [(user_id,)]}
    assert is_logged_in(owner)
    # refused before the password is checked, so it costs no guess
    delete_account(owner, password="not the password")
    assert failures(app) == 0


def test_an_admin_can_delete_itself_while_another_admin_remains(app, boss):
    _demote_root(app, boss)
    other = _make_other_admin(app)
    user_id = _make_carol(app, "admin")

    delete_account(logged_in(app))

    assert account_rows(app, user_id)["user"] == []
    assert _rows(app, "SELECT role FROM user WHERE id = ?", (other,)) == [("admin",)]


def test_the_settings_page_explains_why_the_only_admin_cannot_delete_itself(app, boss):
    _demote_root(app, boss)
    _make_carol(app, "admin")

    page = logged_in(app).get("/settings").get_data(as_text=True)

    assert f'action="{TARGET}"' not in page
    assert "This account cannot be deleted" in page
    assert "only administrator" in page


def test_a_contributor_can_delete_itself_when_no_admin_is_left(app, boss):
    """Only an admin's deletion can take the last admin away."""
    _demote_root(app, boss)
    user_id = _make_carol(app, "contributor")

    delete_account(logged_in(app))

    assert account_rows(app, user_id)["user"] == []


def _make_other_admin(app):
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "dave"
        user_info.password = generate_password_hash(PASSWORD)
        user_info.role = "admin"
        user_info.apitoken = "apitoken-dave"
        user_info.saveToDb()
        return UserInfo.fromDb(username="dave").user_id


# --- the account's other sessions -------------------------------------------------

def test_a_copied_session_ends_with_the_account(app, carol):
    owner = logged_in(app)
    copy = copy_of(app, owner)
    assert is_logged_in(copy)

    delete_account(owner)

    assert not is_logged_in(copy)
    assert copy.post("/admin/regenerate_apitoken").headers["Location"].endswith("/login")


def test_the_account_s_other_logins_end_with_it(app, carol):
    laptop = logged_in(app)
    phone = logged_in(app)

    delete_account(laptop)

    assert not is_logged_in(phone)
    assert phone.get("/settings").headers["Location"].endswith("/login")


def test_a_browser_whose_account_was_deleted_can_sign_in_again(app, boss, carol):
    """The phone's cookie still names the deleted account's id. Kept, it made /login say
    "You are logged in." and turn away every sign-in, as anyone, until it expired."""
    laptop = logged_in(app)
    phone = logged_in(app)
    delete_account(laptop)
    app.config[ENABLED_KEY] = True

    page = phone.get("/login").get_data(as_text=True)

    assert "You are logged in" not in page
    token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = phone.post("/login", data={"csrf_token": token, "username": "boss", "inputPassword": "password"},
                          environ_base=HERE)
    assert response.status_code == 302 and "/login" not in response.headers["Location"]
    assert is_logged_in(phone, "boss")


# --- what stays, however it is asked for ------------------------------------------

def _make_other_contributor_only(app, boss):
    """What editing the database by hand can leave: the first account gone, one
    contributor the only account there is."""
    user_id = _make_carol(app, "contributor")
    connection = sqlite3.connect(app.config["DATABASE"])
    try:
        connection.execute("DELETE FROM user WHERE id = ?", (boss,))
        connection.commit()
    finally:
        connection.close()
    return user_id


def test_the_only_account_left_cannot_delete_itself(app, boss):
    """With the table empty the next registration is the first user's, and makes whoever
    sends it an admin who sets the server the instance talks to."""
    user_id = _make_other_contributor_only(app, boss)
    owner = logged_in(app)

    assert "It is the only account left" in owner.get("/settings").get_data(as_text=True)
    response = delete_account(owner)

    assert response.headers["Location"].endswith("/settings")
    assert flashes(owner) == ["This account cannot be deleted: it is the only account left, and the next "
                              "registration would make whoever sends it an administrator."]
    assert account_rows(app, user_id)["user"] == [(user_id,)]
    with app.app_context():
        assert not db.is_first_user()


def test_the_deletion_asks_again_once_the_password_is_checked(app, boss, monkeypatch):
    """Two admins deleting themselves at once both found the other still there while
    their passwords were checked, and both went. The deletion asks again, and the one
    left stays."""
    _demote_root(app, boss)
    other = _make_other_admin(app)
    user_id = _make_carol(app, "admin")
    owner = logged_in(app)

    import mcritweb.views.authentication as auth

    def the_other_admin_goes_meanwhile(stored, password):
        with app.app_context():
            assert db.delete_user(other) is None
        return True

    monkeypatch.setattr(auth, "check_password_hash", the_other_admin_goes_meanwhile)
    response = delete_account(owner)

    assert response.headers["Location"].endswith("/settings")
    assert flashes(owner) == [auth.LAST_ADMIN_KEPT]
    assert _rows(app, "SELECT id FROM user WHERE role = 'admin'") == [(user_id,)]
    assert is_logged_in(owner)


@pytest.mark.parametrize("change", [lambda user_id: db.delete_user(user_id),
                                    lambda user_id: db.change_role(user_id, "visitor")],
                         ids=["delete", "demote"])
def test_the_count_is_taken_under_the_write_lock(app, carol, monkeypatch, change):
    """A count is not a lock: without the write lock held from the count to the commit,
    a second deletion could count the same rows and go as well."""
    counted = []
    original = db._why_kept

    def while_counting(database, user_id, new_role=None):
        other = sqlite3.connect(app.config["DATABASE"], timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE;")
        finally:
            other.close()
        counted.append(user_id)
        return original(database, user_id, new_role)

    monkeypatch.setattr(db, "_why_kept", while_counting)
    with app.app_context():
        assert change(carol) is None

    assert counted == [carol]


def _users_page(app):
    return logged_in(app, "boss", "password")


def test_the_users_page_cannot_delete_the_only_admin(app, boss):
    """The admin's own account, deleted from the users page, with the first account
    demoted by hand: the check that settings makes is in db.delete_user, so this route
    makes it too."""
    _demote_root(app, boss)
    user_id = _make_carol(app, "admin")
    admin = logged_in(app)

    admin.post(f"/admin/delete_user/{user_id}/all")

    assert account_rows(app, user_id)["user"] == [(user_id,)]
    assert flashes(admin) == ["The only administrator cannot be deleted, as the instance needs one. "
                              "Make another account an administrator first."]


@pytest.mark.parametrize("role", ["visitor", "contributor", "pending"])
def test_the_users_page_cannot_demote_the_only_admin(app, boss, role):
    _demote_root(app, boss)
    user_id = _make_carol(app, "admin")
    admin = logged_in(app)

    admin.post(f"/admin/change_user_role/{user_id}/{role}/all")

    assert _rows(app, "SELECT role FROM user WHERE id = ?", (user_id,)) == [("admin",)]
    assert flashes(admin) == ["The only administrator keeps the administrator role, as the instance needs one. "
                              "Make another account an administrator first."]


def test_the_users_page_can_demote_an_admin_while_another_remains(app, boss):
    user_id = _make_carol(app, "admin")

    _users_page(app).post(f"/admin/change_user_role/{user_id}/visitor/all")

    assert _rows(app, "SELECT role FROM user WHERE id = ?", (user_id,)) == [("visitor",)]


def test_the_users_page_says_why_the_first_account_stays(app, boss, carol):
    admin = _users_page(app)

    admin.post(f"/admin/delete_user/{boss}/all")

    assert account_rows(app, boss)["user"] == [(boss,)]
    assert flashes(admin) == ["The first account registered here cannot be deleted, so that the instance "
                              "always has an administrator."]


if __name__ == "__main__":
    unittest.main()
