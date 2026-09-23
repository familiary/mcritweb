#!/usr/bin/python
"""`/data/jobs` when the backend will not say what is in the queue.

The whole view is built on one call: `getQueueStatistics()` decides the category menu,
the active tab, the totals row and the pagination. `McritClient.handle_response` answers
`None` for every non-200 - a backend that is down, one a version behind that has no such
endpoint, a 500 part-way through the query - and the view read that straight into
`if category in statistics`, which is a `TypeError` and a 500 page.

Found by running MCRITweb against a real mcrit 1.8.1 server rather than the captured
corpus: the fixtures always answer, so nothing in the suite had ever handed this view a
`None`.
"""

import logging

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def test_the_jobs_page_answers_when_the_queue_cannot_be_summarized(client, as_role, fake_mcrit, monkeypatch):
    """A page, not a stack trace. The rest of the view still has to render."""
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "getQueueStatistics", lambda *args, **kwargs: None, raising=False)

    response = client.get("/data/jobs")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "reading MCRIT&#39;s job queue failed" in page or "reading MCRIT's job queue failed" in page


def test_the_jobs_page_says_so_rather_than_showing_an_empty_queue(client, as_role, fake_mcrit, monkeypatch):
    """An unreadable queue and an empty one must not look the same.

    Rendering the empty-queue page silently would tell a user their jobs are gone.
    """
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "getQueueStatistics", lambda *args, **kwargs: {}, raising=False)
    empty_page = client.get("/data/jobs").get_data(as_text=True)

    monkeypatch.setattr(fake_mcrit, "getQueueStatistics", lambda *args, **kwargs: None, raising=False)
    failed_page = client.get("/data/jobs").get_data(as_text=True)

    assert "queue failed" not in empty_page
    assert "queue failed" in failed_page


def test_a_queue_that_answers_is_untouched(client, as_role, fake_mcrit):
    """The guard must not cost the page: the ordinary render still carries its menu."""
    as_role("visitor")

    response = client.get("/data/jobs")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "queue failed" not in page
    assert "getMatchesForSample" in page, "the category menu is built from the statistics"


@pytest.mark.parametrize("category", ["getMatchesForSample", "notAMethodAtAll", ""])
def test_a_category_the_queue_does_not_hold_is_not_a_crash(client, as_role, fake_mcrit, monkeypatch, category):
    """`?active=` is user input, so every value of it has to render.

    `statistics` only carries the categories the queue currently holds, so a category
    link bookmarked while it had jobs, a category that has simply gone quiet, or a
    hand-typed value all indexed a key that is not there. That was a 500 on a perfectly
    healthy backend, not only on one that will not answer.
    """
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "getQueueStatistics", lambda *args, **kwargs: {}, raising=False)

    response = client.get(f"/data/jobs?active={category}")

    assert response.status_code == 200


@pytest.mark.parametrize("category", ["getMatchesForSample", "notAMethodAtAll"])
def test_a_category_filtered_url_answers_when_the_queue_cannot_be_summarized(client, as_role, fake_mcrit, monkeypatch, category):
    """The unreadable-queue guard has to hold on the filtered URLs too.

    Substituting an empty mapping for the `None` keeps the menu rendering, but the
    active category survives the substitution, so the pagination still had a key to
    miss.
    """
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "getQueueStatistics", lambda *args, **kwargs: None, raising=False)

    response = client.get(f"/data/jobs?active={category}")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "queue failed" in page, "the page still has to say the queue could not be read"


def test_a_category_the_queue_does_hold_still_paginates(client, as_role, fake_mcrit, monkeypatch):
    """The tolerant lookup must not flatten a real count to zero.

    A count of zero would leave `Pagination` with one page, so page 2 would be clamped
    back to page 1 and the second page of a 32-job category would silently show the
    first. The count is only observable through the window the view then asks for.
    """
    as_role("visitor")
    monkeypatch.setattr(
        fake_mcrit,
        "getQueueStatistics",
        lambda *args, **kwargs: {"getMatchesForSample": {"finished": 30, "failed": 2}},
        raising=False,
    )

    response = client.get("/data/jobs?active=getMatchesForSample&p=2")

    assert response.status_code == 200
    windows = [kwargs.get("start") for name, args, kwargs in fake_mcrit.calls if name == "getQueueData"]
    assert windows == [25], f"30 finished + 2 failed is a second page starting at 25, asked for {windows}"
