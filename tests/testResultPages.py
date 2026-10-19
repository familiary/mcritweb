#!/usr/bin/python
"""Renders every result type against real reports from tests/fixtures/.

Until now nothing here rendered a result page: the strict fake answers with empty
shapes, which proves a route is reachable and nothing about whether the template can
survive the data. These tests run the real dispatch in `data.result()` over captured
reports, so a template that dereferences a field the backend stopped sending, or a
renderer that miscounts a filtered report, fails here rather than in a browser.

The reports come from a live instance - see tests/fixtures/regenerate.py.
"""

import collections
import copy
import html
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


#: `<script>...</script>` as it comes off the rendered page. Used to lint the block
#: that defines the clipboard helper, so an unrelated script is never the offender.
SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)

#: The copy icon and the textarea it copies, tied together: a helper wired to the
#: wrong id is as broken as no helper.
COPY_ICON = re.compile(r"<i\b[^>]*onclick=\"copyTextAreaToClipboard\('#yara_text'\)\"")

#: `// ...` to end of line. The helper's own comment names the old implementation on
#: purpose, so the lint below reads the code with the comments taken out.
LINE_COMMENT = re.compile(r"//[^\n]*")

#: The shapes of the pre-#80 helper. It filled a detached textarea from
#: `$(element).html()`, so what reached the clipboard was the rendered *markup*:
#: HTML-escaped, and frozen at page load however the reader had edited the rule.
COPIES_THE_MARKUP = ("copyElementToClipboard", ".html()", ".innerHTML")


#: The Block column of the unique blocks table: a `/* ... */` comment and then the
#: hex sequence in braces, which is what a reader copies into a YARA file.
BLOCK_CELL = re.compile(r"<code style=\"white-space:pre\">(.*?)</code>", re.DOTALL)

#: One byte of a YARA hex string: a pair of hex digits, or `??` for a wildcarded
#: one. Anything else - an odd-length run, most of all - is a syntax error.
HEX_TOKEN = re.compile(r"^(?:[0-9a-f]{2}|\?\?)+$")

#: The rule's comment above each selected picblock, as `renderRule` writes it once
#: `name_functions_in_rule` has been over it.
RULE_PICBLOCK_COMMENT = re.compile(r"/\* picblockhash: (0x[0-9a-f]+) - coverage: \d+/\d+ samples(?P<tail>[^\n]*)")


def statistics_table_of(page):
    """The markup of the "Block Statistics across Samples" table."""
    assert "Block Statistics across Samples" in page, "the statistics table is not on the page"
    return page.split("Block Statistics across Samples")[1].split("</table>")[0]


def hex_sequences_of(page):
    """The `{ ... }` half of every Block cell on the page, unescaped."""
    sequences = []
    for cell in BLOCK_CELL.findall(page):
        _, brace, sequence = html.unescape(cell).partition("{")
        assert brace, "a Block cell carries no hex sequence"
        sequences.append(sequence.rsplit("}", 1)[0])
    return sequences


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


# Issue #207: `?samid=` asked `isSampleId` and then `getSampleById` for the same id,
# and the match diagram asked `isSampleId` for the reference sample before
# `getFunctionsBySampleId` - both fetches answer None for an id the backend does not
# know, so each check was one request more than the question needed.

def _sample_lookups(backend):
    return [(name, args) for name, args, _ in backend.calls if name in ("isSampleId", "getSampleById")]


@pytest.mark.parametrize("report", ["matches_for_sample", "matches_for_query"])
def test_the_sample_filter_looks_the_sample_up_once(client, as_role, widened_corpus_mcrit, app, report):
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: widened_corpus_mcrit
    as_role("visitor")
    sample_id = matched_sample_ids(report)[0]
    widened_corpus_mcrit.calls.clear()
    response = client.get(f"/data/result/{job_id_of(report)}?samid={sample_id}")
    assert response.status_code == 200
    assert b"are corrupted" not in response.data and b"Offset B" in response.data
    # the diagram was drawn on this request, off the reference sample's functions
    assert any(name == "getFunctionsBySampleId" for name, _, _ in widened_corpus_mcrit.calls)
    assert _sample_lookups(widened_corpus_mcrit) == [("getSampleById", (sample_id,))]


