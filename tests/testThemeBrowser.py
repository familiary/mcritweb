#!/usr/bin/python
"""What the dark theme looks like, which only a browser can answer.

Reading the HTML back settles that `data-theme="dark"` reached the page and that no
template spells a colour out - testTheme.py does both. Neither says what the page
actually paints: the palette lives in `style.css`, the widgets that ignore it live
in vendored Bootstrap, and whether the second file catches the first is a question
about the cascade, so it is asked here with `getComputedStyle`.

The check is deliberately blunt: on a dark page, nothing may paint an opaque light
background. That is the failure mode of a half-themed application - one white table
or one white modal in the middle of a dark page - and it is what the vendored
stylesheets do if nothing overrides them.

The harness is the offline one the rest of the suite uses - `CorpusMcritClient`
behind MCRIT_CLIENT_FACTORY, no backend and no network - served on a loopback port
so Chromium can load it, exactly as in testBrowser.py.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing.
"""

import logging
import threading

import pytest
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PAGE_TIMEOUT_MS = 10000

#: Relative luminance above which a background is "a light surface". 0.5 is well
#: clear of both palettes - the dark ground is 0.012 and Bootstrap's white is 1.0 -
#: so this fails on a surface that was missed, not on a shade that was debated.
LIGHT_SURFACE = 0.5

#: The rows of the three sample pickers under /analyze. They carry a selection
#: state, and that state has to be painted from the palette like anything else - so
#: they are named here and deliberately *not* exempted from the sweep below. See
#: `test_a_clicked_picker_row_stays_readable` for what went wrong when they were.
SELECTABLE_ROWS = (
    "tr.parent, tr.parent_table_sample, tr.parent_table_sample_a, tr.parent_table_sample_b"
)

#: Enough of the application to cover the widgets the vendored stylesheets own:
#: navbar and dropdowns everywhere, tables and pagination on the listings, the
#: nav-pills, drag panels and form controls on settings, DataTables on jobs, the
#: rendered markdown on help, the sample pickers under /analyze - which paint a
#: selection - and the score-tinted result tables last, those being painted by
#: ScoreColorProvider rather than by CSS.
PAGES = [
    "/",
    "/settings",
    "/explore/families",
    "/explore/samples",
    "/explore/search?query=test",
    "/data/jobs",
    "/help",
    "/analyze/compare",
    "/analyze/compare_versus",
    "/analyze/cross_compare",
    "/analyze/cross_compare?samples=0,1&cache=2",
    f"/data/result/{job_id_of('matches_for_sample')}",
    f"/data/result/{job_id_of('cross_compare')}",
    f"/data/linkhunt/{job_id_of('matches_for_sample')}",
]

