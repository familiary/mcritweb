"""gzip for the static files a browser can decode, without a proxy in front.

The reference deployment (docker-mcrit) does not compress: its NGINX configuration has no
`gzip` directive (docs/adr/0015-the-rest-of-63-is-gzip-in-the-proxy.md), and a bare
`flask run` or waitress does not either, so every stylesheet and script went over the
wire uncompressed - the "enable text compression" bullet of issue #63. This compresses
text, JSON, JavaScript and SVG bodies between 1 KiB and MAX_SIZE when the client accepts
gzip, static files included. A proxy that compresses leaves an already encoded body
alone, so the two do not stack. Off with MCRITWEB_COMPRESS_RESPONSES.

Pages are compressed too. A secret that is the same in every response, compressed next
to input the page reflects (a search term), is what a BREACH attack reads out of the
response's length. The one such secret every page carries, the CSRF token, is masked
afresh on every render (`mcritweb.csrf.mask`), so it is never the same twice.

Left alone:
- A page that shows any other secret: a view marked `@uncompressed`. The settings page
  shows the user's API token, and the server page, before and after a change, the
  registration token and the backend's token. The username is printed on every page,
  next to a search term on some; it is not a secret, and is compressed.
- Downloads (`Content-Disposition: attachment`) and the API passthrough. The exports and
  the cached result download are streamed so that they never sit in memory whole, and
  gzipping them here would read them in.
- A body of unknown length, or one above MAX_SIZE, for the same reason.
"""

import gzip

from flask import current_app, request

COMPRESSIBLE_TYPES = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml")
#: the views marked @uncompressed, as "module.name" - see uncompressed()
_UNCOMPRESSED_VIEWS = set()
MIN_SIZE = 1024
# the largest asset that is compressed, static/jquery-ui.js, is 529,062 bytes
MAX_SIZE = 1024 * 1024
LEVEL = 6


def uncompressed(view):
    """Never gzip what this view answers: it shows a secret other than the CSRF token,
    which an attacker could otherwise read out of the response's length (BREACH)."""
    _UNCOMPRESSED_VIEWS.add(f"{view.__module__}.{view.__name__}")
    return view


def shows_a_secret():
    view = current_app.view_functions.get(request.endpoint) if request.endpoint else None
    return view is not None and f"{view.__module__}.{view.__name__}" in _UNCOMPRESSED_VIEWS


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
    if shows_a_secret():
        return False
    return any(response.mimetype.startswith(prefix) for prefix in COMPRESSIBLE_TYPES)


def compress(response):
    """The response, gzipped in place when that is worth doing."""
    if not is_compressible(response):
        return response
    # the body depends on Accept-Encoding whether or not this client takes gzip, and a cache
    # that keeps a static file for a year (mcritweb/static_cache.py) must not hand the plain
    # copy one client was sent to every other
    response.vary.add("Accept-Encoding")
    if not wants_gzip():
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
