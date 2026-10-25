#!/usr/bin/python
"""The running job overview's polling script, driven in a browser. Issue #183.

testJobOverview.py pins what the page and `data.job_status` render. What the script
does with them - leave an idle page alone, reload once on a change, give up on a job
that is gone - only happens in a browser, so these tests load the page in Chromium
and count the navigations and polls it makes. `/status` is answered either by the
real endpoint over the fake backend or, where a test needs one particular answer, by
`page.route`.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing, as testFunctionVsBrowser.py does.
"""

import json
import logging
import os
import threading

import pytest
from conftest import session_cookie_for
from testJobOverview import PARENT_JOB_ID, CountingJobs, job_data, running
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SCRIPT_TIMEOUT_MS = 20000

#: The page's poll interval in seconds, as the `refresh` parameter. The shortest the
#: parameter can say, so each test waits a few ticks rather than a few minutes.
REFRESH = 1

#: Long enough for three ticks at REFRESH, the number these tests reason about.
THREE_TICKS_MS = 3 * REFRESH * 1000 + 700


@pytest.fixture
def backend(app):
    """A running cross compare, one of its two sub-jobs still unfinished."""
    children = [job_data("child-7", 7, params='{"0": 7}'), running(job_data("child-8", 8, params='{"0": 8}'))]
    parent = running(job_data(PARENT_JOB_ID, 10, "combineMatchesToCross", ["child-7", "child-8"]))
    parent["unfinished_dependencies"] = ["child-8"]
    jobs = CountingJobs(parent, children)
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: jobs
    return jobs


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


class Traffic:
    """The overview loads and status polls a page makes, counted as they are sent."""

    def __init__(self, page):
        self.loads = 0
        self.polls = 0
        page.on("request", self._count)

    def _count(self, request):
        if request.url.split("?")[0].endswith(f"/data/jobs/{PARENT_JOB_ID}"):
            self.loads += 1
        elif request.url.endswith(f"/data/jobs/{PARENT_JOB_ID}/status"):
            self.polls += 1


def open_overview(page, live_server):
    traffic = Traffic(page)
    page.goto(f"{live_server}/data/jobs/{PARENT_JOB_ID}?refresh={REFRESH}")
    return traffic


def status(**changes):
    """The endpoint's answer for the backend fixture's parent, with `changes` applied."""
    answer = {"started": True, "finished": False, "failed": False, "progress": 0.25, "unfinished_sub_jobs": 1}
    answer.update(changes)
    return json.dumps(answer)


def test_an_idle_tick_does_not_reload(browser_page, live_server, backend):
    traffic = open_overview(browser_page, live_server)
    browser_page.wait_for_timeout(THREE_TICKS_MS)

    assert traffic.polls >= 2, "the page stopped polling"
    assert traffic.loads == 1, "an unchanged job reloaded the page"


@pytest.fixture
def solo_backend(app):
    """A running job without sub-jobs, under the id the traffic counter watches."""
    jobs = CountingJobs(running(job_data(PARENT_JOB_ID, 10, params='{"0": 7}')), [])
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: jobs
    return jobs


def progress_text(page):
    return page.evaluate("document.getElementById('job-progress').textContent.replace(/\\s+/g, ' ').trim()")


def progress_bar(page):
    """The value of the cell's progress bar, which assistive technology reads; None without one."""
    return page.evaluate("(document.querySelector('#job-progress progress') || {}).value")


def test_progress_alone_is_updated_in_place(browser_page, live_server, solo_backend):
    answer = status(progress=0.5, unfinished_sub_jobs=0)
    browser_page.route(f"**/data/jobs/{PARENT_JOB_ID}/status", lambda route: route.fulfill(status=200, content_type="application/json", body=answer))
    traffic = open_overview(browser_page, live_server)
    browser_page.wait_for_function("() => document.getElementById('job-progress').textContent.trim() === '50.00%'")

    assert progress_bar(browser_page) == 50
    assert traffic.loads == 1


def test_the_dependencies_average_is_updated_in_place(browser_page, live_server, backend):
    """The cell of a job waiting on sub-jobs shows their average (#46): a tick takes it
    from dependency_progress, not from the job's own counter, which stays at 0."""
    answer = status(progress=0.0, dependency_progress=0.75, dependencies=2)
    browser_page.route(f"**/data/jobs/{PARENT_JOB_ID}/status", lambda route: route.fulfill(status=200, content_type="application/json", body=answer))
    traffic = open_overview(browser_page, live_server)
    assert progress_text(browser_page) == "62.50% (across 2 dependencies)"
    browser_page.wait_for_function("() => document.getElementById('job-progress').textContent.includes('75.00%')")

    assert progress_text(browser_page) == "75.00% (across 2 dependencies)"
    assert progress_bar(browser_page) == 75
    assert traffic.loads == 1


