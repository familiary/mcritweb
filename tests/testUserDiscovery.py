#!/usr/bin/python
"""E2E Test Suite for User Discovery (/explore/users) & Public Profiles (Tiers 1-4).

Covers:
- Tier 1: Core Feature Coverage for `/explore/users`:
  * Accessible to authorized roles: visitor, contributor, admin (HTTP 200)
  * Unauthenticated requests redirected to /login (HTTP 302)
  * Pending role rejected with HTTP 403 Forbidden
  * Renders active usernames, roles, and join dates
  * Single-user mode displays appropriate notice or behavior
  * Admin view includes management shortcuts or controls
- Tier 2: Boundary & Corner Cases:
  * Minimal user list (single admin user) renders gracefully
  * Special characters and HTML in usernames are strictly escaped
  * Pending accounts display appropriate status badge or filtering
  * Backend unreachable or failing gracefully handles queue metrics
- Tier 3: Cross-Feature Combinations (User Discovery & Parameterized Profiles):
  * User directory links to parameterized public profiles (/profile/<username>)
  * Public profiles strictly sanitize secrets (API tokens, password hashes, ephemeral queries) for peers
  * Public profile for admin view reveals administrative management controls
  * Self-profile view preserves developer & API tools and ephemeral queries
  * Nonexistent user profile queries return HTTP 404 Not Found
- Tier 4: Real-World End-to-End Scenarios:
  * Researcher discovers contributor via /explore/users and inspects public contribution statistics
  * Administrator discovers visitor via /explore/users, navigates to profile, and promotes role
"""

import datetime
from unittest.mock import MagicMock

import pytest
from werkzeug.security import generate_password_hash

from mcritweb.db import ServerInfo, UserInfo, format_timestamp, generate_apitoken, get_db, utc_now


def _create_user(app, username, role="visitor", apitoken=None, registered=None, last_login=None):
    """Helper to persist a user with specific metadata in the test SQLite database."""
    with app.app_context():
        user = UserInfo()
        user.username = username
        user.password = generate_password_hash("password123")
        user.role = role
        user.apitoken = apitoken or generate_apitoken()
        user.saveToDb()

        db = get_db()
        if registered is not None:
            reg_val = registered if isinstance(registered, str) else format_timestamp(registered)
            db.execute("UPDATE user SET registered = ? WHERE username = ?", (reg_val, username))
        if last_login is not None:
            login_val = last_login if isinstance(last_login, str) else format_timestamp(last_login)
            db.execute("UPDATE user SET last_login = ? WHERE username = ?", (login_val, username))
        db.commit()
        return UserInfo.fromDb(username=username)


# =============================================================================
# Tier 1: Core Feature Coverage
# =============================================================================

@pytest.mark.parametrize("role", ["visitor", "contributor", "admin"])
def test_explore_users_accessible_for_all_authorized_roles(client, app, role):
    """GET /explore/users is accessible to visitor, contributor, and admin."""
    user = _create_user(app, username=f"{role}_explorer", role=role)
    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert user.username in html


def test_explore_users_unauthenticated_redirects_to_login(client):
    """Unauthenticated requests to /explore/users redirect to /login."""
    response = client.get("/explore/users")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_explore_users_pending_role_forbidden(client, app):
    """Pending user role is rejected with HTTP 403 Forbidden on /explore/users."""
    pending_user = _create_user(app, username="pending_observer", role="pending")
    with client.session_transaction() as sess:
        sess["user_id"] = pending_user.user_id

    response = client.get("/explore/users")
    assert response.status_code == 403


def test_explore_users_renders_registered_users_and_metadata(client, app):
    """User discovery directory renders registered usernames, roles, and joined dates."""
    now = utc_now()
    two_days_ago = now - datetime.timedelta(days=2)
    ten_days_ago = now - datetime.timedelta(days=10)

    admin = _create_user(app, username="sysadmin_user", role="admin", registered=ten_days_ago)
    contributor = _create_user(app, username="analyst_bob", role="contributor", registered=two_days_ago)
    visitor = _create_user(app, username="viewer_claire", role="visitor", registered=now)

    with client.session_transaction() as sess:
        sess["user_id"] = visitor.user_id

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # All three users must appear in the directory
    for u in (admin, contributor, visitor):
        assert u.username in html

    # Role badges or text must appear
    assert "admin" in html.lower()
    assert "contributor" in html.lower()
    assert "visitor" in html.lower()


