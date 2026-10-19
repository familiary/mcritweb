#!/usr/bin/python
"""A cross compare cell's tooltip is put together in the browser, from its row and column.

Every cell of every matrix on the cross compare result page used to carry its whole
tooltip: the score line, and then both of its samples - id, sha256 prefix, family,
version and function count. An n*n matrix held 2*n*n sample descriptions, six matrices to
a page, for tooltips most cells never show (issue #198). The samples' part is the same
along a whole row and down a whole column, so it is now written once per row, and a
script in result_cross.html puts a cell's tooltip together the first time the pointer
reaches it.

What a user sees on hover has to stay what it was, character for character: hint.css
shows the tooltip with `white-space: pre`, so the line breaks and the indentation the
template used to write into the attribute are part of it. The tests up to the browser
ones check what the page hands the browser. The browser tests hover every cell in
Chromium and read the tooltip back; `playwright` is not a dependency of this project and
CI does not install it, so they skip there rather than failing.
"""

import logging
import os
import threading
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of, load
from mcrit.storage.SampleEntry import SampleEntry
from werkzeug.serving import make_server

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: A family and a version that the attribute has to escape and the browser has to decode
#: again: markup, both quotes, an ampersand, a backslash (which the CSS serialisation of
#: the tooltip escapes) and something outside ASCII.
AWKWARD_FAMILY = 'R&D <b>"x"</b> \'y\' \\ Zürich'
AWKWARD_VERSION = '1.0 & "2"'


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.fixture
def report():
    return load("cross_compare.result")


def samples_of(report):
    """The samples of the captured cross compare, by id as the report spells it."""
    return {sample_id: SampleEntry.fromDict(load("samples")[sample_id]) for sample_id in report["unweighted"]["clustered_sequence"]}


def score_line(percent, matches):
    """The first line of a cell's tooltip, the part that differs from cell to cell."""
    return "MCRIT: %.2f%% (%s matches) " % (percent, matches)


def sample_line(sample):
    """How a cell's tooltip names one of its two samples."""
    return "%s: %s -- %s %s -- (%s func)" % (sample.sample_id, sample.sha256[:8], sample.family, sample.version, sample.statistics["num_functions"])


def tooltip(percent, matches, sample, other):
    """A cell's whole tooltip, as the template used to write it into every cell: its
    `&#10;`s, and its own line breaks and indentation, which hint.css shows as they are."""
    return score_line(percent, matches) + "\n\n            %s\n            \nvs.\n\n            %s" % (sample_line(sample), sample_line(other))


def css_string(text):
    """`text` serialised as a CSS string, which is how getComputedStyle reports the
    content of the ::after that hint.css shows a tooltip in."""
    serialised = []
    for char in text:
        if char == "\0":
            serialised.append("\ufffd")
        elif "\x01" <= char <= "\x1f" or char == "\x7f":
            serialised.append("\\%x " % ord(char))
        elif char in '"\\':
            serialised.append("\\" + char)
        else:
            serialised.append(char)
    return '"%s"' % "".join(serialised)


def _page(client, as_role):
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('cross_compare')}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


class _Matrices(HTMLParser):
    """The matrices of the page as {method: [{"sample_line", "hints"}, ...]}, a row each."""

    def __init__(self):
        super().__init__()
        self.matrices = {}
        self._rows = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and "tab-pane" in (attrs.get("class") or "").split():
            self._rows = self.matrices.setdefault(attrs["id"][len("pills-"):], [])
        elif tag == "tr" and "sample-row" in (attrs.get("class") or "").split():
            self._rows.append({"sample_line": attrs.get("data-hint-sample"), "hints": []})
        elif tag == "span" and "data-hint" in attrs and self._rows:
            self._rows[-1]["hints"].append(attrs["data-hint"])


def rendered_matrices(html):
    parser = _Matrices()
    parser.feed(html)
    matrices = {method: rows for method, rows in parser.matrices.items() if rows}
    assert matrices, "the page renders no matrix"
    return matrices


def test_a_cell_carries_only_its_score_line(client, as_role, report):
    """The structural half of #198: nothing about either sample is left in a cell."""
    for method, rows in rendered_matrices(_page(client, as_role)).items():
        order = report[method]["clustered_sequence"]
        percent = report[method]["matching_percent"]
        matches = report[method]["matching_matches"]
        assert len(rows) == len(order)
        for row_id, row in zip(order, rows):
            assert row["hints"] == [score_line(percent[row_id][col_id], matches[row_id][col_id]) for col_id in order]


def test_a_row_carries_the_line_naming_its_sample(client, as_role, report):
    samples = samples_of(report)
    for method, rows in rendered_matrices(_page(client, as_role)).items():
        order = report[method]["clustered_sequence"]
        assert [row["sample_line"] for row in rows] == [sample_line(samples[sample_id]) for sample_id in order]


