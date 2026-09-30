#!/usr/bin/python
"""Renders every result type against real reports from tests/fixtures/.

Until now nothing here rendered a result page: the strict fake answers with empty
shapes, which proves a route is reachable and nothing about whether the template can
survive the data. These tests run the real dispatch in `data.result()` over captured
reports, so a template that dereferences a field the backend stopped sending, or a
renderer that miscounts a filtered report, fails here rather than in a browser.

The reports come from a live instance - see tests/fixtures/regenerate.py.
"""

import json
import logging
import pathlib
import re
import unittest
from html.parser import HTMLParser

import pytest
from fixtureData import CorpusMcritClient, job_id_of, load
from flask import template_rendered
from mcrit.queue.LocalQueue import Job
from mcrit.storage.FunctionEntry import FunctionEntry
from mcrit.storage.MatchingResult import MatchingResult

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.mark.parametrize(
    "report",
    ["matches_for_sample", "matches_for_sample_vs", "matches_for_query", "cross_compare", "unique_blocks"],
)
def test_result_page_renders(client, as_role, report):
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of(report)}")
    assert response.status_code == 200, f"{report} did not render"
    # the h1 of result_corrupted.html - the template's *name* appears nowhere in the
    # rendered page, so asserting on that passed whatever the page actually said
    assert b"are corrupted" not in response.data


@pytest.mark.parametrize("report", ["matches_for_sample", "matches_for_sample_vs", "matches_for_query"])
def test_linkhunt_renders_for_every_matching_report(client, as_role, report):
    as_role("visitor")
    response = client.get(f"/data/linkhunt/{job_id_of(report)}")
    assert response.status_code == 200


@pytest.mark.parametrize("report", ["cross_compare", "unique_blocks"])
def test_linkhunt_reports_a_report_it_cannot_read_instead_of_500ing(client, as_role, report):
    """A job id is part of the URL, so any of them can be asked for a link hunt.

    Only the matching reports carry one. The other job types used to reach the end of
    the dispatch in `data.linkhunt` and return None, which Flask answers with a 500.
    """
    as_role("visitor")
    response = client.get(f"/data/linkhunt/{job_id_of(report)}")
    assert response.status_code == 200, f"{report} did not render"
    # the sentence in result_incompatible.html
    assert b"incompatible with the requested interpretation" in response.data


# --- jobs that store no result at all ----------------------------------------
#
# The fix above put the fallback inside `if result_json:`, so it only reached job types
# that happen to produce a report. A minhashing job or a collection change stores no
# result and is finished the moment it runs, so it fell to the old `elif job_info:` and
# rendered a progress page - for a job that ended long ago, permanently, since nothing
# about it will ever change again. Same for a failed job, a terminated one, and a
# matching job whose report came back empty.

def _job_like(method, result="r", attempts_left=3, terminated=False, finished=True):
    return {
        "_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
        "number": 1,
        "payload": {"method": method, "params": '{"0": 0}', "file_params": "{}", "descriptor": None},
        "all_dependencies": [],
        "created_at": {"$date": "2026-01-01T00:00:00.000Z"},
        "started_at": {"$date": "2026-01-01T00:00:01.000Z"},
        "finished_at": {"$date": "2026-01-01T00:00:02.000Z"} if finished else None,
        "last_error": None,
        "terminated": terminated,
        "attempts_left": attempts_left,
        "progress": 1,
        "result": result,
    }


class OneJob:
    """A backend holding exactly one job, and the result it did or did not produce."""

    def __init__(self, job_data, result_json=None):
        self._job = job_data
        self._result = result_json

    def getJobData(self, job_id, *args, **kwargs):
        from mcrit.queue.LocalQueue import Job
        return Job(self._job, None) if job_id == self._job["_id"] else None

    def getResultForJob(self, job_id, *args, **kwargs):
        return self._result


@pytest.fixture
def one_job(app, client, as_role):
    def _one_job(job_data, result_json=None):
        backend = OneJob(job_data, result_json)
        app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend
        as_role("visitor")
        return client.get(f"/data/linkhunt/{job_data['_id']}")
    return _one_job


@pytest.mark.parametrize(
    "method",
    ["updateMinHashes", "updateMinHashesForSample", "rebuildIndex", "addBinarySample", "deleteSample"],
)
def test_a_finished_job_that_stores_no_result_is_not_called_in_progress(one_job, method):
    response = one_job(_job_like(method, result=None))

    assert response.status_code == 200
    assert b"Job in Progress" not in response.data
    assert b"incompatible" in response.data


