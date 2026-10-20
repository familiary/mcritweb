#!/usr/bin/python
"""The colour theme (issue #70): what is stored, how it is changed, how the page carries it,
and the palette that makes it a theme. No browser; testThemeBrowser.py drives the pages.

1. The account's setting: THEMES / DEFAULT_THEME / normalize_theme, the `theme` column (new
   accounts, accounts from before it, the migration that adds it) and UserInfo writing it
   only when it changed.
2. POST /admin/change_theme, with CSRF protection on: the whitelist, the token, the login,
   the method, and whose row it writes.
3. base.html for each theme, logged in and out: the <html data-theme>, the colour-scheme
   <meta>, the stylesheet <link> and its media query, the logos. The match diagram's
   <picture> is held by testMatchDiagramThemes.py (which of the four result pages carry it,
   and in which theme its <source> is there) and not repeated here.
4. ScoreColorProvider's tints.
5. The palette: static/style.css names every colour (light, the values master had) and
   static/theme-dark.css gives the same names their dark values; every var() names something
   defined; the contrast of the dark palette is measured.
6. The ratchet: no colour is written anywhere but in the two palettes. A new colour gets a
   name in both files, or one of the few exceptions below, with its reason.

The rest of the suite runs with CSRF protection off. This module switches it on, as
testCsrf.py does, since the route is a write and the token is part of what is tested.
"""

import ast
import collections
import functools
import importlib
import logging
import re
import sqlite3
from html.parser import HTMLParser

import pytest
import themeCss
from cfgColors import LIGHT_CFG_COLORS
from testMigrations import SCHEMA_V0_10_6, SCHEMA_V1_4_8, _columns, _legacy_database, _query, _run_migration
from themeCss import PACKAGE, ROOT, STATIC, TEMPLATES, contrast, parse_colour

from mcritweb import db as accounts
from mcritweb.csrf import ENABLED_KEY, FIELD_NAME
from mcritweb.db import UserInfo, get_db
from mcritweb.views.ScoreColorProvider import ScoreColorProvider

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

CHANGE_THEME = "/admin/change_theme"

#: The themes and the default, written out here. A test that has to fail on a checkout without
#: the feature must not import what the feature adds, or the whole module fails to be collected
#: and no test is judged one by one: the names of mcritweb.db and mcritweb.theme are reached
#: inside the tests, and the first of them pins these against the module.
THEMES = ("light", "dark", "system")
DEFAULT_THEME = "light"

#: What each theme must put on the page, written out here and not read from mcritweb/theme.py,
#: so that a wrong query in the module fails these tests instead of agreeing with itself.
EXPECTED = {
    "light": {"scheme": "light", "media": None},
    "dark": {"scheme": "dark", "media": "all"},
    "system": {"scheme": "light dark", "media": "(prefers-color-scheme: dark)"},
}

#: Values a form could send that are not a theme: wrong case, padding, look-alikes, nothing,
#: markup, something large. Each of them has to be refused and change nothing.
NOT_A_THEME = [
    "",
    " ",
    "Dark",
    "DARK",
    "System",
    "dark ",
    " dark",
    "dark\n",
    "dark\x00",
    "d\u0430rk",  # a Cyrillic a
    "dark\u200b",  # a zero-width space
    "auto",
    "default",
    "none",
    "null",
    "0",
    "light,dark",
    "../dark",
    "constructor",
    "__class__",
    "__proto__",
    "THEMES",
    "\ufffd",  # what a byte that is not UTF-8 decodes to
    "<script>alert(1)</script>",
    'light"><script>alert(1)</script>',
    "dark'; DROP TABLE user; --",
    "x" * 10240,
]


@pytest.fixture
def app(app):
    """The standard test app with CSRF protection switched back on."""
    app.config[ENABLED_KEY] = True
    return app


# --- helpers for the account tests ---------------------------------------------------------

def raw_rows(app, statement, parameters=()):
    """Rows straight from the SQLite file, so that nothing reads them through UserInfo."""
    connection = sqlite3.connect(app.config["DATABASE"])
    try:
        return connection.execute(statement, parameters).fetchall()
    finally:
        connection.close()


def stored_theme(app, username):
    """The `theme` column of one account as stored: None for NULL."""
    rows = raw_rows(app, "SELECT theme FROM user WHERE username = ?", (username,))
    assert len(rows) == 1, f"{len(rows)} accounts named {username}"
    return rows[0][0]


def add_user(app, username, role="visitor", theme=DEFAULT_THEME):
    """An account through UserInfo, the way registration makes one."""
    with app.app_context():
        user_info = UserInfo()
        user_info.username = username
        user_info.password = "hashed"
        user_info.role = role
        user_info.apitoken = f"token-of-{username}"
        user_info.theme = theme
        user_info.saveToDb()
        return UserInfo.fromDb(username=username).user_id


def add_user_before_the_theme(app, username, role="visitor"):
    """An account the way every account was stored before #70: no `theme` in the INSERT, so NULL."""
    connection = sqlite3.connect(app.config["DATABASE"])
    try:
        connection.execute(
            "INSERT INTO user (username, password, role, registered, last_login, apitoken) VALUES (?,?,?,?,?,?)",
            (username, "hashed", role, "2020-01-01 00:00:00", "no login", f"token-of-{username}"),
        )
        connection.commit()
    finally:
        connection.close()
    with app.app_context():
        return UserInfo.fromDb(username=username).user_id


def sign_in(client, user_id):
    with client.session_transaction() as test_session:
        test_session["user_id"] = user_id


def trace(connection):
    """The statements the connection runs from now on, as a list that fills up."""
    statements = []
    connection.set_trace_callback(statements.append)
    return statements


def writes_to_user(statements):
    """The columns of the user table that were UPDATEd, in order, and the INSERTs counted."""
    updated = [match.group(1) for statement in statements if (match := re.match(r"UPDATE user SET (\w+) =", statement))]
    inserted = [statement for statement in statements if statement.startswith("INSERT INTO user ")]
    return updated, len(inserted)


# --- 1. the setting: THEMES, DEFAULT_THEME, normalize_theme --------------------------------

def test_the_themes_are_light_dark_and_system_and_light_is_the_default():
    assert accounts.THEMES == ("light", "dark", "system") == THEMES
    assert accounts.DEFAULT_THEME == "light" == DEFAULT_THEME, "every account from before the setting has to keep the light pages it had"


@pytest.mark.parametrize("theme", THEMES)
def test_a_theme_normalizes_to_itself(theme):
    assert accounts.normalize_theme(theme) == theme


@pytest.mark.parametrize(
    "value",
    [None, "", " ", "Dark", "DARK", "System", "dark ", " dark", "dark\n", "d\u0430rk", "auto", "default", 0, 1, True, b"dark", ["dark"], ("dark",), {"dark"}, {"theme": "dark"}],
)
def test_anything_that_is_not_a_theme_normalizes_to_the_default(value):
    assert accounts.normalize_theme(value) == DEFAULT_THEME


# --- 1. the `theme` column ------------------------------------------------------------------

@pytest.mark.parametrize(
    "assigned, stored",
    [
        ("light", "light"),
        ("dark", "dark"),
        ("system", "system"),
        (None, "light"),
        ("Dark", "light"),
        ("", "light"),
        ("<b>dark</b>", "light"),
        ("x" * 10240, "light"),
    ],
)
def test_a_new_account_stores_the_normalized_theme(app, assigned, stored):
    add_user(app, "newcomer", theme=assigned)

    assert stored_theme(app, "newcomer") == stored


def test_an_account_that_never_chose_stores_the_default_and_not_null(app):
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "undecided"
        user_info.password = "hashed"
        user_info.role = "visitor"
        user_info.apitoken = "token"
        user_info.saveToDb()

    assert stored_theme(app, "undecided") == "light"


def test_a_null_column_reads_back_as_light(app):
    user_id = add_user_before_the_theme(app, "veteran")

    assert stored_theme(app, "veteran") is None, "the setup is meant to leave the column NULL"
    with app.app_context():
        by_id = UserInfo.fromDb(user_id=user_id)
        by_name = UserInfo.fromDb(username="veteran")
    assert by_id.theme == by_name.theme == "light"
    assert by_id._stored["theme"] == "light"


@pytest.mark.parametrize("garbage", ["Dark", "dark ", "", "blue", "<script>"])
def test_a_stored_value_outside_the_themes_reads_back_as_light(app, garbage):
    """The column is a VARCHAR that anything with database access could have written."""
    add_user(app, "odd")
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.execute("UPDATE user SET theme = ? WHERE username = 'odd'", (garbage,))
    connection.commit()
    connection.close()

    with app.app_context():
        assert UserInfo.fromDb(username="odd").theme == "light"


def test_a_fresh_database_has_a_nullable_theme_column_without_a_default(app):
    columns = {row[1]: row for row in raw_rows(app, "PRAGMA table_info(user)")}

    assert columns["theme"][2:] == ("VARCHAR", 0, None, 0), "(type, notnull, default, pk)"
    assert "theme" in UserInfo.COLUMNS


# --- 1. the migration -----------------------------------------------------------------------

#: master's `user` table, pinned: `git show 6564602:mcritweb/sql/create_table_user.sql`, as the
#: columns the migration starts from
MASTER_USER_COLUMNS = ["id", "username", "password", "role", "registered", "last_login", "apitoken"]


def _tables_user(schema):
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema)
        return connection.execute("PRAGMA table_info(user)").fetchall()
    finally:
        connection.close()


def test_the_schema_the_migration_starts_from_is_the_one_master_had():
    """testMigrations.py's v1.4.8 schema is the newest of its tables with master's `user`
    table; the tests below are only about the migration if they start from it."""
    assert [row[1] for row in _tables_user(SCHEMA_V1_4_8)] == MASTER_USER_COLUMNS


def _insert_legacy_user(db_path, username, with_apitoken=False):
    """An account in a pre-#70 schema, with a `registered` in the format the application
    writes (testMigrations.py's helper writes a bare date, which UserInfo does not read)."""
    columns = "username, password, role, registered, last_login" + (", apitoken" if with_apitoken else "")
    values = (username, "hashed", "admin", "2020-01-01 00:00:00.000000", "no login") + (("preexisting-token",) if with_apitoken else ())
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(f"INSERT INTO user ({columns}) VALUES ({', '.join('?' * len(values))})", values)
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize("schema", [SCHEMA_V0_10_6, SCHEMA_V1_4_8], ids=["v0.10.6", "master"])
def test_the_migration_adds_the_column_and_leaves_existing_rows_null(tmp_path, schema):
    db_path = _legacy_database(tmp_path, schema)
    _insert_legacy_user(db_path, "first", with_apitoken=schema is SCHEMA_V1_4_8)
    _insert_legacy_user(db_path, "second", with_apitoken=schema is SCHEMA_V1_4_8)
    assert "theme" not in _columns(db_path, "user")

    application = _run_migration(tmp_path, db_path)

    assert "theme" in _columns(db_path, "user")
    assert _query(db_path, "SELECT username, theme FROM user ORDER BY id") == [("first", None), ("second", None)], "an existing account was given a theme"
    with application.app_context():
        assert [UserInfo.fromDb(username=name).theme for name in ("first", "second")] == ["light", "light"]