def test_an_unknown_sample_filter_falls_back_to_the_whole_report(client, as_role):
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    unfiltered = client.get(f"/data/result/{job_id}")
    response = client.get(f"/data/result/{job_id}?samid=987654321")
    assert response.status_code == 200
    # the sample-filtered page has an Offset B column; the report overview does not
    assert b"Offset B" not in response.data
    assert response.data.count(b"<tr") == unfiltered.data.count(b"<tr")


def test_a_job_id_nobody_knows_is_reported_not_crashed(client, as_role):
    as_role("visitor")
    response = client.get("/data/result/ffffffffffffffffffffffff")
    assert response.status_code == 200
    assert b"was not found in the system" in response.data


def test_a_finished_job_with_an_empty_result_says_the_result_is_empty(client, as_role, corpus_mcrit, monkeypatch):
    """An empty report is a result, not a missing one.

    `data.result` opens with `if result_json:`, and {} is falsy, so a finished job
    whose report is empty skipped the whole dispatch and fell through to the page for
    an unknown job id - which told the reader their job did not exist. Issue #73.
    """
    as_role("visitor")
    monkeypatch.setattr(corpus_mcrit, "getResultForJob", lambda *args, **kwargs: {})

    response = client.get(f"/data/result/{job_id_of('matches_for_sample')}")

    assert response.status_code == 200
    # the sentence in result_empty.html
    assert b"does not contain any data" in response.data
    # ... and not the one in result_invalid.html
    assert b"was not found in the system" not in response.data


# --- the other reasons a finished job has no report to show -------------------
#
# The first pass distinguished "empty" from "unknown job id" and then merged three
# further reasons back together. Each of these was reported as something it is not.

def _with_job(corpus_mcrit, monkeypatch, **changes):
    """The corpus's 1vN job, with fields overridden, and no result to hand back."""
    job_data = copy.deepcopy(corpus_mcrit.getJobData(job_id_of("matches_for_sample"))._data)
    job_data.update(changes)
    monkeypatch.setattr(corpus_mcrit, "getJobData", lambda *args, **kwargs: Job(job_data, None))
    monkeypatch.setattr(corpus_mcrit, "getResultForJob", lambda *args, **kwargs: None)
    return job_id_of("matches_for_sample")


def test_a_failed_job_is_not_reported_as_an_unknown_one(client, as_role, corpus_mcrit, monkeypatch):
    """result_invalid.html says the job "was not found in the system". For a failed job
    that is false in both halves - it is in the system and this view is holding its Job
    object. Merging failed into unknown is the same conflation issue #73 is about."""
    as_role("visitor")
    job_id = _with_job(corpus_mcrit, monkeypatch, attempts_left=0)

    response = client.get(f"/data/result/{job_id}")

    assert response.status_code == 200
    assert b"was not found in the system" not in response.data
    assert b"ran out of attempts" in response.data


def test_the_result_page_of_a_terminated_job_says_it_was_terminated(client, as_role, corpus_mcrit, monkeypatch):
    as_role("visitor")
    job_id = _with_job(corpus_mcrit, monkeypatch, terminated=True)

    response = client.get(f"/data/result/{job_id}")

    assert response.status_code == 200
    assert b"was not found in the system" not in response.data
    assert b"terminated before it could finish" in response.data


def test_a_report_that_cannot_be_fetched_is_not_reported_as_an_empty_one(client, as_role, corpus_mcrit, monkeypatch):
    """getResultForJob answers None both when the job produced no result and when the
    stored document can no longer be retrieved - a purged GridFS entry, a backend
    re-provisioned with the job metadata intact. job_info.result, the result id, is what
    separates them, and it was not being consulted.

    Saying "this job does not contain any data" for a report that merely cannot be
    fetched is a wrong analytical answer in a triage UI, not just wrong wording: it
    reads as "your matching run found nothing".
    """
    as_role("visitor")
    job_id = _with_job(corpus_mcrit, monkeypatch, result="6a7464fcffffffffffffffff")

    response = client.get(f"/data/result/{job_id}")

    assert response.status_code == 200
    assert b"does not contain any data" not in response.data
    assert b"was not found in the system" in response.data


