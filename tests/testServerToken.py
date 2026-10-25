#!/usr/bin/python
"""The backend server token is never sent to a browser, and can still be kept,
replaced and removed.

AGENTS.md: "Never log or render secrets: user API tokens, the server token, password
hashes." The admin server page wrote the token into the value of its "Backend Server
Token" field, in clear, so that the form would post it back unchanged - which put it in
the page source, in the browser's cache and form history, and on the screen of anyone
looking over the admin's shoulder. The field is empty now. An empty field keeps the
stored token, so a change of URL leaves it alone; a typed one replaces it; and removing
it, which an empty field used to mean, takes its own button.
"""

import pytest

from mcritweb.db import ServerInfo

TOKEN = "s3cr3t-backend-token"
URL = "http://127.0.0.1:8000"


@pytest.fixture
def admin(app, client, as_role):
    as_role("admin")
    return client


def stored_token(app):
    with app.app_context():
        return ServerInfo.fromDb().server_token


def store_token(app, token):
    with app.app_context():
        server_info = ServerInfo.fromDb()
        server_info.server_token = token
        server_info.saveToDb()


def change_server(admin, **form):
    return admin.post("/admin/change_server", data=dict({"mcrit_server_url": URL}, **form))


def test_the_server_page_does_not_show_the_token(app, admin):
    store_token(app, TOKEN)
    page = admin.get("/admin/server").get_data(as_text=True)

    assert TOKEN not in page
    assert "A token is set" in page


def test_nor_does_the_page_a_change_answers_with(app, admin):
    page = change_server(admin, mcrit_server_token=TOKEN).get_data(as_text=True)

    assert stored_token(app) == TOKEN
    assert TOKEN not in page


def test_an_empty_field_keeps_the_token(app, admin):
    """The form used to post the token back because it was printed into the field; with
    the field empty, the same submit would otherwise have removed it."""
    store_token(app, TOKEN)
    change_server(admin, mcrit_server_url="http://127.0.0.1:9999", mcrit_server_token="")

    assert stored_token(app) == TOKEN
    with app.app_context():
        assert ServerInfo.fromDb().url == "http://127.0.0.1:9999"


def test_a_typed_token_replaces_it(app, admin):
    store_token(app, TOKEN)
    change_server(admin, mcrit_server_token="another-token")

    assert stored_token(app) == "another-token"


def test_the_remove_button_removes_it(app, admin):
    store_token(app, TOKEN)
    page = change_server(admin, mcrit_server_token="", remove_server_token="1").get_data(as_text=True)

    assert stored_token(app) == ""
    assert "No token is set" in page


def test_there_is_nothing_to_remove_without_a_token(app, admin):
    page = admin.get("/admin/server").get_data(as_text=True)

    assert 'name="remove_server_token"' not in page
    assert "No token is set" in page
