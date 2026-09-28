#!/usr/bin/python
"""Ordering the job list, and the jobs a cross compare is made of. Issue #51.

The issue opens with "sort should be done in mcrit and not only consider displayed
elements". What the page had was a DataTable configured against `#job-table` while the
table is rendered with `id="{{ active }}"`, so it never initialised at all - and had it
initialised, it would have reordered the 25 rows on screen and left the other pages of
the queue alone, which is the complaint verbatim.

mcrit can only order a queue one way, by job creation: `getQueueData(ascending=...)`
reaches `mongoqueue.get_jobs`, which sorts by `_id` *before* it skips and limits, so the
order is a property of the queue and not of the page. Nothing in the chain accepts a
sort key, so Type/Started/Finished/Progress cannot be ordered across pages at all; the
`#` column is offered as a link and the other columns are honestly inert.

The third bullet, "what about cross jobs?", turned out to have a concrete answer. A cross
compare runs as one `getMatchesForSampleVsGroup` job per sample plus a
`combineMatchesToCross` job that merges them. mcrit's `Job.method_types` lists neither
the group jobs under "matching" nor in "all", and the jobs page builds its category menu
from that, so the jobs a cross compare does its work in had no tab: they could not be
browsed, ordered or searched, and the "Matching" count left them out while the totals row
above it counted them.
"""

import html
import logging
import re
import unittest

import pytest
from mcrit.queue.LocalQueue import Job

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


class OrderedQueue:
    """Two categories of jobs, ordered as a whole before they are paged.

    That is mcrit's own semantics: `get_jobs` sorts the collection and only then applies
    skip/limit, so a fake that pages first could not tell a queue-wide order from a
    per-page one - which is the distinction every test here turns on.
    """

    CROSS_GROUP_METHOD = "getMatchesForSampleVsGroup"

    def __init__(self, size=100, cross_group_size=5):
        self.size = size
        self.cross_group_size = cross_group_size
        self.calls = []

    def _job(self, index, method):
        if method == self.CROSS_GROUP_METHOD:
            params = '{"0": %d, "1": [0, 1], "2": 2}' % index
        else:
            params = '{"0": %d, "1": "sample%d.exe"}' % (index, index)
        return Job({
            "_id": f"{method}{index:04d}",
            "number": index,
            "payload": {"method": method, "params": params,
                        "file_params": "{}", "descriptor": None},
            "all_dependencies": [],
            "created_at": {"$date": "2026-01-01T00:00:00.000Z"},
            "started_at": {"$date": "2026-01-01T00:00:01.000Z"},
            "finished_at": {"$date": "2026-01-01T00:00:02.000Z"},
            "last_error": None, "terminated": False, "attempts_left": 3,
            "progress": 1, "result": "r",
        }, None)

    def getQueueStatistics(self, *args, **kwargs):
        return {"getMatchesForSample": {"finished": self.size},
                self.CROSS_GROUP_METHOD: {"finished": self.cross_group_size}}

    def getQueueData(self, start=0, limit=0, method=None, filter=None, state=None, ascending=False):
        self.calls.append(("getQueueData", (), {"start": start, "limit": limit, "method": method,
                                                "filter": filter, "state": state, "ascending": ascending}))
        count = self.cross_group_size if method == self.CROSS_GROUP_METHOD else self.size
        jobs = [self._job(index, method or "getMatchesForSample") for index in range(count)]
        if not ascending:
            jobs.reverse()
        if limit:
            jobs = jobs[start:start + limit]
        return jobs

    def getSampleById(self, *args, **kwargs):
        return None

    def getFamily(self, *args, **kwargs):
        return None


@pytest.fixture
def ordered_queue(app, as_role):
    backend = OrderedQueue()
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend
    as_role("visitor")
    return backend


