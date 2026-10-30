#!/usr/bin/python
"""The sample pickers mark a row's state with a class, driven in a browser. Issue #70.

compare, compare_versus, cross_compare and unique_blocks used to write `rgb(240, 240, 240)`
and `white` into a row's style attribute when it was clicked, and cross_compare read
`yellowgreen` back from it to tell a row the URL had already selected. The colour was the
state, so no theme could change it. The scripts toggle `row-pending` now, and cross
compare tests for `row-selected`; static/style.css paints both from the palette.

testRowDecoration.py pins what the pages render and what their scripts say. What a click
does only happens in a browser, so these tests click rows in Chromium and read the
classes back, and the style attribute: a deselected row must lose its class, not be
painted white.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing, as testJobOverviewBrowser.py does.
"""

import logging
import os
import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SCRIPT_TIMEOUT_MS = 20000

#: where a click lands: a cell with no link in it, which would navigate away
PLAIN_CELL = "td:not(:has(a))"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def live_server(app):
    """The app under test on a loopback port - see testJobOverviewBrowser.py."""
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
    """A Chromium page logged in as a visitor - see testJobOverviewBrowser.py."""
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


def first_ids(fake_mcrit, count=3):
    return sorted(sample_id for sample_id in fake_mcrit._samples if sample_id >= 0)[:count]


def row_with_id(page, sample_id, row_class="tr.parent"):
    return page.locator(row_class, has=page.locator(f"th:text-is('{sample_id}')"))


def classes(row):
    return set(row.get_attribute("class").split())


def click(row):
    row.locator(PLAIN_CELL).first.click()


def painted(row, token):
    """(the row's background, what `token` resolves to, its first cell's background).

    Bootstrap leaves a table cell's own background transparent, so a row's shows through
    it; if a later Bootstrap paints the cells, the row's tint is hidden and this is where
    the tests say so."""
    return row.evaluate("""(row, token) => {
        const probe = document.createElement('i');
        probe.style.backgroundColor = 'var(' + token + ')';
        document.body.appendChild(probe);
        const wanted = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return [getComputedStyle(row).backgroundColor, wanted,
                getComputedStyle(row.querySelector('th, td')).backgroundColor];
    }""", token)


def test_compare_moves_the_chosen_row_by_class(browser_page, live_server):
    page = browser_page
    page.goto(f"{live_server}/analyze/compare")
    rows = page.locator("tr.parent_table_sample")
    assert rows.count() >= 3

    # the page script runs from whenReady, which since #313 is after the first paint
    page.wait_for_selector("tr.parent_table_sample.row-pending", state="attached")
    assert "row-pending" in classes(rows.nth(0)), "the first row is chosen when nothing else is"
    click(rows.nth(2))

    assert "row-pending" in classes(rows.nth(2))
    assert "row-pending" not in classes(rows.nth(0)), "the row that was chosen kept its tint"
    assert page.locator("tr.row-pending").count() == 1
    assert page.locator("tr.parent_table_sample[style]").count() == 0, "a colour was written into a row"

    shown, wanted, cell = painted(rows.nth(2), "--row-pending")
    assert shown == wanted != "rgba(0, 0, 0, 0)", "the class paints nothing"
    assert cell == "rgba(0, 0, 0, 0)", "a cell covers the row's tint"
    assert painted(rows.nth(0), "--row-pending")[0] != wanted, "the row that lost the class is still painted"


def test_compare_versus_moves_the_chosen_row_of_each_table_by_class(browser_page, live_server):
    page = browser_page
    page.goto(f"{live_server}/analyze/compare_versus")
    rows_a = page.locator("tr.parent_table_sample_a")
    rows_b = page.locator("tr.parent_table_sample_b")

    click(rows_a.nth(0))
    click(rows_b.nth(1))
    click(rows_a.nth(2))
    click(rows_b.nth(0))

    assert "row-pending" in classes(rows_a.nth(2)) and "row-pending" not in classes(rows_a.nth(0))
    assert "row-pending" in classes(rows_b.nth(0)) and "row-pending" not in classes(rows_b.nth(1))
    assert page.locator("tr.row-pending").count() == 2, "choosing in one table moved the choice of the other"
    assert page.locator("tr[style]").count() == 0


def test_cross_compare_skips_a_selected_row_and_toggles_the_others(browser_page, live_server, fake_mcrit):
    page = browser_page
    selected, other, _ = first_ids(fake_mcrit)
    page.goto(f"{live_server}/analyze/cross_compare?samples={selected}")
    chosen, plain = row_with_id(page, selected), row_with_id(page, other)

    assert "row-selected" in classes(chosen)
    shown, wanted, cell = painted(chosen, "--row-selected")
    assert shown == wanted != "rgba(0, 0, 0, 0)", "the class paints nothing"
    assert cell == "rgba(0, 0, 0, 0)", "a cell covers the row's tint"
    click(chosen)
    assert "row-selected" in classes(chosen) and "row-pending" not in classes(chosen), "a selected row was toggled"
    assert page.evaluate("selected.length") == 0

    click(plain)
    assert "row-pending" in classes(plain)
    assert page.evaluate("selected.map(String)") == [str(other)]
    click(plain)
    assert not classes(plain) & {"row-pending", "row-selected"}
    assert plain.get_attribute("style") is None, "a deselected row was painted instead of cleared"
    assert page.evaluate("selected.length") == 0


def test_unique_blocks_skips_a_selected_row_and_toggles_the_others(browser_page, live_server, fake_mcrit):
    page = browser_page
    selected, other, _ = first_ids(fake_mcrit)
    page.goto(f"{live_server}/analyze/unique_blocks?samples={selected}")
    chosen, plain = row_with_id(page, selected), row_with_id(page, other)

    assert "row-selected" in classes(chosen)
    click(chosen)
    assert "row-selected" in classes(chosen) and "row-pending" not in classes(chosen)
    assert page.evaluate("selected.length") == 0

    click(plain)
    assert "row-pending" in classes(plain)
    assert page.evaluate("selected.map(String)") == [str(other)]
    click(plain)
    assert not classes(plain) & {"row-pending", "row-selected"}
    assert plain.get_attribute("style") is None
    assert page.evaluate("selected.length") == 0
