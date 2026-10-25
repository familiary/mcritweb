#!/usr/bin/python
"""The dark theme (issue #70), driven in Chromium over the offline fixture corpus.

testTheme.py measures the palette and reads the markup. What it cannot see is what the
browser makes of them: a vendored selector that outranks ours, a score tint that is still
near-white, a tooltip that is a bright bubble, a text node whose own background is not the
one the palette says. Every page of the application is walked here with the dark theme on,
and the same three questions are asked of each:

  (a) the structural surfaces (the canvas, the navbar, a table head, a dialog, a menu, a
      field) are the dark palette's colours;
  (b) no visible element paints a light background, bar a short list with a reason;
  (c) every visible text node, and every icon, has the contrast WCAG asks for against the
      background the browser actually composes under it.

Then the states that only exist while something is open or in use (the drop zone's dialog, a
menu, a Bootstrap tooltip, a hint tooltip, each tab of the settings page, a picker row that is
chosen, a control that is hovered or has the keyboard's focus), the pages of an account that is
not signed in (always light), the "system" theme under an emulated colour scheme (and a live
change of it), and the light theme, which must never fetch a dark file. Last, a forced colour
palette (Windows' contrast themes) over either theme: what Bootstrap shows only with fills, images
and box shadows has to survive it.

The probe is tried on pages that are known to be wrong (the light theme's, a dark page with a
light square planted in it) so that "nothing found" can be believed.

One browser for the module and one page per case. The app is the one the rest of the suite
uses (offline corpus behind MCRIT_CLIENT_FACTORY), served on a loopback port, with a signed-in
account per theme whose session cookie is signed with the app's own interface.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing. Where it is installed, MCRITWEB_CHROMIUM names the Chromium
to launch, as in testPickerRowsBrowser.py; a launch that fails with it set is an error, not
a skip: a browser test that silently skips tests nothing.
"""

import io
import logging
import os
import threading

import pytest
import themeCss
from conftest import session_cookie_for
from fixtureData import job_id_of
from PIL import Image, ImageChops
from testResultPages import CorpusWithEveryMatchedFunction
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from mcritweb import create_app
from mcritweb.db import ServerInfo, UserInfo, init_db

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The page's own timeout: a load and anything a script does after it. The app answers in
#: milliseconds, so this is paid in full only by a test that is about to fail; a checkout
#: without the theme fails most of this module by waiting for what is not there, and that
#: has to take seconds, not minutes.
SCRIPT_TIMEOUT_MS = 10000

#: wide enough for the navbar to be expanded (Bootstrap's lg breakpoint, 992px) and for body's min-width
VIEWPORT = {"width": 1400, "height": 900}

#: two functions of the captured corpus the CFG comparison page can draw (testFunctionVsBrowser.py)
FUNCTION_A, FUNCTION_B = 84, 943


# --- the app, the accounts, the browser -----------------------------------------------------

@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """(base URL, {theme: session cookie}) of the app on the offline corpus, one admin per theme.

    Admins, because the point is to see every page, and an admin is let into all of them."""
    root = tmp_path_factory.mktemp("themed")
    backend = CorpusWithEveryMatchedFunction()
    instance_path = root / "instance"
    instance_path.mkdir()
    app = create_app(
        {
            "DATABASE": str(root / "mcritweb.sqlite"),
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "WTF_CSRF_ENABLED": False,
            "MCRIT_CLIENT_FACTORY": lambda **kwargs: backend.raw_variant() if kwargs.get("raw_responses") else backend,
            "MCRIT_SERVER_PROBE": lambda: True,
        },
        instance_path=str(instance_path),
    )
    cookies = {}
    with app.app_context():
        init_db()
        server_info = ServerInfo()
        server_info.url = "http://127.0.0.1:8000"
        server_info.operation_mode = "multi"
        server_info.registration_token = ""
        server_info.server_token = ""
        server_info.server_uuid = "test-uuid"
        server_info.server_version = "test"
        server_info.saveToDb()
        for theme in ("dark", "system", "light"):
            user_info = UserInfo()
            user_info.username = f"{theme}-admin"
            user_info.password = generate_password_hash("password")
            user_info.role = "admin"
            user_info.apitoken = f"apitoken-{theme}"
            user_info.theme = theme
            user_info.saveToDb()
            user_id = UserInfo.fromDb(username=user_info.username).user_id
            cookies[theme] = {
                "name": app.config["SESSION_COOKIE_NAME"],
                "value": session_cookie_for(app, user_id),
                "domain": "127.0.0.1",
                "path": "/",
            }
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", cookies
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


@pytest.fixture(scope="module")
def browser():
    chromium = os.environ.get("MCRITWEB_CHROMIUM") or None
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(executable_path=chromium)
        except sync_api.Error as error:
            if chromium:
                raise  # it was named, so it has to work
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            yield launched
        finally:
            launched.close()


@pytest.fixture(scope="module")
def contexts(browser, served):
    """One browser context per account, and one signed out. Each prefers the colour scheme opposite
    to what its theme says (dark and light prefer light and dark, the signed-out one dark), so that
    a theme which leaks the browser's preference shows. "system" starts on dark and the tests that
    care set it."""
    _base, cookies = served
    made = {}
    for name, scheme in (("dark", "light"), ("light", "dark"), ("system", "dark"), ("anonymous", "dark")):
        context = browser.new_context(viewport=VIEWPORT, color_scheme=scheme)
        if name != "anonymous":
            context.add_cookies([cookies[name]])
        made[name] = context
    try:
        yield made
    finally:
        for context in made.values():
            context.close()


class Visit:
    """A page of a context, with what it asked for and what went wrong on the way."""

    def __init__(self, page, base):
        self.page, self.base = page, base
        self.requests, self.failed, self.errors = [], [], []
        page.set_default_timeout(SCRIPT_TIMEOUT_MS)
        page.on("request", lambda request: self.requests.append(request.url))
        page.on("response", lambda response: self.failed.append((response.status, response.url)) if response.status >= 400 else None)
        page.on("pageerror", lambda error: self.errors.append(str(error)))

    def goto(self, path):
        self.page.goto(self.base + path, wait_until="load")
        return self.page


@pytest.fixture
def visit(served, contexts):
    """visit(context name) -> a Visit on a fresh page of that context; closed after the test."""
    base, _cookies = served
    opened = []

    def _visit(name="dark"):
        page = contexts[name].new_page()
        opened.append(page)
        return Visit(page, base)

    yield _visit
    for page in opened:
        page.close()


# --- what the page is asked ---------------------------------------------------------------

#: The probe, run in the page. It reads computed styles only and changes nothing.
#:
#:  - `background(el)` is the colour the browser composes under an element: every ancestor's
#:    background-color laid over the one above it, from <html> down. A gradient (the only thing
#:    that would make that wrong) marks the answer `unknown`, and such an element is not judged.
#:  - a text node is judged when its element is visible, has no ancestor with an opacity below
#:    1 (a fade in progress, a disabled control: their colour is not the one the palette sets)
#:    and is not Bootstrap's `visually-hidden`. Its text colour is `color` laid over the
#:    background. WCAG's 4.5:1 is asked of all of them, large or not.
#:  - an icon is an element whose ::before is a Font Awesome glyph (its colour is `color`);
#:    3:1, the contrast WCAG asks of a graphic that carries meaning.
#:  - a form field's own text (its value, or its placeholder while it is empty) is judged like a
#:    text node, against its own fill.
#:  - an element paints a light background when its composed background is lighter than 0.4 in
#:    relative luminance, it is more than 2px across both ways (a 1px rule, Bootstrap's <hr> and
#:    dropdown divider, is a line drawn in the text colour at half strength, not a surface) and it
#:    is not on the list of exceptions the caller passes in.
HELPERS = r"""
  const parse = (css) => {
    const parts = css.match(/^(?:rgba?|color\(srgb)\s*\(?\s*([^)]+)\)$/);
    if (!parts) throw new Error("cannot read the colour " + css);
    const numbers = parts[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    const scale = css.startsWith("color(") ? 255 : 1;
    return [numbers[0] * scale, numbers[1] * scale, numbers[2] * scale, numbers.length > 3 ? numbers[3] : 1];
  };
  const over = (top, under) => [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3]));
  const channel = (value) => { const v = value / 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const luminance = (rgb) => 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
  const ratio = (a, b) => { const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
  const describe = (el) => {
    const names = [];
    for (let node = el; node && node !== document.body && names.length < 3; node = node.parentElement) {
      const classes = [...node.classList].slice(0, 2).map((name) => "." + name).join("");
      names.unshift(node.tagName.toLowerCase() + (node.id ? "#" + node.id : "") + classes);
    }
    return names.join(" > ");
  };
  const background = (el) => {
    const chain = [];
    for (let node = el; node; node = node.parentElement) chain.unshift(node);
    let under = [255, 255, 255], unknown = false;
    for (const node of chain) {
      const style = getComputedStyle(node);
      if (/gradient/.test(style.backgroundImage)) unknown = true;
      under = over(parse(style.backgroundColor), under);
      // Bootstrap 5.0 paints a hovered or striped table row's tint as an inset shadow that fills the cell
      const wash = style.boxShadow.match(/(rgba?\([^)]*\)|color\(srgb[^)]*\))\s+0px\s+0px\s+0px\s+\d{3,}px\s+inset/);
      if (wash) under = over(parse(wash[1]), under);
    }
    return { rgb: under, unknown };
  };
  const faded = (el) => { for (let node = el; node; node = node.parentElement) if (parseFloat(getComputedStyle(node).opacity) < 1) return true; return false; };
  const visible = (el) => el.checkVisibility({ checkVisibilityCSS: true, visibilityProperty: true }) && el.getClientRects().length > 0;
  const hidden = (el) => !!el.closest(".visually-hidden, [hidden]");
"""

