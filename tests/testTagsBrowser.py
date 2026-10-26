#!/usr/bin/python
"""The tag field of the edit modals, driven in a browser (familiary/mcrit#53).

The family and sample modals are one form per table, filled from the clicked button's
`data-bs-*` attributes by a script when the modal opens - three copies of that script,
in family_row_std_js, sample_row_std_js and the sample page. testTags.py asserts the
attributes are there; only a browser shows the field actually receiving them, and that a
tag the field is given makes it to the backend through the form, CSRF token included.

The harness is the offline one of testFunctionVsBrowser.py: the tagging corpus of
testTags.py behind MCRIT_CLIENT_FACTORY, served on a loopback port. `playwright` is not
a dependency of this project and CI does not install it, so this module skips there.
"""

import logging
import os
import threading

import pytest
from conftest import session_cookie_for
from testTags import FAMILY_ID, SAMPLE_ID, TaggingCorpus, mcrit_tag_rule  # noqa: F401 - the rule is an autouse fixture
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

logging.disable(logging.CRITICAL)

SCRIPT_TIMEOUT_MS = 20000


@pytest.fixture
def tagging_mcrit():
    corpus = TaggingCorpus()
    corpus.stored_tags["family"][FAMILY_ID] = ["apt", "source:vt"]
    corpus.stored_tags["sample"][SAMPLE_ID] = ["packed", "two words"]
    return corpus


@pytest.fixture
def fake_mcrit(tagging_mcrit):
    return tagging_mcrit


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
def browser_page(app, live_server, make_user):
    """A Chromium page signed in as a contributor, collecting console errors."""
    user_id = make_user(role="contributor")
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
            page.set_default_timeout(SCRIPT_TIMEOUT_MS)
            page.console_errors = []
            page.on("console", lambda message: message.type == "error" and page.console_errors.append(message.text))
            page.on("pageerror", lambda error: page.console_errors.append(str(error)))
            yield page
        finally:
            browser.close()


@pytest.mark.parametrize("path, button, field, expected", [
    ("/explore/families", f'[data-bs-target="#editFamilyModal"][data-bs-familyid="{FAMILY_ID}"]', "#family_tags", "apt, source:vt"),
    ("/explore/samples", f"#sample_{SAMPLE_ID}_edit", "#sample_tags", "packed, two words"),
    (f"/explore/samples/{SAMPLE_ID}", f"#sample_{SAMPLE_ID}_edit", "#sample_tags", "packed, two words"),
])
def test_the_modal_opens_with_the_entrys_tags(browser_page, live_server, path, button, field, expected):
    browser_page.goto(live_server + path)
    browser_page.click(button)
    browser_page.wait_for_selector(field, state="visible")
    assert browser_page.input_value(field) == expected
    assert browser_page.console_errors == []


def test_the_field_is_refilled_for_each_row(browser_page, live_server, tagging_mcrit):
    """One modal serves every row: opening it for an untagged sample after a tagged one
    must not leave the first one's tags in the field, to be saved onto the second."""
    other_sample = next(sample_id for sample_id in sorted(tagging_mcrit._samples) if sample_id != SAMPLE_ID)
    browser_page.goto(live_server + "/explore/samples")
    browser_page.click(f"#sample_{SAMPLE_ID}_edit")
    browser_page.wait_for_selector("#sample_tags", state="visible")
    browser_page.click('#editSampleModal [data-bs-dismiss="modal"]')
    browser_page.wait_for_selector("#sample_tags", state="hidden")
    browser_page.click(f"#sample_{other_sample}_edit")
    browser_page.wait_for_selector("#sample_tags", state="visible")
    assert browser_page.input_value("#sample_tags") == ""


def test_tags_saved_from_the_sample_page_reach_mcrit(browser_page, live_server, tagging_mcrit):
    browser_page.goto(f"{live_server}/explore/samples/{SAMPLE_ID}")
    browser_page.click(f"#sample_{SAMPLE_ID}_edit")
    browser_page.fill("#sample_tags", "packed, Reviewed")
    with browser_page.expect_navigation():
        browser_page.click('#editSampleModal button[type="submit"]:not([hidden])')
    assert f"Tags of sample {SAMPLE_ID} saved." in browser_page.content()
    assert tagging_mcrit.stored_tags["sample"][SAMPLE_ID] == ["packed", "reviewed"]
    assert browser_page.console_errors == []


def test_the_tag_line_does_not_shift_the_page(browser_page, live_server):
    """The tags are in the served markup, under the heading, so nothing moves once the
    page has painted - Lighthouse counts any shift against it."""
    browser_page.goto(f"{live_server}/explore/families/{FAMILY_ID}")
    browser_page.wait_for_load_state("load")
    shifted = browser_page.evaluate("""() => new Promise(resolve => {
        new PerformanceObserver(list => {
            resolve(list.getEntries().some(entry => entry.sources.some(source => source.node && source.node.closest && source.node.closest('.tag-badges'))));
        }).observe({type: 'layout-shift', buffered: true});
        setTimeout(() => resolve(false), 1000);
    })""")
    assert shifted is False
    assert browser_page.locator(".tag-badges a").count() == 2
