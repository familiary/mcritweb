#!/usr/bin/python
"""Covers `db.migrate()`, which upgrades an existing SQLite file in place.

Every deployment that has ever been updated runs this code, and it is the only
place in mcritweb where getting it wrong silently destroys user data: the scripts
in `mcritweb/sql/` all begin with `DROP TABLE IF EXISTS`, so a migration that
re-runs one on a populated database wipes it.

The historical schemas below are transcribed from the release tags, e.g.

    git show v0.10.6:mcritweb/create_table.sql
    git show v1.3.6:mcritweb/sql/create_table_user.sql

They are embedded rather than read from git at test time on purpose: a CI
checkout is shallow and has no tags, and a test that quietly skips itself there
is worse than no test. When a new migration step lands, add the schema it starts
from here.

`create_app()` calls `db.migrate()` itself (see `mcritweb/__init__.py`), so these
tests build the legacy database with raw sqlite3 first, before any app exists,
and then let the app factory perform the migration.
"""

import logging
import sqlite3
import unittest

from mcritweb import create_app

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


# --- historical schemas ----------------------------------------------------------

# v0.10.6: before user_filters, before apitoken, before server_token
SCHEMA_V0_10_6 = """
CREATE TABLE user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  role VARCHAR NOT NULL,
  registered VARCHAR NOT NULL,
  last_login VARCHAR
);

CREATE TABLE server (
  url VARCHAR NOT NULL,
  operation_mode VARCHAR NOT NULL,
  registration_token VARCHAR NOT NULL,
  server_uuid VARCHAR NOT NULL,
  server_version VARCHAR NOT NULL
);
"""

# v0.11.1: user_filters has arrived, apitoken and server_token have not
SCHEMA_V0_11_1 = SCHEMA_V0_10_6 + """
CREATE TABLE user_filters (
  user_id INTEGER PRIMARY KEY,
  filter_direct_min_score INTEGER,
  filter_direct_nonlib_min_score INTEGER,
  filter_frequency_min_score INTEGER,
  filter_frequency_nonlib_min_score INTEGER,
  filter_unique_only INTEGER,
  filter_exclude_own_family INTEGER,
  filter_function_min_score INTEGER,
  filter_function_max_score INTEGER,
  filter_max_num_families INTEGER,
  filter_exclude_library INTEGER,
  filter_exclude_pic INTEGER
);
"""

# v1.3.6: everything except user_column_settings, which arrives in v1.4.0
SCHEMA_V1_3_6 = """
CREATE TABLE user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  role VARCHAR NOT NULL,
  registered VARCHAR NOT NULL,
  last_login VARCHAR,
  apitoken VARCHAR
);

CREATE TABLE user_filters (
  user_id INTEGER PRIMARY KEY,
  filter_direct_min_score INTEGER,
  filter_direct_nonlib_min_score INTEGER,
  filter_frequency_min_score INTEGER,
  filter_frequency_nonlib_min_score INTEGER,
  filter_unique_only INTEGER,
  filter_exclude_own_family INTEGER,
  filter_function_min_score INTEGER,
  filter_function_max_score INTEGER,
  filter_max_num_families INTEGER,
  filter_exclude_library INTEGER,
  filter_exclude_pic INTEGER
);

CREATE TABLE server (
  url VARCHAR NOT NULL,
  operation_mode VARCHAR NOT NULL,
  registration_token VARCHAR NOT NULL,
  server_token VARCHAR NOT NULL,
  server_uuid VARCHAR NOT NULL,
  server_version VARCHAR NOT NULL
);
"""

