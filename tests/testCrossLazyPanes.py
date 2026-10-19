#!/usr/bin/python
"""The cross compare result page renders only the matrix of the tab shown on load.

It used to render the full N*N matrix of all six matching methods and let Bootstrap hide
five of them, so the page grew with methods * N^2 whether a tab was ever clicked or not
(issue #182). The other tabs are now filled in the browser on first click, from a JSON
blob handed over with the page: the order of each method's matrix and, per cell in that
order, what the rendered matrix would have shown - the score to two decimals, the match
count and the colour. The browser clones everything else from the rendered matrix.

What can be checked offline is that the blob carries exactly what the eager render used
to put in each cell, and that the rendered matrix still has the shape the script at the
end of result_cross.html clones from. The script itself does not run here; when it was
written, a headless browser clicked every tab of a live cross compare and compared each
cell's text, tooltip, colour and link with the eager render of the previous version.
Change the script or the cross_table macro, and that comparison is worth repeating.
"""

import copy
import json
import logging
import random
import re
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of, load

from mcritweb.views.cross_compare import score_to_color

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

METHODS = ["unweighted", "score_weighted", "frequency_weighted", "nonlib_unweighted", "nonlib_score_weighted", "nonlib_frequency_weighted"]
RENDERED = "unweighted"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.fixture
def report():
    return load("cross_compare.result")


def _page(client, as_role, query=""):
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('cross_compare')}{query}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _lazy_panes(html):
    blob = re.search(r'<script type="application/json" id="cross-lazy-panes">(.*?)</script>', html, re.S)
    assert blob, "the page hands over no data for the tabs it does not render"
    return json.loads(blob.group(1))


def _pane(html, method):
    match = re.search(r'<div class="tab-pane[^"]*" id="pills-%s".*?(?=<div class="tab-pane|<script)' % method, html, re.S)
    assert match, f"no tab pane for {method}"
    return match.group(0)


def test_only_the_matrix_shown_on_load_is_rendered(client, as_role, report):
    html = _page(client, as_role)
    num_samples = len(report[RENDERED]["clustered_sequence"])

    assert html.count('data-hint="MCRIT:') == num_samples ** 2
    assert _pane(html, RENDERED).count('data-hint="MCRIT:') == num_samples ** 2


@pytest.mark.parametrize("method", [method for method in METHODS if method != RENDERED])
def test_every_other_tab_has_an_empty_pane_to_fill(client, as_role, method):
    html = _page(client, as_role)
    pane = _pane(html, method)

    assert f'data-lazy-method="{method}"' in pane
    assert "<table" not in pane
    assert f'data-bs-target="#pills-{method}"' in html


@pytest.mark.parametrize("method", [method for method in METHODS if method != RENDERED])
def test_the_data_for_a_tab_is_what_its_eager_render_showed(client, as_role, report, method):
    lazy = _lazy_panes(_page(client, as_role))
    pane = lazy["panes"][method]
    order = [str(sample_id) for sample_id in pane["order"]]
    percent = report[method]["matching_percent"]
    matches = report[method]["matching_matches"]

    assert order == report[method]["clustered_sequence"]
    for i, row_id in enumerate(order):
        for j, col_id in enumerate(order):
            # the tooltip line: what '%.2f%%'|format() printed, which the browser gets
            # back from the rounded value with toFixed(2)
            assert "%.2f" % pane["percent"][i][j] == "%.2f" % percent[row_id][col_id]
            assert pane["matches"][i][j] == matches[row_id][col_id]
            # the colour of the unrounded score - rounding first would move a cell
            # sitting just under a threshold into the next colour
            assert lazy["palette"][pane["color"][i][j]] == score_to_color(percent[row_id][col_id])


def test_the_rendered_tab_is_not_sent_twice(client, as_role):
    lazy = _lazy_panes(_page(client, as_role))

    assert sorted(lazy["panes"]) == sorted(method for method in METHODS if method != RENDERED)


def test_a_custom_order_reaches_the_tabs_filled_later(client, as_role, report):
    """Drag and drop reorders by reloading with ?custom=, and every matrix follows it."""
    custom = list(reversed(report[RENDERED]["clustered_sequence"]))
    html = _page(client, as_role, "?custom=" + ",".join(custom))
    lazy = _lazy_panes(html)

    for method, pane in lazy["panes"].items():
        assert [str(sample_id) for sample_id in pane["order"]] == custom
        percent = report[method]["matching_percent"]
        assert pane["percent"][0] == [round(percent[custom[0]][col_id], 2) for col_id in custom]


@pytest.fixture
def serve_report(corpus_mcrit, monkeypatch):
    """Answer the cross compare job with a changed copy of its captured report."""
    def _serve(changed):
        monkeypatch.setattr(corpus_mcrit, "getResultForJob", lambda *args, **kwargs: changed)
    return _serve


