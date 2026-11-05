#!/usr/bin/python
"""Tests for User Discovery (/explore/users).

Verifies:
- Unauthenticated access redirects to /login.
- Multi-user requirement is enforced.
- Active usernames and roles are rendered.
- Pending accounts are hidden from non-admin viewers.
- No sensitive fields (passwords, hashes, API tokens) are present in the response.
"""

from mcritweb.db import ServerInfo, get_db


def test_anonymous_access_redirects_to_login(client):
    response = client.get("/explore/users")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_single_user_mode_blocked(app, client, as_role):
    as_role("visitor")
    with app.app_context():
        server = ServerInfo.fromDb()
        server.operation_mode = "single"
        server.saveToDb()

    response = client.get("/explore/users")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_multi_user_mode_renders_users(client, as_role, make_user):
    as_role("visitor")
    make_user(role="contributor", username="alice_analyst")

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Researchers &amp; Users" in html or "Researchers & Users" in html
    assert "alice_analyst" in html
    assert "Contributor" in html


def test_pending_users_hidden_from_visitors(client, as_role, make_user):
    as_role("visitor")
    make_user(role="pending", username="charlie_pending")

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "charlie_pending" not in html


def test_pending_users_visible_to_admin(client, as_role, make_user):
    as_role("admin")
    make_user(role="pending", username="dana_pending")

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "dana_pending" in html
    assert "Pending" in html


def test_secrets_never_leak_in_user_directory(app, client, as_role, make_user):
    as_role("visitor")
    make_user(role="contributor", username="secret_keeper")

    with app.app_context():
        row = get_db().execute("SELECT apitoken, password FROM user WHERE username = ?", ("secret_keeper",)).fetchone()
        assert row is not None
        token = row["apitoken"]
        pwhash = row["password"]

    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert token not in html
    assert pwhash not in html


def test_peer_profile_view_hides_api_token_tab(client, as_role, make_user):
    as_role("visitor")
    make_user(role="contributor", username="bob_analyst")

    response = client.get("/profile/bob_analyst")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "bob_analyst" in html
    assert "API token" not in html
    assert "show_apitoken" not in html


def test_peer_profile_nonexistent_returns_404(client, as_role):
    as_role("visitor")
    response = client.get("/profile/nonexistent_user_xyz")
    assert response.status_code == 404


def test_peer_profile_pending_returns_403_for_visitor(client, as_role, make_user):
    as_role("visitor")
    make_user(role="pending", username="eve_pending")
    response = client.get("/profile/eve_pending")
    assert response.status_code == 403

