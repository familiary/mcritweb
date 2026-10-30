# The page scripts load after the first paint

---
status: accepted — measured 2026-10-21; changes how 0018 loads jQuery, and keeps its
whenReady rule
---

[ADR-0018](0018-jquery-is-deferred-too.md) deferred jQuery, so that no script in
`<head>` holds back the first paint. A deferred script is still fetched at once,
alongside the stylesheets: jQuery, `post_action.js`, `page_loading.js`, the type-ahead,
a page's own jQuery plugins and Bootstrap's bundle compete for the first round trips with
the CSS the page is painted with, and on a slow connection the first paint waits for
bytes it does not use.

## The queue

base.html's first script defines `later(src)`, which queues a script, and the queue is
loaded once the browser reports the page's first contentful paint, after
DOMContentLoaded. The scripts are added with `async = false`, which runs them in the
order they were queued however they arrive: jQuery first, the page's own plugins (jQuery
UI on the cross compare, SortableJS on the settings page) from `{% block style %}`, and
Bootstrap last, so that it still owns `$.fn.tooltip` (ADR-0015). The paint entry is what
starts it, not a timer set from `requestAnimationFrame`: a frame is presented some time
after the main thread has produced it, and such a timer fetched jQuery before the
first-contentful-paint entry was recorded. A browser without paint timing gets the timer
all the same; a page that is not visible starts the queue at once; and if nothing has
started it three seconds after DOMContentLoaded, that does. It starts once.

## What it changes for templates

- `whenReady(fn)` keeps its rule from 0018 and runs fn once every queued script has run.
  Called after that, it runs fn at once.
- A DOMContentLoaded, load or pageshow listener, or `window.onload`, can run before the
  queue has, so it cannot call `$`, Bootstrap or anything else queued.
  `js/ac_family_names.html`, the one listener that did, waits in whenReady.
  `tests/testParseTimeScripts.py` counts such a listener as code that runs at once.
- `csrfToken()` moves from `post_action.js` to base.html's first script: the CFG viewers
  call it from `window.onload`.
- A click on something only Bootstrap or `post_action.js` acts on - a `data-bs-toggle`,
  `data-bs-dismiss` or `data-post` element - before they are in is held, and made again
  once they are, in order. A tooltip toggle is not held: a tooltip is shown on hover and
  focus, and a press on an element that has one goes on to the element's own handler.
- A new library is queued with `later()`, never a `<script defer>` tag:
  `tests/testPageAssets.py` fails on one.

## What it bought

Measured on the testbed on 2026-10-21, mobile, two runs a side:
- Lighthouse 12.8.2: on #304's head the largest paint comes 0.14 to 0.72 s earlier on eight
  pages, and first paint moves by no more than 0.02 s on seven of them (0.15 s later on the
  jobs page). On a build that also has the viewport tag, gzipped pages, the cut icon fonts
  and Bootstrap's stylesheet cut to the rules in use, it comes 0.27 to 0.74 s earlier, and
  six of the eight pages score 100 in both runs, and one more in one run; before, none did
  in both.
- A real Chromium, throttled to 1.6 Mbps, 150 ms round trips and a CPU four times slower,
  against #304, three runs a side: first paint is 0 to 120 ms earlier, and the page is
  ready - every whenReady handler run - 75 to 560 ms later on most pages. The scripts start
  after the paint instead of with the stylesheets, so they finish later.

Lighthouse simulates the page from what it observed, and counts every request that started
before the first paint against that paint. A real browser on a slow link was already
painting before the deferred scripts had arrived. The change is for the first; on the second
it costs readiness, which the replay of early presses makes up for where a press would
otherwise have been lost.

## Consequences

- The first paint no longer waits for, or shares the first round trips with, script the
  page does not need to be painted.
- Bootstrap's menus, modals and tooltips, the type-ahead and the `data-post` buttons work
  once the queue has run, a little later than with `defer`. A click on a Bootstrap control
  or a `data-post` button before then is kept and made again, so it is late rather than
  lost.
- `load` no longer means the page's scripts have run. A test or a script that needs them
  waits in `whenReady`.
- What a whenReady handler does now runs after the first paint, where Lighthouse counts it
  as blocking time. Keep the handlers light: the upload form's ready handler sets its
  fields' state instead of sliding them, which on most pages animated a form in the closed
  drop overlay.
- A new library is queued with `later()`, after jQuery and before Bootstrap's call.
