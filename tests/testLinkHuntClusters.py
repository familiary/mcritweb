#!/usr/bin/python
"""The link hunt clusters once per job and filter set, not once per request - issue #187.

Clustering fetches every function of the reference sample, control flow graphs
included, and walks their call references. `data.linkhunt` did that on every request,
so turning a page of the individual links or tightening the link score filter - which
changes neither what is clustered nor how - paid for a full clustering again.

The filters handed to getLinkHuntResults decide which links are clustered, so they are
part of what a clustering is cached under. The link score filter is applied to the
clusters afterwards, as it always was.
"""

import logging
import re

import pytest
from fixtureData import job_id_of

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The report whose link hunt produces clusters with more than one link.
REPORT = "matches_for_sample"

#: The link hunt page's own defaults for REPORT, whose reference sample is in family 1,
#: spelled out so a test can vary one of them.
DEFAULT_FILTERS = {
    "filter_button_action": "filter",
    "filter_min_score": 65,
    "filter_lib_min_score": 80,
    "filter_link_score": 30,
    "filter_min_size": 50,
    "filter_unpenalized_family_count": 2,
    "filter_exclude_families": 1,
    "filter_strongest_per_family": "on",
}

#: The body of the link cluster table.
CLUSTER_TABLE = re.compile(r"Link Clusters</h4>.*?<tbody>(.*?)</tbody>", re.S)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.fixture
def linkhunt(client, as_role):
    as_role("visitor")

    def _linkhunt(**query):
        response = client.get(f"/data/linkhunt/{job_id_of(REPORT)}", query_string=query)
        assert response.status_code == 200
        return response
    return _linkhunt


def function_fetches(corpus_mcrit):
    return [args for name, args, _ in corpus_mcrit.calls if name == "getFunctionsBySampleId"]


def cluster_rows(response):
    return re.findall(r"<tr.*?</tr>", CLUSTER_TABLE.search(response.get_data(as_text=True)).group(1), re.S)


def test_paging_and_the_link_score_filter_reuse_the_clustering(linkhunt, corpus_mcrit):
    linkhunt()
    linkhunt(funp=2)
    linkhunt(**DEFAULT_FILTERS)
    linkhunt(**{**DEFAULT_FILTERS, "filter_link_score": 200})

    assert len(function_fetches(corpus_mcrit)) == 1


#: One changed value per argument of getLinkHuntResults, each of which changes what is
#: clustered. Leaving any of them out of the key would serve a stale clustering.
CLUSTERING_FILTER_CHANGES = {
    "min_score": {"filter_min_score": 90},
    "lib_min_score": {"filter_lib_min_score": 95},
    "min_size": {"filter_min_size": 100},
    "min_offset": {"filter_min_offset": "0x1000"},
    "max_offset": {"filter_max_offset": "0x10000"},
    "unpenalized_family_count": {"filter_unpenalized_family_count": 5},
    "exclude_families": {"filter_exclude_families": "1, 2"},
    "exclude_samples": {"filter_exclude_samples": "3"},
    "strongest_per_family": {"filter_strongest_per_family": None},
}


@pytest.mark.parametrize("change", CLUSTERING_FILTER_CHANGES.values(), ids=CLUSTERING_FILTER_CHANGES.keys())
def test_a_filter_that_changes_what_is_clustered_clusters_again(linkhunt, corpus_mcrit, change):
    linkhunt(**DEFAULT_FILTERS)
    # a None drops the parameter, which is how an unticked checkbox arrives
    changed = {name: value for name, value in {**DEFAULT_FILTERS, **change}.items() if value is not None}
    linkhunt(**changed)

    assert len(function_fetches(corpus_mcrit)) == 2


@pytest.mark.parametrize(
    "query",
    [
        {},
        DEFAULT_FILTERS,
        {**DEFAULT_FILTERS, "filter_link_score": 200},
        {"filter_button_action": "clear"},
    ],
)
def test_cached_clusters_render_as_a_fresh_clustering_does(linkhunt, corpus_mcrit, query):
    fresh = cluster_rows(linkhunt(**query))
    cached = cluster_rows(linkhunt(**query))

    assert len(function_fetches(corpus_mcrit)) == 1
    assert cached == fresh
    assert fresh, "the report should produce clusters for this to compare anything"


def test_the_link_score_filter_applies_to_cached_clusters(linkhunt, corpus_mcrit):
    unfiltered = cluster_rows(linkhunt(**{**DEFAULT_FILTERS, "filter_link_score": ""}))
    filtered = cluster_rows(linkhunt(**{**DEFAULT_FILTERS, "filter_link_score": 200}))
    unfiltered_again = cluster_rows(linkhunt(**{**DEFAULT_FILTERS, "filter_link_score": ""}))

    assert len(function_fetches(corpus_mcrit)) == 1
    assert 0 < len(filtered) < len(unfiltered)
    # filtering a cached clustering must not have filtered the cached copy
    assert unfiltered_again == unfiltered


def test_a_server_reset_forgets_the_clusterings(client, as_role, linkhunt, corpus_mcrit, monkeypatch):
    """A reset restarts the backend's id counters, so a job id can come back meaning another job."""
    linkhunt()
    monkeypatch.setattr(corpus_mcrit, "respawn", lambda *args, **kwargs: None, raising=False)
    as_role("admin")
    response = client.post("/admin/reset_server", data={"reset_server": "RESET"})
    assert response.status_code == 302

    linkhunt()
    assert len(function_fetches(corpus_mcrit)) == 2