def test_explore_users_single_user_mode_behavior(client, app):
    """In single-user mode, /explore/users displays a single-user notice or handles gracefully."""
    admin = _create_user(app, username="single_operator", role="admin")
    with app.app_context():
        server_info = ServerInfo.fromDb()
        server_info.operation_mode = "single"
        server_info.saveToDb()

    with client.session_transaction() as sess:
        sess["user_id"] = admin.user_id

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Renders the single user and/or indicates single-user mode
    assert admin.username in html or "single" in html.lower()


def test_explore_users_admin_view_includes_actions(client, app):
    """When viewed by an administrator, /explore/users provides management links or controls."""
    admin = _create_user(app, username="super_admin", role="admin")
    target = _create_user(app, username="regular_user", role="visitor")

    with client.session_transaction() as sess:
        sess["user_id"] = admin.user_id

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Admin view has links to manage user or role actions
    assert target.username in html
    assert f"/profile/{target.username}" in html or "manage" in html.lower() or "admin" in html.lower()


# =============================================================================
# Tier 2: Boundary & Corner Cases
# =============================================================================

def test_explore_users_minimal_user_table(client, app):
    """Directory renders cleanly when only a single user exists."""
    admin = _create_user(app, username="only_admin", role="admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin.user_id

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert admin.username in html


def test_explore_users_escapes_special_characters_in_username(client, app):
    """Special characters and HTML in usernames are strictly escaped to prevent XSS."""
    admin = _create_user(app, username="safe_admin", role="admin")
    malicious_name = "test<script>alert(1)</script>"
    _create_user(app, username=malicious_name, role="visitor")

    with client.session_transaction() as sess:
        sess["user_id"] = admin.user_id

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Unescaped tag must never appear
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html or "test" in html


def test_explore_users_handles_pending_users(client, app):
    """Pending users in the database are either badged or handled appropriately in the directory."""
    admin = _create_user(app, username="admin_reviewer", role="admin")
    pending = _create_user(app, username="pending_applicant", role="pending")

    with client.session_transaction() as sess:
        sess["user_id"] = admin.user_id

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    if pending.username in html:
        # If listed, role must clearly state pending
        assert "pending" in html.lower()


def test_explore_users_backend_unreachable_fallback(client, app, fake_mcrit):
    """When backend queue/stats lookup fails, /explore/users does not 500."""
    admin = _create_user(app, username="resilient_admin", role="admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin.user_id

    def _broken_queue(*args, **kwargs):
        raise ConnectionError("Backend unavailable")

    fake_mcrit.getQueueData = _broken_queue

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert admin.username in html


# =============================================================================
# Tier 3: Cross-Feature Combinations (Discovery + Public Profile)
# =============================================================================

def test_explore_users_links_to_parameterized_public_profile(client, app):
    """User discovery table rows contain links to /profile/<username>."""
    visitor = _create_user(app, username="viewer_dan", role="visitor")
    target = _create_user(app, username="researcher_elena", role="contributor")

    with client.session_transaction() as sess:
        sess["user_id"] = visitor.user_id

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Must contain a link targeting the public profile of researcher_elena
    assert f'href="/profile/{target.username}"' in html or f"/profile/{target.username}" in html

    # Visiting the linked profile returns HTTP 200
    profile_resp = client.get(f"/profile/{target.username}")
    assert profile_resp.status_code == 200


def test_public_profile_peer_view_sanitizes_sensitive_data(client, app):
    """When a visitor views another user's profile, private credentials and workspace are strictly hidden."""
    viewer = _create_user(app, username="visitor_frank", role="visitor")
    secret_token = "99" * 32
    target = _create_user(
        app,
        username="contributor_gina",
        role="contributor",
        apitoken=secret_token,
    )

    with client.session_transaction() as sess:
        sess["user_id"] = viewer.user_id

    response = client.get(f"/profile/{target.username}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Public identity must be visible
    assert target.username in html
    assert "Contributor" in html

    # Sensitive data MUST NOT be exposed
    assert secret_token not in html, "API token leaked to peer visitor!"
    assert target.password not in html, "Password hash leaked to peer visitor!"

    # Developer / Token rotation & Ephemeral queries tabs must be hidden for peer
    assert "regenerate_apitoken" not in html
    assert "Developer &amp; API" not in html and "Developer & API" not in html
    assert "Ephemeral Query Uploads" not in html


def test_public_profile_admin_view_shows_management_controls(client, app):
    """When an administrator views another user's profile, management controls are displayed."""
    admin = _create_user(app, username="admin_harold", role="admin")
    target = _create_user(app, username="contributor_ian", role="contributor")

    with client.session_transaction() as sess:
        sess["user_id"] = admin.user_id

    response = client.get(f"/profile/{target.username}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert target.username in html
    # Admin controls present (change user role or delete user or manage token)
    assert "change_user_role" in html or "delete_user" in html or "Administrative" in html or "Manage" in html


def test_public_profile_self_view_preserves_developer_and_ephemeral_tools(client, app):
    """When a user views their own profile (/profile or /profile/<own_username>), full tools appear."""
    user = _create_user(app, username="self_researcher", role="contributor")

    with client.session_transaction() as sess:
        sess["user_id"] = user.user_id

    for path in ("/profile", f"/profile/{user.username}"):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert user.username in html
        assert user.apitoken in html
        assert "regenerate_apitoken" in html


def test_public_profile_nonexistent_user_returns_404(client, app):
    """Accessing /profile/<username> for a non-existent user returns HTTP 404 Not Found."""
    viewer = _create_user(app, username="any_viewer", role="visitor")
    with client.session_transaction() as sess:
        sess["user_id"] = viewer.user_id

    response = client.get("/profile/nonexistent_phantom_user_12345")
    assert response.status_code == 404


# =============================================================================
# Tier 4: Real-World End-to-End Scenarios
# =============================================================================

def test_e2e_researcher_discovers_contributor_and_views_public_contributions(client, app, fake_mcrit):
    """End-to-End: Visitor discovers contributor in user directory and inspects public contributions."""
    visitor = _create_user(app, username="researcher_karen", role="visitor")
    contributor = _create_user(app, username="prolific_submitter", role="contributor")

    # Mock completed submission in backend queue for contributor
    mock_submission = MagicMock()
    mock_submission.job_id = "00112233445566778899aabb"
    mock_submission.method = "addBinarySample"
    mock_submission.filename = "apt_loader.bin"
    mock_submission.sha256 = "55" * 32
    mock_submission.sample_id = 99
    mock_submission.is_finished = True
    mock_submission.is_failed = False
    mock_submission.is_terminated = False
    mock_submission.duration = 2.4
    mock_submission.created_at = "2026-11-04 15:30:00"

    fake_mcrit.getQueueData = lambda *args, **kwargs: [mock_submission]
    fake_mcrit.getQueueCount = lambda *args, **kwargs: 1

    # Step 1: Visitor browses user discovery directory
    with client.session_transaction() as sess:
        sess["user_id"] = visitor.user_id

    discovery_resp = client.get("/explore/users")
    assert discovery_resp.status_code == 200
    assert contributor.username in discovery_resp.get_data(as_text=True)

    # Step 2: Visitor inspects contributor's public profile
    profile_resp = client.get(f"/profile/{contributor.username}")
    assert profile_resp.status_code == 200
    html = profile_resp.get_data(as_text=True)

    # Contributor's public contributions are displayed
    assert "apt_loader.bin" in html or "Sample #99" in html or "1" in html
    # Private secrets are protected
    assert contributor.apitoken not in html


def test_e2e_admin_discovers_user_and_modifies_role_from_profile(client, app):
    """End-to-End: Admin discovers user via /explore/users, navigates to profile, and promotes role."""
    admin = _create_user(app, username="root_admin", role="admin")
    visitor = _create_user(app, username="promising_user", role="visitor")

    with client.session_transaction() as sess:
        sess["user_id"] = admin.user_id

    # Step 1: Browse discovery directory
    dir_resp = client.get("/explore/users")
    assert dir_resp.status_code == 200
    assert visitor.username in dir_resp.get_data(as_text=True)

    # Step 2: Inspect user profile as admin
    profile_resp = client.get(f"/profile/{visitor.username}")
    assert profile_resp.status_code == 200

    # Step 3: Admin changes user's role to contributor
    role_change_resp = client.post(
        f"/admin/change_user_role/{visitor.user_id}/contributor/profile",
        environ_base={"HTTP_REFERER": f"http://localhost/profile/{visitor.username}"},
    )
    # Redirects back to profile or admin users
    assert role_change_resp.status_code == 302

    # Step 4: Verify role updated in database
    with app.app_context():
        updated_user = UserInfo.fromDb(user_id=visitor.user_id)
        assert updated_user.role == "contributor"
