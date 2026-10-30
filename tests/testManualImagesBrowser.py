#!/usr/bin/python
"""The manual's screenshots at the size they are drawn, in a browser.

Each screenshot carries the pixel size of its file as width and height, so that the room
for it is kept before it loads. The page is `box-sizing: border-box` throughout and a
screenshot has a 1px border, so that size was the size of the box, and the picture inside
it was 2px narrower than its file and resampled.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing.
"""

import os
import threading

import pytest
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

#: every screenshot, loaded now (they are lazy), with what the browser made of it
MEASURE = """async () => {
    const images = Array.from(document.querySelectorAll('.manual img'));
    for (const img of images) { img.loading = 'eager'; }
    await Promise.all(images.map(img => img.decode()));
    const container = document.querySelector('.manual').getBoundingClientRect().width;
    return images.map(img => {
        const box = img.getBoundingClientRect();
        const style = getComputedStyle(img);
        return {src: img.getAttribute('src'), natural: [img.naturalWidth, img.naturalHeight],
                content: [img.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight), img.clientHeight],
                box: [box.width, box.height], container: container};
    });
}"""


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
def open_help(live_server):
    """open_help(width) -> the measures of every screenshot of /help in a window that wide."""
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            def _open(width):
                page = browser.new_page(viewport={"width": width, "height": 900})
                page.goto(live_server + "/help")
                return page.evaluate(MEASURE)
            yield _open
        finally:
            browser.close()


def test_a_screenshot_is_drawn_at_its_own_pixel_size_where_it_fits(open_help):
    """With its border inside the width the file's size gave it, a 1265px screenshot was
    drawn 1263px wide, a resampled picture; the border now goes around the size."""
    shots = open_help(1920)
    assert shots
    fitting = [shot for shot in shots if shot["natural"][0] + 2 <= shot["container"]]
    assert fitting, "no screenshot fits the window to measure"
    for shot in fitting:
        assert shot["content"] == pytest.approx(shot["natural"], abs=0.01), shot


@pytest.mark.parametrize("width", [390, 820])
def test_a_screenshot_too_wide_for_the_window_shrinks_to_it_in_proportion(open_help, width):
    """What does not fit still fits the column, border included, without a sideways scroll and
    without losing its proportions (the height follows the width)."""
    for shot in open_help(width):
        assert shot["box"][0] <= shot["container"] + 0.01, shot
        ratio = shot["natural"][0] / shot["natural"][1]
        assert shot["content"][0] / shot["content"][1] == pytest.approx(ratio, rel=0.01), shot


@pytest.mark.parametrize("width", [390, 1920])
def test_a_screenshot_keeps_its_room_while_it_loads(live_server, width):
    """The size attributes are there so that a screenshot arriving later moves nothing: the box
    it is held in before its file has come is the box it has after."""
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            held = []
            page.route("**/help/images/*", lambda route: held.append(route))
            page.goto(live_server + "/help", wait_until="domcontentloaded")
            boxes = "() => Array.from(document.querySelectorAll('.manual img'), img => { const r = img.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; })"
            page.evaluate("() => document.querySelectorAll('.manual img').forEach(img => { img.loading = 'eager'; })")
            before = page.evaluate(boxes)
            page.unroute("**/help/images/*")
            assert held, "no screenshot was held back"
            page.evaluate("() => Promise.all(Array.from(document.querySelectorAll('.manual img'), img => img.decode()))")
            assert before and page.evaluate(boxes) == before
        finally:
            browser.close()
