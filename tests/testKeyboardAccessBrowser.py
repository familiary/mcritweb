#!/usr/bin/python
"""Keyboard access, driven in a browser: Tab, Enter, Space and the arrow keys.

testKeyboardAccess.py reads the markup for the shapes that kept a keyboard out. Whether a
key then does what it should only shows once a browser runs the page and its scripts, so
these tests press the keys in Chromium and read back focus, URLs and state.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing.
"""

import copy
import os
import threading

import pytest
from conftest import session_cookie_for
from fixtureData import job_id_of, load
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


@pytest.mark.parametrize("path", [
    # a cursor pagination, an offset pagination with the other tables' state to carry, and
    # the search page, which did not define the helper the select calls
    "/explore/functions",
    "/analyze/compare",
    "/explore/search?query=a&type=function",
])
def test_rows_per_page_waits_for_enter_when_changed_with_the_keyboard(open_as, path):
    """A select changes its value at every arrow key, and the page loaded at the first
    one: a keyboard user could not step from 25 to 100 without loading the page at 50."""
    page = open_as("visitor", path)
    select = page.locator("select[aria-label='Rows per page']").first
    start = page.url

    select.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(500)
    assert page.url == start, "the page loaded before the choice was made"

    chosen = select.input_value()
    with page.expect_navigation():
        page.keyboard.press("Enter")
    assert f"={chosen}" in page.url


def test_rows_per_page_chosen_with_the_pointer_loads_at_once(open_as):
    page = open_as("visitor", "/explore/functions")

    with page.expect_navigation():
        page.locator("select[aria-label='Rows per page']").first.select_option("100")

    assert "limit=100" in page.url


def test_rows_per_page_changed_with_the_keyboard_loads_when_focus_leaves(open_as):
    page = open_as("visitor", "/explore/functions")
    select = page.locator("select[aria-label='Rows per page']").first

    select.focus()
    page.keyboard.press("ArrowUp")
    chosen = select.input_value()
    with page.expect_navigation():
        page.keyboard.press("Tab")

    assert f"limit={chosen}" in page.url


def close_with_escape(page, dialog):
    """Escape reaches a dialog once its opening has finished, which is when Bootstrap moves
    focus into it."""
    page.wait_for_function(f"() => document.querySelector('{dialog}').contains(document.activeElement)")
    page.keyboard.press("Escape")
    page.wait_for_function(f"() => !document.querySelector('{dialog}').classList.contains('show') && !document.body.classList.contains('modal-open')")


def test_focus_comes_back_to_the_delete_button_when_its_dialog_closes(open_as):
    """The jobs page opens its delete dialog from a script, and Bootstrap hands focus
    back only to an opener of its own, so Escape left focus on <body>."""
    page = open_as("contributor", "/data/jobs?active=getMatchesForSample")
    opener = page.locator(".confirm-delete").first
    opener_id = opener.get_attribute("id")

    opener.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#jobDeleteModal.show")
    close_with_escape(page, "#jobDeleteModal")

    assert focused(page) == opener_id


def test_a_dialog_opened_from_a_menu_starts_in_its_field_and_ends_on_the_menu(open_as):
    """Bootstrap focuses the dialog's box, so the compare dialog's `autofocus` field never
    had the cursor; and the menu entry that opened it is hidden by the time it closes, so
    focus fell to <body>. The menu's toggle is where it comes back to."""
    page = open_as("visitor", "/explore/functions/0")

    page.focus("#button_function_analyze")
    page.keyboard.press("Enter")
    page.wait_for_selector("#button_function_analyze + .dropdown-menu.show")
    page.locator("[data-bs-target='#compareFunctionModal']").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#compareFunctionModal.show")
    page.wait_for_function("() => document.activeElement.id === 'other_function_id'")
    close_with_escape(page, "#compareFunctionModal")

    assert focused(page) == "button_function_analyze"


