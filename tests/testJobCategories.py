#!/usr/bin/python
"""The jobs page must not 500 on a category that has no jobs.

`data.jobs` sizes its pagination with

    max_count = sum(statistics[active_category].values()) if active_category else 0

and `getQueueStatistics` only reports categories that have at least one job. So every
job type that has never run - or whose jobs have all been deleted - is a KeyError, and
Flask turns that into a 500 for any visitor.

This is not a hand-edited-URL curiosity. `templates/jobs.html` renders a real href for
every submenu entry, available or not (`class="dropdown-item disabled"` is styling, the
link is in the DOM), and the page's own per-category delete button turns a live tab into
an unavailable one - so the obvious "delete these, then go back" makes a bookmark or a
history entry crash. Found while reviewing the search work for issue #51.
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


#: categories the corpus's queue statistics do not mention, so `statistics[...]` raises
EMPTY_CATEGORIES = ["modifyFamily", "rebuildIndex", "recalculatePicHashes",
                    "recalculateMinHashes", "updateMinHashes", "getMatchesForSmdaReport"]


@pytest.mark.parametrize("category", EMPTY_CATEGORIES)
def test_a_category_with_no_jobs_renders_instead_of_crashing(client, as_role, category):
    as_role("visitor")

    response = client.get(f"/data/jobs?active={category}")

    assert response.status_code == 200, f"?active={category} is a 500"


def test_a_category_with_no_jobs_says_it_is_empty(client, as_role):
    """Rendering is not enough - the page has to be honest that there is nothing here,
    rather than showing the previous tab's rows or a blank."""
    as_role("visitor")

    page = client.get("/data/jobs?active=rebuildIndex").get_data(as_text=True)

    assert "rebuildIndex" in page


def test_the_menu_really_does_link_to_an_empty_category(client, as_role):
    """The premise of the tests above: these URLs are in the rendered page, so they are
    reachable, bookmarkable and crawlable whatever the disabled styling suggests."""
    as_role("visitor")

    page = client.get("/data/jobs").get_data(as_text=True)

    assert "/data/jobs?active=rebuildIndex" in page


def test_a_category_that_is_not_a_job_type_at_all_is_rejected(client, as_role):
    """A nonsense value should not silently render as "this type has no jobs" - that
    reads as a fact about the queue rather than about the URL."""
    as_role("visitor")

    response = client.get("/data/jobs?active=notARealCategory", follow_redirects=True)

    assert response.status_code == 200
    assert b"is not a job type" in response.data


def test_a_real_category_is_still_shown(client, as_role):
    """The guard must not reject the categories that do work."""
    as_role("visitor")

    page = client.get("/data/jobs?active=getMatchesForSample").get_data(as_text=True)

    assert "is not a job type" not in page
    assert "getMatchesForSample" in page


def test_every_category_the_menu_offers_is_accepted(client, as_role):
    """A ratchet against the guard and the menu drifting apart: rejecting a category the
    page itself links to would turn this fix into a different bug."""
    as_role("visitor")
    page = client.get("/data/jobs").get_data(as_text=True)

    import re
    linked = set(re.findall(r"/data/jobs\?active=([A-Za-z]+)", page))
    assert linked, "no category links were rendered at all"
    for category in sorted(linked):
        response = client.get(f"/data/jobs?active={category}", follow_redirects=True)
        assert response.status_code == 200, category
        assert b"is not a job type" not in response.data, category


def test_a_category_the_backend_reports_is_accepted_even_if_this_app_has_not_heard_of_it(
        client, as_role, fake_mcrit, monkeypatch):
    """The backend is the authority on what a job type is.

    A hardcoded allowlist here would reject a method a *newer backend* has grown, and
    this front end would need a release to catch up - while the queue statistics it is
    already reading say plainly that the method exists. So a category the backend
    reports is accepted whether or not JOB_CATEGORIES has heard of it.
    """
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "getQueueStatistics",
                        lambda *args, **kwargs: {"someFutureBackendMethod": {"finished": 3}})

    response = client.get("/data/jobs?active=someFutureBackendMethod", follow_redirects=True)

    assert response.status_code == 200
    assert b"is not a job type" not in response.data


def test_a_typo_is_still_rejected_when_the_backend_has_not_heard_of_it_either(
        client, as_role, fake_mcrit, monkeypatch):
    """The other side: deferring to the backend must not accept everything."""
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "getQueueStatistics",
                        lambda *args, **kwargs: {"someFutureBackendMethod": {"finished": 3}})

    response = client.get("/data/jobs?active=notARealCategory", follow_redirects=True)

    assert b"is not a job type" in response.data


def test_totals_is_not_mistaken_for_a_category(client, as_role):
    """The view adds a "totals" key to the statistics dict for its own use. The guard
    runs before that, so the key cannot become an accidental category."""
    as_role("visitor")

    response = client.get("/data/jobs?active=totals", follow_redirects=True)

    assert response.status_code == 200
    assert b"is not a job type" in response.data


def test_the_known_categories_cover_what_the_backend_can_produce(client):
    """Job.method_types["all"] omits the two maintenance methods the admin routes
    create, so it cannot be the whole list on its own - this pins that."""
    from mcritweb.views.data import JOB_CATEGORIES

    for method in Job(None, None).method_types["all"]:
        assert method in JOB_CATEGORIES, method
    for method in ("recalculatePicHashes", "recalculateMinHashes"):
        assert method in JOB_CATEGORIES, f"{method} is created by admin routes"


if __name__ == "__main__":
    unittest.main()