PROBE = "(allowed, iconExceptions) => {" + HELPERS + r"""
  const out = { text: [], icons: [], fields: [], light: [], counts: { textNodes: 0, judged: 0, skippedFaded: 0, skippedHidden: 0, skippedUnknown: 0, icons: 0, fields: 0, elements: 0 } };

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue.trim();
    if (!text) continue;
    const el = node.parentElement;
    if (!el || el.closest("script, style, noscript, template, option, svg, title, textarea, [data-theme-probe-skip]")) continue;
    out.counts.textNodes++;
    if (!visible(el) || hidden(el)) { out.counts.skippedHidden++; continue; }
    if (faded(el)) { out.counts.skippedFaded++; continue; }
    const range = document.createRange();
    range.selectNodeContents(node);
    if (![...range.getClientRects()].some((rect) => rect.width > 0 && rect.height > 0)) { out.counts.skippedHidden++; continue; }
    const under = background(el);
    if (under.unknown) { out.counts.skippedUnknown++; continue; }
    const colour = over(parse(getComputedStyle(el).color), under.rgb);
    out.counts.judged++;
    const found = ratio(colour, under.rgb);
    if (found < 4.5) out.text.push({ where: describe(el), text: text.slice(0, 40), ratio: Math.round(found * 100) / 100, colour: colour.map(Math.round), background: under.rgb.map(Math.round) });
  }

  for (const el of document.querySelectorAll("*")) {
    out.counts.elements++;
    if (el.closest("svg")) continue;
    if (!visible(el) || hidden(el)) continue;
    const style = getComputedStyle(el);
    const glyph = getComputedStyle(el, "::before");
    if (glyph.content && !/^(none|normal|"")$/.test(glyph.content) && /Font Awesome/.test(glyph.fontFamily) && !faded(el) && !iconExceptions.some((selector) => el.matches(selector))) {
      const under = background(el);
      if (!under.unknown) {
        out.counts.icons++;
        const found = ratio(over(parse(style.color), under.rgb), under.rgb);
        if (found < 3) out.icons.push({ where: describe(el), ratio: Math.round(found * 100) / 100, colour: parse(style.color).map(Math.round), background: under.rgb.map(Math.round), classes: el.className });
      }
    }
    if (["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName) && !["hidden", "checkbox", "radio", "range", "file", "submit", "button", "image", "reset"].includes(el.type) && !faded(el)) {
      const empty = !(el.value || "").trim();
      const shown = !empty ? style.color : el.placeholder ? getComputedStyle(el, "::placeholder").color : null;
      if (shown) {
        const under = background(el);
        out.counts.fields++;
        const found = ratio(over(parse(shown), under.rgb), under.rgb);
        if (found < 4.5) out.fields.push({ where: describe(el), placeholder: empty, ratio: Math.round(found * 100) / 100 });
      }
    }
    const box = el.getBoundingClientRect();
    if (parse(style.backgroundColor)[3] > 0 && box.width > 2 && box.height > 2 && !allowed.some((selector) => el.matches(selector))) {
      const under = background(el);
      if (!under.unknown && luminance(under.rgb) > 0.4) out.light.push({ where: describe(el), background: under.rgb.map(Math.round), luminance: Math.round(luminance(under.rgb) * 1000) / 1000 });
    }
  }
  return out;
}
"""

#: Icons the 3:1 is not asked of, and why: the idle arrow of a sortable column head. It says
#: "this column sorts", beside a label that says which column, at 2.93:1 on the head in dark
#: against the 2.09:1 master drew it with in light (testTheme.py holds dark above light).
ICON_EXCEPTIONS = [".fa-sort"]

#: Elements that may paint a light background in the dark theme, and why.
LIGHT_BACKGROUND_ALLOWED = [
    # an image carries its own pixels: the diagram and the logos are checked for which file they loaded
    "img", "picture", "canvas", "video", "iframe",
    # the cross compare matrix: one square per pair, a data colour in both themes, dimmed by a filter
    ".score-swatch",
]


#: the pages of the application, by the name a failure says: (path, a selector that has to be on the
#: page when it has finished, so that a page that did not draw what the test means cannot pass)
RESULT = {name: job_id_of(name) for name in ("matches_for_sample", "matches_for_sample_vs", "matches_for_query", "cross_compare", "unique_blocks", "maintenance_rebuild_index")}
PAGES = {
    "index": ("/", "nav.navbar"),
    "samples": ("/explore/samples", "table.table tbody tr"),
    "families": ("/explore/families", "table.table tbody tr"),
    "a family": ("/explore/families/1", "h1, h2"),
    "a sample": ("/explore/samples/0", "h1, h2"),
    "functions": ("/explore/functions", "table.table tbody tr"),
    "a function": (f"/explore/functions/{FUNCTION_A}", "#graphContainer g.node"),
    "search with results": ("/explore/search?query=citadel&type=sample", "table.table tbody tr"),
    "jobs": ("/data/jobs", "table.table"),
    "a job": (f"/data/jobs/{RESULT['matches_for_sample']}", "h1, h2"),
    "help": ("/help", ".manual"),
    "statistics": ("/explore/statistics", "h1, h2"),
    "users": ("/admin/users/", "table.table"),
    "server settings": ("/admin/server", "form"),
    "compare picker": ("/analyze/compare", "tr.parent_table_sample"),
    "versus picker": ("/analyze/compare_versus", "tr.parent_table_sample_a"),
    "cross compare picker": ("/analyze/cross_compare", "tr.parent"),
    "unique blocks picker": ("/analyze/unique_blocks", "tr.parent"),
    "submit": ("/data/submit", ".dropzone"),
    "import": ("/data/import", ".dropzone"),
    "export": ("/data/export", "form"),
    "query": ("/analyze/query", ".dropzone"),
    "compare all": (f"/data/result/{RESULT['matches_for_sample']}", "tr[style*='color-mix']"),
    "compare family": (f"/data/result/{RESULT['matches_for_sample']}?famid=1", "tr[style*='color-mix']"),
    "compare query": (f"/data/result/{RESULT['matches_for_query']}", "tr[style*='color-mix']"),
    "compare vs": (f"/data/result/{RESULT['matches_for_sample_vs']}", "tr[style*='color-mix']"),
    "compare sample": (f"/data/result/{RESULT['matches_for_sample_vs']}?samid=3", "tr[style*='color-mix']"),
    "compare function": (f"/data/result/{RESULT['matches_for_sample_vs']}?funid=880", "tr[style*='color-mix']"),
    "cross compare": (f"/data/result/{RESULT['cross_compare']}", ".score-swatch"),
    "unique blocks": (f"/data/result/{RESULT['unique_blocks']}", "table.table"),
    "maintenance result": (f"/data/result/{RESULT['maintenance_rebuild_index']}", "h1, h2"),
    "linkhunt": (f"/data/linkhunt/{RESULT['matches_for_sample']}", "tr[style*='color-mix']"),
    "function vs function": (f"/data/matches/function/{FUNCTION_A}/{FUNCTION_B}", "#graphContainer_a g.node"),
}
#: the tabs of the settings page, each shown in turn
SETTINGS_TABS = ["info", "username", "password", "default-filter", "theme", "column-settings"]


def calm(page):
    """Wait for every colour transition to have run out. Bootstrap eases the colour of a link, a
    pager and a button over 0.15s, so a page that has just changed theme, or been hovered, shows
    the colours half way for a moment: a probe that reads them then judges a colour no one sees."""
    page.wait_for_function("document.getAnimations().every((animation) => !(animation instanceof CSSTransition) || animation.playState !== 'running')")
    page.wait_for_timeout(50)


def settle(page, ready=None):
    """Wait for the page to have finished: the network quiet, every image decoded, what the page
    script draws (the graphs, the lazily filled panes) there, and the colours at rest."""
    page.wait_for_load_state("networkidle")
    if ready:
        if "graphContainer" in ready:
            # the graphs are drawn once they come into view (#308, #310, #312)
            page.evaluate("document.querySelector('[id^=graphContainer]').scrollIntoView()")
        page.wait_for_selector(ready, state="attached")
        page.evaluate("window.scrollTo(0, 0)")
    # an image below the fold that loads lazily (#245) is not fetched until it is scrolled to
    page.wait_for_function("[...document.images].every((image) => image.complete || (image.loading === 'lazy' && image.getBoundingClientRect().top >= innerHeight))")
    calm(page)


def show_tab(page, tab):
    page.click(f"#pills-{tab}-tab")
    page.wait_for_selector(f"#pills-{tab}.tab-pane.show.active")
    # the pane fades in; a text node mid-fade is skipped by the probe, so let it finish
    page.wait_for_function(f"getComputedStyle(document.querySelector('#pills-{tab}')).opacity === '1'")
    calm(page)


