"""gzip for the static files a browser can decode, without a proxy in front.

The reference deployment (docker-mcrit) does not compress: its NGINX configuration has no
`gzip` directive (docs/adr/0015-the-rest-of-63-is-gzip-in-the-proxy.md), and a bare
`flask run` or waitress does not either, so every stylesheet and script went over the
wire uncompressed - the "enable text compression" bullet of issue #63. This compresses
text, JSON, JavaScript and SVG bodies between 1 KiB and MAX_SIZE when the client accepts
gzip, static files included. A proxy that compresses leaves an already encoded body
alone, so the two do not stack. Off with MCRITWEB_COMPRESS_RESPONSES.

Left alone:
- HTML. The pages carry the session's CSRF token, which is the same in every response,
  next to input they reflect (a search term), which is what a BREACH attack needs once
  the page is compressed.
- Downloads (`Content-Disposition: attachment`) and the API passthrough. The exports and
  the cached result download are streamed so that they never sit in memory whole, and
  gzipping them here would read them in.
- A body of unknown length, or one above MAX_SIZE, for the same reason.
"""

import gzip

from flask import request

COMPRESSIBLE_TYPES = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml")
UNCOMPRESSED_TYPES = ("text/html",)
MIN_SIZE = 1024
# the largest asset that is compressed, static/jquery-ui.js, is 529,062 bytes
MAX_SIZE = 1024 * 1024
LEVEL = 6


def wants_gzip():
    # a quality, 0 when gzip is not acceptable, also for an explicit "gzip;q=0"
    return request.accept_encodings["gzip"] > 0


def is_compressible(response):
    if response.status_code != 200 or response.headers.get("Content-Encoding"):
        return False
    if request.blueprint == "api" or "attachment" in response.headers.get("Content-Disposition", "").lower():
        return False
    length = response.content_length
    if length is None or not MIN_SIZE <= length <= MAX_SIZE:
        return False
    if response.mimetype in UNCOMPRESSED_TYPES:
        return False
    return any(response.mimetype.startswith(prefix) for prefix in COMPRESSIBLE_TYPES)


def compress(response):
    """The response, gzipped in place when that is worth doing."""
    if not wants_gzip() or not is_compressible(response):
        return response
    # a static file is streamed through untouched by default; reading it here is what
    # makes it compressible, and MAX_SIZE bounds what that reads
    response.direct_passthrough = False
    body = response.get_data()
    # mtime pinned so the same body always gzips to the same bytes, and the ETag names
    # this representation rather than the plain one
    response.set_data(gzip.compress(body, compresslevel=LEVEL, mtime=0))
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(response.content_length)
    response.headers.add("Vary", "Accept-Encoding")
    etag, weak = response.get_etag()
    if etag:
        response.set_etag(f"{etag}-gzip", weak)
        # send_file compared If-None-Match with the plain ETag before this ran, so a
        # client revalidating its gzipped copy never matched and got the body again
        response.make_conditional(request)
    return response


def register(app):
    if app.config.get("MCRITWEB_COMPRESS_RESPONSES", True):
        app.after_request(compress)