#: Walks the rendered page and reports every opaque background lighter than the
#: threshold, with enough of a description to find it again. Elements too small to
#: see, and the ones a closed modal or dropdown leaves collapsed, are skipped - and
#: so is any element whose background the *view* wrote into its style attribute.
#: Those are the score palettes, which are deliberately saturated in the cross
#: compare matrix and are covered by ScoreColorProvider's own tests and by the
#: contrast test below; everything else here comes from a stylesheet, which is what
#: the theme is responsible for.
#:
#: A picker row is the one exception to that exception. Its tint is a *state*, not a
#: computed score, so it belongs to the palette - and while the pickers wrote the
#: state into the style attribute, this skip is what hid it. Of the four picker pages
#: added below, only /analyze/compare failed the sweep before the fix - it auto-selects
#: a row, so it was the one carrying a light inline surface on load. The other three
#: needed a click, which the sweep does not do; the per-picker tests below are what
#: cover those. Post-fix the narrowing is defensive: the rows carry no inline style at
#: all now, so the skip cannot fire for them either way.
FIND_LIGHT_SURFACES = """
([threshold, selectable]) => {
  const channel = (value) => {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  const luminance = (r, g, b) =>
    0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
  const parse = (value) => {
    const m = value.match(/rgba?\\(([\\d.]+),\\s*([\\d.]+),\\s*([\\d.]+)(?:,\\s*([\\d.]+))?\\)/);
    if (!m) return null;
    return {r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4]};
  };
  const describe = (el) => {
    const id = el.id ? '#' + el.id : '';
    const cls = typeof el.className === 'string' && el.className
      ? '.' + el.className.trim().split(/\\s+/).join('.') : '';
    return el.tagName.toLowerCase() + id + cls;
  };
  const findings = [];
  for (const el of document.querySelectorAll('*')) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 6 || rect.height < 6) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.opacity === '0') continue;
    if (el.style && el.style.backgroundColor && !el.matches(selectable)) continue;
    // a plate behind a logo: the Fraunhofer mark is a third party's and keeps the
    // light ground it was drawn for rather than being recoloured
    if (!el.textContent.trim() && el.querySelector('img')) continue;
    const bg = parse(style.backgroundColor);
    if (!bg || bg.a < 0.5) continue;
    if (luminance(bg.r, bg.g, bg.b) > threshold) {
      findings.push(describe(el) + ' -> ' + style.backgroundColor);
    }
  }
  return findings;
}
"""


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.fixture
def live_server(app):
    """The app under test on a loopback port, for the seconds a test needs it."""
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
def themed_page(app, live_server, make_user):
    """Opens a Chromium page logged in as a visitor whose stored theme is the given
    one. Yields the opener, so a test can ask for either theme - or for both, when
    what it is checking is that the two disagree only in the palette.

    The cookie is signed with the app's own session interface rather than driven
    through the login form, so a change to that form cannot fail these.
    """
    from mcritweb.db import UserInfo

    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        opened = []

        def _open(theme):
            user_id = make_user(role="visitor", username=f"{theme}user{len(opened)}")
            with app.app_context():
                user_info = UserInfo.fromDb(user_id=user_id)
                user_info.theme = theme
                user_info.saveToDb()
            cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})
            context = browser.new_context()
            context.add_cookies([{
                "name": app.config["SESSION_COOKIE_NAME"],
                "value": cookie,
                "domain": "127.0.0.1",
                "path": "/",
            }])
            page = context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT_MS)
            opened.append(page)
            return page

        try:
            yield _open
        finally:
            browser.close()


@pytest.fixture
def dark_page(themed_page):
    """The page the sweeps above run on: dark, because that is where a missed
    surface shows."""
    return themed_page("dark")


@pytest.mark.parametrize("path", PAGES)
def test_no_page_paints_a_light_surface_under_the_dark_theme(dark_page, live_server, path):
    response = dark_page.goto(live_server + path)

    assert response.status == 200, f"{path} did not render"
    assert dark_page.get_attribute("html", "data-theme") == "dark"
    findings = dark_page.evaluate(FIND_LIGHT_SURFACES, [LIGHT_SURFACE, SELECTABLE_ROWS])
    assert not findings, f"{path} still paints a light surface: {findings}"


def test_the_navigation_menus_are_themed_when_they_open(dark_page, live_server):
    """A dropdown is display:none until it is opened, so the sweep above never sees
    one - and `.dropdown-menu` is painted white by Bootstrap, not by us."""
    dark_page.goto(live_server + "/")
    dark_page.click("#navbarDropdownExplore")
    dark_page.wait_for_selector(".dropdown-menu.show")

    findings = dark_page.evaluate(FIND_LIGHT_SURFACES, [LIGHT_SURFACE, SELECTABLE_ROWS])

    assert not findings, f"the open menu paints a light surface: {findings}"


def test_the_submit_modal_is_themed_when_it_opens(dark_page, live_server):
    """Same for the drop-a-file modal base.html carries on every page."""
    dark_page.goto(live_server + "/")
    dark_page.evaluate("new bootstrap.Modal(document.getElementById('submitFileModal')).show()")
    dark_page.wait_for_selector("#submitFileModal.show")

    findings = dark_page.evaluate(FIND_LIGHT_SURFACES, [LIGHT_SURFACE, SELECTABLE_ROWS])

    assert not findings, f"the open modal paints a light surface: {findings}"


