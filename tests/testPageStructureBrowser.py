#!/usr/bin/python
"""Page structure that only exists once a browser has run the page's scripts.

The markup checks are in testTableSemantics.py and the other structure tests; what is here
is what a script builds or changes: the cross compare panes filled on first click, and the
other script-driven pieces of the page's semantics.

The harness is the offline one of tests/testLiveSearchBrowser.py: `CorpusMcritClient`
behind the app, served on a loopback port. `playwright` is not a dependency of this
project and CI does not install it, so this module skips there rather than failing.
"""

import os
import threading

import pytest
from conftest import session_cookie_for
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

TIMEOUT_MS = 20000

#: resolves once base.html's queue has run (ADR-0021)
QUEUE_HAS_RUN = "() => new Promise(resolve => whenReady(() => resolve(true)))"


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
def page(app, live_server, make_user):
    """A Chromium page signed in as a contributor."""
    user_id = make_user(role="contributor")
    cookie = session_cookie_for(app, user_id)
    with sync_api.sync_playwright() as playwright:
        try:
            # a Chromium that playwright did not install itself is named through MCRITWEB_CHROMIUM
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context()
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie,
                                  "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            page.set_default_timeout(TIMEOUT_MS)
            page.errors = []
            page.on("pageerror", lambda error: page.errors.append(str(error)))
            yield page
        finally:
            browser.close()


def open_page(page, url):
    page.goto(url)
    page.evaluate(QUEUE_HAS_RUN)


def test_a_cross_compare_pane_filled_on_first_click_keeps_the_table_s_name(page, live_server):
    """The panes other than the first are built by cloning the rendered table without its
    children, so the caption has to be copied over or those tables have no name."""
    open_page(page, f"{live_server}/data/result/{job_id_of('cross_compare')}")

    page.locator("#pills-score_weighted-tab").click()
    page.wait_for_selector("#pills-score_weighted table")

    captions = page.locator("#pills-tabContent table > caption").all_inner_texts()
    assert captions == ["Cross comparison matrix"] * 2
    assert page.errors == []
