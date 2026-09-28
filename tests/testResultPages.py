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

import pytest
from fixtureData import CorpusMcritClient, job_id_of, load
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
    assert int(shown.group(1)) == int(expected)


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
    """method -> the sample id order of that matrix's rows."""
    panes = re.finditer(r'id="pills-(\w+)" role="tabpanel"(.*?)(?=<div class="tab-pane|\Z)', html, re.S)
    return {
        pane.group(1): re.findall(r'<td class="id clickable"[^>]*>\s*(\d+)\s*</td>', pane.group(2))
        for pane in panes
    }


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