def test_result_text_stays_readable_on_a_score_tinted_cell(dark_page, live_server):
    """ScoreColorProvider mixes its hues into the page rather than into white, so on
    a dark page the tints come out dark - and the text on them, which the templates
    used to pin to `black`, has to come out light."""
    dark_page.goto(live_server + f"/data/result/{job_id_of('matches_for_sample')}")

    contrasts = dark_page.evaluate("""
    () => {
      const channel = (v) => { const c = v / 255;
        return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
      const luminance = (r, g, b) =>
        0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
      const parse = (value) => {
        const m = value.match(/rgba?\\(([\\d.]+),\\s*([\\d.]+),\\s*([\\d.]+)/);
        return m ? [+m[1], +m[2], +m[3]] : null;
      };
      const out = [];
      for (const row of document.querySelectorAll('tr[style*="background-color"]')) {
        const bg = parse(getComputedStyle(row).backgroundColor);
        if (!bg) continue;
        for (const cell of row.querySelectorAll('td')) {
          if (!cell.textContent.trim()) continue;
          const fg = parse(getComputedStyle(cell).color);
          const a = luminance(...fg), b = luminance(...bg);
          out.push([(Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
                    getComputedStyle(row).backgroundColor]);
        }
      }
      return out;
    }
    """)

    assert contrasts, "no score-tinted row was rendered, so nothing was measured"
    worst = min(contrasts, key=lambda pair: pair[0])
    assert worst[0] >= 4.5, f"text on a score-tinted row is unreadable: {worst}"


# --- the sample pickers under /analyze -------------------------------------------

#: Contrast at which body text is readable on the surface behind it - WCAG AA for
#: normal text, the same bar the score-tinted rows above are held to.
READABLE = 4.5

#: The three sample pickers and the rows each of them lets you click.
PICKERS = [
    ("/analyze/compare", "tr.parent_table_sample"),
    ("/analyze/compare_versus", "tr.parent_table_sample_a"),
    ("/analyze/compare_versus", "tr.parent_table_sample_b"),
    ("/analyze/cross_compare", "tr.parent"),
]