def test_each_tab_follows_its_own_clustered_order(client, as_role, report, serve_report):
    """Every method comes with its own clustered_sequence. The captured report happens
    to have the same one for all six, so one of them is shuffled here to tell them apart."""
    changed = copy.deepcopy(report)
    shuffled = list(changed["score_weighted"]["clustered_sequence"])
    random.Random(182).shuffle(shuffled)
    assert shuffled != report["score_weighted"]["clustered_sequence"]
    changed["score_weighted"]["clustered_sequence"] = shuffled
    serve_report(changed)

    lazy = _lazy_panes(_page(client, as_role))

    pane = lazy["panes"]["score_weighted"]
    percent = report["score_weighted"]["matching_percent"]
    assert [str(sample_id) for sample_id in pane["order"]] == shuffled
    assert pane["percent"] == [[round(percent[row_id][col_id], 2) for col_id in shuffled] for row_id in shuffled]
    assert [str(sample_id) for sample_id in lazy["panes"]["frequency_weighted"]["order"]] == report["frequency_weighted"]["clustered_sequence"]


def test_a_result_without_unweighted_renders_its_first_method(client, as_role, report, serve_report):
    """The tab shown on load is unweighted only if the result has it. Otherwise the
    script would find no matrix to clone from, and every other tab would stay empty."""
    changed = {method: results for method, results in report.items() if method != "unweighted"}
    first = next(iter(changed))
    serve_report(changed)

    html = _page(client, as_role)
    num_samples = len(report[first]["clustered_sequence"])

    assert _pane(html, first).count('data-hint="MCRIT:') == num_samples ** 2
    assert 'class="tab-pane fade show active" id="pills-%s"' % first in html
    assert re.search(r'<button class="nav-link show active" id="pills-%s-tab"[^>]*aria-selected="true"' % first, html)
    assert 'id="pills-unweighted"' not in html
    assert re.search(r'<button class="nav-link" id="pills-unweighted-tab"[^>]*aria-selected="false"', html)
    assert '"pills-" + "%s"' % first in html
    assert sorted(_lazy_panes(html)["panes"]) == sorted(method for method in changed if method != first)


class _Rows(HTMLParser):
    """The rows of a table as [{"attrs": ..., "cells": [{"attrs", "text", "hint", "edit"}]}]."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.rows.append({"attrs": attrs, "cells": []})
        elif tag == "td":
            self._cell = {"attrs": attrs, "text": "", "hint": None, "edit": False}
            self.rows[-1]["cells"].append(self._cell)
        elif tag == "span" and "data-hint" in attrs:
            self._cell["hint"] = attrs["data-hint"]
        elif tag == "a" and attrs.get("data-bs-target") == "#editSampleModal":
            self._cell["edit"] = True

    def handle_endtag(self, tag):
        if tag == "td":
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data


def test_the_rendered_matrix_has_the_shape_the_script_clones_from(client, as_role, report):
    """What the script filling the other tabs assumes about cross_table. Change the
    macro so that one of these no longer holds, and the script has to change with it."""
    order = report[RENDERED]["clustered_sequence"]
    percent = report[RENDERED]["matching_percent"]
    matches = report[RENDERED]["matching_matches"]
    parser = _Rows()
    parser.feed(_pane(_page(client, as_role), RENDERED))
    rows = parser.rows

    # rows[0] and rows[1] are the two header rows, the second holds every fifth id
    assert "sample-row" not in rows[0]["attrs"].get("class", "")
    fifths = [cell["text"].strip() for cell in rows[1]["cells"] if "align" in cell["attrs"]]
    assert fifths == [order[(k + 1) * 5 - 1] for k in range(len(order) // 5)]
    sample_rows = rows[2:]
    assert [row["attrs"].get("class") for row in sample_rows] == ["sample-row"] * len(order)
    num_leading = len(sample_rows[0]["cells"]) - len(order)
    for row_id, row in zip(order, sample_rows):
        # td.id names the row's sample
        ids = [cell["text"].strip() for cell in row["cells"] if "id" in cell["attrs"].get("class", "").split()]
        assert ids == [row_id]
        # the matrix is the last N cells, behind the same number of leading ones in every row
        assert len(row["cells"]) - len(order) == num_leading
        assert all(cell["hint"] is None for cell in row["cells"][:num_leading])
        assert sum(cell["edit"] for cell in row["cells"][:num_leading]) == 1
        for col_id, cell in zip(order, row["cells"][num_leading:]):
            # the script replaces the hint, which is the score line alone (issue #198)
            assert cell["hint"] == "MCRIT: %.2f%% (%d matches) " % (percent[row_id][col_id], matches[row_id][col_id])
