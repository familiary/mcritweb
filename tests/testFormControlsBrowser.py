#!/usr/bin/python
"""The form controls of every page are one size, sit where the page's text starts, and no page
scrolls sideways, in Chromium at a phone's, a tablet's and two desktop widths.

The filter forms of the result pages, the link hunt and the settings page laid their check
boxes out as cells of the grid, `<input class="col-1" type="checkbox">` beside a
`<label class="col-3">`: a box was stretched to a twelfth of the row, 32px to 605px wide and
up to 72px tall, its label sat a column away and up to 59px above or below it, and the boxes
and the Filter button started 12px left of the fields, at 4px from the edge of a phone. The
CFG viewer's boxes were the browser's own 13px ones and the rest Bootstrap's 14.4px. Now every
check box and radio button is --control-check-size (style.css), centred on its label's first
line and 12px clear of every other target (WCAG 2.5.8), and every line of a filter form starts
at the page's gutter. The home page's job table squeezed its Type column to four letters a
line on a phone, and the cross compare matrix's hidden column names made a phone's page 60px
wider than the screen.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there; tests/testFormControls.py checks the markup in CI.
"""

import threading

import pytest
from conftest import session_cookie_for
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

WIDTHS = [390, 820, 1280, 1920]

#: key -> (path, a tab to open first); the path's {name} is job_id_of(name)
PAGES = {
    "result_sample": ("/data/result/{matches_for_sample}", None),
    "result_sample_famid": ("/data/result/{matches_for_sample}?famid=1", None),
    "result_query": ("/data/result/{matches_for_query}", None),
    "result_vs": ("/data/result/{matches_for_sample_vs}", None),
    "result_vs_samid": ("/data/result/{matches_for_sample_vs}?samid=3", None),
    "result_vs_funid": ("/data/result/{matches_for_sample_vs}?funid=880", None),
    "linkhunt": ("/data/linkhunt/{matches_for_sample}", None),
    "unique_blocks": ("/data/result/{unique_blocks}?tab=blocks", None),
    "settings_filter": ("/settings", "#pills-default-filter-tab"),
    "settings_theme": ("/settings", "#pills-theme-tab"),
    "explore_search": ("/explore/search?query=citadel&type=sample", None),
    "analyze_compare": ("/analyze/compare", None),
    "analyze_cross_compare": ("/analyze/cross_compare", None),
    "data_submit": ("/data/submit", None),
    "function_vs": ("/data/matches/function/84/943", None),
    "function": ("/explore/functions/84", None),
    "index": ("/", None),
    "result_cross": ("/data/result/{cross_compare}", None),
}
#: the pages with check boxes or radio buttons
CHECK_PAGES = [key for key in PAGES if key not in ("unique_blocks", "index", "result_cross")]
#: the pages with a filter form (templates/table/filter_fields.html)
FILTER_PAGES = [key for key in PAGES if key.startswith(("result_sample", "result_query", "result_vs", "linkhunt", "unique_blocks", "settings_filter"))]
JOBS = ("matches_for_sample", "matches_for_sample_vs", "matches_for_query", "cross_compare", "unique_blocks")