def test_the_migrated_column_is_the_column_a_fresh_database_has(tmp_path, app):
    """create_table_user.sql and the ALTER TABLE spell the column twice."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    db_path = _legacy_database(legacy, SCHEMA_V1_4_8)
    _run_migration(legacy, db_path)

    migrated = {row[1]: row[2:] for row in _query(db_path, "PRAGMA table_info(user)")}
    fresh = {row[1]: row[2:] for row in raw_rows(app, "PRAGMA table_info(user)")}
    assert migrated["theme"] == fresh["theme"]


def test_the_migration_keeps_what_the_account_already_had(tmp_path):
    db_path = _legacy_database(tmp_path, SCHEMA_V1_4_8)
    _insert_legacy_user(db_path, "olduser", with_apitoken=True)
    before = _query(db_path, "SELECT id, username, password, role, registered, last_login, apitoken FROM user")

    _run_migration(tmp_path, db_path)

    assert _query(db_path, "SELECT id, username, password, role, registered, last_login, apitoken FROM user") == before


def test_a_second_start_does_not_touch_the_theme_the_first_one_left(tmp_path):
    db_path = _legacy_database(tmp_path, SCHEMA_V1_4_8)
    _insert_legacy_user(db_path, "olduser", with_apitoken=True)
    _run_migration(tmp_path, db_path)
    connection = sqlite3.connect(str(db_path))
    connection.execute("UPDATE user SET theme = 'dark'")
    connection.commit()
    connection.close()

    _run_migration(tmp_path, db_path)

    assert _query(db_path, "SELECT theme FROM user") == [("dark",)]
    assert _columns(db_path, "user").count("theme") == 1


# --- 1. UserInfo writes the theme only when it changed -------------------------------------

def test_saving_an_account_nobody_changed_writes_nothing(app):
    user_id = add_user(app, "steady", theme="dark")

    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        statements = trace(get_db())
        user_info.saveToDb()

    assert writes_to_user(statements) == ([], 0)


def test_changing_the_theme_writes_the_theme_and_nothing_else(app):
    user_id = add_user(app, "mover", theme="light")

    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        user_info.theme = "dark"
        statements = trace(get_db())
        user_info.saveToDb()

    assert writes_to_user(statements) == (["theme"], 0)
    assert stored_theme(app, "mover") == "dark"


def test_changing_something_else_does_not_write_the_theme(app):
    user_id = add_user(app, "promoted", theme="dark")

    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        user_info.role = "contributor"
        statements = trace(get_db())
        user_info.saveToDb()

    assert writes_to_user(statements) == (["role"], 0)


def test_choosing_the_theme_an_old_account_already_reads_as_writes_nothing(app):
    """A NULL column reads as light. Choosing light is not a change, so the row is left as it is."""
    user_id = add_user_before_the_theme(app, "veteran")

    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        user_info.theme = "light"
        statements = trace(get_db())
        user_info.saveToDb()

    assert writes_to_user(statements) == ([], 0)
    assert stored_theme(app, "veteran") is None


def test_a_copy_read_earlier_does_not_undo_a_theme_chosen_since(app):
    """Why the columns are tracked at all: a request holds its copy of the row across other
    requests, and writing the whole copy back would restore the theme it read."""
    user_id = add_user(app, "twice", theme="light")

    with app.app_context():
        stale = UserInfo.fromDb(user_id=user_id)
        current = UserInfo.fromDb(user_id=user_id)
        current.theme = "dark"
        current.saveToDb()
        stale.role = "contributor"
        stale.saveToDb()

    assert stored_theme(app, "twice") == "dark"
    assert raw_rows(app, "SELECT role FROM user WHERE username = 'twice'") == [("contributor",)]


# --- theme.py: what a theme turns into --------------------------------------------------------

def theme_module():
    """mcritweb.theme, imported when a test needs it (see THEMES above)."""
    return importlib.import_module("mcritweb.theme")


def test_every_theme_has_a_media_query_and_a_colour_scheme_and_nothing_else_has():
    module = theme_module()

    assert set(module.DARK_MEDIA) == set(module.COLOR_SCHEME) == set(THEMES) == set(EXPECTED)


@pytest.mark.parametrize("theme", THEMES)
def test_a_theme_turns_into_its_media_query_and_colour_scheme(theme):
    assert theme_module().theme_context(theme) == {
        "theme": theme,
        "theme_dark_media": EXPECTED[theme]["media"],
        "theme_color_scheme": EXPECTED[theme]["scheme"],
    }


@pytest.mark.parametrize("value", [None, "", "Dark", "dark ", "nonsense"])
def test_a_value_that_is_not_a_theme_renders_as_the_default(value):
    theme_context = theme_module().theme_context

    assert theme_context(value) == theme_context(DEFAULT_THEME)
    assert theme_context()["theme"] == "light"


# --- 2. POST /admin/change_theme, CSRF protection on -----------------------------------------

def csrf_token(client, page="/settings"):
    """The token this session will be checked against, minted by fetching a page as a browser would."""
    assert client.get(page).status_code == 200, page
    with client.session_transaction() as test_session:
        return test_session[FIELD_NAME]


def flashes(client):
    """The (category, message) pairs waiting in the session for the next page."""
    with client.session_transaction() as test_session:
        return [tuple(pair) for pair in test_session.get("_flashes", [])]


def post_theme(client, theme, token=None, **extra):
    data = {} if theme is None else {"theme": theme}
    if token is not None:
        data[FIELD_NAME] = token
    data.update(extra)
    return client.post(CHANGE_THEME, data=data)


class RootTag(HTMLParser):
    """The attributes of the <html> element of a page."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.attributes = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        if tag == "html" and self.attributes is None:
            self.attributes = dict(attrs)


def theme_of_page(response):
    assert response.status_code == 200
    return RootTag(response.data.decode()).attributes["data-theme"]


@pytest.mark.parametrize("chosen", THEMES)
def test_each_theme_is_stored_and_the_next_page_renders_it(client, app, chosen):
    user_id = add_user(app, "chooser", theme="light" if chosen != "light" else "dark")
    sign_in(client, user_id)
    token = csrf_token(client)

    response = post_theme(client, chosen, token)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings")
    assert stored_theme(app, "chooser") == chosen
    assert flashes(client) == [("success", "Theme changed")]
    assert theme_of_page(client.get("/settings")) == chosen
    assert theme_of_page(client.get("/explore/samples")) == chosen


def test_the_choice_belongs_to_the_account_and_not_the_session(client, app):
    user_id = add_user(app, "chooser")
    sign_in(client, user_id)
    post_theme(client, "dark", csrf_token(client))

    elsewhere = app.test_client()
    sign_in(elsewhere, user_id)

    assert theme_of_page(elsewhere.get("/settings")) == "dark"


def test_the_theme_can_be_changed_again_and_again(client, app):
    user_id = add_user(app, "fickle")
    sign_in(client, user_id)
    token = csrf_token(client)

    for chosen in ("dark", "system", "light", "dark", "dark", "system"):
        assert post_theme(client, chosen, token).status_code == 302
        assert stored_theme(app, "fickle") == chosen


@pytest.mark.parametrize("role", ["pending", "visitor", "contributor", "admin"])
def test_any_role_can_change_their_own_theme(client, app, role):
    user_id = add_user(app, "mine", role=role)
    sign_in(client, user_id)

    response = post_theme(client, "dark", csrf_token(client))

    assert response.status_code == 302
    assert stored_theme(app, "mine") == "dark"


@pytest.mark.parametrize("value", NOT_A_THEME, ids=lambda value: repr(value)[:24])
def test_a_value_outside_the_themes_is_refused_with_an_error_and_changes_nothing(client, app, value):
    user_id = add_user(app, "careful", theme="dark")
    sign_in(client, user_id)
    token = csrf_token(client)

    response = post_theme(client, value, token)

    assert response.status_code == 302, "refused, the way the other settings forms refuse: back to /settings with a flash"
    assert response.headers["Location"].endswith("/settings")
    assert flashes(client) == [("error", "Unknown theme requested")], "the message is fixed text and does not echo what was sent"
    assert stored_theme(app, "careful") == "dark"
    page = client.get("/settings")
    assert theme_of_page(page) == "dark"
    assert b"alert(1)" not in page.data, "what was sent came back onto a page"


@pytest.mark.parametrize("raw", [b"theme=%ff%fe", b"theme=d%c3%a4rk", b"theme=%c0%af", b"theme=dark%00", b"theme"])
def test_a_body_that_is_not_utf_8_text_is_refused_like_any_other_value(client, app, raw):
    user_id = add_user(app, "careful", theme="dark")
    sign_in(client, user_id)
    token = csrf_token(client)

    response = client.post(CHANGE_THEME, data=raw + f"&{FIELD_NAME}={token}".encode(), content_type="application/x-www-form-urlencoded")

    assert response.status_code == 302, "a body that is not text was not answered like the other refusals"
    assert flashes(client) == [("error", "Unknown theme requested")]
    assert stored_theme(app, "careful") == "dark"


def test_a_post_without_a_theme_field_is_refused_and_changes_nothing(client, app):
    user_id = add_user(app, "careful", theme="dark")
    sign_in(client, user_id)

    response = post_theme(client, None, csrf_token(client))

    assert response.status_code == 302
    assert flashes(client) == [("error", "Unknown theme requested")]
    assert stored_theme(app, "careful") == "dark"


def test_the_theme_is_read_from_the_form_and_from_nowhere_else(client, app):
    user_id = add_user(app, "careful", theme="dark")
    sign_in(client, user_id)
    token = csrf_token(client)

    in_the_query = client.post(f"{CHANGE_THEME}?theme=light", data={FIELD_NAME: token})
    as_json = client.post(CHANGE_THEME, json={"theme": "light"}, headers={"X-CSRFToken": token})
    as_a_cookie = client.post(CHANGE_THEME, data={FIELD_NAME: token}, headers={"Cookie": "theme=light"})

    for response in (in_the_query, as_json, as_a_cookie):
        assert response.status_code == 302
    assert stored_theme(app, "careful") == "dark"


@pytest.mark.parametrize("theme", ["light", "dark", "system"])
def test_a_post_without_a_token_is_refused_by_the_token_check_and_changes_nothing(client, app, theme):
    user_id = add_user(app, "target", theme="dark" if theme != "dark" else "light")
    before = stored_theme(app, "target")
    sign_in(client, user_id)
    csrf_token(client)

    response = post_theme(client, theme)

    # a 400 is also what a malformed body could earn, so what tells the causes apart is the
    # token check's own message, and that the view never ran: no flash, no write
    assert response.status_code == 400
    assert b"CSRF token is missing or invalid" in response.data
    assert flashes(client) == []
    assert stored_theme(app, "target") == before