def test_a_failed_job_says_it_failed(one_job):
    response = one_job(_job_like("getMatchesForSample", result=None, attempts_left=0))

    assert response.status_code == 200
    assert b"Job in Progress" not in response.data
    assert b"did not finish" in response.data
    assert b"ran out of attempts" in response.data


def test_a_terminated_job_says_it_was_terminated(one_job):
    response = one_job(_job_like("getMatchesForSample", result=None, terminated=True))

    assert response.status_code == 200
    assert b"Job in Progress" not in response.data
    assert b"terminated before it could finish" in response.data


def test_a_matching_job_with_an_empty_report_is_not_called_in_progress(one_job):
    """Right kind of job, nothing to hunt through - which is an answer, not a wait."""
    response = one_job(_job_like("getMatchesForSample"), result_json={})

    assert response.status_code == 200
    assert b"Job in Progress" not in response.data
    assert b"does not contain any data" in response.data


def test_a_job_that_really_is_running_still_says_so(one_job):
    """The branch has to survive: this is the one case the progress page is for."""
    response = one_job(_job_like("getMatchesForSample", result=None, finished=False))

    assert response.status_code == 200
    assert b"Job in Progress" in response.data


def test_linkhunt_for_a_job_id_nobody_knows_says_it_was_not_found(client, as_role):
    """Unknown job id and wrong report type are different answers, and were swapped:
    the unknown case rendered "incompatible with the requested interpretation"."""
    as_role("visitor")
    response = client.get("/data/linkhunt/ffffffffffffffffffffffff")
    assert response.status_code == 200
    assert b"was not found in the system" in response.data


def test_result_page_applies_a_score_filter(client, as_role):
    """The filter parameters drive MatchingResult.applyFilterValues, which is where a
    report gets narrowed - rendering it unfiltered proves much less."""
    as_role("visitor")
    unfiltered = client.get(f"/data/result/{job_id_of('matches_for_sample')}")
    filtered = client.get(f"/data/result/{job_id_of('matches_for_sample')}?filter_direct_min_score=99")

    assert unfiltered.status_code == 200
    assert filtered.status_code == 200
    assert filtered.data != unfiltered.data


def test_unique_blocks_page_paginates(client, as_role):
    as_role("visitor")
    first = client.get(f"/data/result/{job_id_of('unique_blocks')}")
    second = client.get(f"/data/result/{job_id_of('unique_blocks')}?blkp=2")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data != second.data


# --- the sample-filtered function table ------------------------------------------
#
# `?samid=` narrows a matching report to one matched sample and lists the function
# matches against it. The widget under that table and the table itself have to be
# built from the same list, or the header lies and the tail of the list has no page
# that reaches it.

#: A function entry carrying nothing but its id and an offset, for ids the trimmed
#: corpus does not hold. Shaped for FunctionEntry.fromDict, which is strict about
#: which keys are present.
STAND_IN_FUNCTION_ENTRY = {
    "architecture": "intel",
    "binweight": 0.0,
    "family_id": 0,
    "function_name": "",
    "function_labels": [],
    "matches": {},
    "minhash": "",
    "minhash_shingle_composition": {},
    "num_blocks": 0,
    "num_instructions": 0,
    "offset": 0,
    "pichash": None,
    "picblockhashes": [],
    "sample_id": 0,
    "xcfg": None,
}


class CorpusWithEveryMatchedFunction(CorpusMcritClient):
    """The captured corpus, with the by-id function pool widened to answer any id.

    `tests/fixtures/regenerate.py` trims `functions_matched` to the ids the 1-vs-1
    page looks up and says in as many words that the filtered result views
    (`?famid=` / `?samid=` / `?funid=`) reach past that set. They do: `data.py`
    calls `assign_matched_offsets` on every filtered match, and a single id it
    cannot resolve makes the whole page render as `result_corrupted.html`. With the
    shipped pool that is every sample of every report but one, so there is no table
    left to count.

    A real backend answers every id it is asked for, so this one does too: the
    captured entry where the corpus has it, an offset-only stand-in where it does
    not. The offset is display-only on this page - the tests below count rows and
    never look inside one.
    """

    def getFunctionsByIds(self, function_ids, *args, **kwargs):
        entries = super().getFunctionsByIds(function_ids, *args, **kwargs)
        for function_id in function_ids:
            if int(function_id) not in entries:
                entries[int(function_id)] = FunctionEntry.fromDict(
                    dict(STAND_IN_FUNCTION_ENTRY, function_id=int(function_id))
                )
        return entries