def run_probe(page):
    """The probe's report on the page as it is now."""
    return page.evaluate("([allowed, exceptions]) => (" + PROBE.strip() + ")(allowed, exceptions)", [LIGHT_BACKGROUND_ALLOWED, ICON_EXCEPTIONS])


class Walker:
    """Runs the probe over a page once and remembers the answer, so that the three questions
    asked of every page (a light surface, unreadable text, a faint icon) cost one visit."""

    def __init__(self, base, contexts):
        self.base, self.contexts, self.walks = base, contexts, {}

    def walk(self, key, path, ready=None, tab=None, context="dark"):
        """The report for `key`. A walk that failed fails again at once instead of waiting again:
        a page that does not draw is not asked six times."""
        if key not in self.walks:
            try:
                self.walks[key] = self._walk(path, ready, tab, context)
            except Exception as error:
                self.walks[key] = error
        if isinstance(self.walks[key], Exception):
            raise self.walks[key]
        return self.walks[key]

    def _walk(self, path, ready, tab, context):
        page = self.contexts[context].new_page()
        try:
            visit = Visit(page, self.base)
            visit.goto(path)
            settle(page, ready)
            if tab:
                show_tab(page, tab)
            result = run_probe(page)
            result["failed"] = list(visit.failed)
            result["errors"] = list(visit.errors)
            result["logos"] = page.evaluate(
                "[...document.querySelectorAll('nav picture img')].map((image) => ({src: image.currentSrc, width: image.naturalWidth}))"
            )
            result["diagrams"] = page.evaluate(
                "[...document.querySelectorAll('.mcrit-diagram img')].map((image) => ({src: image.currentSrc, width: image.naturalWidth}))"
            )
            result["present"] = page.evaluate("(ready) => !ready || document.querySelectorAll(ready).length", ready)
            result["tinted"] = page.evaluate("document.querySelectorAll('[style*=\"color-mix\"]').length")
            result["requests"] = list(visit.requests)
            return result
        finally:
            page.close()


@pytest.fixture(scope="module")
def walker(served, contexts):
    base, _cookies = served
    return Walker(base, contexts)


# --- (b) and (c): every page of the application ---------------------------------------------

WALKED = [pytest.param(name, id=name) for name in PAGES] + [pytest.param(f"settings, the {tab} tab", id=f"settings-{tab}") for tab in SETTINGS_TABS]


def walked(walker, name):
    """The probe's report on one of the WALKED pages (see Walker)."""
    if name in PAGES:
        path, ready = PAGES[name]
        return walker.walk(name, path, ready)
    tab = name.removeprefix("settings, the ").removesuffix(" tab")
    return walker.walk(name, "/settings", tab=tab)


def listed(items, limit=8):
    return "\n".join(f"    {item}" for item in items[:limit]) + (f"\n    ... {len(items)} in all" if len(items) > limit else "")


@pytest.mark.parametrize("name", WALKED)
def test_the_page_draws_what_the_walk_means_to_judge(walker, name):
    """A page that never drew (an error page, a graph that did not appear, an empty table) would
    pass the two tests below with nothing to look at."""
    report = walked(walker, name)

    assert report["present"], f"{name}: the page does not have {PAGES.get(name, ('', 'its tab'))[1]!r}"
    assert report["errors"] == [], f"{name}: the page script failed: {report['errors']}"
    assert report["failed"] == [], f"{name}: requests that failed: {report['failed']}"
    assert report["counts"]["judged"] >= 4, f"{name}: only {report['counts']['judged']} text nodes were judged, {report['counts']}"


@pytest.mark.parametrize("name", WALKED)
def test_no_visible_element_paints_a_light_background(walker, name):
    light = walked(walker, name)["light"]

    assert light == [], f"{name}: elements whose background is lighter than 0.4:\n" + listed(light)


@pytest.mark.parametrize("name", WALKED)
def test_every_visible_text_node_has_4_5_to_1_against_the_background_under_it(walker, name):
    report = walked(walker, name)

    assert report["text"] == [], f"{name}: text below 4.5:1:\n" + listed(report["text"])
    assert report["fields"] == [], f"{name}: field values or placeholders below 4.5:1:\n" + listed(report["fields"])


@pytest.mark.parametrize("name", WALKED)
def test_every_visible_icon_has_3_to_1_against_the_background_under_it(walker, name):
    icons = walked(walker, name)["icons"]

    assert icons == [], f"{name}: icons below 3:1:\n" + listed(icons)


@pytest.mark.parametrize("name", WALKED)
def test_the_dark_page_loaded_the_dark_logos_and_diagram(walker, name):
    """The source swap has to have happened: three logos, all of them the dark file and all of
    them drawn; and the match diagram, where the page has one."""
    report = walked(walker, name)

    assert len(report["logos"]) == 3, report["logos"]
    assert all(logo["src"].endswith("_dark.png") and logo["width"] > 0 for logo in report["logos"]), report["logos"]
    assert all(diagram["src"].endswith(".dark.png") and diagram["width"] > 0 for diagram in report["diagrams"]), report["diagrams"]


#: the result pages that carry the match diagram (testMatchDiagramThemes.py holds the markup)
WITH_DIAGRAM = ["compare all", "compare family", "compare query", "compare sample", "compare function"]


@pytest.mark.parametrize("name", WITH_DIAGRAM)
def test_a_result_page_with_a_diagram_loaded_the_dark_one(walker, name):
    diagrams = walked(walker, name)["diagrams"]

    assert len(diagrams) == 1, f"{name}: {len(diagrams)} diagrams on the page"
    assert diagrams[0]["src"].endswith(".dark.png") and diagrams[0]["width"] > 0, diagrams


#: the result pages that tint rows and cells by score
TINTED = ["compare all", "compare family", "compare query", "compare vs", "compare sample", "compare function", "linkhunt"]


@pytest.mark.parametrize("name", TINTED)
def test_a_tinted_row_is_dark_and_still_a_tint(walker, name):
    """Score tints are the hue at 28% over the dark ground. On every result page that tints, a
    tinted element is there, and none of them is light (the walk above judges them all)."""
    report = walked(walker, name)

    assert report["tinted"] >= 1, f"{name}: {report['tinted']} tinted elements"


def test_the_probe_is_not_blind_to_a_light_page(walker):
    """Run on the light theme's page the walk has to find the light surfaces it is looking for,
    or a clean answer on the dark pages means nothing."""
    page = walker.contexts["light"].new_page()
    try:
        Visit(page, walker.base).goto("/explore/samples")
        settle(page, "table.table tbody tr")
        report = run_probe(page)
    finally:
        page.close()

    wheres = [item["where"] for item in report["light"]]
    assert "html" in wheres and "" in wheres, f"the light canvas and body are not found light: {wheres[:5]}"  # "" is <body>: describe() stops short of it
    # the navbar paints nothing of its own since #253, so it is not among them
    assert any(where.endswith("thead.table-light") for where in wheres), "the light table head is not found light"
    assert len(report["light"]) >= 10


# --- (a): the palette's colours on the structural surfaces -----------------------------------

def dark_token(name):
    return themeCss.parse_colour(f"var({name})", themeCss.resolver(themeCss.dark_cascade()))[:3]


def computed(page, selector, *properties):
    """The computed properties of the first element matching `selector`, as written by the browser."""
    return page.evaluate(
        """([selector, properties]) => {
            const element = document.querySelector(selector);
            if (!element) return null;
            const style = getComputedStyle(element);
            return properties.map((property) => style.getPropertyValue(property));
        }""",
        [selector, list(properties)],
    )


def as_rgb(value):
    return themeCss.parse_colour(value)[:3]


def assert_colour(found, token, what, tolerance=1.0):
    wanted = dark_token(token)
    assert all(abs(a - b) <= tolerance for a, b in zip(as_rgb(found), wanted)), f"{what} is {found}; the dark palette's {token} is rgb{tuple(round(c) for c in wanted)}"


def test_the_canvas_the_navbar_the_table_head_and_the_fields_are_the_palettes_colours(visit):
    page = visit("dark").goto("/explore/samples")
    settle(page, "table.table tbody tr")

    for what, selector, token in (
        ("the canvas (html)", "html", "--surface-page"),
        ("the page (body)", "body", "--surface-page"),
        ("a table head", "thead.table-light", "--surface-chrome"),
        ("the search field", "input.form-control", "--surface-input"),
        ("the page's pager", ".pagination .page-link", "--surface-page"),
    ):
        found = computed(page, selector, "background-color")
        assert found is not None, f"no {what} on the page"
        assert_colour(found[0], token, what)
    assert_colour(computed(page, "body", "color")[0], "--text-body", "the body text")
    # since #253 the navbar paints nothing of its own: it shows the page
    assert computed(page, "nav.navbar", "background-color")[0] == "rgba(0, 0, 0, 0)", "the navbar paints a colour of its own"
    assert_colour(computed(page, "thead.table-light", "color")[0], "--text-strong", "the table head text")
    assert_colour(computed(page, "h1", "color")[0], "--accent", "a heading")
    assert_colour(computed(page, "table.table td a", "color")[0], "--link", "a link in a table")