@pytest.mark.parametrize("token", ["", "a" * 64, "0" * 63, "x"])
def test_a_post_with_a_token_that_is_not_the_sessions_is_refused(client, app, token):
    user_id = add_user(app, "target", theme="dark")
    sign_in(client, user_id)
    csrf_token(client)

    response = post_theme(client, "light", token)

    assert response.status_code == 400
    assert b"CSRF token is missing or invalid" in response.data
    assert stored_theme(app, "target") == "dark"


def test_the_token_of_another_session_does_not_work_either(client, app):
    first = add_user(app, "first", theme="dark")
    second = add_user(app, "second", theme="dark")
    sign_in(client, first)
    other = app.test_client()
    sign_in(other, second)

    foreign_token = csrf_token(other)
    csrf_token(client)
    response = post_theme(client, "light", foreign_token)

    assert response.status_code == 400
    assert stored_theme(app, "first") == "dark"


def test_the_token_works_as_a_header_for_scripts(client, app):
    user_id = add_user(app, "scripted", theme="dark")
    sign_in(client, user_id)
    token = csrf_token(client)

    response = client.post(CHANGE_THEME, data={"theme": "light"}, headers={"X-CSRFToken": token})

    assert response.status_code == 302
    assert stored_theme(app, "scripted") == "light"


def test_logged_out_a_post_goes_to_the_login_and_changes_nothing(client, app):
    add_user(app, "somebody", theme="dark")
    token = csrf_token(client, "/login")

    response = post_theme(client, "light", token)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert stored_theme(app, "somebody") == "dark"


def test_logged_out_with_a_stale_session_the_post_still_goes_to_the_login(client, app):
    add_user(app, "somebody", theme="dark")
    token = csrf_token(client, "/login")
    sign_in(client, 424242)

    response = post_theme(client, "light", token)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert stored_theme(app, "somebody") == "dark"


@pytest.mark.parametrize("method", ["get", "head", "put", "delete", "patch"])
def test_only_post_is_routed(client, app, method):
    user_id = add_user(app, "target", theme="dark")
    sign_in(client, user_id)
    token = csrf_token(client)

    if method in ("get", "head"):
        response = getattr(client, method)(f"{CHANGE_THEME}?theme=light")
    else:
        response = getattr(client, method)(CHANGE_THEME, data={"theme": "light", FIELD_NAME: token})

    assert response.status_code == 405
    assert stored_theme(app, "target") == "dark"


def test_logged_out_a_get_is_a_405_too(client, app):
    add_user(app, "somebody")

    assert client.get(CHANGE_THEME).status_code == 405


def test_the_route_never_changes_another_accounts_row(client, app):
    """Whatever the request names, the row written is the session's own."""
    mine = add_user(app, "mine", theme="light")
    boss = add_user(app, "boss", role="admin", theme="light")
    veteran = add_user_before_the_theme(app, "veteran")
    sign_in(client, mine)
    token = csrf_token(client)
    others_before = raw_rows(app, "SELECT * FROM user WHERE username != 'mine' ORDER BY id")
    mine_before = raw_rows(app, "SELECT id, username, password, role, registered, last_login, apitoken FROM user WHERE username = 'mine'")

    for named in (boss, veteran):
        response = client.post(
            f"{CHANGE_THEME}?user_id={named}&username=boss",
            data={"theme": "dark", FIELD_NAME: token, "user_id": named, "id": named, "username": "boss", "user": "veteran", "userid": named},
        )
        assert response.status_code == 302

    assert stored_theme(app, "mine") == "dark"
    assert raw_rows(app, "SELECT * FROM user WHERE username != 'mine' ORDER BY id") == others_before
    assert stored_theme(app, "veteran") is None, "a NULL column was written"
    assert raw_rows(app, "SELECT id, username, password, role, registered, last_login, apitoken FROM user WHERE username = 'mine'") == mine_before, "the change wrote more than the theme"
    assert raw_rows(app, "SELECT COUNT(*) FROM user") == [(3,)]


# --- 2. the settings form that posts to it ---------------------------------------------------

class ThemeForm(HTMLParser):
    """The form of the settings page that posts to /admin/change_theme, and its inputs."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.form = None
        self.inputs = []
        self._inside = False
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("action", "").endswith(CHANGE_THEME):
            self.form = attrs
            self._inside = True
        elif tag == "input" and self._inside:
            self.inputs.append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self._inside = False


@pytest.mark.parametrize("stored", [None, "light", "dark", "system"])
def test_the_settings_form_offers_each_theme_and_checks_the_one_in_force(client, app, stored):
    if stored is None:
        user_id = add_user_before_the_theme(app, "viewer")
    else:
        user_id = add_user(app, "viewer", theme=stored)
    sign_in(client, user_id)

    form = ThemeForm(client.get("/settings").data.decode())

    assert form.form is not None, "no form posts to /admin/change_theme"
    assert form.form["method"].lower() == "post"
    radios = [attrs for attrs in form.inputs if attrs.get("type") == "radio"]
    assert [attrs["value"] for attrs in radios] == list(THEMES)
    assert {attrs["name"] for attrs in radios} == {"theme"}
    assert [attrs["value"] for attrs in radios if "checked" in attrs] == [stored or "light"]
    tokens = [attrs for attrs in form.inputs if attrs.get("name") == FIELD_NAME]
    assert len(tokens) == 1 and tokens[0].get("type") == "hidden" and tokens[0].get("value")


# --- 3. base.html for each theme ------------------------------------------------------------

#: the three logos of the navbar: the light file each <img> falls back to, and the dark one
LOGOS = {
    "/static/d20_mcrit_cabaret.png": "/static/d20_mcrit_cabaret_dark.png",
    "/static/fkie_190x52.gif": "/static/fkie_190x52_dark.png",
}
DARK_STYLESHEET = "/static/theme-dark.css"


class Document(HTMLParser):
    """What the theme puts on a page, in document order: the <html> attributes, the <meta>s
    and the stylesheets of the <head>, every <link>, and every <picture>."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.html = None
        self.metas = []
        self.head_sheets = []  # ("link", attrs) / ("style", {}) of the <head>, in order
        self.links = []  # every <link> of the page, <head> or not
        self.pictures = []  # each a list of (tag, attrs): its <source>s and <img>
        self._in_head = False
        self._picture = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html" and self.html is None:
            self.html = attrs
        elif tag == "head":
            self._in_head = True
        elif tag == "meta" and self._in_head:
            self.metas.append(attrs)
        elif tag == "link":
            self.links.append(attrs)
            if self._in_head and attrs.get("rel") == "stylesheet":
                self.head_sheets.append(("link", attrs))
        elif tag == "style" and self._in_head:
            self.head_sheets.append(("style", attrs))
        elif tag == "picture":
            self._picture = []
            self.pictures.append(self._picture)
        elif tag in ("source", "img") and self._picture is not None:
            self._picture.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == "head":
            self._in_head = False
        elif tag == "picture":
            self._picture = None

    def logos(self):
        """The navbar's pictures, found by the light file their <img> shows."""
        return [picture for picture in self.pictures if any(tag == "img" and attrs.get("src") in LOGOS for tag, attrs in picture)]


@pytest.fixture
def signed_in_as_theme(client, app):
    """Sign the test client in as an account of the given theme (a visitor unless said otherwise)."""
    def _sign_in(theme, role="visitor"):
        sign_in(client, add_user(app, f"{theme}-viewer", role=role, theme=theme))
    return _sign_in


#: pages with something to say about the <head>: settings.html and jobs.html bring <style>
#: blocks of their own, which must not end up after the dark stylesheet
SIGNED_IN_PAGES = ["/settings", "/explore/samples", "/data/jobs"]


def check_theme_on_the_page(page, theme):
    expected = EXPECTED[theme]
    document = Document(page)

    assert document.html["data-theme"] == theme

    schemes = [attrs for attrs in document.metas if attrs.get("name") == "color-scheme"]
    assert [attrs.get("content") for attrs in schemes] == [expected["scheme"]], "exactly one colour-scheme <meta>"

    dark_links = [attrs for attrs in document.links if attrs.get("href", "").split("?")[0] == DARK_STYLESHEET]
    if expected["media"] is None:
        assert dark_links == [], "a light page links the dark stylesheet, so it downloads it"
        assert all(attrs.get("href", "").split("?")[0] != DARK_STYLESHEET for _tag, attrs in document.head_sheets)
    else:
        assert len(dark_links) == 1
        assert dark_links[0].get("rel") == "stylesheet"
        assert dark_links[0].get("media") == expected["media"]
        last_tag, last_attrs = document.head_sheets[-1]
        assert last_tag == "link" and last_attrs.get("href", "").split("?")[0] == DARK_STYLESHEET, (
            f"the dark stylesheet is not the last of the <head>'s stylesheets: {[(tag, attrs.get('href')) for tag, attrs in document.head_sheets]}"
        )
        assert len(document.head_sheets) > 3, "the page has the stylesheets the dark one has to follow"

    logos = document.logos()
    assert len(logos) == 3, "the navbar has the large and the small brand logo and the Fraunhofer one"
    for picture in logos:
        sources = [attrs for tag, attrs in picture if tag == "source"]
        images = [attrs for tag, attrs in picture if tag == "img"]
        assert len(images) == 1 and images[0]["src"] in LOGOS
        if expected["media"] is None:
            assert sources == [], "a light page offers the dark logo, so it fetches it"
        else:
            assert [tag for tag, _attrs in picture] == ["source", "img"], "the <source> has to come before the <img>"
            assert sources[0]["media"] == expected["media"], "the logo and the stylesheet switch on different queries"
            assert sources[0]["srcset"] == LOGOS[images[0]["src"]]
    return document


@pytest.mark.parametrize("page", SIGNED_IN_PAGES)
@pytest.mark.parametrize("theme", THEMES)
def test_a_signed_in_page_carries_its_accounts_theme(client, signed_in_as_theme, theme, page):
    signed_in_as_theme(theme)
    response = client.get(page)

    assert response.status_code == 200
    check_theme_on_the_page(response.data.decode(), theme)


@pytest.mark.parametrize("page", ["/login", "/register", "/help"])
def test_a_page_nobody_is_signed_in_to_is_always_light(client, app, page):
    """The theme is the account's. A visitor who is not signed in has none, and gets the
    light pages whatever their browser prefers; "system" is only ever an account's choice."""
    add_user(app, "somebody", theme="dark")

    response = client.get(page, follow_redirects=False)

    assert response.status_code == 200, page
    check_theme_on_the_page(response.data.decode(), "light")


def test_signing_out_ends_the_theme_with_the_session(client, app):
    sign_in(client, add_user(app, "leaver", theme="dark"))
    assert theme_of_page(client.get("/settings")) == "dark"

    client.get("/logout")

    check_theme_on_the_page(client.get("/login").data.decode(), "light")


@pytest.mark.parametrize("theme", ["dark", "system"])
def test_the_images_a_dark_page_is_offered_exist(client, signed_in_as_theme, theme):
    signed_in_as_theme(theme)
    document = Document(client.get("/settings").data.decode())

    assert len(document.logos()) == 3, "the navbar's three logos are not pictures: there is nothing to fetch"
    for picture in document.logos():
        for tag, attrs in picture:
            path = attrs["src"] if tag == "img" else attrs["srcset"]
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.mimetype.startswith("image/"), path


