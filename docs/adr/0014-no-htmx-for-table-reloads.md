# No htmx for table reloads; show the wait instead

---
status: accepted — decides #60
---

Issue #60 proposes htmx for (re)loading tables and pagination: *"reload all tables via
htmlx, show spinner"* and *"initial loading via htmx and stencil"*. It says **consider**,
so a decision is the resolution. This is that decision: **do not adopt htmx now.** Ship
the feedback the issue is really asking for — `static/page_loading.js` draws a spinner
over the outgoing page while a pagination or sort click is in flight — and leave the
navigation itself alone.

The numbers below were taken so the question does not have to be re-argued from scratch.

## The vendoring rule is not the reason

The obvious objection is AGENTS.md § Front-end: *no build step, no npm, no bundler*.
htmx passes that test, and it would be dishonest to hide behind it.

- `htmx.min.js` 2.0.4 is **50,917 B** (16,326 B gzipped), one file.
- Zero dependencies, licensed 0BSD.
- It is used by a `<script src>` tag and HTML attributes. No toolchain, no CDN, works
  offline, and it would sit alongside Bootstrap and jQuery under the same rule that
  admitted them.

So this ADR rejects htmx on cost and benefit, not on principle. If the balance changes,
nothing in the repo's conventions stands in the way.

## What htmx would buy, measured

Measured offline against the captured corpus (`tests/fixtures/matches_for_sample.result.json`,
135,128 B) on Python 3.11.9 / Flask 3.1.3, driving `/data/result/<job_id>` — the heaviest
page in the application and the one #60 is about. Page size was swept over
`funl` ∈ {10, 25, 50, 100, 250} and the per-row and fixed costs separated by a linear fit,
so "the table" and "everything else" are measured rather than guessed.

At the default page size of 100 function matches:

| | full page | the paginated table | what a swap would save |
| --- | --- | --- | --- |
| response | 206,750 B | 160,264 B (78%) | **46,486 B (22%)** |
| server time | 18.50 ms | 3.53 ms (19%) | at most 14.97 ms (81%) — but see below |

The bytes are the headline: **the table is 78% of the page.** Paging is a request for
different rows, so the rows have to travel either way; the chrome is the only thing a
swap stops re-sending, and on this page the chrome is a fifth of it. Page 1 and page 2 of
the same report share only 26% of their bytes line for line.

The server time is worse than the 81% suggests, because most of it is not the chrome.
Broken down on a comparable request against the same report (`?funp=2`, page size 100):

```
total                         17.92 ms
load_cached_result             2.06 ms
MatchingResult.fromDict        1.14 ms
render_template (all Jinja)    9.57 ms
unattributed                   5.16 ms
```

`load_cached_result` and `MatchingResult.fromDict` run on **every** request for that
report regardless of which page was asked for — the view loads and parses the whole
report and then slices it in memory. They scale with the size of the report, not with the
page size, and htmx removes neither. That is the cost issue #68 is about, and it is where
a multi-second wait on a real deployment comes from: on a cache miss the same view calls
`client.getResultForJob()`, pulling the entire report from mcrit-server over HTTP.

Which is the point. The reproduction for this issue has to *stall the backend by hand* to
make the problem visible, because the rendering was never the slow part. htmx makes the
response smaller; it does not make the backend answer sooner.

The ratio is friendlier on the small list pages — `/explore/samples` is 61,506 B of which
the table is 35,935 B, so 42% is chrome — but those pages already answer in about 4 ms.
The saving is largest exactly where it is least needed.

## What it would cost, measured

**Row clicks stop working.** This is the one that decided it, and it is not a prediction.
Every click handler on a table row is a *direct* jQuery binding made inside
`$(document).ready`, against the elements that exist at that moment — nine of them:
`table/sample_row.html`, `table/family_row.html`, `table/function_row.html`,
`table/job_row.html`, `compare.html`, `compare_versus.html` (two), `cross_compare.html`
and `result_cross.html`. htmx replaces those elements. Driven in Chromium 151 against the
app served from the corpus:

```
A  /explore/samples rows: 13
A  baseline row click: /explore/samples -> /explore/samples/0   NAVIGATED
B  after swap: {'rows': 13, 'page_bytes': 61506, 'swap_bytes': 35935}
B  row click after swap: /explore/samples -> /explore/samples   DID NOT NAVIGATE
```

