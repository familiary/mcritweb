#!/usr/bin/python
"""What the API passthrough hands on from the backend.

`handle_raw_response` used to parse every answer with `response.json()` and serialise
it again with `json.dumps()`, a full extra pass over job results and function listings
that are megabytes in size, only to send the same document on. It now forwards the
backend's bytes, and with them the JSON the backend answers a failure with, which it
used to drop. Issue #200.

The fakes in conftest return parsed values, but the router is handed the raw
`requests.Response` a `McritClient(raw_responses=True)` returns, so these tests build
real ones. Parsing is made to fail loudly, so a return of the round trip shows up here.
"""

import logging
import unittest

import pytest
import requests

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

TOKEN = {"apitoken": "apitoken-visitor"}

#: compact and written the way `json.dumps` would not write it, so a re-encoding is visible
BACKEND_JSON = b'{"status":"successful","data":{"score":1.50,"ids":[3,1,2]}}'


class UnparsedResponse(requests.Response):
    """The backend's answer, refusing to be parsed on the way through."""

    def json(self, **kwargs):
        raise AssertionError("the passthrough parsed the backend's body")


def backend_answer(status_code, body, content_type="application/json", **headers):
    response = UnparsedResponse()
    response.status_code = status_code
    response._content = body
    if content_type is not None:
        response.headers["Content-Type"] = content_type
    response.headers.update(headers)
    return response


class RawBackend:
    """Answers the two passthrough calls used here with a fixed raw response."""

    def __init__(self, answer):
        self.answer = answer

    def getStatus(self, *args, **kwargs):
        return self.answer

    def getResultForJob(self, *args, **kwargs):
        return self.answer


@pytest.fixture
def answer_with(app, make_user):
    make_user("visitor")

    def _answer_with(response):
        app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: RawBackend(response)
    return _answer_with


@pytest.mark.parametrize("path", ["status", "jobs/0123456789abcdef01234567/result"])
def test_the_backend_body_is_forwarded_byte_for_byte(client, answer_with, path):
    answer_with(backend_answer(200, BACKEND_JSON))

    response = client.get(f"/api/{path}", headers=TOKEN)

    assert response.status_code == 200
    assert response.get_data() == BACKEND_JSON
    assert response.mimetype == "application/json"


def test_an_accepted_answer_keeps_its_status(client, answer_with):
    answer_with(backend_answer(202, BACKEND_JSON))

    response = client.get("/api/status", headers=TOKEN)

    assert response.status_code == 202
    assert response.get_data() == BACKEND_JSON


def test_the_content_type_is_the_backend_own(client, answer_with):
    answer_with(backend_answer(200, BACKEND_JSON, content_type="application/json; charset=UTF-8"))

    response = client.get("/api/status", headers=TOKEN)

    assert response.status_code == 200
    assert response.get_data() == BACKEND_JSON
    assert response.headers["Content-Type"] == "application/json; charset=UTF-8"


def test_the_backend_headers_stay_behind(client, answer_with):
    """Only the body, the status and the JSON content type cross over: a cookie or an
    internal header the backend (or a proxy in front of it) sets is not ours to hand on."""
    answer_with(backend_answer(200, BACKEND_JSON, **{"Set-Cookie": "backend=secret", "X-Backend-Node": "worker-3", "Keep-Alive": "timeout=5"}))

    response = client.get("/api/status", headers=TOKEN)

    assert "Set-Cookie" not in response.headers
    assert "X-Backend-Node" not in response.headers
    assert "Keep-Alive" not in response.headers


@pytest.mark.parametrize(
    "body, content_type",
    [
        (b"<html><script>alert(1)</script></html>", "text/html"),   # a proxy's error or login page
        (b"<html>no header at all</html>", None),
        (b"", "application/json"),                                   # an empty answer
    ],
)
def test_a_body_that_is_not_json_is_a_bad_gateway(client, answer_with, body, content_type):
    """This used to raise in `response.json()` and end as an unhandled 500. It must
    not become a 200 that hands the page on under a JSON label."""
    answer_with(backend_answer(200, body, content_type=content_type))

    response = client.get("/api/status", headers=TOKEN)

    assert response.status_code == 502
    assert response.get_data() == b""


@pytest.mark.parametrize("status_code", [400, 404, 409, 500])
def test_an_error_answer_reaches_the_client(client, answer_with, status_code):
    """The backend explains its failures in JSON ("We don't have a sample with that
    id."), and that explanation is for whoever made the call. It used to be dropped,
    leaving a bare status."""
    body = b'{"status": "failed", "data": {"message": "We don\'t have a sample with that id."}}'
    answer_with(backend_answer(status_code, body))

    response = client.get("/api/jobs/0123456789abcdef01234567/result", headers=TOKEN)

    assert response.status_code == status_code
    assert response.get_data() == body
    assert response.mimetype == "application/json"


@pytest.mark.parametrize("content_type", ["text/html", None])
def test_an_error_page_keeps_its_status_but_not_its_body(client, answer_with, content_type):
    """A proxy's HTML error page is not the backend's answer, and is not ours to serve."""
    answer_with(backend_answer(502, b"<html><h1>Bad Gateway</h1><pre>upstream trace</pre></html>", content_type=content_type))

    response = client.get("/api/status", headers=TOKEN)

    assert response.status_code == 502
    assert response.get_data() == b""


if __name__ == "__main__":
    unittest.main()
