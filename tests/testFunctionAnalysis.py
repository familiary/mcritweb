#!/usr/bin/python
"""Analyzing a single function, from its row and from its page - issue #35.

The Analyze button on a function row pointed at `analyze.compare` carrying
`query=sample_id:<parent>`. That is the sample picker, pre-filtered to the sample the
function happens to live in: every function of a sample got the same link, and the
function the user clicked was gone by the time the page rendered.

There is no per-function 1-vs-N in the backend - matching runs per sample - so
"analyze this function" can only mean the parent sample's match job, read through the
function filter that `data.result` already implements as `?funid=`. The new route says
exactly that: queue (or, by default, reuse) `getMatchesForSample` for the parent, then
land on that job's result filtered to this function.

Reuse is the default on purpose. The backend deduplicates by descriptor and answers
the job it already has unless the caller forces a recalculation (see issue #97 and
tests/testJobSubmission.py), so clicking through a table of function rows costs one
lookup per click rather than one full sample match. `?rematch=true` is the opt-out,
the same contract `analyze.compare_all` offers.

The corpus in tests/fixtures/ holds one captured 1-vs-N job, for sample 0, which is
also the sample that owns the reference functions - so these can follow the whole path
from the button to the rendered function report without a backend.
"""

import logging
import re
import unittest

import pytest
from fixtureData import job_id_of
from mcrit.storage.FunctionEntry import FunctionEntry

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: A function of sample 0 that the captured report has matches for.
FUNCTION_ID = 2
PARENT_SAMPLE_ID = 0


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


def analyze_buttons(html):
    """function_id -> the href of the Analyze button on that row, None if it has none."""
    buttons = {}
    for row in re.findall(r'<tr class="function-row.*?</tr>', html, re.S):
        function_id = int(re.search(r'class="function-id">(-?\d+)<', row).group(1))
        button = re.search(r'<a class="btn[^"]*" href="([^"]+)"', row)
        buttons[function_id] = button.group(1).replace("&amp;", "&") if button else None
    return buttons


def calls_to(fake, method):
    return [call for call in fake.calls if call[0] == method]


# --- the button ------------------------------------------------------------------

def test_the_analyze_button_on_a_function_row_names_that_function(client, as_role, fake_mcrit):
    """The reported bug: every row of a sample carried the same sample-level link."""
    as_role("visitor")
    response = client.get("/explore/functions")

    assert response.status_code == 200
    buttons = analyze_buttons(response.data.decode())
    assert len(buttons) > 1, "need more than one function row to tell the links apart"
    assert len(set(buttons.values())) == len(buttons), (
        "the Analyze buttons of different functions all point at the same URL:\n  " +
        "\n  ".join(sorted(set(buttons.values())))
    )
    for function_id, href in buttons.items():
        assert href == f"/analyze/compare_function/{function_id}"


def test_a_query_functions_row_offers_no_analysis(client, as_role, fake_mcrit):
    """`url_for` writes a negative id into the href even though `<int:function_id>`
    will not match it back, so an ungated button here is one that only ever answers
    404. The old button was built from the parent sample id, which is at least a page
    that renders, so this is the one way the fix could have made a row worse."""
    as_role("visitor")
    fake_mcrit._functions[-1] = FunctionEntry.fromDict({**fake_mcrit.getFunctionById(FUNCTION_ID).toDict(), "function_id": -1})

    response = client.get("/explore/functions")

    assert response.status_code == 200
    buttons = analyze_buttons(response.data.decode())
    assert buttons[-1] is None, "a negative function id must not be offered an Analyze link"
    assert any(href is not None for href in buttons.values()), "the ordinary rows still link"


def test_the_function_page_offers_the_same_analysis(client, as_role, fake_mcrit):
    """Issue #35 also notes there is no way in from a function's own page."""
    as_role("visitor")
    response = client.get(f"/explore/functions/{FUNCTION_ID}")

    assert response.status_code == 200
    assert f'href="/analyze/compare_function/{FUNCTION_ID}"'.encode() in response.data


def test_the_function_page_of_a_query_function_offers_no_analysis(client, as_role, fake_mcrit):
    """A query sample's functions carry negative ids - the result templates link to
    `explore.function_by_id` with `function_id * -1` - and they are not part of the
    corpus, so there is nothing to run a 1-vs-N against. The route refuses them, and
    the page must not advertise a link that only ever answers 404.

    The fabricated entry keeps a real parent sample: `explore.function_by_id` hands
    `single_function.html` a None sample_entry for a sample that is not in the
    database, and the page already fails on that for its own reasons.
    """
    as_role("visitor")
    query_function = FunctionEntry.fromDict({**fake_mcrit.getFunctionById(FUNCTION_ID).toDict(), "function_id": -1})
    fake_mcrit._functions[-1] = query_function

    response = client.get("/explore/functions/-1")

    assert response.status_code == 200
    assert b"/analyze/compare_function/" not in response.data


# --- the route -------------------------------------------------------------------

def test_analyzing_a_function_queues_the_job_for_its_parent_sample(client, as_role, fake_mcrit):
    as_role("visitor")
    response = client.get(f"/analyze/compare_function/{FUNCTION_ID}")

    assert response.status_code == 302
    queued = calls_to(fake_mcrit, "requestMatchesForSample")
    assert len(queued) == 1
    assert queued[0][1][0] == PARENT_SAMPLE_ID


