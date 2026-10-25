#!/usr/bin/python
"""What moves on a page when the reader has asked for less motion, in a browser.

Bootstrap's own transitions (its fades, collapses and dialogs) are off under
`prefers-reduced-motion: reduce` already, in the vendored stylesheet. The upload form's fields
were not: jQuery slides them open and shut.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import os
import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

#: the page's own timeout: the app answers in milliseconds, so this is paid only by a test about to fail
TIMEOUT_MS = 10000

#: whether jQuery is sliding the element, and how tall it is: read in one go, right after the click
SLIDING = "(selector) => [jQuery(selector).is(':animated'), document.querySelector(selector).getBoundingClientRect().height]"


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
def open_page(app, live_server, make_user):
    """open_page(path, reduced_motion) -> a page of a contributor's browser, once its scripts have run."""
    cookie = session_cookie_for(app, make_user(role="contributor"))
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")

        def _open(path, reduced_motion):
            context = browser.new_context(reduced_motion=reduced_motion)
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            page.set_default_timeout(TIMEOUT_MS)
            page.goto(live_server + path)
            # whenReady's handlers have run once jQuery's ready queue has
            page.wait_for_function("window.jQuery !== undefined && jQuery.isReady && window.bootstrap !== undefined")
            return page

        try:
            yield _open
        finally:
            browser.close()


def test_with_reduced_motion_the_upload_forms_fields_open_without_sliding(open_page):
    """Choosing "Dumped" slid the base address field open over 400 ms, whatever the reader had
    asked for: jQuery's slideDown() does not look at prefers-reduced-motion."""
    page = open_page("/data/submit", "reduce")

    page.click("#dumped")
    animated, height = page.evaluate(SLIDING, "#extendedFields")

    assert not animated, "the fields slide open"
    assert height > 0, "the fields did not open"


def test_the_upload_form_follows_a_change_of_the_preference_while_the_page_is_open(open_page):
    """The preference can change while a page is open (a setting of the operating system); the
    form has to follow it either way without a reload."""
    page = open_page("/data/submit", "no-preference")
    page.click("#dumped")
    assert page.evaluate(SLIDING, "#extendedFields")[0], "without a preference the fields slide, as they always did"
    page.wait_for_function("!jQuery('#extendedFields').is(':animated')")
    page.click("#unmapped")
    page.wait_for_function("!jQuery('#extendedFields').is(':animated') && getComputedStyle(document.getElementById('extendedFields')).display === 'none'")

    page.emulate_media(reduced_motion="reduce")
    page.click("#dumped")
    assert not page.evaluate(SLIDING, "#extendedFields")[0], "after the switch to reduce, the fields still slide"

    page.emulate_media(reduced_motion="no-preference")
    page.click("#unmapped")
    assert page.evaluate(SLIDING, "#extendedFields")[0], "after the switch back, the fields no longer slide"