def test_the_menus_and_the_dialog_are_raised_dark_surfaces(visit):
    page = visit("dark").goto("/explore/samples")
    settle(page, "table.table tbody tr")

    # a dropdown menu: opened by the navbar entry, not read from the DOM while it is hidden
    page.click("#navbarDropdownExplore")
    page.wait_for_selector(".dropdown-menu.show")
    menu = computed(page, ".dropdown-menu.show", "background-color", "border-top-color", "box-shadow")
    assert_colour(menu[0], "--surface-raised", "an open dropdown menu")
    assert_colour(menu[1], "--border", "the border of the menu")
    assert "0.45" in menu[2], f"the menu's shadow is {menu[2]}"
    assert_colour(computed(page, ".dropdown-menu.show .dropdown-item", "color")[0], "--text-body", "a menu entry")
    page.keyboard.press("Escape")
    page.click("body", position={"x": 5, "y": 400})

    # the drop zone's dialog
    # Dropzone is fetched on the first drag (#126); the dialog's zone is one only after that
    page.evaluate("new Promise((resolve) => mcritLoadDropzone(resolve))")
    page.evaluate("$('#submitFileModal').modal('show')")
    page.wait_for_selector("#submitFileModal.show")
    page.wait_for_function("getComputedStyle(document.querySelector('#submitFileModal')).opacity === '1'")
    modal = computed(page, "#submitFileModal .modal-content", "background-color", "border-top-color", "color")
    assert_colour(modal[0], "--surface-raised", "the dialog")
    assert_colour(modal[1], "--border", "the dialog's border")
    assert_colour(modal[2], "--text-body", "the dialog's text")
    zone = computed(page, "#submitFileModal .dropzone", "background-color", "border-top-color", "border-top-style")
    assert_colour(zone[0], "--surface-chrome", "the drop zone's fill")
    assert_colour(zone[1], "--dropzone-border", "the drop zone's border")
    assert zone[2] == "dashed"
    assert "invert" in computed(page, "#submitFileModal .btn-close", "filter")[0], "the close cross is still black"
    assert_colour(computed(page, "#submitFileModal .form-control", "background-color")[0], "--surface-input", "a field in the dialog")
    calm(page)
    report = run_probe(page)
    assert report["text"] == [] and report["light"] == [] and report["icons"] == [], f"{report['text'][:4]} {report['light'][:4]} {report['icons'][:4]}"


def test_the_drop_zone_outline_holds_3_to_1_in_the_dialog(visit):
    """The dashed border is all there is of the target's outline: WCAG 1.4.11 asks 3:1 of it against
    the fill inside it and the dialog around it (the dialog is what every page shows it in)."""
    page = visit("dark").goto("/explore/samples")
    settle(page, "table.table tbody tr")
    # Dropzone is fetched on the first drag (#126); the dialog's zone is one only after that
    page.evaluate("new Promise((resolve) => mcritLoadDropzone(resolve))")
    page.evaluate("$('#submitFileModal').modal('show')")
    page.wait_for_selector("#submitFileModal.show")
    page.wait_for_function("getComputedStyle(document.querySelector('#submitFileModal')).opacity === '1'")

    border = as_rgb(computed(page, "#submitFileModal .dropzone", "border-top-color")[0])
    inside = as_rgb(computed(page, "#submitFileModal .dropzone", "background-color")[0])
    around = as_rgb(computed(page, "#submitFileModal .modal-content", "background-color")[0])

    assert min(themeCss.contrast(border, inside), themeCss.contrast(border, around)) >= 3, (
        f"the outline rgb{tuple(round(c) for c in border)} is {themeCss.contrast(border, inside):.3f}:1 against the fill and {themeCss.contrast(border, around):.3f}:1 against the dialog"
    )


def test_the_column_settings_lists_are_dark(visit):
    page = visit("dark").goto("/settings")
    settle(page)
    show_tab(page, "column-settings")

    assert_colour(computed(page, "#pills-column-settings .list-group.col", "background-color")[0], "--surface-subtle", "a column-settings list")
    item = computed(page, "#pills-column-settings .list-group-item", "background-color", "color", "border-top-color")
    assert_colour(item[0], "--surface-raised", "an item of the list")
    assert_colour(item[1], "--text-body", "an item's text")
    assert page.locator("#pills-column-settings .list-group-item").count() >= 10, "the lists have hardly any items"
    report = run_probe(page)
    assert report["text"] == [] and report["light"] == [], f"{report['text'][:4]} {report['light'][:4]}"


# --- the two kinds of tooltip ----------------------------------------------------------------

def luminance_of(value):
    return themeCss.luminance(as_rgb(value))


def test_a_bootstrap_tooltip_is_a_raised_dark_bubble(visit):
    """The clipboard button next to a hash (links.html) has a Bootstrap tooltip."""
    page = visit("dark").goto(PAGES["a sample"][0])
    settle(page, PAGES["a sample"][1])
    button = page.locator("button[data-toggle=tooltip]:visible").first
    assert button.count() == 1, "the page has no clipboard button to rest the pointer on"

    button.hover()
    page.wait_for_selector(".tooltip.show .tooltip-inner")
    bubble = computed(page, ".tooltip.show .tooltip-inner", "background-color", "color")
    assert_colour(bubble[0], "--tooltip-bg", "the tooltip's bubble")
    assert_colour(bubble[1], "--tooltip-text", "the tooltip's text")
    assert themeCss.contrast(as_rgb(bubble[1]), as_rgb(bubble[0])) >= 4.5
    arrow = page.evaluate(
        """() => {
            const style = getComputedStyle(document.querySelector('.tooltip.show .tooltip-arrow'), '::before');
            return ['top', 'right', 'bottom', 'left'].map((side) => style.getPropertyValue('border-' + side + '-color'));
        }"""
    )
    painted = [colour for colour in arrow if colour != "rgba(0, 0, 0, 0)"]
    assert painted, f"the arrow paints no side: {arrow}"
    for colour in painted:
        assert_colour(colour, "--tooltip-bg", "the arrow of the tooltip")


def test_a_hint_tooltip_is_a_raised_dark_bubble(visit):
    """hint.css draws its tooltip from the ::after of the element under the pointer."""
    page = visit("dark").goto(f"/data/result/{RESULT['matches_for_sample']}")
    settle(page, "tr[style*='color-mix']")
    cell = page.locator("td span[data-hint]:visible").first
    assert cell.count() == 1, "the page has no score cell to rest the pointer on"

    cell.hover()
    page.wait_for_function(
        "(cell) => getComputedStyle(cell, '::after').visibility === 'visible' && getComputedStyle(cell, '::after').opacity === '1'",
        arg=cell.element_handle(),
    )
    bubble, arrow = cell.evaluate(
        """(cell) => {
            const after = getComputedStyle(cell, '::after'), before = getComputedStyle(cell, '::before');
            return [
                [after.backgroundColor, after.color, after.content],
                ['top', 'right', 'bottom', 'left'].map((side) => before.getPropertyValue('border-' + side + '-color')),
            ];
        }"""
    )
    assert "Percent" in bubble[2], f"the bubble says {bubble[2]!r}: this is not a score tooltip"
    assert_colour(bubble[0], "--tooltip-bg", "the hint bubble")
    assert_colour(bubble[1], "--tooltip-text", "the hint text")
    assert themeCss.contrast(as_rgb(bubble[1]), as_rgb(bubble[0])) >= 4.5
    painted = [colour for colour in arrow if colour != "rgba(0, 0, 0, 0)"]
    assert painted, f"the hint's arrow paints no side: {arrow}"
    for colour in painted:
        assert_colour(colour, "--tooltip-bg", "the arrow of the hint")


# --- the pickers' row states ---------------------------------------------------------------------

def test_the_picker_rows_are_painted_from_the_dark_palette_in_each_state(visit):
    """A row of a sample picker is `row-selected` when the URL chose it and `row-pending` once it is
    clicked (testPickerRowsBrowser.py holds the classes and the scripts). What the classes paint is
    the theme's: here, the dark --row-selected and --row-pending, with the row's text still readable."""
    page = visit("dark").goto("/analyze/cross_compare?samples=0")
    settle(page, "tr.parent")
    chosen = page.locator("tr.parent.row-selected").first
    assert chosen.count() == 1, "the URL's sample is not marked selected"
    plain = page.locator("tr.parent:not(.row-selected)").first
    plain.locator("td:not(:has(a))").first.click()
    assert "row-pending" in plain.get_attribute("class")

    for row, token in ((chosen, "--row-selected"), (plain, "--row-pending")):
        assert_colour(row.evaluate("(row) => getComputedStyle(row).backgroundColor"), token, f"a {token[2:]} row")
        assert row.evaluate("(row) => getComputedStyle(row.querySelector('td')).backgroundColor") == "rgba(0, 0, 0, 0)", "a cell covers the row's colour"
    calm(page)
    report = run_probe(page)
    assert report["text"] == [] and report["light"] == [], f"{report['text'][:4]} {report['light'][:4]}"


# --- signed out: always light -----------------------------------------------------------------

#: the colours of the light pages, as the browser reports them
LIGHT_PAGE = "rgb(255, 255, 255)"
DARK_PAGE = "rgb(20, 26, 33)"
DARK_FILES = ("theme-dark.css", "_dark.png", ".dark.png")


def asked_for_dark(requests):
    return [url for url in requests if any(marker in url for marker in DARK_FILES)]


