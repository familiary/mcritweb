#!/usr/bin/python
"""Drives a real browser over the result pages whose score tooltips changed.

AGENTS.md asks for the affected pages to be exercised when a template changes, and
the rest of the suite reads rendered HTML as text - which cannot see the two things
that only exist once a browser has the page. The hover text is an *attribute*, so
`&#10;` has to survive parsing as a newline; and the tooltip itself is drawn by
`hint.css` from `content: attr(data-hint)`, so nothing about it is in the markup.

Skipped rather than failed where playwright or its Chromium build is missing, so the
offline suite still runs on a machine that has neither.
"""

import logging
import re
import threading
import unittest

import pytest
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_playwright = pytest.importorskip("playwright.sync_api", reason="playwright is not installed").sync_playwright

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The score tooltip, as the DOM has it - `&#10;` is a newline by the time it is read
#: back off the attribute, which is the half the offline test cannot check.
SCORE_TOOLTIP = re.compile(r"Bytes:\s*(-?\d+\.\d+)\s*/\s*(\d+\.\d+)\s*\nPercent:\s*(-?\d+\.\d+)%")

#: Both halves of the fraction and the percentage are rendered to two decimals.
TWO_DECIMALS = 0.005 + 1e-6

#: The four templates that render a score tooltip, reached the way `data.result`
#: dispatches to them - the same triples `testResultPages.RESULT_PAGES` documents.
WALK = [
    ("matches_for_sample", "", "result_compare_all.html"),
    ("matches_for_query", "", "result_compare_all.html"),
    ("matches_for_sample_vs", "", "result_compare_vs.html"),
    ("matches_for_sample", "?famid=1", "result_compare_family.html"),
    ("matches_for_sample_vs", "?samid=3", "result_compare_sample.html"),
]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Serve the captured reports, so the pages have real scores to render."""
    return corpus_mcrit


@pytest.fixture
def live_server(app, make_user):
    """The app on a real socket - a browser cannot drive a Flask test client."""
    make_user(role="visitor", username="walker")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


@pytest.fixture
def browser_page(live_server):
    """A logged-in page, and the console errors it collected along the way."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as error:  # playwright raises its own Error subclass
            pytest.skip(f"chromium is not available to playwright: {error}")
        try:
            page = browser.new_page()
            console_errors = []
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: console_errors.append(str(error)))
            page.goto(f"{live_server}/login")
            page.fill("#username", "walker")
            page.fill("#inputPassword", "password")
            page.click("button[type=submit]")
            page.wait_for_load_state()
            yield page, console_errors
        finally:
            browser.close()


def test_score_tooltips_hold_up_in_a_browser(live_server, browser_page):
    """Every score tooltip on every affected page states a fraction that is its own
    percentage, and the cell beside it is that percentage rounded (issue #7).
    """
    page, console_errors = browser_page
    inconsistent = []
    unrounded = []
    seen = 0

    for report, query, template in WALK:
        page.goto(f"{live_server}/data/result/{job_id_of(report)}{query}")
        page.wait_for_load_state()
        cells = page.eval_on_selector_all(
            "span[data-hint]",
            "nodes => nodes.map(node => [node.getAttribute('data-hint'), node.textContent.trim()])",
        )
        for hint, shown in cells:
            match = SCORE_TOOLTIP.search(hint)
            if match is None:
                continue
            seen += 1
            numerator, divisor, percent = (float(group) for group in match.groups())
            where = f"{template}{query} {hint.splitlines()[0]}"
            if abs(100.0 * numerator / divisor - percent) > TWO_DECIMALS:
                inconsistent.append((where, numerator, divisor, percent))
            if abs(int(shown) - percent) > 0.5 + TWO_DECIMALS:
                unrounded.append((where, percent, shown))

    assert seen, "the walk found no score tooltip on any page"
    assert not inconsistent, f"hover text states a fraction that is not its percentage: {inconsistent}"
    assert not unrounded, f"score cell is not its percentage rounded: {unrounded}"
    assert not console_errors, f"the walk logged console errors: {console_errors}"


def test_the_tooltip_css_draws_the_corrected_text(live_server, browser_page):
    """The numbers above are only in an attribute. `hint.css` is what turns one into
    visible text, so read back what the browser actually composes on hover - if the
    attribute were malformed this is where it would show up empty or truncated.
    """
    page, _ = browser_page
    page.goto(f"{live_server}/data/result/{job_id_of('matches_for_sample')}")
    cell = page.locator("span[data-hint*='Frequency Weighted Score (Library Excluded)']").first
    cell.hover()

    drawn = cell.evaluate("node => getComputedStyle(node, ':after').content")
    assert "Bytes:" in drawn and "Percent:" in drawn, f"hint.css drew {drawn!r}"

    numerator, divisor, percent = (float(group) for group in SCORE_TOOLTIP.search(cell.get_attribute("data-hint")).groups())
    assert f"{numerator:.2f}" in drawn and f"{divisor:.2f}" in drawn
    assert abs(100.0 * numerator / divisor - percent) <= TWO_DECIMALS


if __name__ == "__main__":
    unittest.main()
