#!/usr/bin/python
"""Who a query upload belongs to: the local account id, recorded when the upload is kept.

The profile lists a user's query uploads. The first version found them through the
backend's jobs for the username, which docs/adr/0007 rules out as an owner: a username is
renamed by its holder and freed by a delete, so a renamed account lost its uploads and the
next holder of a freed name was shown the old holder's. The `query_upload` table, the one
`get_query_filename` already reads, now records the uploader's id next to the name, and
the list is keyed on that: it survives a rename and does not follow a freed name.
"""

import io
import logging

import pytest

from mcritweb.db import UserInfo, get_db, get_query_uploads, remember_query_filename

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


def query_as(client, filename="query.exe"):
    response = client.post("/analyze/query", data={"options": "unmapped", "file": (io.BytesIO(b"MZ" + filename.encode()), filename)},
                           content_type="multipart/form-data")
    assert response.status_code == 202
    return response.get_data(as_text=True).split("/data/jobs/")[1].split("?")[0]


def test_a_query_upload_records_the_uploader(app, client, as_role):
    user_id = as_role("visitor")
    job_id = query_as(client)
    with app.app_context():
        assert get_query_uploads(user_id) == [(job_id, "query.exe")]


def test_the_uploads_are_listed_by_account_not_by_name(app, client, as_role, make_user):
    other_id = make_user(role="visitor", username="other")
    user_id = as_role("visitor")
    job_id = query_as(client)
    with app.app_context():
        assert get_query_uploads(other_id) == []
        assert get_query_uploads(user_id) == [(job_id, "query.exe")]


def test_a_renamed_account_keeps_its_uploads(app, client, as_role):
    """change_username lets a user rename themselves; keyed on the name the history
    would have split at the rename."""
    user_id = as_role("visitor", username="before")
    job_id = query_as(client)
    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        user_info.username = "after"
        user_info.saveToDb()
        assert UserInfo.fromDb(user_id=user_id).username == "after"
        assert get_query_uploads(user_id) == [(job_id, "query.exe")]


def test_the_next_holder_of_a_freed_name_sees_nothing_of_the_old_one(app, client, as_role, make_user):
    """delete_user frees the name and the next registration can take it; the old
    holder's uploads must not come with it."""
    old_id = as_role("visitor", username="reused")
    job_id = query_as(client)
    with app.app_context():
        database = get_db()
        database.execute("DELETE FROM user WHERE id = ?;", (old_id,))
        database.commit()
    new_id = make_user(role="visitor", username="reused")
    assert new_id != old_id
    with app.app_context():
        assert get_query_uploads(new_id) == []
        assert get_query_uploads(old_id) == [(job_id, "query.exe")]


def test_the_admin_listing_holds_every_upload_including_ownerless_ones(app, client, as_role):
    """Rows from before the column existed have no uploader; they are nobody's and are
    listed where everything is, for the admin."""
    user_id = as_role("visitor")
    job_id = query_as(client)
    with app.app_context():
        remember_query_filename("fedcba9876543210fedcba98", "from_before.exe")
        assert get_query_uploads() == [("fedcba9876543210fedcba98", "from_before.exe"), (job_id, "query.exe")]
        assert get_query_uploads(user_id) == [(job_id, "query.exe")]


@pytest.mark.parametrize("role", ["visitor", "contributor", "admin"])
def test_every_role_that_can_query_is_recorded(app, client, as_role, role):
    user_id = as_role(role)
    job_id = query_as(client)
    with app.app_context():
        assert get_query_uploads(user_id) == [(job_id, "query.exe")]
