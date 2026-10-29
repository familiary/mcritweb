#!/usr/bin/python
"""The cross compare picker's row clicks, driven in a browser.

A sample clicked on one page of the picker is carried to the next as `?cache=`, and its
row comes back tinted. The script kept those ids as the numbers the URL parsed into, but
compared and pushed a clicked row's id as the text of its cell, so a tinted row could not
be clicked off again: the click pushed the same sample a second time, and "Add" then sent
it twice. These tests click such a row, and one the URL did not carry.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing, as testFunctionVsBrowser.py does.
"""

import logging
import os
import re
import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SCRIPT_TIMEOUT_MS = 20000

#: the tint of a row picked but not yet added, as row_decoration.html paints it
PENDING = "rgb(240, 240, 240)"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """The captured corpus, so the picker lists its samples."""
    return corpus_mcrit


@pytest.fixture
def live_server(app):
    """The app under test on a loopback port - see testFunctionVsBrowser.py."""
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
def browser_page(app, live_server, make_user):
    """A Chromium page logged in as a visitor - see testFunctionVsBrowser.py."""
    user_id = make_user(role="visitor")
    cookie = session_cookie_for(app, user_id)

    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context()
            context.add_cookies([{
                "name": app.config["SESSION_COOKIE_NAME"],
                "value": cookie,
                "domain": "127.0.0.1",
                "path": "/",
            }])
            page = context.new_page()
            page.set_default_timeout(SCRIPT_TIMEOUT_MS)
            yield page
        finally:
            browser.close()


def open_picker(page, live_server, query=""):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{live_server}/analyze/cross_compare{query}")
    page.wait_for_load_state("load")
    return errors


def row_of(page, sample_id):
    return page.locator("tr.parent").filter(has=page.locator("th.id", has_text=re.compile(rf"^{sample_id}$")))


def is_pending(row):
    """Read from the computed style, so it holds whether the tint is an inline style or a class."""
    return row.evaluate("row => getComputedStyle(row).backgroundColor") == PENDING


def test_a_row_the_url_carried_clicks_off_and_on_again(browser_page, live_server):
    errors = open_picker(browser_page, live_server, "?cache=1")
    row = row_of(browser_page, 1)
    assert is_pending(row)

    row.locator("th.id").click()
    assert not is_pending(row)
    assert browser_page.evaluate("selected") == []
    assert browser_page.evaluate("joinSampleIds()") == ""

    row.locator("th.id").click()
    assert is_pending(row)
    assert browser_page.evaluate("selected") == ["1"]
    assert browser_page.evaluate("joinSampleIds()") == "1"
    assert errors == []


def test_a_row_clicked_on_this_page_clicks_off_again(browser_page, live_server):
    errors = open_picker(browser_page, live_server, "?cache=1")
    row = row_of(browser_page, 2)
    assert not is_pending(row)

    row.locator("th.id").click()
    assert is_pending(row)
    assert browser_page.evaluate("joinSampleIds()") == "1,2"

    row.locator("th.id").click()
    assert not is_pending(row)
    assert browser_page.evaluate("joinSampleIds()") == "1"
    assert errors == []
