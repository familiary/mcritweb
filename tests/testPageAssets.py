#!/usr/bin/python
"""What base.html makes every page download, and why.

Issue #63: "even if js is cached, it is still always renderblocking", and its first
bullet, "remove data-tables js/css once it is not needed anymore".

Two libraries in base.html were paid for by every page in the app:

  jquery-ui.js                529 KB  }  623 KB of render-blocking JavaScript
  jquery.dataTables.min.js     90 KB  }  and 41 KB of CSS, everywhere
  dataTables.bootstrap5.min.js  4 KB  }
  jquery-ui.css                37 KB
  dataTables.bootstrap5.min.css 4 KB

jQuery UI served exactly one `.sortable()` call, on the cross compare result page.
DataTables served exactly one `.DataTable()` call, on the jobs page - against the
selector `#job-table`, which *that page* never renders: it builds its table with
`table_id=active`, the job method. The id itself is not dead - `job_table` still
defaults to it and job_overview and single_sample take the default - but no page that
renders it ever ran an initialiser, so DataTables styled nothing anywhere.

Total bytes per page, document plus every /static/ file it references, served over
HTTP against the captured corpus:

    page                     before     after
    /                       1621593    935520
    /explore/samples        1650077    964004
    /data/jobs              1637892    951517
    /explore/samples/1      1619030    932957
    /data/result/<cross>    1720977   1621889   (keeps jQuery UI, loses DataTables)

Render-blocking bytes in <head> go from 1234797 to 549016. Behaviour is identical on
both: `.sortable()` still applies on the cross compare, the clipboard tooltips still
get Bootstrap instances rather than jQuery UI's, no console errors anywhere. See
docs/adr/0015-the-rest-of-63-is-gzip-in-the-proxy.md for the rest of #63's checklist.
"""

import logging
import pathlib
import re
import unittest

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

TEMPLATE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "mcritweb" / "templates"
STATIC_ROOT = TEMPLATE_ROOT.parent / "static"
BASE = TEMPLATE_ROOT / "base.html"

#: assets base.html must not load, and where they belong instead
PAGE_SPECIFIC = {
    "jquery-ui.js": "result_cross.html",
    "jquery-ui.css": "result_cross.html",
}

#: assets no template should load at all any more. dropzone.min.js is stock 5.9.2,
#: without the patch in dropzone.js that sends the head of a dropped file for the
#: pre-fill (AGENTS.md); all.css and bootstrap.css are the full builds the subset and
#: the minified file replaced (issue #63)
RETIRED = ["jquery.dataTables.min.js", "dataTables.bootstrap5.min.js", "dataTables.bootstrap5.min.css", "dropzone.min.js", "css/all.css", "css/bootstrap.css"]


@pytest.mark.parametrize("asset", sorted(PAGE_SPECIFIC))
def test_a_page_specific_library_is_not_loaded_by_every_page(asset):
    assert asset not in code_of(BASE), f"base.html makes every page download {asset}"


@pytest.mark.parametrize("asset", sorted(PAGE_SPECIFIC))
def test_it_is_loaded_by_the_page_that_needs_it(asset):
    owner = TEMPLATE_ROOT / PAGE_SPECIFIC[asset]
    assert asset in code_of(owner), f"{owner.name} calls into {asset} but does not load it"


JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def code_of(path):
    """The template with its comments stripped, Jinja and HTML alike.

    These tests look for *calls*, and the comments explaining why a library was moved
    naturally name the call they are about - so scanning the raw text would find the
    prose and report the library as still in use. The HTML kind matters too: both CFG
    templates carry a commented-out second jQuery.
    """
    return HTML_COMMENT.sub("", JINJA_COMMENT.sub("", path.read_text()))


def templates_containing(needle):
    return {path.name for path in TEMPLATE_ROOT.rglob("*.html") if needle in code_of(path)}


