# The rest of #63, done in the application after all

---
status: accepted — measured 2026-09-06, supersedes 0015
---

ADR-0015 measured the five bullets of #63 and left four of them alone: text compression
belonged in the proxy, `defer` needed an audit first, lazy Dropzone meant reimplementing
Flask-Dropzone's output, and the CSS was gzip's problem. Every one of those was true as
a statement about cost, and none of them was a reason not to do the work once it was
paid. This ADR records what was done and what changed the arithmetic.

All figures: headless Chromium, 1400×900, the app served by `flask run` against a live
MongoDB + mcrit instance, `/explore/samples`.

| | before | after |
|---|---|---|
| decoded bytes | 1,706 KB | 582 KB |
| bytes on the wire | 1,712 KB | 264 KB |
| render-blocking scripts in `<head>` | 6 | 2 (jQuery, autocomplete) |

## Text compression, without a dependency

0015 rejected `flask-compress` because the reference deployment always terminates in
NGINX. That is still the case, and NGINX is still where compression *should* happen
there. What it left uncovered is every other shape: `flask run` in development, a
waitress deployment without a proxy, and a proxy whose config never grew the `gzip`
directive (docker-mcrit's has not). `mcritweb/compression.py` is forty lines over the
standard library's `gzip`: compressible types only, bodies over 1 KiB only, clients that
accept it only, `Vary: Accept-Encoding`, `mtime=0` so the bytes are stable, and an ETag
suffix so a cache keeps the two encodings apart. A proxy that compresses on its own sees
`Content-Encoding` already set and leaves the body alone, so leaving it on behind NGINX
costs nothing; `MCRITWEB_COMPRESS_RESPONSES = False` hands the job over entirely.

## `defer`, after the audit

The audit 0015 asked for was done. The scripts that touch Bootstrap at parse time are the
`new Autocomplete(...)` constructions (`autocomplete.js` builds a Bootstrap `Dropdown` in
its constructor); those moved inside `DOMContentLoaded`. Everything else that calls a
library at parse time calls jQuery, which stays a plain script. `bootstrap.bundle.min.js`
and `post_action` are deferred; the `onclick=` handlers are untouched because no function
they name moved into a block scope. Bootstrap is deferred but never lazy: its jQuery
plugin registrations (`$.fn.tooltip`, `$.fn.dropdown`) have to exist before the page's own
ready handlers run, and `tests/testPageAssets.py` pins both that it is deferred and that
nothing loads it later.

## Dropzone, constructed explicitly

0015 was right that injecting `dropzone.js` late binds the form with default options,
because auto-discovery runs immediately once `readyState` is `complete`. The fix is the
one it named: the macro still uses `dropzone.create()` and `dropzone.config()` for their
markup and option block, rewrites the class to `dropzone-pending` and the option
assignment to `window.mcritDropzoneOptions`, and a small loader fetches the script and
stylesheet on the first `dragover` or when the submit modal opens, sets
`Dropzone.autoDiscover = false`, and calls `new Dropzone(element, options)` itself. The
project's patched `static/dropzone.js` (the pre-fill patch) is what loads; the stock
`dropzone.min.js` is retired and a test keeps it that way.

## The CSS

`bootstrap.min.css` is the minified build of the vendored 5.0.2 sources, produced with
`rcssmin`, so nothing is vendored twice. Font Awesome is subset by
`scripts/subset_fontawesome.py`, which scans the templates for the `fa-*` classes in use
and writes only those glyph rules to `static/css/fontawesome-subset.css`; the webfonts
are unchanged. `tests/testAssetDiet.py` fails if a template uses an icon the subset does
not carry, so adding an icon means re-running the script and committing the result.

## Consequences

- The rule from 0015 stands: reference vendored libraries, inline the lines that wire
  them to this page's data, and put a library in `base.html` only if most pages call it.
- A new icon needs `scripts/subset_fontawesome.py` run again; the test says so.
- A new inline script that calls Bootstrap must run from `DOMContentLoaded` or later.
- gzip in the proxy is still worth turning on for the reference deployment, for the
  bytes the proxy serves itself; it is no longer the only place the bullet can be closed.
