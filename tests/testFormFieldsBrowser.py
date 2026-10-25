#!/usr/bin/python
"""The sign-in, registration and settings forms in a browser: static/form_fields.js.

What a field says about itself to a screen reader has to follow what the page shows, and
neither can be read off the HTML the server sends.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import os
import threading

import pytest
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

WAIT_FOR_THE_QUEUE = ("() => new Promise((resolve, reject) => { whenReady(() => resolve(true));"
                      " setTimeout(() => reject(new Error('the queued scripts did not run')), 10000); })")


@pytest.fixture
def live_server(app, make_user):
    make_user(role="visitor", username="walker")
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
def page(live_server):
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            page = browser.new_page()
            page.errors = []
            page.on("pageerror", lambda error: page.errors.append(str(error)))
            page.live_server = live_server
            yield page
        finally:
            browser.close()


def open_ready(page, path):
    page.goto(page.live_server + path)
    page.evaluate(WAIT_FOR_THE_QUEUE)


def invalid(page, selector):
    return page.get_attribute(selector, "aria-invalid")


def test_a_field_is_announced_invalid_when_it_is_shown_invalid(page):
    """:user-invalid paints a field once the user has left it in a state its constraints
    refuse; aria-invalid now says the same, at the same moment, and not before."""
    open_ready(page, "/register")
    page.fill("#username", "a")
    assert invalid(page, "#username") is None, "invalid before the user left the field"
    page.press("#username", "Tab")
    assert invalid(page, "#username") == "true"
    assert page.evaluate("() => document.getElementById('username').matches(':user-invalid')")

    page.fill("#username", "alice")
    assert invalid(page, "#username") is None
    assert page.errors == []


def test_editing_a_refused_field_withdraws_the_verdict(page):
    """The server's message and mark describe what was sent, and stop being true as soon
    as the field is edited; a sign-in refusal concerns both fields, so both are cleared."""
    open_ready(page, "/login")
    page.fill("#username", "walker")
    page.fill("#current-password", "not the password")
    with page.expect_navigation():
        page.click("button[type=submit]")
    page.evaluate(WAIT_FOR_THE_QUEUE)
    assert page.evaluate("() => document.activeElement.id") == "current-password"
    assert invalid(page, "#username") == invalid(page, "#current-password") == "true"
    assert page.is_visible("#current-password-error")

    page.keyboard.type("password")
    assert invalid(page, "#current-password") is None and invalid(page, "#username") is None
    assert not page.is_visible("#current-password-error")
    assert page.get_attribute("#username", "aria-describedby") is None
    assert page.errors == []