@pytest.mark.parametrize("path", ["/login", "/register", "/help"])
def test_a_page_nobody_is_signed_in_to_is_light_whatever_the_browser_prefers(visit, path):
    """The context prefers dark. Nobody is signed in, so there is no account to have chosen a
    theme, and the page is light: its canvas, its text, its logos, its stylesheets."""
    opened = visit("anonymous")
    page = opened.goto(path)
    settle(page)

    assert page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches"), "the context was meant to prefer dark"
    assert page.get_attribute("html", "data-theme") == "light"
    assert page.evaluate("getComputedStyle(document.documentElement).backgroundColor") == LIGHT_PAGE
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == LIGHT_PAGE
    assert_text = page.evaluate("getComputedStyle(document.body).color")
    assert themeCss.luminance(as_rgb(assert_text)) < 0.05, f"the text of a light page is {assert_text}"
    assert page.evaluate("document.querySelector('meta[name=color-scheme]').content") == "light"
    assert asked_for_dark(opened.requests) == [], "a light page fetched a dark file"
    assert [image["src"].rsplit("/", 1)[-1] for image in page.evaluate("[...document.querySelectorAll('nav picture img')].map((image) => ({src: image.currentSrc}))")] == [
        "d20_mcrit_cabaret.png",
        "d20_mcrit_cabaret.png",
        "fkie_190x52.gif",
    ]


# --- "system": the browser decides, live ------------------------------------------------------

def logo_files(page):
    return [src.rsplit("/", 1)[-1] for src in page.evaluate("[...document.querySelectorAll('nav picture img')].map((image) => image.currentSrc)")]


@pytest.mark.parametrize("preference, dark", [("dark", True), ("light", False)])
def test_the_system_theme_applies_the_dark_palette_when_the_browser_prefers_dark_and_not_otherwise(visit, preference, dark):
    opened = visit("system")
    opened.page.emulate_media(color_scheme=preference)
    page = opened.goto("/explore/samples")
    settle(page, "table.table tbody tr")

    assert page.get_attribute("html", "data-theme") == "system"
    assert page.evaluate("getComputedStyle(document.documentElement).backgroundColor") == (DARK_PAGE if dark else LIGHT_PAGE)
    # since #253 the navbar paints nothing of its own: it shows the page
    assert page.evaluate("getComputedStyle(document.querySelector('nav.navbar')).backgroundColor") == "rgba(0, 0, 0, 0)"
    assert logo_files(page) == (["d20_mcrit_cabaret_dark.png"] * 2 + ["fkie_190x52_dark.png"] if dark else ["d20_mcrit_cabaret.png"] * 2 + ["fkie_190x52.gif"])
    if dark:
        assert report_clean(run_probe(page)), "the system theme's dark page has what the dark theme's has not"
        assert any(url.endswith("theme-dark.css") for url in opened.requests)
    else:
        assert [url for url in opened.requests if "_dark.png" in url or ".dark.png" in url] == [], "a browser that prefers light fetched a dark image"
        assert page.evaluate("document.styleSheets.length") >= 1


@pytest.mark.parametrize("account", ["system", "dark", "light"])
@pytest.mark.parametrize("preference", ["dark", "light"])
def test_the_settings_page_says_which_way_the_system_leans(visit, account, preference):
    """"Match my system (currently ...)" names the operating system's preference, whichever
    theme the page itself is in, and only one of its two words shows."""
    opened = visit(account)
    opened.page.emulate_media(color_scheme=preference)
    page = opened.goto("/settings")
    settle(page)
    show_tab(page, "theme")
    label = page.inner_text("label[for=theme_system]")
    assert f"(currently {preference})" in label
    assert f"(currently {'light' if preference == 'dark' else 'dark'})" not in label


def report_clean(report):
    return report["text"] == [] and report["light"] == [] and report["fields"] == []


def test_the_system_theme_follows_a_change_of_the_browsers_preference_without_a_reload(visit):
    opened = visit("system")
    opened.page.emulate_media(color_scheme="light")
    page = opened.goto(PAGES["samples"][0])
    settle(page, PAGES["samples"][1])
    page.evaluate("window.__sameDocument = true")
    assert page.evaluate("getComputedStyle(document.documentElement).backgroundColor") == LIGHT_PAGE

    page.emulate_media(color_scheme="dark")
    page.wait_for_function(f"getComputedStyle(document.documentElement).backgroundColor === '{DARK_PAGE}'")
    page.wait_for_function("[...document.querySelectorAll('nav picture img')].every((image) => image.currentSrc.endsWith('_dark.png'))")
    calm(page)
    assert logo_files(page) == ["d20_mcrit_cabaret_dark.png"] * 2 + ["fkie_190x52_dark.png"]
    assert report_clean(run_probe(page)), "after the switch the page is not the dark theme's"

    page.emulate_media(color_scheme="light")
    page.wait_for_function(f"getComputedStyle(document.documentElement).backgroundColor === '{LIGHT_PAGE}'")
    page.wait_for_function("[...document.querySelectorAll('nav picture img')].every((image) => !image.currentSrc.includes('_dark'))")
    assert logo_files(page) == ["d20_mcrit_cabaret.png"] * 2 + ["fkie_190x52.gif"]
    assert page.evaluate("window.__sameDocument") is True, "the page reloaded: the switch was not live"


def test_the_dark_theme_is_dark_whatever_the_browser_prefers(visit):
    """The context behind `dark` prefers light."""
    page = visit("dark").goto(PAGES["samples"][0])

    assert not page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
    assert page.evaluate("getComputedStyle(document.documentElement).backgroundColor") == DARK_PAGE
    page.emulate_media(color_scheme="light")
    assert page.evaluate("getComputedStyle(document.documentElement).backgroundColor") == DARK_PAGE


# --- the light theme never asks for a dark file ----------------------------------------------

LIGHT_VISITS = [
    PAGES["samples"],
    PAGES["a function"],
    ("/settings", None),
    (f"/data/result/{RESULT['matches_for_sample']}", ".mcrit-diagram img"),
    PAGES["cross compare"],
]


def test_the_light_theme_never_requests_the_dark_stylesheet_or_a_dark_image(visit):
    """Under a browser that prefers dark, which is where a leak of the preference would show."""
    opened = visit("light")
    for path, ready in LIGHT_VISITS:
        page = opened.goto(path)
        settle(page, ready)
        assert page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
        assert page.get_attribute("html", "data-theme") == "light", path
        assert page.evaluate("getComputedStyle(document.documentElement).backgroundColor") == LIGHT_PAGE, path
        assert logo_files(page) == ["d20_mcrit_cabaret.png"] * 2 + ["fkie_190x52.gif"], path

    assert asked_for_dark(opened.requests) == []
    assert any(url.endswith("/static/style.css") for url in opened.requests), "the visit recorded nothing"
    assert any("/data/diagrams/" in url and url.endswith(".png") for url in opened.requests), "the light diagram was never fetched: the visit did not reach one"
    assert opened.failed == []


def test_the_dark_theme_asks_for_exactly_the_dark_files(visit):
    """The other side of the test above: the same visits under the dark theme do fetch them, so
    that a recorder that sees nothing cannot pass the light test."""
    opened = visit("dark")
    for path, ready in LIGHT_VISITS:
        settle(opened.goto(path), ready)

    asked = asked_for_dark(opened.requests)
    assert any(url.endswith("/static/theme-dark.css") for url in asked)
    assert any(url.endswith("/static/d20_mcrit_cabaret_dark.png") for url in asked)
    assert any(url.endswith("/static/fkie_190x52_dark.png") for url in asked)
    assert any("/data/diagrams/" in url and url.endswith(".dark.png") for url in asked)
    light_logos = [url for url in opened.requests if url.endswith(("/static/d20_mcrit_cabaret.png", "/static/fkie_190x52.gif"))]
    assert light_logos == [], f"a dark page also fetched the light logos: {light_logos}"
    assert opened.failed == []


# --- the probe itself ---------------------------------------------------------------------------

def test_the_probe_catches_a_light_background_unreadable_text_a_faint_icon_and_a_faint_field(visit):
    """When a probe says "nothing", it has to be able to say something. This plants one of each on
    a dark page, and a light square and a hairline the probe is meant to leave alone."""
    page = visit("dark").goto(PAGES["index"][0])
    settle(page)
    page.evaluate(
        """() => {
            const plant = (html) => document.querySelector('main').insertAdjacentHTML('beforeend', html);
            plant('<div id="planted-light" style="background: #eee; width: 80px; height: 30px">x</div>');
            plant('<p id="planted-faint" style="color: #3a4654">planted faint text</p>');
            plant('<p id="planted-over" style="color: #cfd7e0; background: #e8eef4">planted text on a light background</p>');
            plant('<i id="planted-icon" class="fa-solid fa-square-check" style="color: #1c242e; font-size: 20px"></i>');
            plant('<input id="planted-field" value="planted field" style="color: #151c24">');
            plant('<style>#planted-placeholder::placeholder { color: #3a4654 }</style><input id="planted-placeholder" placeholder="planted placeholder">');
            plant('<span class="score-swatch" id="planted-swatch" style="display:inline-block; width: 25px; height: 25px; background: #ffff00"></span>');
            plant('<div id="planted-hairline" style="height: 1px; width: 200px; background: #fff"></div>');
            plant('<p id="planted-ok" style="color: #cfd7e0">planted readable text</p>');
        }"""
    )

    report = run_probe(page)

    assert any("planted-light" in item["where"] for item in report["light"]), report["light"]
    assert any("planted-over" in item["where"] for item in report["light"])
    assert any("planted-faint" in item["where"] for item in report["text"]), report["text"]
    assert any("planted-over" in item["where"] for item in report["text"])
    assert any("planted-icon" in item["where"] for item in report["icons"]), report["icons"]
    assert any("planted-field" in item["where"] for item in report["fields"]), report["fields"]
    assert any("planted-placeholder" in item["where"] and item["placeholder"] for item in report["fields"]), report["fields"]
    everything = str(report)
    for left_alone in ("planted-swatch", "planted-hairline", "planted-ok"):
        assert left_alone not in everything, f"{left_alone} was reported"


