#!/usr/bin/env python3
"""Cut Bootstrap's stylesheet down to the rules that can match something mcritweb renders.

base.html loads Bootstrap's CSS on every page, render-blocking: 157 KB, 23 KB gzipped,
which is more than the first round trip of a fresh connection carries, for an app that
uses a few hundred of its classes. This drops a selector that needs a class appearing
nowhere in mcritweb - not in a template, a view, the manual, or any script, Bootstrap's
own included, since scripts add classes such as `show` or `collapsing` at run time - and
a rule once none of its selectors is left. A class built from a prefix and a value, as in
`bg-{{ colour }}` or `"btn-" + kind`, keeps every class with that prefix. The result goes to
mcritweb/static/css/bootstrap-subset.css, which base.html loads; the minified build stays
as the source. Run it after a template starts using a Bootstrap class it did not use
before; tests/testBootstrapSubset.py fails while the subset is stale.

    python scripts/subset_bootstrap.py
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "mcritweb"
SOURCE = PACKAGE / "static" / "bootstrap-5.0.2-dist" / "css" / "bootstrap.min.css"
TARGET = PACKAGE / "static" / "css" / "bootstrap-subset.css"
#: where a class can come from: what renders pages, what the help page renders, and every
#: script the pages load, vendored ones included
SCANNED = [(PACKAGE / "templates", "*.html"), (PACKAGE, "*.py"), (PACKAGE / "static", "*.js"),
           (ROOT / "docs" / "manual", "*.md")]
WORD = re.compile(r"[A-Za-z_][\w-]*")
#: a class name completed at run time: `bg-{{`, `bg-{%`, `"btn-" +`, `'btn-' +`, `alert-${`
PREFIX = re.compile(r"([A-Za-z][\w-]*-)(?:\{\{|\{%|[\"']\s*\+|\$\{)")
CLASS = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")
#: what a selector may hold that does not have to be in use: `:not(...)` matches elements
#: without the class, and an attribute selector's value is not a class
NOT_REQUIRED = re.compile(r":not\([^)]*\)|\[[^\]]*\]")


def used_names(scanned=SCANNED):
    """Every word in the sources, and the prefixes of the class names built at run time."""
    words, prefixes = set(), set()
    for directory, pattern in scanned:
        for path in sorted(directory.rglob(pattern)):
            text = path.read_text(errors="replace")
            words.update(WORD.findall(text))
            prefixes.update(PREFIX.findall(text))
    return words, prefixes


def string_end(css, i):
    """The index just past the string that starts at css[i]."""
    end = i + 1
    while css[end] != css[i]:
        end += 2 if css[end] == "\\" else 1
    return end + 1


def parse(css, start=0):
    """[(prelude, body, children)] up to the brace closing this level.

    A style rule has a body; @media and @supports have children; any other block at-rule
    (@keyframes, @font-face) keeps its whole body; a statement at-rule has neither.
    """
    items, i, begin = [], start, start
    while i < len(css):
        if css.startswith("/*", i):
            i = css.index("*/", i) + 2
            continue
        char = css[i]
        if char in "\"'":
            i = string_end(css, i)
            continue
        if char == ";" and css[begin:i].strip().startswith("@"):
            items.append((css[begin:i].strip(), None, None))
            i += 1
            begin = i
            continue
        if char == "{":
            prelude = css[begin:i].strip()
            if prelude.startswith(("@media", "@supports")):
                children, i = parse(css, i + 1)
                items.append((prelude, None, children))
            else:
                depth, end = 1, i + 1
                while depth:
                    if css[end] in "\"'":
                        end = string_end(css, end)
                        continue
                    depth += {"{": 1, "}": -1}.get(css[end], 0)
                    end += 1
                items.append((prelude, css[i + 1:end - 1], None))
                i = end - 1
            i += 1
            begin = i
            continue
        if char == "}":
            return items, i
        i += 1
    return items, i


def selectors(prelude):
    """A selector list split at its own commas, not at those inside `:not(.a,.b)`."""
    parts, depth, begin = [], 0, 0
    for i, char in enumerate(prelude):
        depth += {"(": 1, ")": -1, "[": 1, "]": -1}.get(char, 0)
        if char == "," and depth == 0:
            parts.append(prelude[begin:i])
            begin = i + 1
    return parts + [prelude[begin:]]


def required_classes(selector):
    return CLASS.findall(NOT_REQUIRED.sub("", selector))


def can_match(selector, words, prefixes):
    return all(name in words or name.startswith(tuple(prefixes)) for name in required_classes(selector))


def cut(items, words, prefixes):
    """The rules and blocks of `items` that can match, one per line."""
    lines = []
    for prelude, body, children in items:
        if children is not None:
            inner = cut(children, words, prefixes)
            if inner:
                lines.append(prelude + "{" + "".join(inner) + "}")
        elif body is None:
            lines.append(prelude + ";")
        elif prelude.startswith("@"):
            lines.append(prelude + "{" + body + "}")
        else:
            kept = [selector for selector in selectors(prelude) if can_match(selector, words, prefixes)]
            if kept:
                lines.append(",".join(kept) + "{" + body + "}")
    return lines


def subset(css, words, prefixes):
    """The cut stylesheet. Comments go before parsing, so that none ends up in a selector
    and reads as a class there (the licence names getbootstrap.com); the licence comes
    back after the @charset, which has to stay first."""
    licence = re.search(r"/\*!.*?\*/", css, re.S)
    items, _ = parse(re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    lines = cut(items, words, prefixes)
    if licence:
        lines.insert(1 if lines and lines[0].startswith("@charset") else 0, licence.group(0))
    return "\n".join(lines) + "\n"


def main():
    words, prefixes = used_names()
    css = subset(SOURCE.read_text(), words, prefixes)
    TARGET.write_text(css)
    print(f"{SOURCE.stat().st_size} -> {TARGET.stat().st_size} bytes, {len(prefixes)} class prefixes built at run time")
    return 0


if __name__ == "__main__":
    sys.exit(main())