#: What each row matching a selector is painted, and whether its text survives it.
#: `tint` is the row's own background - `rgba(0, 0, 0, 0)` for a row carrying no
#: state, which is how a test tells "selected" from "not". `background` is what the
#: eye actually sees behind the text: the tint if there is one, otherwise the
#: nearest opaque surface above it, so the contrast is the one a reader gets.
ROW_PAINT = """
(selector) => {
  const channel = (value) => {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  const luminance = (c) =>
    0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const parse = (value) => {
    const m = value.match(/rgba?\\(([\\d.]+),\\s*([\\d.]+),\\s*([\\d.]+)(?:,\\s*([\\d.]+))?\\)/);
    if (!m) return null;
    return {r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4]};
  };
  const behind = (el) => {
    for (let node = el; node; node = node.parentElement) {
      const c = parse(getComputedStyle(node).backgroundColor);
      if (c && c.a >= 0.5) return c;
    }
    return {r: 255, g: 255, b: 255, a: 1};
  };
  const out = [];
  for (const row of document.querySelectorAll(selector)) {
    const cell = Array.from(row.children).find((c) => c.textContent.trim()) || row;
    const bg = behind(row);
    const fg = parse(getComputedStyle(cell).color) || {r: 0, g: 0, b: 0, a: 1};
    const a = luminance(fg), b = luminance(bg);
    out.push({
      row: cell.textContent.trim(),
      tint: getComputedStyle(row).backgroundColor,
      background: 'rgb(' + bg.r + ', ' + bg.g + ', ' + bg.b + ')',
      color: getComputedStyle(cell).color,
      contrast: Math.round(100 * (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)) / 100,
    });
  }
  return out;
}
"""


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("path,rows", PICKERS, ids=lambda value: value.replace("tr.", "").strip("/").replace("/", "-"))
def test_a_clicked_picker_row_stays_readable(themed_page, live_server, theme, path, rows):
    """The pickers used to paint a clicked row by writing a literal into its style
    attribute, so on a dark page a selected row came out near-white under light text
    - 1.14 : 1, which is not a shade of unreadable, it is invisible."""
    page = themed_page(theme)
    page.goto(live_server + path)

    page.locator(rows).nth(1).click()
    page.mouse.move(0, 0)   # the hover tint is not what is being measured

    painted = page.evaluate(ROW_PAINT, rows)[1]

    assert painted["tint"] != "rgba(0, 0, 0, 0)", f"{path} did not mark the clicked row"
    assert painted["contrast"] >= READABLE, f"{path} in the {theme} theme: {painted}"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_clicked_row_is_painted_like_a_server_tinted_one(themed_page, live_server, theme):
    """`cache=` tells cross_compare which samples are already computed and it tints
    them while rendering; clicking a row puts it in the same state from the browser.
    One state, so one colour - it was two, and one of them ignored the theme."""
    page = themed_page(theme)
    page.goto(live_server + "/analyze/cross_compare?cache=0")

    page.locator("tr.parent").nth(1).click()
    page.mouse.move(0, 0)

    painted = page.evaluate(ROW_PAINT, "tr.parent")

    assert painted[0]["tint"] == painted[1]["tint"], (
        f"in the {theme} theme cross_compare tinted its own row {painted[0]} "
        f"but the row clicked here {painted[1]}"
    )
    assert painted[1]["contrast"] >= READABLE, f"in the {theme} theme: {painted[1]}"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_state_the_sample_picker_paints_is_readable(themed_page, live_server, theme):
    """The worst case, all three states in one table: two samples already in the
    comparison, one already computed, and one clicked here."""
    page = themed_page(theme)
    page.goto(live_server + "/analyze/cross_compare?samples=0,1&cache=2")

    page.locator("tr.parent").nth(3).click()
    page.mouse.move(0, 0)

    painted = page.evaluate(ROW_PAINT, "tr.parent")[:4]

    assert painted[2]["tint"] == painted[3]["tint"], (
        f"already computed is {painted[2]} but just clicked is {painted[3]}"
    )
    worst = min(painted, key=lambda row: row["contrast"])
    assert worst["contrast"] >= READABLE, f"in the {theme} theme: {worst}"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_clicking_toggles_a_cross_compare_row_in_and_out_of_the_selection(themed_page, live_server, theme):
    """What the colour must not cost: the picker still picks. `selected` is what
    createJob() posts, so it is the selection, and the tint has to follow it."""
    page = themed_page(theme)
    page.goto(live_server + "/analyze/cross_compare")
    rows = page.locator("tr.parent")
    untinted = page.evaluate(ROW_PAINT, "tr.parent")[0]["tint"]

    rows.nth(0).click()
    assert page.evaluate("selected") == ["0"]
    assert page.evaluate(ROW_PAINT, "tr.parent")[0]["tint"] != untinted

    rows.nth(1).click()
    assert page.evaluate("selected") == ["0", "1"], "clicking a second row dropped the first"

    rows.nth(0).click()
    assert page.evaluate("selected") == ["1"], "clicking a selected row did not deselect it"
    assert page.evaluate(ROW_PAINT, "tr.parent")[0]["tint"] == untinted


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_row_already_in_the_comparison_does_not_toggle(themed_page, live_server, theme):
    """The handler used to recognise that state by comparing the row's inline
    background against the literal "yellowgreen" - which is exactly why the colour
    could not move into the palette. The state has to survive the move."""
    page = themed_page(theme)
    page.goto(live_server + "/analyze/cross_compare?samples=0")
    before = page.evaluate(ROW_PAINT, "tr.parent")[0]["tint"]

    page.locator("tr.parent").nth(0).click()
    page.mouse.move(0, 0)

    assert page.evaluate("selected") == [], "a sample already being compared was added again"
    assert page.evaluate(ROW_PAINT, "tr.parent")[0]["tint"] == before, "its tint was overwritten"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_single_sample_picker_moves_its_selection(themed_page, live_server, theme):
    """/analyze/compare picks one sample: clicking another has to clear the first."""
    page = themed_page(theme)
    page.goto(live_server + "/analyze/compare")

    painted = page.evaluate(ROW_PAINT, "tr.parent_table_sample")
    untinted, chosen = painted[1]["tint"], painted[0]["tint"]
    assert chosen != untinted, "the page did not highlight the row it starts on"

    page.locator("tr.parent_table_sample").nth(2).click()
    page.mouse.move(0, 0)
    painted = page.evaluate(ROW_PAINT, "tr.parent_table_sample")

    assert page.evaluate("selected") == "2"
    assert painted[0]["tint"] == untinted, "the previous selection was not cleared"
    assert painted[2]["tint"] == chosen, "the same state came out two colours"
