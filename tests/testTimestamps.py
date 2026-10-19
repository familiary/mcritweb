#!/usr/bin/python
"""How the two `user` timestamps are written and read back.

`registered` and `last_login` are VARCHAR columns, and until issue #98 the INSERT
handed sqlite3 a `datetime` and let its implicit adapter decide the text. That
adapter is deprecated as of Python 3.12, and it did not always produce something
`UserInfo.fromDb` could parse - it drops the fractional part when the microsecond is
exactly 0, while the read side requires one.

These tests pin the contract both ways: what we write is what we read, and what
older versions wrote is still readable.
"""

import datetime
import logging
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from mcritweb.db import (
    TIMESTAMP_FORMAT,
    TIMESTAMP_FORMAT_WITHOUT_MICROSECONDS,
    UserInfo,
    format_timestamp,
    get_db,
    parse_timestamp,
    utc_now,
)

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


def _make_user(username="timestamped"):
    user_info = UserInfo()
    user_info.username = username
    user_info.password = generate_password_hash("password")
    user_info.role = "admin"
    user_info.apitoken = "apitoken"
    user_info.saveToDb()
    return UserInfo.fromDb(username=username)


def test_utc_now_is_timezone_aware_and_in_utc():
    """`datetime.utcnow()` returned a naive value that merely held UTC, which is how
    a UTC timestamp ends up compared against local time without anything complaining."""
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == datetime.timedelta(0)


def test_a_stored_timestamp_survives_the_round_trip():
    moment = datetime.datetime(2026, 3, 4, 5, 6, 7, 890123, tzinfo=datetime.UTC)
    assert parse_timestamp(format_timestamp(moment)) == moment


def test_a_timestamp_written_without_microseconds_is_still_readable():
    """What sqlite3's adapter produced when the microsecond happened to be 0.

    `datetime.isoformat(" ")` drops `.000000`, and the old read side asked for it
    unconditionally - so a user registered on that microsecond could never be read
    back and every page 500'd for them.
    """
    stored = datetime.datetime(2026, 1, 1, 0, 0, 0).strftime(TIMESTAMP_FORMAT_WITHOUT_MICROSECONDS)
    assert stored == "2026-01-01 00:00:00", "the format this test is about"

    parsed = parse_timestamp(stored)

    assert parsed == datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


def test_something_that_is_not_a_timestamp_is_rejected():
    with pytest.raises(ValueError):
        parse_timestamp("last tuesday")


def test_a_registered_user_reads_back_as_an_aware_utc_datetime(app):
    with app.app_context():
        user_info = _make_user()

    assert user_info.registered.tzinfo is not None
    assert user_info.registered.utcoffset() == datetime.timedelta(0)
    # a fresh registration, so it should be about now
    assert abs(utc_now() - user_info.registered) < datetime.timedelta(minutes=5)


def test_the_stored_text_is_the_shape_older_versions_wrote(app):
    """An existing deployment's rows have to stay readable, so the column keeps
    holding a naive-looking UTC string rather than gaining a +00:00 offset. Handing
    sqlite3 an *aware* datetime instead would have appended one, and `fromDb` would
    then have failed on every row this version wrote."""
    with app.app_context():
        _make_user()
        stored = get_db().execute("SELECT registered FROM user WHERE username = ?;", ("timestamped",)).fetchone()[0]

    # parses under the read format, and carries no offset
    datetime.datetime.strptime(stored, TIMESTAMP_FORMAT)
    assert "+" not in stored


def test_a_last_login_written_by_this_version_reads_back(app):
    """last_login takes the UPDATE path rather than the INSERT one, so it is stored
    by a different line of code and is worth its own round trip."""
    moment = datetime.datetime(2026, 2, 3, 4, 5, 6, 7, tzinfo=datetime.UTC)
    with app.app_context():
        user_info = _make_user()
        user_info.last_login = moment
        user_info.saveToDb()

        assert UserInfo.fromDb(username="timestamped").last_login == moment


def test_a_row_written_by_the_old_implicit_adapter_still_reads(app):
    """The exact bytes sqlite3's deprecated default adapter used to write."""
    legacy = datetime.datetime(2020, 6, 1, 12, 30, 45, 123456)
    with app.app_context():
        _make_user()
        database = get_db()
        # what `database.execute(..., (legacy,))` produced before this change
        database.execute("UPDATE user SET registered = ? WHERE username = ?;", (legacy.isoformat(" "), "timestamped"))
        database.commit()

        user_info = UserInfo.fromDb(username="timestamped")

    assert user_info.registered == legacy.replace(tzinfo=datetime.UTC)
    assert user_info.registration_date == "2020-06-01"


def test_no_datetime_reaches_sqlite3_as_an_object(app):
    """The deprecation is only silenced while nothing hands sqlite3 a datetime.

    A raised adapter is the least ambiguous way to assert that - if any write still
    passes one through, this fails rather than emitting a warning nobody reads.
    """
    def refuse(value):
        raise AssertionError(f"a datetime reached sqlite3's adapter: {value!r}")

    sqlite3.register_adapter(datetime.datetime, refuse)
    try:
        with app.app_context():
            user_info = _make_user()
            user_info.last_login = utc_now()
            user_info.saveToDb()
    finally:
        # sqlite3 has no unregister; put the deprecated default back for other tests
        sqlite3.register_adapter(datetime.datetime, lambda value: value.isoformat(" "))
