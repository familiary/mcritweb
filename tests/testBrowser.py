#!/usr/bin/python
"""The parts of the unique blocks page that only a browser can answer.

The rest of this suite renders a template and reads the HTML back, which settles
markup and nothing else. Both halves of issue #80 are script behaviour: the copy
icon has to put on the clipboard what the *textarea* currently holds - the old
implementation copied the rendered markup instead, HTML-escaped and blind to every
edit - and a statistics header has to reorder the rows numerically when clicked. A
page can carry every attribute a lint would look for and still do neither.

The harness is the offline one the rest of the suite uses - `CorpusMcritClient`
behind MCRIT_CLIENT_FACTORY, no backend and no network - served on a loopback port
so Chromium can load it.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing. The markup lints in testResultPages.py are
what CI keeps; these are the tests that exercise the behaviour.
"""

import logging
import threading
import time

import pytest
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The page's own timeout, for a load and for anything a script does after a click.
#: Generous, because it is only ever paid in full when a test is about to fail.
SCRIPT_TIMEOUT_MS = 10000

#: Typed into the rule before copying. Both halves of it matter: the text differs
#: from what the page shipped, which the old helper could not notice, and `&` `<`
#: `>` are exactly the characters an `.innerHTML` read hands back as entities.
EDITED_RULE = 'rule edited { condition: 1 < 2 and 3 > 2 /* R&D */ }'

#: Columns of the statistics table, 1-based as :nth-child() counts them.
VERSION_COLUMN = 2
UNIQUE_BLOCKS_COLUMN = 5

#: The Unique Blocks column of the captured report, in the order the report arrives
#: in. Written out as rendered rather than as the raw counts, so what the test reads
#: is what a reader sees - a test resolving data-sort-value for itself would be
#: asserting the implementation back at itself.
UNIQUE_BLOCKS_CELLS = ["611 (14.34%)", "597 (14.90%)", "2643 (50.53%)"]

#: The Version column, same rows, same order.
VERSION_CELLS = ["1.3.5.1", "1.3.4.0", "0.0.1.1"]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.fixture
def live_server(app):
    """The app under test on a loopback port, for the seconds a test needs it.

    `client` is a WSGI stub with no socket behind it, so a browser cannot reach it.
    Port 0 lets the OS pick, so concurrent runs do not collide.
    """
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
    """A Chromium page already logged in as a visitor, on `live_server`.

    The session cookie is signed with the app's own session interface rather than
    driven through the login form: this module is about what the result page's
    scripts do, and going through the form would make every test here fail for a
    change to the login markup instead.
    """
    user_id = make_user(role="visitor")
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})

    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            # 127.0.0.1 is a secure context, so navigator.clipboard is the path the
            # copy helper takes here; the permissions keep it from prompting.
            context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
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
def alerts(browser_page):
    """Every alert() the page raised, dismissed as it arrives.

    Playwright only dismisses dialogs by itself while nothing is listening, and a
    listener that does not dismiss deadlocks the click that opened it - alert()
    blocks the page until it is answered.
    """
    messages = []

    def dismiss(dialog):
        messages.append(dialog.message)
        dialog.dismiss()

    browser_page.on("dialog", dismiss)
    return messages


def open_unique_blocks(page, live_server, tab="stats"):
    page.goto(f"{live_server}/data/result/{job_id_of('unique_blocks')}?tab={tab}")
    assert page.locator("h1").first.inner_text() == "Unique Block Isolation Report"
    return page


def click_and_wait_for_alert(page, selector, alerts):
    """Click, and hand back what the copy helper reported when it was done.

    The helper alerts on both outcomes, and on the secure-context path it does so
    from a promise callback - so the alert is also the signal that the copy has
    finished and the clipboard can be read.
    """
    already_seen = len(alerts)
    page.click(selector)
    deadline = time.monotonic() + SCRIPT_TIMEOUT_MS / 1000
    while len(alerts) == already_seen:
        assert time.monotonic() < deadline, "the copy helper never reported an outcome"
        page.wait_for_timeout(25)
    return alerts[-1]


def test_the_copy_icon_puts_the_textareas_current_value_on_the_clipboard(browser_page, live_server, alerts):
    """Issue #80. The rule is editable, so the clipboard has to get the value, not
    the markup: the old helper copied `$(element).html()` out of a detached
    textarea, which threw away every edit and handed back HTML entities."""
    page = open_unique_blocks(browser_page, live_server, tab="yara")
    page.fill("#yara_text", EDITED_RULE)

    message = click_and_wait_for_alert(page, "#pills-yara i.fa-copy", alerts)

    assert "Copied" in message, f"the copy reported failure: {message}"
    assert page.evaluate("navigator.clipboard.readText()") == EDITED_RULE


def statistics_column(page, column):
    """One column of the statistics table, top to bottom, as it reads on screen."""
    return page.eval_on_selector_all(
        f"table.sortable-table tbody tr td:nth-child({column})",
        "cells => cells.map(cell => cell.textContent.trim())",
    )


def statistics_header(page, column):
    return page.locator("table.sortable-table thead th").nth(column - 1)


def test_clicking_a_numeric_header_sorts_the_rows_by_value(browser_page, live_server):
    """Unique Blocks is the column worth driving: it is formatted, reading
    "611 (14.34%)", so the order it comes out in says whether data-sort-value is
    what got compared. Without it the numeric comparator sees NaN in every row and
    leaves the table exactly as it was."""
    page = open_unique_blocks(browser_page, live_server)
    header = statistics_header(page, UNIQUE_BLOCKS_COLUMN)
    assert header.inner_text().startswith("Unique Blocks")
    assert statistics_column(page, UNIQUE_BLOCKS_COLUMN) == UNIQUE_BLOCKS_CELLS, "the report did not arrive in its captured order"

    header.click()
    descending = [UNIQUE_BLOCKS_CELLS[row] for row in (2, 0, 1)]
    assert statistics_column(page, UNIQUE_BLOCKS_COLUMN) == descending
    assert header.locator("i").get_attribute("class") == "fa fa-sort-down"

    header.click()
    assert statistics_column(page, UNIQUE_BLOCKS_COLUMN) == descending[::-1]
    assert header.locator("i").get_attribute("class") == "fa fa-sort-up"


def test_clicking_a_text_header_sorts_the_rows_by_version(browser_page, live_server):
    """Version carries no data-sort-value - the cell text is the value - and sorts as
    text. The captured report already arrives newest-first, so the assertion that
    tells sorting from doing nothing is the ascending one."""
    page = open_unique_blocks(browser_page, live_server)
    header = statistics_header(page, VERSION_COLUMN)
    assert header.inner_text().startswith("Version")
    assert statistics_column(page, VERSION_COLUMN) == VERSION_CELLS, "the report did not arrive in its captured order"

    header.click()
    header.click()
    assert statistics_column(page, VERSION_COLUMN) == VERSION_CELLS[::-1]
