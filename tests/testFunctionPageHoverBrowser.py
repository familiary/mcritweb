#!/usr/bin/python
"""Hovering the single function page's control flow graph, driven in a browser.

Hovering a node highlights its block in the code panel and scrolls the panel to it.
main.js looked the panel up as `#xcfg_right`, which only the comparison page has, and
threw on every hover. That only shows in a browser, so it is watched here on the
offline harness of testFunctionVsBrowser.py, which skips without playwright.
"""

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

import testFunctionVsBrowser as harness  # noqa: E402

# the offline app, its loopback server and a logged-in page, as testFunctionVsBrowser.py has them
fake_mcrit = harness.fake_mcrit
live_server = harness.live_server
browser_page = harness.browser_page


@pytest.mark.parametrize("function_id", [harness.FUNCTION_A, harness.FUNCTION_B])
def test_hovering_every_node_throws_nothing_and_brings_its_block_into_view(browser_page, live_server, function_id):
    errors = []
    browser_page.on("pageerror", lambda error: errors.append(str(error)))
    browser_page.goto(f"{live_server}/explore/functions/{function_id}")
    browser_page.wait_for_selector("#graphContainer g.node", state="attached")
    browser_page.wait_for_function("document.querySelectorAll('#text_code p').length > 0")

    nodes = browser_page.locator("#graphContainer g.node")
    highlighted = 0
    for index in range(nodes.count()):
        nodes.nth(index).hover(force=True)
        state = browser_page.evaluate(
            """() => {
                const panel = document.getElementById('xcfg_text_right');
                const block = document.querySelector('#text_code p.highlight');
                if (!block) { return null; }
                const p = panel.getBoundingClientRect(), b = block.getBoundingClientRect();
                return {top: b.top - p.top, bottom: p.bottom - b.top};
            }"""
        )
        if state is not None:
            highlighted += 1
            assert state["top"] >= -1 and state["bottom"] > 0, f"node {index}'s block is not in view: {state}"
    assert errors == []
    assert highlighted > 0, "no hover highlighted a block, so nothing was proven"