def order_toggle(page):
    """The link the job number column offers, which is the page's only sort control."""
    match = re.search(r'<a href="([^"]*)" title="Order the whole queue by job number">', page)
    assert match, "the job number column does not offer the other order"
    return html.unescape(match.group(1))


def test_the_job_number_column_offers_the_other_order(client, ordered_queue):
    """Newest first is the default, so the link on offer is the ascending one, and from
    there it must lead back - a toggle that only goes one way is a dead end."""
    descending = client.get("/data/jobs").get_data(as_text=True)
    assert "ascending=true" in order_toggle(descending)

    ascending = client.get(order_toggle(descending)).get_data(as_text=True)
    assert "ascending=false" in order_toggle(ascending)


def test_the_order_covers_the_whole_queue_and_not_the_page(client, ordered_queue):
    """The point of the issue. 100 jobs, 25 to a page: reversing the order has to bring
    up the far end of the queue, which a sort of the rows on screen could never do."""
    descending = client.get("/data/jobs").get_data(as_text=True)
    ascending = client.get(order_toggle(descending)).get_data(as_text=True)

    assert "getMatchesForSample0099" in descending and "getMatchesForSample0000" not in descending
    assert "getMatchesForSample0000" in ascending and "getMatchesForSample0099" not in ascending


def test_flipping_the_order_starts_over_at_the_first_page(client, ordered_queue):
    """Page 3 of one order is an unrelated slice of the other, so carrying the page
    number across would land you somewhere you did not ask to be."""
    page_three = client.get("/data/jobs?p=3").get_data(as_text=True)

    assert "p=" not in order_toggle(page_three)


def test_the_order_toggle_keeps_what_you_were_looking_at(client, ordered_queue):
    """Reordering should rearrange the list you have, not hand you a different one."""
    page = client.get("/data/jobs?active=getMatchesForSample&Search=sample1").get_data(as_text=True)

    link = order_toggle(page)
    assert "active=getMatchesForSample" in link
    assert "Search=sample1" in link


def test_the_order_toggle_carries_only_what_the_page_understands(client, ordered_queue):
    """The link is built with url_for, and url_for reads `_method`, `_scheme` and friends
    out of the keyword arguments it is handed - so a query string forwarded wholesale is
    a query string that gets to steer it, `_external` here being the mildest of them. It
    only ever needs five parameters."""
    page = client.get("/data/jobs?active=getMatchesForSample&_external=true&zzz=1").get_data(as_text=True)

    link = order_toggle(page)
    assert link.startswith("/data/jobs?"), "the query string steered url_for"
    assert "active=getMatchesForSample" in link
    assert "zzz" not in link


def test_the_page_does_not_sort_the_rows_it_has_client_side(client, ordered_queue):
    """A DataTable over the page would reorder 25 of 100 jobs and call it a sort, which
    is what the issue is about. It also never ran here - it selected an id the page does
    not render - so there was nothing to keep working."""
    page = client.get("/data/jobs").get_data(as_text=True)

    assert ".DataTable(" not in page


def test_the_jobs_a_cross_compare_runs_can_be_listed(client, ordered_queue):
    """getMatchesForSampleVsGroup is missing from mcrit's Job.method_types, which is
    where the category menu comes from, so the jobs behind every cross compare had no
    tab to be listed, ordered or searched under."""
    page = client.get("/data/jobs").get_data(as_text=True)
    assert "active=getMatchesForSampleVsGroup" in page

    listed = client.get("/data/jobs?active=getMatchesForSampleVsGroup").get_data(as_text=True)
    assert "getMatchesForSampleVsGroup0004" in listed


def test_the_matching_count_includes_the_jobs_cross_compares_run(client, ordered_queue):
    """100 matching jobs plus the 5 group jobs of a cross compare. Leaving the group jobs
    out made the tab disagree with the totals row above it, which counts every job."""
    page = client.get("/data/jobs").get_data(as_text=True)

    assert "Matching (105)" in page


if __name__ == "__main__":
    unittest.main()
