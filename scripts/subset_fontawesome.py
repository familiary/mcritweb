#!/usr/bin/env python3
"""Cut mcritweb/static/css/all.css down to the icons the templates actually use.

Font Awesome's all.css is 140 KB of render-blocking CSS, of which mcritweb uses a few
dozen icons. This keeps every rule that is not an icon glyph, plus the glyph rules of
the icons named in the templates and scripts, and writes the result to
mcritweb/static/css/fontawesome-subset.css - the file base.html loads. Run it after
adding an icon to a template; tests/testPageAssets.py fails when the subset is stale.

    python scripts/subset_fontawesome.py

The full all.css stays in the repository as the source, unreferenced by any page.

The webfonts are cut the same way. The solid face is 150 KB and base.html preloads it on
every signed-in page, though the templates draw 35 of its 1,389 glyphs. The script writes
those glyphs of the solid and regular faces, and the text the faces cover
(TEXT_CODEPOINTS), to static/webfonts/subset/, as woff2 and as the TrueType fallback, and
points the subset's @font-face rules at them. The full fonts stay beside them as the
source. Cutting a font needs fontTools with brotli, which nothing else here does, so it is
not in requirements.txt:

    pip install fonttools brotli
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
#: a glyph rule, `.fa-house::before`; all.css writes its brand icons' with one colon,
#: `.fa-github:before`, and those are glyph rules too
GLYPH_RULE = re.compile(r"\.fa-([a-z0-9-]+)::?before")
#: the codepoint a glyph rule draws, e.g. `content: "\f002"`
GLYPH_CODEPOINT = re.compile(r'content:\s*"\\([0-9a-f]+)"')
WEBFONTS = ROOT / "static" / "webfonts"
SUBSET_WEBFONTS = WEBFONTS / "subset"
#: the faces the templates draw with: fa-solid and fa-regular. The brands face is never
#: drawn, so it is left whole, as is the v4 compatibility face
FACES = ("fa-solid-900", "fa-regular-400")
#: text the faces also map, kept though no template draws it. Chromium positions an icon by
#: metrics it takes from the text a face covers: cut to the icons' private-use codepoints
#: alone, the pager's arrows sat a fraction of a pixel lower. With Basic Latin and the four
#: angle quotation marks, which map to the arrows' own glyphs, every page renders as before
TEXT_CODEPOINTS = frozenset(range(0x20, 0x7F)) | {0xAB, 0xBB, 0x2039, 0x203A}
#: the formats each face is written in. Every name record stays, the copyright among them
FONT_FORMATS = (("woff2", ".woff2"), (None, ".ttf"))
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
    return point_at_subset_fonts("\n".join(kept) + tail)


def point_at_subset_fonts(css):
    """Every @font-face of the two cut faces names the cut file instead of the whole one."""
    for face in FACES:
        css = css.replace(f'url("../webfonts/{face}.', f'url("../webfonts/subset/{face}.')
    return css


def used_codepoints(css):
    """The codepoints the glyph rules of a subset draw."""
    rules, _ = split_rules(css)
    return sorted({int(match.group(1), 16) for selector, body in rules if GLYPH_RULE.search(selector)
                   for match in [GLYPH_CODEPOINT.search(body)] if match})


def subset_fonts(codepoints):
    """Write FACES cut to `codepoints` and TEXT_CODEPOINTS to SUBSET_WEBFONTS, from their
    TrueType files. A codepoint a face has no glyph for is skipped."""
    from fontTools import subset as fontsubset

    SUBSET_WEBFONTS.mkdir(exist_ok=True)
    for face in FACES:
        for flavor, suffix in FONT_FORMATS:
            options = fontsubset.Options()
            options.flavor = flavor
            options.layout_features = ["*"]
            options.name_IDs = ["*"]
            options.name_languages = ["*"]
            options.notdef_outline = True
            font = fontsubset.load_font(str(WEBFONTS / f"{face}.ttf"), options)
            subsetter = fontsubset.Subsetter(options)
            subsetter.populate(unicodes=sorted(set(codepoints) | TEXT_CODEPOINTS))
            subsetter.subset(font)
            fontsubset.save_font(font, str(SUBSET_WEBFONTS / f"{face}{suffix}"), options)


def main():
    # checked before anything is written, so the CSS never gets ahead of the fonts
    try:
        import brotli  # noqa: F401 - fontTools writes woff2 with it
        import fontTools  # noqa: F401
    except ImportError:
        sys.exit("cutting the webfonts needs fontTools and brotli: pip install fonttools brotli")
    icons = used_icons()
    css = subset(SOURCE.read_text(), icons)
    TARGET.write_text(css)
    print(f"{len(icons)} icons, {SOURCE.stat().st_size} -> {TARGET.stat().st_size} bytes")
    codepoints = used_codepoints(css)
    subset_fonts(codepoints)
    print(f"{len(codepoints)} codepoints drawn, kept with the text each face covers")
    for face in FACES:
        for _, suffix in FONT_FORMATS:
            whole, cut = WEBFONTS / f"{face}{suffix}", SUBSET_WEBFONTS / f"{face}{suffix}"
            print(f"{cut.relative_to(ROOT)}: {whole.stat().st_size} -> {cut.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
