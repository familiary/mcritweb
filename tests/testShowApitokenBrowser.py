#!/usr/bin/python
"""The settings page's "Show token" button, driven in a browser.

/settings no longer prints the API token (testShowApitoken.py); static/show_apitoken.js
fetches it from admin.show_apitoken when the button is pressed, and once on its own after
a regenerate, which used to lead back to a page with the new token on it. These tests
press it with CSRF protection on, as a deployment has it, and check that the token lands
where it was shown before, that the copy button next to it copies it, that a failure says
so, and that a page left with the token on it does not keep it.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import os
import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

from mcritweb.csrf import ENABLED_KEY
from mcritweb.db import UserInfo, generate_apitoken

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

TIMEOUT_MS = 30000
SHOW = "button[data-show-apitoken]"
VALUE = "#apitoken-value"
STATUS = "#apitoken-status"
COPY = "#apitoken-copy .btn-clipboard"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def app(app):
    app.config[ENABLED_KEY] = True
    return app


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
def signed_in(request, app, make_user):
    """A page signed in as a contributor holding a real token, that token, and the page's errors.

    Playwright starts Chromium with its back/forward cache switched off, and the headless
    shell it uses by default never restores a page from it either. A test that needs the
    cache asks for it with `indirect` parametrisation, {"bfcache": True}, and gets the full
    Chromium build, in its new headless mode, with the cache on."""
    bfcache = getattr(request, "param", {}).get("bfcache", False)
    user_id = make_user(role="contributor")
    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        user_info.apitoken = generate_apitoken()
        user_info.saveToDb()
        token = user_info.apitoken
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None,
                                                 channel="chromium" if bfcache else None,
                                                 ignore_default_args=["--disable-back-forward-cache"] if bfcache else None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": session_cookie_for(app, user_id),
                                  "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            page.set_default_timeout(TIMEOUT_MS)
            page.errors = []
            page.on("pageerror", lambda error: page.errors.append(str(error)))
            yield page, token, user_id
        finally:
            browser.close()


def test_show_token_fills_in_the_token_and_the_copy_button_copies_it(signed_in, live_server):
    page, token, _user_id = signed_in
    page.goto(f"{live_server}/settings")
    assert page.text_content(VALUE) == ""
    assert not page.is_visible(COPY), "a copy button with nothing to copy"
    page.click(SHOW)
    page.wait_for_function("() => document.querySelector('#apitoken-value').textContent !== ''")
    assert page.text_content(VALUE) == token
    assert page.get_attribute(STATUS, "role") == "status"
    assert page.text_content(STATUS).strip() == "API token shown."
    page.click(COPY)
    page.wait_for_function("(token) => navigator.clipboard.readText().then((text) => text === token)", arg=token)
    assert page.errors == []


def test_a_press_before_the_script_is_in_is_answered_once_it_is(signed_in, live_server):
    """The button is on the page before the queued scripts are; a press then is held, as
    base.html holds one on a data-post button, and made again once they have run."""
    page, token, _user_id = signed_in
    held = []
    page.route("**/static/show_apitoken.js", lambda route: held.append(route))
    page.goto(f"{live_server}/settings", wait_until="commit")
    page.wait_for_selector(SHOW)
    page.click(SHOW)
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the script was never asked for"
    assert page.text_content(VALUE) == ""
    held[0].continue_()
    page.wait_for_function("(token) => document.querySelector('#apitoken-value').textContent === token", arg=token)
    assert page.errors == []


def test_a_regenerated_token_is_shown_at_once(app, signed_in, live_server):
    page, old_token, user_id = signed_in
    page.goto(f"{live_server}/settings")
    page.wait_for_function("() => window.bootstrap !== undefined")
    with page.expect_navigation():
        page.click("button[data-post$='/regenerate_apitoken']")
    with app.app_context():
        new_token = UserInfo.fromDb(user_id=user_id).apitoken
    assert new_token != old_token
    page.wait_for_function("(token) => document.querySelector('#apitoken-value').textContent === token", arg=new_token)
    page.click(COPY)
    page.wait_for_function("(token) => navigator.clipboard.readText().then((text) => text === token)", arg=new_token)
    assert page.errors == []


def test_a_failure_says_so_and_shows_nothing(signed_in, live_server):
    page, token, _user_id = signed_in
    page.route("**/admin/show_apitoken", lambda route: route.fulfill(status=500, body="no"))
    page.goto(f"{live_server}/settings")
    page.click(SHOW)
    page.wait_for_function("() => document.querySelector('#apitoken-status').textContent !== ''")
    assert "Could not load the API token" in page.text_content(STATUS)
    assert page.text_content(VALUE) == ""
    assert not page.is_visible(COPY)
    assert page.errors == []


def test_a_signed_out_session_is_told_to_sign_in_again(signed_in, live_server):
    """The route sends a signed-out caller to /login, which the fetch follows to a page of
    HTML: that is not a token, and is not shown as one."""
    page, token, _user_id = signed_in
    page.goto(f"{live_server}/settings")
    page.wait_for_function("() => window.bootstrap !== undefined")
    page.context.clear_cookies()
    page.click(SHOW)
    page.wait_for_function("() => document.querySelector('#apitoken-status').textContent !== ''")
    assert "Could not load the API token" in page.text_content(STATUS)
    assert page.text_content(VALUE) == ""


@pytest.mark.parametrize("signed_in", [{"bfcache": True}], indirect=True)
def test_the_token_does_not_stay_on_a_page_that_is_left(signed_in, live_server):
    """The page is no longer no-store, so the back/forward cache keeps it, with whatever
    the script put on it, and Back - after logout, too - brings it back without asking the
    server. The token is taken off it as it is left."""
    page, token, _user_id = signed_in
    page.goto(f"{live_server}/settings")
    page.click(SHOW)
    page.wait_for_function("(token) => document.querySelector('#apitoken-value').textContent === token", arg=token)
    page.evaluate("() => { window.keptInTheCache = true; }")
    page.goto(f"{live_server}/help")
    # a page restored from the cache fires no load event
    page.go_back(wait_until="commit")
    page.wait_for_selector(SHOW, state="attached")
    assert page.evaluate("() => window.keptInTheCache === true"), "the page was loaded again, not restored"
    assert page.text_content(VALUE) == ""
    assert token not in page.content()
    assert not page.is_visible(COPY)