def test_the_rename_dialog_starts_in_its_field(open_as):
    page = open_as("contributor", "/explore/functions/0")

    page.focus("#button_function_rename")
    page.keyboard.press("Enter")
    page.wait_for_selector("#renameFunctionModal.show")

    page.wait_for_function("() => document.activeElement.id === 'function_name'")
    close_with_escape(page, "#renameFunctionModal")
    assert focused(page) == "button_function_rename"


def tab_list_state(page, tablist="#pills-tab"):
    """(the focused tab, the selected tab, the tabs Tab stops on) of a tab list."""
    return page.evaluate("""list => {
        const tabs = [...document.querySelector(list).querySelectorAll('[role=tab]')];
        return [document.activeElement.id,
                tabs.filter(tab => tab.getAttribute('aria-selected') === 'true').map(tab => tab.id),
                tabs.filter(tab => tab.tabIndex >= 0).map(tab => tab.id)];
    }""", tablist)


def wait_until_shown(page, tab_id):
    page.wait_for_function("""id => {
        const tab = document.getElementById(id);
        const pane = document.querySelector(tab.getAttribute('data-bs-target'));
        return tab.tabIndex === 0 && pane.classList.contains('active') && pane.classList.contains('show');
    }""", arg=tab_id)


def test_the_arrow_keys_move_between_tabs_and_show_them(open_as):
    """Bootstrap 5.0.2 gives a tab list no keys: every tab was a tab stop, and the arrow
    keys did nothing."""
    page = open_as("contributor", "/settings")
    assert tab_list_state(page)[1:] == [["pills-info-tab"], ["pills-info-tab"]]

    page.focus("#pills-info-tab")
    page.keyboard.press("ArrowRight")
    wait_until_shown(page, "pills-username-tab")
    assert tab_list_state(page) == ["pills-username-tab", ["pills-username-tab"], ["pills-username-tab"]]

    page.keyboard.press("End")
    wait_until_shown(page, "pills-theme-tab")
    page.keyboard.press("ArrowRight")
    wait_until_shown(page, "pills-info-tab")
    page.keyboard.press("ArrowLeft")
    wait_until_shown(page, "pills-theme-tab")
    page.keyboard.press("Home")
    wait_until_shown(page, "pills-info-tab")
    assert tab_list_state(page) == ["pills-info-tab", ["pills-info-tab"], ["pills-info-tab"]]


def test_the_arrow_keys_pass_a_disabled_tab(open_as, fake_mcrit, monkeypatch):
    report = copy.deepcopy(load("unique_blocks.result"))
    report["statistics"]["has_yara_rule"] = False
    monkeypatch.setattr(fake_mcrit, "getResultForJob", lambda *args, **kwargs: report)
    page = open_as("visitor", f"/data/result/{job_id_of('unique_blocks')}?tab=blocks")
    assert tab_list_state(page)[1:] == [["pills-blocks-tab"], ["pills-blocks-tab"]]

    page.focus("#pills-blocks-tab")
    page.keyboard.press("ArrowRight")

    wait_until_shown(page, "pills-statistics-tab")
    assert tab_list_state(page)[0] == "pills-statistics-tab"


def test_a_cross_compare_tab_is_shown_by_enter_not_by_reaching_it(open_as):
    """Each of its tabs builds a matrix the first time it is shown, so the arrow keys
    only move between them."""
    page = open_as("visitor", f"/data/result/{job_id_of('cross_compare')}")
    first, second = page.eval_on_selector_all("#pills-tab [role=tab]", "tabs => tabs.slice(0, 2).map(tab => tab.id)")

    page.focus(f"#{first}")
    page.keyboard.press("ArrowRight")
    assert tab_list_state(page) == [second, [first], [first]]
    assert page.locator("[data-lazy-method]").count() == 5, "a pane was built by moving to its tab"

    page.keyboard.press("Enter")
    wait_until_shown(page, second)
    assert tab_list_state(page) == [second, [second], [second]]