def test_analyzing_a_function_lands_on_the_function_filtered_result(client, as_role, fake_mcrit):
    """The whole point: the job is the parent sample's, the page is the function's."""
    as_role("visitor")
    response = client.get(f"/analyze/compare_function/{FUNCTION_ID}", follow_redirects=True)

    assert response.status_code == 200
    assert response.request.path == f"/data/result/{job_id_of('matches_for_sample')}"
    assert response.request.args.get("funid") == str(FUNCTION_ID)
    # the h3 of result_compare_function.html
    assert f"Matches for Function: {FUNCTION_ID}".encode() in response.data


def test_analyzing_a_function_reuses_an_existing_job_by_default(client, as_role, fake_mcrit):
    """A table of function rows is a table of clicks. Forcing a recalculation on each
    one queues a full sample match per click, which is what issue #97 removed from the
    sample-level routes."""
    as_role("visitor")
    client.get(f"/analyze/compare_function/{FUNCTION_ID}")
    client.get(f"/analyze/compare_function/{FUNCTION_ID}?rematch=false")

    queued = calls_to(fake_mcrit, "requestMatchesForSample")
    assert len(queued) == 2, "no job was queued at all"
    for _name, _args, kwargs in queued:
        assert kwargs["force_recalculation"] is False


def test_a_rematch_request_still_forces_a_recalculation(client, as_role, fake_mcrit):
    """A job that predates a change to the corpus is stale, and this is the way out."""
    as_role("visitor")
    client.get(f"/analyze/compare_function/{FUNCTION_ID}?rematch=true")

    _name, _args, kwargs = calls_to(fake_mcrit, "requestMatchesForSample")[0]
    assert kwargs["force_recalculation"] is True


def test_a_repeated_request_reaches_the_backend_identically(client, as_role, fake_mcrit):
    """Deduplication happens in the backend, by descriptor. This side only has to
    make two identical requests look identical to it."""
    as_role("visitor")
    client.get(f"/analyze/compare_function/{FUNCTION_ID}")
    client.get(f"/analyze/compare_function/{FUNCTION_ID}")

    queued = calls_to(fake_mcrit, "requestMatchesForSample")
    assert len(queued) == 2
    assert queued[0] == queued[1]


def test_analyzing_an_unknown_function_is_reported_not_crashed(client, as_role, fake_mcrit):
    as_role("visitor")
    response = client.get("/analyze/compare_function/999999")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/explore/functions")
    assert calls_to(fake_mcrit, "requestMatchesForSample") == []


def test_a_query_functions_negative_id_is_not_a_route(client, as_role, fake_mcrit):
    """Gating this in the template only would leave the route open. There is no
    sample in the corpus behind a negative function id, so the URL must not exist."""
    as_role("visitor")
    response = client.get("/analyze/compare_function/-1")

    assert response.status_code == 404
    assert calls_to(fake_mcrit, "requestMatchesForSample") == []


# --- carrying the filter across the job page --------------------------------------

def test_the_job_page_carries_the_function_filter_into_the_result(client, as_role, fake_mcrit):
    """A job that is still running lands the user on the job page, which auto-forwards
    once it finishes. Dropping `funid` there would quietly turn the function report the
    user asked for back into the sample report - the bug, one redirect later."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    response = client.get(f"/data/jobs/{job_id}?forward=1&funid={FUNCTION_ID}")

    assert response.status_code == 302
    assert response.headers["Location"] == f"/data/result/{job_id}?funid={FUNCTION_ID}"


def test_the_job_page_forwards_unfiltered_when_no_function_was_named(client, as_role, fake_mcrit):
    """Every other caller of the auto-forward keeps its current URL."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    response = client.get(f"/data/jobs/{job_id}?forward=1")

    assert response.status_code == 302
    assert response.headers["Location"] == f"/data/result/{job_id}"


def test_a_function_the_report_does_not_name_falls_back_to_the_sample_report(client, as_role, fake_mcrit):
    """`data.result` only takes the function filter when the report has that function,
    and otherwise renders the sample report - there is no function view to show. Pinned
    because the hand-written URL is the way to get there, and it must degrade rather
    than fail."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    absent = 873  # a corpus function, but of another sample - not in this report

    response = client.get(f"/data/jobs/{job_id}?forward=1&funid={absent}", follow_redirects=True)

    assert response.status_code == 200
    assert f"Matches for Function: {absent}".encode() not in response.data
    assert b"are corrupted" not in response.data


def test_the_job_page_ignores_a_funid_that_is_not_a_number(client, as_role, fake_mcrit):
    """The value is reflected into the next URL, so it does not get to be arbitrary."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    response = client.get(f"/data/jobs/{job_id}?forward=1&funid=%22%3E%3Cscript%3E")

    assert response.status_code == 302
    assert response.headers["Location"] == f"/data/result/{job_id}"



def test_a_backend_that_will_not_start_the_job_says_so_rather_than_500ing(
        client, as_role, fake_mcrit, monkeypatch):
    """`handle_response` answers None for every non-200, so a refused job is
    indistinguishable from a backend that is down.

    Unguarded, that None reaches `url_for(job_id=None)`; werkzeug drops the None and then
    cannot build `data.job_by_id` at all, so the route raises BuildError - a 500 with a
    traceback where the reader should have got a sentence.

    The landing page is asserted, not merely "some page rendered": the fallback is
    deliberately the listing rather than this function's own page, so a backend that is
    genuinely down does not answer with a second, wrong message on top of this one.
    """
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "requestMatchesForSample", lambda *args, **kwargs: None)

    response = client.get(f"/analyze/compare_function/{FUNCTION_ID}", follow_redirects=True)

    assert response.status_code == 200
    assert "would not start the job" in response.get_data(as_text=True)
    assert response.request.path == "/explore/functions"

if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()