def test_the_dependencies_average_stays_when_no_sub_job_could_be_read(browser_page, live_server, backend):
    """A tick that read none of the sub-jobs has no average to give; the cell keeps the
    one it has rather than falling back to the job's own 0."""
    answer = status(progress=0.0, dependency_progress=None, dependencies=0)
    browser_page.route(f"**/data/jobs/{PARENT_JOB_ID}/status", lambda route: route.fulfill(status=200, content_type="application/json", body=answer))
    traffic = open_overview(browser_page, live_server)
    browser_page.wait_for_timeout(THREE_TICKS_MS)

    assert traffic.polls >= 2
    assert progress_text(browser_page) == "62.50% (across 2 dependencies)"
    assert traffic.loads == 1


def test_a_changed_state_reloads_once(browser_page, live_server, backend):
    """The first poll reports the last sub-job finished; the endpoint answers for real
    afterwards, which matches what the reloaded page renders, so there is no second."""
    answered = []

    def first_poll_changed(route):
        if answered:
            route.continue_()
        else:
            answered.append(True)
            route.fulfill(status=200, content_type="application/json", body=status(unfinished_sub_jobs=0))

    browser_page.route(f"**/data/jobs/{PARENT_JOB_ID}/status", first_poll_changed)
    traffic = open_overview(browser_page, live_server)
    browser_page.wait_for_timeout(THREE_TICKS_MS)

    assert traffic.loads == 2, "a changed state should reload exactly once"
    assert traffic.polls >= 2, "the reloaded page stopped polling"


def test_a_job_that_is_gone_reloads_once_and_stops(browser_page, live_server, backend):
    """The poll's 404 reloads the page, which then renders the job as invalid - a page
    with no script - rather than retrying the poll forever."""
    traffic = open_overview(browser_page, live_server)
    del backend._jobs[PARENT_JOB_ID]
    browser_page.wait_for_timeout(THREE_TICKS_MS)

    assert traffic.polls == 1
    assert traffic.loads == 2
    assert f"/data/jobs/{PARENT_JOB_ID}/status" not in browser_page.content()


#: A tab the test can hide and show: headless Chromium reports every page as visible, so
#: `document.hidden` is the test's to set, and setting it sends the visibilitychange a
#: browser would.
HIDDEN_TAB = """
window.mcritHidden = false;
Object.defineProperty(document, "hidden", {configurable: true, get: () => window.mcritHidden});
Object.defineProperty(document, "visibilityState", {configurable: true, get: () => window.mcritHidden ? "hidden" : "visible"});
window.mcritSetHidden = (hidden) => {
    window.mcritHidden = hidden;
    document.dispatchEvent(new Event("visibilitychange"));
};
"""

#: an interval long enough that a poll made at once on showing the tab cannot be the timer's
SLOW_REFRESH = 5


def test_a_hidden_tab_is_not_polled_and_is_polled_at_once_when_shown(browser_page, live_server, backend):
    """A job watched from a background tab cost the backend a status read every interval,
    for as long as the tab stayed open: about 1,200 an hour at refresh=3."""
    browser_page.add_init_script(HIDDEN_TAB)
    traffic = Traffic(browser_page)
    browser_page.goto(f"{live_server}/data/jobs/{PARENT_JOB_ID}?refresh={SLOW_REFRESH}")
    browser_page.evaluate("mcritSetHidden(true)")
    browser_page.wait_for_timeout(SLOW_REFRESH * 1000 + 1500)
    assert traffic.polls == 0, "a hidden tab was polled"

    browser_page.evaluate("mcritSetHidden(false)")
    browser_page.wait_for_timeout(1500)
    assert traffic.polls == 1, "showing the tab did not poll at once"
    browser_page.wait_for_timeout(SLOW_REFRESH * 1000)
    assert traffic.polls == 2, "the polling did not go on at its interval"
    assert traffic.loads == 1


def test_a_tab_opened_in_the_background_waits_to_be_shown(browser_page, live_server, backend):
    browser_page.add_init_script(HIDDEN_TAB.replace("window.mcritHidden = false;", "window.mcritHidden = true;"))
    traffic = open_overview(browser_page, live_server)
    browser_page.wait_for_timeout(THREE_TICKS_MS)
    assert traffic.polls == 0

    browser_page.evaluate("mcritSetHidden(false)")
    browser_page.wait_for_timeout(THREE_TICKS_MS)
    assert traffic.polls >= 2
    assert traffic.loads == 1