Step B does exactly what `hx-get` with `hx-swap="outerHTML"` does — fetch the markup,
replace the element with it. Thirteen rows are still there and they look identical. They
are simply dead, silently, with nothing in the console. Getting them back means
converting all nine sites to delegated bindings (or re-binding on `htmx:afterSwap`)
*before* the first table is swapped, and the failure mode for missing one is invisible.

The rest of the surface:

- **25** `pagination_widget(...)` call sites across **17** templates, fed by **28**
  pagination objects built in **14** view functions. Every one of them is a place where
  the swap has to be wired and a fragment has to be served.
- **36** `<table>` elements in the templates, **2** of which carry an `id`. Nothing on a
  result page is addressable, so every target needs one before htmx has anywhere to aim.
- The paginations on a result page are anchored (`_anchor="function-matches"`) and one
  URL carries all of them at once: `result_compare_all.html` renders three, so a page link
  is `?famp=…&libp=…&funp=…#function-matches`. Reproducing today's back-button and
  deep-link behaviour means `hx-push-url` plus merging those parameters by hand, which is
  exactly what `pagination_js_helper` already does.
- `cross_compare.html` paginates through `_js_argument_provider="fetchState"`, so its
  page links carry client-side selection state into the URL. That path has no href at all.

One thing that looks like a cost and is not: `jobs.html` initialises DataTables on
`#job-table`, which would normally have to be destroyed and re-created around a swap. It
never runs — the page renders the table with `table_id=active`, so the selector matches
nothing and `isDataTable('#job-table')` is `false` in the browser. DataTables is inert
here today.

## "stencil"

The second bullet, *"initial loading via htmx and stencil"*, reads as a skeleton
placeholder — grey bars in the shape of the table while it loads. That is the only
reading that pairs sensibly with htmx and with the first bullet's spinner, both of which
are about what is on screen during a wait. The alternative reading —
[Stencil](https://stenciljs.com/), the web-component compiler — is ruled out by AGENTS.md
outright: it is npm and a build step.

Under the intended reading it is still not free. The vendored Bootstrap is **5.0.2**, and
its stylesheet carries no `.placeholder` rule at all, so a skeleton means hand-written CSS
or a Bootstrap bump — and bumping a vendored asset is its own decision.

## What ships instead

`static/page_loading.js`, a script tag in `base.html`, and three lines in
`pagination_widget.html`. A pagination or sort click that will actually replace the
document raises a Bootstrap spinner over the outgoing page after 150 ms, and it goes away
with the page. Measured in the same browser session against a backend stalled by 1.5 s,
the overlay is on screen at **t+175 ms**, where before there was nothing at all until the
new document painted.

That is the whole of the first bullet's user-visible intent — *"show spinner"* — for 174
lines of vendored-nothing, and it is orthogonal to the transport: if the tables ever do
move to htmx, `hx-indicator` replaces it and nothing else changes.

## Consequences

Full-page navigation stays. Every pagination click keeps re-sending ~46 KB of chrome it
did not need to, and the browser keeps re-parsing it. On a LAN, against a backend that is
the actual bottleneck, that is not what anyone is waiting for.

The second bullet of #60 — a skeleton on first load — is **not** addressed, and cannot be
without either htmx or a hand-rolled fetch. It is deliberately left open.

## When to revisit

In this order. The first two are worth doing whether or not htmx ever lands, and the
third is the one that changes the arithmetic:

1. **Delegate the nine row-click bindings** (`$(document).on("click", "tr.sample-row", …)`
   rather than `$("tr.sample-row").click(…)`). This is a small, self-contained change that
   removes the sharpest edge above, and it is the precondition for any partial rendering
   at all, htmx or not.
2. **Give the paginated tables ids** and wrap each in a container the server can render on
   its own — a `{% macro %}` per table that both the page template and a fragment response
   can call. Without this there is nothing to target.
3. **Fix the retrieval cost first (#68).** While a pagination click re-reads and re-parses
   the entire report, shrinking the response is polish on top of the real wait. If #68
   lands and a page change becomes cheap on the server, the 22% of bytes and the round
   trip start to matter, and this decision should be taken again — with the same
   measurements re-run, because they are all fixture-scale and it is the ratios, not the
   milliseconds, that carry the argument.