# v1.4.8: the schema both query_upload (#40) and the theme column (#70) are migrated
# onto - the last release before either, so it has neither: no query_upload table and a
# `user` table with no `theme`. create_table_user_column_settings.sql has had exactly one
# commit since it was introduced in v1.4.0, so this is that file verbatim.
SCHEMA_V1_4_8 = SCHEMA_V1_3_6 + """
CREATE TABLE user_column_settings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  family_table_family_id INTEGER DEFAULT 0,
  family_table_family_name INTEGER DEFAULT 1,
  family_table_num_samples INTEGER DEFAULT 2,
  family_table_num_functions INTEGER DEFAULT 3,
  family_table_is_library INTEGER DEFAULT 4,
  samples_table_sample_id INTEGER DEFAULT 0,
  samples_table_sha256 INTEGER DEFAULT 1,
  samples_table_family INTEGER DEFAULT 2,
  samples_table_version INTEGER DEFAULT 3,
  samples_table_filename INTEGER DEFAULT 4,
  samples_table_bitness INTEGER DEFAULT 5,
  samples_table_num_functions INTEGER DEFAULT 6,
  samples_table_is_library INTEGER DEFAULT 7,
  functions_table_function_id INTEGER DEFAULT 0,
  functions_table_family_id INTEGER DEFAULT 1,
  functions_table_sample_id INTEGER DEFAULT 2,
  functions_table_pic_hash INTEGER DEFAULT 3,
  functions_table_has_minhash INTEGER DEFAULT 4,
  functions_table_offset INTEGER DEFAULT 5,
  functions_table_function_name INTEGER DEFAULT 6,
  functions_table_num_instructions INTEGER DEFAULT 7,
  functions_table_num_blocks INTEGER DEFAULT 8,
  result_family_table_family_name INTEGER DEFAULT 0,
  result_family_table_version INTEGER DEFAULT 1,
  result_family_table_sample_id INTEGER DEFAULT 2,
  result_family_table_sha256 INTEGER DEFAULT 3,
  result_family_table_filename INTEGER DEFAULT 4,
  result_family_table_num_functions INTEGER DEFAULT 5,
  result_family_table_num_minhash INTEGER DEFAULT 6,
  result_family_table_num_pichash INTEGER DEFAULT 7,
  result_family_table_direct_score INTEGER DEFAULT 8,
  result_family_table_direct_nonlib_score INTEGER DEFAULT 9,
  result_family_table_frequency_score INTEGER DEFAULT 10,
  result_family_table_frequency_nonlib_score INTEGER DEFAULT 11,
  result_family_table_uniq_score INTEGER DEFAULT 12,
  result_function_unfiltered_table_matched_function_id INTEGER DEFAULT 0,
  result_function_unfiltered_table_offset INTEGER DEFAULT 1,
  result_function_unfiltered_table_num_bytes INTEGER DEFAULT 2,
  result_function_unfiltered_table_num_matched_families INTEGER DEFAULT 3,
  result_function_unfiltered_table_num_matched_samples INTEGER DEFAULT 4,
  result_function_unfiltered_table_num_matched_functions INTEGER DEFAULT 5,
  result_function_unfiltered_table_best_score INTEGER DEFAULT 6,
  result_function_unfiltered_table_num_minhash INTEGER DEFAULT 7,
  result_function_unfiltered_table_num_pichash INTEGER DEFAULT 8,
  result_function_unfiltered_table_is_library_match INTEGER DEFAULT 9,
  result_function_unfiltered_table_is_unique_match INTEGER DEFAULT 10,
  result_function_sample_filtered_table_function_id_a INTEGER DEFAULT 0,
  result_function_sample_filtered_table_offset_a INTEGER DEFAULT 1,
  result_function_sample_filtered_table_offset_b INTEGER DEFAULT 2,
  result_function_sample_filtered_table_function_id_b INTEGER DEFAULT 3,
  result_function_sample_filtered_table_num_bytes INTEGER DEFAULT 4,
  result_function_sample_filtered_table_best_score INTEGER DEFAULT 5,
  result_function_sample_filtered_table_is_minhash_match INTEGER DEFAULT 6,
  result_function_sample_filtered_table_is_pichash_match INTEGER DEFAULT 7,
  result_function_sample_filtered_table_is_library_match INTEGER DEFAULT 8,
  result_function_sample_filtered_table_is_unique_match INTEGER DEFAULT 9,
  result_function_function_filtered_table_function_id_a INTEGER DEFAULT 0,
  result_function_function_filtered_table_offset_a INTEGER DEFAULT 1,
  result_function_function_filtered_table_offset_b INTEGER DEFAULT 2,
  result_function_function_filtered_table_function_id_b INTEGER DEFAULT 3,
  result_function_function_filtered_table_family_name_b INTEGER DEFAULT 4,
  result_function_function_filtered_table_sample_id_b INTEGER DEFAULT 5,
  result_function_function_filtered_table_best_score INTEGER DEFAULT 6,
  result_function_function_filtered_table_is_minhash_match INTEGER DEFAULT 7,
  result_function_function_filtered_table_is_pichash_match INTEGER DEFAULT 8,
  result_function_function_filtered_table_is_library_match INTEGER DEFAULT 9,
  result_function_function_filtered_table_is_unique_match INTEGER DEFAULT 10,
  FOREIGN KEY (user_id) REFERENCES user (id)
);
"""

