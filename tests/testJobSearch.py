#!/usr/bin/python
"""Searching the job list.

The jobs page had a search box that was commented out in the template, and a view
that read `request.form['Search']` into a variable it never used - so the feature
looked half-present and did nothing. Issue #51.

`McritClient.getQueueData` takes a `filter` string, and the obvious implementation is
to pass the term straight through. That is wrong, and `PagedQueue` below is the fake
that shows why: mcrit slices the requested page *first* and filters what is left

    return [job._data for job in self.queue.get_jobs(start_index, limit, method, state,
            ascending) if filter in job.parameters]

so `start=0, limit=25, filter=x` answers "the matches among jobs 0-24", not "the first
25 matches". On any queue longer than a page that makes the feature worse than absent:
page 1 of a search whose only hit is job 60 renders empty, and the pagination offers
pages counted from the *unfiltered* total, so the match is only reachable by guessing
which page it is on. The filter is therefore applied here, over an unpaged fetch, where
the count that drives pagination is the count of rows actually shown.
"""

import logging
import unittest

import pytest
from mcrit.queue.LocalQueue import Job

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def queue_filters(backend):
    """The `filter` argument of every getQueueData call the request made."""
    return [kwargs.get("filter") for name, args, kwargs in backend.calls if name == "getQueueData"]


class PagedQueue:
    """A queue of `size` jobs, one of which matches, implementing mcrit's own semantics.

    The corpus fake ignores `start`, `limit` and `filter` and hands back the whole
    queue, so no test written against it can observe a paging bug. This one slices
    before it filters, exactly as QueueRemoteCalls.getQueueData does.
    """

    def __init__(self, size=100, needle="evil.exe", needle_at=60):
        self.size = size
        self.needle = needle
        self.needle_at = needle_at
        self.calls = []

    def _params(self, index):
        name = self.needle if index == self.needle_at else f"benign{index}.exe"
        return '{"0": %d, "1": "%s"}' % (index, name)

    def _job(self, index):
        return Job({
            "_id": f"job{index:04d}",
            "number": index,
            "payload": {"method": "getMatchesForSample", "params": self._params(index),
                        "file_params": "{}", "descriptor": None},
            "all_dependencies": [],
            "created_at": {"$date": "2026-01-01T00:00:00.000Z"},
            "started_at": {"$date": "2026-01-01T00:00:01.000Z"},
            "finished_at": {"$date": "2026-01-01T00:00:02.000Z"},
            "last_error": None, "terminated": False, "attempts_left": 3,
            "progress": 1, "result": "r",
        }, None)

    def getQueueStatistics(self, *args, **kwargs):
        return {"getMatchesForSample": {"finished": self.size}}

    def getQueueData(self, start=0, limit=0, method=None, filter=None, state=None, ascending=False):
        self.calls.append(("getQueueData", (), {"start": start, "limit": limit, "method": method,
                                                "filter": filter, "state": state, "ascending": ascending}))
        jobs = [self._job(i) for i in range(self.size)]
        if limit:
            jobs = jobs[start:start + limit]
        if filter is not None:
            jobs = [job for job in jobs if filter in job.parameters]
        return jobs

    def getSampleById(self, *args, **kwargs):
        return None

    def getFamily(self, *args, **kwargs):
        return None


@pytest.fixture
def paged_queue(app, as_role):
    """Wire the app to PagedQueue and log in."""
    backend = PagedQueue()
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend
    as_role("visitor")
    return backend


def test_the_jobs_page_offers_a_search_box(client, as_role):
    as_role("visitor")
    page = client.get("/data/jobs").get_data(as_text=True)

    assert 'name="Search"' in page
    assert 'placeholder="Search jobs"' in page


def test_a_search_finds_a_match_that_is_not_on_the_first_page(client, paged_queue):
    """The one that fails if the term is handed to the backend's own filter: the only
    matching job is number 60, so a filtered request for jobs 0-24 returns nothing."""
    page = client.get("/data/jobs?Search=evil.exe").get_data(as_text=True)

    assert "job0060" in page, "the match was not on the page the search landed on"
    assert "1 job matching" in page


def test_a_search_does_not_ask_the_backend_to_filter(client, paged_queue):
    """Because it cannot do it correctly alongside paging - see the module docstring.
    This also keeps the term out of the URL the client builds, which it interpolates
    without encoding."""
    assert queue_filters(paged_queue) == []
    client.get("/data/jobs?Search=evil.exe")
    assert all(value is None for value in queue_filters(paged_queue))


def test_the_pages_are_counted_from_the_matches_not_the_whole_queue(client, paged_queue):
    """100 jobs, 1 match, 25 to a page: a search must offer one page, not four."""
    page = client.get("/data/jobs?Search=evil.exe").get_data(as_text=True)
    unfiltered = client.get("/data/jobs").get_data(as_text=True)

    assert page.count('class="page-item ') < unfiltered.count('class="page-item ')
    assert "p=4" not in page