@pytest.fixture
def widened_corpus_mcrit():
    return CorpusWithEveryMatchedFunction()


def matched_sample_ids(report):
    """Every sample a report matched, i.e. every `?samid=` its own page links to."""
    return [sample["sample_id"] for sample in load(f"{report}.result")["matches"]["samples"]]


def matched_family_ids(report):
    """Every family a report matched, i.e. every `?famid=` its own page links to."""
    return sorted({sample["family_id"] for sample in load(f"{report}.result")["matches"]["samples"]})


def report_totals(report):
    """A report's own function totals, in both units the result pages count in.

    Read off the untouched report rather than off a page, so the pages are being
    checked against the data and not against each other.
    """
    matching_result = MatchingResult.fromDict(load(f"{report}.result"))
    return {
        # every (function, matched function) pair the report holds
        "matches": matching_result.num_original_function_matches,
        # every function of the reference sample that matched anything at all
        "functions": len(matching_result.getAggregatedFunctionMatches(unfiltered=True)),
    }


def read_function_table(html):
    """What the function match table says about itself.

    (selection, first row shown, last row shown, filtered-out count, rows drawn) -
    the first four off the header line above the table, which is written from the
    Pagination object, and the last from the table body itself.
    """
    body = html.split('id="function-matches"', 1)[1]
    header = re.search(r"selection: (\d+), showing: (\d+) - (\d+) \(filtered: (-?\d+)\)", body)
    rows = re.search(r"<tbody>(.*?)</tbody>", body, re.S)
    assert header is not None and rows is not None, "the function match table did not render"
    return (*(int(group) for group in header.groups()), rows.group(1).count("<tr "))


@pytest.mark.parametrize("report", ["matches_for_sample", "matches_for_query", "matches_for_sample_vs"])
def test_sample_filtered_page_paginates_the_rows_it_shows(client, as_role, widened_corpus_mcrit, app, report):
    """`?samid=` must page over the list the table draws from.

    The header count, the per-page arithmetic and the rendered rows all come off one
    Pagination. Building it from a different list than the table iterates leaves the
    tail of that list on no page at all, and makes every "showing X - Y" wrong.

    Run over every matched sample of three captured reports, so one fixture whose two
    lists happen to be the same length cannot carry it.
    """
    # the rest of this module wants the corpus exactly as captured, so swap the backend
    # through the same seam conftest uses rather than overriding the fixture module-wide
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: widened_corpus_mcrit
    as_role("visitor")
    job_id = job_id_of(report)

    disagreements = []
    for sample_id in matched_sample_ids(report):
        # walk to the widget's own last page. The bound is a runaway guard, not an
        # expected exit - falling out of it means the count ran away from the rows,
        # which is the failure this test is here for, so it is an error and not a
        # quiet end to the loop
        for page in range(1, 51):
            response = client.get(f"/data/result/{job_id}?samid={sample_id}&funp={page}&funl=250")
            assert response.status_code == 200
            assert b"are corrupted" not in response.data, f"{report} samid={sample_id} did not render"
            count, first, last, _filtered, drawn = read_function_table(response.data.decode())
            if drawn != last - first + 1:
                disagreements.append(
                    f"{report} samid={sample_id} funp={page}: widget says {count} in total and "
                    f"{first}-{last} on this page ({last - first + 1} rows), table drew {drawn}"
                )
            if last >= count:
                break
        else:
            raise AssertionError(f"{report} samid={sample_id}: the widget never reached its last page")

    assert not disagreements, "pagination and table disagree:\n  " + "\n  ".join(disagreements)


#: Which of a report's totals each function match table is counted in. The `filtered:`
#: figure beside a table is the rest of the report, so it only means anything when it
#: is the table's own total minus the table's own selection - and a page that changes
#: what one of its rows is has to change its entry here with it.
FUNCTION_TABLE_UNIT = {
    # one row per matched function of the reference sample, aggregated over every
    # sample it matched
    "": "functions",
    "famid": "functions",
    # one row per function match: this table has an Offset B and a Function B, which
    # only an individual match has
    "samid": "matches",
}