def test_a_job_that_produced_no_result_at_all_says_it_is_empty(client, as_role, corpus_mcrit, monkeypatch):
    """The other side of the same test: no result id means nothing was ever produced."""
    as_role("visitor")
    job_id = _with_job(corpus_mcrit, monkeypatch, result=None)

    response = client.get(f"/data/result/{job_id}")

    assert response.status_code == 200
    assert b"does not contain any data" in response.data


def test_a_report_this_dispatch_cannot_render_is_reported_not_crashed(client, as_role, corpus_mcrit, monkeypatch):
    """The dispatch chain in `data.result` had no else, so a job method it does not
    know - a new one on the backend, say - returned None and Flask answered 500."""


def test_a_report_of_a_job_type_the_dispatch_does_not_know_is_reported_not_crashed(client, as_role, corpus_mcrit, monkeypatch):
    """The dispatch in `data.result` had no else, so a finished job whose type it does not
    know returned None and Flask answered 500. The three repair jobs mcrit 1.9.0 added
    were such types on a live instance."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    # deep copy: the corpus client hands out the dict it keeps, and Job wraps it
    job_data = copy.deepcopy(corpus_mcrit.getJobData(job_id)._data)
    job_data["payload"]["method"] = "someFutureMethod"
    monkeypatch.setattr(corpus_mcrit, "getJobData", lambda *args, **kwargs: Job(job_data, None))

    response = client.get(f"/data/result/{job_id}")

    assert response.status_code == 200
    assert b"incompatible with the requested interpretation" in response.data


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


def test_unique_blocks_statistics_carries_a_sample_version(client, as_role):
    """The report names samples by id only - `statistics["by_sample_id"]` is block
    counts - so the version has to be looked up on the backend (issue #80)."""
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('unique_blocks')}")

    assert response.status_code == 200
    statistics_table = statistics_table_of(response.data.decode())
    assert ">Version<" in statistics_table
    # the versions of the three win.citadel samples the captured report covers
    for version in ("1.3.5.1", "1.3.4.0", "0.0.1.1"):
        assert version in statistics_table, f"{version} missing from the statistics table"


def test_the_yara_copy_icon_is_wired_to_the_textareas_value(client, as_role):
    """Issue #80: the copy icon used to copy `$(element).html()` out of a detached
    textarea, so it handed back the rendered markup - HTML entities for `& < >`, and
    none of the edits the reader had made to a rule that is deliberately editable.

    A lint, because it is what CI can run: `tests/testBrowser.py` clicks this icon in
    Chromium and reads the clipboard back, but playwright is not a dependency of this
    project and CI does not install it, so that module skips there. Without something
    here the old implementation can be restored verbatim and the suite stays green.
    """
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('unique_blocks')}?tab=yara")
    page = response.data.decode()

    assert response.status_code == 200
    assert 'id="yara_text"' in page, "the rule textarea the icon names is not on the page"
    assert COPY_ICON.search(page), "no copy icon calls copyTextAreaToClipboard on #yara_text"

    # base.html carries a `copy_to_clipboard` of its own, hence the camel-cased
    # needle: it matches this page's helper and the pre-#80 one, and nothing else.
    helpers = [body for body in SCRIPT_BLOCK.findall(page) if "ToClipboard" in body]
    assert len(helpers) == 1, f"expected one YARA clipboard helper on the page, found {len(helpers)}"
    code = LINE_COMMENT.sub("", helpers[0])
    assert "textarea.value" in code, "the clipboard helper never reads the textarea's value"
    for shape in COPIES_THE_MARKUP:
        assert shape not in code, f"the clipboard helper reads {shape} - that is the markup, not the value"


