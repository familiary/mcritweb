#!/usr/bin/python
"""Deleting and promoting happens by POST only - issue #84.

These routes took a GET, which means anything that makes a browser fetch a URL could
fire them: an <img> tag in a mail or an issue comment, a link scanner, a prefetch.
No form, no JavaScript, no click. An admin merely loading a page could be made to
promote an account or delete a user.

The maintenance jobs are here for the same reason. They were already POST forms in
admin_server.html, but the routes accepted GET as well, so the form was a convention
rather than a constraint.

This closes the vector where an attacker picks the request. CSRF - a forged POST
carrying the victim's cookie - is closed separately by the token in mcritweb/csrf.py
(issue #83), and is covered by testCsrf.py. These tests run with that check off, so
that a missing token can never be what makes them pass.
"""

import logging
import sqlite3
import unittest

import pytest
from conftest import log_in_as

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

POST_ONLY = [
    "/admin/delete_user/2",
    "/admin/delete_user/2/all",
    "/admin/change_user_role/2/visitor/all",
    "/admin/schedule_rebuild_index",
    "/admin/schedule_recalc_minhashes",
    "/admin/schedule_recalc_pichashes",
    "/data/jobs/0123456789abcdef/delete",
]


@pytest.fixture
def fake_mcrit(recording_mcrit):
    """The permissive fake: these tests ask what reached the backend, and the strict
    one would raise on the first unknown method instead of recording the call."""
    return recording_mcrit


def _rows(app, statement, parameters=()):
    connection = sqlite3.connect(app.config["DATABASE"])
    try:
        return connection.execute(statement, parameters).fetchall()
    finally:
        connection.close()


@pytest.mark.parametrize("path", POST_ONLY)
def test_a_get_cannot_reach_a_destructive_route(client, as_role, path):
    as_role("admin")
    assert client.get(path).status_code == 405, f"GET {path} is still routed"


def test_deleting_a_user_by_post_removes_the_account(app, client, as_role, make_user):
    as_role("admin", username="theadmin")
    victim = make_user("visitor", username="victim")

    response = client.post(f"/admin/delete_user/{victim}/all")

    assert response.status_code == 302
    assert _rows(app, "SELECT id FROM user WHERE id = ?", (victim,)) == []


def test_deleting_a_user_takes_their_settings_with_them(app, client, as_role, make_user):
    """Noted on issue #84: the delete left user_filters and user_column_settings
    behind. Harmless while AUTOINCREMENT never reuses an id, but it is loose."""
    as_role("admin", username="theadmin")
    victim = make_user("visitor", username="victim")
    client.get("/settings")   # nothing creates these rows until a page asks for them
    log_in_as(client, victim)
    client.get("/settings")

    assert _rows(app, "SELECT user_id FROM user_filters WHERE user_id = ?", (victim,))

    log_in_as(client, _rows(app, "SELECT id FROM user WHERE username = 'theadmin'")[0][0])
    client.post(f"/admin/delete_user/{victim}/all")

    assert _rows(app, "SELECT user_id FROM user_filters WHERE user_id = ?", (victim,)) == []
    assert _rows(app, "SELECT user_id FROM user_column_settings WHERE user_id = ?", (victim,)) == []


def test_changing_a_role_by_post_takes_effect(app, client, as_role, make_user):
    as_role("admin", username="theadmin")
    target = make_user("visitor", username="target")

    response = client.post(f"/admin/change_user_role/{target}/contributor/all")

    assert response.status_code == 302
    assert _rows(app, "SELECT role FROM user WHERE id = ?", (target,)) == [("contributor",)]


def test_the_root_account_is_still_untouchable(app, client, as_role):
    """user_id 1 is always admin and always exists - the POST change must not alter
    that guard."""
    as_role("admin", username="theadmin")
    root_role = _rows(app, "SELECT role FROM user WHERE id = 1")

    client.post("/admin/change_user_role/1/visitor/all")
    client.post("/admin/delete_user/1/all")

    assert _rows(app, "SELECT role FROM user WHERE id = 1") == root_role


def test_deleting_a_job_by_post_reaches_the_backend(client, as_role, fake_mcrit):
    as_role("contributor")
    client.post("/data/jobs/0123456789abcdef/delete")

    called = [name for name, _args, _kwargs in fake_mcrit.calls]
    assert "deleteJob" in called


if __name__ == "__main__":
    unittest.main()