# --- 4. ScoreColorProvider: the tints --------------------------------------------------------

#: what a tint is made of: the hue of the score at the theme's strength over the theme's ground
TINT = re.compile(r"color-mix\(in srgb, #([0-9a-f]{6}) var\(--tint-strength\), var\(--tint-ground\)\)")
NO_SCORE = "var(--tint-ground)"


def hue_of(tint):
    match = TINT.fullmatch(tint)
    assert match, f"not a tint: {tint!r}"
    return match.group(1)


class Scored:
    """A stand-in for the match result and function entries the templates pass in."""

    def __init__(self, **attributes):
        self.__dict__.update(attributes)


def test_a_match_tint_is_the_hue_of_the_score_at_the_themes_strength():
    provider = ScoreColorProvider()

    for score in (*range(0, 101, 5), 1, 9, 10, 29, 49, 59, 99, 100, 101, 250):
        for tint, hue in (
            (provider.getMatchTintByScore100(score), provider.getMatchHexColorByScore100(score)),
            (provider.getMatchTintByScore50(score), provider.getMatchHexColorByScore50(score)),
        ):
            if hue == "ffffff":
                assert tint == NO_SCORE
            else:
                assert hue_of(tint) == hue, f"score {score}"


def test_a_tint_from_a_result_follows_the_score_type_and_the_scale():
    provider = ScoreColorProvider()
    result = Scored(
        matched_percent_score_weighted=95.0,
        matched_percent_frequency_weighted=72.5,
        matched_percent_nonlib_frequency_weighted=12.0,
        matched_score=75,
    )

    assert hue_of(provider.getMatchTintFromResult(result, "matched_percent_score_weighted")) == provider.getMatchHexColorByScore100(95.0)
    assert hue_of(provider.getMatchTintFromResult(result, "matched_percent_frequency_weighted")) == provider.getMatchHexColorByScore100(72.5)
    assert hue_of(provider.getMatchTintFromResult(result, "matched_percent_nonlib_frequency_weighted", scale=100)) == provider.getMatchHexColorByScore100(12.0)
    assert hue_of(provider.getMatchTintFromResult(result, "matched_score", scale=50)) == provider.getMatchHexColorByScore50(75)
    assert hue_of(provider.getMatchTintFromResult(result, "matched_score")) == provider.getMatchHexColorByScore100(75)
    assert provider.getMatchTintFromResult(result, "matched_score", scale=50) != provider.getMatchTintFromResult(result, "matched_score"), "75 is a different band on the two scales"


def test_no_score_tints_with_the_ground_so_that_a_cell_without_a_score_leaves_its_row_alone():
    provider = ScoreColorProvider()

    assert provider.getMatchTintByScore100(0) == NO_SCORE
    assert provider.getMatchTintByScore100(-3) == NO_SCORE
    assert provider.getMatchTintByScore50(49) == NO_SCORE
    assert provider.getMatchTintByScore50(0) == NO_SCORE
    assert provider.getMatchTintFromResult(Scored(matched_percent_score_weighted=0.0), "matched_percent_score_weighted") == NO_SCORE
    assert provider.getUniqueTint(0) == NO_SCORE
    assert provider.getUniqueTint(None) == NO_SCORE
    assert provider.getUniqueTint(-1) == NO_SCORE


def test_a_unique_score_is_one_hue_whatever_its_size():
    provider = ScoreColorProvider()

    hues = {hue_of(provider.getUniqueTint(score)) for score in (0.5, 1, 25, 60, 100)}

    assert hues == {provider.getMatchHexColorByScore100(60)}


def test_a_tint_writes_no_colour_of_its_own():
    """The page decides how strongly a hue shows and over what; the provider only names the hue."""
    provider = ScoreColorProvider()
    tints = [provider.getMatchTintByScore100(score) for score in range(0, 101, 7)]
    tints += [provider.getMatchTintByScore50(score) for score in range(40, 111, 7)]
    tints += [provider.getUniqueTint(5)]

    for tint in tints:
        assert tint == NO_SCORE or TINT.fullmatch(tint), tint
        assert not re.search(r"(?<![a-z])rgba?\(", tint) and "%" not in tint, "a colour or a strength written into the tint"


def test_on_the_light_theme_a_tint_is_the_blend_the_pages_always_had_give_or_take_a_rounding_step():
    """The docstring says so: 40% of the hue into white. master baked it in with int() of
    255 - 0.4 * (255 - c); the browser rounds 0.4 * c + 0.6 * 255."""
    provider = ScoreColorProvider()
    hues = {hue_of(provider.getMatchTintByScore100(score)) for score in range(1, 101)}
    hues |= {hue_of(provider.getMatchTintByScore50(score)) for score in range(50, 111)}
    assert len(hues) >= 13

    for hue in hues:
        channels = [int(hue[i:i + 2], 16) for i in (0, 2, 4)]
        master = [int(255 - 0.4 * (255 - c)) for c in channels]
        light = [round(0.4 * c + 0.6 * 255) for c in channels]
        assert max(abs(a - b) for a, b in zip(master, light)) <= 1, hue


@pytest.mark.parametrize("name", ["getMatchHexColor", "getUniqueColorScore", "getFrequencyHexColor"])
def test_no_template_asks_for_a_baked_in_hex(name):
    """A hex blended toward white is a light-page colour that no theme can change."""
    offenders = [path.relative_to(ROOT).as_posix() for path in sorted(TEMPLATES.rglob("*.html")) if name in path.read_text(encoding="utf-8")]

    assert offenders == [], f"{name}* is still called from {offenders}; the templates use the getMatchTint* / getUniqueTint methods"


def test_the_templates_that_tint_a_row_or_a_cell_use_the_tint_methods():
    called = collections.Counter()
    for path in sorted(TEMPLATES.rglob("*.html")):
        called.update(re.findall(r"scp\.(\w+)\(", path.read_text(encoding="utf-8")))

    assert set(called) == {"getMatchTintFromResult", "getMatchTintByScore100", "getMatchTintByScore50", "getUniqueTint"}
    assert sum(called.values()) >= 20


# --- 5. the palette --------------------------------------------------------------------------

@functools.cache
def light_properties():
    return themeCss.light_palette()


@functools.cache
def dark_properties():
    return themeCss.dark_palette()


@functools.cache
def dark_cascade():
    return themeCss.dark_cascade()


@functools.cache
def dark_rules():
    return themeCss.dark_rules()


@functools.cache
def light_rules():
    return themeCss.light_rules()


#: The light palette as master (6564602) had it, token by token, each with where on master the
#: literal stood. Pinned here so that a change to a light page cannot hide in the palette: the
#: tests compare what static/style.css says now with these, as colours (so `#FFF` and `white`
#: are the same, a different shade is not). The twelve block matching colours of the CFG viewer
#: are pinned in cfgColors.py already and added below, not written twice.
#: A token that master had no literal for has no business being here; a new colour is a new
#: line, with the literal it replaces.
MASTER_LIGHT = {
    # static/style.css `html`, `body` (:3, :12)
    "--surface-page": "white",
    # nav (:63), .nav-pills .nav-link.active (:170), .thead-light, .accordion-button, .page-item.active,
    # .mcrit-diagram (:290), the drop zone (submit_or_query_dropzone.html:6)
    "--surface-chrome": "rgb(240, 240, 240)",
    # .manual pre (:269), settings.html `.list-group.col` (:13, written #f8f9fa)
    "--surface-subtle": "rgb(248, 249, 250)",
    # .flash (:109)
    "--surface-info": "#cae6f6",
    # page_loading.js (:42) `background:rgba(255,255,255,0.6)`
    "--surface-veil": "rgba(255, 255, 255, 0.6)",
    # Bootstrap 5.0.2's `body` colour: master did not set one, the vendored stylesheet did
    "--text-body": "#212529",
    # .nav-pills .nav-link (:171), .accordion-button (:179), .page-link (:198), and `color: black` in the templates
    "--text-strong": "black",
    # Bootstrap's .text-muted, and the templates' `color: #6c757d`
    "--text-muted": "#6c757d",
    # .post .about (:129) and .mcrit-diagram::before (:300)
    "--text-subtle": "slategray",
    # input.danger (:163)
    "--text-danger": "#cc2f2e",
    # h1..h6 (:16), hr (:57), .flash (:110)
    "--accent": "#377ba8",
    # a (:27)
    "--link": "#377ba8",
    # tr[style*="background-color"] a (:33)
    "--link-on-tint": "#17466b",
    # table.html:24 and unique_blocks.html:176 `color: #0d6efd`
    "--link-plain": "#0d6efd",
    # Bootstrap's a:hover, which master's `a` rule never overrode
    "--link-hover": "#0a58ca",
    # single_function.html:212 and result_compare_function_vs.html `color:#3f51b5`
    "--text-progress": "#3f51b5",
    # .content > header (:96)
    "--rule-subtle": "lightgray",
    # .manual img, table, h1 (:252, :264, :277)
    "--border-subtle": "rgb(222, 226, 230)",
    # jobs.html `.table-bordered` (:33, :41)
    "--border-strong": "#333",
    # .cfg-legend span.swatch, .edge-swatch (:361, :368)
    "--border-swatch": "#444",
    # submit_or_query_dropzone.html:6 and import.html:7 `2px dashed black`
    "--dropzone-border": "black",
    # .fa-sort (:206), .fa-sort-up, .fa-sort-down (:210)
    "--sort-idle": "rgba(0, 0, 0, 0.3)",
    "--sort-active": "rgba(0, 0, 0, 1)",
    # .accordion-button:focus (:189)
    "--accordion-focus-ring": "#FFF",
    # cross_compare.html:20 and unique_blocks.html:155 (`yellowgreen`), and ROW_TINTS in row_decoration.html:22
    "--row-selected": "yellowgreen",
    # compare.html:22, cross_compare.html:23 and the others (`rgb(240, 240, 240)`), ROW_TINTS
    "--row-pending": "rgb(240, 240, 240)",
    # the `color:green` / `color:darkred` icons of sample_row.html:142, family_row.html:105 and a dozen more
    "--status-yes": "green",
    "--status-no": "darkred",
    # ScoreColorProvider blended 40% of the hue into white
    "--tint-ground": "white",
    "--tint-strength": "40%",
    # style.css #xcfg_combined (:329), .node.matched rect (:340), #tooltip_c (:347, :349)
    "--cfg-canvas": "#FFFFFF",
    "--cfg-matched": "#ff7f00",
    "--cfg-tooltip-bg": "white",
    "--cfg-tooltip-shadow": "4px 4px 10px rgba(0, 0, 0, 0.4)",
    # result_compare_function_vs.html:82 and single_function.html:203, the first loop colour
    "--cfg-loop-label": "#fdd0a2",
}
PINNED_LIGHT = {**MASTER_LIGHT, **LIGHT_CFG_COLORS}


def colour_or_text(value, properties):
    """A token's value as something comparable: its (r, g, b, a) if it is a colour, else its
    text with the whitespace normalized."""
    try:
        return tuple(round(channel, 6) for channel in parse_colour(value, themeCss.resolver(properties)))
    except ValueError:
        return re.sub(r"\s+", " ", value.strip().lower())


