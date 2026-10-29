#!/usr/bin/python
"""Proves the test harness itself works: an app on a throwaway database, a fake
backend substituted through MCRIT_CLIENT_FACTORY, and role-based login.

These are also the seed of the route/role matrix described in issue #88.
"""

import logging
import unittest

import pytest
from conftest import log_in_as

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


def test_app_fixture_builds_with_routes(app):
    assert app.config["TESTING"] is True
    assert len(list(app.url_map.iter_rules())) > 1


def test_backend_is_the_fake(app, fake_mcrit):
    """The app must reach the fake, not a real McritClient."""
    from mcritweb.views.client import get_client
    with app.test_request_context("/"):
        assert get_client() is fake_mcrit


def test_index_redirects_to_registration_on_an_empty_instance(client):
    """With no users at all, the app treats itself as unconfigured."""
    response = client.get("/")
    assert response.status_code == 302
    assert "/register" in response.headers["Location"]


def test_index_renders_once_a_user_exists(client, as_role):
    as_role("admin")
    response = client.get("/")
    assert response.status_code == 200


def test_index_queries_the_backend(client, as_role, fake_mcrit):
    """The fake records calls, so tests can assert on backend interaction."""
    as_role("admin")
    client.get("/")
    called = {name for name, _args, _kwargs in fake_mcrit.calls}
    assert "getQueueData" in called
    assert "search_samples" in called


def test_the_manual_is_reachable_without_a_session(client, as_role):
    """/help is public by design - it is what a new user reads before registering."""
    as_role("admin", username="seedhelp")
    with client.session_transaction() as test_session:
        test_session.clear()

    response = client.get("/help")

    assert response.status_code == 200


@pytest.mark.parametrize("role", ["anonymous", "pending"])
def test_index_spares_the_backend_for_callers_it_shows_nothing(client, as_role, fake_mcrit, role):
    """index.html renders nothing for these two, so the queries behind it are waste.

    It is one getQueueData plus a getSampleById and a getFamily per job, on every
    anonymous hit - a crawler alone can keep the backend busy.
    """
    user_id = as_role("admin" if role == "anonymous" else role, username=f"idx{role}")
    with client.session_transaction() as test_session:
        test_session.clear()
    if role != "anonymous":
        log_in_as(client, user_id)
    fake_mcrit.calls.clear()

    response = client.get("/")

    assert response.status_code == 200
    assert fake_mcrit.calls == [], f"index queried the backend for {role}"


def test_anonymous_access_is_redirected_to_login(client, as_role):
    # a user has to exist, otherwise every route redirects to registration instead
    as_role("admin", username="someadmin")
    with client.session_transaction() as test_session:
        test_session.clear()
    response = client.get("/explore/families")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "role,expected",
    [
        ("admin", 200),
        ("contributor", 200),
        ("visitor", 200),
        ("pending", 403),
    ],
)
def test_explore_families_enforces_roles(client, as_role, role, expected):
    """visitor_required admits visitor and above, and rejects pending."""
    as_role(role)
    response = client.get("/explore/families")
    assert response.status_code == expected


def test_admin_only_route_rejects_a_contributor(client, as_role):
    as_role("contributor")
    response = client.get("/admin/users/")
    assert response.status_code == 403


def test_authorization_is_checked_before_the_backend(app, client, as_role):
    """Decorator order: @<role>_required must run before @mcrit_server_required.

    Otherwise an unauthorized request to an unreachable instance is answered with
    "No connection to the MCRIT server" instead of being rejected on its merits,
    and every rejected request pays for a backend round-trip first.
    """
    probed = []

    def failing_probe():
        probed.append(True)
        raise RuntimeError("backend is down")

    app.config["MCRIT_SERVER_PROBE"] = failing_probe

    as_role("pending")
    response = client.get("/explore/families")
    assert response.status_code == 403, "role check must reject before the backend is consulted"
    assert probed == [], "the backend was probed despite the role check failing"


def test_backend_check_still_applies_to_authorized_users(app, client, as_role):
    """The reordering must not disable the check for users who pass authorization."""
    def failing_probe():
        raise RuntimeError("backend is down")

    app.config["MCRIT_SERVER_PROBE"] = failing_probe

    as_role("admin")
    response = client.get("/explore/families")
    assert response.status_code == 302
    assert response.headers["Location"] in ("/", "http://localhost/")


if __name__ == "__main__":
    unittest.main()
