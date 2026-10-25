#!/usr/bin/python
"""What a copy says when it is done, in a browser: the status message of
static/status_message.js, and the copies that report through it.

The clipboard buttons confirmed a copy with a "Copied!" tooltip, which a screen reader is
not told about; the registration URL was copied by an icon no keyboard could reach and
confirmed with alert(), which takes the focus; and both called `navigator.clipboard`,
which a page served over plain HTTP does not have. testStatusMessages.py lints the markup
for CI; this module makes the copies.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

from mcritweb.db import ServerInfo

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

#: The page's own timeout, generous because it is only paid in full by a failing test.
SCRIPT_TIMEOUT_MS = 10000

#: The registration token the admin page offers to copy, in a URL.
REGISTRATION_TOKEN = "a.token-1"

#: A page served over plain HTTP, as far as the copy can tell: no `navigator.clipboard` and
#: no secure context. The clipboard itself stays reachable to the test as mcritTestClipboard.
PLAIN_HTTP = """
window.mcritTestClipboard = navigator.clipboard;
Object.defineProperty(Navigator.prototype, "clipboard", {get: () => undefined, configurable: true});
Object.defineProperty(window, "isSecureContext", {get: () => false, configurable: true});
"""

#: ...and a browser that will not copy a selection either.
NO_COPY_AT_ALL = PLAIN_HTTP + "Document.prototype.execCommand = function () { return false; };"

#: A browser without the popover API, which gets the fixed box instead.
NO_POPOVER = "delete HTMLElement.prototype.popover;"

#: Every layout shift of the page, those right after a click too, which Lighthouse leaves
#: out: the message comes up within half a second of the click that copies.
COUNT_LAYOUT_SHIFTS = """
window.mcritShifted = 0;
new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) { window.mcritShifted += entry.value; }
}).observe({type: "layout-shift", buffered: true});
"""

WAIT_FOR_THE_QUEUE = "() => new Promise((resolve) => whenReady(() => resolve(true)))"

#: The status message: its text, whether it is drawn, and how it is placed.
STATUS = """() => {
    const region = document.querySelector('.status-message [role=status]');
    const box = region.parentElement;
    const rect = box.getBoundingClientRect();
    return {text: region.textContent, drawn: rect.width > 0 && rect.height > 0,
            position: getComputedStyle(box).position,
            popover: box.matches('[popover]') ? box.matches(':popover-open') : null};
}"""


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def live_server(app):
    with app.app_context():
        server_info = ServerInfo.fromDb()
        server_info.registration_token = REGISTRATION_TOKEN
        server_info.saveToDb()
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
def open_page(app, live_server, make_user):
    """open_page(path, role, init_script) -> a page, logged in, with every dialog the page
    opens recorded in `page.dialogs` and dismissed."""
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")

        def _open(path, role="contributor", init_script=None):
            user_id = make_user(role=role, username=f"{role}-{len(browser.contexts)}")
            # 127.0.0.1 is a secure context, so navigator.clipboard is there unless the
            # init script takes it away; the permissions keep it from prompting
            context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": session_cookie_for(app, user_id), "url": live_server}])
            page = context.new_page()
            page.set_default_timeout(SCRIPT_TIMEOUT_MS)
            page.dialogs = []
            page.on("dialog", lambda dialog: (page.dialogs.append(dialog.message), dialog.dismiss()))
            page.add_init_script(COUNT_LAYOUT_SHIFTS)
            if init_script:
                page.add_init_script(init_script)
            page.goto(live_server + path)
            page.evaluate(WAIT_FOR_THE_QUEUE)
            return page

        try:
            yield _open
        finally:
            browser.close()


def wait_for_status(page, text):
    page.wait_for_function("(text) => document.querySelector('.status-message [role=status]').textContent.includes(text)", arg=text)
    return page.evaluate(STATUS)


def read_clipboard(page):
    return page.evaluate("() => (window.mcritTestClipboard || navigator.clipboard).readText()")


def test_the_registration_url_is_copied_by_a_button_the_keyboard_reaches(open_page, live_server):
    """The copy was an `<i onclick>`: Tab never reached it, a screen reader saw no control,
    and it answered with an alert() that took the focus. It is a button now, named for what
    it copies; Enter copies the URL and the status message says so."""
    page = open_page("/admin/server", role="admin")
    for _ in range(80):
        page.keyboard.press("Tab")
        # Bootstrap moves a tooltip's title to aria-label once it has set the tooltip up
        if page.evaluate("document.activeElement.getAttribute('aria-label') || document.activeElement.getAttribute('title')") == "Copy registration URL":
            break
    else:
        pytest.fail("Tab never reached the registration URL's copy button")
    assert page.evaluate("[document.activeElement.tagName, document.activeElement.type]") == ["BUTTON", "button"]

    page.keyboard.press("Enter")
    status = wait_for_status(page, "Copied")

    assert read_clipboard(page) == f"{live_server}/register?token={REGISTRATION_TOKEN}"
    assert page.dialogs == [], f"the copy opened a dialog: {page.dialogs}"
    assert status["drawn"], "the status message is not on screen"


@pytest.mark.parametrize("init_script", [None, NO_POPOVER], ids=["popover", "fixed"])
def test_a_copy_is_announced_without_moving_the_page(open_page, init_script):
    """The "Copied!" of a clipboard button was a tooltip, which a screen reader is not told
    about. The status message is a live region on the page from the start, drawn over the
    page in the top layer, or fixed where there is no popover, so it moves nothing."""
    page = open_page("/settings", init_script=init_script)
    shifted = page.evaluate("window.mcritShifted")
    page.click("button.btn-clipboard")
    status = wait_for_status(page, "Copied to the clipboard")

    assert read_clipboard(page) == "apitoken-contributor"
    assert status["drawn"]
    assert status["position"] == "fixed"
    assert status["popover"] is (None if init_script else True), status
    page.wait_for_timeout(100)
    assert page.evaluate("window.mcritShifted") == shifted, "the message moved the page"
    page.wait_for_function("() => document.querySelector('.status-message [role=status]').textContent === ''", timeout=8000)
    assert not page.evaluate(STATUS)["drawn"], "the message stays on screen once it has gone"


def test_a_copy_works_on_a_page_served_over_plain_http(open_page):
    """`navigator.clipboard` is there only in a secure context, and MCRITweb is often served
    over plain HTTP: the copy threw before it did anything, and said nothing. It copies a
    selection there instead."""
    page = open_page("/settings", init_script=PLAIN_HTTP)
    assert page.evaluate("navigator.clipboard") is None
    page.click("button.btn-clipboard")
    wait_for_status(page, "Copied to the clipboard")
    assert read_clipboard(page) == "apitoken-contributor"
    assert page.evaluate("document.activeElement.classList.contains('btn-clipboard')"), "the copy took the focus away"


def test_a_copy_that_cannot_be_made_leaves_the_text_selected(open_page):
    """Where neither the clipboard nor a selection can be copied, the message says so and
    shows the text, selected, for Ctrl+C - and stays until it is closed."""
    page = open_page("/settings", init_script=NO_COPY_AT_ALL)
    page.click("button.btn-clipboard")
    wait_for_status(page, "Ctrl+C")
    assert page.evaluate("window.getSelection().toString()") == "apitoken-contributor"
    page.wait_for_timeout(5500)
    assert page.evaluate(STATUS)["drawn"], "a message to act on went away by itself"
    page.click(".status-message .btn-close")
    assert not page.evaluate(STATUS)["drawn"]



def test_the_script_fills_the_box_the_page_already_has(open_page):
    """An element added to <body> after the page loaded makes the browser restyle all of it
    (bootstrap's `legend + *`): 300 ms on a 100 by 100 cross compare matrix. The empty box
    comes with the page, and the script fills that one instead of adding its own."""
    page = open_page("/explore/samples", role="visitor")
    assert page.evaluate("() => document.querySelectorAll('.status-message').length") == 1
    assert page.evaluate("() => document.querySelector('.status-message').parentElement === document.body")
    assert page.evaluate("() => document.querySelector('.status-message [role=status]') !== null")
