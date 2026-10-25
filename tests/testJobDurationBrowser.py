#!/usr/bin/python
"""The running total of a job with dependencies, counting on in a browser.

The total is rendered up to the latest time the backend wrote on the job or its
children (see testJobDuration.py), and from there the page counts on by itself, so it
does not stand still between refreshes. `playwright` is not a dependency of this
project and CI does not install it, so this module skips there rather than failing.
"""

import logging
import os

import pytest
from conftest import session_cookie_for
from fixtureData import job_id_of
from testFunctionVsBrowser import live_server  # noqa: F401 - a fixture
from testJobDuration import CorpusStillRunningCross

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit():
    return CorpusStillRunningCross()


@pytest.fixture
def browser_page(app, live_server, make_user):  # noqa: F811
    """A Chromium page logged in as a visitor, with its clock under the test's control."""
    user_id = make_user(role="visitor")
    cookie = session_cookie_for(app, user_id)
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context()
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            page.set_default_timeout(20000)
            page.clock.install()
            yield page
        finally:
            browser.close()


def test_the_running_total_counts_on(browser_page, live_server):  # noqa: F811
    errors = []
    browser_page.on("pageerror", lambda error: errors.append(str(error)))
    browser_page.goto(f"{live_server}/data/jobs/{job_id_of('cross_compare')}")
    total = browser_page.locator("[data-elapsed-seconds]")
    assert total.text_content() == "0:00:08"

    browser_page.clock.run_for(2000)
    assert total.text_content() == "0:00:10"

    # a day and an hour later, in the format the server renders a timedelta in
    # (fast_forward fires the interval once, where run_for would fire it 90,000 times)
    browser_page.clock.fast_forward((25 * 3600 - 2) * 1000)
    assert total.text_content() == "1 day, 1:00:08"
    assert errors == []


def test_the_running_total_stops_while_the_tab_is_hidden(browser_page, live_server):  # noqa: F811
    """Nobody sees it count in a hidden tab, which it woke once a second for. It counts from
    the time the page was loaded, so it is right again as soon as the tab is shown."""
    from testJobOverviewBrowser import HIDDEN_TAB
    browser_page.add_init_script(HIDDEN_TAB)
    browser_page.goto(f"{live_server}/data/jobs/{job_id_of('cross_compare')}")
    total = browser_page.locator("[data-elapsed-seconds]")
    browser_page.evaluate("""() => {
        window.mcritWrites = 0;
        new MutationObserver((records) => { window.mcritWrites += records.length; })
            .observe(document.querySelector("[data-elapsed-seconds]"), {childList: true, characterData: true, subtree: true});
    }""")

    browser_page.evaluate("mcritSetHidden(true)")
    browser_page.clock.run_for(5000)
    assert total.text_content() == "0:00:08"
    assert browser_page.evaluate("window.mcritWrites") == 0

    browser_page.evaluate("mcritSetHidden(false)")
    assert total.text_content() == "0:00:13"
    browser_page.clock.run_for(1000)
    assert total.text_content() == "0:00:14"