def test_a_long_block_stays_valid_yara_when_it_wraps(client, as_role):
    """Issue #80, "copy to clipboard break with extensively long yara strings".

    The Block column is YARA syntax and is there to be copied out. It used to be
    wrapped by breaking every 80th character, which lands mid-byte far more often
    than not: 41 of the 51 sequences in the captured report were cut inside a
    token and 23 of those left an odd-length run like "6a3", which no YARA
    compiler will take. Short sequences never wrapped, so the damage only showed
    on the long ones the issue names.

    The page's own block-length filter is what puts those on the first page: the
    default order is by score, and the hundred highest-scoring blocks of the
    captured report are all short enough that nothing wraps at all.
    """
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('unique_blocks')}?tab=blocks&min_block_length=20")

    assert response.status_code == 200
    sequences = hex_sequences_of(response.data.decode())
    assert sequences, "no blocks rendered, so nothing was checked"
    wrapped = [sequence for sequence in sequences if "\n" in sequence]
    assert len(wrapped) == len(sequences) == 18, "the filtered page is no longer the eighteen long blocks it was"
    for sequence in wrapped:
        for token in sequence.split():
            assert HEX_TOKEN.match(token), f"{token!r} is not a YARA hex byte, in: {sequence!r}"


def test_the_yara_rule_names_the_function_each_picblock_came_from(client, as_role):
    """Issue #80, "maybe include function_id ... (more robustness)".

    mcrit picks the cover blind to which function a block sits in, so a rule can
    quietly end up fingerprinting one function - and `7 of them` then dies with
    the next recompile of it. The captured report spreads its ten blocks over
    eight functions (seven before #184 sorted the blocks ahead of the cover);
    naming them is what lets a reader see that at all.
    """
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('unique_blocks')}?tab=yara")
    page = html.unescape(response.data.decode())

    assert response.status_code == 200
    comments = RULE_PICBLOCK_COMMENT.findall(page)
    assert len(comments) == 10, f"expected the ten picblocks of the captured rule, found {len(comments)}"
    for pichash, tail in comments:
        assert tail.startswith(", function_id: "), f"{pichash} does not name the function it came from"
    # and the annotation is per block, not one id repeated over all of them
    assert len({tail for _, tail in comments}) == 8


def test_unique_blocks_statistics_table_carries_the_sorting_markup(client, as_role):
    """A markup lint, and named as one: it reads the attributes the sorting script
    reads and says nothing about what a click does. Deleting the script outright
    leaves this green, which is exactly the limit of what an HTML assertion can
    reach.

    `tests/testBrowser.py` is where the headers get clicked. That module needs
    playwright, which is not a dependency of this project and which CI does not
    install, so this is the half of the cover CI keeps.

    Worth linting even so: a formatted cell reads "2844 (66.76%)", which is not a
    number, so the raw count has to travel beside it or the column cannot be
    ordered at all.
    """
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('unique_blocks')}")
    page = response.data.decode()

    assert response.status_code == 200
    statistics_table = statistics_table_of(page)
    assert 'class="table table-hover sortable-table"' in statistics_table, "the sorting script only touches tables marked sortable-table"
    # one text column, Version, and one per count
    assert statistics_table.count('data-sort="text"') == 1
    assert statistics_table.count('data-sort="number"') == 4
    # the raw counts of sample 0, beside the cells that render them formatted
    for count in ("4260", "2844", "611"):
        assert f'data-sort-value="{count}"' in statistics_table, f"{count} carries no sortable value"
    assert any("sortable-table" in body for body in SCRIPT_BLOCK.findall(page)), "no script on the page acts on the marked tables"


