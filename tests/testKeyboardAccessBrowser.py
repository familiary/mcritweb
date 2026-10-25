#!/usr/bin/python
"""Keyboard access, driven in a browser: Tab, Enter, Space and the arrow keys.

testKeyboardAccess.py reads the markup for the shapes that kept a keyboard out. Whether a
key then does what it should only shows once a browser runs the page and its scripts, so
these tests press the keys in Chromium and read back focus, URLs and state.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing.
"""

import os
import threading

import pytest
from conftest import session_cookie_for
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

SCRIPT_TIMEOUT_MS = 20000
#: how many Tab presses a test allows itself to reach a control, from the top of the page
TAB_BUDGET = 250


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    # renaming a function needs mcrit 1.10.0, below which the page offers no rename dialog
    corpus_mcrit.getVersion = lambda *args, **kwargs: {"version": "1.10.0"}
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
def open_as(app, live_server, make_user):
    """open_as(role, path) -> a Chromium page signed in as `role`, at `path`, once its
    queued scripts have run."""
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            def _open(role, path):
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.add_cookies([{
                    "name": app.config["SESSION_COOKIE_NAME"],
                    "value": session_cookie_for(app, make_user(role=role)),
                    "domain": "127.0.0.1",
                    "path": "/",
                }])
                page = context.new_page()
                page.set_default_timeout(SCRIPT_TIMEOUT_MS)
                page.goto(live_server + path)
                ready(page)
                return page
            yield _open
        finally:
            browser.close()


def ready(page):
    """Once every queued script has run: whenReady() is replaced by then."""
    page.wait_for_function("() => window.jQuery !== undefined && window.bootstrap !== undefined")
    page.evaluate("() => new Promise(resolve => whenReady(resolve))")


def focused(page):
    return page.evaluate("() => document.activeElement.id || document.activeElement.tagName")


def tab_to(page, selector):
    """Press Tab from the top of the page until `selector` has focus."""
    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    for _ in range(TAB_BUDGET):
        page.keyboard.press("Tab")
        if page.evaluate("selector => document.activeElement.matches(selector)", selector):
            return
    raise AssertionError(f"{TAB_BUDGET} presses of Tab never reached {selector}")


@pytest.mark.parametrize("path, opener, dialog", [
    ("/explore/families", "[id^=family_][id$=_edit]", "#editFamilyModal"),
    ("/explore/samples", "[id^=sample_][id$=_edit]", "#editSampleModal"),
    ("/explore/samples/0", "#sample_0_edit", "#editSampleModal"),
    ("/explore/functions/0", "#button_function_rename", "#renameFunctionModal"),
    ("/explore/functions/0", "[data-bs-target='#apiUsageModal']", "#apiUsageModal"),
    (f"/data/result/{job_id_of('cross_compare')}", "[id$=_edit]", "#editSampleModal"),
])
def test_a_dialog_opens_from_the_keyboard(open_as, path, opener, dialog):
    """Tab reaches the control that opens it and Enter opens it. They were anchors
    without an href, which Tab passes over."""
    page = open_as("admin", path)

    tab_to(page, opener)
    page.keyboard.press("Enter")

    page.wait_for_selector(f"{dialog}.show")
    assert page.get_attribute(dialog, "aria-modal") == "true"
