#!/usr/bin/python
"""The result pages' filter forms keep their controls 24px apart (WCAG 2.5.8).

At desktop width a checkbox there is less than 24px tall. The rule lets a target that small
pass when a circle of 24px around its centre touches no other target, and style.css gave
every field a 1em gap below it, which kept that true. With that gap gone, a checkbox sat
flush against the family-name field above it and the Filter button below, and Lighthouse's
target-size audit failed the 1vN result at desktop width. This measures the same thing in
Chromium, at the width of Lighthouse's desktop preset. At its mobile width the flex rows
stretch the checkboxes past 24px, so there is nothing to measure there.

Skipped rather than failed where playwright or its Chromium build is missing, so the
offline suite still runs on a machine that has neither.
"""

import os
import threading

import pytest
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_playwright = pytest.importorskip("playwright.sync_api", reason="playwright is not installed").sync_playwright

#: the pages with a filter form, reached the way data.result dispatches to them
PAGES = [
    ("matches_for_sample", ""),
    ("matches_for_sample", "?famid=1"),
    ("matches_for_sample_vs", ""),
    ("matches_for_sample_vs", "?samid=3"),
    ("matches_for_query", ""),
]
#: the filter forms those pages and link hunt render between them
FORMS = 9

#: Lighthouse's desktop preset
VIEWPORT = {"width": 1350, "height": 940}

#: WCAG 2.5.8 "Target Size (Minimum)", spacing exception: an undersized target passes when a
#: 24px circle on its centre intersects no other target, nor the circle of another undersized one
CROWDED = """(form) => {
  const targets = [...form.querySelectorAll('input:not([type=hidden]), button, select, textarea, a[href]')]
    .filter((el) => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
    .map((el) => ({ el, box: el.getBoundingClientRect() }));
  const small = ({ box }) => box.width < 24 || box.height < 24;
  const centre = ({ box }) => [box.x + box.width / 2, box.y + box.height / 2];
  const toBox = ([x, y], { box }) => Math.hypot(Math.max(box.left - x, 0, x - box.right), Math.max(box.top - y, 0, y - box.bottom));
  const crowded = [];
  for (const target of targets.filter(small)) {
    const c = centre(target);
    for (const other of targets) {
      if (other === target) continue;
      const near = small(other) ? Math.hypot(c[0] - centre(other)[0], c[1] - centre(other)[1]) < 24 : toBox(c, other) < 12;
      if (near) crowded.push(`${target.el.id || target.el.name} is within 12px of ${other.el.id || other.el.name || other.el.value}`);
    }
  }
  return crowded;
}"""


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Serve the captured reports, so the pages render their filter forms."""
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
def browser(live_server):
    with sync_playwright() as playwright:
        try:
            # a Chromium that playwright did not install itself is named through MCRITWEB_CHROMIUM
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except Exception as error:  # playwright raises its own Error subclass
            pytest.skip(f"chromium is not available to playwright: {error}")
        try:
            yield browser
        finally:
            browser.close()


def test_no_filter_form_control_crowds_another(live_server, browser):
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(f"{live_server}/login")
    page.fill("#username", "walker")
    page.fill("#current-password", "password")
    page.click("button[type=submit]")
    page.wait_for_load_state()

    crowded, forms = [], 0
    paths = [f"/data/result/{job_id_of(report)}{query}" for report, query in PAGES]
    paths.append(f"/data/linkhunt/{job_id_of('matches_for_sample')}")
    for path in paths:
        page.goto(f"{live_server}{path}")
        page.wait_for_load_state()
        for form in page.locator("form.form-inline").all():
            forms += 1
            crowded += [f"{path}: {problem}" for problem in form.evaluate(CROWDED)]

    assert forms == FORMS, f"{forms} filter forms on {len(paths)} pages"
    assert crowded == [], "\n".join(crowded)
