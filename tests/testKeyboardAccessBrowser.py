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
import re
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
    """open_as(role, path, **context options) -> a Chromium page signed in as `role`, at
    `path`, once its queued scripts have run."""
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            def _open(role, path, init_script=None, before=None, **context_options):
                context = browser.new_context(**{"viewport": {"width": 1280, "height": 900}, **context_options})
                context.add_cookies([{
                    "name": app.config["SESSION_COOKIE_NAME"],
                    "value": session_cookie_for(app, make_user(role=role)),
                    "domain": "127.0.0.1",
                    "path": "/",
                }])
                page = context.new_page()
                page.set_default_timeout(SCRIPT_TIMEOUT_MS)
                if init_script:
                    page.add_init_script(init_script)
                if before:
                    # a test that holds a request back does not wait for the queue to run
                    before(page)
                    page.goto(live_server + path)
                    return page
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
    select = page.locator("select[aria-label^='Rows per page']").first
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


def test_rows_per_page_chosen_before_the_keyboard_script_is_in_loads_once_it_is(open_as):
    """The select's behaviour is keyboard.js's, queued after the first paint. A size chosen
    in the moment before it runs would otherwise be lost: the select would show 100 rows
    and the page would still hold 25."""
    held = []
    page = open_as("visitor", "/explore/functions", before=lambda page: page.route("**/static/keyboard.js*", lambda route: held.append(route)))
    page.wait_for_function("() => document.querySelector('select[data-pager-url]') !== null")
    while not held:
        page.wait_for_timeout(50)

    page.locator("select[aria-label^='Rows per page']").first.select_option("100")
    assert "limit=100" not in page.url
    with page.expect_navigation():
        held[0].continue_()

    assert "limit=100" in page.url


def test_rows_per_page_chosen_with_the_pointer_loads_at_once(open_as):
    page = open_as("visitor", "/explore/functions")

    with page.expect_navigation():
        page.locator("select[aria-label^='Rows per page']").first.select_option("100")

    assert "limit=100" in page.url


def test_rows_per_page_changed_with_the_keyboard_loads_when_focus_leaves(open_as):
    page = open_as("visitor", "/explore/functions")
    select = page.locator("select[aria-label^='Rows per page']").first

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
    wait_until_shown(page, "pills-delete-account-tab")
    page.keyboard.press("ArrowRight")
    wait_until_shown(page, "pills-info-tab")
    page.keyboard.press("ArrowLeft")
    wait_until_shown(page, "pills-delete-account-tab")
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


def picked(page, row_class="tr.parent"):
    """The ids of the rows a picker has tinted as chosen."""
    return page.eval_on_selector_all(f"{row_class}.row-pending th", "cells => cells.map(cell => cell.textContent.trim())")


def test_compare_picks_its_sample_with_the_arrow_keys(open_as):
    page = open_as("visitor", "/analyze/compare")
    ids = page.eval_on_selector_all("tr.parent_table_sample th.sample-id", "cells => cells.map(cell => cell.textContent.trim())")
    assert page.evaluate("selected") == ids[0], "the first row is chosen when nothing else is"

    tab_to(page, "tr.parent_table_sample input[type=radio]")
    assert page.evaluate("document.activeElement.value") == ids[0]
    page.keyboard.press("ArrowDown")

    assert page.evaluate("selected") == ids[1]
    assert picked(page, "tr.parent_table_sample") == [ids[1]]


def test_compare_versus_picks_a_sample_on_each_side_from_the_keyboard(open_as):
    page = open_as("visitor", "/analyze/compare_versus")
    ids_b = page.eval_on_selector_all("tr.parent_table_sample_b th.sample-id", "cells => cells.map(cell => cell.textContent.trim())")

    tab_to(page, "tr.parent_table_sample_a input[type=radio]")
    page.keyboard.press("Space")
    first_a = page.evaluate("document.activeElement.value")
    tab_to(page, "tr.parent_table_sample_b input[type=radio]")
    page.keyboard.press("Space")
    page.keyboard.press("ArrowDown")

    assert page.evaluate("[selected_a, selected_b]") == [first_a, ids_b[1]]
    assert page.is_visible("#button_match")


