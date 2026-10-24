# Bootstrap's stylesheet is cut to the rules in use

---
status: accepted — measured 2026-10-21; adds to the CSS section of 0017, which swapped the
unminified build for the minified one
---

[ADR-0017](0017-the-rest-of-63-done-in-the-application.md) swapped the unminified
Bootstrap build for the minified one. That is still 156,958 bytes, 22,973 gzipped, and
base.html loads it on every page as a render-blocking stylesheet, so the first paint
waits for all of it. A fresh connection's first round trip carries about 14 KB. mcritweb
uses a small part of Bootstrap: 551 of the build's 2,290 style rules keep a selector that
can match something it renders.

## The rule

`scripts/subset_bootstrap.py` drops a selector that needs a class appearing nowhere in
mcritweb, and a rule once none of its selectors is left. It writes the rest to
`static/css/bootstrap-subset.css`, which base.html loads. The minified build stays as the
source, as `all.css` does for the icon subset.

- **Where a class can come from.** Every word in the templates, the Python package, the
  manual and every script under `static/`, the vendored ones included. Bootstrap's own
  JavaScript adds `show`, `collapsing` and `modal-open` at run time, and those words are
  in `bootstrap.bundle.min.js`.
- **Names built at run time.** A class written as a prefix and a value, as in
  `bg-{{ colour }}`, `"btn-" + kind` or `` `alert-${level}` ``, keeps every class with that
  prefix. That is also how the tooltip's placements survive: the bundle builds
  `bs-tooltip-top` and the rest from `"bs-tooltip-"+`.
- **What a selector does not need.** A class inside `:not(...)` matches elements without
  it, and an attribute selector's value is not a class, so neither has to be in use.
- **At-rules.** `@media` and `@supports` are cut inside and dropped when nothing is left.
  Any other at-rule, `@keyframes` for one, is kept whole.

The cut is 51,972 bytes, 9,846 gzipped at the application's level. Bootstrap stays at
5.0.2, and nothing is restyled. In Chromium the computed style of every element and of its
`::before` and `::after` is the same with the whole build and with the cut. That was
checked on 22 pages, at 1440 and 390 px wide. Each page was read at rest, with the navbar's
menus open, and with up to three of its modals open, and again with its tabs, collapses,
dropdowns and tooltips open.

## Consequences

- A template that starts using a Bootstrap class no file used before needs
  `scripts/subset_bootstrap.py` run again. `tests/testBootstrapSubset.py` fails until it is.
- A class name made up entirely at run time, with no literal prefix anywhere in the
  sources, is not seen. Write the prefix or the whole name out where the class is built.
- Upgrading Bootstrap means vendoring the new build and running the script on it.
