# Pages are thinned before they are gzipped

---
status: accepted — measured 2026-10-26; revisits the whitespace half of 0004
---

[ADR-0004](0004-minifying-html-is-not-worth-a-dependency.md) measured what collapsing a
page's white space would save and found 1.2 KB gzipped on the heaviest page, under 0.24% of
what a page costs to load, and declined a dependency, `{%- -%}` in forty templates, and a
minifier. Its arithmetic was the whole load against the whole saving. The arithmetic that
matters to a slow phone is the first round trip: TCP sends about 14.6 KB before it waits
for an answer, and a result page that fits in it is painted a round trip sooner than one
that does not. A result page is 14 to 18 KB gzipped, so 1.2 to 1.6 KB of white space is
the difference between the two.

## What is done

`mcritweb/thin_pages.py` runs on every response that is a page (it starts with a doctype)
before `compression.py` gzips it, in one pass of one regular expression, about 9 ms for the
largest page:

- Between tags, a run of white space that holds a line break becomes the line break. A
  browser shows any run of white space as one space, so nothing moves; a run without a line
  break, which may be what separates two words, is left alone.
- `pre`, `textarea`, `style`, comments and everything inside a tag are left as they are.
  An attribute may hold a line break of its own: hint.css prints the one in a score's
  `data-hint`, as it is.
- In an inline script, the indentation, the blank lines and the lines that are only a `//`
  comment. A script with a line break inside a template literal, a block comment, or a
  line continued by a backslash is left alone, as is one that is data (`type=` something
  that is not JavaScript).

Measured on the offline corpus, gzip level 6, the level `compression.py` uses:

| page | raw HTML | thinned | gzipped | thinned |
|---|---|---|---|---|
| `/` | 20,642 | 17,762 | 6,162 | 5,700 |
| `/data/jobs` | 30,115 | 24,827 | 8,000 | 7,232 |
| `/explore/samples` | 64,554 | 52,507 | 10,951 | 9,927 |
| result of a sample's matches | 239,888 | 173,016 | 14,335 | 12,740 |
| the same, one family | 223,464 | 159,763 | 13,549 | 12,086 |
| cross compare result | 51,504 | 43,670 | 11,675 | 10,050 |

## Why a pass over the page and not the templates

The forty templates stay indented for whoever reads them, and the `{%- -%}` ADR-0004 turned
down would still be forty templates and a rule for the next one. The page is also the only
place that knows where a macro's output lands: the same macro may be written into a `pre`
or an attribute, and only a pass over the rendered page can tell. The user manual and the
job error are `pre`; the page does not need a list of them, since the pass reads the tags.

The pass does not touch what users type. A value in a text node is escaped, and a run of
white space in one is shown as one space already; a `textarea` and a `pre` are kept whole.

## What it asks of the tests

Two tests keep it honest, and both are about the page being the same page.
`tests/testThinPages.py` renders every page of the accessibility checks with the pass on
and off and compares the two as a browser builds them: the same tags, attributes and text,
white space read as a browser reads it. `tests/testThinPagesBrowser.py` loads a dozen pages
both ways in Chromium and compares the box of every element and the text on show. Set
`MCRITWEB_THIN_PAGES = False` in `instance/config.py` to turn the pass off, to read a page's
source with its indentation.
