#!/usr/bin/python
"""Line lengths, in a browser.

The manual's paragraphs ran the width of the window.

On the harness of testTableScrollBrowser.py; like it, this skips where playwright is not
installed, CI among them. tests/testManual.py checks the stylesheet in CI.
"""

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

import testTableScrollBrowser as harness  # noqa: E402

fake_mcrit = harness.fake_mcrit
live_server = harness.live_server
browser = harness.browser
open_page = harness.open_page


def test_the_manual_s_lines_are_at_most_80_characters_long(open_page, live_server):
    """171 characters a line at 1280px and 199 at 1920px: past about 80 a reader loses
    the start of the next line."""
    page = open_page(live_server + "/help", 1920, height=1080)
    longest = page.evaluate(
        "() => Math.max(...[...document.querySelectorAll('.manual p, .manual li')].map((element) => {"
        " const probe = document.createElement('span'); probe.textContent = '0'.repeat(100); probe.style.whiteSpace = 'nowrap';"
        " element.appendChild(probe); const ch = probe.getBoundingClientRect().width / 100; probe.remove();"
        " return element.getBoundingClientRect().width / ch; }))"
    )
    assert longest <= 80.5, f"a manual line is {longest:.0f} characters wide"
