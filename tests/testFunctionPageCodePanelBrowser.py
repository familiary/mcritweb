#!/usr/bin/python
"""The function page's code panel does not wait for its block lookups, in a browser.

`getCodefromGraph()` in main.js looks up the matches of every block that has a picblockhash,
one request each, and drew the panel only once all of them had settled: on function 945, 51
lookups, so with 80 ms more on every request the code appeared 3.46 s after navigation, held
back by the lines that annotate it. It draws the panel at once now, a "looking up" line in
each block that has a lookup, and puts each block's matches into that line as its answer
comes: the code appears at 2.71 s (medians of 23 runs).

On the offline harness of testFunctionVsBrowser.py; like it, this skips where playwright
is not installed, CI among them.
"""

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

import testFunctionVsBrowser as harness  # noqa: E402

fake_mcrit = harness.fake_mcrit
live_server = harness.live_server
browser_page = harness.browser_page

PANEL = """() => [...document.querySelectorAll('#text_code p')].map((p) => p.textContent.split('\\n')[0])"""
LAST_BLOCK_TOP = "() => document.querySelector('#text_code p:last-child').offsetTop"


def _open(page, live_server, answer):
    """The function page, its block lookups handed to `answer` as they are sent."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/getPicBlockMatches/*", answer)
    page.goto(f"{live_server}/explore/functions/{harness.FUNCTION_A}")
    page.evaluate("document.getElementById('xcfg_top').scrollIntoView()")
    return errors


def test_the_code_is_shown_before_any_lookup_answers(browser_page, live_server):
    held = []
    errors = _open(browser_page, live_server, lambda route: held.append(route))
    browser_page.wait_for_function("() => document.querySelectorAll('#text_code p').length > 0")
    first_lines = browser_page.evaluate(PANEL)
    assert held, "the panel sent no block lookups"
    assert first_lines.count(">>> Matches: looking up...") == len(held)
    top = browser_page.evaluate(LAST_BLOCK_TOP)

    held[0].continue_()
    browser_page.wait_for_function("() => [...document.querySelectorAll('#text_code p')].some((p) => /^>>> Matches: \\d+ families/.test(p.textContent))")
    for route in held[1:]:
        route.continue_()
    browser_page.wait_for_function("() => ![...document.querySelectorAll('#text_code p')].some((p) => p.textContent.startsWith('>>> Matches: looking up'))")
    answered = browser_page.evaluate(PANEL)
    assert len(answered) == len(first_lines)
    assert sum(line.startswith(">>> Matches: ") for line in answered) == len(held)
    # each answer took the line kept for it, so nothing below it moved
    assert browser_page.evaluate(LAST_BLOCK_TOP) == top
    assert errors == []


def test_a_failed_lookup_leaves_its_block_without_a_match_line(browser_page, live_server):
    errors = _open(browser_page, live_server, lambda route: route.fulfill(status=404, body=""))
    browser_page.wait_for_function("() => document.querySelectorAll('#text_code p').length > 0")
    browser_page.wait_for_function("() => ![...document.querySelectorAll('#text_code p')].some((p) => p.textContent.startsWith('>>> Matches'))")
    assert errors == []

