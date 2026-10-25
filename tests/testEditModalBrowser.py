#!/usr/bin/python
"""The family and sample edit dialogs open disarmed, in a browser.

Each page has one dialog per kind, filled from the button that opens it. Its Delete box was
never reset: ticked and the dialog closed, it came back ticked for the next family or sample,
and the plain "Submit Changes" sent `family_delete` (or `sample_delete`), which
explore.modifyFamily and explore.modifySample act on by its presence alone.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import threading

import pytest
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

#: page -> (dialog, its Delete box, the button that confirms a delete)
PAGES = {
    "/explore/families": ("#editFamilyModal", "#family_delete", "#button_delete_family"),
    "/explore/samples": ("#editSampleModal", "#sample_delete", "#button_delete_sample"),
    "/explore/samples/0": ("#editSampleModal", "#sample_delete", "#button_delete_sample"),
    "cross_compare": ("#editSampleModal", "#sample_delete", "#button_delete_sample"),
}


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


@pytest.mark.parametrize("path", sorted(PAGES))
def test_a_delete_ticked_and_cancelled_is_not_ticked_when_the_dialog_opens_again(app, live_server, make_user, path):
    dialog, delete_box, delete_button = PAGES[path]
    url = live_server + (f"/data/result/{job_id_of('cross_compare')}" if path == "cross_compare" else path)
    user_id = make_user(role="contributor")
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context()
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            page.goto(url)
            page.wait_for_function("() => window.bootstrap !== undefined")
            openers = page.locator(f"[data-bs-target='{dialog}']")
            assert openers.count() >= 1, f"{path} has no button opening {dialog}"

            openers.nth(0).click()
            page.wait_for_selector(f"{dialog}.show")
            page.check(delete_box)
            assert page.is_visible(delete_button)
            page.keyboard.press("Escape")
            page.wait_for_selector(f"{dialog}.show", state="detached")
            page.wait_for_function(f"() => !document.querySelector('{dialog}').classList.contains('show') && !document.body.classList.contains('modal-open')")

            openers.nth(1 if openers.count() > 1 else 0).click()
            page.wait_for_selector(f"{dialog}.show")
            armed = page.is_checked(delete_box)
            confirm_shown = page.is_visible(delete_button)
            keeps_samples = page.is_checked("#family_keeping_samples") if dialog == "#editFamilyModal" else True
        finally:
            browser.close()

    assert not armed, f"the Delete box on {path} is still ticked"
    assert not confirm_shown, f"the delete button on {path} is still shown"
    assert keeps_samples, "keeping the family's samples is no longer the default"