@pytest.mark.parametrize("report", ["matches_for_sample", "matches_for_query"])
def test_the_filtered_figure_accounts_for_the_rest_of_the_report(client, as_role, widened_corpus_mcrit, app, report):
    """`selection` and `filtered` have to add up to the report, on every result page.

    Both are printed on one line as if they were two halves of one total, so they have
    to be counted the same way. Subtracting an aggregated selection from a raw match
    total is not a smaller number, it is a different question - it made the unfiltered
    page report a four-figure `filtered:` with no filter applied at all.
    """
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: widened_corpus_mcrit
    as_role("visitor")
    job_id = job_id_of(report)
    totals = report_totals(report)

    pages = [("", "")]
    pages += [(f"&famid={family_id}", "famid") for family_id in matched_family_ids(report)]
    pages += [(f"&samid={sample_id}", "samid") for sample_id in matched_sample_ids(report)]

    wrong = []
    for query, kind in pages:
        response = client.get(f"/data/result/{job_id}?funp=1{query}")
        assert response.status_code == 200
        assert b"are corrupted" not in response.data, f"{report} {query} did not render"
        selection, _first, _last, filtered, _drawn = read_function_table(response.data.decode())
        total = totals[FUNCTION_TABLE_UNIT[kind]]
        if selection + filtered != total:
            wrong.append(
                f"{report} {query or '(unfiltered)'}: selection {selection} + filtered {filtered} "
                f"= {selection + filtered}, report holds {total}"
            )

    assert not wrong, "the filtered figure does not account for the report:\n  " + "\n  ".join(wrong)


def test_a_job_id_nobody_knows_is_reported_not_crashed(client, as_role):
    as_role("visitor")
    response = client.get("/data/result/ffffffffffffffffffffffff")
    assert response.status_code == 200
    assert b"was not found in the system" in response.data


@pytest.mark.parametrize(
    "report",
    ["matches_for_sample", "matches_for_sample_vs", "matches_for_query", "cross_compare", "unique_blocks"],
)
def test_job_page_renders_for_a_finished_job(client, as_role, report):
    """This used to cover only matches_for_sample, which is the one report in the corpus
    with no sub-jobs. The cross compare has five, none of them captured - the same shape
    as a dependency deleted through the UI - and the page 500d on it. See
    tests/testJobOverview.py."""
    as_role("visitor")
    response = client.get(f"/data/jobs/{job_id_of(report)}")
    assert response.status_code == 200


#: (report fixture, query string, the template the request has to reach). `data.result`
#: dispatches on the query parameters, so `?famid=` / `?samid=` / `?funid=` are the only
#: way to reach three of the five result templates that render a score - parametrising
#: over reports alone renders `result_compare_all.html` twice and calls it coverage.
#: The ids are the captured 1-vs-1 report's own: it matched samples 1 and 3, function 880
#: is one of its matches, and its by-id function pool is complete - the 1-vs-N report's is
#: not, so ?samid= and ?funid= land on result_corrupted.html there (fixtures/README.md).
RESULT_PAGES = [
    ("matches_for_sample", "", "result_compare_all.html"),
    ("matches_for_query", "", "result_compare_all.html"),
    ("matches_for_sample_vs", "", "result_compare_vs.html"),
    ("matches_for_sample", "?famid=1", "result_compare_family.html"),
    ("matches_for_sample_vs", "?samid=3", "result_compare_sample.html"),
    ("matches_for_sample_vs", "?funid=880", "result_compare_function.html"),
]

#: result_compare_function.html is the one of them that carries no sample-level table.
SAMPLE_SCORE_PAGES = [page for page in RESULT_PAGES if page[2] != "result_compare_function.html"]


@pytest.fixture
def renders(app):
    """(template name, context) for every template rendered during a request.

    The function-match tables render their score bare, so there is no tooltip to read
    the exact value back out of the page with - it has to come from the context the
    template was handed. Recording it also lets a test say which template it meant to
    exercise, rather than trusting a query parameter to have dispatched where it looks.

    `record` has to stay referenced for the length of the test - blinker holds its
    receivers weakly, and a receiver nobody else keeps is collected and never called.
    The generator frame this fixture suspends in is what keeps it alive.
    """
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template.name, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


