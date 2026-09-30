#!/usr/bin/python
"""What a request reads from the local database, and how often.

The request hooks run on every request, the login page included, and several views
went back to SQLite for rows an earlier step of the same request had already loaded:
the first-user check fetched the whole user table to test it for emptiness, the index
asked that question a second time, and the single-row `server` table was read once by
the operation-mode hook, once more by the reachability probe and again by the client
factory - five times on an ordinary explore page. Issue #190.

The statements are counted through sqlite3's trace callback on the connection
`get_db` opens, so these tests see exactly what the views send.
"""

import logging
import re
import sqlite3
import unittest

import pytest
from werkzeug.security import generate_password_hash

from mcritweb import db
from mcritweb.db import ServerInfo, UserInfo
from mcritweb.views.utility import get_server_token, get_server_url

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PASSWORD = "correct horse battery staple"

#: A read of the server row, however it is spelled.
SERVER_ROW_READ = re.compile(r"^\s*SELECT\b.*\bFROM server\b", re.IGNORECASE | re.DOTALL)
#: A read of one user row by id, which is what load_logged_in_user does.
USER_ROW_BY_ID = re.compile(r"^\s*SELECT\b.*\bFROM user WHERE id = ", re.IGNORECASE | re.DOTALL)
#: A read of the user table with nothing after the table name - no WHERE, no LIMIT -
#: so every row comes back. The end-of-statement anchor is what excludes the rest.
WHOLE_USER_TABLE = re.compile(r"^\s*SELECT\b.*\bFROM user\s*(?:;|$)", re.IGNORECASE | re.DOTALL)


@pytest.fixture
def statements(monkeypatch):
    """Every statement sent over a connection opened from here on."""
    seen = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(seen.append)
        return connection

    monkeypatch.setattr(db.sqlite3, "connect", traced_connect)
    return seen


@pytest.fixture
def server_settings_readers(app, fake_mcrit):
    """The probe and the client factory, reading the server settings the way the real
    ones do. conftest substitutes both, and its substitutes read nothing - which would
    hide exactly the reads this module is about."""
    seen = []

    def probe():
        seen.append(("probe", get_server_url(), get_server_token()))
        return True

    def factory(**kwargs):
        seen.append(("client", get_server_url(), get_server_token()))
        return fake_mcrit

    app.config["MCRIT_SERVER_PROBE"] = probe
    app.config["MCRIT_CLIENT_FACTORY"] = factory
    return seen


def matching(pattern, seen):
    return [statement for statement in seen if pattern.search(statement)]


# --- the first-user check ---------------------------------------------------------

def test_the_first_user_check_does_not_fetch_the_user_table(app, make_user, statements):
    """It runs on every request, including anonymous ones, and used to be
    `SELECT * FROM user` + `fetchall()` - every row, password hashes included."""
    for index in range(5):
        make_user(role="visitor", username=f"user{index}")
    statements.clear()

    with app.test_request_context("/"):
        assert db.is_first_user() is False

    assert statements, "the check sent nothing, so this test proves nothing"
    assert matching(WHOLE_USER_TABLE, statements) == []


def test_the_first_user_check_still_answers_both_ways(app, make_user):
    with app.app_context():
        assert db.is_first_user() is True
    make_user(role="visitor")
    with app.app_context():
        assert db.is_first_user() is False


@pytest.mark.parametrize("path,role", [("/login", None), ("/", "admin"), ("/explore/samples", "admin"),
                                       ("/settings", "admin")])
def test_no_page_reads_the_whole_user_table(client, make_user, as_role, statements, path, role):
    if role is None:
        make_user(role="visitor")   # past first-user registration, but not logged in
    else:
        as_role(role)
    statements.clear()

    assert client.get(path).status_code == 200
    assert statements, "nothing was captured, so this test proves nothing"
    assert matching(WHOLE_USER_TABLE, statements) == []


def test_the_index_asks_whether_there_is_a_user_once(client, as_role, statements):
    """The hook has already put the answer on g.first_user."""
    as_role("admin")
    statements.clear()

    assert client.get("/").status_code == 200
    first_user_checks = [s for s in statements if re.search(r"\bFROM user\b", s) and "WHERE" not in s]
    assert len(first_user_checks) == 1


def test_an_empty_instance_still_sends_everyone_to_registration(client):
    response = client.get("/")

    assert response.status_code == 302
    assert "/register" in response.headers["Location"]


# --- the server row -----------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/explore/samples", "/admin/server"])
def test_a_page_that_builds_a_client_reads_the_server_row_once(client, as_role, statements,
                                                               server_settings_readers, path):
    """The hook, the probe and the client factory all want the URL and token."""
    as_role("admin")
    statements.clear()

    assert client.get(path).status_code == 200
    assert server_settings_readers, "nothing asked for the settings, so this test proves nothing"
    assert len(matching(SERVER_ROW_READ, statements)) == 1


