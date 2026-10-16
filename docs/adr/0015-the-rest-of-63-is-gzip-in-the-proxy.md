# The rest of #63 is gzip in the proxy, not more surgery here

---
status: superseded by 0016 — measured 2026-08-30; each "not now" below was later done, see 0016 for what changed the arithmetic
---

Issue #63 is a five-item checklist about browser performance. Removing DataTables and
scoping jQuery UI to the one page that uses it — its first bullet — is done and is worth
roughly half of a cold first paint. Everything the list has left is either **a
deployment change** or **worth less than it costs**, and the numbers below are the
reason rather than a preference.

All figures: one machine, headless Chromium, the app served over real HTTP against the
captured corpus in `tests/fixtures/`. Byte counts are of the bodies as served — they are
the reproducible part; the milliseconds are one machine and one Chromium build.

## Where the checklist stands

| bullet | verdict |
|---|---|
| remove data-tables js/css | **done** — the change this ADR accompanies |
| lazy-load dropzone / autocomplete on mouseover | **not now** — costs a rewrite of the Flask-Dropzone integration, buys no first paint |
| consider removing jQuery | **no** — 15 inline blocks, two vendored files and Bootstrap's own jQuery bridge depend on it |
| enable text compression | **yes, and it is the largest single win left** — but it belongs in `docker-mcrit` |
| what to inline vs reference | **rule recorded below**; nothing to change today |

## What the first bullet was worth

Total bytes a page ships (document plus every `/static/` file it references):

| page | before | after | delta |
|---|---|---|---|
| `/` | 1,621,593 | 935,520 | −686,073 |
| `/settings` | 1,674,634 | 988,561 | −686,073 |
| `/explore/samples` | 1,650,077 | 964,004 | −686,073 |
| `/data/jobs` | 1,637,892 | 951,517 | −686,375 |
| `/explore/samples/1` | 1,619,030 | 932,957 | −686,073 |
| `/data/jobs/<id>` | 1,607,685 | 921,612 | −686,073 |
| `/data/result/<cross>` | 1,720,977 | 1,621,889 | −99,088 |

Render-blocking bytes in `<head>` fall from 1,234,797 to 549,016 on every page but the
cross compare, which keeps jQuery UI and lands at 1,135,901.

Cold load of `/` on a simulated Fast-3G link (1.6 Mbit/s, 150 ms RTT), empty cache,
median of 7 runs:

| | first paint | DOMContentLoaded | load | bytes on the wire |
|---|---|---|---|---|
| before | 8,112 ms | 8,106 ms | 8,997 ms | 1,756,829 |
| after | **3,872 ms** | 5,511 ms | 5,512 ms | 1,069,548 |
| after, with gzip on | **1,024 ms** | 1,585 ms | 2,113 ms | 354,002 |

## Text compression belongs in `docker-mcrit`

Nothing is compressed anywhere today. Flask's static handler does not compress, and the
reference deployment does not either: `nginx/nginx.conf`, `nginx/mcritweb_plain.conf`
and `nginx/mcritweb_ssl.conf` in `danielplohmann/docker-mcrit` (checked at
`7e9501ae28e2ea627115b45c4e971af075130cce`) contain no `gzip` directive, and `location /`
proxies everything — `/static/` included — straight to Flask.

What one `gzip on;` would do to the assets `base.html` loads (`gzip -9`):

| asset | bytes | gzipped | ratio |
|---|---|---|---|
| `bootstrap-5.0.2-dist/css/bootstrap.css` | 206,543 | 25,039 | 8.25× |
| `css/all.css` | 147,661 | 23,306 | 6.34× |
| `dropzone.js` | 353,924 | 77,110 | 4.59× |
| `jquery.js` | 89,503 | 30,882 | 2.90× |
| `bootstrap.bundle.min.js` | 78,749 | 22,383 | 3.52× |
| `dropzone.min.css` | 9,830 | 1,552 | 6.33× |
| `hint.css` | 5,906 | 1,171 | 5.04× |
| `autocomplete.js` | 4,621 | 1,507 | 3.07× |
| `style.css` | 4,378 | 1,313 | 3.33× |
| `post_action.js` | 1,545 | 794 | 1.95× |
| `navbar.css` | 280 | 162 | 1.73× |
| `/` (HTML) | 32,580 | 6,765 | 4.82× |

`jquery-ui.js`, which the cross compare still loads, is 548,118 → 127,207 (4.31×).

So gzip takes `/` from 935,520 bytes to about 192,000, and first paint from 3.87 s to
1.02 s — **more than the whole of the first bullet, from three lines of NGINX config.**
That is the single most valuable thing left on #63 and it cannot be done here.

Doing it in the application instead would mean adding `flask-compress` as a runtime
dependency to compress bytes a proxy is already positioned to compress, on a deployment
that always terminates in NGINX. Not worth a dependency. If a deployment without a
proxy ever becomes a supported shape, revisit.

## Lazy-loading the dropzone: right idea, wrong shape today