class _TableReader(HTMLParser):
    """Every <table> on a page, as a list of rows of cell text.

    Stacked rather than flat because a table macro nested in a cell would otherwise
    hand its rows to whichever table happened to be open last, and a cell would be
    read out of the wrong column without anything looking wrong.
    """

    def __init__(self):
        super().__init__()
        self.tables = []
        self._open_tables = []
        self._open_rows = []
        self._open_cells = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._open_tables.append([])
        elif tag == "tr" and self._open_tables:
            self._open_rows.append([])
        elif tag in ("td", "th") and self._open_rows:
            self._open_cells.append([])

    def handle_endtag(self, tag):
        if tag == "table" and self._open_tables:
            self.tables.append(self._open_tables.pop())
        elif tag == "tr" and self._open_rows:
            row = self._open_rows.pop()
            if self._open_tables:
                self._open_tables[-1].append(row)
        elif tag in ("td", "th") and self._open_cells:
            cell = "".join(self._open_cells.pop()).strip()
            if self._open_rows:
                self._open_rows[-1].append(cell)

    def handle_data(self, data):
        if self._open_cells:
            self._open_cells[-1].append(data)


def column_under(page, header):
    """Every cell below the column headed `header`, located the way a reader locates it.

    Rows of a different width than the header row are skipped - the sample tables put a
    `colspan` header row above their own, and it is not a row of cells.
    """
    reader = _TableReader()
    reader.feed(page)
    cells = []
    for rows in reader.tables:
        for index, row in enumerate(rows):
            if header in row:
                position, width = row.index(header), len(row)
                cells += [below[position] for below in rows[index + 1:] if len(below) == width]
                break
    return cells


def score_column(page):
    """The score column of the function-match table: headed "Best Score" where the table
    aggregates a function's matches, and "Score" where it lists them one by one."""
    for header in ("Best Score", "Score"):
        cells = column_under(page, header)
        if cells:
            return cells
    return []


def function_match_scores(template, context):
    """The scores that template's function-match table was handed, in row order."""
    matching_result, funp = context["matching_result"], context["funp"]
    if template in ("result_compare_all.html", "result_compare_family.html"):
        return [aggregate["best_score"] for aggregate in matching_result.getAggregatedFunctionMatches(funp.start_index, funp.limit)]
    return [matched_function.matched_score for matched_function in matching_result.getFunctionsSlice(funp.start_index, funp.limit)]


@pytest.mark.parametrize("report,query,template", RESULT_PAGES)
def test_function_score_column_rounds_rather_than_truncates(client, as_role, renders, report, query, template):
    """The function-match table truncated its score the same way the sample columns did.

    `MatchedFunctionEntry.matched_score` is a float and `MatchingResult` maxes it into
    `best_score`, so `%d` showed 93.75 as 93 - issue #7 again, one table lower on the
    same page, in a column that is active by default and needs no query parameter.

    These cells carry no tooltip and are marked up exactly like the byte counts beside
    them, so they are found by the header over the column and checked against the value
    the render context holds, which is the score itself rather than a rounding of it.
    """
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of(report)}{query}")
    assert response.status_code == 200
    assert renders, f"{report}{query} rendered no template at all"
    assert renders[-1][0] == template, f"{report}{query} rendered {renders[-1][0]}, not {template}"

    exact = function_match_scores(*renders[-1])
    shown = score_column(response.data.decode())
    assert exact, f"{template} rendered no function matches to check"
    assert len(shown) == len(exact), f"{template}: {len(shown)} score cells for {len(exact)} function matches"

    not_rounded = [(score, cell) for score, cell in zip(exact, shown) if int(cell) != round(score)]
    assert not not_rounded, f"{template}: score cells not rounded: {not_rounded}"


# every sample score cell renders the exact percentage into its hover text and an integer
# into the cell itself:  ... Percent: 85.88%">  85</span>
SCORE_CELL = re.compile(r"Percent:\s*(\d+\.\d+)%\">\s*(-?\d+)\s*</span>")


def score_cells(page):
    """(exact percent, integer shown) for every score cell on a rendered page."""
    return [
        (float(percent), int(shown))
        for percent, shown in SCORE_CELL.findall(page)
    ]


