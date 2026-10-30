#!/usr/bin/python
"""The page a 400, 403, 404 or 405 answers with, the CSRF check's 400 among them.

They were Werkzeug's bare documents: a title and a sentence, without the site's layout, its
stylesheets or the colour-scheme <meta>, so an account on the dark theme was shown a white
page. They are drawn in base.html now, in the account's theme. The API, and a script that asks
for JSON, read the status and not the page, and keep Werkzeug's answer as it was. Nothing on
these pages may need the backend: an error is the moment it is least likely to be there.
"""

import re

import pytest
from conftest import log_in_as
from werkzeug.security import generate_password_hash

from mcritweb.csrf import ENABLED_KEY
from mcritweb.db import UserInfo

DARK_STYLESHEET = "/static/theme-dark.css"
#: what Werkzeug's own error document starts with; none of the site's pages does
WERKZEUG_PAGE = b"<!doctype html>\n<html lang=en>\n<title>"


def add_user(app, username, role, theme):
    with app.app_context():
        user_info = UserInfo()
        user_info.username = username
        user_info.password = generate_password_hash("password")
        user_info.role = role
        user_info.apitoken = f"apitoken-{username}"
        user_info.theme = theme
        user_info.saveToDb()
        return UserInfo.fromDb(username=username).user_id


@pytest.fixture
def signed_in(client, app):
    """signed_in(role, theme) -> the id of an account the client is now signed in as."""
    def _signed_in(role="visitor", theme="dark"):
        user_id = add_user(app, f"{role}-{theme}", role, theme)
        log_in_as(client, user_id)
        return user_id
    return _signed_in


def assert_the_sites_page(response, status, theme="dark"):
    """A page in base.html's layout and in the account's theme, with the error's name as its heading."""
    page = response.get_data(as_text=True)
    assert response.status_code == status
    assert response.mimetype == "text/html"
    assert not response.data.startswith(WERKZEUG_PAGE), "Werkzeug's own error document"
    # url_for appends a `?v=` version to a static URL
    assert re.search(r'href="/static/style\.css(\?v=[^"]*)?"', page), "the page does not have the site's stylesheet"
    assert 'class="navbar' in page, "the page is not in the site's layout"
    assert f'<html lang="en" data-theme="{theme}">' in page
    if theme == "dark":
        assert '<meta name="color-scheme" content="dark">' in page
        assert re.search(rf'href="{re.escape(DARK_STYLESHEET)}(\?v=[^"]*)?" media="all"', page), "a dark account is shown the light page"
    return page


def test_a_page_that_is_not_there_is_answered_in_the_sites_layout_and_theme(client, signed_in):
    signed_in("visitor", "dark")

    page = assert_the_sites_page(client.get("/no/such/page"), 404)

    assert "<h1>Not Found</h1>" in page
    assert "404 Not Found - MCRIT Web" in page


def test_a_page_the_role_does_not_reach_is_answered_in_the_sites_layout_and_theme(client, signed_in):
    signed_in("visitor", "dark")

    page = assert_the_sites_page(client.get("/admin/server"), 403)

    assert "<h1>Forbidden</h1>" in page


def test_a_method_a_route_does_not_take_is_answered_in_the_sites_layout_and_keeps_its_allow_header(client, signed_in):
    signed_in("visitor", "dark")

    response = client.get("/admin/change_theme")

    page = assert_the_sites_page(response, 405)
    assert "<h1>Method Not Allowed</h1>" in page
    assert "POST" in response.headers["Allow"], "a 405 has to say which methods the route takes"


def test_a_form_the_csrf_check_refuses_is_answered_in_the_accounts_theme(client, app, signed_in):
    """The CSRF check runs before the hook that loads the account, so without help the page would
    be drawn in the default theme, light, whatever the account had chosen."""
    app.config[ENABLED_KEY] = True
    signed_in("visitor", "dark")

    page = assert_the_sites_page(client.post("/admin/change_theme", data={"theme": "light"}), 400)

    assert "<h1>Bad Request</h1>" in page
    assert "The CSRF token is missing or invalid." in page


def test_signed_out_the_page_is_the_light_one(client, app):
    add_user(app, "someone", "visitor", "dark")

    assert_the_sites_page(client.get("/no/such/page"), 404, theme="light")


@pytest.mark.parametrize(
    "method, path, headers, status",
    [
        ("get", "/api/status", {}, 403),
        ("get", "/api/status", {"Accept": "text/html"}, 403),
        ("put", "/api/samples/1", {"apitoken": "apitoken-visitor-dark"}, 405),
        ("get", "/api", {}, 404),
    ],
    ids=["no token", "no token, asking for a page", "a method the router does not take", "the bare prefix"],
)
def test_the_api_keeps_werkzeugs_answer(client, signed_in, method, path, headers, status):
    """A caller of the API is a program; it reads the status. What it got before is what it gets."""
    signed_in("visitor", "dark")

    response = getattr(client, method)(path, headers=headers)

    assert response.status_code == status
    assert response.data.startswith(WERKZEUG_PAGE)
    assert b"style.css" not in response.data


def test_a_script_that_asks_for_json_keeps_werkzeugs_answer(client, signed_in):
    """The upload form and Dropzone ask for JSON and act on the status alone."""
    signed_in("visitor", "dark")

    response = client.get("/no/such/page", headers={"Accept": "application/json"})

    assert response.status_code == 404
    assert response.data.startswith(WERKZEUG_PAGE)


class NoBackend:
    """A backend every call to which fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"the error page called the backend: {name}")


@pytest.fixture
def fake_mcrit():
    return NoBackend()


@pytest.mark.parametrize("role", ["visitor", "contributor", "admin"])
@pytest.mark.parametrize("path, status", [("/no/such/page", 404), ("/admin/change_theme", 405)])
def test_an_error_page_needs_no_backend(client, app, signed_in, role, path, status):
    """Every role draws the navbar its own way (the admin's has the server settings); none of it
    may ask the backend, and the probe is not consulted either."""
    app.config["MCRIT_SERVER_PROBE"] = lambda: (_ for _ in ()).throw(AssertionError("the error page probed the backend"))
    signed_in(role, "dark")

    assert_the_sites_page(client.get(path), status)