MEASURE = r"""() => {
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'
    && !el.closest('.modal, [hidden], .visually-hidden, .tab-pane:not(.active)');
  const probe = document.createElement('div');
  probe.style.cssText = 'position:absolute;visibility:hidden;width:var(--control-check-size)';
  document.body.appendChild(probe);
  const token = probe.getBoundingClientRect().width;
  probe.remove();
  const main = document.querySelector('main');
  const gutter = main.getBoundingClientRect().left + parseFloat(getComputedStyle(main).paddingLeft);
  // the centre of a label's first line of text, not of its box
  const firstLine = (label) => {
    const walker = document.createTreeWalker(label, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const rect = [...range.getClientRects()].find((r) => r.width > 0);
      if (rect) return rect.top + rect.height / 2;
    }
    return null;
  };
  const checks = [...document.querySelectorAll('input[type=checkbox], input[type=radio]')].filter(shown).map((box) => {
    const r = box.getBoundingClientRect();
    const label = (box.id && document.querySelector(`label[for="${CSS.escape(box.id)}"]`)) || box.closest('label');
    const line = label && shown(label) ? firstLine(label) : null;
    return {id: box.id || box.name, w: r.width, h: r.height, dy: line === null ? null : r.top + r.height / 2 - line};
  });
  const forms = [...document.querySelectorAll('form.form-inline')].filter(shown).map((form) => {
    const parts = [...form.querySelectorAll('.filter-field, .filter-checks, .filter-actions')].filter(shown);
    const lefts = [...form.querySelectorAll('label, input:not([type=hidden]), button')].filter(shown)
      .map((el) => [el.id || el.name || el.textContent.trim(), el.getBoundingClientRect().left]);
    return {
      action: form.getAttribute('action'),
      parts: parts.map((el) => el.className),
      lefts,
      fields: [...form.querySelectorAll('.filter-field .form-control')].filter(shown).map((el) => el.getBoundingClientRect().height),
      buttons: [...form.querySelectorAll('.filter-actions .btn')].filter(shown).map((el) => el.getBoundingClientRect().height),
    };
  });
  // WCAG 2.5.8's spacing exception, as Lighthouse's target-size audit applies it: a box under
  // 24px passes when a 24px circle on its centre keeps 12px from every other target
  const targets = [...document.querySelectorAll('input:not([type=hidden]), button, select, textarea, a[href]')].filter(shown);
  const crowded = [];
  for (const box of targets.filter((el) => el.type === 'checkbox' || el.type === 'radio')) {
    const r = box.getBoundingClientRect();
    const [x, y] = [r.left + r.width / 2, r.top + r.height / 2];
    for (const other of targets) {
      if (other === box) continue;
      const o = other.getBoundingClientRect();
      const distance = Math.hypot(Math.max(o.left - x, 0, x - o.right), Math.max(o.top - y, 0, y - o.bottom));
      if (distance < 12) crowded.push(`${box.id || box.name} is ${distance.toFixed(1)}px from ${other.id || other.name || other.textContent.trim().slice(0, 30)}`);
    }
  }
  const types = [...document.querySelectorAll('#matching_jobs td.job-description')].map((cell) => cell.getBoundingClientRect().width);
  const d = document.documentElement;
  return {token, gutter, checks, forms, types, crowded, scrollWidth: d.scrollWidth, clientWidth: d.clientWidth};
}"""


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Serve the captured reports, so the result pages render their forms."""
    return corpus_mcrit


@pytest.fixture
def live_server(app):
    """The app on a real socket - a browser cannot drive a Flask test client."""
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
def measure(app, live_server, make_user):
    """Open a page as a contributor at a width and measure its controls."""
    cookie = session_cookie_for(app, make_user(role="contributor"))
    ids = {name: job_id_of(name) for name in JOBS}
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")

        def open_and_measure(key, width):
            path, tab = PAGES[key]
            context = browser.new_context(viewport={"width": width, "height": 900})
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            page.goto(live_server + path.format(**ids))
            page.wait_for_function("() => window.bootstrap !== undefined")
            if tab:
                page.click(tab)
                page.wait_for_function("(tab) => document.querySelector(document.querySelector(tab).dataset.bsTarget).classList.contains('show')", arg=tab)
            measured = page.evaluate(MEASURE)
            context.close()
            return measured

        try:
            yield open_and_measure
        finally:
            browser.close()


@pytest.mark.parametrize("width", WIDTHS, ids=[f"{w}px" for w in WIDTHS])
@pytest.mark.parametrize("key", CHECK_PAGES)
def test_every_check_box_is_the_token_size_and_level_with_its_label(measure, key, width):
    page = measure(key, width)

    assert page["checks"], f"{key} shows no check box or radio button"
    assert page["token"] == 16, "the size is no longer read from --control-check-size"
    wrong_size = [(c["id"], c["w"], c["h"]) for c in page["checks"] if (c["w"], c["h"]) != (page["token"], page["token"])]
    assert wrong_size == [], f"not {page['token']}px square"
    off_line = [(c["id"], round(c["dy"], 1)) for c in page["checks"] if c["dy"] is not None and abs(c["dy"]) > 2]
    assert off_line == [], "more than 2px above or below the first line of its label"


@pytest.mark.parametrize("width", WIDTHS, ids=[f"{w}px" for w in WIDTHS])
@pytest.mark.parametrize("key", CHECK_PAGES)
def test_no_check_box_crowds_another_target(measure, key, width):
    """A 16px box is under the 24px WCAG 2.5.8 asks of a target, so it needs room around it:
    the search page's boxes sat 3px under the search field, 11px from its centre, and
    Lighthouse's target-size audit failed the page."""
    page = measure(key, width)

    assert page["crowded"] == []


@pytest.mark.parametrize("width", WIDTHS, ids=[f"{w}px" for w in WIDTHS])
@pytest.mark.parametrize("key", FILTER_PAGES)
def test_a_filter_form_starts_at_the_gutter_and_its_fields_and_buttons_are_one_height(measure, key, width):
    page = measure(key, width)

    assert page["forms"], f"{key} shows no filter form"
    for form in page["forms"]:
        lefts = [left for _, left in form["lefts"]]
        assert abs(min(lefts) - page["gutter"]) <= 1, f"{form['action']} starts at {min(lefts)}px, the page's text at {page['gutter']}px"
        astray = [(name, left) for name, left in form["lefts"] if left < page["gutter"] - 1]
        assert astray == [], f"{form['action']}: left of the page's gutter"
        heights = set(form["fields"] + form["buttons"])
        assert len(heights) == 1, f"{form['action']}: fields and buttons {sorted(heights)}px tall"


@pytest.mark.parametrize("width", WIDTHS, ids=[f"{w}px" for w in WIDTHS])
@pytest.mark.parametrize("key", list(PAGES))
def test_no_page_scrolls_sideways(measure, key, width):
    page = measure(key, width)

    assert page["scrollWidth"] <= page["clientWidth"], f"{key} is {page['scrollWidth'] - page['clientWidth']}px wider than the window"


@pytest.mark.parametrize("width", WIDTHS, ids=[f"{w}px" for w in WIDTHS])
def test_the_home_page_job_table_gives_its_type_column_room(measure, width):
    """At two thirds of a phone the Type column, which may break anywhere, was about 48px
    wide: four letters a line, and a job's description twenty lines tall. At the phone's
    whole width it is 118px."""
    page = measure("index", width)

    assert page["types"], "the home page shows no matching job"
    assert min(page["types"]) >= 100