@pytest.mark.parametrize("asset", ["jquery.dataTables.min.js", "dataTables.bootstrap5.min.js", "dataTables.bootstrap5.min.css"])
def test_datatables_is_not_shipped(asset):
    """Nothing loads DataTables any more, and unlike all.css or the full Bootstrap build it
    is no subset's source, so the files went with it (#63's first item)."""
    assert not (STATIC_ROOT / asset).exists()


@pytest.mark.parametrize("asset", RETIRED)
def test_a_retired_library_is_not_loaded_anywhere(asset):
    """DataTables' one initialiser ran on a page that does not render its selector."""
    loaded_by = templates_containing(asset)
    assert loaded_by == set(), f"{asset} is back, in {sorted(loaded_by)}"


def test_nothing_calls_datatables_any_more():
    callers = templates_containing(".DataTable(")
    assert callers == set(), f"{sorted(callers)} calls .DataTable() but the library is no longer loaded"


JOBS_PAGES = ("/data/jobs", "/data/jobs?state=finished", "/data/jobs?active=getMatchesForSample")


def test_the_sortable_call_and_its_library_are_on_the_same_page():
    """The one thing jQuery UI is still here for. If this call moves, the library has
    to move with it - or come out."""
    callers = templates_containing(".sortable(")
    assert callers == {"result_cross.html"}, f"jQuery UI is loaded by result_cross.html but called from {sorted(callers)}"


def test_jquery_itself_stays_in_base():
    """It is not page-specific: most of the inline blocks across the tree call `$`,
    including one in base.html itself. Moving it is issue #63's "consider removing
    jquery", answered no in ADR-0015."""
    assert "jquery.js" in code_of(BASE)


def test_only_base_html_loads_a_jquery():
    """A second jQuery replaces `window.$` after Bootstrap has attached its plugins to
    the first, so every `$.fn.*` the page already reached for comes back undefined.
    Both CFG templates carry such an include, commented out - keep it that way."""
    loaders = {path.name for path in TEMPLATE_ROOT.rglob("*.html")
               if re.search(r"filename='[^']*jquery(?!-ui)[^']*\.js'", code_of(path))}
    assert loaders == {"base.html"}, f"more than one template loads a jQuery build: {sorted(loaders)}"


#: a script base.html's queue loads after the first paint: `later({{ url_for(...)|tojson }})`
#: in a template, `later("/static/...")` in a page
QUEUED = re.compile(r"""later\((?:\{\{\s*url_for\('static',\s*filename='([^']+)'\)\|tojson\s*\}\}|"/static/([^"]+)")\)""")


def queued(code):
    """The static files `code` queues with later(), in order."""
    return [template or page for template, page in QUEUED.findall(code)]


def test_bootstrap_is_queued_and_never_loaded_on_demand():
    """What decides that `$.fn.tooltip` is Bootstrap's and not jQuery UI's.

    Bootstrap 5.0.2 defines its jQuery plugins when it executes, or on DOMContentLoaded
    if it executes earlier. The queue runs its scripts in the order they were queued,
    after DOMContentLoaded, so Bootstrap registers last - and wins the name - as long as
    it is queued after every other script that defines jQuery plugins: jQuery UI on the
    cross compare, queued from {% block style %}, which is why base.html queues Bootstrap
    below that block (test_bootstrap_comes_after_the_page_libraries). A script tag of its
    own, or loading it on demand after the queue has run, would let jQuery UI register
    after it. See ADR-0015, ADR-0018, ADR-0021 and issue #63.
    """
    code = code_of(BASE)
    assert "bootstrap-5.0.2-dist/js/bootstrap.bundle.min.js" in queued(code), "base.html no longer queues Bootstrap's bundle"
    assert re.search(r"<script[^>]*bootstrap\.bundle\.min\.js", code) is None, "Bootstrap has a script tag of its own"


def test_bootstrap_comes_after_the_page_libraries():
    """...and after jQuery, whose `$.fn` it registers its plugins on."""
    code = code_of(BASE)
    bootstrap = code.index("bootstrap.bundle.min.js")
    assert code.index("jquery.js") < bootstrap, "Bootstrap runs before the jQuery it attaches its plugins to"
    assert re.search(r"{%\s*block style\s*%}", code).start() < bootstrap, \
        "a page's own deferred jQuery plugins (jQuery UI) would run after Bootstrap and take $.fn.tooltip from it"