`dropzone.js` is 353,924 bytes, 38% of what every page now ships and by a wide margin
its largest single file, and it exists on most pages only to serve a drag-and-drop
overlay (`base.html`'s `#submitFileModal`) that nothing but a `dragover` on the window
ever opens.

It is loaded at the end of `<body>`, though, not in `<head>`, so it costs **nothing** in
first paint. Blocking it outright — an upper bound on what any lazy loader could buy, on
top of gzip:

| | first paint | DOMContentLoaded | load | bytes |
|---|---|---|---|---|
| gzip | 1,024 ms | 1,585 ms | 2,113 ms | 354,002 |
| gzip, dropzone blocked | 1,016 ms | 1,009 ms | 1,908 ms | 274,066 |

Half a second of DOMContentLoaded and 23% of the transfer, and no first paint at all.

The cost is not the loader, it is Flask-Dropzone. `dropzone.create()` emits
`<form class="dropzone" id="myDropzone">` and `dropzone.config()` emits
`Dropzone.options.myDropzone = {…}` *after* the library, which works only because
Dropzone's auto-discovery waits for DOMContentLoaded. Inject the script later and
`contentLoaded()` sees `document.readyState === "complete"` and discovers immediately,
at which point `Dropzone.options.myDropzone` does not exist yet and the element is bound
with default options — no CSRF header, no `autoProcessQueue: false`, no size limit.
Doing this properly means constructing the Dropzone explicitly instead of through
`dropzone.create()`/`config()`, i.e. reimplementing that macro's output by hand, in the
one place `static/dropzone.js`'s local patch (`file.upload.header`, which pre-fills the
submit form) has to keep working. That is its own change with its own reproduction.

The other half of the bullet, "autocomplete on mouseover", is not worth measuring
twice: `autocomplete.js` is 4,621 bytes, 1,507 gzipped. **No.**

## jQuery stays

Not a judgement, a census:

- **15 of the 26 inline `<script>` blocks** in `templates/` call `$`, plus 36 inline
  `onclick=`/`onchange=` attributes bound to functions those blocks declare.
- `static/trace_CFG/main.js` and `main_duo.js` — vendored from CFGExplorer, and
  `main_duo.js` is a project fork — use jQuery and get it from `base.html`. (Both CFG
  templates carry their own `jquery-3.1.1.min.js` include, commented out; leave it that
  way. A second jQuery replaces `window.$` after Bootstrap has already attached its
  plugins to the first.)
- jQuery UI cannot load without it, so it cannot leave the cross compare at all while
  `.sortable()` stays.
- `$('[data-toggle="tooltip"]').tooltip()` and `$("#submitFileModal").modal("show")` go
  through **Bootstrap's** jQuery bridge. Dropping jQuery means rewriting those as
  `new bootstrap.Tooltip(el)` / `bootstrap.Modal.getOrCreateInstance(el).show()`.

For 89,503 bytes — 30,882 gzipped, under 9% of a compressed page. The work is large, the
risk is every row-click handler in the application, and the prize is smaller than
turning gzip on. **No.**

## Which library owns `$.fn.tooltip`

[ADR-0004](0004-minifying-html-is-not-worth-a-dependency.md) called this a load-order
race and warned that scoping jQuery UI per page could hand the name back. Measured, that
is not what decides it.

Bootstrap 5.0.2 registers its jQuery plugins on `DOMContentLoaded`, which is strictly
after **every** synchronous `<script>` in the document has run. So source order is
irrelevant among plain script tags. Both arrangements were checked in Chromium, on every
page in the app that renders a `[data-toggle="tooltip"]` target:

- jQuery UI **before** Bootstrap (`base.html` as it was): `$.fn.tooltip.Constructor.NAME`
  is `"tooltip"`, `bootstrap.Tooltip.getInstance(el)` is set, `$(el).data("ui-tooltip")`
  is not.
- jQuery UI **after** Bootstrap (`result_cross.html` as it is now): identical.

What *does* flip it is loading jQuery UI after DOMContentLoaded. Injected from the
console on `/settings`, `$.fn.tooltip` becomes jQuery UI's immediately — existing
Bootstrap instances survive, so `copy_to_clipboard` keeps working, but every subsequent
`.tooltip()` call builds a jQuery UI widget instead.

**So the hazard is lazy loading, not ordering.** A future change that loads jQuery UI on
demand — the natural next step for the cross compare — has to call
`new bootstrap.Tooltip(...)` directly, or re-run Bootstrap's plugin definition, or keep
jQuery UI synchronous. `tests/testPageAssets.py` pins the synchronous arrangement so the
question at least has to be answered deliberately.

## What to inline and what to reference

`/` after the first bullet: 32,580 bytes of HTML, of which 7,141 is inline JavaScript
and 122 inline CSS; 374,598 bytes of referenced CSS and 528,342 of referenced JS. The
inline share is under 1% of the page and it is the part that is page-specific and
changes with the data — exactly what should not be in a cacheable file. The referenced
share is vendored libraries — exactly what should not be inlined.

The split is already right. The rule, for anything new: **reference vendored libraries,
inline the handful of lines that wire them to this page's data**, and put a library in
`base.html` only if most pages call it. What is left to attack is the size of the
vendored CSS — `bootstrap.css` is the *unminified* build at 206,543 bytes and Font
Awesome's `all.css` is 147,661 — but both compress 6–8×, which makes gzip the answer to
that too, and shipping `bootstrap.min.css` would mean vendoring another copy for an 8%
gain over gzip.

## Consequences

- #63's remaining value lives in `docker-mcrit`: `gzip on;` plus `gzip_types` for
  `text/css application/javascript text/html application/json` in `nginx/nginx.conf`.
  Open it there; it is not an MCRITweb change.
- Do not add `defer` to `base.html`'s scripts without auditing the 26 inline blocks and
  36 event attributes first — several call libraries at parse time, and wrapping a block
  in `DOMContentLoaded` turns its function *declarations* into locals, silently breaking
  every `onclick=` that names them.
- Lazy-loading anything that defines a jQuery plugin changes which library owns the
  name. Assert it, do not assume it.