@pytest.mark.parametrize("report,query,template", SAMPLE_SCORE_PAGES)
def test_score_columns_round_rather_than_truncate(client, as_role, renders, report, query, template):
    """`%d` truncates toward zero, so a sample scoring 85.88 showed as 85 - a whole
    point below what the tooltip on the same cell says, and 0.76 showed as 0 next to
    a neighbour at 1.04 that had scored barely more (issue #7).

    The assertion below is a half-unit tolerance rather than an equality because the
    tooltip is the score rounded to two decimals rather than the score itself; see the
    comment on TOOLTIP_PRECISION for why that makes an equality wrong here.
    """
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of(report)}{query}")
    assert response.status_code == 200
    assert renders, f"{report}{query} rendered no template at all"
    assert renders[-1][0] == template, f"{report}{query} rendered {renders[-1][0]}, not {template}"

    cells = score_cells(response.data.decode())
    assert cells, f"{report}{query} rendered no score cells to check"
    # The tooltip is the score to two decimals, so it is not the source value: a score
    # of 2.501 renders as tooltip "2.50" and cell "3", and asserting shown == round(2.50)
    # would fail on a correct page. What the tooltip does pin is an interval - the score
    # is within 0.005 of it - so the cell must be within half a unit of that interval.
    # Truncation is caught all the same: it is off by the whole fractional part, and
    # these reports carry plenty above 0.505 (85.88, 82.70, 42.81, 60.99).
    TOOLTIP_PRECISION = 0.005
    not_rounded = [(exact, shown) for exact, shown in cells if abs(shown - exact) > 0.5 + TOOLTIP_PRECISION]
    assert not not_rounded, f"{report}{query}: score cells not rounded: {not_rounded}"


# the same hover text states a fraction above its percentage, and the percentage has to
# be that fraction:  ... Bytes: 130682.88 / 152337.00 &#10;Percent: 85.79%
SCORE_TOOLTIP = re.compile(r"Bytes:\s*(-?\d+\.\d+)\s*/\s*(\d+\.\d+)\s*&#10;Percent:\s*(-?\d+\.\d+)%")

#: Both numbers in the fraction and the percentage itself are rendered to two decimals,
#: so the quotient can miss the stated percentage by the percentage's own rounding. The
#: numerator's rounding contributes ~4e-8 relative on these reports and is ignored.
TWO_DECIMALS = 0.005 + 1e-6


@pytest.mark.parametrize("report,query,template", SAMPLE_SCORE_PAGES)
def test_score_tooltips_divide_by_the_total_their_percentage_uses(client, as_role, renders, report, query, template):
    """The hover text used to print the sample's binweight as the divisor while
    stating a percentage taken against a different total - the matchable bytes, or
    for the `nonlib_` columns the matchable bytes minus the library-matching ones.
    On the top match of `matches_for_sample` it offered 130682.88 / 155065 = 84.28
    and then said 85.79%.

    That is what sets a reader's expectation, and issue #7 is a report of the value
    being "too far from the expected value" - so an inconsistency here is the defect,
    not a cosmetic one. See docs/adr/0009-nonlib-frequency-score.md.
    """
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of(report)}{query}")
    assert response.status_code == 200
    assert renders and renders[-1][0] == template, f"{report}{query} did not render {template}"

    page = response.data.decode()
    tooltips = SCORE_TOOLTIP.findall(page)
    assert tooltips, f"{report}{query} rendered no score tooltip to check"

    inconsistent = [
        (numerator, divisor, percent)
        for numerator, divisor, percent in (
            (float(a), float(b), float(c)) for a, b, c in tooltips
        )
        if abs(100.0 * numerator / divisor - percent) > TWO_DECIMALS
    ]
    assert not inconsistent, (
        f"{report}{query}: hover text states a fraction that is not its percentage: {inconsistent}"
    )

    # A report whose functions were all matchable and none library-matched would make
    # the check above pass against the binweight too, so pin that the divisor actually
    # moved off it. None of the three fixtures is that report.
    binweight = renders[-1][1]["matching_result"].reference_sample_entry.binweight
    assert any(float(divisor) != binweight for _, divisor, _ in tooltips), (
        f"{report}{query}: every score tooltip still divides by the sample binweight {binweight}"
    )


# --- downloading the raw report (issue #75) ---------------------------------------


def _disposition(response):
    return response.headers.get("Content-Disposition", "")


@pytest.mark.parametrize(
    "report",
    ["matches_for_sample", "matches_for_sample_vs", "matches_for_query", "cross_compare", "unique_blocks"],
)
def test_raw_result_downloads_the_unmodified_report(client, as_role, report):
    """The download is what the backend produced, not a re-rendering of it.

    `MatchingResult.fromDict` and the templates consume exactly this dict, so a
    download that differs from tests/fixtures/<report>.result.json is not the raw
    result the issue asks for.
    """
    as_role("visitor")
    job_id = job_id_of(report)
    response = client.get(f"/data/result/{job_id}/download")

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert "attachment" in _disposition(response)
    assert job_id in _disposition(response)
    assert json.loads(response.data) == load(f"{report}.result")


