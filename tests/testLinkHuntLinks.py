#!/usr/bin/python
"""The link cluster table is paginated, and long clusters are folded - issue #197.

The link hunt rendered every cluster, and every link of every cluster, into one table
on one page: nothing capped either. The cluster list now pages like the other ranked
tables of a result, and a cluster shows its first links with the rest behind a
<details> element, so the page stays readable without needing any script.
"""

import logging
import re

import pytest
from fixtureData import job_id_of

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The report whose link hunt, unfiltered, produces 16 clusters of 2 to 17 links.
REPORT = "matches_for_sample"

#: The body of the link cluster table.
CLUSTER_TABLE = re.compile(r"Link Clusters</h4>.*?<tbody>(.*?)</tbody>", re.S)

#: One link of a cluster: the offset of the function it starts from.
CLUSTER_LINK = re.compile(r"/explore/functions/\d+\">\s*(?:<b[^>]*>)?(0x[0-9a-f]+)")


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.fixture
def linkhunt(client, as_role):
    as_role("visitor")

    def _linkhunt(**query):
        response = client.get(f"/data/linkhunt/{job_id_of(REPORT)}", query_string={"filter_button_action": "clear", **query})
        assert response.status_code == 200
        return response.get_data(as_text=True)
    return _linkhunt


def cluster_rows(html):
    return re.findall(r"<tr.*?</tr>", CLUSTER_TABLE.search(html).group(1), re.S)


def rank_of(row):
    return int(re.search(r"<td[^>]*>\s*(\d+)\s*</td>", row).group(1))


def cluster_size_of(row):
    return int(re.findall(r"<td[^>]*>\s*(\d+)\s*</td>", row)[1])


def test_the_cluster_list_is_paginated(linkhunt):
    everything = cluster_rows(linkhunt(clul=250))
    first_page = cluster_rows(linkhunt())
    second_page = cluster_rows(linkhunt(clup=2))

    assert len(everything) == 16
    assert len(first_page) == 10
    assert [rank_of(row) for row in first_page + second_page] == list(range(1, 17))
    assert first_page + second_page == everything


def test_a_long_cluster_shows_its_first_links_and_folds_the_rest(linkhunt):
    rows = cluster_rows(linkhunt(clul=250))
    long_clusters = [row for row in rows if cluster_size_of(row) > 8]
    short_clusters = [row for row in rows if cluster_size_of(row) <= 8]
    assert long_clusters and short_clusters

    for row in long_clusters:
        size = cluster_size_of(row)
        shown, folded = row.split("<details>")
        assert len(CLUSTER_LINK.findall(shown)) == 8
        assert f"<summary>{size - 8} more</summary>" in folded
        # nothing is lost, only folded away
        assert len(CLUSTER_LINK.findall(folded)) == size - 8
    for row in short_clusters:
        assert "<details>" not in row
        assert len(CLUSTER_LINK.findall(row)) == cluster_size_of(row)


def test_paging_the_clusters_keeps_the_filters_and_the_individual_links_page(linkhunt):
    html = linkhunt(funp=2)
    links = re.findall(r'href="([^"]*clup=2[^"]*)"', html)

    assert links
    assert all("funp=2" in link and "filter_button_action=clear" in link for link in links)


def test_each_pagination_anchors_to_its_own_heading(linkhunt):
    html = linkhunt()
    ids = re.findall(r'<h[34] id="([^"]+)"', html)
    assert len(ids) == len(set(ids)), f"headings share an id: {ids}"
    assert 'id="linkhunt-clusters"' in html and 'id="linkhunt-links"' in html