def test_nothing_touches_bootstrap_while_the_page_parses():
    """The one construction that needs Bootstrap at once - `new Autocomplete`, which
    builds a Bootstrap dropdown - waits in whenReady, by which time the queued bundle has
    run. DOMContentLoaded is too early now: the queue starts loading after it. Measured:
    without the wait every page logged "bootstrap is not defined" from autocomplete.js."""
    for path in TEMPLATE_ROOT.rglob("*.html"):
        code = path.read_text()
        for match in re.finditer(r"new Autocomplete\(", code):
            before = code[max(0, match.start() - 600):match.start()]
            assert "whenReady(" in before and "DOMContentLoaded" not in before, f"{path.name} builds an Autocomplete before Bootstrap is there"


def test_jquery_is_queued_first():
    """It used to be the last script in <head> that held back the first paint: inline
    blocks called `$` while the page parsed, `$(document).ready(...)` being itself such a
    call. They hand that work to whenReady() now, and testParseTimeScripts.py fails on
    one that does not. It is the first script queued: every library after it extends it.
    See ADR-0018 and ADR-0021."""
    code = code_of(BASE)
    assert queued(code)[:1] == ["jquery.js"], queued(code)
    assert re.search(r"<script[^>]*jquery\.js", code) is None, "jQuery has a script tag of its own"


def test_when_ready_is_defined_before_any_other_script():
    """Every inline script may call it, so it has to exist before the first of them -
    and it cannot come from a file, which would have to be a plain script to be there
    in time."""
    code = code_of(BASE)
    first_script = re.search(r"<script\b[^>]*>(.*?)</script>", code, re.S)
    assert "src=" not in first_script.group(0), first_script.group(0)
    assert re.search(r"function whenReady\(fn\)", first_script.group(1)), "the first script in base.html is not the one defining whenReady"
    assert "jQuery(fn)" in first_script.group(1), "whenReady no longer hands its function to jQuery's ready queue"


def test_the_listener_only_scripts_are_queued():
    """Nothing calls these before the queue has run: page_loading.js and post_action.js
    only register event listeners, and every `new Autocomplete` waits in whenReady
    (test_nothing_touches_bootstrap_while_the_page_parses). As plain scripts in `<head>`
    they only delayed first paint."""
    code = code_of(BASE)
    for asset in ("autocomplete.js", "page_loading.js", "post_action.js"):
        assert asset in queued(code), f"base.html no longer queues {asset}"
        assert re.search(r"<script[^>]*" + re.escape(asset), code) is None, f"{asset} has a script tag of its own"


def test_no_template_defers_a_script():
    """A deferred script runs before DOMContentLoaded, so before the queue: one that
    extends jQuery would find no jQuery, and one that does not would hold back the
    queue's own first paint for nothing."""
    deferred = sorted(path.name for path in TEMPLATE_ROOT.rglob("*.html") if re.search(r"<script\b[^>]*\sdefer\b", code_of(path)))
    assert deferred == []


def test_the_pages_that_lost_a_library_still_render(client, as_role):
    as_role("visitor")
    for path in ("/", "/explore/samples", "/explore/families", "/data/jobs", "/settings"):
        assert client.get(path).status_code == 200, path


def test_no_page_references_an_asset_that_is_not_shipped():
    """A ratchet with a wider reach than this change: a moved or renamed asset leaves a
    404 that nothing else in the suite notices, because a missing script does not stop a
    page returning 200."""
    static_root = TEMPLATE_ROOT.parent / "static"
    referenced = set()
    for path in TEMPLATE_ROOT.rglob("*.html"):
        referenced |= set(re.findall(r"filename='([^']+)'", path.read_text()))
        referenced |= set(re.findall(r'filename="([^"]+)"', path.read_text()))

    missing = sorted(name for name in referenced if not (static_root / name).exists())
    assert missing == [], f"templates reference static files that do not exist: {missing}"


