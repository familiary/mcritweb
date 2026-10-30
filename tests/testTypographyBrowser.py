#!/usr/bin/python
"""Text sizes and line lengths, in a browser.

A reader's system text size reaches a page as a larger root font size, once the page opts
in with `<meta name="text-scale">`; here it is doubled by hand, which is what the browser
does at 200%. What was sized in px did not follow, and a box with a px maximum clipped what
grew in it. And the manual's paragraphs ran the width of the window.

On the harness of testTableScrollBrowser.py; like it, this skips where playwright is not
installed, CI among them. tests/testTextScale.py and tests/testManual.py check the
stylesheets in CI.
"""

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

import testTableScrollBrowser as harness  # noqa: E402
from fixtureData import job_id_of  # noqa: E402

fake_mcrit = harness.fake_mcrit
live_server = harness.live_server
browser = harness.browser
open_page = harness.open_page

DOUBLE_THE_ROOT = "() => new Promise((resolve) => { document.documentElement.style.fontSize = '200%'; requestAnimationFrame(() => requestAnimationFrame(resolve)); })"


def test_at_twice_the_text_size_a_copy_button_holds_its_icon(open_page, live_server):
    """The button was at most 24px, and the icon of a doubled text size is 28px."""
    page = open_page(live_server + "/explore/samples/0", 390)
    page.evaluate(DOUBLE_THE_ROOT)
    buttons = page.evaluate(
        "() => [...document.querySelectorAll('.btn-clipboard')].filter((b) => b.offsetParent).map((button) => {"
        " const b = button.getBoundingClientRect(), i = button.querySelector('i').getBoundingClientRect();"
        " return [i.left >= b.left - 0.5 && i.right <= b.right + 0.5 && i.top >= b.top - 0.5 && i.bottom <= b.bottom + 0.5, b.width, i.width]; })"
    )
    assert buttons, "no copy button on the sample page"
    assert all(fits for fits, _, _ in buttons), buttons


def font_sizes(page):
    return page.evaluate(
        "() => [document.querySelector('.cross-matrix th'), document.querySelector('.cross-matrix .cross-index'),"
        " document.querySelector('.cross-matrix .cross-label'), document.querySelector('[data-hint]')]"
        ".map((element, i) => parseFloat(getComputedStyle(element, i === 3 ? '::after' : null).fontSize))"
    )


def test_at_twice_the_text_size_the_matrix_labels_and_the_tooltips_are_twice_as_big(open_page, live_server):
    """13px, 10px and hint.css's 12px stayed what they were."""
    page = open_page(live_server + f"/data/result/{job_id_of('cross_compare')}", 1280)
    before = font_sizes(page)
    page.evaluate(DOUBLE_THE_ROOT)
    # hint.css eases every property of a tooltip over 0.3s, its size among them
    page.wait_for_timeout(500)
    after = font_sizes(page)

    assert before == [13, 10, 13, 12], "the default sizes changed"
    assert after == [2 * size for size in before], after


def test_the_manual_s_lines_are_at_most_80_characters_long(open_page, live_server):
    """171 characters a line at 1280px and 199 at 1920px: past about 80 a reader loses
    the start of the next line."""
    page = open_page(live_server + "/help", 1920, height=1080)
    longest = page.evaluate(
        "() => Math.max(...[...document.querySelectorAll('.manual p:not(:has(> img)), .manual li')].map((element) => {"
        " const probe = document.createElement('span'); probe.textContent = '0'.repeat(100); probe.style.whiteSpace = 'nowrap';"
        " element.appendChild(probe); const ch = probe.getBoundingClientRect().width / 100; probe.remove();"
        " return element.getBoundingClientRect().width / ch; }))"
    )
    assert longest <= 80.5, f"a manual line is {longest:.0f} characters wide"


def test_the_manual_s_screenshots_keep_the_width_of_the_page(open_page, live_server):
    """The manual's pictures sit in a paragraph each, and a paragraph held to 80
    characters held its picture to it: the screenshots were 640px of a 1,116px column,
    their lettering too small to read. A picture is as wide as it is, or as its column."""
    page = open_page(live_server + "/help", 1280)
    narrow = page.evaluate(
        "() => { const manual = document.querySelector('.manual'); const style = getComputedStyle(manual);"
        " const column = manual.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);"
        " return [...document.querySelectorAll('.manual img')].map((image) => [image.alt, Math.round(image.getBoundingClientRect().width), Math.min(Number(image.getAttribute('width')), column)])"
        ".filter(([, shown, wanted]) => shown < wanted - 2); }"
    )
    assert narrow == [], narrow


def test_the_pages_are_set_in_the_font_they_always_were(open_page, live_server):
    """The font moved from a style attribute on <body> to style.css; the pages look as
    they did."""
    page = open_page(live_server + "/explore/samples", 1280)
    assert page.evaluate("() => [getComputedStyle(document.body).fontFamily, getComputedStyle(document.querySelector('main h1')).fontFamily]") == ["sans-serif", "sans-serif"]
