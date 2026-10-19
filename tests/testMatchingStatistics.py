#!/usr/bin/python
"""The "Matching Method Statistics" table, over the matches a page is showing.

A match report carries one job-wide aggregation and `applyFilterValues()` never
revises it, so a result page narrowed to one family or sample used to state the whole
job's numbers - issue #38. `matching_statistics()` recomputes four of the five fields
over the matches the narrowing left standing instead: those in the function match list
*and* matched against a sample still in the sample table, because mcrit splits its
filters between those two lists and counting either alone follows only half of them.

The warrant for recomputing an aggregation in the front end at all is that it
reproduces the backend's own numbers *exactly* when nothing is filtered, for every
report shape the backend produces. That is what `test_agrees_with_the_backend`
asserts, and it is the test to keep: if it ever fails, this presentation helper has
started disagreeing with the matching, which is not this repository's to change.
"""

import json
import pathlib
import re
import unittest

import pytest
from fixtureData import job_id_of
from mcrit.storage.MatchingResult import MatchingResult

from mcritweb.views.matching_statistics import matching_statistics

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
# every captured report that carries a match_aggregation, i.e. every report these
# templates render. cross_compare and unique_blocks have no aggregation at all.
REPORTS = ["matches_for_sample", "matches_for_sample_vs", "matches_for_query"]
# num_self_matches is deliberately absent: MatcherInterface._summarizeMatches drops
# self-matches from the report's function list, so nothing here can recompute it.
RECOMPUTED_FIELDS = [
    "num_own_functions_matched",
    "num_foreign_functions_matched",
    "num_own_functions_matched_as_library",
    "bytes_matched",
]


def load_report(report):
    with open(FIXTURES / f"{report}.result.json") as report_file:
        return MatchingResult.fromDict(json.load(report_file))


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.mark.parametrize("report", REPORTS)
def test_agrees_with_the_backend(report):
    """Unfiltered, the recomputation must equal what the backend put in the report."""
    matching_result = load_report(report)
    statistics = matching_statistics(matching_result)

    for method in ("minhash", "pichash"):
        for field in RECOMPUTED_FIELDS:
            assert statistics[method][field] == matching_result.match_aggregation[method][field], f"{report}/{method}/{field}"


@pytest.mark.parametrize("report", REPORTS)
def test_self_matches_are_carried_through_unchanged(report):
    matching_result = load_report(report)
    statistics = matching_statistics(matching_result)

    assert not statistics["is_filtered"]
    for method in ("minhash", "pichash"):
        assert statistics[method]["num_self_matches"] == matching_result.match_aggregation[method]["num_self_matches"]


def test_filtering_to_a_family_narrows_the_statistics():
    """The defect itself: famid=3 is a four-function sliver of a 756-function report."""
    matching_result = load_report("matches_for_sample")
    matching_result.filterToFamilyId(3)
    statistics = matching_statistics(matching_result)

    assert statistics["is_filtered"]
    assert statistics["minhash"]["num_own_functions_matched"] == 4
    assert statistics["minhash"]["num_foreign_functions_matched"] == 7
    assert statistics["minhash"]["bytes_matched"] == 249
    # unchanged, because it cannot be recomputed - the table says so
    assert statistics["minhash"]["num_self_matches"] == matching_result.match_aggregation["minhash"]["num_self_matches"]


def test_filtering_to_a_sample_narrows_the_statistics():
    """The issue's own case: one win.dridex sample, three matched functions."""
    matching_result = load_report("matches_for_sample")
    matching_result.filterToSampleId(5)
    statistics = matching_statistics(matching_result)

    assert statistics["is_filtered"]
    assert statistics["minhash"]["num_own_functions_matched"] == 3
    assert statistics["minhash"]["num_foreign_functions_matched"] == 3
    assert statistics["minhash"]["bytes_matched"] == 197


