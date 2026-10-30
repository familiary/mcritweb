#!/usr/bin/python
"""The partner logo in the navbar, in a browser: where it sits is not something markup checks see.

#253 turned `.navbar .right-logo` from an absolutely placed element into a flex item, which
moved the Fraunhofer FKIE logo from the navbar's top-right corner to right beside the MCRIT
logo from 992px up, and every other test still passed. The logo is `d-lg-block`, so it is
hidden below 992px and, from there up, it has to sit in the top-right corner, 1rem in from
the right edge, 40px high, with nothing on top of it. Measured signed out and signed in (the
menu is longer then), at the breakpoint and below it, and at two desktop widths.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing.
"""

import threading

import pytest
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

REM = 16
SLACK = 2
PADDING_REACH = 4

# Every box in the navbar that the logo must not sit on: the MCRIT logo, the menu and the
# toggler. Hidden ones (a collapsed menu, a closed dropdown) have no box and are skipped.
MEASURE = """() => {
  const box = (el) => { const r = el.getBoundingClientRect(); return {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height}; };
  const navbar = document.querySelector('nav.navbar');
  const logo = document.querySelector('.navbar .right-logo');
  const image = logo.querySelector('img');
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const others = Array.from(navbar.querySelectorAll('.navbar-brand img, .navbar-toggler, .navbar-nav a, .navbar-nav button'))
    .filter(visible)
    .map((el) => ({what: el.className || el.tagName, id: el.id, box: box(el)}));
  const r = image.getBoundingClientRect();
  const top = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
  return {
    display: getComputedStyle(logo).display,
    navbar: box(navbar),
    image: box(image),
    others: others,
    onTop: top ? top.closest('.right-logo') !== null : false,
  };
}"""


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
def measure(app, live_server, make_user):
    """Open the front page at a width, signed in or not, and measure the navbar."""

    def _measure(width, signed_in):
        with sync_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except sync_api.Error as error:
                pytest.skip(f"no Chromium for playwright to drive: {error}")
            try:
                context = browser.new_context(viewport={"width": width, "height": 900})
                if signed_in:
                    user_id = make_user(role="contributor")
                    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})
                    context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
                page = context.new_page()
                page.goto(live_server + "/")
                page.wait_for_selector("nav.navbar .right-logo", state="attached")
                measured = page.evaluate(MEASURE)
                measured["shown"] = page.locator(".navbar .right-logo img").is_visible()
                return measured
            finally:
                browser.close()

    return _measure


def overlaps(a, b):
    """Whether the boxes share more than the few px of a menu link's own padding.

    The logo is 40px high and 3px down; the links start 40.4px down, and their padding
    box reaches up under the logo by design while their text is clear of it.
    """
    across = min(a["right"], b["right"]) - max(a["left"], b["left"])
    down = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
    return across > 0 and down > PADDING_REACH


@pytest.mark.parametrize("signed_in", [False, True], ids=["signed-out", "signed-in"])
@pytest.mark.parametrize("width", [992, 1280, 1920])
def test_the_partner_logo_sits_in_the_top_right_corner_with_nothing_on_it(measure, width, signed_in):
    m = measure(width, signed_in)
    navbar, image = m["navbar"], m["image"]

    assert m["shown"], f"the partner logo is not shown at {width}px"
    assert image["height"] == 40, f"the partner logo is {image['height']}px high, not 40"
    right_gap = navbar["right"] - image["right"]
    assert abs(right_gap - REM) <= SLACK, f"at {width}px the partner logo is {right_gap:.0f}px from the navbar's right edge, not 1rem; it is at x={image['left']:.0f}-{image['right']:.0f}"
    top_gap = image["top"] - navbar["top"]
    assert 0 <= top_gap <= 3 + SLACK, f"at {width}px the partner logo is {top_gap:.0f}px below the navbar's top edge, not in the corner"
    assert m["onTop"], f"at {width}px something else is drawn over the middle of the partner logo"

    for other in m["others"]:
        assert not overlaps(image, other["box"]), f"at {width}px the partner logo overlaps {other['what']} {other['id']}: {other['box']}"
        assert image["right"] >= other["box"]["right"] - SLACK, f"at {width}px {other['what']} {other['id']} reaches {other['box']['right']:.0f}, past the partner logo's right edge {image['right']:.0f}"
        if "img-fluid" in other["what"]:
            assert image["left"] > other["box"]["right"], f"at {width}px the partner logo (left {image['left']:.0f}) is not to the right of the MCRIT logo (right {other['box']['right']:.0f})"


@pytest.mark.parametrize("signed_in", [False, True], ids=["signed-out", "signed-in"])
@pytest.mark.parametrize("width", [991, 390])
def test_the_partner_logo_is_hidden_below_the_large_breakpoint(measure, width, signed_in):
    m = measure(width, signed_in)
    assert m["display"] == "none" and not m["shown"], f"the partner logo is displayed ({m['display']}) at {width}px"