def test_the_memo_does_not_outlive_the_request(app, client, as_role, server_settings_readers):
    """A second request has to see a write made between the two, even one that did not
    go through ServerInfo.saveToDb. In production every request gets a fresh `g`; a
    request run inside an already-pushed app context, as here, shares that context's
    `g` - and with it both the server row and the client built from it."""
    as_role("admin")
    with app.app_context():
        client.get("/explore/samples")
        db.get_db().execute("UPDATE server SET url = ?;", ("http://changed.invalid:8000",))
        db.get_db().commit()
        server_settings_readers.clear()

        client.get("/explore/samples")

    assert ("probe", "http://changed.invalid:8000", "") in server_settings_readers
    assert ("client", "http://changed.invalid:8000", "") in server_settings_readers
    assert {url for _, url, _ in server_settings_readers} == {"http://changed.invalid:8000"}


def test_a_write_in_the_request_is_what_the_rest_of_it_reads(app):
    """Holds for any writer, not only the admin form: first-user registration saves a
    ServerInfo it built from scratch, not the one the memo handed out."""
    with app.test_request_context("/"):
        assert get_server_url() == "http://127.0.0.1:8000"

        replacement = ServerInfo.fromDb()
        replacement.url = "http://elsewhere.invalid:8000"
        replacement.server_token = "new-token"
        replacement.saveToDb()

        assert get_server_url() == "http://elsewhere.invalid:8000"
        assert get_server_token() == "new-token"


def test_changing_the_server_builds_the_client_from_the_new_settings(client, as_role, server_settings_readers):
    """change_server reads the row, writes it, and then builds a client in the same
    request - against the settings it has just saved, not the ones it started with."""
    as_role("admin")
    server_settings_readers.clear()

    response = client.post(
        "/admin/change_server",
        data={"mcrit_server_url": "http://127.0.0.1:9999", "mcrit_server_token": "newtoken"},
    )

    assert response.status_code == 200
    assert server_settings_readers == [("client", "http://127.0.0.1:9999", "newtoken")]
    assert "http://127.0.0.1:9999" in response.get_data(as_text=True)


def test_registering_the_first_user_saves_the_server_settings(app, client):
    """The one other writer of the server row, on an instance with no row yet."""
    with app.app_context():
        db.get_db().execute("DELETE FROM server;")
        db.get_db().commit()

    response = client.post("/register", data={
        "username": "firstadmin", "inputPassword1": PASSWORD, "inputPassword2": PASSWORD,
        "url": "http://127.0.0.1:8123", "operationMode": "multi",
        "setRegistrationToken": "", "mcritServerToken": "",
    })

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    with app.app_context():
        assert UserInfo.fromDb(username="firstadmin").role == "admin"
        assert ServerInfo.fromDb().url == "http://127.0.0.1:8123"
    assert client.post("/login", data={"username": "firstadmin", "inputPassword": PASSWORD}).status_code == 302


# --- the rows the request already holds -------------------------------------------

def test_the_settings_page_uses_the_user_the_hook_loaded(client, as_role, statements):
    as_role("admin")
    statements.clear()

    assert client.get("/settings").status_code == 200
    assert len(matching(USER_ROW_BY_ID, statements)) == 1


@pytest.mark.parametrize("path,data", [("/admin/server", None),
                                       ("/admin/change_server", {"mcrit_server_url": "http://127.0.0.1:8000",
                                                                 "mcrit_server_token": ""})])
def test_the_server_page_shows_the_version_the_app_started_with(app, client, as_role, path, data):
    """create_app reads setup.py once; the admin pages used to read it again."""
    as_role("admin")
    app.config["MCRITWEB_VERSION"] = "9.8.7"

    response = client.post(path, data=data) if data is not None else client.get(path)

    assert response.status_code == 200
    assert "9.8.7" in response.get_data(as_text=True)


# --- and the one that is read afresh on purpose ------------------------------------

def test_the_absent_user_check_follows_the_table_between_logins(app, client, monkeypatch):
    """get_stored_password_hash_method is not cached, and must not become so: the
    dummy has to match whatever the table holds *now*, including changes this process
    had no part in - here a direct UPDATE, standing in for another worker."""
    from mcritweb.views import authentication

    # restored afterwards, so the dummy built here does not leak into other tests
    monkeypatch.setattr(authentication, "_ABSENT_USER_PASSWORD_HASH", None)
    monkeypatch.setattr(authentication, "_ABSENT_USER_HASH_METHOD", None)
    with app.app_context():
        for index in range(3):
            user_info = UserInfo()
            user_info.username = f"user{index}"
            user_info.password = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")
            user_info.role = "visitor"
            user_info.apitoken = f"apitoken-{index}"
            user_info.saveToDb()

    client.post("/login", data={"username": "nobody-by-that-name", "inputPassword": "x"})
    assert authentication._ABSENT_USER_HASH_METHOD == "pbkdf2:sha256:1000"

    with app.app_context():
        db.get_db().execute("UPDATE user SET password = ?;", (generate_password_hash(PASSWORD, method="pbkdf2:sha256:2000"),))
        db.get_db().commit()

    client.post("/login", data={"username": "nobody-by-that-name", "inputPassword": "x"})
    assert authentication._ABSENT_USER_HASH_METHOD == "pbkdf2:sha256:2000"
    assert authentication._ABSENT_USER_PASSWORD_HASH.split("$", 1)[0] == "pbkdf2:sha256:2000"


if __name__ == "__main__":
    unittest.main()