def test_unique_blocks_family_page_reads_the_versions_off_the_family(client, as_role, fake_mcrit):
    """`getFamily` answers with the family's samples, and `result_unique_blocks` is
    holding that entry before it builds the statistics table - so the Version column
    of a family job costs no request of its own.

    Asserted on the rendered page rather than on `get_sample_versions` directly,
    because the unit test below can only say the helper prefers the family; it
    cannot say the view hands it a family that has any samples in it. That is a
    property of the backend, and it is what this one pins down.
    """
    as_role("visitor")
    fake_mcrit.calls.clear()

    response = client.get(f"/data/result/{job_id_of('unique_blocks')}")

    assert response.status_code == 200
    requested = collections.Counter(name for name, _, _ in fake_mcrit.calls)
    assert requested["getFamily"] == 1
    assert requested["getSampleById"] == 0, "the family already carried the samples, so nothing had to be fetched by id"
    assert requested["getSamplesByIds"] == 0
    # and the column is populated, so "no requests" cannot mean "no versions"
    assert "1.3.5.1" in statistics_table_of(response.data.decode())


def test_the_corpus_answers_the_two_family_endpoints_differently(corpus_mcrit):
    """A guard on the fixture the test above stands on.

    `/families` and `/families/{id}` do not return the same entry: storage does not
    keep a family's sample list, so the collection cannot carry one, and only
    `FamilyResource.on_get` fills `samples` in - for one family, and only when asked.
    Serving the richer shape from both would put samples somewhere the real backend
    never does, and a view leaning on that would pass here and fail in a browser.
    """
    assert corpus_mcrit.getFamily(1).samples, "a single family must arrive with its samples"
    assert corpus_mcrit.getFamily(1, with_samples=False).samples is None
    assert all(entry.samples is None for entry in corpus_mcrit.getFamilies().values())


def test_unique_blocks_page_survives_a_backend_that_lost_a_sample(client, as_role, fake_mcrit):
    """A sample the family no longer lists falls through to a lookup by id, and a
    backend that cannot resolve it answers None. The version column has nothing to
    show for that row, which is not a reason to lose the whole report."""
    as_role("visitor")
    family = fake_mcrit.getFamily(1)
    assert family.samples, "the captured family carries no samples - see tests/fixtures/regenerate.py"
    family.samples = {key: entry for key, entry in family.samples.items() if entry.sample_id != 2}
    # the batch answers nothing for it, and neither does the per-id fallback behind it
    fake_mcrit.getSamplesByIds = lambda *args, **kwargs: {}
    fake_mcrit.getSampleById = lambda sample_id, *args, **kwargs: None

    response = client.get(f"/data/result/{job_id_of('unique_blocks')}")

    assert response.status_code == 200
    statistics_table = statistics_table_of(response.data.decode())
    assert "1.3.5.1" in statistics_table, "the samples the family still lists lost their version"
    assert "0.0.1.1" not in statistics_table, "the sample nothing could resolve was given a version anyway"


class _StubSample:
    def __init__(self, sample_id, version):
        self.sample_id = sample_id
        self.version = version


class _StubFamily:
    def __init__(self, samples=None):
        self.samples = samples


class _StubEntries:
    """Stands in for `get_sample_entries`: answers from a dict and records each batch."""

    def __init__(self, samples):
        self.samples = samples
        self.batches = []

    def __call__(self, sample_ids):
        self.batches.append(list(sample_ids))
        return {sample_id: self.samples.get(sample_id) for sample_id in sample_ids}


def test_sample_versions_come_from_the_family_without_extra_requests(monkeypatch):
    """getFamily already answers with the family's samples, and result_unique_blocks
    has that entry in hand before the statistics table is built."""
    from mcritweb.views import data

    entries = _StubEntries({})
    monkeypatch.setattr(data, "get_sample_entries", entries)
    family = _StubFamily({"0": _StubSample(0, "1.0"), "1": _StubSample(1, "2.0")})

    assert data.get_sample_versions(family, [0, 1]) == {0: "1.0", 1: "2.0"}
    assert entries.batches == [[]]