class TestTheJobsPage:
    """Why dropping DataTables was safe, checked rather than asserted in a comment.

    `job_table` still defaults to `table_id="job-table"` and job_overview and
    single_sample take that default, so the id is not gone from the application. What
    made DataTables dead is narrower: the one page that carried the initialiser passes
    `table_id=active` - the job method - so its selector matched nothing. Needs the
    captured corpus, because the menu categories only exist once the queue has jobs.
    """

    @pytest.fixture
    def fake_mcrit(self, corpus_mcrit):
        return corpus_mcrit

    @pytest.mark.parametrize("path", JOBS_PAGES)
    def test_it_does_not_render_the_id_datatables_selected(self, client, as_role, path):
        as_role("visitor")
        # /data/jobs redirects to the URL naming its first tab (issue #36); check the page it lands on
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200, path
        assert b'id="job-table"' not in response.data, f"{path} renders the selector DataTables used"


class TestTheCrossComparePage:
    """The page that kept jQuery UI, rendered against a real captured report.

    Reading the template only proves the tag was written; these prove it reaches the
    browser, and in an order jQuery UI can survive - it is a plugin, so `jquery.js` has
    to have run first. That holds only because base.html loads jQuery *above*
    `{% block style %}`, and Bootstrap below it, which is not obvious from either file
    on its own.
    """

    @pytest.fixture
    def fake_mcrit(self, corpus_mcrit):
        return corpus_mcrit

    def rendered(self, client, as_role):
        from fixtureData import job_id_of
        as_role("visitor")
        response = client.get(f"/data/result/{job_id_of('cross_compare')}")
        assert response.status_code == 200
        return response.data.decode()

    def test_it_ships_the_library_it_calls(self, client, as_role):
        page = self.rendered(client, as_role)
        assert "jquery-ui.js" in page
        assert "jquery-ui.css" in page
        assert ".sortable()" in page

    def test_jquery_reaches_the_page_before_jquery_ui(self, client, as_role):
        page = self.rendered(client, as_role)
        assert page.index("jquery.js") < page.index("jquery-ui.js"), \
            "jQuery UI is loaded before the jQuery it extends"

    def test_jquery_ui_runs_between_jquery_and_bootstrap(self, client, as_role):
        """All three are queued, so they run in the order they were queued: jQuery UI
        after the jQuery it extends, and before the Bootstrap that has to take
        `$.fn.tooltip` and `$.fn.button` back from it."""
        order = queued(self.rendered(client, as_role))
        assert {"jquery.js", "jquery-ui.js", "bootstrap-5.0.2-dist/js/bootstrap.bundle.min.js"} <= set(order), order
        assert order.index("jquery.js") < order.index("jquery-ui.js") < order.index("bootstrap-5.0.2-dist/js/bootstrap.bundle.min.js")

    def test_no_other_page_ships_it(self, client, as_role):
        as_role("visitor")
        for path in ("/", "/explore/samples", "/data/jobs", "/settings"):
            assert "jquery-ui" not in client.get(path).data.decode(), path


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize("name", ["single_function.html", "result_compare_function_vs.html"])
def test_the_graph_libraries_stay_out_of_the_head(name):
    """d3, dagre-d3 and graphlib are only used by the CFG scripts at the end of the page
    and by window.onload; loaded from the style block they blocked the first paint."""
    code = code_of(TEMPLATE_ROOT / name)
    style = re.search(r"{%\s*block style\s*%}(.*?){%\s*endblock\s*%}", code, re.S).group(1)
    assert "trace_CFG/lib/" not in style
    first_graph_script = code.index("trace_CFG/main")
    for lib in ("d3.min.js", "graphlib.min.js", "graphlib-dot.min.js", "dagre-d3.min.js", "convexhull.js"):
        assert -1 < code.find("trace_CFG/lib/" + lib) < first_graph_script, lib