def test_a_row_escapes_what_it_names(client, as_role, fake_mcrit):
    """Family and version come from whoever submitted the sample, and now travel in an
    attribute of their own. Parsing it back has to give the text, not markup."""
    fake_mcrit._samples[4].family = AWKWARD_FAMILY
    fake_mcrit._samples[4].version = AWKWARD_VERSION
    html = _page(client, as_role)

    assert AWKWARD_FAMILY not in html
    for rows in rendered_matrices(html).values():
        assert sample_line(fake_mcrit._samples[4]) in [row["sample_line"] for row in rows]


# --- in a browser ------------------------------------------------------------------

#: The page's own timeout. Generous, because it is only ever paid in full when a test is
#: about to fail.
SCRIPT_TIMEOUT_MS = 20000

#: Where the pointer has to go to be over each cell of one matrix, in the order of the
#: rows and their cells.
CELL_CENTRES = """
pane => Array.from(document.querySelectorAll('#' + pane + ' tr.sample-row span[data-hint]')).map(span => {
    const box = span.getBoundingClientRect();
    return [box.left + box.width / 2, box.top + box.height / 2];
})
"""

#: Every cell of one matrix, as the browser has it: the tooltip attribute and the content
#: hint.css gives the ::after that shows it, in the order of the rows and their cells.
READ_TOOLTIPS = """
pane => Array.from(document.querySelectorAll('#' + pane + ' tr.sample-row')).map(row =>
    Array.from(row.querySelectorAll('span[data-hint]')).map(span =>
        [span.getAttribute('data-hint'), getComputedStyle(span, '::after').content]))
"""


@pytest.fixture
def live_server(app):
    """The app under test on a loopback port, for the seconds a test needs it - as in
    testFunctionVsBrowser.py, which says why."""
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
def browser_page(app, live_server, make_user):
    """A Chromium page logged in as a visitor, on `live_server`."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    user_id = make_user(role="visitor")
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})

    with sync_api.sync_playwright() as playwright:
        try:
            # a Chromium that playwright did not install itself is named through MCRITWEB_CHROMIUM
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            # Bootstrap scrolls smoothly unless reduced motion is asked for, which would
            # move the cells while the pointer is being aimed at them
            context = browser.new_context(viewport={"width": 1400, "height": 900}, reduced_motion="reduce")
            context.add_cookies([{
                "name": app.config["SESSION_COOKIE_NAME"],
                "value": cookie,
                "domain": "127.0.0.1",
                "path": "/",
            }])
            page = context.new_page()
            page.set_default_timeout(SCRIPT_TIMEOUT_MS)
            yield page
        finally:
            browser.close()


@pytest.mark.parametrize("reverse", [False, True], ids=["clustered order", "custom order"])
def test_every_tooltip_reads_as_it_did(browser_page, live_server, fake_mcrit, report, reverse):
    """Hover every cell of every matrix, and read back what hint.css shows.

    The expected text is the tooltip as the template wrote it before #198, so this
    passes against that template as well - which is the point: the page got smaller,
    and no tooltip changed. One sample carries a family and version that need escaping.
    """
    fake_mcrit._samples[4].family = AWKWARD_FAMILY
    fake_mcrit._samples[4].version = AWKWARD_VERSION
    samples = {sample_id: fake_mcrit._samples[int(sample_id)] for sample_id in report["unweighted"]["clustered_sequence"]}
    errors = []
    browser_page.on("pageerror", lambda error: errors.append(str(error)))
    custom = list(reversed(report["unweighted"]["clustered_sequence"])) if reverse else None
    query = "?custom=" + ",".join(custom) if custom else ""
    browser_page.goto(f"{live_server}/data/result/{job_id_of('cross_compare')}{query}")

    # every tab is opened before any cell is hovered, so that no tab can be showing
    # tooltips another tab already put together
    for method in report:
        browser_page.click(f"#pills-{method}-tab")
    for method in report:
        browser_page.click(f"#pills-{method}-tab")
        table = browser_page.locator(f"#pills-{method} table")
        table.wait_for(state="visible")
        table.scroll_into_view_if_needed()
        # a real pointer, moved over one cell after another, as a user's would be
        for x, y in browser_page.evaluate(CELL_CENTRES, f"pills-{method}"):
            browser_page.mouse.move(x, y)

        order = custom or report[method]["clustered_sequence"]
        percent = report[method]["matching_percent"]
        matches = report[method]["matching_matches"]
        expected = [[tooltip(percent[row_id][col_id], matches[row_id][col_id], samples[row_id], samples[col_id]) for col_id in order] for row_id in order]
        seen = browser_page.evaluate(READ_TOOLTIPS, f"pills-{method}")
        assert [[hint for hint, _ in row] for row in seen] == expected, method
        assert [[content for _, content in row] for row in seen] == [[css_string(text) for text in row] for row in expected], method

    assert not errors