def test_a_search_that_matches_everything_still_pages(client, paged_queue):
    """The filter must not collapse paging: 100 matches is still four pages."""
    first = client.get("/data/jobs?Search=.exe").get_data(as_text=True)
    second = client.get("/data/jobs?Search=.exe&p=2").get_data(as_text=True)

    assert "100 jobs matching" in first
    assert "job0000" in first and "job0000" not in second
    assert "job0025" in second


def test_the_search_is_case_insensitive(client, paged_queue):
    """The backend's filter was a case-sensitive `in`. Doing it here means choosing,
    and a search box that misses EVIL.EXE for evil.exe is a surprise."""
    page = client.get("/data/jobs?Search=EVIL.EXE").get_data(as_text=True)

    assert "job0060" in page


def test_no_search_term_means_no_filter(client, as_role, fake_mcrit):
    """An empty box must not narrow anything - `filter=""` would be a substring test
    that happens to match everything today, but saying None is what we mean."""
    as_role("visitor")

    client.get("/data/jobs")

    assert queue_filters(fake_mcrit) == [None]


def test_whitespace_only_is_treated_as_no_search(client, as_role, fake_mcrit):
    as_role("visitor")

    client.get("/data/jobs?Search=%20%20")

    assert queue_filters(fake_mcrit) == [None]


def test_the_page_says_what_it_searched_for_and_how_much_it_found(client, paged_queue):
    page = client.get("/data/jobs?Search=evil.exe").get_data(as_text=True)

    assert '1 job matching "evil.exe"' in page


def test_a_search_that_matches_nothing_does_not_offer_to_create_a_first_job(client, paged_queue):
    """The table's empty state reads "No jobs available. Click here to create your
    first job" - the wrong thing to say to someone whose search missed on a full
    queue, and the reading of it is "there are no jobs", which is false."""
    page = client.get("/data/jobs?Search=zzznomatchzzz").get_data(as_text=True)

    assert "create your first job" not in page
    assert 'No job\'s parameters contain "zzznomatchzzz"' in page
    assert "0 jobs matching" in page


def test_the_search_term_is_escaped(client, as_role):
    """It is echoed into both a value= attribute and the page body, and it is whatever
    somebody typed."""
    as_role("visitor")

    page = client.get("/data/jobs?Search=%22%3E%3Cimg+src%3Dx+onerror%3Dalert(1)%3E").get_data(as_text=True)

    assert '"><img src=x onerror=alert(1)>' not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page


def test_searching_keeps_the_category_you_were_looking_at(client, as_role):
    """Otherwise typing in the box silently throws away the tab you had open."""
    as_role("visitor")

    page = client.get("/data/jobs?active=getMatchesForSample").get_data(as_text=True)

    assert '<input type="hidden" name="active" value="getMatchesForSample">' in page


def test_searching_keeps_the_sort_order_you_had(client, as_role):
    """Same reasoning as the category above - searching should narrow the list, not
    quietly reset how it is arranged."""
    as_role("visitor")

    page = client.get("/data/jobs?ascending=true").get_data(as_text=True)

    assert '<input type="hidden" name="ascending" value="true">' in page


def test_the_jobs_page_no_longer_accepts_a_post(client, as_role):
    """A search is a read. The POST branch it used to have raised BadRequestKeyError
    on `request.form['Search']` for any POST that did not carry the field."""
    as_role("visitor")

    assert client.post("/data/jobs", data={"Search": "anything"}).status_code == 405


class CorruptJobInTheQueue(PagedQueue):
    """One job whose payload cannot be parsed, in the middle of the category.

    Job.parameters does not return None for such a job - it raises. A truncated Mongo
    write, a hand-edit, or a document from an older mcrit produces one.
    """

    def _params(self, index):
        if index == 40:
            return "{not json"
        return super()._params(index)


@pytest.fixture
def corrupt_queue(app, as_role):
    backend = CorruptJobInTheQueue()
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend
    as_role("visitor")
    return backend


def test_one_unreadable_job_does_not_break_the_whole_search(client, corrupt_queue):
    """Filtering the category rather than a page means the search touches every job in
    it, so a single unreadable one would take the search down for the entire category
    - where before it only broke the one page of the browse view that showed it."""
    response = client.get("/data/jobs?Search=evil.exe")

    assert response.status_code == 200
    assert b"job0060" in response.data, "the match is still found"


def test_the_unreadable_job_is_not_listed_as_a_match(client, corrupt_queue):
    """It cannot contain the term - nothing can be read out of it - so leaving it out
    is the honest answer rather than a workaround. Its neighbours are unaffected: 98 of
    the 100 jobs match (one is unreadable, one is the evil.exe needle), and page 2 holds
    the ones either side of the gap."""
    first = client.get("/data/jobs?Search=benign").get_data(as_text=True)
    assert "98 jobs matching" in first

    page_two = client.get("/data/jobs?Search=benign&p=2").get_data(as_text=True)
    assert "job0040" not in page_two
    assert "job0039" in page_two and "job0041" in page_two


if __name__ == "__main__":
    unittest.main()
