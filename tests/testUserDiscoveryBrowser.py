#!/usr/bin/python
"""Another account's profile and the directory, in a browser: the tabs an admin gets on
someone else's page work as on the own page, a peer's one-pane page shows that pane, and
nothing scrolls sideways on a phone.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import os
import threading

import pytest
from werkzeug.serving import make_server

from mcritweb.db import remember_query_filename
from mcritweb.views.utility import query_upload_path

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

WIDTHS = [390, 820, 1280, 1920]
ADMIN_TABS = ["overview", "jobs", "submissions", "uploads"]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def live_server(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


@pytest.fixture
def target(app, make_user, fake_mcrit):
    user_id = make_user(role="contributor", username="analyst1")
    fake_mcrit.record_requester("analyst1")
    with app.app_context():
        remember_query_filename("fedcba9876543210fedcba98", "query.exe", user_id)
        path = query_upload_path(app, "fedcba9876543210fedcba98")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as upload:
            upload.write(b"MZquery")
    return user_id


@pytest.fixture
def open_as(app, live_server, make_user, target):
    def _open(role, path, use, width=1280):
        user_id = make_user(role=role)
        cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})
        with sync_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except sync_api.Error as error:
                pytest.skip(f"no Chromium for playwright to drive: {error}")
            try:
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(live_server + path)
                page.wait_for_function("() => window.bootstrap !== undefined")
                return use(page, errors)
            finally:
                browser.close()

    return _open


def shown_pane(page):
    return page.evaluate("() => document.querySelector('.tab-pane.show.active').id.replace('pane-', '')")


@pytest.mark.parametrize("width", WIDTHS)
def test_an_admins_view_of_another_account_scrolls_nowhere_sideways(open_as, width):
    def use(page, errors):
        widths = {}
        for tab in ADMIN_TABS:
            page.click(f"#tab-{tab}")
            page.wait_for_selector(f"#pane-{tab}.show.active")
            widths[tab] = page.evaluate("() => document.documentElement.scrollWidth")
        return widths, errors

    widths, errors = open_as("admin", "/profile/analyst1", use, width=width)
    assert all(scroll_width == width for scroll_width in widths.values()), widths
    assert errors == []


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("path", ["/profile/analyst1", "/explore/users"])
def test_a_peers_pages_scroll_nowhere_sideways(open_as, path, width):
    scroll_width, errors = open_as("visitor", path, lambda page, errors: (page.evaluate("() => document.documentElement.scrollWidth"), errors), width=width)
    assert scroll_width == width
    assert errors == []


def test_the_arrow_keys_and_the_hash_work_on_another_accounts_page(open_as):
    def use(page, errors):
        opened = shown_pane(page)
        page.focus("#tab-uploads")
        page.keyboard.press("ArrowLeft")
        return opened, page.evaluate("() => document.activeElement.id"), shown_pane(page), page.evaluate("() => location.hash"), page.locator("#tab-developer").count()

    opened, focused, shown, hash_value, developer_tabs = open_as("admin", "/profile/analyst1#uploads", use)
    assert opened == "uploads"
    assert (focused, shown, hash_value) == ("tab-submissions", "submissions", "#submissions")
    assert developer_tabs == 0


def test_a_peer_sees_the_submissions_pane_and_only_that(open_as):
    def use(page, errors):
        return shown_pane(page), page.locator(".tab-pane").count(), page.evaluate("() => document.getElementById('profileTabList').getBoundingClientRect().width"), page.inner_text("h1")

    shown, panes, tab_row_width, heading = open_as("visitor", "/profile/analyst1#uploads", use)
    assert shown == "submissions" and panes == 1
    assert tab_row_width <= 1, "a one-tab row is hidden"
    assert heading.startswith("Profile: analyst1")