def test_sample_versions_fetch_the_rest_in_one_batch(monkeypatch):
    """The sample-job case has no family, and a backend answering a family without
    its samples lands here too. The rows go to the backend as one batch (#191),
    not one request each."""
    from mcritweb.views import data

    entries = _StubEntries({7: _StubSample(7, "3.x"), 8: _StubSample(8, "4.x")})
    monkeypatch.setattr(data, "get_sample_entries", entries)

    assert data.get_sample_versions(None, [7, 8]) == {7: "3.x", 8: "4.x"}
    assert entries.batches == [[7, 8]]


def test_sample_versions_omit_a_sample_the_backend_no_longer_has(monkeypatch):
    from mcritweb.views import data

    entries = _StubEntries({})
    monkeypatch.setattr(data, "get_sample_entries", entries)

    assert data.get_sample_versions(_StubFamily(None), [7]) == {}
    assert entries.batches == [[7]]


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


# --- #42: ordering of the cross compare matrix ------------------------------------
#
# The five corpus samples of the captured cross compare, and what each ordering has
# to produce out of them:
#
#   id  family       version
#    0  win.citadel  1.3.5.1
#    1  win.citadel  1.3.4.0
#    2  win.citadel  0.0.1.1
#    4  win.vmzeus   3.x
#    6  win.dridex   (none)

#: What the backend clustered, i.e. the order the page shows when nothing is asked for.
CLUSTERED_ORDER = ["6", "4", "2", "0", "1"]
SAMPLE_ID_ORDER = ["0", "1", "2", "4", "6"]
FAMILY_ORDER = ["2", "1", "0", "6", "4"]

#: Every matrix on the page. The order is applied to all of them identically - it is
#: one global order, not a per-tab one - so a test that only looked at the active tab
#: would miss five sixths of the change.
CROSS_METHODS = {
    "unweighted",
    "score_weighted",
    "frequency_weighted",
    "nonlib_unweighted",
    "nonlib_score_weighted",
    "nonlib_frequency_weighted",
}


def cross_compare_page(client, query=""):
    response = client.get(f"/data/result/{job_id_of('cross_compare')}{query}")
    assert response.status_code == 200
    return response.data.decode()


def sortable_order(html):
    """Sample ids of the drag-and-drop list, which has to mirror what is on screen -
    otherwise a drag starts from an order the user is not looking at."""
    block = re.search(r'id="sortable">(.*?)</ul>', html, re.S)
    assert block is not None, "the cross compare page has no drag-and-drop list"
    return [item.split()[0] for item in re.findall(r"<li>(.*?)</li>", block.group(1), re.S)]


def matrix_orders(html):
    """method -> the sample id order of that matrix's rows.

    Only one matrix is rendered (#213); the others are filled in the browser on first
    click from the page's `cross-lazy-panes` JSON, so their order is read from there."""
    panes = re.finditer(r'id="pills-(\w+)" role="tabpanel"(.*?)(?=<div class="tab-pane|\Z)', html, re.S)
    orders = {
        pane.group(1): re.findall(r'<td class="id clickable"[^>]*>\s*(\d+)\s*</td>', pane.group(2))
        for pane in panes
    }
    lazy = re.search(r'<script type="application/json" id="cross-lazy-panes">(.*?)</script>', html, re.S)
    if lazy is not None:
        for method, pane in json.loads(lazy.group(1))["panes"].items():
            assert not orders.get(method), f"matrix {method} is both rendered and lazy"
            orders[method] = [str(sample_id) for sample_id in pane["order"]]
    return orders


def active_ordering_button(html):
    """Which of the three ordering buttons is rendered as pressed, by the order its own
    onclick asks for. `None` when none is - which is what a drag order looks like."""
    pressed = re.findall(r"<button onclick=\"[^\"]*[?&;]order=(\w+)[^\"]*\"[^>]*aria-pressed=\"true\"", html)
    assert len(pressed) <= 1, f"more than one ordering button is pressed: {pressed}"
    return pressed[0] if pressed else None


