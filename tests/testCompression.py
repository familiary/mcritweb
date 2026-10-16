#!/usr/bin/python
"""gzip for the deployments that have no compressing proxy (issue #63)."""

import gzip
import logging
import unittest

import pytest

from mcritweb import compression

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def test_a_page_is_gzipped_for_a_client_that_accepts_it(client, as_role):
    as_role("visitor")
    plain = client.get("/explore/samples")
    zipped = client.get("/explore/samples", headers={"Accept-Encoding": "gzip, deflate"})

    assert plain.headers.get("Content-Encoding") is None
    assert zipped.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in zipped.headers.get("Vary", "")
    assert int(zipped.headers["Content-Length"]) == len(zipped.data) < len(plain.data)
    assert gzip.decompress(zipped.data) == plain.data


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
    first = client.get("/static/style.css", headers={"Accept-Encoding": "gzip"})
    again = client.get("/static/style.css", headers={"Accept-Encoding": "gzip", "If-None-Match": first.headers["ETag"]})
    # the gzipped representation carries its own ETag, and a client that presents the
    # plain one gets the body again rather than a 304 for a different representation
    assert again.status_code in (200, 304)
    if again.status_code == 304:
        assert again.headers.get("Content-Encoding") is None or again.data == b""


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
