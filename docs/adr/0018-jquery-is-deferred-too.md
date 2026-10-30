# jQuery is deferred too; inline scripts wait for it with whenReady

---
status: accepted — measured 2026-10-20; finishes the `defer` section of 0017, which kept
jQuery a plain script. Its loading is changed by 0021: the scripts are queued and loaded
after the first paint, not deferred, and a press on Bootstrap's controls before they are
in is replayed; the whenReady rule stands
---

[ADR-0017](0017-the-rest-of-63-done-in-the-application.md) deferred Bootstrap,
`post_action.js`, `page_loading.js` and `autocomplete.js`, and left jQuery a plain
script because inline blocks called `$` while the page parsed. That left jQuery the one
script base.html puts in every page's `<head>` that still held back the first paint, and
#245 named it as what stood between the mobile Lighthouse Performance score and 100. This records the
audit that deferring it took, the shape chosen, and the rule it leaves for templates.

## What reached for jQuery while the page parsed

At 05a082e (#245's head) these are 21 sites in 15 templates. The scanner in
`tests/testParseTimeScripts.py` looks for everything a deferred script defines, not only
jQuery, and finds 35 sites in 16 templates there: these 21, and the 14 `new Sortable(...)`
calls settings.html made while parsing - harmless while SortableJS was a plain script, the
same mistake once it is deferred (see the second table). It finds none after this change.

| kind | sites |
|---|---|
| `$(document).ready(fn)` | 16: base.html's drop overlay; the four row macros in `table/*_row.html`; `table/submit_or_query_dropzone.html` twice (its form toggles, and `mcritInitDropzone` on the upload pages); compare, compare_versus, cross_compare, unique_blocks, single_function, single_sample; result_cross three times |
| `$(fn)` | 2: `clipboard_js` in `table/links.html`, and result_cross's `.sortable()` |
| a binding at the top level of a block | 3: jobs.html's two delete-confirmation handlers, and result_cross's delegated `mouseover` that puts a matrix cell's tooltip together |

And the external scripts:

| script | where | needs jQuery when it runs? | now |
|---|---|---|---|
| `jquery-ui.js` | result_cross, `<head>` | yes - its wrapper calls `factory(jQuery)` | `defer`, after jQuery, before Bootstrap |
| `sortablejs/Sortable.min.js` | settings, `<head>` | no, but it held back the first paint there | `defer`; the `new Sortable(...)` calls run from `whenReady` |
| `dropzone.js` | import, query, submit, in `<body>` | no - it attaches `$.fn.dropzone` only if jQuery is already there (`typeof jQuery !== "undefined"`), and nothing calls that | unchanged |
| d3, graphlib, graphlib-dot, dagre-d3, convexhull, `trace_CFG/main.js`, `main_duo.js`, `fnManip.js`, `loopCollapser.js`, `loopify_dagre.js`, `function_compare.js` | the two CFG pages, end of `<body>` | no - every `$(...)` in `main.js` and `main_duo.js` is in a comment, although [ADR-0015](0015-the-rest-of-63-is-gzip-in-the-proxy.md) counted them among jQuery's users | unchanged |

What did not need touching: a function *declaration* whose body uses `$` - `fetchState`,
`createJob`, `deleteSample`, `show_extended`, `fill_form`, `mcritInitDropzone`,
`copy_to_clipboard` and the rest - runs when an `onclick=` or another script calls it,
not while the page parses. These stay top-level declarations, which is what lets the
`onclick=` attributes find them by name.

## The shape: `whenReady(fn)`

base.html defines, inline and above every other script:

```js
function whenReady(fn) {
  document.addEventListener("DOMContentLoaded", function () { jQuery(fn); });
}
```

and every site above hands its work to it. `$(document).ready(function () {...})` became
`whenReady(function () {...})`, `$(fn)` became `whenReady(fn)`, and the three top-level
bindings and the settings page's Sortable block are wrapped in
`whenReady(function () {...})`. Each half is there for a reason:

- **DOMContentLoaded** is the first moment at which every deferred script has run: the
  parser runs them, in document order, before it fires the event. Code started from there
  can rely on jQuery, Bootstrap's plugins and the page's own deferred libraries alike.
- **`jQuery(fn)`** rather than calling `fn` from the listener keeps the timing a ready
  handler always had. jQuery 3 runs ready handlers from a timer - its Deferred calls
  `.then` callbacks through `window.setTimeout` - so they ran after *every*
  DOMContentLoaded listener, and the upload pages depend on that: `mcritInitDropzone`
  calls `Dropzone.forElement("#myDropzone")`, which throws until Dropzone's own
  DOMContentLoaded listener, registered later by `dropzone.js` in the body, has found the
  form. Handing `fn` to jQuery also keeps the handlers in the order they were registered.

Checked in Chromium with the vendored jQuery 3.6.0, jQuery UI 1.13.1 and Bootstrap 5.0.2:
an inline script runs with `document.readyState` `"loading"` and `jQuery` undefined; a
deferred script runs with `"interactive"`; a DOMContentLoaded listener registered at the
end of `<body>` runs before the first `whenReady` handler; three handlers registered
head, head, body run in that order.

Considered and not taken:

- **A `$` stand-in that records calls until jQuery arrives.** It has to imitate every
  chain a script might start with it - `$(document).ready`, `$(fn)`, `$("#x").on(...)` -
  and is silently replaced when jQuery loads. More code than the edits it saves, and it
  hides a parse-time call instead of making it visible.
- **`<script type="module">` for the inline blocks.** Modules are deferred, but they have
  a scope of their own and run in strict mode, so every function declaration an
  `onclick=` names would disappear from `window`.
- **Wrapping whole blocks in a DOMContentLoaded listener.** The same problem, which
  ADR-0015 warned about: it turns the declarations into locals.
- **Moving the blocks into deferred files.** The values they get from Jinja through
  `|tojson` would have to travel in data attributes instead - a rewrite of 15 templates.

## The order of the deferred scripts

Deferred scripts run in document order, so the order of the tags now decides which
library defines what. In base.html:

1. the inline `whenReady`;
2. `jquery.js`;
3. `post_action.js`, `page_loading.js`, `autocomplete.js`;
4. `{% block style %}`, where a page loads its own libraries, deferred: jQuery UI on the
   cross compare, SortableJS on the settings page;
5. `bootstrap.bundle.min.js`.

Bootstrap moved below the block. Bootstrap 5.0.2 defines its jQuery plugins on
DOMContentLoaded if the document is still loading, and at once otherwise - and a deferred
script runs when it is no longer loading, so Bootstrap defines them as it runs. jQuery UI
has a `tooltip` and a `button` widget of its own, and whichever of the two runs last owns
`$.fn.tooltip` and `$.fn.button`. Before this change Bootstrap was deferred and jQuery UI
plain, so Bootstrap ran last whatever the order of the tags; with both deferred it has to
come later in the document. Checked the other way round too: with jQuery UI's tag after
Bootstrap's, `$.fn.tooltip` is jQuery UI's. It has to be Bootstrap's, because
`copy_to_clipboard` asks `bootstrap.Tooltip.getInstance(btn)` for the tooltip that
`.tooltip()` built.

## The rule for templates

- Code in an inline `<script>` that runs at once must not touch `$`/`jQuery` or anything a
  deferred script defines. Put it in `whenReady(function () {...})`. Function
  declarations stay at the top level of the block, where `onclick=` can reach them.
- `whenReady` is for scripts that run while the page parses. Called after
  DOMContentLoaded it would wait for an event that has already fired.
- A function an `onclick=` calls can run before jQuery has, from the first paint on.
  Where jQuery is incidental in it - reading a checkbox, finding a row - read the page
  without it, as `fetchState` does; and do not leave state behind for a handler that is
  only bound from `whenReady` (the Analyze flag, under "What it costs"). A control a
  user reaches before anything else - a box the page opens with focus in - gets its
  handler while the page parses, bound to `document`, like the pickers' search forms.
- A page's own library goes in `{% block style %}`, with `defer`. A plain `<script src>`
  anywhere must not need jQuery.
- `tests/testParseTimeScripts.py` fails on a template, or a rendered page, whose inline
  script reaches jQuery - or `bootstrap`, `Sortable`, `Autocomplete`, `csrfToken`,
  `postTo`, `mcritwebPageLoading`, the other names deferred scripts define - while it
  parses; on a script in `<head>` that is not deferred; and on a plain script that names
  jQuery. `tests/testDeferredJqueryBrowser.py` loads every page the accessibility
  checks render in Chromium and fails on any page error or console error.

## What it costs

Between the first paint and DOMContentLoaded the page is visible and jQuery has not run
yet. Before, jQuery held the parser in `<head>` until it had been fetched and run, so that
time was spent on a blank page instead. What a click in that window does:

- Handlers bound from `whenReady` - row clicks, row selection on the pickers, the edit
  modals' fields - are not there yet, as ready handlers never were.
- The pickers' search forms are not among them. The search box is the one each picker
  opens with focus in, so Enter is the first thing a user can do there; bound from
  `whenReady`, a search in that window was a plain form submission, which kept the query
  and dropped the rest of the URL - the selected samples, rematch, the MinHash setting.
  The five handlers (compare, compare_versus's two, cross_compare, unique_blocks) are
  bound to `document` while the page parses instead, filtered on the form's id since the
  form is not there yet, and read the page without jQuery. After load they do what they
  did; they are bound there only, not from `whenReady` as well.
- A function an `onclick=` calls throws if it reaches `$`. Where `$` was incidental, it
  no longer does: `fetchState` on compare, compare_versus and cross_compare reads its
  checkboxes with `document.getElementById(...).checked`, so the pagination links, sort
  headers, "Add" and "Compare" keep the selection and the settings; `deleteSample` on
  cross_compare and unique_blocks finds its row with `closest("tr")`.
- The upload forms' radio buttons slide their extra fields with jQuery, so before it has
  run they leave the fields alone, and the ready handler then puts the fields in the
  state the checked radios ask for. It used to check Dumped only, and SMDA shows the
  same fields, so it checks both now.
- Bootstrap, `page_loading.js` and `autocomplete.js` run after jQuery, so the menus, tabs,
  accordions and dialogs, the loading spinner and the type-ahead are not there yet
  either: a click on them does nothing and is not replayed once they are. The navbar's
  Analyze and Data menus linked a signed-in user to `""`, so a click on them reloaded
  the page; they link to `#` now, like Explore and the user menu.
- A clipboard button still copies - `navigator.clipboard.writeText` comes first - and
  only the "Copied!" tooltip after it throws, as it did on the old page before its ready
  handler had built the tooltips.
- On the sample listings, the Analyze button's `onclick=` sets a flag for the row's click
  handler to take back. Clicked before that handler existed, the flag stayed set and the
  next row click after load opened a menu instead of the sample; the ready handler starts
  from a cleared flag now. single_sample.html has the same flag, but there the handler is
  bound to the Analyze button itself, whose own `onclick=` sets it before every click
  reaches the handler, so a stale value changes nothing.

`tests/testDeferredJqueryBrowser.py` holds jquery.js back and checks the pagination, the
remove buttons, the searches, the Analyze flag, the SMDA fields and the navbar menus in
that window; the searches after load too, and that one submit makes one navigation.

`$.fn.dropzone` is attached on some pages and not on others now. On the upload pages
`dropzone.js` is a plain script, runs before jQuery and attaches nothing; on every other
page the drop overlay's loader inserts it after DOMContentLoaded, finds jQuery and
attaches the plugin as before. Nothing calls it.

## Measured

Lighthouse 12.8.2, mobile (simulated slow 4G, 4x CPU slowdown), against side instances of
#245's head (8cba370) and of this change, on the live testbed's mcrit, logged in, with
nothing else running. The two sides were run in turn, page by page; each figure is the
median of the runs, and a page's runs spread by up to six points of Performance.

| page | runs | Performance | FCP | LCP | TBT |
|---|---|---|---|---|---|
| `/` | 3 | 96 -> 96 | 1.66 -> 1.39 s | 2.73 -> 2.74 s | 3 -> 5 ms |
| `/explore/samples` | 3 | 91 -> 92 | 2.09 -> 1.84 s | 3.18 -> 3.18 s | 9 -> 15 ms |
| `/explore/samples/1` | 3 | 92 -> 93 | 1.83 -> 1.85 s | 3.09 -> 3.03 s | 0 -> 5 ms |
| `/explore/functions/83` | 7 | 80 -> 79 | 1.94 -> 1.87 s | 4.76 -> 4.74 s | 161 -> 196 ms |
| `/data/result/<1 vs N>` | 7 | 86 -> 85 | 2.59 -> 2.76 s | 3.63 -> 3.78 s | 21 -> 7 ms |

`jquery.js` was in Lighthouse's render-blocking list of every page before and is in none
after, and the first paint of the index and the samples list comes a quarter of a second
earlier. The Performance score does not move beyond the spread of its runs: the work
jQuery did before the first paint now happens after it, where Lighthouse counts what holds
up the main thread, and on the function page that shows as blocking time. Desktop is
98-100 on these pages before and after. Accessibility, Best Practices and SEO do not
change (mobile 100/93/100, 96/93/100 on the result page).

What the mobile score still loses, on the pages above, is not a script:

- the five stylesheets in `<head>` (`bootstrap.min.css`, `fontawesome-subset.css`,
  `style.css`, `navbar.css`, `hint.css`; `cfg_style.css` on the CFG pages), which are
  render-blocking because they have to be: deferring them paints the page unstyled first;
- the HTML itself, sent uncompressed - deliberately, see ADR-0017 on BREACH - which
  Lighthouse counts as 23-213 KB of "text compression" and as document latency;
- there is no `<meta name="viewport">`, so a phone lays the page out 980 px wide (Best
  Practices 93, `font-size`, `viewport`), which #253 adds;
- the LCP element is the navbar logo on every page but the function page, and its time
  splits between the uncompressed document, fetching the logo and a render delay; on the
  function page it is a block of the code panel, drawn once the CFG has been laid out,
  and d3 and graphlib are the largest share of that page's long tasks.

## Consequences

- jQuery stays, per ADR-0015; it no longer blocks the first paint.
- ADR-0017's "calls jQuery, which stays a plain script" no longer holds; the rest of it
  does.
- What is left between Lighthouse and 100 is in the table above, and none of it is a
  script.
