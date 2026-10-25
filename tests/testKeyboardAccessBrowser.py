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


@pytest.mark.parametrize("path, toggle", [
    ("/explore/samples", "#navbarDropdownExplore"),
    ("/explore/samples", "#navbarDropdownUser"),
    ("/data/jobs?active=getMatchesForSample", "[id^=navbarDropdown-]"),
])
def test_space_opens_a_menu(open_as, path, toggle):
    """A button toggle, where a link to "#" took Enter only."""
    page = open_as("contributor", path)

    page.locator(toggle).first.focus()
    page.keyboard.press("Space")

    assert page.locator(toggle).first.get_attribute("aria-expanded") == "true"
    assert page.locator(f"{toggle} + .dropdown-menu.show").count() == 1


def test_a_column_sorts_from_the_keyboard(open_as):
    page = open_as("visitor", "/explore/samples")
    assert page.get_attribute("thead th:has(a.table-sort) >> nth=0", "aria-sort") == "ascending"

    tab_to(page, "thead th a.table-sort")
    with page.expect_navigation():
        page.keyboard.press("Enter")

    assert "ascending=False" in page.url
    assert page.get_attribute("thead th:has(a.table-sort) >> nth=0", "aria-sort") == "descending"


def test_the_unique_blocks_statistics_sort_from_the_keyboard(open_as):
    """They sort in the page, and their headers took a click and nothing else."""
    page = open_as("visitor", f"/data/result/{job_id_of('unique_blocks')}")
    header = page.locator("table.sortable-table thead th").nth(4)
    assert header.inner_text().startswith("Unique Blocks")
    cells = "table.sortable-table tbody tr td:nth-child(5)"
    before = page.eval_on_selector_all(cells, "cells => cells.map(cell => Number(cell.dataset.sortValue))")

    tab_to(page, "table.sortable-table thead th:nth-child(5) button")
    page.keyboard.press("Enter")
    assert header.get_attribute("aria-sort") == "descending"
    assert page.eval_on_selector_all(cells, "cells => cells.map(cell => Number(cell.dataset.sortValue))") == sorted(before, reverse=True)

    page.keyboard.press("Space")
    assert header.get_attribute("aria-sort") == "ascending"
    assert page.eval_on_selector_all(cells, "cells => cells.map(cell => Number(cell.dataset.sortValue))") == sorted(before)
    assert page.locator("table.sortable-table thead th[aria-sort]").count() == 1
