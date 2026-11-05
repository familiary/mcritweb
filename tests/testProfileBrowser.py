#!/usr/bin/python
"""The profile page in a browser: what its scripts do, and how wide it is.

static/profile.js moves between the tabs with the arrow keys, Home and End, opens the pane
a URL hash names and writes the shown pane into the hash; static/show_apitoken.js fetches
the API token into the page on a click, and the snippet buttons copy the examples. None of
that is visible to a test that reads the HTML as text, nor is the width of the page: the
developer tab was 603px wide on a 390px phone (a 64-character token with no break
opportunity and a min-width), while the HTML tests passed.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import os
import threading

import pytest
from werkzeug.serving import make_server

from mcritweb.db import remember_query_filename
from mcritweb.views.utility import query_upload_path

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

WIDTHS = [390, 820, 1280, 1920]
TABS = ["overview", "jobs", "submissions", "uploads", "developer"]
TOKEN = "apitoken-contributor"


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
def populated(app, make_user, fake_mcrit):
    """A contributor with the captured queue's jobs, and one query upload; its cookie."""
    user_id = make_user(role="contributor", username="analyst1")
    fake_mcrit.record_requester("analyst1")
    with app.app_context():
        remember_query_filename("fedcba9876543210fedcba98", "query.exe", user_id)
        path = query_upload_path(app, "fedcba9876543210fedcba98")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as upload:
            upload.write(b"MZquery")
    return app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})


@pytest.fixture
def open_profile(app, live_server, populated):
    def _open(use, width=1280, path="/profile", clipboard=False):
        with sync_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except sync_api.Error as error:
                pytest.skip(f"no Chromium for playwright to drive: {error}")
            try:
                context = browser.new_context(viewport={"width": width, "height": 900})
                if clipboard:
                    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_server)
                context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": populated, "domain": "127.0.0.1", "path": "/"}])
                page = context.new_page()
                page.goto(live_server + path)
                page.wait_for_function("() => window.bootstrap !== undefined")
                return use(page)
            finally:
                browser.close()

    return _open


def shown_pane(page):
    return page.evaluate("() => document.querySelector('.tab-pane.show.active').id.replace('pane-', '')")


@pytest.mark.parametrize("width", WIDTHS)
def test_no_tab_scrolls_the_page_sideways(open_profile, width):
    def use(page):
        widths = {}
        for tab in TABS:
            page.click(f"#tab-{tab}")
            page.wait_for_selector(f"#pane-{tab}.show.active")
            if tab == "developer":
                page.click("[data-show-apitoken]")
                page.wait_for_selector("#apitoken-copy:not([hidden])")
            widths[tab] = page.evaluate("() => document.documentElement.scrollWidth")
        return widths

    widths = open_profile(use, width=width)
    assert all(scroll_width == width for scroll_width in widths.values()), f"at {width}px the page is {widths} wide"


@pytest.mark.parametrize("width", WIDTHS)
def test_the_capability_table_is_read_without_swiping(open_profile, width):
    """At 390px the three-column table scrolled inside its box, so the descriptions could
    only be read after a sideways swipe within the table. On a phone the description now
    sits under the capability's name; from 576px up it is the third column. Either way
    the table fits its box and every description is on screen."""
    def use(page):
        page.click("#tab-overview")
        page.wait_for_selector("#pane-overview.show.active")
        return page.evaluate("""() => {
            const box = document.querySelector('.capability-table').closest('.table-responsive');
            const rows = Array.from(document.querySelectorAll('.capability-table tbody tr')).map(row => {
                const visible = Array.from(row.querySelectorAll('th, td, th > div'))
                    .filter(cell => cell.offsetParent !== null)
                    .map(cell => cell.textContent.trim()).join(' ');
                const description = row.querySelector('td:last-child').textContent.trim();
                return {description, visible};
            });
            return {overflow: box.scrollWidth - box.clientWidth, rows};
        }""")

    seen = open_profile(use, width=width)
    assert seen["overflow"] == 0, f"at {width}px the capability table is {seen['overflow']}px wider than its box"
    assert len(seen["rows"]) == 7
    for row in seen["rows"]:
        assert row["description"] and row["description"] in row["visible"], f"at {width}px the description is not on screen: {row}"


def test_the_arrow_keys_move_between_the_tabs_and_the_hash_follows(open_profile):
    def use(page):
        page.focus("#tab-overview")
        page.keyboard.press("ArrowRight")
        after_right = (page.evaluate("() => document.activeElement.id"), shown_pane(page), page.evaluate("() => location.hash"))
        page.keyboard.press("End")
        after_end = (page.evaluate("() => document.activeElement.id"), shown_pane(page), page.evaluate("() => location.hash"))
        page.keyboard.press("ArrowRight")
        wrapped = (page.evaluate("() => document.activeElement.id"), shown_pane(page))
        page.keyboard.press("Home")
        tabindexes = page.evaluate("() => Array.from(document.querySelectorAll('#profileTabList [role=tab]')).map(t => t.getAttribute('tabindex') + t.getAttribute('aria-selected'))")
        return after_right, after_end, wrapped, tabindexes

    after_right, after_end, wrapped, tabindexes = open_profile(use)
    assert after_right == ("tab-jobs", "jobs", "#jobs")
    assert after_end == ("tab-developer", "developer", "#developer")
    assert wrapped == ("tab-overview", "overview")
    assert tabindexes == ["0true", "-1false", "-1false", "-1false", "-1false"]


def test_a_hash_opens_its_pane_and_a_click_writes_the_hash(open_profile):
    def use(page):
        opened = (shown_pane(page), page.evaluate("() => document.querySelector('#tab-developer').getAttribute('aria-selected')"))
        page.click("#tab-uploads")
        page.wait_for_selector("#pane-uploads.show.active")
        return opened, page.evaluate("() => location.hash")

    opened, hash_after_click = open_profile(use, path="/profile#developer")
    assert opened == ("developer", "true")
    assert hash_after_click == "#uploads"


def test_the_token_arrives_on_a_click_and_not_before(open_profile):
    def use(page):
        page.click("#tab-developer")
        before = page.content()
        page.click("[data-show-apitoken]")
        page.wait_for_selector("#apitoken-copy:not([hidden])")
        shown = page.inner_text("#apitoken-value")
        slots = page.evaluate("() => Array.from(document.querySelectorAll('[data-apitoken-slot]')).map(s => s.textContent)")
        copy_value = page.get_attribute("#apitoken-copy .btn-clipboard", "data-clipboard-value")
        return before, shown, slots, copy_value

    before, shown, slots, copy_value = open_profile(use)
    assert TOKEN not in before
    assert shown == TOKEN and slots == [TOKEN, TOKEN] and copy_value == TOKEN


def test_the_copy_button_copies_the_snippet_as_shown(open_profile):
    def use(page):
        page.click("#tab-developer")
        page.click("[data-show-apitoken]")
        page.wait_for_selector("#apitoken-copy:not([hidden])")
        page.click("[data-copy-snippet='snippetCurl']")
        page.wait_for_function("() => document.querySelector(\"[data-copy-snippet='snippetCurl']\").textContent === 'Copied'")
        return page.evaluate("() => navigator.clipboard.readText()")

    copied = open_profile(use, clipboard=True)
    assert copied.startswith('curl -H "apitoken: ' + TOKEN + '"') and "/api/status" in copied