def test_raw_result_download_is_offered_on_the_result_and_job_pages(client, as_role):
    """One link in the shared job table covers the job overview and every result
    page, which is the only reason a single route is enough."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    expected = f"/data/result/{job_id}/download".encode()

    assert expected in client.get(f"/data/result/{job_id}").data
    assert expected in client.get(f"/data/jobs/{job_id}").data


def test_raw_result_download_is_not_offered_before_a_job_finishes(app):
    """A running job has nothing to download, so the link must not be there to
    follow. Rendered from the macro rather than through a page, because every job
    the corpus answers `getJobData` for has already finished - the only unfinished
    one available is built here, from a captured queue entry."""
    queue_entry = load("queue")[0]
    finished = Job(queue_entry, None)
    unfinished = Job(dict(queue_entry, finished_at=None, result=None, progress=0.5), None)
    template = "{% from 'table/column_table.html' import job_column_table %}{{ job_column_table(job_info) }}"

    with app.test_request_context():
        rendered_finished = app.jinja_env.from_string(template).render(job_info=finished)
        rendered_unfinished = app.jinja_env.from_string(template).render(job_info=unfinished)

    assert f"/data/result/{finished.job_id}/download" in rendered_finished
    assert "/download" not in rendered_unfinished


def test_raw_result_download_prefers_the_cache_over_a_second_fetch(client, as_role, corpus_mcrit):
    """The report is cached on first sight and served from there afterwards, so a
    second download costs the backend nothing - and answers with the same bytes."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")

    first = client.get(f"/data/result/{job_id}/download")
    fetches_after_first = [call for call in corpus_mcrit.calls if call[0] == "getResultForJob"]
    second = client.get(f"/data/result/{job_id}/download")
    fetches_after_second = [call for call in corpus_mcrit.calls if call[0] == "getResultForJob"]

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.data == first.data
    assert len(fetches_after_first) == 1
    assert len(fetches_after_second) == 1, "a cached report was fetched from the backend again"


def test_raw_result_download_serves_the_job_that_was_asked_for(client, as_role):
    """Two reports in the cache, each download its own. The cache is a flat directory
    that `load_cached_result` searches by substring, so selecting the wrong file in
    it is a real way to hand one caller another caller's report."""
    as_role("visitor")
    for report in ("matches_for_sample", "cross_compare"):
        client.get(f"/data/result/{job_id_of(report)}")

    for report in ("matches_for_sample", "cross_compare"):
        response = client.get(f"/data/result/{job_id_of(report)}/download")
        assert json.loads(response.data) == load(f"{report}.result")


