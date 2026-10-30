#!/usr/bin/python
"""Search as you type, driven in a browser.

`static/live_search.js` fetches the page a search form would load as its term is typed,
and swaps in that page's table. These tests type into the sample and family lists of
the captured corpus and check that the table follows without a page load, that what is
swapped in still works (row clicks, the edit modal), and that a failed fetch falls back
to loading the page.

The harness is the offline one of tests/testFunctionVsBrowser.py: `CorpusMcritClient`
behind the app, served on a loopback port. `playwright` is not a dependency of this
project and CI does not install it, so this module skips there rather than failing.
"""

import os
import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

TIMEOUT_MS = 20000


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
    """A Chromium page logged in as an admin, who sees the edit buttons."""
    user_id = make_user(role="admin")
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
            yield page
        finally:
            browser.close()


#: resolves once base.html's queue has run, live_search.js among it (ADR-0021)
QUEUE_HAS_RUN = "() => new Promise(resolve => whenReady(() => resolve(true)))"


def open_page(page, url):
    """Load `url` and mark the document, so a later page load shows as the mark gone."""
    page.goto(url)
    page.evaluate(QUEUE_HAS_RUN)
    page.evaluate("window.loadedOnce = true")


def type_term(page, term):
    """Type `term` into the search box and wait for the table to follow it."""
    page.locator("#query").press_sequentially(term, delay=20)
    page.wait_for_function("term => new URL(location.href).searchParams.get('query') === term", arg=term)
    page.wait_for_function("() => !document.querySelector('[aria-busy]')")


def row_ids(page, row):
    return page.locator(f"tr.{row} th").all_inner_texts()


def test_the_sample_list_follows_the_typed_term_without_a_page_load(page, live_server):
    open_page(page, f"{live_server}/explore/samples")
    everything = row_ids(page, "sample-row")

    type_term(page, "dridex")

    narrowed = row_ids(page, "sample-row")
    assert narrowed and len(narrowed) < len(everything)
    assert all("dridex" in text for text in page.locator("tr.sample-row").all_inner_texts())
    assert page.evaluate("window.loadedOnce") is True
    assert page.locator("#sample-list-status").inner_text() == f"{len(narrowed)} rows on this page"


def test_a_term_typed_before_the_script_has_run_is_searched_once_it_has(page, live_server):
    """The box has focus from the start, and base.html's queue runs live_search.js only
    after the first paint, so typing can come first."""
    held = []
    page.route("**/static/live_search.js", lambda route: held.append(route))
    page.goto(f"{live_server}/explore/samples", wait_until="commit")
    page.wait_for_selector("#query")
    everything = row_ids(page, "sample-row")
    page.locator("#query").press_sequentially("dridex", delay=20)
    for _ in range(200):
        if held:
            break
        page.wait_for_timeout(50)
    page.evaluate("window.loadedOnce = true")
    held[0].continue_()

    page.wait_for_function("term => new URL(location.href).searchParams.get('query') === term", arg="dridex")
    page.wait_for_function("() => !document.querySelector('[aria-busy]')")
    narrowed = row_ids(page, "sample-row")
    assert narrowed and len(narrowed) < len(everything)
    assert page.evaluate("window.loadedOnce") is True


def test_the_rows_a_search_swaps_in_mark_the_term(page, live_server):
    """The marks come with the page the search fetches, inside the region it swaps."""
    open_page(page, f"{live_server}/explore/samples")
    assert page.locator("#sample-list mark").count() == 0

    type_term(page, "dridex")

    marks = page.locator("#sample-list tr.sample-row mark").all_inner_texts()
    assert marks and all(text.lower() == "dridex" for text in marks)
    assert page.evaluate("window.loadedOnce") is True


def test_an_answer_for_a_term_no_longer_typed_is_not_shown(page, live_server):
    """Typed, then put back to what is on screen while the answer was on its way."""
    open_page(page, f"{live_server}/explore/samples")
    everything = row_ids(page, "sample-row")
    held = []
    page.route("**/explore/samples?*query=dridex*", lambda route: held.append(route))
    page.locator("#query").press_sequentially("dridex", delay=20)
    page.wait_for_function("() => document.querySelector('[aria-busy]')")

    page.locator("#query").fill("")
    page.wait_for_function("() => !document.querySelector('[aria-busy]')")
    held[0].continue_()
    page.wait_for_timeout(500)

    assert row_ids(page, "sample-row") == everything
    assert "dridex" not in page.url


def test_a_row_swapped_in_still_opens_its_sample(page, live_server):
    open_page(page, f"{live_server}/explore/samples")
    type_term(page, "dridex")
    first = row_ids(page, "sample-row")[0]

    with page.expect_navigation():
        page.locator("tr.sample-row th.sample-id").first.click()

    assert page.url == f"{live_server}/explore/samples/{first}"


