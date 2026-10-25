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

The project's own stylesheets are served with their comments removed (strip_comments). The
comments say why a rule is there and stay in the files; a browser does not need them, and on
the combined branches they were two thirds of the 15 KB (gzipped) that every page's first
paint waits for. The stripped body is kept per file, and made again when the file's size or
modification time changes, so a changed file is served changed on the next request, with the
ETag of the body that is sent.

Only the static endpoint is affected. Match diagrams and the manual's screenshots are sent
with send_from_directory too, and are still revalidated on every view.
"""

import hashlib
import os

from flask import Response, request
from werkzeug.security import safe_join

#: how long the answer to a URL carrying its file's current `v` may be kept
VERSIONED_MAX_AGE = 365 * 24 * 3600
#: and the answer to any other /static/ URL
UNVERSIONED_MAX_AGE = 3600
#: static files, and folders ending in "/", that another static file names by a relative
#: URL. tests/testStaticCaching.py finds those URLs and fails on one that is not covered here.
NAMED_BY_STATIC_FILES = ("webfonts/", "trace_CFG/lib/dagre-d3.min.js")

#: the project's own stylesheets, served without their comments. Listed one by one: the
#: vendored ones (Bootstrap, Font Awesome, jQuery UI, hint.css, ...) are served as they are.
STRIPPED_STYLESHEETS = ("style.css", "theme-dark.css", "navbar.css", "signin.css")

#: path -> ((mtime_ns, size), digest)
_digests = {}
#: path -> ((mtime_ns, size), stripped body, ETag)
_stripped = {}


def strip_comments(css):
    """The stylesheet without its /* ... */ comments, which do not nest. A comment that
    starts /*! stays (a licence). Text in a quoted string and in an unquoted url(...) is
    copied as it is, and so is anything after a backslash: `content: "/*"` and `\\/*` are not
    comments. A comment that is the only thing on its line takes the line with it; one
    between two words leaves a space, so that `a/**/b` is not read as `ab`."""
    out = []
    # whether nothing but blanks has been written since the last newline
    blank_line = True
    i, n = 0, len(css)
    while i < n:
        c = css[i]
        if c == "\\":
            out.append(css[i:i + 2])
            blank_line = False
            i += 2
        elif c in "\"'":
            # a string ends at its quote, or, unterminated, at the end of its line
            j = i + 1
            while j < n and css[j] != c and css[j] != "\n":
                j += 2 if css[j] == "\\" else 1
            if j < n and css[j] == c:
                j += 1
            out.append(css[i:j])
            blank_line = False
            i = j
        elif c in "uU" and css[i:i + 4].lower() == "url(" and not (out and (out[-1][-1:].isalnum() or out[-1][-1:] in "-_")):
            j = i + 4
            while j < n and css[j] in " \t\r\n":
                j += 1
            if j < n and css[j] in "\"'":
                out.append(css[i:j])  # url("...") is a string: the branch above takes it
                blank_line = False
                i = j
            else:
                while j < n and css[j] != ")":
                    j += 2 if css[j] == "\\" else 1
                out.append(css[i:j + 1])
                blank_line = False
                i = j + 1
        elif css.startswith("/*", i):
            end = css.find("*/", i + 2)
            end = n if end < 0 else end + 2
            if css.startswith("/*!", i):
                out.append(css[i:end])
                blank_line = False
                i = end
                continue
            rest_end = css.find("\n", end)
            rest_end = n if rest_end < 0 else rest_end
            if blank_line and not css[end:rest_end].strip():
                # nothing else on the line: drop the indentation, the comment and the newline
                while out and out[-1] in (" ", "\t"):
                    out.pop()
                i = min(rest_end + 1, n)
                continue
            before, after = out[-1][-1:] if out else "", css[end:end + 1]
            if (before.isalnum() or before in "-_") and (after.isalnum() or after in "-_") and before and after:
                out.append(" ")
            i = end
        else:
            out.append(c)
            if c == "\n":
                blank_line = True
            elif not c.isspace():
                blank_line = False
            i += 1
    return "".join(out)


def stripped_stylesheet(static_folder, filename):
    """(body, ETag, mtime) of one of STRIPPED_STYLESHEETS as it is on disk now, or None."""
    path = safe_join(static_folder, filename)
    if path is None:
        return None
    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
        known = _stripped.get(path)
        if known is None or known[0] != stamp:
            with open(path, "rb") as file:
                body = strip_comments(file.read().decode("utf-8")).encode("utf-8")
            known = _stripped[path] = (stamp, body, hashlib.sha256(body).hexdigest()[:32])
    except (OSError, UnicodeDecodeError):
        return None
    return known[1], known[2], stat.st_mtime


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
    send_static_file = app.view_functions["static"]

    def static(filename):
        """The static view, but a stylesheet of the project is sent without its comments.
        Built as send_from_directory builds a file's answer: an ETag, Last-Modified and a
        conditional response, so a 304 and the gzip of compression.py behave the same."""
        served = stripped_stylesheet(app.static_folder, filename) if filename in STRIPPED_STYLESHEETS else None
        if served is None:
            return send_static_file(filename=filename)
        body, etag, mtime = served
        response = Response(body, mimetype="text/css")
        response.set_etag(etag)
        response.last_modified = int(mtime)
        response.cache_control.no_cache = True
        return response.make_conditional(request)

    app.view_functions["static"] = static

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
