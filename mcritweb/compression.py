"""gzip for the responses a browser can decode, without a proxy in front.

The reference deployment (docker-mcrit) terminates in NGINX, which compresses on its
own; a bare `flask run` or waitress does not, and then every page and stylesheet goes
over the wire uncompressed - the "enable text compression" bullet of issue #63. This
compresses text, JSON, JavaScript and SVG bodies of 1 KiB or more when the client
accepts gzip, static files included. A proxy that compresses leaves an already
encoded body alone, so the two do not stack. Off with MCRITWEB_COMPRESS_RESPONSES.
"""

import gzip

from flask import request

COMPRESSIBLE_TYPES = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml")
MIN_SIZE = 1024
LEVEL = 6


def wants_gzip():
    return "gzip" in request.headers.get("Accept-Encoding", "").lower()


def is_compressible(response):
    if response.status_code != 200 or response.headers.get("Content-Encoding"):
        return False
    return any(response.mimetype.startswith(prefix) for prefix in COMPRESSIBLE_TYPES)


def compress(response):
    """The response, gzipped in place when that is worth doing."""
    if not wants_gzip() or not is_compressible(response):
        return response
    # a static file is streamed through untouched by default; reading it here is what
    # makes it compressible, and the assets are small enough for that to be fine
    response.direct_passthrough = False
    body = response.get_data()
    if len(body) < MIN_SIZE:
        return response
    # mtime pinned so the same body always gzips to the same bytes, and the ETag names
    # this representation rather than the plain one
    response.set_data(gzip.compress(body, compresslevel=LEVEL, mtime=0))
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(response.content_length)
    response.headers.add("Vary", "Accept-Encoding")
    etag, weak = response.get_etag()
    if etag:
        response.set_etag(f"{etag}-gzip", weak)
    return response


def register(app):
    if app.config.get("MCRITWEB_COMPRESS_RESPONSES", True):
        app.after_request(compress)