def test_a_sample_level_filter_narrows_like_the_equivalent_family_link():
    """Two routes to the same two samples have to give the same numbers.

    `filter_family_name` narrows only the sample list, `?famid=` narrows the sample
    list and the function matches together. Both leave the win.dridex pair on screen,
    so the statistics must agree - they did not before `_on_screen_function_matches`
    intersected the two lists.
    """
    by_link = load_report("matches_for_sample")
    by_link.filterToFamilyId(3)

    by_filter = load_report("matches_for_sample")
    by_filter.setFilterValues({"filter_family_name": "win.dridex"})
    by_filter.applyFilterValues()

    assert {sample.sample_id for sample in by_filter.filtered_sample_matches} == {sample.sample_id for sample in by_link.filtered_sample_matches}
    assert matching_statistics(by_filter) == matching_statistics(by_link)
    assert matching_statistics(by_filter)["minhash"]["num_own_functions_matched"] == 4


def test_filtering_to_a_library_family_narrows_the_library_count():
    matching_result = load_report("matches_for_sample")
    matching_result.filterToFamilyId(4)
    statistics = matching_statistics(matching_result)

    assert statistics["minhash"]["num_own_functions_matched_as_library"] == 25
    # nothing in the MSVC family matched by pichash, so that column is empty rather
    # than absent
    assert statistics["pichash"]["num_own_functions_matched"] == 0
    assert statistics["pichash"]["bytes_matched"] == 0


def test_a_filter_that_keeps_nothing_gives_zeros():
    matching_result = load_report("matches_for_sample")
    matching_result.filterToFamilyId(0x7FFFFFFF)
    statistics = matching_statistics(matching_result)

    assert statistics["is_filtered"]
    for method in ("minhash", "pichash"):
        for field in RECOMPUTED_FIELDS:
            assert statistics[method][field] == 0


def test_a_score_filter_narrows_the_statistics_too():
    """Not only famid/samid: the score filters left the table equally wrong."""
    matching_result = load_report("matches_for_sample")
    matching_result.setFilterValues({"filter_function_min_score": 100})
    matching_result.applyFilterValues()
    statistics = matching_statistics(matching_result)

    assert statistics["is_filtered"]
    assert statistics["minhash"]["num_own_functions_matched"] < matching_result.match_aggregation["minhash"]["num_own_functions_matched"]


def statistics_table_of(page):
    """The rendered statistics table as {field: (minhash, pichash)}."""
    table = re.search(r"Matching Method Statistics.*?</table>", page, re.S)
    assert table is not None, "the page has no statistics table"
    rows = re.findall(
        r"<td valign=\"middle\">(.*?): </td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>",
        table.group(0),
        re.S,
    )
    # the field label is wrapped in a tooltip span on the row that cannot be filtered
    return {re.sub(r"<[^>]*>", "", field).strip(): (minhash.strip(), pichash.strip()) for field, minhash, pichash in rows}


def test_an_unfiltered_page_still_shows_the_backends_numbers(client, as_role):
    as_role("visitor")
    page = client.get(f"/data/result/{job_id_of('matches_for_sample')}")
    assert page.status_code == 200

    table = statistics_table_of(page.data.decode())
    assert table["num_own_functions_matched"] == ("756", "587")
    assert table["bytes_matched"] == ("151654.0", "87582.0")
    assert table["num_self_matches"] == ("183", "50")


def test_a_family_filtered_page_shows_that_familys_numbers(client, as_role):
    as_role("visitor")
    page = client.get(f"/data/result/{job_id_of('matches_for_sample')}?famid=3")
    assert page.status_code == 200

    table = statistics_table_of(page.data.decode())
    assert table["num_own_functions_matched"] == ("4", "1")
    assert table["num_foreign_functions_matched"] == ("7", "1")
    assert table["bytes_matched"] == ("249.0", "52.0")
    # the one field that cannot follow says so instead of pretending
    assert "num_self_matches (whole job)" in table
    assert table["num_self_matches (whole job)"] == ("183", "50")