def test_the_stylesheet_is_a_palette_first_and_the_palette_is_one_block():
    rules = light_rules()
    assert rules[0].selector == ":root", "the token block is the first rule of style.css"
    assert [rule.selector for rule in rules].count(":root") == 1, "a second :root block would define names the first does not show"
    assert themeCss.duplicated_names(rules[0]) == []


def test_the_dark_palette_is_one_block_and_defines_nothing_twice():
    rules = dark_rules()
    assert [rule.selector for rule in rules].count(":root") == 1
    assert themeCss.duplicated_names(themeCss.root_rule(rules)) == []
    assert rules[0].selector == ":root", "the palette is the first rule of theme-dark.css"


def test_the_dark_palette_redefines_every_name_the_light_one_defines():
    missing = sorted(set(light_properties()) - set(dark_properties()))

    assert missing == [], f"theme-dark.css leaves {missing} at their light values: those parts of a dark page stay light"


def test_the_dark_palette_is_dark():
    dark = themeCss.resolver(dark_cascade())
    page = parse_colour("var(--surface-page)", dark)[:3]
    body = parse_colour("var(--text-body)", dark)[:3]

    assert themeCss.luminance(page) < 0.03, "the page ground is not dark"
    assert themeCss.luminance(body) > 0.5, "the body text is not light"
    for name in ("--surface-chrome", "--surface-subtle", "--surface-raised", "--surface-input", "--surface-active"):
        assert themeCss.luminance(parse_colour(f"var({name})", dark)[:3]) < 0.1, name
    assert {d.name: d.value for d in themeCss.root_rule(dark_rules()).declarations}["color-scheme"] == "dark", (
        "without it the scrollbars, the form controls and the canvas stay light"
    )


def test_every_dark_colour_is_a_colour_and_none_is_a_copy_of_the_light_one():
    light, dark = themeCss.resolver(light_properties()), themeCss.resolver(dark_cascade())
    same = []
    for name, light_value in light_properties().items():
        try:
            light_colour = parse_colour(light_value, light)
        except ValueError:
            continue  # --tint-strength, --cfg-tooltip-shadow: not colours
        dark_value = dark_properties()[name]
        if dark_value.startswith("color-mix("):
            continue  # --cfg-loop-label: the loop colour at the hull opacity over the canvas
        assert parse_colour(dark_value, dark), name  # raises on a value that is not a colour
        if parse_colour(dark_value, dark) == light_colour:
            same.append(name)

    assert same == [], f"{same} are the same in both themes: a dark value copied from the light one, or a token that does not need to be a token"


@pytest.mark.parametrize("name", sorted(PINNED_LIGHT))
def test_a_light_value_is_what_master_had(name):
    assert name in light_properties(), f"{name} is gone from style.css"

    assert colour_or_text(light_properties()[name], light_properties()) == colour_or_text(PINNED_LIGHT[name], PINNED_LIGHT), (
        f"{name} is {light_properties()[name]!r} in style.css; master painted {PINNED_LIGHT[name]!r}, and the light pages are meant to stay as they were"
    )


def test_every_light_token_is_pinned_to_a_literal_master_had():
    unpinned = sorted(set(light_properties()) - set(PINNED_LIGHT))
    stale = sorted(set(PINNED_LIGHT) - set(light_properties()))

    assert unpinned == [], f"{unpinned}: add the literal on master each replaces to MASTER_LIGHT, or the token changes the light pages without a record"
    assert stale == []


@functools.cache
def bootstrap_rules():
    return themeCss.parse_css((STATIC / "bootstrap-5.0.2-dist" / "css" / "bootstrap.css").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "selector, declaration, expected",
    [
        ("body", "color", "#212529"),
        ("a:hover", "color", "#0a58ca"),
        (".text-muted", "color", "#6c757d"),
    ],
)
def test_the_three_light_values_master_took_from_bootstrap_are_still_bootstrap_s(selector, declaration, expected):
    """--text-body, --link-hover and --text-muted are not literals of master's own: they are
    what the vendored Bootstrap 5.0.2 painted. Pinned above; here is where they come from."""
    values = [d.value for rule in bootstrap_rules() if rule.selector == selector for d in rule.declarations if d.name == declaration]

    assert expected in [value.replace(" !important", "").strip() for value in values]


# --- 5. names: every var() has something to resolve to ----------------------------------------

VAR_REFERENCE = re.compile(r"var\(\s*(--[a-zA-Z0-9_]+(?:-[a-zA-Z0-9_]+)*)\s*[,)]")