def test_the_probe_composes_a_translucent_background_before_it_judges(visit):
    """Bootstrap's hover and active states are translucent white over the dark ground (about 6% of
    it): composed, a dark colour; read raw, white."""
    page = visit("dark").goto(PAGES["index"][0])
    settle(page)
    page.evaluate(
        """() => document.querySelector('main').insertAdjacentHTML('beforeend',
            '<p id="planted-veil" style="background: rgba(255, 255, 255, 0.06); color: #cfd7e0">planted text on a veil</p>')"""
    )

    report = run_probe(page)

    assert "planted-veil" not in str(report)


# --- the states a control is in while it is used ---------------------------------------------

#: The element's own text against the background composed under it, as it is right now.
CONTRAST_OF = (
    "(element) => {" + HELPERS + """
  const under = background(element);
  const colour = over(parse(getComputedStyle(element).color), under.rgb);
  return { ratio: Math.round(ratio(colour, under.rgb) * 100) / 100, colour: colour.map(Math.round), background: under.rgb.map(Math.round), unknown: under.unknown };
}"""
)

#: (what it is, the page it is on, the element): one of each kind of control the pages have
CONTROLS = [
    ("a primary button", "server settings", ".btn-primary"),
    ("a secondary button", "compare all", ".btn-secondary"),
    ("an outline button", "samples", ".btn-outline-dark"),
    ("an outline primary button", "cross compare", ".btn-outline-primary"),
    ("a light button", "a sample", ".btn-light"),
    ("a pager link", "samples", ".page-item:not(.disabled) .page-link"),
    ("a pager link to another page", "functions", ".page-item:not(.disabled):not(.active) .page-link"),
    ("a pill", "samples", ".nav-link"),
    ("an accordion header", "a function", ".accordion-button"),
    ("a cell of a hovered table row", "samples", ".table-hover tbody td"),
]


@pytest.mark.parametrize("state", ["hover", "focus"])
@pytest.mark.parametrize("what, name, selector", CONTROLS, ids=[control[0] for control in CONTROLS])
def test_a_control_keeps_4_5_to_1_while_it_is_hovered_and_while_it_has_focus(visit, what, name, selector, state):
    """Bootstrap restyles a control's text and fill when the pointer or the keyboard is on it, and
    the dark stylesheet restates each of those. The probe judges the resting page; this is the
    same arithmetic on the state a person is in when they are about to click."""
    path, ready = PAGES[name]
    page = visit("dark").goto(path)
    settle(page, ready)
    element = page.locator(f"{selector}:visible").first
    assert element.count() == 1, f"{name} has no {what} ({selector})"

    if state == "hover":
        element.hover()
    else:
        element.focus()
    calm(page)
    found = element.evaluate(CONTRAST_OF)

    assert not found["unknown"]
    assert found["ratio"] >= 4.5, f"{what} while {state}ed: {found['ratio']}:1, text rgb{tuple(found['colour'])} on rgb{tuple(found['background'])}"


def test_a_menu_entry_keeps_4_5_to_1_while_it_is_hovered(visit):
    page = visit("dark").goto(PAGES["samples"][0])
    settle(page, PAGES["samples"][1])
    page.click("#navbarDropdownExplore")
    page.wait_for_selector(".dropdown-menu.show")
    entry = page.locator(".dropdown-menu.show .dropdown-item").first

    entry.hover()
    calm(page)
    found = entry.evaluate(CONTRAST_OF)

    assert found["ratio"] >= 4.5, f"{found}"


#: How visible the focus indicator of the element in focus is: its outer ring (a box-shadow with a
#: spread, the way Bootstrap draws it) laid over what is behind the element, and its border, each
#: against what is behind the element. The larger of the two is what a person has to go by.
FOCUS_INDICATOR_OF = (
    "(element) => {" + HELPERS + """
  const behind = background(element.parentElement).rgb;
  const style = getComputedStyle(element);
  const ring = style.boxShadow.match(/(rgba?\\([^)]*\\)|color\\(srgb[^)]*\\))\\s+0px\\s+0px\\s+0px\\s+([\\d.]+)px(?!\\s+inset)/);
  const found = { ring: null, border: null, ringColour: null };
  if (ring) { const colour = over(parse(ring[1]), behind); found.ring = Math.round(ratio(colour, behind) * 100) / 100; found.ringColour = colour.map(Math.round); }
  const border = parse(style.borderTopColor);
  if (border[3] > 0 && parseFloat(style.borderTopWidth) > 0) found.border = Math.round(ratio(over(border, behind), behind) * 100) / 100;
  found.behind = behind.map(Math.round);
  return found;
}"""
)

FOCUSABLE = [
    ("an outline button", "samples", ".btn-outline-dark"),
    ("a primary button", "server settings", ".btn-primary"),
    ("an accordion header", "a function", ".accordion-button"),
    ("a field", "samples", "input.form-control"),
]


@pytest.mark.parametrize("what, name, selector", FOCUSABLE, ids=[control[0] for control in FOCUSABLE])
def test_the_focus_indicator_holds_3_to_1_against_what_is_behind_the_control(visit, what, name, selector):
    """WCAG 1.4.11: what shows where the keyboard is has to be 3:1 against its surroundings. The
    ring or the border, whichever is the better of the two, is the indicator."""
    path, ready = PAGES[name]
    page = visit("dark").goto(path)
    settle(page, ready)
    element = page.locator(f"{selector}:visible").first
    assert element.count() == 1, f"{name} has no {what}"

    element.focus()
    calm(page)
    found = element.evaluate(FOCUS_INDICATOR_OF)

    best = max(value for value in (found["ring"], found["border"]) if value is not None)
    assert best >= 3, f"{what} in focus: ring {found['ring']}:1, border {found['border']}:1 against rgb{tuple(found['behind'])}"


def test_the_current_page_of_the_pager_shows_its_focus(visit):
    """The current page is marked by an underline drawn as a box-shadow, the property Bootstrap's
    focus ring is drawn with too: one rule replaced the other, and focusing it changed nothing.
    Its border is there with or without focus, so only the ring tells."""
    path, ready = PAGES["samples"]
    page = visit("dark").goto(path)
    settle(page, ready)
    current = page.locator(".page-item.active .page-link:visible").first
    assert current.count() == 1, "the samples page has no current page in its pager"

    current.focus()
    calm(page)
    found = current.evaluate(FOCUS_INDICATOR_OF)

    assert found["ring"] is not None and found["ring"] >= 3, f"the current page in focus: {found}"


# --- forced colours (Windows' contrast themes) ------------------------------------------------
#
# A forced colour palette replaces every colour an author sets with the user's own few, and drops
# background images and box shadows. Bootstrap 5.0.2 draws a ticked box as a white SVG on its blue
# fill, its focus rings as box shadows, the current page and tab as a fill: all of it goes. The
# palette Chromium emulates follows the colour scheme, and the light one is where a white tick lands
# on a white box, so these run on it, for an account of each theme.

FORCED = {"forced_colors": "active", "color_scheme": "light"}


@pytest.fixture
def open_in(browser, served):
    """open_in(theme, path, ready=None, **media) -> a page of a fresh context of that theme's account,
    with the media features asked for emulated (forced_colors, reduced_motion, contrast, color_scheme)."""
    base, cookies = served
    opened = []

    def _open(theme, path, ready=None, **media):
        context = browser.new_context(viewport=VIEWPORT, **media)
        opened.append(context)
        context.add_cookies([cookies[theme]])
        page = context.new_page()
        page.set_default_timeout(SCRIPT_TIMEOUT_MS)
        page.goto(base + path, wait_until="load")
        settle(page, ready)
        return page

    yield _open
    for context in opened:
        context.close()


def picture_of(page, locator, pad=0, inset=0):
    """The pixels of an element as drawn, `pad` px around it, or only its middle with `inset` (a
    fraction of each side) cut off."""
    box = locator.bounding_box()
    x, y, width, height = box["x"] - pad, box["y"] - pad, box["width"] + 2 * pad, box["height"] + 2 * pad
    clip = {"x": x + width * inset, "y": y + height * inset, "width": width * (1 - 2 * inset), "height": height * (1 - 2 * inset)}
    return Image.open(io.BytesIO(page.screenshot(clip=clip))).convert("RGB")


def pixels_apart(first, second):
    """How many pixels of two pictures of one size differ, past antialiasing."""
    red, green, blue = ImageChops.difference(first, second).split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda value: 255 if value > 48 else 0).histogram()[255]


