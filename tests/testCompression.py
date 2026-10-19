#!/usr/bin/python
"""gzip for the deployments that have no compressing proxy (issue #63)."""

import gzip
import json
import logging
import pathlib
import unittest

import pytest

from mcritweb import compression

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def test_a_page_is_not_gzipped(client, as_role):
    """A page carries the session's CSRF token next to input it reflects; compressed,
    that is what a BREACH attack reads the token out of."""
    as_role("visitor")
    page = client.get("/explore/samples?query=abc", headers={"Accept-Encoding": "gzip, deflate"})
    assert page.status_code == 200 and page.mimetype == "text/html"
    assert page.headers.get("Content-Encoding") is None


def test_json_is_gzipped_for_a_client_that_accepts_it(app):
    with app.test_request_context("/explore/samples", headers={"Accept-Encoding": "gzip, deflate"}):
        body = json.dumps({"rows": list(range(1000))})
        response = compression.compress(app.response_class(body, mimetype="application/json"))
        assert response.headers["Content-Encoding"] == "gzip"
        assert "Accept-Encoding" in response.headers.get("Vary", "")
        assert int(response.headers["Content-Length"]) == len(response.get_data()) < len(body)
        assert gzip.decompress(response.get_data()).decode() == body


@pytest.mark.parametrize("accept_encoding", ["gzip;q=0", "identity", "deflate, br"])
def test_nothing_is_gzipped_for_a_client_that_refuses_it(client, accept_encoding):
    response = client.get("/static/style.css", headers={"Accept-Encoding": accept_encoding})
    assert response.status_code == 200 and response.headers.get("Content-Encoding") is None


def test_static_files_are_gzipped_too(client):
    response = client.get("/static/style.css", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(response.data).startswith(client.get("/static/style.css").data[:64])
    assert response.headers.get("ETag", "").endswith('-gzip"')


def test_small_and_binary_responses_are_left_alone(client):
    tiny = client.get("/static/navbar.css", headers={"Accept-Encoding": "gzip"})
    assert tiny.status_code == 200 and tiny.headers.get("Content-Encoding") is None, "262 bytes are not worth a gzip header"
    image = client.get("/static/malpedia_small.png", headers={"Accept-Encoding": "gzip"})
    assert image.status_code == 200 and image.headers.get("Content-Encoding") is None


def test_a_conditional_request_still_gets_its_304(client):
    """send_file compares If-None-Match before the ETag is renamed, so a client that
    revalidates its gzipped copy has to be answered here."""
    first = client.get("/static/style.css", headers={"Accept-Encoding": "gzip"})
    again = client.get("/static/style.css", headers={
        "Accept-Encoding": "gzip",
        "If-None-Match": first.headers["ETag"],
        "If-Modified-Since": first.headers["Last-Modified"],
    })
    assert again.status_code == 304
    assert again.data == b""
    assert again.headers["ETag"] == first.headers["ETag"]
    changed = client.get("/static/style.css", headers={"Accept-Encoding": "gzip", "If-None-Match": '"something-else-gzip"'})
    assert changed.status_code == 200 and changed.headers["Content-Encoding"] == "gzip"


def test_a_plain_etag_still_gets_its_304(client):
    first = client.get("/static/style.css")
    again = client.get("/static/style.css", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304


def big_json(size):
    return json.dumps({"x": "a" * size})


def test_a_download_is_left_alone(app):
    with app.test_request_context("/data/export", headers={"Accept-Encoding": "gzip"}):
        response = app.response_class(big_json(4096), mimetype="application/json", headers={"Content-Disposition": "attachment; filename=export_samples.json"})
        assert compression.compress(response).headers.get("Content-Encoding") is None


def test_the_api_passthrough_is_left_alone(app):
    with app.test_request_context("/api/version", headers={"Accept-Encoding": "gzip"}):
        response = app.response_class(big_json(4096), mimetype="text/plain")
        assert compression.compress(response).headers.get("Content-Encoding") is None


def test_a_body_of_unknown_length_is_not_read(app):
    read = []

    def pieces():
        read.append(True)
        yield big_json(4096).encode()

    with app.test_request_context("/explore/samples", headers={"Accept-Encoding": "gzip"}):
        response = app.response_class(pieces(), mimetype="application/json")
        assert compression.compress(response).headers.get("Content-Encoding") is None
    assert read == []


def test_a_body_above_the_cap_is_left_alone(app):
    with app.test_request_context("/explore/samples", headers={"Accept-Encoding": "gzip"}):
        response = app.response_class(big_json(compression.MAX_SIZE), mimetype="application/json")
        assert compression.compress(response).headers.get("Content-Encoding") is None


def test_the_cap_covers_every_compressible_asset():
    static = pathlib.Path(compression.__file__).parent / "static"
    largest = max(path.stat().st_size for path in static.rglob("*") if path.suffix in (".js", ".css", ".svg", ".json"))
    assert largest <= compression.MAX_SIZE


def test_it_can_be_switched_off():
    """The switch is read once, when the app is built - a proxy that compresses is a
    deployment property, not something that changes per request."""
    from flask import Flask
    off = Flask("off")
    off.config["MCRITWEB_COMPRESS_RESPONSES"] = False
    compression.register(off)
    assert compression.compress not in off.after_request_funcs.get(None, [])
    on = Flask("on")
    compression.register(on)
    assert compression.compress in on.after_request_funcs.get(None, [])


def test_an_already_encoded_body_is_not_encoded_twice(app):
    from flask import Response
    with app.test_request_context("/", headers={"Accept-Encoding": "gzip"}):
        response = Response(b"x" * 4096, mimetype="text/plain", headers={"Content-Encoding": "br"})
        assert compression.compress(response) is response
        assert response.headers["Content-Encoding"] == "br"


if __name__ == "__main__":
    unittest.main()
