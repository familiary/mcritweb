"""What the CFG viewer's tests need to know about its colours (#70): the light values the
block matching colours had before they were names, and a reader for the custom properties
of the two stylesheets. Shared by testFunctionPages.py and testFunctionVsBrowser.py."""

import os
import re

STATIC = os.path.join(os.path.dirname(__file__), "..", "mcritweb", "static")

#: What the block matching colours were before they became names. The light values in
#: static/style.css have to stay these, or the light pages change.
LIGHT_CFG_COLORS = {
    "--cfg-unmatched": "#FFA0A0",
    "--cfg-escaped-match": "#00ff00",
    "--cfg-adhoc-picblock": "#C0F4FF",
    "--cfg-full-picblock": "#00DDFF",
    "--cfg-only-in-b": "#D0B0FF",
    "--cfg-levenshtein-0": "#40ff40",
    "--cfg-levenshtein-1": "#c0ff80",
    "--cfg-levenshtein-2": "#FFFF40",
    "--cfg-levenshtein-3": "#FFCC40",
    "--cfg-edge-both": "#000000",
    "--cfg-edge-only-a": "#d62728",
    "--cfg-edge-only-b": "#7b2cbf",
}

#: the block fills among those; the rest are edges
CFG_FILLS = [name for name in LIGHT_CFG_COLORS if "edge" not in name]
CFG_EDGES = [name for name in LIGHT_CFG_COLORS if "edge" in name]


def custom_properties(stylesheet):
    """The custom properties of a stylesheet's first :root block, name -> value."""
    with open(os.path.join(STATIC, stylesheet)) as f:
        css = f.read()
    start = css.index(":root {")
    block = css[start:css.index("\n}", start)]
    return dict(re.findall(r"^\s*(--[\w-]+):\s*(.+?);\s*$", block, re.M))


def dark_value(name):
    """A dark custom property with its var() references followed."""
    dark = custom_properties("theme-dark.css")
    value = dark[name]
    while value.startswith("var("):
        value = dark[value[4:-1]]
    return value


def hex_to_rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def luminance(rgb):
    red, green, blue = [c / 255 / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first, second):
    """WCAG contrast ratio of two (r, g, b) triples."""
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def parse_rgb(css):
    """(r, g, b) out of a computed `rgb(...)` or `rgba(...)` value."""
    return tuple(float(number) for number in re.findall(r"[\d.]+", css)[:3])


def blend(foreground, opacity, background):
    """What `foreground` at `opacity` shows as over `background`, per channel."""
    return tuple(opacity * f + (1 - opacity) * b for f, b in zip(foreground, background))


def script_palette(script, pattern):
    """The hex colours of the first array in a trace_CFG script that a line matching `pattern` opens
    (a line of its own, so a commented-out earlier palette is not taken for it)."""
    with open(os.path.join(STATIC, "trace_CFG", script)) as f:
        source = f.read()
    start = re.search(pattern, source, re.M).end()
    return re.findall(r"#[0-9a-fA-F]{6}", source[start:source.index("]", start)])
