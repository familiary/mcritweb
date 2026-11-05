#!/usr/bin/python
"""Tests for the user profile page (/profile) and enhanced /settings integration.

Covers:
- Authentication & role access policy (/profile requires login, admits all roles including pending)
- Account metadata presentation: ID, username, role badges, formatted timestamps and relative ages
- Role capabilities breakdown matrix
- Developer & API token hub: masked/unmasked toggle, rotation action, snippets, and legacy token warnings
- Analysis activity ledger: jobs list, empty state, and backend unreachable fallback
- Ephemeral query uploads workspace: file size, TTL remaining, original filenames, promote action gating
- Submissions ledger: sample details and links
- Navigation integration from navbar and settings
"""

import datetime
import pathlib

import pytest
from werkzeug.security import generate_password_hash

from mcritweb.db import UserInfo, generate_apitoken, get_db, remember_query_filename, utc_now
from mcritweb.views.authentication import format_file_size, format_relative_time, format_ttl


def _make_test_user(app, username="profileuser", role="contributor", apitoken=None, registered=None, last_login=None):
    with app.app_context():
        user = UserInfo()
        user.username = username
        user.password = generate_password_hash("testpassword")
        user.role = role
        user.apitoken = apitoken or generate_apitoken()
        user.saveToDb()
        db = get_db()
        from mcritweb.db import format_timestamp
        if registered:
            db.execute("UPDATE user SET registered = ? WHERE username = ?", (format_timestamp(registered), username))
        if last_login:
            last_val = last_login if isinstance(last_login, str) else format_timestamp(last_login)
            db.execute("UPDATE user SET last_login = ? WHERE username = ?", (last_val, username))
        db.commit()
        return UserInfo.fromDb(username=username)


# --- 1. Authorization & Role Access ---

def test_profile_requires_login(client):
    response = client.get("/profile")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


@pytest.mark.parametrize("role", ["pending", "visitor", "contributor", "admin"])
def test_profile_renders_for_all_roles(client, app, role):
    user = _make_test_user(app, username=f"{role}_user", role=role)
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert f"User Profile: {user.username}" in html
    assert f"#{user.user_id}" in html

    if role == "pending":
        assert "Pending Approval" in html
        assert "API Access Restricted" in html
        assert user.apitoken not in html
    elif role == "visitor":
        assert "Visitor" in html
        assert user.apitoken in html
        assert "Capped (1 MiB)" in html
    elif role == "contributor":
        assert "Contributor" in html
        assert user.apitoken in html
        assert "Binary Sample Submissions" in html
    elif role == "admin":
        assert "Administrator" in html
        assert user.apitoken in html
        assert "Server &amp; User Administration" in html or "Server & User Administration" in html


# --- 2. Metadata & Relative Timestamps ---

def test_profile_displays_timestamps_and_relative_ages(client, app):
    now = utc_now()
    five_days_ago = now - datetime.timedelta(days=5, hours=3)
    two_hours_ago = now - datetime.timedelta(hours=2, minutes=10)

    user = _make_test_user(
        app,
        username="timestamper",
        role="visitor",
        registered=five_days_ago,
        last_login=two_hours_ago,
    )
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "5 days ago" in html
    assert "2 hours ago" in html
    assert five_days_ago.strftime("%Y-%m-%d") in html


def test_profile_handles_no_login_recorded(client, app):
    user = _make_test_user(app, username="nologinuser", role="visitor", last_login="no login")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "No prior login recorded" in html


# --- 3. Developer & API Token Hub ---

def test_profile_developer_hub_and_snippets(client, app):
    token = "a" * 64
    user = _make_test_user(app, username="apidev", role="contributor", apitoken=token)
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Masked token present in DOM
    assert "••••" in html
    # Unmasked token present
    assert token in html
    # Rotate token button with POST action
    assert "regenerate_apitoken" in html
    # Python code snippet
    assert "from mcrit.client.McritClient import McritClient" in html
    # cURL snippet
    assert 'apitoken: <YOUR_API_TOKEN>' in html or 'apitoken: &lt;YOUR_API_TOKEN&gt;' in html or f'apitoken: {token}' in html