@pytest.mark.parametrize("path", ["/analyze/cross_compare?samples=0", "/analyze/unique_blocks?samples=0"])
def test_a_multi_picker_ticks_a_sample_on_and_off_from_the_keyboard(open_as, path):
    """A sample the URL already holds is ticked and disabled, so Tab passes it."""
    page = open_as("visitor", path)

    tab_to(page, "tr.parent input[type=checkbox]")
    box = page.evaluate("document.activeElement.value")
    assert box != "0", "Tab stopped on the sample the page already holds"
    page.keyboard.press("Space")
    assert page.evaluate("selected") == [box]
    assert picked(page) == [box]

    page.keyboard.press("Space")
    assert page.evaluate("selected") == []
    assert picked(page) == []


def test_a_chosen_row_is_framed_in_forced_colours(open_as):
    """Forced colours paint every row alike, the chosen one's tint included."""
    page = open_as("visitor", "/analyze/compare")
    page.emulate_media(forced_colors="active")

    styles = page.eval_on_selector_all("tr.parent_table_sample", "rows => rows.map(row => [row.classList.contains('row-pending'), getComputedStyle(row).outlineStyle])")

    assert [style for chosen, style in styles if chosen] == ["solid"]
    assert {style for chosen, style in styles if not chosen} == {"none"}


@pytest.mark.parametrize("path, link, target", [
    ("/explore/samples", "tr.sample-row th a", r"/explore/samples/\d+"),
    ("/explore/families", "tr.family-row th a", r"/explore/families/\d+"),
    ("/explore/functions", "tr.function-row th a", r"/explore/functions/\d+"),
    ("/data/jobs?active=getMatchesForSample", "tr.job-row th a", r"/data/jobs/\w+"),
    (f"/data/result/{job_id_of('cross_compare')}", "tr.sample-row th.id a", r"/data/result/\w+"),
])
def test_enter_on_a_row_s_id_opens_what_the_row_opens(open_as, live_server, path, link, target):
    page = open_as("contributor", path)

    tab_to(page, link)
    with page.expect_navigation():
        page.keyboard.press("Enter")

    assert re.fullmatch(live_server + target, page.url)


def test_a_click_elsewhere_on_a_row_still_opens_it(open_as, live_server):
    """The row's own handler stays, for a pointer, beside the link in its id cell."""
    page = open_as("visitor", "/explore/samples")
    row = page.locator("tr.sample-row").first
    sample_id = row.locator("th.sample-id").inner_text().strip()

    with page.expect_navigation():
        row.locator("td").nth(2).click()
    assert page.url == f"{live_server}/explore/samples/{sample_id}"


def column_order(page, table):
    return page.evaluate("table => [getElementsFromContainer(table + '_active'), getElementsFromContainer(table + '_available')]", table)


def test_a_column_moves_up_down_and_across_from_the_keyboard(open_as):
    """The column lists were ordered by dragging and nothing else; no key reached an item."""
    page = open_as("contributor", "/settings")
    page.click("#pills-column-settings-tab")
    page.wait_for_selector("#samples_table_active [data-reorder]", state="visible")
    active, available = column_order(page, "samples_table")
    first, second = active[:2]

    page.focus(f"#samples_table_active [data-column='{first}'] [data-reorder='down']")
    page.keyboard.press("Enter")
    assert column_order(page, "samples_table") == [[second, first] + active[2:], available]
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == f"Move {first} down"
    assert page.inner_text("#reorder-status") == f"{first} moved to position 2 of {len(active)} in Active."

    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Space")
    assert column_order(page, "samples_table")[0] == active
    page.keyboard.press("Space")
    assert page.inner_text("#reorder-status") == f"{first} is already first."

    across = f"#samples_table_active [data-column='{first}'] [data-reorder='across']"
    page.focus(across)
    page.keyboard.press("Enter")
    assert column_order(page, "samples_table") == [active[1:], available + [first]]
    moved = page.locator(f"#samples_table_available [data-column='{first}'] [data-reorder='across']")
    assert moved.evaluate("button => button === document.activeElement")
    assert moved.get_attribute("aria-label") == f"Move {first} to Active"
    assert page.inner_text("#reorder-status") == f"{first} moved to position {len(available) + 1} of {len(available) + 1} in Available."

    # and the order saved is the order on screen
    with page.expect_navigation():
        page.click("#saveButton")
    page.click("#pills-column-settings-tab")
    page.wait_for_selector("#samples_table_active [data-reorder]", state="visible")
    assert column_order(page, "samples_table") == [active[1:], available + [first]]