def comments_removed(text):
    text = re.sub(r"\{#.*?#\}", " ", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def python_strings(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def names_used_outside_the_dark_stylesheet():
    """name -> the files that write var(name), for everything the light theme renders with:
    style.css, the other project stylesheets, the templates, the project scripts and the
    Python that hands a var() to a page (functiondiff, ScoreColorProvider)."""
    used = collections.defaultdict(set)
    for path in [STATIC / "style.css", STATIC / "navbar.css", STATIC / "signin.css", *sorted(TEMPLATES.rglob("*.html"))]:
        for name in VAR_REFERENCE.findall(comments_removed(path.read_text(encoding="utf-8"))):
            used[name].add(path.name)
    for path in project_scripts():
        _code, strings = javascript_skeleton(path.read_text(encoding="utf-8"))
        for _offset, content in strings:
            for name in VAR_REFERENCE.findall(content):
                used[name].add(path.name)
    for path in sorted(PACKAGE.rglob("*.py")):
        for value in python_strings(path):
            for name in VAR_REFERENCE.findall(value):
                used[name].add(path.name)
    return used


def test_every_var_the_light_theme_writes_names_a_token_style_css_defines():
    used = names_used_outside_the_dark_stylesheet()
    undefined = {name: sorted(files) for name, files in used.items() if name not in light_properties() and not name.startswith(("--bs-", "--fa-"))}

    assert undefined == {}, f"var() of a name no :root block of style.css defines (the light theme resolves it to nothing): {undefined}"
    assert len(used) >= 30, "the scan found hardly any var(): it has stopped looking"


def test_a_name_only_the_dark_stylesheet_defines_is_used_only_inside_it():
    dark_only = set(dark_properties()) - set(light_properties())
    used = names_used_outside_the_dark_stylesheet()

    leaked = {name: sorted(used[name]) for name in dark_only if name in used}

    assert leaked == {}, f"{leaked} are defined for the dark theme alone and used by something the light theme renders"
    assert len(dark_only) >= 10


def test_every_var_the_dark_stylesheet_writes_names_something_defined():
    defined = set(dark_cascade())
    used = set()
    for rule in dark_rules():
        for declaration in rule.declarations:
            used.update(VAR_REFERENCE.findall(declaration.value))
    undefined = sorted(name for name in used - defined if not name.startswith("--bs-"))

    assert undefined == [], f"theme-dark.css writes var() of {undefined}, which nothing defines"
    assert len(used) >= 30


def test_the_bootstrap_properties_the_dark_stylesheet_sets_are_ones_bootstrap_5_0_2_has():
    """5.0.2 predates Bootstrap's colour modes, so most of the --bs-* names of 5.3 do not exist
    in it and setting one would do nothing."""
    bootstrap = (STATIC / "bootstrap-5.0.2-dist" / "css" / "bootstrap.css").read_text(encoding="utf-8")
    set_by_dark = {d.name for rule in dark_rules() for d in rule.declarations if d.name.startswith("--bs-")}

    assert set_by_dark, "the dark stylesheet sets none, the table colours would be light"
    assert sorted(name for name in set_by_dark if name + ":" not in bootstrap and name + " :" not in bootstrap) == []


# --- 5. the dark stylesheet: part two outranks the vendored selectors -------------------------

def test_every_rule_after_the_palette_starts_at_root():
    """`:root .x` has the specificity of `.x` plus a pseudo-class, so it wins over Bootstrap's `.x`
    whatever order the stylesheets end up linked in (a PR that moves stylesheets after this one
    changes the order, and the selectors must not care). The palette block and ::selection are
    the two rules that are not about a vendored selector."""
    rules = dark_rules()
    offenders = []
    for rule in rules:
        if rule.selector in (":root", "::selection") and not rule.context:
            continue
        for selector in themeCss.selectors_of(rule):
            if not re.match(r":root\s+\S", selector):
                offenders.append(selector)

    assert offenders == [], f"selectors that do not start with ':root ': {offenders}"
    assert len(rules) > 100, "the dark stylesheet has stopped being read"


def test_the_dark_stylesheet_has_a_part_two_marker_and_the_palette_is_before_it():
    css = themeCss.read_static("theme-dark.css")

    assert css.count("part two") >= 1
    assert css.index(":root {") < css.index("part two")


def test_the_dark_stylesheet_has_no_at_rule_that_could_hide_a_light_rule():
    rules = dark_rules()

    assert [rule.context for rule in rules if rule.context] == [], "an @media or @supports block: its rules have to start with ':root ' too, and be checked"
    assert not [rule.selector for rule in rules if rule.selector.startswith("@")]


# --- 5. contrast, measured from the values ----------------------------------------------------

def rgb_of(properties, name):
    """A token as an opaque (r, g, b): a translucent one is laid over the page ground."""
    lookup = themeCss.resolver(properties)
    colour = parse_colour(f"var({name})", lookup)
    ground = parse_colour("var(--surface-page)", lookup)[:3]
    return themeCss.composite(colour, ground)


def tint_hues():
    """The hues the score maps paint, as the provider hands them out: every one of the
    tints for every score the two scales distinguish, and the unique tint."""
    provider = ScoreColorProvider()
    tints = set()
    for score in range(-5, 140):
        tints.add(provider.getMatchTintByScore100(score))
        tints.add(provider.getMatchTintByScore50(score))
        tints.add(provider.getUniqueTint(score))
    hues = {hue_of(tint) for tint in tints if tint != NO_SCORE}
    assert len(hues) >= 14, "fewer hues than the maps have: the walk missed a band"
    return sorted(hues)


def tint_of(properties, hue):
    """What a hue shows as at the theme's strength over the theme's ground."""
    strength = float(properties["--tint-strength"].rstrip("%")) / 100
    ground = parse_colour("var(--tint-ground)", themeCss.resolver(properties))[:3]
    return themeCss.mix(tuple(int(hue[i:i + 2], 16) for i in (0, 2, 4)), ground, strength)


def worst_on_tints(properties, foreground):
    """The lowest contrast of `foreground` against any tint, and the hue it is against."""
    fg = rgb_of(properties, foreground)
    return min((contrast(fg, tint_of(properties, hue)), hue) for hue in tint_hues())


TEXT_ROLES = ["--text-body", "--text-strong", "--text-muted", "--text-subtle", "--link", "--link-plain", "--link-hover", "--text-danger", "--status-yes", "--status-no", "--accent", "--text-progress"]
LIGHT_SURFACES = ["--surface-page", "--surface-chrome", "--surface-subtle", "--surface-info"]
#: the dark theme's raised and active surfaces are where menus, dialogs, list items and the
#: active pill sit; --surface-input is a field's own fill
DARK_SURFACES = LIGHT_SURFACES + ["--surface-raised", "--surface-active", "--surface-input"]


def test_dark_text_is_readable_on_every_surface_it_sits_on():
    failures = []
    for foreground in TEXT_ROLES:
        for surface in DARK_SURFACES:
            ratio = contrast(rgb_of(dark_cascade(), foreground), rgb_of(dark_cascade(), surface))
            if ratio < 4.5:
                failures.append(f"{foreground} on {surface}: {ratio:.2f}")

    assert failures == [], f"below 4.5:1 {failures}"


def test_dark_body_text_and_links_on_tinted_rows_are_readable_on_the_worst_tint():
    """A score tint is the hue at --tint-strength over --tint-ground. The yellow and the lime
    are the brightest of them; the links on a row are --link-on-tint because --link is not
    readable there (3.3:1 on the yellow)."""
    failures = []
    for foreground in ("--text-body", "--text-strong", "--link-on-tint"):
        ratio, hue = worst_on_tints(dark_cascade(), foreground)
        if ratio < 4.5:
            failures.append(f"{foreground} on the tint of #{hue}: {ratio:.2f}")

    assert failures == [], f"below 4.5:1 {failures}"


def test_dark_status_icons_on_tinted_rows_hold_3_to_1_on_the_worst_tint():
    failures = []
    for foreground in ("--status-yes", "--status-no"):
        ratio, hue = worst_on_tints(dark_cascade(), foreground)
        if ratio < 3:
            failures.append(f"{foreground} on the tint of #{hue}: {ratio:.2f}")

    assert failures == [], f"below 3:1 {failures}"


def test_dark_picker_rows_keep_their_text_links_and_icons_readable():
    failures = []
    for row in ("--row-selected", "--row-pending"):
        for foreground, minimum in (("--text-body", 4.5), ("--text-strong", 4.5), ("--link-on-tint", 4.5), ("--status-yes", 3), ("--status-no", 3)):
            ratio = contrast(rgb_of(dark_cascade(), foreground), rgb_of(dark_cascade(), row))
            if ratio < minimum:
                failures.append(f"{foreground} on {row}: {ratio:.2f} < {minimum}")

    assert failures == [], failures


def test_dark_control_borders_hold_3_to_1_on_the_surfaces_a_control_is_drawn_on():
    """WCAG 1.4.11. A field's border has its own fill on one side (--surface-input) and the
    page, the grey chrome or a panel on the other."""
    failures = []
    for surface in ("--surface-page", "--surface-subtle", "--surface-chrome", "--surface-input"):
        ratio = contrast(rgb_of(dark_cascade(), "--border-control"), rgb_of(dark_cascade(), surface))
        if ratio < 3:
            failures.append(f"--border-control on {surface}: {ratio:.3f}")

    assert failures == [], f"below 3:1 {failures}"


def test_the_dark_drop_zone_border_holds_3_to_1_against_its_fill_and_what_surrounds_it():
    """The dashed border is the whole of the drop target's outline. The target sits in the
    modal of every page (base.html's #submitFileModal, a raised surface) and in the pages
    that embed it (the page ground), and is filled with --surface-chrome."""
    border = rgb_of(dark_cascade(), "--dropzone-border")
    failures = []
    for place in ("--surface-chrome", "--surface-raised", "--surface-page"):
        ratio = contrast(border, rgb_of(dark_cascade(), place))
        if ratio < 3:
            failures.append(f"--dropzone-border on {place}: {ratio:.3f}")

    assert failures == [], f"below 3:1 {failures}"


def test_the_dark_focus_ring_and_the_focus_border_hold_3_to_1_against_what_they_surround():
    """The ring is a translucent shadow (--focus-ring), the focus border of a field the accent. A
    button's own border or fill can carry its focus without the ring (testThemeBrowser.py measures
    that); an accordion header has the ring alone (--accordion-focus-ring), on the grey chrome."""
    lookup = themeCss.resolver(dark_cascade())
    ring = parse_colour("var(--focus-ring)", lookup)
    failures = []
    for surface in ("--surface-page", "--surface-chrome"):
        ground = rgb_of(dark_cascade(), surface)
        ratio = contrast(themeCss.composite(ring, ground), ground)
        if ratio < 3:
            failures.append(f"--focus-ring over {surface}: {ratio:.3f}")
    accent = contrast(rgb_of(dark_cascade(), "--accent"), rgb_of(dark_cascade(), "--surface-page"))
    if accent < 3:
        failures.append(f"--accent on --surface-page: {accent:.3f}")

    assert failures == [], f"below 3:1 {failures}"


def test_the_idle_sort_arrow_is_at_least_as_visible_in_dark_as_it_was_on_master():
    """The arrow of a sortable column head, before the column is sorted, is faint by design: master
    drew it at 30% black on the grey head, 2.09:1. testThemeBrowser.py leaves it out of the 3:1 it
    asks of icons for that reason; this is what holds it to master's own bar."""
    master_head = rgb_of(PINNED_LIGHT, "--surface-chrome")
    master_arrow = themeCss.composite(parse_colour(PINNED_LIGHT["--sort-idle"]), master_head)
    dark_head = rgb_of(dark_cascade(), "--surface-chrome")
    dark_arrow = themeCss.composite(parse_colour(dark_cascade()["--sort-idle"]), dark_head)

    assert contrast(dark_arrow, dark_head) >= contrast(master_arrow, master_head)
    assert contrast(master_arrow, master_head) < 2.1, "the figure in the docstring is master's"


def test_the_dark_tooltip_is_readable():
    ratio = contrast(rgb_of(dark_cascade(), "--tooltip-text"), rgb_of(dark_cascade(), "--tooltip-bg"))

    assert ratio >= 4.5, f"{ratio:.2f}"


def test_every_dark_rule_that_sets_a_text_and_a_background_pairs_them_readably():
    """Every rule of the dark stylesheet that gives both a colour and a background (buttons,
    alerts, table variants, ::selection) is a pair a reader meets. A translucent background is
    laid over the page, the chrome and a raised surface and the worst is taken."""
    lookup = themeCss.resolver(dark_cascade())
    grounds = [rgb_of(dark_cascade(), name) for name in ("--surface-page", "--surface-chrome", "--surface-raised")]
    failures, pairs = [], 0
    for rule in dark_rules():
        values = {d.name: d.value for d in rule.declarations}
        text = values.get("color")
        background = values.get("background-color") or values.get("--bs-table-bg")
        if not text or not background or text in ("inherit", "currentcolor"):
            continue
        pairs += 1
        worst = min(_pair_contrast(parse_colour(text, lookup), parse_colour(background, lookup), ground) for ground in grounds)
        if worst < 4.5:
            failures.append(f"{' | '.join(themeCss.selectors_of(rule))[:90]}: {text} on {background}: {worst:.2f}")

    assert failures == [], f"below 4.5:1 {failures}"
    assert pairs >= 25, f"only {pairs} pairs found: the scan has stopped looking"


def _pair_contrast(text, background, ground):
    shown_background = themeCss.composite(background, ground)
    return contrast(themeCss.composite(text, shown_background), shown_background)


def light_measurements(properties):
    """The pairs the light theme can be compared on, name -> contrast, from a set of tokens."""
    measured = {}
    for foreground in TEXT_ROLES:
        for surface in LIGHT_SURFACES:
            measured[f"{foreground} on {surface}"] = contrast(rgb_of(properties, foreground), rgb_of(properties, surface))
    for foreground in ("--text-body", "--text-strong", "--link-on-tint", "--status-yes", "--status-no"):
        measured[f"{foreground} on the worst tint"] = worst_on_tints(properties, foreground)[0]
    for row in ("--row-selected", "--row-pending"):
        for foreground in ("--text-body", "--text-strong", "--link-on-tint", "--status-yes", "--status-no"):
            measured[f"{foreground} on {row}"] = contrast(rgb_of(properties, foreground), rgb_of(properties, row))
    return measured


def test_the_light_theme_is_no_worse_than_master_on_any_of_these_pairs():
    """The light values are master's, so this is a record more than a test: every pair the dark
    theme is held to, measured at the light values and at the literals master had."""
    now = light_measurements(light_properties())
    master = light_measurements(PINNED_LIGHT)

    worse = {pair: (round(now[pair], 3), round(master[pair], 3)) for pair in master if now[pair] < master[pair] - 1e-9}

    assert worse == {}, f"(now, master) {worse}"
    assert len(master) >= 60


# --- 6. the ratchet: no colour is written outside the two palettes ---------------------------
#
# The scanners below read what the project writes where a colour can be: the declarations of
# the project stylesheets, <style> blocks and style="..." attributes of the templates, the
# string literals of the project scripts (and of the scripts and Jinja tags inside templates),
# and the HTML attributes that take a colour. They do not look at prose: a comment, a class
# name, `white-space`, the word "green" in a sentence. What they look for is a hex, a colour
# function (rgb, hsl, ...) or one of the 148 named colours.

#: Scripts and stylesheets in static/ that the project did not write (AGENTS.md lists them).
#: Everything else in the top level of static/ is project code and is scanned; a new file
#: therefore fails the classification test below until someone says what it is.
VENDORED_SCRIPTS = {"autocomplete.js", "dropzone.js", "dropzone.min.js", "jquery.js", "jquery-ui.js", "jquery.dataTables.min.js", "dataTables.bootstrap5.min.js"}
VENDORED_STYLESHEETS = {"dropzone.min.css", "dataTables.bootstrap5.min.css", "hint.css", "jquery-ui.css"}
VENDORED_DIRECTORIES = {"bootstrap-5.0.2-dist", "css", "images", "sortablejs", "trace_CFG", "webfonts"}
#: the dark palette itself is where the dark colours are written
DARK_STYLESHEET_FILE = "theme-dark.css"

#: Words a colour scanner has to leave alone, and why. None of them is in the table of named
#: colours; the test below holds the scanner to that.
KEYWORDS_NOT_FLAGGED = {
    "transparent": "paints nothing, so there is no light or dark to it",
    "currentcolor": "the element's own text colour, which is a token",
    "inherit": "the parent's value, which is a token's",
    "initial": "the property's default, not a colour of ours",
    "unset": "inherit or initial, whichever the property does",
}

#: Colours that stay as written because they are data, not interface: the same in both themes.
#: (file, regex on the source line, reason). Each has to match something, or it is stale.
ALLOWED_DATA_COLOURS = [
    (
        "result_cross.html",
        r"score_to_color\(",
        "the cross compare matrix: one square per pair of samples, painted in full saturation by score_to_color (views/cross_compare.py) "
        "with no text on it, the same in both themes; theme-dark.css dims the squares with a filter (.score-swatch)",
    ),
    (
        "result_cross.html",
        r"lazy\.palette\[",
        "the same squares in the tabs that are filled in the browser: the palette of score_to_color, sent with the page, written onto a cloned .score-swatch",
    ),
]

#: Properties whose values are words that are not colours (names, strings), even when a word
#: is the name of one.
NON_COLOUR_PROPERTIES = {"content", "font-family", "font", "animation-name", "transition-property", "will-change", "grid-area", "grid-template-areas", "counter-reset", "counter-increment", "quotes", "src", "font-feature-settings"}

HEX = re.compile(r"#[0-9a-fA-F]{3,8}(?![\w-])")
FUNCTION = re.compile(r"(?<![\w-])(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|device-cmyk)\(", re.I)
WORD = re.compile(r"(?<![\w#-])[A-Za-z]+(?![\w-])")
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
JINJA_STRING = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"")
URL_SCHEMES = {"http", "https", "data", "javascript", "mailto", "url", "file"}
#: a JavaScript statement that paints something
PAINT_SINK = re.compile(r"[cC]olor|background|border|outline|fill|stroke|[sS]hadow")

Finding = collections.namedtuple("Finding", "line kind literal")


def css_value_literals(value, property_name=""):
    """The colour literals in the value of one declaration: hexes, colour functions and, unless
    the property takes names and strings, named colours."""
    cleaned = re.sub(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'", " ", value)
    cleaned = re.sub(r"url\([^)]*\)", " ", cleaned, flags=re.I)
    cleaned = cleaned.replace("\x01", " ").replace("\x02", " ")
    found = [match.group(0) for match in HEX.finditer(cleaned)]
    found += [match.group(0) for match in FUNCTION.finditer(cleaned)]
    if property_name.lower() not in NON_COLOUR_PROPERTIES:
        found += [match.group(0) for match in WORD.finditer(cleaned) if match.group(0).lower() in themeCss.NAMED_COLOURS]
    return found


def colours_in_string(content):
    """The colour literals a string literal stands for: the whole string a colour, or a bit
    of CSS (`prop: value; ...`, a style="..." attribute) with one in it."""
    text = content.strip()
    if not text:
        return []
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", text) or text.lower() in themeCss.NAMED_COLOURS:
        return [text]
    found = [match.group(0) for match in FUNCTION.finditer(text)]
    for match in re.finditer(r"(?<![\w-])([a-z][a-z-]*)\s*:\s*([^;\"']+)", text):
        name, value = match.groups()
        if name not in URL_SCHEMES:
            found += css_value_literals(value, name)
    return list(dict.fromkeys(found))


def declarations_of(css_text):
    return themeCss.parse_css("a{" + css_text.replace("\x01", " ").replace("\x02", " ") + "}")[0].declarations


def javascript_skeleton(source):
    """(code, strings): the script with its comments blanked and every string literal replaced
    by a placeholder \\x03<n>\\x03, and the strings as [(offset, content)]. Newlines are kept,
    so a position in the code is a line in the source. Regular expression literals are skipped
    as one token. Raises on a string or comment that does not end: a reader that has lost its
    place must not go on and report nothing."""
    code, strings = [], []
    i, n = 0, len(source)
    previous = ""
    while i < n:
        char = source[i]
        following = source[i + 1] if i + 1 < n else ""
        if char == "/" and following == "/":
            end = source.find("\n", i)
            end = n if end < 0 else end
            code.append(" " * (end - i))
            i = end
        elif char == "/" and following == "*":
            end = source.find("*/", i + 2)
            if end < 0:
                raise ValueError("a comment that never ends")
            code.append(re.sub(r"[^\n]", " ", source[i:end + 2]))
            i = end + 2
        elif char in "\"'`":
            j = i + 1
            while j < n and source[j] != char:
                if source[j] == "\\":
                    j += 2
                elif char == "`" and source.startswith("${", j):
                    depth = 0
                    while j < n:
                        depth += {"{": 1, "}": -1}.get(source[j], 0)
                        j += 1
                        if depth == 0:
                            break
                else:
                    j += 1
            if j >= n:
                raise ValueError(f"a string that never ends, from offset {i}")
            strings.append((i, source[i + 1:j]))
            code.append(f"\x03{len(strings) - 1}\x03" + "\n" * source.count("\n", i, j))
            previous = '"'
            i = j + 1
        elif char == "/" and (previous == "" or previous in "(,=:[!&|?{};"):
            j = i + 1
            in_class = False
            while j < n and (source[j] != "/" or in_class):
                if source[j] == "\\":
                    j += 1
                elif source[j] == "[":
                    in_class = True
                elif source[j] == "]":
                    in_class = False
                j += 1
            if j >= n:
                raise ValueError(f"a regular expression that never ends, from offset {i}")
            code.append(" ")
            previous = ")"
            i = j + 1
        else:
            code.append(char)
            if not char.isspace():
                previous = char
            i += 1
    return "".join(code), strings


def scan_script(source, base_line=1):
    """Findings in a script: colours in its strings, and a hex put together at run time."""
    code, strings = javascript_skeleton(source)
    findings = []
    for offset, content in strings:
        line = base_line + source.count("\n", 0, offset)
        findings += [Finding(line, "colour in a string", literal) for literal in colours_in_string(content)]
    for statement in re.split(r"[;\n]", code):
        if not PAINT_SINK.search(statement):
            continue
        for index in re.findall(r"\x03(\d+)\x03", statement):
            offset, content = strings[int(index)]
            if re.fullmatch(r"#[0-9a-fA-F]{0,2}", content):
                findings.append(Finding(base_line + source.count("\n", 0, offset), "a hex put together at run time", content))
    return findings, len(strings)


def blanked(match):
    return re.sub(r"[^\n]", " ", match.group(0))


def scan_template(text):
    """(findings, counts) for the text of one template."""
    findings, counts = [], collections.Counter()
    text = re.sub(r"\{#.*?#\}", blanked, text, flags=re.S)
    text = re.sub(r"<!--.*?-->", blanked, text, flags=re.S)
    for match in JINJA.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        for string in JINJA_STRING.finditer(match.group(0)):
            content = string.group(1) if string.group(1) is not None else string.group(2)
            counts["Jinja strings"] += 1
            findings += [Finding(line, "colour in a Jinja string", literal) for literal in colours_in_string(content)]
    page = JINJA.sub(lambda match: ("\x01" if match.group(0).startswith("{{") else "\x02") + "\n" * match.group(0).count("\n"), text)

    def line_of(position):
        return page.count("\n", 0, position) + 1

    for match in re.finditer(r"<style\b[^>]*>(.*?)</style\s*>", page, re.S | re.I):
        base = line_of(match.start(1))
        if "#\x01" in match.group(1):
            findings.append(Finding(base, "a hex put together at render time", "#{{...}}"))
        for rule in themeCss.parse_css(match.group(1).replace("\x01", " ").replace("\x02", " ")):
            for declaration in rule.declarations:
                counts["declarations"] += 1
                findings += [Finding(base, "colour in a <style> block", literal) for literal in css_value_literals(declaration.value, declaration.name)]
    page = re.sub(r"<style\b[^>]*>.*?</style\s*>", blanked, page, flags=re.S | re.I)

    for match in re.finditer(r"<script\b[^>]*>(.*?)</script\s*>", page, re.S | re.I):
        script_findings, strings = scan_script(match.group(1), line_of(match.start(1)))
        findings += script_findings
        counts["script strings"] += strings
    page = re.sub(r"<script\b[^>]*>.*?</script\s*>", blanked, page, flags=re.S | re.I)

    for match in re.finditer(r"(?<![\w-])style\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", page):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        line = line_of(match.start())
        counts["style attributes"] += 1
        if "#\x01" in value:
            findings.append(Finding(line, "a hex put together at render time", "#{{...}}"))
        for declaration in declarations_of(value):
            counts["declarations"] += 1
            findings += [Finding(line, "colour in a style attribute", literal) for literal in css_value_literals(declaration.value, declaration.name)]

    colour_attributes = r"(?<![\w-])(?:bgcolor|color|fill|stroke|stop-color|flood-color|lighting-color|alink|vlink)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')"
    for match in re.finditer(colour_attributes, page):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        counts["colour attributes"] += 1
        findings += [Finding(line_of(match.start()), "colour in an attribute", literal) for literal in colours_in_string(value)]
    for match in re.finditer(r"<meta\b[^>]*name\s*=\s*[\"']theme-color[\"'][^>]*content\s*=\s*[\"']([^\"']*)", page, re.I):
        findings += [Finding(line_of(match.start()), "a theme-color <meta>", literal) for literal in colours_in_string(match.group(1))]
        counts["theme-color metas"] += 1
    return findings, counts


def scan_stylesheet(css, skip_palette=False):
    """(findings, declaration count) for a project stylesheet, leaving out the first :root
    block when it is the palette. A finding's `line` is the rule it is in."""
    findings, count, skipped = [], 0, False
    for rule in themeCss.parse_css(css):
        if skip_palette and rule.selector == ":root" and not skipped:
            skipped = True
            continue
        for declaration in rule.declarations:
            count += 1
            findings += [Finding(rule.selector, "colour in a declaration", literal) for literal in css_value_literals(declaration.value, declaration.name)]
    return findings, count


def project_scripts():
    return sorted(path for path in STATIC.glob("*.js") if path.name not in VENDORED_SCRIPTS)


def project_stylesheets():
    return sorted(path for path in STATIC.glob("*.css") if path.name not in VENDORED_STYLESHEETS | {DARK_STYLESHEET_FILE})


def allowed_in(path, finding, text):
    """The reason a finding is allowed, or None."""
    line = text.splitlines()[finding.line - 1] if isinstance(finding.line, int) else ""
    for name, pattern, reason in ALLOWED_DATA_COLOURS:
        if path.name == name and re.search(pattern, line):
            return pattern
    return None


def test_the_static_directory_holds_nothing_unclassified():
    top_level_scripts = {path.name for path in STATIC.glob("*.js")}
    top_level_sheets = {path.name for path in STATIC.glob("*.css")}
    directories = {path.name for path in STATIC.iterdir() if path.is_dir()}

    assert VENDORED_SCRIPTS <= top_level_scripts and VENDORED_STYLESHEETS <= top_level_sheets, "a vendored file left: update the lists"
    assert directories == VENDORED_DIRECTORIES, f"a directory of static/ nobody has said is vendored (or not): {directories ^ VENDORED_DIRECTORIES}"
    assert {path.name for path in project_scripts()} >= {"page_loading.js", "post_action.js", "function_compare.js"}
    assert {path.name for path in project_stylesheets()} >= {"style.css", "navbar.css", "signin.css"}


def test_the_table_of_named_colours_is_the_whole_table():
    names = themeCss.NAMED_COLOURS

    assert len(names) == 148
    assert {"black", "white", "red", "green", "blue", "grey", "gray", "darkgrey", "lightslategrey", "rebeccapurple", "yellowgreen", "lightgoldenrodyellow", "slategray", "darkred", "lightgray"} <= set(names)
    assert set(KEYWORDS_NOT_FLAGGED).isdisjoint(names)


@pytest.mark.parametrize("keyword", sorted(KEYWORDS_NOT_FLAGGED))
def test_a_keyword_that_is_no_colour_of_ours_is_not_flagged(keyword):
    assert css_value_literals(f"1px solid {keyword}", "border") == []
    assert colours_in_string(f"border: 1px solid {keyword}") == []


@pytest.mark.parametrize(
    "property_name, value, expected",
    [
        ("color", "black", ["black"]),
        ("color", "BLACK", ["BLACK"]),
        ("border", "1px solid lightgray", ["lightgray"]),
        ("background", "rgba(255,255,255,0.6)", ["rgba("]),
        ("background", "rgb(240, 240, 240)!important", ["rgb("]),
        ("color", "#FFF", ["#FFF"]),
        ("color", "#fdd0a2", ["#fdd0a2"]),
        ("color", "#12345678", ["#12345678"]),
        ("background", "hsl(10 20% 30%)", ["hsl("]),
        ("background", "oklch(60% 0.1 200)", ["oklch("]),
        ("background", "color(srgb 1 0 0)", ["color("]),
        ("box-shadow", "0 0 0 .25rem #FFF !important", ["#FFF"]),
        ("box-shadow", "4px 4px 10px rgba(0, 0, 0, 0.4)", ["rgba("]),
        ("background", "linear-gradient(to right, red, blue)", ["red", "blue"]),
        ("background", "color-mix(in srgb, #fdd0a2 22%, var(--cfg-canvas))", ["#fdd0a2"]),
        ("--anything", "yellowgreen", ["yellowgreen"]),
        ("color", "var(--text-strong)", []),
        ("color", "var(--text-red)", []),
        ("background", "color-mix(in srgb, var(--accent) 14%, transparent)", []),
        ("color", "inherit", []),
        ("background", "none", []),
        ("white-space", "pre-wrap", []),
        ("font-family", "'Roboto', sans-serif", []),
        ("font-family", "Orchid, serif", []),
        ("content", '"red"', []),
        ("background-image", "url(data:image/svg+xml;fill=red)", []),
        ("margin", "0 .5rem", []),
        ("border", "0", []),
        ("width", "100%", []),
    ],
)
def test_the_value_scanner_finds_colours_and_only_colours(property_name, value, expected):
    assert css_value_literals(value, property_name) == expected


@pytest.mark.parametrize(
    "content, expected",
    [
        ("white", ["white"]),
        ("  Yellowgreen ", ["Yellowgreen"]),
        ("#fff", ["#fff"]),
        ("rgb(240, 240, 240)", ["rgb("]),
        ("background:rgba(255,255,255,0.6)", ["rgba("]),
        ("border: 2px dashed black; margin: 30%", ["black"]),
        ('<span style="color: red">', ["red"]),
        ("color: var(--text-strong)", []),
        ("display:flex", []),
        ("z-index:2000", []),
        ("#cross-table", []),
        ("#" + "x", []),
        ("light", []),
        ("white space", []),
        ("Dark", []),
        ("https://example.org/red", []),
        ("Error: the sample is red", []),
        ("", []),
    ],
)
def test_the_string_scanner_finds_a_colour_and_leaves_prose_alone(content, expected):
    assert colours_in_string(content) == expected


@pytest.mark.parametrize(
    "template, kinds",
    [
        ('<td style="color: black">x</td>', ["colour in a style attribute"]),
        ('<td style="font-size: 13px;color: black">x</td>', ["colour in a style attribute"]),
        ("<td style='background:#fdd0a2'>x</td>", ["colour in a style attribute"]),
        ('<i {% if a %} style="color:green;" {% else %} style="color:darkred;" {% endif %}></i>', ["colour in a style attribute"] * 2),
        ('<p style="border: 0px solid black">', ["colour in a style attribute"]),
        ('<td style="background-color: #{{ f(x) }}; width: 25px">', ["a hex put together at render time"]),
        ("<style>.a { color: red; }</style>", ["colour in a <style> block"]),
        ("<style>.a { border: 1px solid rgb(1, 2, 3); }</style>", ["colour in a <style> block"]),
        ("<script>row.style.backgroundColor = \"white\";</script>", ["colour in a string"]),
        ("<script>row.style.backgroundColor = 'rgb(240, 240, 240)';</script>", ["colour in a string"]),
        ("<script>el.style.color = '#" + "fff';</script>", ["colour in a string"]),
        ("<script>if (this.style.backgroundColor != 'yellowgreen') { go(); }</script>", ["colour in a string"]),
        ("<script>span.style.backgroundColor = '#' + palette[i];</script>", ["a hex put together at run time"]),
        ("<script>$(el).css({background: 'white'});</script>", ["colour in a string"]),
        ("<script>el.setAttribute('style', 'color: red');</script>", ["colour in a string"]),
        ("{% set TINTS = {'selected': 'yellowgreen'} %}", ["colour in a Jinja string"]),
        ("{{ dropzone.style('border: 2px dashed black; margin: 30%') }}", ["colour in a Jinja string"]),
        ('<svg><path fill="#ff0000"/></svg>', ["colour in an attribute"]),
        ('<font color="red">x</font>', ["colour in an attribute"]),
        ('<meta name="theme-color" content="#ffffff">', ["a theme-color <meta>"]),
        # none of these is a colour
        ('<td style="color: var(--text-strong)">x</td>', []),
        ('<td style="font-size: 13px; color: var(--text-strong)">x</td>', []),
        ('<td style="background-color: {{ scp.getMatchTintFromResult(m, \'matched_score\') }}">x</td>', []),
        ('<td style="white-space: pre-wrap; border: 0">x</td>', []),
        ("<p>Please pick the green one, or the white one; it is red.</p>", []),
        ('<a href="#fade" class="text-dark bg-light" data-bs-target="#add">x</a>', []),
        ('<svg><path fill="currentColor"/><path fill="none"/></svg>', []),
        ("<script>$('#' + id).css('color', 'inherit'); el.style.fill = 'currentColor'; var x = '#section';</script>", []),
        ("<script>row.classList.add('row-pending');  // was 'white', then 'rgb(240, 240, 240)'\n/* yellowgreen */</script>", []),
        ("{# a comment about black, white and #fff #}<!-- and red -->", []),
        ("{% if x == 'primary' or y == \"light\" %}<b class=\"text-danger\">x</b>{% endif %}", []),
        ('<input type="color" value="x">', []),
    ],
)
def test_the_template_scanner_finds_a_colour_in_each_place_one_can_be_written(template, kinds):
    findings, _counts = scan_template(template)

    assert [finding.kind for finding in findings] == kinds


@pytest.mark.parametrize("script", ["var s = 'unterminated;", "/* never closed", "var r = /abc;"])
def test_the_script_reader_refuses_what_it_cannot_follow(script):
    with pytest.raises(ValueError):
        javascript_skeleton(script)


def test_the_script_reader_keeps_its_place_around_comments_regular_expressions_and_templates():
    source = (
        "var a = 1; // 'white'\n"
        "var re = /['\"]white/g;\n"
        "var url = \"http://x.org/red\";\n"
        "/* 'black'\n 'rgb(1,2,3)' */\n"
        "var t = `<b style=\"color: ${x}\">`;\n"
        "el.style.color = 'red';\n"
    )

    findings, strings = scan_script(source)

    assert [(finding.line, finding.literal) for finding in findings] == [(7, "red")]
    assert strings == 3, "the string, the template literal and 'red'; the comments and the regular expression hold none"


def test_every_template_is_free_of_colour_literals_and_the_scan_looked():
    problems, allowed, counts = [], set(), collections.Counter()
    paths = sorted(TEMPLATES.rglob("*.html"))
    for path in paths:
        text = path.read_text(encoding="utf-8")
        findings, file_counts = scan_template(text)
        counts.update(file_counts)
        for finding in findings:
            reason = allowed_in(path, finding, text)
            if reason:
                allowed.add((path.name, reason))
            else:
                problems.append(f"{path.relative_to(TEMPLATES).as_posix()}:{finding.line}: {finding.kind}: {finding.literal}")

    assert problems == [], "a colour written into a template; give it a name in static/style.css and static/theme-dark.css:\n" + "\n".join(problems)
    # what it looked at on the day this was written was 67 templates, 468 style attributes, 572
    # declarations, 439 script strings and 2046 Jinja strings: a third of that is a floor a scan
    # that has stopped looking falls below, and ordinary edits do not
    assert len(paths) >= 50
    assert counts["style attributes"] >= 150 and counts["declarations"] >= 190 and counts["script strings"] >= 140 and counts["Jinja strings"] >= 680, counts


def test_every_project_stylesheet_is_free_of_colour_literals_outside_the_palette():
    problems, declarations = [], 0
    for path in project_stylesheets():
        findings, count = scan_stylesheet(path.read_text(encoding="utf-8"), skip_palette=path.name == "style.css")
        declarations += count
        problems += [f"{path.name}: {finding.line} {{ {finding.literal} }}" for finding in findings]

    assert problems == [], "a colour written in a project stylesheet outside the :root palette:\n" + "\n".join(problems)
    assert declarations >= 60, "the scan found hardly any declarations: it has stopped looking"


def test_every_project_script_is_free_of_colour_literals():
    problems, strings = [], 0
    for path in project_scripts():
        findings, count = scan_script(path.read_text(encoding="utf-8"))
        strings += count
        problems += [f"{path.name}:{finding.line}: {finding.kind}: {finding.literal}" for finding in findings]

    assert problems == [], "a colour written in a project script:\n" + "\n".join(problems)
    assert strings >= 40, "the scan found hardly any strings: it has stopped looking"


def test_the_data_colours_allowed_to_stay_are_still_there_and_still_dimmed():
    used = set()
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        findings, _counts = scan_template(text)
        used.update((path.name, allowed_in(path, finding, text)) for finding in findings)
    entries = {(name, pattern) for name, pattern, _reason in ALLOWED_DATA_COLOURS}

    assert used == entries, f"an allowed data colour that is no longer in the code (remove it from ALLOWED_DATA_COLOURS), or code that is no longer allowed: {used ^ entries}"
    assert all(reason for _name, _pattern, reason in ALLOWED_DATA_COLOURS)
    # what lets a data colour stay the same in both themes: the dark theme dims the squares
    cross = (TEMPLATES / "result_cross.html").read_text(encoding="utf-8")
    assert re.search(r'class="[^"]*\bscore-swatch\b[^"]*"[^>]*\n?\s*style="background-color: #\{\{ score_to_color\(', cross), "the matrix square lost its class"
    assert any(":root .score-swatch" in selector for rule in dark_rules() for selector in themeCss.selectors_of(rule)), "the dark theme no longer dims the squares"


def test_the_manuals_screenshots_are_no_brighter_than_the_text_beside_them():
    """The help page is half screenshots of the light pages. Dimmed, their white has to stay
    under the body text, or the brightest thing on a dark page is a picture."""
    filters = [declaration.value for rule in dark_rules() if ":root .manual img" in themeCss.selectors_of(rule)
               for declaration in rule.declarations if declaration.name == "filter"]
    assert len(filters) == 1, filters
    brightness = float(re.fullmatch(r"brightness\(([\d.]+)\)", filters[0]).group(1))
    palette = themeCss.dark_cascade()
    text = parse_colour(palette["--text-body"], themeCss.resolver(palette))
    assert themeCss.luminance((255 * brightness,) * 3) < themeCss.luminance(text)