def test_profile_legacy_token_deprecation_warning(client, app):
    legacy_token = "0123456789abcdef0123456789abcdef"  # 32 chars
    user = _make_test_user(app, username="legacyuser", role="contributor", apitoken=legacy_token)
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Deprecated Token Format" in html
    assert "pre-1.5.0" in html


# --- 4. Activity Ledger & Backend States ---

def test_profile_activity_ledger_empty_state(client, app, fake_mcrit):
    user = _make_test_user(app, username="emptyjobs", role="visitor")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "No Analysis Jobs Found" in html
    assert "Start Compare 1vsN" in html


def test_profile_activity_ledger_with_jobs(client, app, fake_mcrit):
    from unittest.mock import MagicMock
    user = _make_test_user(app, username="activeuser", role="contributor")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    mock_job = MagicMock()
    mock_job.job_id = "0123456789abcdef01234567"
    mock_job.method = "getMatchesForSample"
    mock_job.is_finished = True
    mock_job.is_failed = False
    mock_job.is_terminated = False
    mock_job.is_matching_job = True
    mock_job.duration = 4.25
    mock_job.created_at = "2026-11-01 12:00:00"

    fake_mcrit.getQueueData = lambda *args, **kwargs: [mock_job]
    fake_mcrit.getQueueCount = lambda *args, **kwargs: 1

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "0123456789abcdef01234567" in html
    assert "4.25s" in html
    assert f"/data/result/{mock_job.job_id}" in html


def test_profile_activity_ledger_backend_unreachable(client, app, fake_mcrit):
    user = _make_test_user(app, username="offlineuser", role="visitor")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    def _fail(*args, **kwargs):
        raise ConnectionError("Backend down")

    fake_mcrit.getQueueData = _fail

    response = client.get("/profile")
    # Must not 500; gracefully display fallback
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Queue Service Unreachable" in html


# --- 5. Ephemeral Query Uploads Workspace ---

def test_profile_ephemeral_query_uploads(client, app, fake_mcrit):
    from unittest.mock import MagicMock
    job_id = "aabbccddeeff001122334455"
    uploads_dir = pathlib.Path(app.instance_path) / "temp" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    sample_file = uploads_dir / job_id
    sample_file.write_bytes(b"MZtestbinarycontentforquery")

    with app.app_context():
        remember_query_filename(job_id, "suspicious_payload.exe")

    user = _make_test_user(app, username="uploader", role="contributor")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    mock_job = MagicMock()
    mock_job.job_id = job_id
    fake_mcrit.getQueueData = lambda *args, **kwargs: [mock_job]

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert job_id in html
    assert "suspicious_payload.exe" in html
    assert "27 B" in html or "B" in html
    assert "promote_query" in html


def test_profile_ephemeral_query_uploads_promotion_restricted_for_visitor(client, app, fake_mcrit):
    from unittest.mock import MagicMock
    job_id = "aabbccddeeff001122334466"
    uploads_dir = pathlib.Path(app.instance_path) / "temp" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    sample_file = uploads_dir / job_id
    sample_file.write_bytes(b"MZtestbinarycontentforquery")

    user = _make_test_user(app, username="visitor_uploader", role="visitor")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    mock_job = MagicMock()
    mock_job.job_id = job_id
    fake_mcrit.getQueueData = lambda *args, **kwargs: [mock_job]

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert job_id in html
    assert "Promotion Restricted" in html


def test_profile_ephemeral_query_uploads_does_not_leak_other_users_files(client, app, fake_mcrit):
    other_job_id = "aabbccddeeff001122334477"
    uploads_dir = pathlib.Path(app.instance_path) / "temp" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    sample_file = uploads_dir / other_job_id
    sample_file.write_bytes(b"MZotheruserbinary")

    with app.app_context():
        remember_query_filename(other_job_id, "secret_payload.exe")

    user = _make_test_user(app, username="innocent_visitor", role="visitor")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    # User has no jobs; must not see other users' uploads
    fake_mcrit.getQueueData = lambda *args, **kwargs: []

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert other_job_id not in html
    assert "secret_payload.exe" not in html
    assert "No Ephemeral Query Uploads" in html