def test_a_cross_compare_sample_moves_by_touch(open_as, live_server):
    """jQuery UI's drag takes no touch, so a phone could not set an order of its own."""
    page = open_as("visitor", f"/data/result/{job_id_of('cross_compare')}", has_touch=True, is_mobile=True,
                   viewport={"width": 390, "height": 844})
    order = page.eval_on_selector_all("#sortable li", "items => items.map(item => item.dataset.sampleId)")

    page.tap(f"#sortable li[data-sample-id='{order[0]}'] [data-reorder='down']")
    assert page.eval_on_selector_all("#sortable li", "items => items.map(item => item.dataset.sampleId)") == [order[1], order[0]] + order[2:]
    assert page.inner_text("#reorder-status") == f"Sample {order[0]} moved to position 2 of {len(order)}."

    with page.expect_navigation():
        page.tap("text=Change order")
    assert page.url.endswith("?custom=" + ",".join([order[1], order[0]] + order[2:]))


COUNT_LAYOUT_SHIFTS = """
window.mcritShifts = [];
new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
        if (entry.hadRecentInput) { continue; }
        // the moved nodes that sit in a list the buttons are added to
        const inList = entry.sources.some(({node}) => node && (node.closest ? node : node.parentNode).closest('[data-reorder-item], #sortable, .list-group'));
        window.mcritShifts.push([entry.value, inList]);
    }
}).observe({type: "layout-shift", buffered: true});
"""

#: what the lists the script adds the buttons to shifted by; the token cell's copy button on
#: /settings moves a hair too, which is not the buttons' doing
SHIFTED_BY_THE_SCRIPT = """() => window.mcritShifts.filter(([, inList]) => inList).reduce((sum, [value]) => sum + value, 0)"""


#: the box of every list item and of its name
ITEM_BOXES = """() => Array.from(document.querySelectorAll('[data-reorder-item]'), (item) => {
    const box = item.getBoundingClientRect(), range = document.createRange();
    range.selectNodeContents(item.firstChild);
    const name = range.getBoundingClientRect();
    return [box.x, box.y, box.width, box.height, name.x, name.y, name.width, name.height];
})"""


#: the items whose name runs under their buttons, as "id: name right edge > buttons left edge"
NAMES_UNDER_BUTTONS = """() => Array.from(document.querySelectorAll('[data-reorder-item]')).filter((item) => {
    const range = document.createRange();
    range.selectNodeContents(item.firstChild);
    const right = Math.max(...Array.from(range.getClientRects(), (box) => box.right));
    return right > item.querySelector('.reorder-buttons').getBoundingClientRect().left;
}).map((item) => item.textContent)"""


