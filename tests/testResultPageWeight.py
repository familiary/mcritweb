#!/usr/bin/python
"""What the three result pages weigh gzipped, with the fixes of the web guidance in.

A result page is sent in a first round trip only while it is small: TCP sends about 14.6 KB
before it waits for an answer, and Lighthouse's mobile run paints the page from that flight.
The pages grew by 3 KB each as the accessibility, keyboard, tooltip, form, security and
layout fixes were merged (13.9 to 17.1 KB for the sample's matches), and were brought back
to the weight they had before them by the changes this test keeps: icon titles from the
aria-label, the clipboard report, the keyboard handlers and the comments in cached scripts,
and the pages without the indentation of their templates (ADR-0022).

The budgets are the weights measured with a hundredth of headroom for the nonce and the
masked CSRF token, which are random and do not compress. Raise one when a page has to grow,
and say in the commit what for.
"""

import gzip

import pytest
from fixtureData import job_id_of

from mcritweb import compression

#: gzip -6 of the page, as compression.py sends it
BUDGETS = [
    (f"/data/result/{job_id_of('matches_for_sample')}", 14200),
    (f"/data/result/{job_id_of('matches_for_sample')}?famid=1", 13350),
    (f"/data/result/{job_id_of('cross_compare')}", 11200),
]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.mark.parametrize("path, budget", BUDGETS)
def test_a_result_page_stays_inside_its_gzipped_budget(client, as_role, path, budget):
    as_role("contributor")
    client.get(path)
    response = client.get(path, headers={"Accept-Encoding": "gzip"})

    assert response.headers["Content-Encoding"] == "gzip"
    body = gzip.decompress(response.get_data())
    assert len(gzip.compress(body, compression.LEVEL, mtime=0)) <= budget
    assert len(response.get_data()) <= budget