def test_a_sample_filtered_page_agrees_with_the_family_filtered_one(client, as_role):
    """The defect end to end: two links, the same two samples, the same table.

    Before the intersection, `?famid=3` rendered 4 / 249.0 and
    `?filter_family_name=win.dridex` rendered the whole job's 756 / 151654.0, on a page
    whose sample table showed exactly the same win.dridex pair.
    """
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")

    by_link = client.get(f"/data/result/{job_id}?famid=3")
    by_filter = client.get(f"/data/result/{job_id}?filter_family_name=win.dridex")
    assert by_link.status_code == 200
    assert by_filter.status_code == 200

    table = statistics_table_of(by_filter.data.decode())
    assert table == statistics_table_of(by_link.data.decode())
    assert table["num_own_functions_matched"] == ("4", "1")
    assert table["bytes_matched"] == ("249.0", "52.0")


def test_a_sample_narrowed_page_renders_and_its_statistics_follow(client, as_role):
    """`?samid=` at page level, on the one report whose by-id pool reaches that far.

    The 1-vs-corpus report cannot render `?samid=` offline - `assign_matched_offsets`
    needs matched entries the fixtures do not carry - but the 1-vs-1 report's pool is
    complete by construction (see tests/fixtures/README.md), so its `?samid=` page does
    render. It matched a single sample, so narrowing to that sample is a no-op and the
    table must stay unlabelled; adding a function filter on top narrows it and brings
    the `(whole job)` marker out.
    """
    as_role("visitor")
    job_id = job_id_of("matches_for_sample_vs")

    page = client.get(f"/data/result/{job_id}?samid=3")
    assert page.status_code == 200
    table = statistics_table_of(page.data.decode())
    assert table["num_own_functions_matched"] == ("422", "273")
    assert table["num_self_matches"] == ("184", "46")

    page = client.get(f"/data/result/{job_id}?samid=3&filter_exclude_pic=on")
    assert page.status_code == 200
    table = statistics_table_of(page.data.decode())
    assert table["num_own_functions_matched"] == ("149", "0")
    assert table["num_self_matches (whole job)"] == ("184", "46")


#: Every filter `MatchingResult.applyFilterValues` understands, and which of its two
#: lists that filter rebinds: "samples" for the ones above the sample table, "functions"
#: for the ones above the function table. The split is the whole reason this table
#: exists - counting either list alone follows only half the filters - and the test
#: below asserts that the statistics now narrow for all nine either way.
FILTERS_AND_THE_LIST_THEY_NARROW = [
    ({"filter_direct_min_score": 20}, "samples"),
    ({"filter_direct_nonlib_min_score": 20}, "samples"),
    ({"filter_frequency_min_score": 20}, "samples"),
    ({"filter_unique_only": True}, "samples"),
    ({"filter_exclude_own_family": True}, "samples"),
    ({"filter_family_name": "win.citadel"}, "samples"),
    ({"filter_exclude_library": True}, "functions"),
    ({"filter_exclude_pic": True}, "functions"),
    ({"filter_func_unique": True}, "functions"),
]


@pytest.mark.parametrize(("filter_values", "narrowed_list"), FILTERS_AND_THE_LIST_THEY_NARROW)
def test_every_filter_reaches_the_statistics(filter_values, narrowed_list):
    report = json.loads((FIXTURES / "matches_for_sample.result.json").read_text())

    unfiltered = MatchingResult.fromDict(report)
    filtered = MatchingResult.fromDict(report)
    filtered.setFilterValues(filter_values)
    filtered.applyFilterValues()

    # the row's second column, checked rather than trusted: these are mcrit's
    # semantics, and a release that moved a filter between the two groups would
    # otherwise silently make the case below untested
    if narrowed_list == "samples":
        assert len(filtered.filtered_sample_matches) < len(unfiltered.filtered_sample_matches)
    else:
        assert len(filtered.filtered_function_matches) < len(unfiltered.filtered_function_matches)

    # the foreign-function count is the field that has to move: a foreign function
    # belongs to exactly one matched sample, so dropping any match at all drops one.
    # The own-function count does not - the same own function typically matches in
    # several samples, so six of these nine leave it at 756 while narrowing hard.
    statistics = matching_statistics(filtered)
    assert statistics["is_filtered"]
    assert statistics["minhash"]["num_foreign_functions_matched"] < matching_statistics(unfiltered)["minhash"]["num_foreign_functions_matched"]


if __name__ == "__main__":
    unittest.main()