def test_raw_result_download_of_a_job_without_a_result_defers_to_the_report_page(client, as_role, corpus_mcrit, monkeypatch):
    """A known job the backend has no result for - still queued, failed, or a job
    type that produces nothing. The report page already distinguishes those, so the
    download hands over to it rather than growing a second vocabulary for it."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    monkeypatch.setattr(corpus_mcrit, "getResultForJob", lambda *args, **kwargs: None)

    response = client.get(f"/data/result/{job_id}/download", follow_redirects=True)

    assert response.status_code == 200
    assert "attachment" not in _disposition(response)
    assert b"no result to download" in response.data


def test_raw_result_download_of_an_unknown_job_is_reported_not_served(client, as_role):
    as_role("visitor")
    response = client.get("/data/result/ffffffffffffffffffffffff/download")

    assert response.status_code == 200
    assert b"was not found in the system" in response.data
    assert "attachment" not in _disposition(response)


@pytest.mark.parametrize(
    "crafted",
    [
        # substrings of a real job id: `load_cached_result` matches cache filenames by
        # substring, so a partial id must not be able to select the report it names
        "f8b8d2c6f836649a",
        "6a74",
        # none of these may reach the cache directory or the response headers
        "..",
        "../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        '0123456789abcdef"; filename="evil.json',
        "0123456789abcdef%0d%0aX-Injected: yes",
        # a trailing newline still satisfies a `$`-anchored hex pattern
        "0123456789abcdef%0a",
        "-",
        ".json",
        "*",
    ],
)
def test_raw_result_download_refuses_a_crafted_job_id(client, as_role, crafted):
    """A crafted job_id lands in a filename match, a filesystem path and a response
    header. None of them may hand out a file."""
    as_role("visitor")
    # prime the cache, so there is something for a crafted id to match against
    client.get(f"/data/result/{job_id_of('matches_for_sample')}")

    response = client.get(f"/data/result/{crafted}/download")

    assert response.status_code < 500
    assert response.mimetype != "application/json"
    assert "attachment" not in _disposition(response)
    assert "X-Injected" not in response.headers
    assert b"root:x:" not in response.data


def test_the_download_job_id_pattern_rejects_a_trailing_newline():
    """Pinned here because the route-level checks above cannot see it: a crafted id
    is already refused for naming no job the backend knows, whichever pattern sits
    in front of it.

    A `$` anchor matches before a trailing newline as well as at the end of the
    string, so a `$`-anchored hex pattern accepts a job_id ending in one - and a
    newline in the job_id is what splits the Content-Disposition header in two.
    """
    from mcritweb.views.data import JOB_ID_PATTERN

    assert JOB_ID_PATTERN.fullmatch("6a7464faf8b8d2c6f836649a")
    assert not JOB_ID_PATTERN.fullmatch("6a7464faf8b8d2c6f836649a\n")
    assert not JOB_ID_PATTERN.fullmatch("../../etc/passwd")
    assert not JOB_ID_PATTERN.fullmatch('abc"; filename="evil.json')


def test_cached_result_lookup_matches_whole_job_ids_only(app):
    """The cache lookup behind the download, on its own.

    Same reason as above: every route-level test is already satisfied by the "no
    such job" gate, so none of them would notice this widening back into the
    substring match `load_cached_result` uses.
    """
    from mcritweb.views.data import find_cached_result_filename

    cache_path = pathlib.Path(app.instance_path) / "cache" / "results"
    (cache_path / "20260806-104636-6a7464faf8b8d2c6f836649a.json").write_text("{}")
    (cache_path / "20260807-104636-6a7464faf8b8d2c6f836649a.json").write_text("{}")

    # the whole id, newest capture first
    assert find_cached_result_filename(app, "6a7464faf8b8d2c6f836649a") == "20260807-104636-6a7464faf8b8d2c6f836649a.json"
    # a prefix, a suffix and a wildcard-ish id all name a file that is not theirs
    assert find_cached_result_filename(app, "6a7464") is None
    assert find_cached_result_filename(app, "f8b8d2c6f836649a") is None
    assert find_cached_result_filename(app, "") is None

#: One function from each reference sample - the only pool that keeps a control flow
#: graph, so the only one the comparison page can build its two panels from.
FUNCTION_VS_A = 84
FUNCTION_VS_B = 943


@pytest.fixture
def function_vs_page(client, as_role):
    as_role("visitor")
    response = client.get(f"/data/matches/function/{FUNCTION_VS_A}/{FUNCTION_VS_B}")
    assert response.status_code == 200
    return response.data.decode()


def test_the_function_comparison_page_shows_the_overall_match_score(function_vs_page, fake_mcrit):
    """Issue #69's second box. The score is the one number that says how close the
    two functions are, and it is the backend's - so the assertion is that the page
    shows *that* number, not merely that it shows one."""
    expected = fake_mcrit.getMatchFunctionVs(FUNCTION_VS_A, FUNCTION_VS_B)["match_entry"]["matches"][3]

    shown = re.search(r"Match Score:(?:\s|<[^>]*>)*(\d+)", function_vs_page)
    assert shown, "the comparison page shows no match score"
    # rounded to the nearest whole number rather than truncated, like every other score
    # column (issue #7): "%.0f" and round() both take a tie to the even neighbour
    assert int(shown.group(1)) == round(expected)


def test_the_loop_boundary_control_is_live(function_vs_page):
    """Issue #69's first box, as far as markup can see it. The control shipped
    `disabled`, with a title saying the side-by-side view had nothing to toggle;
    testFunctionVsBrowser.py drives what it now toggles."""
    checkbox = re.search(r'<input[^>]*id="loopBgFill"[^>]*>', function_vs_page)
    assert checkbox, "the loop boundary control is gone"
    assert "disabled" not in checkbox.group(0)
    assert "checked" in checkbox.group(0), "boundaries are meant to start visible"


if __name__ == "__main__":
    unittest.main()
