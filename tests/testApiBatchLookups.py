#!/usr/bin/python
"""The API passthrough forwards mcrit's batch sample and family lookups.

`POST /samples/ids` and `POST /families/ids` take a comma-separated id list as the body,
as `POST /functions` does, and sample ids may be negative, for query samples. The router
hands the list to `getSamplesByIds` / `getFamiliesByIds`. A body that is not an id list
is answered 400 without asking the backend, which is what the backend itself answers.

The router passes the client's answer to `handle_raw_response`, which wants a real
`requests.Response`; the fake answers a plain dict, so a request that reaches the
backend dies after the call these tests are about. See testApiTokens.py.
"""

import logging

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

VISITOR = {"apitoken": "apitoken-visitor"}


def post(client, fake_mcrit, path, body):
    """The response, or None when the request died after reaching the backend."""
    fake_mcrit.calls.clear()
    try:
        return client.post(f"/api/{path}", headers=VISITOR, data=body)
    except Exception:
        return None


def calls(fake_mcrit, name):
    return [args for called, args, kwargs in fake_mcrit.calls if called == name]


@pytest.mark.parametrize(
    "path, body, name, ids",
    [
        ("samples/ids", "3,5", "getSamplesByIds", [3, 5]),
        ("samples/ids", "3, 5 ,-1", "getSamplesByIds", [3, 5, -1]),
        ("families/ids", "1,2", "getFamiliesByIds", [1, 2]),
    ],
)
def test_an_id_list_reaches_the_batch_lookup(client, make_user, fake_mcrit, path, body, name, ids):
    make_user("visitor")

    post(client, fake_mcrit, path, body)

    assert calls(fake_mcrit, name) == [(ids,)]


@pytest.mark.parametrize(
    "path, body",
    [
        ("samples/ids", ""),
        ("samples/ids", "3,x"),
        ("samples/ids", "3,,5"),
        ("families/ids", "-1"),
        ("families/ids", "1;2"),
    ],
)
def test_a_body_that_is_not_an_id_list_is_refused_without_the_backend(client, make_user, fake_mcrit, path, body):
    make_user("visitor")

    response = post(client, fake_mcrit, path, body)

    assert response is not None and response.status_code == 400
    assert calls(fake_mcrit, "getSamplesByIds") == [] and calls(fake_mcrit, "getFamiliesByIds") == []


@pytest.mark.parametrize("path", ["samples/ids", "families/ids"])
def test_the_lookups_answer_only_post(client, make_user, fake_mcrit, path):
    make_user("visitor")

    assert client.get(f"/api/{path}", headers=VISITOR).status_code == 405