def assert_page_is_ordered(html, expected):
    assert sortable_order(html) == expected
    orders = matrix_orders(html)
    assert set(orders) == CROSS_METHODS, "not every matrix was found on the page"
    for method, order in orders.items():
        assert order == expected, f"matrix {method} is not in the requested order"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", CLUSTERED_ORDER),
        ("?order=clustered", CLUSTERED_ORDER),
        ("?order=sample_id", SAMPLE_ID_ORDER),
        ("?order=family", FAMILY_ORDER),
    ],
)
def test_cross_compare_renders_in_the_requested_named_order(client, as_role, query, expected):
    as_role("visitor")
    assert_page_is_ordered(cross_compare_page(client, query), expected)


def test_the_reset_button_asks_for_the_page_without_an_order(client, as_role):
    """Issue #42 (a), which was already implemented: dropping the parameters is the
    reset, because the view falls back to the backend's clustered_sequence."""
    as_role("visitor")
    html = cross_compare_page(client)
    assert f"window.location.href='/data/result/{job_id_of('cross_compare')}'" in html
    assert_page_is_ordered(html, CLUSTERED_ORDER)


def test_the_named_orderings_are_offered_on_the_page(client, as_role):
    as_role("visitor")
    html = cross_compare_page(client)
    for ordering in ("clustered", "sample_id", "family"):
        assert f"/data/result/{job_id_of('cross_compare')}?order={ordering}" in html
    assert html.count('aria-pressed="true"') == 1, "exactly one ordering is the active one"


def test_a_dragged_order_marks_none_of_the_named_orderings_active(client, as_role):
    """A ?custom= order is none of them, and saying otherwise would tell the user the
    matrix is in an order it is not in."""
    as_role("visitor")
    assert 'aria-pressed="true"' not in cross_compare_page(client, "?custom=1,0,2,4,6")


@pytest.mark.parametrize(
    "value",
    ["", "bogus", "Family", "sample_id%20", "clustered%2Cfamily", "../../etc/passwd", "family%3Bdrop"],
)
def test_an_ordering_nobody_offers_falls_back_to_the_clustered_one(client, as_role, value):
    as_role("visitor")
    html = cross_compare_page(client, f"?order={value}")
    assert_page_is_ordered(html, CLUSTERED_ORDER)
    # the buttons have to agree with the matrix. Falling back silently and then
    # highlighting nothing would tell the user the page is in a drag order it is not in.
    assert active_ordering_button(html) == "clustered"


def test_the_ordering_parameter_never_reaches_the_page(client, as_role):
    """It is a query parameter that decides what a template renders, so the thing to
    check is that its raw value is not one of the things rendered."""
    as_role("visitor")
    response = client.get(
        f"/data/result/{job_id_of('cross_compare')}?order=%22%3E%3Cscript%3Ealert%281%29%3C/script%3E"
    )
    assert response.status_code == 200
    assert b"alert(1)" not in response.data
    assert b"alert%281%29" not in response.data


def test_a_custom_order_still_works(client, as_role):
    """Backward compatibility: ?custom= is what the drag-and-drop list produces and
    what any already-bookmarked link carries."""
    as_role("visitor")
    assert_page_is_ordered(cross_compare_page(client, "?custom=1,0,2,4,6"), ["1", "0", "2", "4", "6"])


def test_a_custom_order_wins_over_a_named_one(client, as_role):
    """A drag is the more specific statement of intent, and it is what `changeorder()`
    sends - it builds a ?custom= link and does not carry the ?order= over."""
    as_role("visitor")
    assert_page_is_ordered(
        cross_compare_page(client, "?order=family&custom=1,0,2,4,6"), ["1", "0", "2", "4", "6"]
    )


def test_a_custom_order_naming_a_sample_outside_the_job_says_which_job(client, as_role):
    """The corrupted page needs the job to offer its "delete job data" button; this
    path used to hand it the report instead, rendering "Results for Job:  " and a
    data-post of /data/jobs//delete."""
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('cross_compare')}?custom=1,0,2,4,999")
    assert response.status_code == 200
    assert b"are corrupted" in response.data
    assert job_id_of("cross_compare").encode() in response.data


if __name__ == "__main__":
    unittest.main()
