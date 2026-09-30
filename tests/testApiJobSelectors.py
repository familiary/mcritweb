#!/usr/bin/python
"""The API passthrough hands mcrit's two `/jobs` selectors on.

`sample_ids` selects the jobs whose first argument is one of the ids, and `job_ids`
the jobs with those ids, both in mcrit's own query. The router forwards them as the
lists `McritClient.getQueueData` takes. An item that is not an id is dropped, as the
backend drops it. A parameter that is present but empty stays an empty list, which
selects nothing, and stays apart from an absent one, which selects everything.

The router hands the client's answer to `handle_raw_response`, which wants a real
`requests.Response`; the fake answers a plain list, so the request dies after the call
these tests are about. See testApiTokens.py for the same arrangement.
"""

import logging

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

JOB_A = "6ab3b4455ccfc2538ffea9bf"
JOB_B = "6ab3b4455ccfc2538ffea9c0"


def forwarded(client, fake_mcrit, query):
    """The keyword arguments the router called getQueueData with."""
    fake_mcrit.calls.clear()
    try:
        client.get(f"/api/jobs{query}", headers={"apitoken": "apitoken-visitor"})
    except Exception:
        pass
    calls = [kwargs for name, _args, kwargs in fake_mcrit.calls if name == "getQueueData"]
    assert len(calls) == 1, calls
    return calls[0]


@pytest.mark.parametrize(
    "query, sample_ids, job_ids",
    [
        ("", None, None),
        ("?method=getMatchesForSample&sample_ids=7,8,9", [7, 8, 9], None),
        ("?method=getMatchesForSample&sample_ids=7, x,,-3", [7, -3], None),
        # what int() takes, as the backend's parsing does
        ("?method=getMatchesForSample&sample_ids=%2B3,1_000, 4 ,1.5", [3, 1000, 4], None),
        ("?method=getMatchesForSample&sample_ids=", [], None),
        (f"?job_ids={JOB_A},,{JOB_B}", None, [JOB_A, JOB_B]),
        ("?job_ids=", None, []),
    ],
)
def test_the_selectors_are_forwarded_as_lists(client, make_user, fake_mcrit, query, sample_ids, job_ids):
    make_user("visitor")

    kwargs = forwarded(client, fake_mcrit, query)

    assert kwargs.get("sample_ids") == sample_ids
    assert kwargs.get("job_ids") == job_ids


def test_the_other_parameters_are_forwarded_as_before(client, make_user, fake_mcrit):
    make_user("visitor")

    kwargs = forwarded(client, fake_mcrit, "?start=5&limit=10&method=getMatchesForSample&filter=abc&state=finished&ascending=true")

    assert {key: kwargs[key] for key in ("start", "limit", "method", "filter", "state", "ascending")} == {
        "start": 5, "limit": 10, "method": "getMatchesForSample", "filter": "abc", "state": "finished", "ascending": True,
    }
