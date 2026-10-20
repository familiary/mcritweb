#!/usr/bin/env python3
"""Cut mcritweb/static/css/all.css down to the icons the templates actually use.

Font Awesome's all.css is 140 KB of render-blocking CSS, of which mcritweb uses a few
dozen icons. This keeps every rule that is not an icon glyph, plus the glyph rules of
the icons named in the templates and scripts, and writes the result to
mcritweb/static/css/fontawesome-subset.css - the file base.html loads. Run it after
adding an icon to a template; tests/testPageAssets.py fails when the subset is stale.

    python scripts/subset_fontawesome.py

The full all.css stays in the repository as the source, unreferenced by any page.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "mcritweb"
SOURCE = ROOT / "static" / "css" / "all.css"
TARGET = ROOT / "static" / "css" / "fontawesome-subset.css"
#: where icon classes appear: every template, and the scripts mcritweb wrote itself
#: (the vendored CFG viewer under trace_CFG carries no Font Awesome markup)
SCANNED = [ROOT / "templates", ROOT / "static" / "function_compare.js", ROOT / "static" / "post_action.js", ROOT / "static" / "autocomplete.js"]
ICON_CLASS = re.compile(r"\bfa-([a-z0-9][a-z0-9-]*)\b")
GLYPH_RULE = re.compile(r"\.fa-([a-z0-9-]+)::before")
#: fa-* classes that are sizing, style or layout utilities rather than glyphs; their
#: rules are kept by the "not a glyph rule" test below, they are listed here so the
#: staleness test does not expect a glyph for them
NOT_GLYPHS = {"solid", "regular", "brands", "light", "thin", "duotone", "sharp", "classic", "xs", "sm", "lg", "xl", "2xl", "1x", "2x", "3x", "4x", "5x", "6x", "7x", "8x", "9x", "10x", "fw", "ul", "li", "border", "pull-left", "pull-right", "spin", "pulse", "beat", "fade", "beat-fade", "bounce", "flip", "shake", "spin-pulse", "spin-reverse", "rotate-90", "rotate-180", "rotate-270", "flip-horizontal", "flip-vertical", "flip-both", "rotate-by", "stack", "stack-1x", "stack-2x", "inverse", "sr-only", "sr-only-focusable",
              # the webfont files themselves: base.html preloads fa-solid-900.woff2 and
              # explains why in a comment (issue #62) - a file name, not a glyph
              "solid-900", "regular-400", "brands-400"}


def used_icons(paths=SCANNED):
    names = set()
    for path in paths:
        files = path.rglob("*") if path.is_dir() else [path]
        for file in files:
            if file.is_file():
                names.update(ICON_CLASS.findall(file.read_text(errors="replace")))
    return {name for name in names if name not in NOT_GLYPHS}


def split_rules(css):
    """(prefix, selector, body) triples in source order; the prefix holds the comments
    and whitespace before a rule so the licence header survives. all.css nests nothing
    but @font-face and @keyframes blocks, whose bodies hold no braces of their own."""
    rules = []
    position = 0
    for match in re.finditer(r"([^{}]*?)\{([^{}]*)\}", css):
        rules.append((match.group(1), match.group(2)))
        position = match.end()
    return rules, css[position:]


def subset(css, icons):
    rules, tail = split_rules(css)
    kept = []
    for selector, body in rules:
        glyphs = GLYPH_RULE.findall(selector)
        if glyphs and not any(glyph in icons for glyph in glyphs):
            continue
        kept.append(f"{selector.rstrip()} {{{body}}}")
    return "\n".join(kept) + tail


def main():
    icons = used_icons()
    TARGET.write_text(subset(SOURCE.read_text(), icons))
    print(f"{len(icons)} icons, {SOURCE.stat().st_size} -> {TARGET.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
