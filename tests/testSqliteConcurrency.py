#!/usr/bin/python
"""The SQLite file is shared by every worker, so it runs in WAL mode.

docker-mcrit serves this app with two gunicorn workers of up to eight threads each, and
all of them open connections to the same `instance/mcritweb.sqlite`. That file was left
in SQLite's default rollback-journal mode, where a reader holding a transaction open
keeps a writer from committing, and a writer committing locks every reader out, in both
workers, until it is done. Every page reads the user and server rows, and a login,
a filter change or a query upload writes, so the two meet in ordinary use.

WAL lets readers carry on against the last commit while one writer appends. The mode is
a property of the file, so `db.migrate()` sets it once at startup; these tests check
that it ends up set on every way a database comes to exist, and that it buys what it
is meant to. The rollback-journal behaviour is pinned alongside, so the contrast is
measured here rather than asserted from memory.
"""

import logging
import sqlite3
import unittest

import pytest

from mcritweb import create_app
from mcritweb import db as mcritweb_db

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: Seconds the *contending* connection in these tests waits for a lock. Short, so a
#: lock that is held shows up as "database is locked" at once rather than after the
#: app's own five seconds.
CONTENDING_TIMEOUT = 0.05


def _journal_mode(database_path):
    connection = sqlite3.connect(database_path)
    try:
        return connection.execute("PRAGMA journal_mode;").fetchone()[0]
    finally:
        connection.close()


def _set_journal_mode(database_path, mode):
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute(f"PRAGMA journal_mode={mode};").fetchone()[0] == mode
    finally:
        connection.close()


def _connect(database_path):
    # isolation_level=None so BEGIN/COMMIT here are exactly what the test says
    return sqlite3.connect(database_path, timeout=CONTENDING_TIMEOUT, isolation_level=None)


def _fresh_app(tmp_path, database_path):
    instance_path = tmp_path / "instance"
    instance_path.mkdir(exist_ok=True)
    return create_app(
        {
            "DATABASE": str(database_path),
            "TESTING": True,
            "SECRET_KEY": "test-secret",
        },
        instance_path=str(instance_path),
    )


# --- the mode is set ---------------------------------------------------------------

def test_app_start_up_leaves_the_database_in_wal(app):
    """The conftest app: create_app (and with it migrate), then init_db."""
    assert _journal_mode(app.config["DATABASE"]) == "wal"


def test_an_existing_rollback_journal_database_is_switched_on_start(app):
    """A deployment upgrading from a release before this one has a `delete`-mode file
    with data in it. The next start converts it, and the data is still there."""
    database_path = app.config["DATABASE"]
    _set_journal_mode(database_path, "delete")

    create_app({"DATABASE": database_path, "TESTING": True, "SECRET_KEY": "test-secret"},
               instance_path=app.instance_path)

    assert _journal_mode(database_path) == "wal"
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT server_version FROM server;").fetchall() == [("test",)]
    finally:
        connection.close()


def test_a_database_made_by_init_db_is_in_wal(tmp_path):
    """`flask init-db` on a path with no database yet.

    The command builds the app first, so migrate() meets a file with no tables and
    returns early - the mode has to be set before that return, or a fresh install
    would stay in rollback-journal mode until its second start.
    """
    database_path = tmp_path / "mcritweb.sqlite"
    assert not database_path.exists()
    application = _fresh_app(tmp_path, database_path)

    result = application.test_cli_runner().invoke(args=["init-db"])

    assert result.exit_code == 0, result.output
    assert "Initialized the database." in result.output
    assert _journal_mode(str(database_path)) == "wal"
    connection = sqlite3.connect(str(database_path))
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        connection.close()
    assert {"user", "server", "user_filters", "user_column_settings", "login_attempt", "query_upload"} <= tables


def test_a_database_that_cannot_use_wal_is_reported_not_fatal(tmp_path, capsys):
    """SQLite keeps the old mode when WAL is impossible and answers with that mode
    instead of raising. An in-memory database is the reproducible case."""
    connection = sqlite3.connect(":memory:")
    try:
        assert mcritweb_db.enable_write_ahead_log(connection) == "memory"
    finally:
        connection.close()
    assert "journal_mode=memory" in capsys.readouterr().out

    # and the app still starts on one
    _fresh_app(tmp_path, ":memory:")


