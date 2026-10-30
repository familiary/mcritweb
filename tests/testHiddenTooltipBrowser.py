#!/usr/bin/python
"""A tooltip nobody is hovering must not widen the page, checked in a browser.

hint.css draws a tooltip with the element's ::after, on one line (`white-space: pre`),
and hides it with opacity and visibility alone. A hidden box still takes its room, and an
absolutely positioned one that reaches past the right edge makes the page scroll sideways:
the "Total (since queued)" label of the cross compare result carried a 962px hint and took
the page from 916px to 1136px wide in a phone.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing.
"""

import os

import pytest
from fixtureData import job_id_of
from testFunctionVsBrowser import live_server  # noqa: F401 - a fixture

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

PAGE_WIDTH = "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def open_page(app, live_server, make_user):  # noqa: F811
    """open_page(path, width) -> a Chromium page of that width, signed in as a visitor."""
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": make_user(role="visitor")})
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            def _open(path, width):
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
                page = context.new_page()
                page.set_default_timeout(20000)
                page.goto(live_server + path)
                return page
            yield _open
        finally:
            browser.close()


@pytest.mark.parametrize("width", [390, 820])
def test_the_total_label_s_hidden_tooltip_does_not_widen_the_cross_compare_page(open_page, width):
    """The page is as wide with the label's tooltip in place as with it taken away (the
    matrix is wider than a phone on its own), and the tooltip is laid out again, as a box,
    once the label is hovered."""
    page = open_page(f"/data/result/{job_id_of('cross_compare')}", width)
    label = page.locator("[data-hint]", has_text="Total (since queued)")
    with_tooltip, without_tooltip = label.evaluate("""el => {
        const widest = () => document.documentElement.scrollWidth;
        const hint = el.getAttribute('data-hint');
        const before = widest();
        el.removeAttribute('data-hint');
        const after = widest();
        el.setAttribute('data-hint', hint);
        return [before, after];
    }""")
    assert with_tooltip == without_tooltip, f"the hidden tooltip makes the page {with_tooltip}px wide, not {without_tooltip}px"
    assert label.evaluate("el => getComputedStyle(el, '::after').display") == "none"
    label.hover()
    assert label.evaluate("el => getComputedStyle(el, '::after').display") == "block"
