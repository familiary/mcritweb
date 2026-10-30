#!/usr/bin/python
"""The single function page's block tooltip, driven in a browser.

"Enable Tooltip" shows a block's lines when its node is hovered. Its text comes from the
analysed binary - API names among it - so it must reach the page as text. It only runs
once hovering works (the hover used to throw first), and only shows in a browser, so it is
watched here on the offline harness of testFunctionVsBrowser.py, which skips without
playwright.
"""

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

import testFunctionVsBrowser as harness  # noqa: E402

# the loopback server and a logged-in page, as testFunctionVsBrowser.py has them
live_server = harness.live_server
browser_page = harness.browser_page


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """The captured corpus, with markup in one of FUNCTION_A's API names. An API name comes
    from the analysed binary, so whoever built the sample chooses it."""
    apirefs = corpus_mcrit._functions[harness.FUNCTION_A].xcfg["apirefs"]
    apirefs[next(iter(apirefs))] = "gdi32.dll!<img src=x onerror=window.__tooltipScript=1>"
    return corpus_mcrit


def test_the_tooltip_shows_a_blocks_text_as_text(browser_page, live_server):
    """With hovering fixed, "Enable Tooltip" shows a block's lines on hover. They were joined
    into innerHTML, so an API name that is markup ran as script."""
    errors = []
    browser_page.on("pageerror", lambda error: errors.append(str(error)))
    browser_page.goto(f"{live_server}/explore/functions/{harness.FUNCTION_A}")
    browser_page.wait_for_selector("#graphContainer g.node", state="attached")
    browser_page.check("#enableTooltip")
    node = browser_page.evaluate_handle(
        "[...document.querySelectorAll('#graphContainer tspan')]"
        ".find(t => t.textContent.includes('onerror')).closest('g.node')")
    node.as_element().hover(force=True)
    browser_page.wait_for_function("!document.getElementById('tooltip').classList.contains('hidden')")
    tooltip = browser_page.evaluate("""() => ({
        text: document.querySelector('#tooltip #value').textContent,
        elements: [...document.querySelectorAll('#tooltip #value *')].map(e => e.tagName),
        ran: window.__tooltipScript,
    })""")
    assert "<img src=x onerror=" in tooltip["text"]
    assert set(tooltip["elements"]) == {"BR"}
    assert tooltip["ran"] is None
    assert errors == []