def test_get_db_waits_on_a_lock_for_the_configured_time(app, monkeypatch):
    """The busy timeout is the app's, not Python's default that happens to agree."""
    with app.app_context():
        assert mcritweb_db.get_db().execute("PRAGMA busy_timeout;").fetchone()[0] == 5000

    monkeypatch.setattr(mcritweb_db, "DATABASE_TIMEOUT", 0.25)
    with app.app_context():
        assert mcritweb_db.get_db().execute("PRAGMA busy_timeout;").fetchone()[0] == 250


# --- what the mode buys ------------------------------------------------------------

def test_a_reader_is_not_held_up_by_an_open_write_transaction(app):
    """A writer partway through: it has the write lock and an uncommitted change.

    A reader sees the last committed row. This one also holds in rollback-journal
    mode - there an uncommitted change sits in the writer's page cache and readers are
    only locked out once it commits - so it is the baseline, not the difference.
    """
    database_path = app.config["DATABASE"]
    writer, reader = _connect(database_path), _connect(database_path)
    try:
        writer.execute("BEGIN IMMEDIATE;")
        writer.execute("UPDATE server SET server_version = 'uncommitted';")

        assert reader.execute("SELECT server_version FROM server;").fetchall() == [("test",)]
    finally:
        writer.execute("ROLLBACK;")
        writer.close()
        reader.close()


@pytest.mark.parametrize("journal_mode, reader_is_locked_out", [("wal", False), ("delete", True)])
def test_a_reader_while_a_writer_holds_the_exclusive_lock(app, journal_mode, reader_is_locked_out):
    """The lock a rollback-journal commit takes while it writes the file.

    In `delete` mode that lock keeps every reader out until the commit is done. In WAL
    a commit appends to the log instead, and readers carry on.
    """
    database_path = app.config["DATABASE"]
    if journal_mode != "wal":
        _set_journal_mode(database_path, journal_mode)
    writer, reader = _connect(database_path), _connect(database_path)
    try:
        writer.execute("BEGIN EXCLUSIVE;")
        writer.execute("UPDATE server SET server_version = 'uncommitted';")

        if reader_is_locked_out:
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                reader.execute("SELECT server_version FROM server;").fetchall()
        else:
            assert reader.execute("SELECT server_version FROM server;").fetchall() == [("test",)]
    finally:
        writer.execute("ROLLBACK;")
        writer.close()
        reader.close()


@pytest.mark.parametrize("journal_mode, writer_is_locked_out", [("wal", False), ("delete", True)])
def test_a_writer_commits_while_a_reader_is_mid_transaction(app, journal_mode, writer_is_locked_out):
    """The other direction: a request in one worker reading, one in the other writing.

    In `delete` mode the reader's shared lock keeps the writer from committing at all;
    under load that is a login or a settings change failing with "database is locked"
    once the busy timeout runs out. In WAL the commit goes through, and the reader goes
    on seeing the snapshot it started with.
    """
    database_path = app.config["DATABASE"]
    if journal_mode != "wal":
        _set_journal_mode(database_path, journal_mode)
    writer, reader = _connect(database_path), _connect(database_path)
    try:
        reader.execute("BEGIN;")
        assert reader.execute("SELECT server_version FROM server;").fetchall() == [("test",)]
        writer.execute("BEGIN IMMEDIATE;")
        writer.execute("UPDATE server SET server_version = 'committed';")

        if writer_is_locked_out:
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                writer.execute("COMMIT;")
            writer.execute("ROLLBACK;")
        else:
            writer.execute("COMMIT;")
            assert reader.execute("SELECT server_version FROM server;").fetchall() == [("test",)]
    finally:
        reader.execute("COMMIT;")
        writer.close()
        reader.close()


if __name__ == "__main__":
    unittest.main()