def pixels_off(picture, ground):
    """How many pixels of a picture are not the (r, g, b) ground."""
    return pixels_apart(picture, Image.new("RGB", picture.size, ground))


def canvas_of(page):
    return tuple(round(channel) for channel in as_rgb(page.evaluate("getComputedStyle(document.documentElement).backgroundColor")))


def keyboard_focus(page, locator):
    """Focus an element the way the keyboard does, so that :focus-visible is what it matches."""
    page.keyboard.press("Shift")
    locator.evaluate("(element) => element.focus()")
    calm(page)
    assert locator.evaluate("(element) => element.matches(':focus-visible')"), "the focus did not count as the keyboard's"


#: (what it is, the page it is on, a box that is ticked, one that is not)
CHOICES = [
    ("a ticked search type", "/explore/search?query=citadel&type=sample&type=family", "#families_checkbox", "#functions_checkbox"),
    ("the chosen kind of upload", "/data/submit", "#unmapped", "#dumped"),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("what, path, ticked, empty", CHOICES, ids=[choice[0] for choice in CHOICES])
def test_in_forced_colours_a_ticked_box_does_not_look_like_an_empty_one(open_in, theme, what, path, ticked, empty):
    """Bootstrap turns the native box off (appearance: none) and draws the tick as a white image on a
    blue fill. Forced colours paint the fill in the canvas colour and keep the image: white on white,
    so the search types, the kind of upload and the theme a user had chosen all looked unchosen."""
    page = open_in(theme, path, **FORCED)
    ticked, empty = page.locator(ticked), page.locator(empty)
    assert ticked.is_checked() and not empty.is_checked(), "the page does not start the way the test needs"

    assert ticked.evaluate("(box) => getComputedStyle(box).appearance") != "none"
    assert pixels_apart(picture_of(page, ticked, inset=0.25), picture_of(page, empty, inset=0.25)) >= 4, f"{what}: the middle of a ticked box is drawn like an empty one's"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_in_forced_colours_the_minhash_slider_is_drawn(open_in, theme):
    """The slider's track and thumb are background colours, which forced colours turn into the canvas:
    the whole control vanished, and with it the setting it shows."""
    page = open_in(theme, "/analyze/compare", "tr.parent_table_sample", **FORCED)
    slider = page.locator("#minhashBandRange")

    assert pixels_off(picture_of(page, slider), canvas_of(page)) >= 50


#: (what it is, the page it is on, the element): one of each kind of control whose focus Bootstrap
#: draws as a box shadow, which forced colours drop
FORCED_FOCUS = [
    ("a row's action button", "/explore/samples", "td a.btn"),
    ("a menu button", "/explore/samples", "button.btn.dropdown-toggle"),
    ("the current page", "/explore/samples", ".page-item.active .page-link"),
    ("another page", "/explore/functions", ".page-item:not(.active):not(.disabled) a.page-link"),
    ("the rows-per-page menu", "/explore/samples", "select.page-link"),
    ("a search field", "/explore/samples", "input.form-control"),
    ("a checkbox", "/explore/search?query=citadel", "#functions_checkbox"),
    ("a radio button", "/data/submit", "#dumped"),
    ("an accordion header", f"/explore/functions/{FUNCTION_A}", ".accordion-button"),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("what, path, selector", FORCED_FOCUS, ids=[control[0] for control in FORCED_FOCUS])
def test_in_forced_colours_the_keyboard_focus_is_drawn(open_in, theme, what, path, selector):
    """Bootstrap sets `outline: 0` and draws focus as a box shadow; forced colours drop the shadow, so
    on these controls a keyboard user could not see where they were. An outline is drawn instead."""
    page = open_in(theme, path, **FORCED)
    element = page.locator(f"{selector}:visible").first
    assert element.count() == 1, f"{path} has no {what} ({selector})"
    element.scroll_into_view_if_needed()
    page.evaluate("document.activeElement && document.activeElement.blur()")
    calm(page)
    before = picture_of(page, element, pad=8)

    keyboard_focus(page, element)
    outline = element.evaluate("(element) => [getComputedStyle(element).outlineStyle, parseFloat(getComputedStyle(element).outlineWidth)]")
    after = picture_of(page, element, pad=8)

    assert outline[0] not in ("none", "hidden") and outline[1] >= 2, f"{what} in focus: outline {outline}"
    assert pixels_apart(before, after) >= 20, f"{what}: focusing it changes nothing on the screen"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(
    "what, path, ready, selector",
    [
        ("a square of the cross compare matrix", f"/data/result/{RESULT['cross_compare']}", ".score-swatch", ".score-swatch"),
        ("a swatch of the CFG legend", f"/data/matches/function/{FUNCTION_A}/{FUNCTION_B}", "#graphContainer_a g.node", ".cfg-legend span.swatch"),
    ],
    ids=["matrix", "legend"],
)
def test_in_forced_colours_a_colour_that_is_data_keeps_its_colour(open_in, theme, what, path, ready, selector):
    """The matrix squares are nothing but their colour, and the legend says which colour of the graph
    means what (the graph keeps its colours, SVG fills are not forced). Forced colours painted both in
    the canvas colour: a blank matrix, and a legend of identical empty boxes."""
    page = open_in(theme, path, ready, **FORCED)
    swatches = page.locator(f"{selector}:visible")
    assert swatches.count() >= 3, f"{what}: hardly any on the page"

    found = swatches.evaluate_all("(swatches) => swatches.map((swatch) => [getComputedStyle(swatch).forcedColorAdjust, getComputedStyle(swatch).backgroundColor])")
    assert all(adjust == "none" for adjust, _colour in found), found
    assert len({colour for _adjust, colour in found}) >= 2, f"{what}: every one is {found[0][1]}"
    assert all(tuple(round(channel) for channel in as_rgb(colour)) != canvas_of(page) for _adjust, colour in found), found


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(
    "what, path, current, other",
    [
        ("the current tab", "/settings", ".nav-pills .nav-link.active", ".nav-pills .nav-link:not(.active)"),
        ("the current page", "/explore/functions", ".page-item.active .page-link", ".page-item:not(.active):not(.disabled) .page-link"),
    ],
    ids=["tab", "page"],
)
def test_in_forced_colours_the_current_tab_and_page_are_marked(open_in, theme, what, path, current, other):
    """Both were told apart by their fill alone (and in the dark theme an underline drawn as a box
    shadow), which forced colours replace: six identical tab labels, a row of identical page numbers.
    The mark is an outline, and the keyboard's focus is one too, so focusing the current one has to
    show as well."""
    page = open_in(theme, path, **FORCED)
    current, other = page.locator(f"{current}:visible").first, page.locator(f"{other}:visible").first

    assert current.evaluate("(element) => getComputedStyle(element).outlineStyle") not in ("none", "hidden"), f"{what} is not marked"
    assert other.evaluate("(element) => getComputedStyle(element).outlineStyle") in ("none", "hidden"), f"the others are marked like {what}"
    current.scroll_into_view_if_needed()
    page.evaluate("document.activeElement && document.activeElement.blur()")
    calm(page)
    before = picture_of(page, current, pad=8)
    keyboard_focus(page, current)
    assert pixels_apart(before, picture_of(page, current, pad=8)) >= 20, f"focusing {what} changes nothing on the screen"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_in_forced_colours_a_page_link_that_leads_nowhere_looks_it(open_in, theme):
    """On the first page, "First page" and "Previous page" go nowhere; forced colours drew them in the
    link colour like the others."""
    page = open_in(theme, "/explore/functions", **FORCED)
    colour = "(element) => getComputedStyle(element).color"

    assert page.locator(".page-item.disabled .page-link").first.evaluate(colour) != page.locator(".page-item:not(.active):not(.disabled) a.page-link").first.evaluate(colour)


# --- the light theme's own contrast ------------------------------------------------------------
#
# The light pages keep master's colours (testTheme.py pins them) except where a pair of them was
# measured short of WCAG: those changes are named in testTheme.py's LIGHT_CHANGES, and measured on
# the pages here.

#: (what it is, the page it is on, the element) whose edge is all that shows where it is
LIGHT_EDGES = [
    ("a field", "/admin/server", "#mcrit_server_url"),
    ("a search field", "/explore/samples", "input.form-control"),
    ("a checkbox", "/explore/search?query=citadel", "#functions_checkbox"),
    ("a radio button", "/data/submit", "#dumped"),
]


@pytest.mark.parametrize("what, path, selector", LIGHT_EDGES, ids=[control[0] for control in LIGHT_EDGES])
def test_in_the_light_theme_the_edge_of_a_field_or_a_box_holds_3_to_1(open_in, what, path, selector):
    """WCAG 1.4.11. Bootstrap's grey edge was 1.5:1 on the page (a check box's 1.8:1): an empty field,
    an unticked box, showed hardly at all."""
    page = open_in("light", path)
    element = page.locator(f"{selector}:visible").first
    assert element.count() == 1, f"{path} has no {what} ({selector})"

    found = element.evaluate("(element) => {" + HELPERS + """
      const behind = background(element.parentElement).rgb, own = background(element).rgb;
      const edge = over(parse(getComputedStyle(element).borderTopColor), behind);
      return [ratio(edge, behind), ratio(edge, own)];
    }""")

    assert min(found) >= 3, f"{what}: its edge is {found[0]:.2f}:1 against the page and {found[1]:.2f}:1 against its own fill"


#: How visible the ring or the outline of the element in focus is, laid over what is behind it. Unlike
#: FOCUS_INDICATOR_OF, the border is left out: here it is there before the focus as well.
FOCUS_RING_OF = (
    "(element) => {" + HELPERS + """
  const behind = background(element.parentElement).rgb;
  const style = getComputedStyle(element);
  const found = { ring: null, outline: null, behind: behind.map(Math.round) };
  const ring = style.boxShadow.match(/(rgba?\\([^)]*\\)|color\\(srgb[^)]*\\))\\s+0px\\s+0px\\s+0px\\s+([\\d.]+)px(?!\\s+inset)/);
  if (ring && parseFloat(ring[2]) >= 2) found.ring = Math.round(ratio(over(parse(ring[1]), behind), behind) * 100) / 100;
  if (!["none", "hidden"].includes(style.outlineStyle) && parseFloat(style.outlineWidth) >= 2) found.outline = Math.round(ratio(over(parse(style.outlineColor), behind), behind) * 100) / 100;
  return found;
}"""
)

#: (what it is, the page it is on, the element): one of each kind of control the light pages focus
LIGHT_FOCUS = [
    ("a page link", "/explore/functions", ".page-item:not(.active):not(.disabled) a.page-link"),
    ("the rows-per-page menu", "/explore/samples", "select.page-link"),
    ("an outline button", "/explore/samples", ".btn-outline-dark"),
    ("a primary button", "/admin/server", ".btn-primary"),
    ("a field", "/admin/server", "#mcrit_server_url"),
    ("a search field without the ring", "/explore/samples", "input.form-control.shadow-none"),
    ("a radio button", "/data/submit", "#dumped"),
    ("a checkbox without the ring", "/explore/search?query=citadel", "#functions_checkbox"),
    ("an accordion header", f"/explore/functions/{FUNCTION_A}", ".accordion-button"),
]


@pytest.mark.parametrize("what, path, selector", LIGHT_FOCUS, ids=[control[0] for control in LIGHT_FOCUS])
def test_in_the_light_theme_the_keyboards_focus_holds_3_to_1_against_what_is_behind_the_control(open_in, what, path, selector):
    """WCAG 1.4.11 again, for what shows where the keyboard is. Bootstrap's ring was 1.4:1 on the
    light page; the search boxes and check boxes that turn it off (`.shadow-none`) showed a 1px
    border going a paler blue, 2.1:1, and five pages open with the focus in such a box."""
    page = open_in("light", path)
    element = page.locator(f"{selector}:visible").first
    assert element.count() == 1, f"{path} has no {what} ({selector})"
    element.scroll_into_view_if_needed()

    keyboard_focus(page, element)
    found = element.evaluate(FOCUS_RING_OF)

    best = max([value for value in (found["ring"], found["outline"]) if value is not None], default=0)
    assert best >= 3, f"{what} in focus: ring {found['ring']}:1, outline {found['outline']}:1 against rgb{tuple(found['behind'])}"


def test_in_the_light_theme_the_current_page_of_the_pager_is_marked_at_3_to_1(open_in):
    """The current page was told from the others by a grey fill alone, #f0f0f0 on the white page:
    1.14:1, a state no one with low vision could see. It is underlined in the accent, as in dark."""
    page = open_in("light", "/explore/functions")
    current = page.locator(".page-item.active .page-link:visible").first
    assert current.count() == 1, "the functions page has no current page in its pager"

    found = current.evaluate("(element) => {" + HELPERS + """
      const mark = getComputedStyle(element).boxShadow.match(/(rgba?\\([^)]*\\))\\s+0px\\s+-?[\\d.]+px\\s+0px(?:\\s+0px)?\\s+inset/);
      if (!mark) return null;
      const own = background(element).rgb, page = background(element.parentElement).rgb;
      return [ratio(over(parse(mark[1]), own), own), ratio(over(parse(mark[1]), own), page)];
    }""")

    assert found is not None, "the current page has no underline"
    assert min(found) >= 3, f"its underline is {found[0]:.2f}:1 against its own fill and {found[1]:.2f}:1 against the page"


def test_in_the_light_theme_the_current_page_of_the_pager_shows_its_focus(open_in):
    """The underline is a box shadow, the property the focus ring is drawn with: one rule must not
    replace the other (test_the_current_page_of_the_pager_shows_its_focus, for dark)."""
    page = open_in("light", "/explore/functions")
    current = page.locator(".page-item.active .page-link:visible").first
    current.scroll_into_view_if_needed()

    keyboard_focus(page, current)
    found = current.evaluate(FOCUS_RING_OF)

    assert found["ring"] is not None and found["ring"] >= 3, f"the current page in focus: {found}"


def walked_in_light(walker, name):
    """The probe's report on one of the WALKED pages under the light theme."""
    if name in PAGES:
        path, ready = PAGES[name]
        return walker.walk(f"light: {name}", path, ready, context="light")
    tab = name.removeprefix("settings, the ").removesuffix(" tab")
    return walker.walk(f"light: {name}", "/settings", tab=tab, context="light")


@pytest.mark.parametrize("name", WALKED)
def test_in_the_light_theme_every_visible_text_node_and_icon_has_its_contrast(walker, name):
    """The walk of the dark pages, over the light ones: 4.5:1 for text and 3:1 for icons. Two pairs
    were short there. The tick of a yes/no column was `green`, 2.5:1 on the darker score tints; and
    the job number's link in the head of the jobs list was the accent on the grey Bootstrap paints a
    `.table-light` cell with, 4.4:1."""
    report = walked_in_light(walker, name)

    assert report["present"] and report["counts"]["judged"] >= 4, f"{name}: the page did not draw what the walk judges, {report['counts']}"
    assert report["errors"] == [] and report["failed"] == [], f"{name}: {report['errors']} {report['failed']}"
    assert report["text"] == [], f"{name}: text below 4.5:1:\n" + listed(report["text"])
    assert report["fields"] == [], f"{name}: field values or placeholders below 4.5:1:\n" + listed(report["fields"])
    assert report["icons"] == [], f"{name}: icons below 3:1:\n" + listed(report["icons"])


#: The blocks "Show Cycles" colours, each block's text against its fill as it is drawn: the fill at
#: its own fill-opacity over what is behind the graph.
CYCLE_TEXT_CONTRAST = (
    "() => {" + HELPERS + """
  return [...document.querySelectorAll('g.node[fill="white"]')].map((node) => {
    const rect = node.querySelector('rect'), text = node.querySelector('text');
    const behind = background(node.closest('svg')).rgb;
    const fill = parse(getComputedStyle(rect).fill);
    const shown = over([fill[0], fill[1], fill[2], fill[3] * parseFloat(getComputedStyle(rect).fillOpacity)], behind);
    const ink = over(parse(getComputedStyle(text).fill), shown);
    return { ratio: Math.round(ratio(ink, shown) * 100) / 100, fill: fill.slice(0, 3).map(Math.round) };
  });
}"""
)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(
    "path, ready, button",
    [
        (f"/explore/functions/{FUNCTION_A}", "#graphContainer g.node", "#showCycles"),
        (f"/data/matches/function/{FUNCTION_A}/{FUNCTION_B}", "#graphContainer_a g.node", "#showCycles"),
    ],
    ids=["function", "function vs function"],
)
def test_the_blocks_show_cycles_colours_keep_their_text_readable(open_in, theme, path, ready, button):
    """The viewer paints the blocks of each cycle in one of sixteen saturated colours and turns their
    text white (fill="white" on the block, main.js and main_duo.js). In the light theme that was
    2.1:1 on the orange, and black text would be 1.8:1 on the darkest; the dark theme already shows
    the colours at 35% under its own text colour."""
    page = open_in(theme, path, ready)
    page.wait_for_function(f"!document.querySelector('{button}').disabled")

    page.click(button)
    page.wait_for_selector('g.node[fill="white"]', state="attached")
    found = page.evaluate(CYCLE_TEXT_CONTRAST)

    assert len(found) >= 3, f"hardly any block was coloured: {found}"
    worst = min(found, key=lambda block: block["ratio"])
    assert worst["ratio"] >= 4.5, f"block text at {worst['ratio']}:1 on rgb{tuple(worst['fill'])}"


def test_a_file_waiting_to_be_uploaded_is_drawn_on_the_dark_palette(open_in):
    """The walk sees an empty drop zone. Once a file is in it, Dropzone shows its progress in a
    capsule it paints 90% white (dropzone.min.css), which theme-dark.css's Dropzone rules did not
    reach: a bright bar in the dark preview card of every upload that waits for "Submit"."""
    page = open_in("dark", "/data/submit", ".dropzone")
    page.wait_for_function("window.Dropzone !== undefined && document.getElementById('myDropzone').dropzone !== undefined")

    page.evaluate("Dropzone.forElement('#myDropzone').addFile(new File(['MZ'], 'waiting.exe'))")
    page.wait_for_selector(".dropzone .dz-preview .dz-progress")
    calm(page)
    report = run_probe(page)

    assert page.locator(".dropzone .dz-preview .dz-progress").is_visible()
    assert report["light"] == [], "elements whose background is lighter than 0.4:\n" + listed(report["light"])
    assert report["text"] == [], "text below 4.5:1:\n" + listed(report["text"])
