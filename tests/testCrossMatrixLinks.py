#!/usr/bin/python
"""A cross compare cell opens the row sample's report filtered to the column sample,
and only when the two samples have matches.

mcrit fills `matching_matches[a][b]` from the entry for b in a's report and leaves 0 where
there is none (MatcherCross), so a cell with no matches names a sample that report never
matched. Its `?samid=` page listed nothing, and its diagram was a broken image:
data.render_missing_match_diagram draws one only for a sample the report matched. The
diagonal is always such a cell, since mcrit counts a sample's matches with itself apart.
On a live cross compare of 20 samples, 286 of its 400 cells were.
"""

import copy
import json
import logging
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of, load

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

METHODS = ["unweighted", "score_weighted", "frequency_weighted", "nonlib_unweighted", "nonlib_score_weighted", "nonlib_frequency_weighted"]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.fixture
def report():
    return load("cross_compare.result")


@pytest.fixture
def job_of_sample():
    """The 1vN job behind each row, as the cross compare job's parameters name it."""
    return json.loads(load("cross_compare.job")["payload"]["params"])["0"]


@pytest.fixture
def serve_report(corpus_mcrit, monkeypatch):
    """Answer the cross compare job with a changed copy of its captured report."""
    def _serve(changed):
        monkeypatch.setattr(corpus_mcrit, "getResultForJob", lambda *args, **kwargs: changed)
    return _serve


class _Cells(HTMLParser):
    """The matrix cells of the rendered pane, as {(row id, column id): onclick or None}."""

    def __init__(self, order):
        super().__init__()
        self.order = order
        self.cells = {}
        self._row = None
        self._column = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and attrs.get("class") == "sample-row":
            self._row = None
            self._column = 0
        elif tag == "th" and "id" in (attrs.get("class") or "").split():
            self._row = "pending"
        elif tag == "span" and "data-hint" in attrs and self._row not in (None, "pending"):
            self.cells[(self._row, self.order[self._column])] = attrs.get("onclick")
            self._column += 1

    def handle_data(self, data):
        if self._row == "pending" and data.strip():
            self._row = data.strip()


def _cells(client, as_role, order):
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('cross_compare')}?order=clustered")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    pane = html[html.index('id="pills-unweighted"'):html.index('id="cross-lazy-panes"')]
    parser = _Cells(order)
    parser.feed(pane)
    assert len(parser.cells) == len(order) ** 2
    return parser.cells


def test_a_pair_with_matches_opens_the_rows_report_filtered_to_the_column(client, as_role, report, job_of_sample):
    order = report["unweighted"]["clustered_sequence"]
    matches = report["unweighted"]["matching_matches"]
    cells = _cells(client, as_role, order)

    linked = [(row_id, col_id) for row_id in order for col_id in order if matches[row_id][col_id]]
    assert linked
    for row_id, col_id in linked:
        assert cells[(row_id, col_id)].startswith(f"window.location.href='{job_of_sample[row_id]}?samid={col_id}'")


def test_the_diagonal_links_nowhere(client, as_role, report):
    order = report["unweighted"]["clustered_sequence"]
    # what the captured report says of every sample against itself
    assert all(report["unweighted"]["matching_matches"][sample_id][sample_id] == 0 for sample_id in order)
    cells = _cells(client, as_role, order)

    assert [cells[(sample_id, sample_id)] for sample_id in order] == [None] * len(order)


def test_a_pair_of_samples_without_matches_links_nowhere(client, as_role, report, serve_report):
    """The captured report has no such pair off the diagonal, so one is made: two samples
    that mcrit found nothing in common for, in either direction."""
    order = report["unweighted"]["clustered_sequence"]
    first, second = order[0], order[1]
    changed = copy.deepcopy(report)
    for method in METHODS:
        changed[method]["matching_matches"][first][second] = 0
        changed[method]["matching_matches"][second][first] = 0
        changed[method]["matching_percent"][first][second] = 0
        changed[method]["matching_percent"][second][first] = 0
    serve_report(changed)
    cells = _cells(client, as_role, order)

    assert cells[(first, second)] is None
    assert cells[(second, first)] is None
    linked = {pair for pair, onclick in cells.items() if onclick}
    assert linked == {(row_id, col_id) for row_id in order for col_id in order if row_id != col_id} - {(first, second), (second, first)}