# --- helpers ---------------------------------------------------------------------

def _legacy_database(tmp_path, schema=None):
    """Write a database in a historical schema, without going through the app."""
    db_path = tmp_path / "mcritweb.sqlite"
    connection = sqlite3.connect(str(db_path))
    try:
        if schema is not None:
            connection.executescript(schema)
        connection.commit()
    finally:
        connection.close()
    return db_path


def _run_migration(tmp_path, db_path):
    """Build an app on db_path, which is what actually triggers db.migrate()."""
    instance_path = tmp_path / "instance"
    instance_path.mkdir(exist_ok=True)
    return create_app(
        {
            "DATABASE": str(db_path),
            "TESTING": True,
            "SECRET_KEY": "test-secret",
        },
        instance_path=str(instance_path),
    )


def _query(db_path, statement, parameters=()):
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute(statement, parameters).fetchall()
    finally:
        connection.close()


def _tables(db_path):
    return {row[0] for row in _query(db_path, "SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(db_path, table):
    return [row[1] for row in _query(db_path, f"PRAGMA table_info({table})")]


def _insert_legacy_user(db_path, username, with_apitoken=False):
    connection = sqlite3.connect(str(db_path))
    try:
        if with_apitoken:
            connection.execute(
                "INSERT INTO user (username, password, role, registered, apitoken) VALUES (?, ?, ?, ?, ?)",
                (username, "hashed", "admin", "2020-01-01", "preexisting-token"),
            )
        else:
            connection.execute(
                "INSERT INTO user (username, password, role, registered) VALUES (?, ?, ?, ?)",
                (username, "hashed", "admin", "2020-01-01"),
            )
        connection.commit()
    finally:
        connection.close()


def _insert_legacy_server(db_path, with_server_token=False):
    connection = sqlite3.connect(str(db_path))
    try:
        if with_server_token:
            connection.execute(
                "INSERT INTO server (url, operation_mode, registration_token, server_token, server_uuid, server_version)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ("http://127.0.0.1:8000", "multi", "regtoken", "srvtoken", "uuid", "1.0.0"),
            )
        else:
            connection.execute(
                "INSERT INTO server (url, operation_mode, registration_token, server_uuid, server_version)"
                " VALUES (?, ?, ?, ?, ?)",
                ("http://127.0.0.1:8000", "multi", "regtoken", "uuid", "1.0.0"),
            )
        connection.commit()
    finally:
        connection.close()


# --- the migration path ----------------------------------------------------------

def test_an_uninitialized_database_is_left_alone(tmp_path):
    """No `user` table means the instance has never run `init-db`.

    migrate() must bail out rather than half-create a schema, because the
    registration flow expects to find an empty instance and call init_db itself.
    """
    db_path = _legacy_database(tmp_path)
    _run_migration(tmp_path, db_path)
    assert _tables(db_path) == set()


def test_the_oldest_schema_is_brought_fully_up_to_date(tmp_path):
    db_path = _legacy_database(tmp_path, SCHEMA_V0_10_6)
    _insert_legacy_user(db_path, "olduser")
    _insert_legacy_server(db_path)

    _run_migration(tmp_path, db_path)

    assert {"user", "user_filters", "server", "user_column_settings",
            "login_attempt", "query_upload"} <= _tables(db_path)
    assert "apitoken" in _columns(db_path, "user")
    assert "server_token" in _columns(db_path, "server")


def test_an_existing_user_survives_the_apitoken_migration(tmp_path):
    """The account keeps its identity and gains a usable token."""
    db_path = _legacy_database(tmp_path, SCHEMA_V0_10_6)
    _insert_legacy_user(db_path, "olduser")

    _run_migration(tmp_path, db_path)

    rows = _query(db_path, "SELECT username, password, role, apitoken FROM user")
    assert len(rows) == 1
    username, password, role, apitoken = rows[0]
    assert (username, password, role) == ("olduser", "hashed", "admin")
    assert apitoken, "a pre-existing user must not be left without an API token"


def test_each_user_gets_its_own_apitoken(tmp_path):
    """A shared token would let any account authenticate as any other."""
    db_path = _legacy_database(tmp_path, SCHEMA_V0_10_6)
    _insert_legacy_user(db_path, "first")
    _insert_legacy_user(db_path, "second")

    _run_migration(tmp_path, db_path)

    tokens = [row[0] for row in _query(db_path, "SELECT apitoken FROM user")]
    assert len(set(tokens)) == 2


def test_apitokens_are_not_regenerated_on_a_second_run(tmp_path):
    """Migration runs on every app start - a token that changes each time would
    invalidate every stored credential on restart."""
    db_path = _legacy_database(tmp_path, SCHEMA_V0_10_6)
    _insert_legacy_user(db_path, "olduser")

    _run_migration(tmp_path, db_path)
    first = _query(db_path, "SELECT apitoken FROM user")[0][0]
    _run_migration(tmp_path, db_path)
    second = _query(db_path, "SELECT apitoken FROM user")[0][0]

    assert first == second


def test_the_server_row_gains_an_empty_server_token(tmp_path):
    db_path = _legacy_database(tmp_path, SCHEMA_V0_10_6)
    _insert_legacy_user(db_path, "olduser")
    _insert_legacy_server(db_path)

    _run_migration(tmp_path, db_path)

    rows = _query(db_path, "SELECT url, registration_token, server_token FROM server")
    assert rows == [("http://127.0.0.1:8000", "regtoken", "")]


def test_stored_user_filters_are_not_dropped(tmp_path):
    """create_table_user_filters.sql starts with DROP TABLE IF EXISTS, so the
    migration must only run it when the table is genuinely absent."""
    db_path = _legacy_database(tmp_path, SCHEMA_V0_11_1)
    _insert_legacy_user(db_path, "olduser")
    connection = sqlite3.connect(str(db_path))
    connection.execute(
        "INSERT INTO user_filters (user_id, filter_direct_min_score, filter_exclude_pic) VALUES (?, ?, ?)",
        (1, 42, 1),
    )
    connection.commit()
    connection.close()

    _run_migration(tmp_path, db_path)

    assert _query(db_path, "SELECT user_id, filter_direct_min_score, filter_exclude_pic FROM user_filters") == [(1, 42, 1)]


def test_a_v1_3_6_database_gains_the_tables_added_since(tmp_path):
    """The steps after it that only create a table: user_column_settings arrives in
    v1.4.0 and query_upload with issue #40, and the already-migrated pieces must be
    left untouched by either."""
    db_path = _legacy_database(tmp_path, SCHEMA_V1_3_6)
    _insert_legacy_user(db_path, "olduser", with_apitoken=True)
    _insert_legacy_server(db_path, with_server_token=True)

    assert {"user_column_settings", "query_upload"}.isdisjoint(_tables(db_path))
    _run_migration(tmp_path, db_path)

    assert {"user_column_settings", "query_upload"} <= _tables(db_path)
    assert _query(db_path, "SELECT apitoken FROM user") == [("preexisting-token",)]
    assert _query(db_path, "SELECT server_token FROM server") == [("srvtoken",)]


def test_a_v1_4_8_database_only_gains_the_theme_column(tmp_path):
    """The theme is a per-user preference, so it lands on `user` (#70). An account
    that predates it has no theme to keep and is not backfilled - `UserInfo` reads a
    NULL back as the default, which testTheme.py pins."""
    db_path = _legacy_database(tmp_path, SCHEMA_V1_4_8)
    _insert_legacy_user(db_path, "olduser", with_apitoken=True)
    _insert_legacy_server(db_path, with_server_token=True)

    assert "theme" not in _columns(db_path, "user")
    _run_migration(tmp_path, db_path)

    assert "theme" in _columns(db_path, "user")
    assert _query(db_path, "SELECT username, apitoken, theme FROM user") == [("olduser", "preexisting-token", None)]
    assert _query(db_path, "SELECT server_token FROM server") == [("srvtoken",)]


def test_a_stored_theme_survives_a_second_run(tmp_path):
    """migrate() runs on every start; a preference that reset on restart would be
    indistinguishable from one that never saved."""
    db_path = _legacy_database(tmp_path, SCHEMA_V1_4_8)
    _insert_legacy_user(db_path, "olduser", with_apitoken=True)

    _run_migration(tmp_path, db_path)
    connection = sqlite3.connect(str(db_path))
    connection.execute("UPDATE user SET theme = 'dark'")
    connection.commit()
    connection.close()
    _run_migration(tmp_path, db_path)

    assert _query(db_path, "SELECT theme FROM user") == [("dark",)]


def test_a_database_from_before_the_throttle_gains_the_attempt_table(tmp_path):
    """The migration step added for issue #101, from the schema it starts from.

    Everything up to v1.4.8 predates `login_attempt`, so an upgrading deployment has to
    grow it on first start - the login path reads it on every POST and would otherwise
    raise `no such table` for every attempt, locking everyone out rather than metering
    anyone.
    """
    db_path = _legacy_database(tmp_path, SCHEMA_V0_10_6)
    _insert_legacy_user(db_path, "olduser")
    assert "login_attempt" not in _tables(db_path)

    _run_migration(tmp_path, db_path)

    assert "login_attempt" in _tables(db_path)
    assert {"remote_addr", "username", "attempted_at"} <= set(_columns(db_path, "login_attempt"))


def test_recorded_attempts_survive_a_second_migration(tmp_path):
    """The guard is a CREATE, so a second start must not drop what the first counted."""
    db_path = _legacy_database(tmp_path, SCHEMA_V0_10_6)
    _insert_legacy_user(db_path, "olduser")
    _run_migration(tmp_path, db_path)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO login_attempt (remote_addr, username, attempted_at) VALUES (?, ?, ?)",
            ("203.0.113.7", "olduser", 1)),
        connection.commit()
    finally:
        connection.close()

    _run_migration(tmp_path, db_path)

    assert _query(db_path, "SELECT remote_addr, username FROM login_attempt") == [
        ("203.0.113.7", "olduser")]

def test_a_v1_4_8_database_only_gains_query_upload(tmp_path):
    """The schema query_upload is actually migrated onto, rather than two steps back.

    Everything else is already in place here, so this is the only step that may run -
    and create_table_user_column_settings.sql drops its table first, so a guard that
    misfired would take that user's whole column setup with it.
    """
    db_path = _legacy_database(tmp_path, SCHEMA_V1_4_8)
    _insert_legacy_user(db_path, "olduser", with_apitoken=True)
    _insert_legacy_server(db_path, with_server_token=True)
    connection = sqlite3.connect(str(db_path))
    connection.execute("INSERT INTO user_column_settings (user_id, samples_table_sample_id) VALUES (?, ?)", (1, 6))
    connection.commit()
    connection.close()

    assert "query_upload" not in _tables(db_path)
    _run_migration(tmp_path, db_path)

    assert "query_upload" in _tables(db_path)
    assert _columns(db_path, "query_upload") == ["job_id", "filename"]
    assert _query(db_path, "SELECT user_id, samples_table_sample_id FROM user_column_settings") == [(1, 6)]


def test_the_current_schema_is_a_no_op(tmp_path):
    """Running the app twice on an up-to-date database must change nothing."""
    from mcritweb.db import init_db

    db_path = tmp_path / "mcritweb.sqlite"
    application = _run_migration(tmp_path, db_path)
    with application.app_context():
        init_db()
    _insert_legacy_user(db_path, "currentuser", with_apitoken=True)

    before = {table: _columns(db_path, table) for table in sorted(_tables(db_path))}
    _run_migration(tmp_path, db_path)
    after = {table: _columns(db_path, table) for table in sorted(_tables(db_path))}

    assert before == after
    assert _query(db_path, "SELECT username, apitoken FROM user") == [("currentuser", "preexisting-token")]


if __name__ == "__main__":
    unittest.main()
