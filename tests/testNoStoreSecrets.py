#!/usr/bin/python
"""A page that shows a secret is never kept: not by a cache, not by the back/forward cache."""

import pytest

from mcritweb import compression


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def test_the_api_token_is_not_stored(client, as_role):
    """Without no-store, the answer could be kept in the HTTP cache and read back out of it
    with no request to the server, after logout among other times. The settings page used
    to print the token and was no-store for it; the token now comes from this route."""
    as_role("contributor")
    client.get("/settings")
    answer = client.post("/admin/show_apitoken")
    assert answer.status_code == 200 and answer.get_json() == {"apitoken": "apitoken-contributor"}
    assert "no-store" in answer.headers.get("Cache-Control", "")


def test_the_server_page_is_not_stored(client, as_role):
    """It prints the registration token."""
    as_role("admin")
    page = client.get("/admin/server")
    assert page.status_code == 200
    assert "no-store" in page.headers.get("Cache-Control", "")


def test_the_server_page_after_a_change_is_not_stored(client, as_role):
    """change_server renders the same page, registration token included, from a POST."""
    as_role("admin")
    page = client.post("/admin/change_server", data={"mcrit_server_url": "http://localhost:8000"})
    assert page.status_code == 200
    assert "no-store" in page.headers.get("Cache-Control", "")


@pytest.mark.parametrize("path", ["/explore/samples", "/help", "/data/submit", "/settings"])
def test_a_page_without_a_secret_keeps_its_caching(client, as_role, path):
    """no-store keeps a page out of the back/forward cache, which makes Back slower; only
    the pages that need it pay that."""
    as_role("admin")
    page = client.get(path)
    assert page.status_code == 200, path
    assert "no-store" not in page.headers.get("Cache-Control", "")


def test_every_page_marked_as_showing_a_secret_is_not_stored(app):
    """The two lists are one: a view that is kept from compression for its secret is kept
    from the caches for it too."""
    assert compression._UNCOMPRESSED_VIEWS == {
        "mcritweb.views.administration.show_apitoken",
        "mcritweb.views.administration.server",
        "mcritweb.views.administration.change_server"}
    for endpoint in ("admin.show_apitoken", "admin.server", "admin.change_server"):
        assert getattr(app.view_functions[endpoint], "no_store", False), endpoint
