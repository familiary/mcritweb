#!/usr/bin/python
"""The aggregated function table of the 1-vs-N and family-filtered result pages - issue #194.

Each row of that table aggregates every match of one function of the reference sample.
The template used to ask getAggregatedFunctionMatches(start, limit) for its page,
which aggregates the whole filtered report and only then slices - so a page of a
hundred rows cost as much as the report is large. The view now aggregates just the
matches of the functions on the page, and these tests hold it to producing exactly
the rows the old call did.
"""

import logging
import unittest

import pytest
from fixtureData import job_id_of, load
from mcrit.storage.MatchingResult import MatchingResult

from mcritweb.views import data
from mcritweb.views.pagination import Pagination

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


#: the reports whose result page draws the aggregated function table
AGGREGATED_REPORTS = ["matches_for_sample", "matches_for_query"]


def filtered_results(report):
    """The report as the view would hand it to the table: unfiltered, narrowed to each
    family it matched, and put through two of the user filters that drop matches."""
    variants = [("unfiltered", MatchingResult.fromDict(load(f"{report}.result")))]
    for family_id in sorted({sample["family_id"] for sample in load(f"{report}.result")["matches"]["samples"]}):
        matching_result = MatchingResult.fromDict(load(f"{report}.result"))
        matching_result.filterToFamilyId(family_id)
        variants.append((f"famid={family_id}", matching_result))
    for filter_values in ({"filter_exclude_pic": True}, {"filter_function_min_score": 80, "filter_func_unique": True}):
        matching_result = MatchingResult.fromDict(load(f"{report}.result"))
        matching_result.setFilterValues(filter_values)
        matching_result.applyFilterValues()
        variants.append((str(filter_values), matching_result))
    return variants


@pytest.mark.parametrize("report", AGGREGATED_REPORTS)
@pytest.mark.parametrize("limit", [10, 100, 250])
def test_every_page_holds_the_rows_the_full_aggregation_slices_to(app, report, limit):
    mismatches = []
    for label, matching_result in filtered_results(report):
        num_rows = len(matching_result.getAggregatedFunctionMatches())
        # one past the last page as well: Pagination clamps it to the last one
        for page in range(1, num_rows // limit + 3):
            with app.test_request_context(f"/?funp={page}&funl={limit}"):
                pagination = Pagination(data.request, num_rows, limit=limit, query_param="funp", limit_param="funl")
            expected = matching_result.getAggregatedFunctionMatches(pagination.start_index, pagination.limit)
            if data.aggregate_function_matches_page(matching_result, pagination) != expected:
                mismatches.append(f"{report} {label} funl={limit} funp={page}")
    assert not mismatches, "page rows differ from the full aggregation:\n  " + "\n  ".join(mismatches)


def test_the_page_is_aggregated_without_touching_the_views_filtered_list(app):
    matching_result = MatchingResult.fromDict(load("matches_for_sample.result"))
    matching_result.filterToFamilyId(1)
    filtered_before = list(matching_result.filtered_function_matches)
    with app.test_request_context("/?funl=10"):
        pagination = Pagination(data.request, 100, limit=10, query_param="funp", limit_param="funl")
    data.aggregate_function_matches_page(matching_result, pagination)
    assert matching_result.filtered_function_matches == filtered_before


def result_pages(report):
    job_id = job_id_of(report)
    families = sorted({sample["family_id"] for sample in load(f"{report}.result")["matches"]["samples"]})
    pages = [f"/data/result/{job_id}", f"/data/result/{job_id}?funl=10&funp=3", f"/data/result/{job_id}?filter_exclude_pic=on&funl=25&funp=2"]
    pages += [f"/data/result/{job_id}?famid={family_id}&funl=10&funp=2" for family_id in families]
    return pages


@pytest.mark.parametrize("report", AGGREGATED_REPORTS)
def test_the_rendered_pages_are_unchanged(client, as_role, monkeypatch, report):
    """The whole page, byte for byte, against the page rendered the way it used to be:
    with the rows sliced out of the aggregation of the entire filtered report."""
    as_role("visitor")
    for url in result_pages(report):
        response = client.get(url)
        assert response.status_code == 200
        assert b'id="function-matches"' in response.data, f"{url} did not draw the function table"
        with monkeypatch.context() as patched:
            patched.setattr(data, "aggregate_function_matches_page", lambda matching_result, pagination: matching_result.getAggregatedFunctionMatches(pagination.start_index, pagination.limit))
            reference = client.get(url)
        assert response.data == reference.data, f"{url} renders differently"


@pytest.mark.parametrize("report", AGGREGATED_REPORTS)
@pytest.mark.parametrize("query", ["", "&famid=1"])
def test_the_table_rows_are_aggregated_from_the_page_alone(client, as_role, monkeypatch, report, query):
    """No aggregate-then-slice while rendering, and the rows come from an aggregation
    over the matches of the page's ten functions rather than of the whole report."""
    as_role("visitor")
    aggregations = []
    aggregate = MatchingResult.getAggregatedFunctionMatches

    def recording_aggregate(matching_result, start=None, limit=None, unfiltered=False):
        source = matching_result.function_matches if unfiltered else matching_result.filtered_function_matches
        aggregations.append((start, limit, {function_match.function_id for function_match in source}))
        return aggregate(matching_result, start, limit, unfiltered)

    monkeypatch.setattr(MatchingResult, "getAggregatedFunctionMatches", recording_aggregate)
    response = client.get(f"/data/result/{job_id_of(report)}?funl=10&funp=2{query}")
    assert response.status_code == 200

    assert [(start, limit) for start, limit, _ in aggregations if start is not None or limit is not None] == []
    assert sorted(len(function_ids) for _, _, function_ids in aggregations)[0] == 10


if __name__ == "__main__":
    unittest.main()