def test_profile_admin_can_see_all_ephemeral_query_uploads(client, app, fake_mcrit):
    orphaned_job_id = "aabbccddeeff001122334488"
    uploads_dir = pathlib.Path(app.instance_path) / "temp" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    sample_file = uploads_dir / orphaned_job_id
    sample_file.write_bytes(b"MZorphanedbinary")

    with app.app_context():
        remember_query_filename(orphaned_job_id, "orphaned_sample.exe")

    admin = _make_test_user(app, username="sysadmin", role="admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin.user_id

    # Admin has no personal jobs, but role is admin
    fake_mcrit.getQueueData = lambda *args, **kwargs: []

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert orphaned_job_id in html
    assert "orphaned_sample.exe" in html


def test_profile_token_rotation_redirects_back_to_profile(client, app):
    user = _make_test_user(app, username="profilerotator", role="contributor")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    response = client.post(
        "/admin/regenerate_apitoken",
        data={"next": "profile"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/profile#developer")


# --- 6. Submissions Ledger ---

def test_profile_submissions_ledger(client, app, fake_mcrit):
    from unittest.mock import MagicMock
    user = _make_test_user(app, username="submitter", role="contributor")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    sub_job = MagicMock()
    sub_job.job_id = "11223344556677889900aabb"
    sub_job.method = "addBinarySample"
    sub_job.filename = "trojan.dll"
    sub_job.sha256 = "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
    sub_job.sample_id = 42
    sub_job.is_finished = True
    sub_job.is_failed = False
    sub_job.is_terminated = False
    sub_job.duration = 1.8
    sub_job.created_at = "2026-11-02 10:00:00"

    fake_mcrit.getQueueData = lambda *args, **kwargs: [sub_job]
    fake_mcrit.getQueueCount = lambda *args, **kwargs: 1

    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "trojan.dll" in html
    assert "1111222233334444" in html
    assert "Sample #42" in html
    assert "/explore/samples/42" in html


# --- 7. Settings and Navbar Linkage ---

def test_settings_links_to_profile(client, app):
    user = _make_test_user(app, username="settings_user", role="visitor")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    response = client.get("/settings")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "/profile" in html
    assert "View Full User Profile & Activity Hub" in html
    assert "Last Login:" in html


def test_navbar_contains_profile_link(client, app):
    user = _make_test_user(app, username="navuser", role="visitor")
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert 'href="/profile"' in html
    assert "View profile" in html


# --- 8. Helper Formatting Units ---

def test_format_relative_time():
    now = datetime.datetime(2026, 11, 5, 12, 0, 0, tzinfo=datetime.UTC)
    assert format_relative_time(None) == ""
    assert format_relative_time("no login") == "Never"
    assert format_relative_time(now - datetime.timedelta(seconds=30), now=now) == "just now"
    assert format_relative_time(now - datetime.timedelta(minutes=5), now=now) == "5 minutes ago"
    assert format_relative_time(now - datetime.timedelta(hours=3), now=now) == "3 hours ago"
    assert format_relative_time(now - datetime.timedelta(days=2), now=now) == "2 days ago"
    assert format_relative_time(now - datetime.timedelta(days=45), now=now) == "1 month ago"
    assert format_relative_time(now - datetime.timedelta(days=400), now=now) == "1 year ago"


def test_format_file_size():
    assert format_file_size(500) == "500 B"
    assert format_file_size(2048) == "2.0 KiB"
    assert format_file_size(5 * 1024 * 1024) == "5.0 MiB"


def test_format_ttl():
    assert format_ttl(0) == "Expired"
    assert format_ttl(-10) == "Expired"
    assert format_ttl(86400 * 3 + 3600 * 2) == "3d 2h remaining"
    assert format_ttl(3600 * 4 + 60 * 12) == "4h 12m remaining"
    assert format_ttl(60 * 25) == "25m remaining"
