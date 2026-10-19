# Minifying HTML is not worth a dependency

---
status: accepted — measured 2026-08-30
---

Issue #48 asks for three things: `{%- -%}` whitespace control in Jinja, an HTML
minifier, and a prettifier in debug mode. **The first two should not be done**, and the
numbers are the reason rather than a preference.

Whitespace-collapsing every rendered page — an *upper bound* on what either `{%- -%}` or
a minifier could save — measured against the offline corpus:

| page | raw HTML | ws-stripped | saving | gzip raw | gzip min | **gzip saving** |
|---|---|---|---|---|---|---|
| `/` | 32,880 | 28,432 | 13.5% | 6,795 | 6,359 | **436 B** |
| `/data/jobs` | 49,175 | 41,384 | 15.8% | 8,907 | 8,145 | **762 B** |
| `/data/result/…` (1vsN) | 206,344 | 142,401 | 31.0% | 12,340 | 11,176 | **1,164 B** |
| `/explore/samples` | 61,360 | 49,797 | 18.8% | 9,808 | 9,029 | **779 B** |
| **13 pages** | **1,155,307** | **910,958** | **21.2%** | **129,019** | **120,401** | **8,618 B** |

Against what a page actually ships. Vendored assets loaded unconditionally by
`base.html` total **1,184,774 bytes raw / 277,117 gzipped** (`gzip -6`, the level used
for every gzip figure here) - 798,006 / 216,020 of that is JavaScript. Cold load in
headless Chromium, counting every response body (these are decoded sizes, not transfer
sizes - the dev server does not compress):

```
index        doc 32,880 | script 1,141,444 (n=8) | css 396,598 (n=8) | font 150,472 | img 18,876
             TOTAL 1,740,270 — html share 1.9%
result 1vsN  doc 206,344 | script 1,141,444 | css 396,598 | font 175,568 | img 22,362
             TOTAL 1,942,316 — html share 10.6%
```

Comparing like with like on the heaviest page in the application:

* **raw against raw** — 63,943 bytes of whitespace out of a 1,942,316-byte load: **3.3%**.
* **gzipped against gzipped** — 1,164 bytes out of a transfer of *at least* **486,223**
  bytes: **under 0.24%**. That denominator is a floor built only from measured parts —
  the gzipped document (11,176), gzipped `base.html` CSS (61,097) and JS (216,020), plus
  fonts (175,568) and images (22,362), which are already-compressed formats no server
  re-compresses. The result page loads a further ~353 KB of raw CSS/JS beyond
  `base.html`; counting it can only push the share lower.

On light pages the saving is 400–800 bytes gzipped.

The `{%- -%}` variant is worse than merely not worth it: it means editing ~40 templates,
it silently changes rendering inside `<pre>` (`column_table.html`'s error block is
`<pre><code>`), and it makes every future template diff noisier.

## Consequences

The payload problem in this application is vendored JavaScript, not markup whitespace:
**798 KB raw / 216 KB gzipped on every page** from `base.html` alone, reaching 1.14 MB
raw on a result page. `jquery-ui.js` is 529 KB raw / 127 KB gzipped of that. Issue #63 is
the right shape for it — loading a library only on the pages that use it — and it is
worth roughly 100× what minification is.

Before acting on #63, note what actually consumes jQuery UI, because it is not obvious:

* `$( "#sortable" ).sortable()` at `result_cross.html:73`. That is the **only** direct
  call in the repository.
* `static/autocomplete.js` does *not* use it — it drives `bootstrap.Dropdown`
  (`autocomplete.js:28`) and contains no jQuery UI call at all.
* `$('[data-toggle="tooltip"]').tooltip()` at `table/links.html:45`, run from
  `base.html:49` on every page, resolves to **Bootstrap's** tooltip, not jQuery UI's —
  but only by a load-order race. `jquery-ui.js` registers `$.fn.tooltip` when it loads;
  Bootstrap 5.0.2's `defineJQueryPlugin` overwrites the same name on `DOMContentLoaded`,
  which lands before `clipboard_js()`'s `$(function(){…})` handler. Measured in headless
  Chromium with `base.html`'s script order: `bootstrap.Tooltip.getInstance(el)` is set,
  `$.data(el, "ui-tooltip")` is not.

So dropping `jquery-ui.js` from every page but cross-compare is safe today. It is safe
*because of that race*, though — deferring Bootstrap, or scoping it per page, would hand
the name back to jQuery UI and silently change every tooltip in the application. Whatever
#63 does, it should assert which library owns `$.fn.tooltip` rather than assume.

The third bullet, a prettifier in debug mode, is a separate and much smaller question
about developer experience, not payload. It is not decided here.

## Outcome

Recommend closing the minify and `{%- -%}` halves of #48 as measured-not-worth-doing, and
opening the vendored-JS weight as its own issue if it is not already covered by #63.
