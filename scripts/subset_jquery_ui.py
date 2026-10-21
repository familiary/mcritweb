#!/usr/bin/env python3
"""Cut jQuery UI down to its sortable widget, the one part of it mcritweb uses.

The cross compare result page lets the reader drag its samples into an order with
`.sortable()` (templates/result_cross.html), and no page calls anything else of jQuery UI.
The vendored build holds every widget and effect, 529 KB, and running it was the longest
task of that page in Lighthouse's phone runs. This keeps the modules the sortable widget is
built from - the widget factory, the `:data` selector, `scrollParent()`, `$.ui.ie`, the mouse
widget and the sortable widget itself, the dependencies jQuery UI's own sources declare - in
the order and inside the wrapper of the full build, and writes them to
mcritweb/static/jquery-ui-sortable.js, which the page loads. The full build stays as the
source: run this after replacing it with another release; tests/testJqueryUiSubset.py fails
while the cut is stale.

    python scripts/subset_jquery_ui.py
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "mcritweb" / "static" / "jquery-ui.js"
TARGET = ROOT / "mcritweb" / "static" / "jquery-ui-sortable.js"
#: the modules kept, by the name in their licence header, and the variable the build assigns
#: each module's value to, in the statement that ends the module
MODULES = [("Widget", "widget"), (":data", "data"), ("Scroll Parent", "scrollParent"),
           ("Mouse", "widgetsMouse"), ("Sortable", "widgetsSortable")]
#: what the banner lists instead of the full build's modules
KEPT = "widget.js, data.js, scroll-parent.js, ie.js, widgets/mouse.js, widgets/sortable.js"
#: the one kept module without a licence header: the build puts it, after this comment,
#: just above the mouse widget
IE = re.compile(r"^// This file is deprecated\nvar ie = \$\.ui\.ie = .*\n", re.M)
HEADER = re.compile(r"^/\*!\n \* jQuery (?:UI )?(.+?) v?\d+\.\d+\.\d+\n", re.M)
#: the wrapper, `$.ui` and its version: everything above the first module
PROLOGUE = re.compile(r'^var version = \$\.ui\.version = "[^"]+";\n', re.M)
INCLUDES = re.compile(r"^\* Includes: .*$", re.M)
#: a line that starts in the first column. The build indents everything inside a statement,
#: so the first such line after a statement's first is its last, and ends in a semicolon
TOP_LEVEL = re.compile(r"^\S.*$", re.M)
TRAILING_SPACE = re.compile(r"[ \t]+$", re.M)


def statement_end(text, start):
    """The index just past the statement that starts at text[start]."""
    first = TOP_LEVEL.match(text, start)
    last = first if first.group().endswith(";") else TOP_LEVEL.search(text, first.end())
    if not last or not last.group().endswith(";"):
        raise ValueError(f"no end to the statement {first.group()!r}")
    return last.end() + 1


def module(text, name, variable):
    """The module from its licence header to the end of the statement assigning its value."""
    headers = [match for match in HEADER.finditer(text) if match.group(1) == name]
    if len(headers) != 1:
        raise ValueError(f"{len(headers)} modules named {name!r}")
    assignment = re.compile(rf"^var {re.escape(variable)} = ", re.M).search(text, headers[0].end())
    following = HEADER.search(text, headers[0].end())
    if not assignment or (following and assignment.start() > following.start()):
        raise ValueError(f"module {name!r} does not assign {variable}")
    return text[headers[0].start():statement_end(text, assignment.start())]


def cut(text):
    """The sortable widget and what it needs, out of a full jQuery UI 1.13 build."""
    prologue = PROLOGUE.search(text)
    ie = IE.findall(text)
    if not prologue or len(ie) != 1:
        raise ValueError("not a full jQuery UI 1.13 build")
    banner = f"* Includes: {KEPT}\n* (cut from the full build by scripts/subset_jquery_ui.py)"
    head, count = INCLUDES.subn(banner, text[:prologue.end()])
    if count != 1:
        raise ValueError("the banner lists no modules")
    parts = [head]
    for name, variable in MODULES:
        if name == "Mouse":
            parts.append(ie[0])
        parts.append(module(text, name, variable))
    parts.append("\n} );\n")
    # the wrapper has a line of nothing but a tab
    return TRAILING_SPACE.sub("", "\n".join(parts))


def main():
    TARGET.write_text(cut(SOURCE.read_text()))
    print(f"{TARGET.relative_to(ROOT)}: {TARGET.stat().st_size} bytes, from {SOURCE.stat().st_size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