@pytest.mark.parametrize("width", [1280, 390])
def test_the_move_buttons_are_added_by_the_script_and_move_nothing(open_as, width):
    """Written by the server into every item, the buttons took /settings from 42 KB to
    100 KB, and that page is sent uncompressed. The script makes them after the first
    paint into room the stylesheet keeps at the end of each item, so the items must not change size, nor must
    the page shift (a shift costs Lighthouse points, one in a hidden tab included)."""
    for path, role in [("/settings", "contributor"), (f"/data/result/{job_id_of('cross_compare')}", "visitor")]:
        page = open_as(role, path, init_script=COUNT_LAYOUT_SHIFTS, viewport={"width": width, "height": 900})
        page.wait_for_selector("[data-reorder-item] [data-reorder]", state="attached")
        page.evaluate("() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        # counted before the tab is opened, whose own reveal is not the buttons'
        assert page.evaluate(SHIFTED_BY_THE_SCRIPT) == 0, path
        if path == "/settings":
            page.click("#pills-column-settings-tab")
            page.wait_for_selector("#samples_table_active [data-reorder]", state="visible")
        items = page.locator("[data-reorder-item]")
        assert items.count() > 0
        assert all(items.nth(at).locator("[data-reorder]").count() == (3 if path == "/settings" else 2) for at in range(items.count())), path
        assert page.evaluate(NAMES_UNDER_BUTTONS) == [], path
        # what an item measures with its buttons, and again with the buttons removed
        full = page.evaluate(ITEM_BOXES)
        page.evaluate("() => document.querySelectorAll('.reorder-buttons').forEach((holder) => holder.remove())")
        empty = page.evaluate(ITEM_BOXES)
        assert full == empty, path


#: the pairs of move buttons whose boxes overlap, as "name / name"
OVERLAPPING_BUTTONS = """() => {
    const boxes = Array.from(document.querySelectorAll('[data-reorder]'), (button) => [button.getAttribute('aria-label'), button.getBoundingClientRect()]);
    const overlaps = [];
    boxes.forEach(([name, box], at) => boxes.slice(at + 1).forEach(([other, next]) => {
        if (box.left < next.right && next.left < box.right && box.top < next.bottom && next.top < box.bottom) { overlaps.push(name + ' / ' + other); }
    }));
    return overlaps;
}"""


@pytest.mark.parametrize("width", [1280, 390])
def test_the_move_buttons_of_one_item_keep_clear_of_the_next(open_as, width):
    """The buttons sit out of the flow, 24px tall, so the item no longer grows to hold
    them. The cross compare order's items are a line of small text, 21.6px, and each
    item's buttons ran 2.4px over the next one's: they covered each other, and
    Lighthouse's target-size audit failed the page. Every item is at least as tall as its
    buttons."""
    for path, role in [("/settings", "contributor"), (f"/data/result/{job_id_of('cross_compare')}", "visitor")]:
        page = open_as(role, path, viewport={"width": width, "height": 900})
        page.wait_for_selector("[data-reorder-item] [data-reorder]", state="attached")
        if path == "/settings":
            page.click("#pills-column-settings-tab")
            page.wait_for_selector("#samples_table_active [data-reorder]", state="visible")
        heights = page.evaluate("() => Array.from(document.querySelectorAll('[data-reorder-item]'), (item) => item.getBoundingClientRect().height).filter((height) => height > 0)")
        assert heights and min(heights) >= 24, (path, min(heights))
        assert page.evaluate(OVERLAPPING_BUTTONS) == [], path


FOCUSED_SCORE = """() => {
    const score = document.activeElement;
    return score.classList.contains('score-swatch') ? [score.closest('tr').querySelector('th.id').textContent.trim(),
        score.closest('table').rows[1].cells[score.parentNode.cellIndex].textContent.trim(), score.getAttribute('aria-label')] : null;
}"""


def test_the_matrix_is_read_and_followed_from_the_keyboard(open_as, live_server):
    """Its scores showed on hover alone and opened a pair's report from a click alone."""
    report = load("cross_compare.result")["unweighted"]
    order = report["clustered_sequence"]
    page = open_as("visitor", f"/data/result/{job_id_of('cross_compare')}")

    def score(row, col):
        return [order[row], order[col], "%.2f%% (%d matches)" % (report["matching_percent"][order[row]][order[col]], report["matching_matches"][order[row]][order[col]])]

    tab_to(page, "#pills-unweighted .score-swatch")
    assert page.evaluate(FOCUSED_SCORE) == score(0, 0)
    # the tooltip shows on focus as on hover, with the pair it is for
    page.wait_for_function("() => getComputedStyle(document.activeElement, '::after').visibility === 'visible'")
    assert "vs." in page.evaluate("() => document.activeElement.getAttribute('data-hint')")

    page.keyboard.press("ArrowRight")
    assert page.evaluate(FOCUSED_SCORE) == score(0, 1)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    assert page.evaluate(FOCUSED_SCORE) == score(2, 1)
    page.keyboard.press("End")
    assert page.evaluate(FOCUSED_SCORE) == score(2, len(order) - 1)
    page.keyboard.press("Home")
    assert page.evaluate(FOCUSED_SCORE) == score(2, 0)

    # one tab stop: Tab leaves the matrix, and Shift+Tab comes back to the score it left
    page.keyboard.press("Tab")
    assert page.evaluate(FOCUSED_SCORE) is None
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(FOCUSED_SCORE) == score(2, 0)

    page.keyboard.press("ArrowRight")
    with page.expect_navigation():
        page.keyboard.press("Enter")
    assert page.url.endswith(f"?samid={order[1]}")


def test_a_matrix_filled_on_its_tab_is_named_and_one_tab_stop_too(open_as):
    page = open_as("visitor", f"/data/result/{job_id_of('cross_compare')}")
    page.focus("#pills-unweighted-tab")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    wait_until_shown(page, "pills-score_weighted-tab")

    scores = page.eval_on_selector_all("#pills-score_weighted .score-swatch", """scores => scores.map(score =>
        [score.tabIndex, score.getAttribute('aria-label'), score.getAttribute('data-hint')])""")
    assert [tab_index for tab_index, _, _ in scores].count(0) == 1 and scores[0][0] == 0
    assert all(hint.startswith("MCRIT: " + label) for _, label, hint in scores)
    assert page.locator("#pills-score_weighted caption").count() == 1
