#!/usr/bin/python
"""The settings write routes: reachable only by POST, and landing somewhere real.

Each of these routes used to accept GET and act on it. `admin.change_server` blanked
the backend URL and both tokens; `admin.change_default_filter` reset every stored
filter to its default. Neither needed a single form field, and with no CSRF token
anywhere in the application at the time, any page an admin visited could fire them.
Both defences are in place now; the token itself is covered by testCsrf.py.

They also rendered `settings.html` without the context that template needs, so the
page 500'd immediately after the write went through. They redirect to the settings
view now, which is the one place that assembles it.
"""

import logging
import unittest

import pytest
from werkzeug.security import check_password_hash

from mcritweb.db import UserInfo, get_db

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

POST_ONLY = [
    ("/admin/change_default_filter", "admin"),
    ("/admin/change_server", "admin"),
    ("/admin/reset_server", "admin"),
    ("/admin/change_username", "contributor"),
    ("/admin/change_password", "contributor"),
]


@pytest.mark.parametrize("path,role", POST_ONLY)
def test_a_get_cannot_reach_a_settings_write(client, as_role, path, role):
    as_role(role)
    response = client.get(path)
    assert response.status_code == 405, f"GET {path} is still routed"


def test_changing_default_filters_lands_on_a_page_that_renders(client, as_role):
    as_role("admin")
    response = client.post("/admin/change_default_filter", data={"filter_direct_min_score": "42"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings")
    assert client.get("/settings").status_code == 200


def test_a_rejected_username_change_lands_on_a_page_that_renders(client, as_role):
    as_role("admin")
    response = client.post(
        "/admin/change_username",
        data={"username": "no", "inputPassword1": "password"},   # too short to be valid
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings")
    assert client.get("/settings").status_code == 200


def test_an_unconfirmed_reset_changes_nothing_and_still_answers(client, as_role):
    """The view used to fall off the end without returning, so Flask raised."""
    as_role("admin")
    response = client.post("/admin/reset_server", data={"reset_server": "not the word"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/server")


def test_changing_the_server_still_works_by_post(app, client, as_role):
    as_role("admin")
    response = client.post(
        "/admin/change_server",
        data={"mcrit_server_url": "http://127.0.0.1:9999", "mcrit_server_token": "newtoken"},
    )

    assert response.status_code == 200
    with app.app_context():
        from mcritweb.db import ServerInfo
        assert ServerInfo.fromDb().url == "http://127.0.0.1:9999"


# --- the account forms: cheapest check first, and the password gates the rest ----
# Issue #206. The checks used to be independent ifs, so a malformed name still paid
# for a password hash and a lookup, and whether a name was taken was answered to a
# caller who had just got the password wrong.

def _count_password_checks(monkeypatch):
    from mcritweb.views import administration
    checked = []
    real_check = administration.check_password_hash
    monkeypatch.setattr(administration, "check_password_hash",
                        lambda pwhash, password: checked.append(password) or real_check(pwhash, password))
    return checked


def _count_username_lookups(monkeypatch):
    looked_up = []
    real_from_db = UserInfo.fromDb.__func__
    def _from_db(cls, user_id=None, username=None):
        if username is not None:
            looked_up.append(username)
        return real_from_db(cls, user_id=user_id, username=username)
    monkeypatch.setattr(UserInfo, "fromDb", classmethod(_from_db))
    return looked_up


def _flashes(client):
    with client.session_transaction() as test_session:
        return [message for _category, message in test_session.get("_flashes", [])]


def test_a_malformed_username_is_refused_before_any_password_check(client, as_role, monkeypatch):
    as_role("contributor")
    checked = _count_password_checks(monkeypatch)
    looked_up = _count_username_lookups(monkeypatch)

    client.post("/admin/change_username", data={"username": "no", "inputPassword1": "password"})

    assert _flashes(client) == ["Username has invalid format."]
    assert checked == []
    assert looked_up == []


def test_a_wrong_password_does_not_say_whether_a_name_is_taken(app, client, as_role, make_user, monkeypatch):
    user_id = as_role("contributor")
    make_user(role="visitor", username="taken_name")
    looked_up = _count_username_lookups(monkeypatch)

    client.post("/admin/change_username", data={"username": "taken_name", "inputPassword1": "wrong"})

    assert _flashes(client) == ["Incorrect Password!"]
    assert looked_up == []
    with app.app_context():
        assert UserInfo.fromDb(user_id=user_id).username == "contributoruser"


def test_a_taken_name_is_refused_to_the_right_password(app, client, as_role, make_user):
    user_id = as_role("contributor")
    make_user(role="visitor", username="taken_name")

    client.post("/admin/change_username", data={"username": "taken_name", "inputPassword1": "password"})

    assert _flashes(client) == ["Username is already taken!"]
    with app.app_context():
        assert UserInfo.fromDb(user_id=user_id).username == "contributoruser"


def test_changing_the_username(app, client, as_role):
    user_id = as_role("contributor")

    response = client.post("/admin/change_username", data={"username": "renamed.user", "inputPassword1": "password"})

    assert response.status_code == 302
    assert _flashes(client) == ["Username successfully changed"]
    with app.app_context():
        assert UserInfo.fromDb(user_id=user_id).username == "renamed.user"


def test_mismatched_new_passwords_are_refused_before_the_password_check(app, client, as_role, monkeypatch):
    user_id = as_role("contributor")
    checked = _count_password_checks(monkeypatch)

    client.post("/admin/change_password",
                data={"inputPassword2": "password", "inputPassword3": "new-one", "inputPassword4": "other"})

    assert _flashes(client) == ["The entered passwords do not match!"]
    assert checked == []
    with app.app_context():
        assert check_password_hash(UserInfo.fromDb(user_id=user_id).password, "password")


def test_a_wrong_old_password_changes_nothing(app, client, as_role):
    user_id = as_role("contributor")

    client.post("/admin/change_password",
                data={"inputPassword2": "wrong", "inputPassword3": "new-one", "inputPassword4": "new-one"})

    assert _flashes(client) == ["Incorrect password!"]
    with app.app_context():
        assert check_password_hash(UserInfo.fromDb(user_id=user_id).password, "password")


def test_changing_the_password(app, client, as_role):
    user_id = as_role("contributor")

    response = client.post("/admin/change_password",
                           data={"inputPassword2": "password", "inputPassword3": "new-one", "inputPassword4": "new-one"})

    assert response.status_code == 302
    assert _flashes(client) == ["Password successfully changed"]
    with app.app_context():
        assert check_password_hash(UserInfo.fromDb(user_id=user_id).password, "new-one")


@pytest.mark.parametrize("path,data", [
    ("/admin/change_username", {"username": "renamed.user", "inputPassword1": "password"}),
    ("/admin/change_password", {"inputPassword2": "password", "inputPassword3": "new-one", "inputPassword4": "new-one"}),
    ("/admin/regenerate_apitoken", {}),
])
def test_a_session_for_a_deleted_user_writes_nothing(app, client, as_role, make_user, path, data):
    # the handlers act on g.user, so a session whose row is gone has to be stopped by
    # login_required before any of them runs
    make_user(role="admin", username="remaining.admin")
    user_id = as_role("contributor")
    with app.app_context():
        database = get_db()
        database.execute("DELETE FROM user WHERE id = ?;", (user_id,))
        database.commit()

    response = client.post(path, data=data)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    with app.app_context():
        assert UserInfo.fromDb(username="renamed.user") is None
        # saveToDb() through a row that is gone INSERTs it again under a new id, so a
        # resurrected account is found by its name, not by the id it used to have
        assert UserInfo.fromDb(username="contributoruser") is None


if __name__ == "__main__":
    unittest.main()
