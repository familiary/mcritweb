#!/usr/bin/python
"""A job page's folded Task row, opened from the keyboard and by touch. Issue #300.

The Task row folds a unique blocks job's sample ids into ranges or a count, and opens
into every one of them as a <details> whose summary is the line (testJobNames.py pins
that markup). That a summary is reached with Tab, toggles with Enter and Space and opens
on a tap is the browser's doing rather than the template's, which is what this checks.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing, as testFunctionVsBrowser.py does.
"""

import logging
import os
import threading

import pytest
from testJobNames import TASK_JOB_ID, serve_unique_blocks_job
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SCRIPT_TIMEOUT_MS = 20000

#: More than any job page has focusable elements before its Task row.
MAX_TABS = 200

SAMPLE_IDS = [30, 14, 15, 16, 17, 20, 21]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
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
    """A Chromium page with touch, logged in as a visitor - see testFunctionVsBrowser.py."""
    user_id = make_user(role="visitor")
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})

    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context(has_touch=True)
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


@pytest.fixture
def job_page(browser_page, live_server, corpus_mcrit):
    serve_unique_blocks_job(corpus_mcrit, SAMPLE_IDS)
    browser_page.goto(f"{live_server}/data/jobs/{TASK_JOB_ID}")
    return browser_page


def task_row(page):
    return page.locator("td.job-description details")


def sample_links(page):
    return task_row(page).locator("a")


def test_the_folded_task_row_opens_and_closes_from_the_keyboard(job_page):
    for _ in range(MAX_TABS):
        job_page.keyboard.press("Tab")
        if job_page.evaluate("document.activeElement.closest('td.job-description') !== null"):
            break
    else:
        pytest.fail("Tab never reached the Task row")
    assert job_page.evaluate("document.activeElement.tagName") == "SUMMARY"
    assert not sample_links(job_page).first.is_visible(), "the samples are listed before the row is opened"

    job_page.keyboard.press("Enter")
    assert task_row(job_page).evaluate("details => details.open")
    assert [link.inner_text() for link in sample_links(job_page).all()] == [str(sample_id) for sample_id in SAMPLE_IDS]
    assert all(link.is_visible() for link in sample_links(job_page).all())

    job_page.keyboard.press("Space")
    assert not task_row(job_page).evaluate("details => details.open")


def test_the_folded_task_row_opens_on_a_tap(job_page):
    job_page.locator("td.job-description summary").tap()
    assert task_row(job_page).evaluate("details => details.open")
    assert sample_links(job_page).first.is_visible()
