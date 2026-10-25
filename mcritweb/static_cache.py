"""Static files a browser keeps, named by URLs that change when the files do.

Flask sends a static file with `Cache-Control: no-cache` and an ETag, so the browser asked
for every one of them again on every page: on /explore/samples after a visit to
/explore/families, all 16 went to the network and came back 304, five of them the
stylesheets the first paint waits for. The reference deployment's NGINX passes /static/
to Flask and caches nothing itself (docs/adr/0015-the-rest-of-63-is-gzip-in-the-proxy.md).

`url_for('static', filename=...)` adds `v`, a digest of the file as it is on disk, and the
answer to a URL whose `v` is the digest of the file it serves may be kept for a year and
is never asked for again (`immutable`). A changed file is a changed URL on the next page,
whether it came with a `git pull` and a restart or with an edit under `flask run`, since
the digest is taken again whenever the file's size or modification time changes.

A file that another static file names by a relative URL cannot carry a `v`, because that
URL has none: the fonts css/fontawesome-subset.css names, the images
trace_CFG/cfg_style.css names and the dagre bundle trace_CFG/layout_worker.js imports.
They are kept for an hour and then revalidated, which bounds what a deployment that
changes one of them can leave stale. A page that names one of them as well (the font
preload in base.html, the CFG pages' dagre script) has to ask for the URL the other file
asks for, or the browser fetches the file twice, so url_for leaves `v` off them. Any other
URL without the current `v`, such as a page rendered before its file changed, gets the
hour as well.

Only the static endpoint is affected. Match diagrams and the manual's screenshots are sent
with send_from_directory too, and are still revalidated on every view.
"""

import hashlib
import os

from flask import request
from werkzeug.security import safe_join

#: how long the answer to a URL carrying its file's current `v` may be kept
VERSIONED_MAX_AGE = 365 * 24 * 3600
#: and the answer to any other /static/ URL
UNVERSIONED_MAX_AGE = 3600
#: static files, and folders ending in "/", that another static file names by a relative
#: URL. tests/testStaticCaching.py finds those URLs and fails on one that is not covered here.
NAMED_BY_STATIC_FILES = ("webfonts/", "trace_CFG/lib/dagre-d3.min.js")

#: path -> ((mtime_ns, size), digest)
_digests = {}


def version_of(static_folder, filename):
    """The `v` of a static file, a digest of its content, or None where it gets none:
    a file another static file names, or one that is not there."""
    if filename.startswith(NAMED_BY_STATIC_FILES):
        return None
    path = safe_join(static_folder, filename)
    if path is None:
        return None
    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
        known = _digests.get(path)
        if known is None or known[0] != stamp:
            with open(path, "rb") as file:
                known = _digests[path] = (stamp, hashlib.file_digest(file, "sha256").hexdigest()[:12])
    except OSError:
        return None
    return known[1]


def register(app):
    @app.url_defaults
    def add_static_version(endpoint, values):
        if endpoint == "static" and "v" not in values:
            version = version_of(app.static_folder, values.get("filename", ""))
            if version:
                values["v"] = version

    @app.after_request
    def static_cache_lifetime(response):
        if request.endpoint != "static" or response.status_code not in (200, 304):
            return response
        asked = request.args.get("v")
        current = version_of(app.static_folder, (request.view_args or {}).get("filename", ""))
        cache_control = response.cache_control
        cache_control.no_cache = None
        cache_control.public = True
        if asked and asked == current:
            cache_control.max_age = VERSIONED_MAX_AGE
            cache_control.immutable = True
        else:
            cache_control.max_age = UNVERSIONED_MAX_AGE
        return response
