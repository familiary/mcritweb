#!/usr/bin/python
"""The job list has to keep showing the tab the user is looking at (issue #36).

Which category the job list shows is view state, and the only place a browser can
carry view state across a refresh or a step back through history is the URL.
`data.jobs` picks a default category by walking the known method types in a fixed
order and taking the first one the *live* queue statistics mention, so a job list
reached without an `active` parameter displays a tab that nothing in the URL records.
Refresh it after the queue has changed - which is the entire reason anyone refreshes
a job list - and the tab moves under the user; step back to it from a job page and
the browser replays a URL that no longer means the same thing.

The last two tests cover the categories the job list links to but could not render:
one nobody has queued yet, and one that does not exist at all. Both indexed straight
into the statistics dictionary and answered 500.
"""

import copy
import logging
import re
import unittest

import pytest
from conftest import FakeMcritClient

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


#: A queue holding nothing of the category that `data.jobs` prefers by default
#: (getMatchesForSample), so that a later arrival of one is a visible change.
INITIAL_STATISTICS = {
    "combineMatchesToCross": {"queued": 0, "in_progress": 0, "finished": 3},
    "addBinarySample": {"queued": 0, "in_progress": 0, "finished": 22},
}


class QueueStatisticsClient(FakeMcritClient):
    """A backend whose queue statistics a test can change between two requests.

    Each call answers a fresh copy: `data.jobs` writes a "totals" entry into the
    dictionary it gets back, and a shared dictionary would carry that fabricated
    category into the next request.
    """

    def __init__(self, statistics):
        super().__init__()
        self.statistics = statistics

    def getQueueStatistics(self, *args, **kwargs):
        self._record("getQueueStatistics", *args, **kwargs)
        return copy.deepcopy(self.statistics)


@pytest.fixture
def fake_mcrit():
    """Wire the app in this module to a backend with a queue we can change."""
    return QueueStatisticsClient(copy.deepcopy(INITIAL_STATISTICS))


def address_bar(response):
    """The URL the browser would be showing after this request, redirects included."""
    query_string = response.request.query_string.decode()
    return response.request.path + (f"?{query_string}" if query_string else "")


def category_shown(response):
    """The category the rendered job list says it is showing, or None."""
    match = re.search(rb"Showing jobs for category: (\S+)", response.data)
    return match.group(1).decode() if match else None


def test_the_job_list_names_in_its_url_the_tab_it_shows(client, as_role):
    """Without this, the tab is a server-side guess the browser cannot reproduce."""
    as_role("visitor")
    response = client.get("/data/jobs", follow_redirects=True)

    assert response.status_code == 200
    shown = category_shown(response)
    assert shown is not None, "the job list rendered no category at all"
    assert f"active={shown}" in address_bar(response), (
        f"the job list shows '{shown}' but its URL {address_bar(response)} does not say so"
    )


def test_refreshing_the_job_list_does_not_switch_tabs(client, as_role, fake_mcrit):
    """The reported symptom: refresh to watch progress, land on another tab.

    The refresh is the same URL the first request settled on - that is all a browser
    replays for F5 or for a step back in history.
    """
    as_role("visitor")
    first = client.get("/data/jobs", follow_redirects=True)
    was_showing = category_shown(first)
    url_the_browser_now_holds = address_bar(first)

    # while the user reads the page, a job of a category ranked ahead of theirs starts
    fake_mcrit.statistics["getMatchesForSample"] = {"queued": 0, "in_progress": 1}

    refreshed = client.get(url_the_browser_now_holds, follow_redirects=True)

    assert refreshed.status_code == 200
    assert category_shown(refreshed) == was_showing, (
        f"refreshing {url_the_browser_now_holds} moved the user from "
        f"'{was_showing}' to '{category_shown(refreshed)}'"
    )


def test_a_category_nobody_has_queued_yet_renders_as_an_empty_tab(client, as_role):
    """The job list links to every known category, including the ones with no jobs."""
    as_role("visitor")
    response = client.get("/data/jobs?active=rebuildIndex")

    assert response.status_code == 200
    assert category_shown(response) == "rebuildIndex"


def test_an_invented_category_falls_back_instead_of_failing(client, as_role):
    """`active` is user input on a page that indexes a dictionary with it."""
    as_role("visitor")
    response = client.get("/data/jobs?active=no-such-category", follow_redirects=True)

    assert response.status_code == 200
    # the value is reported rather than rendered as a tab: the flash names it as not
    # being a job type, and the tab shown is one the queue really has
    assert b"is not a job type" in response.data
    assert category_shown(response) in INITIAL_STATISTICS


def test_the_canonical_redirect_stays_on_this_page(client, as_role):
    """The redirect carries the request's own query string, which is user input. It has
    to stay a relative URL to this route, and the names url_for reserves for itself -
    which master's pagination refuses to act on - must arrive as inert parameters
    rather than steer where the redirect points."""
    as_role("visitor")
    response = client.get("/data/jobs?_external=1&_anchor=evil&_scheme=javascript")

    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith("/data/jobs?"), location
    assert "#" not in location, "an _anchor turned into a fragment"
    assert "active=" in location, "the redirect exists to name the tab"


if __name__ == "__main__":
    unittest.main()


def test_a_maintenance_category_the_menu_links_to_is_accepted(client, as_role):
    """`recalculatePicHashes` and `recalculateMinHashes` are rendered as menu entries
    in this very view (data.py, the "minhashing" group) but are absent from mcrit's
    own Job.method_types["all"]. Validating against that list alone would make a link
    the page draws for itself fall back to a different tab."""
    as_role("visitor")

    for category in ("recalculatePicHashes", "recalculateMinHashes"):
        response = client.get(f"/data/jobs?active={category}")
        assert response.status_code == 200, f"{category} was not accepted as a category"