@pytest.mark.parametrize("listing, row, modal, kind, name_field", [
    ("families", "family-row", "editFamilyModal", "Family", "family_new_name"),
    ("samples", "sample-row", "editSampleModal", "Sample", "sample_family_name"),
])
def test_the_edit_modal_still_opens_for_a_row_swapped_in(page, live_server, listing, row, modal, kind, name_field):
    open_page(page, f"{live_server}/explore/{listing}")
    type_term(page, "zeus")
    row_id = row_ids(page, row)[0]

    page.locator(f"tr.{row}", has=page.locator(f"th > a:text-is('{row_id}')")).locator(f"button[data-bs-target='#{modal}']").click()

    page.wait_for_selector(f"#{modal}.show")
    assert page.locator(f"#{modal} .modal-title").inner_text() == f"Change Data for {kind} {row_id}"
    assert "zeus" in page.locator(f"#{name_field}").input_value()


def test_a_failed_fetch_loads_the_page_instead(page, live_server):
    """Here the session is gone, so the fetch answers with the login page, which has no
    table to take; the page the form would load is loaded, and says what happened."""
    open_page(page, f"{live_server}/explore/families")
    page.context.clear_cookies()

    with page.expect_navigation():
        page.locator("#query").press_sequentially("zeus", delay=20)

    assert "/login" in page.url


def test_enter_still_loads_the_whole_page(page, live_server):
    open_page(page, f"{live_server}/explore/families")
    page.locator("#query").fill("zeus")

    with page.expect_navigation():
        page.locator("#query").press("Enter")

    assert page.evaluate("window.loadedOnce") is None
    assert row_ids(page, "family-row")


def test_an_edit_modal_a_search_brings_in_works_like_one_loaded_with_the_page(page, live_server):
    """A list that loaded with no rows has no edit modal; the first search that finds
    rows brings one in, after the page's listeners, the type-ahead and the modal's own
    script would have been set up."""
    open_page(page, f"{live_server}/explore/families?query=nothing-matches-this")
    assert page.locator("#editFamilyModal").count() == 0
    page.locator("#query").fill("")
    type_term(page, "zeus")
    family_id = row_ids(page, "family-row")[0]

    page.locator("tr.family-row", has=page.locator(f"th > a:text-is('{family_id}')")).locator("button[data-bs-target='#editFamilyModal']").click()
    page.wait_for_selector("#editFamilyModal.show")
    page.locator("#family_new_name").focus()

    assert page.locator("#editFamilyModal .modal-title").inner_text() == f"Change Data for Family {family_id}"
    assert page.locator("#family_new_name").get_attribute("data-family-autocomplete") == "attached"
    assert page.evaluate("typeof toggle_delete_family") == "function"


def test_a_chosen_page_size_is_kept(page, live_server):
    """The pagination reads a page size only along with a page number."""
    open_page(page, f"{live_server}/explore/samples?limit=10&page=1")
    assert len(row_ids(page, "sample-row")) == 10

    type_term(page, "_")

    assert len(row_ids(page, "sample-row")) == 10
    assert "limit=10" in page.url


def test_an_edit_modal_open_when_the_answer_comes_is_left_alone(page, live_server):
    """The modal lives in the swapped region; the table follows once it is closed."""
    open_page(page, f"{live_server}/explore/families")
    everything = row_ids(page, "family-row")
    held = []
    # the first answer is held; the search after the modal closes goes through
    page.route("**/explore/families?*query=zeus*",
               lambda route: route.continue_() if held else held.append(route))
    page.locator("#query").press_sequentially("zeus", delay=20)
    page.wait_for_function("() => document.querySelector('[aria-busy]')")
    page.locator("tr.family-row button[data-bs-target='#editFamilyModal']").first.click()
    page.wait_for_selector("#editFamilyModal.show")

    held[0].continue_()
    page.wait_for_function("() => !document.querySelector('[aria-busy]')")
    assert page.locator("#editFamilyModal.show").count() == 1
    assert row_ids(page, "family-row") == everything

    page.wait_for_timeout(500)  # Bootstrap ignores hide() while the modal is still fading in
    page.evaluate("bootstrap.Modal.getInstance(document.getElementById('editFamilyModal')).hide()")
    page.wait_for_function("term => new URL(location.href).searchParams.get('query') === term", arg="zeus")
    page.wait_for_function("() => !document.querySelector('[aria-busy]')")
    assert page.locator(".modal-backdrop").count() == 0
    assert len(row_ids(page, "family-row")) < len(everything)
