"""Reading the two palettes (#70): a CSS reader, a colour reader and the WCAG arithmetic.

Shared by testTheme.py and testThemeBrowser.py, and deliberately not named `test*.py` so
pytest does not collect it. Nothing here knows what a good palette is; the tests do.

The CSS reader is a small tokenizer rather than a regex over lines, because the two
stylesheets hold things a line-based reader gets wrong: declarations that share a line with
a selector, `!important` after a space, a `;` inside `url(...)`, a `{` inside a string, and
at-rules whose blocks hold rules. It reads what the project writes; it is not a CSS engine.
"""

import collections
import pathlib
import re

from PIL import ImageColor

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "mcritweb" / "static"
TEMPLATES = ROOT / "mcritweb" / "templates"
PACKAGE = ROOT / "mcritweb"

#: The CSS Color Module's named colours (148 of them, `rebeccapurple` and both spellings of
#: grey included), name -> (r, g, b). Pillow carries the table, and Pillow is a dependency of
#: the project; testTheme.py pins its size and a few of its names so that a different table
#: cannot thin the ratchet out unnoticed. `transparent` and `currentcolor` are not colours in
#: this sense, they are keywords, and are left out.
NAMED_COLOURS = {
    name: ImageColor.getrgb(value)[:3]
    for name, value in ImageColor.colormap.items()
}


def read_static(name):
    return (STATIC / name).read_text(encoding="utf-8")


# --- CSS --------------------------------------------------------------------------------

Rule = collections.namedtuple("Rule", "selector declarations context")
Declaration = collections.namedtuple("Declaration", "name value important")


def strip_comments(css):
    """The stylesheet without its /* ... */ comments, newlines kept so line numbers hold.
    A comment opener inside a string is not a comment."""
    out = []
    i, n = 0, len(css)
    while i < n:
        char = css[i]
        if char in "\"'":
            j = i + 1
            while j < n and css[j] != char:
                j += 2 if css[j] == "\\" else 1
            out.append(css[i:j + 1])
            i = j + 1
        elif css.startswith("/*", i):
            end = css.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(re.sub(r"[^\n]", " ", css[i:end]))
            i = end
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _split_declaration(text):
    name, colon, value = text.partition(":")
    if not colon:
        return None
    value = value.strip()
    important = False
    match = re.search(r"!\s*important\s*$", value, re.I)
    if match:
        important = True
        value = value[:match.start()].strip()
    return Declaration(name.strip(), value, important)


def _read_block(css, i, selector, context, rules):
    """Parse the contents of a block that opens at css[i] (just after its `{`) and ends at
    the matching `}`; returns the index after that brace. The block's own declarations go
    into the Rule appended for `selector`; nested rules are appended after it."""
    declarations = []
    rule = Rule(selector, declarations, context)
    rules.append(rule)
    n = len(css)
    start = i
    depth = 0
    quote = None
    while i < n:
        char = css[i]
        if quote:
            if char == "\\":
                i += 1
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and char == ";":
            declaration = _split_declaration(css[start:i])
            if declaration:
                declarations.append(declaration)
            start = i + 1
        elif depth == 0 and char == "{":
            prelude = css[start:i].strip()
            i = _read_block(css, i + 1, prelude, context + ((selector,) if selector.startswith("@") else ()), rules)
            start = i
            continue
        elif depth == 0 and char == "}":
            declaration = _split_declaration(css[start:i])
            if declaration:
                declarations.append(declaration)
            return i + 1
        i += 1
    return i


def parse_css(css):
    """Every rule of a stylesheet, nested ones (inside @media and the like) flattened:
    [Rule(selector, [Declaration(name, value, important)], context)]. An at-rule that
    holds rules comes out as a Rule of its own with no declarations, and its rules follow
    it with the at-rule in `context`."""
    css = strip_comments(css)
    rules = []
    # the top level is a block without braces
    holder = []
    _read_block(css + "}", 0, "", (), holder)
    # the holder's first element is the top level itself, whose declarations are @import
    # lines and stray text: not rules
    rules.extend(holder[1:])
    return rules


def selectors_of(rule):
    """The comma-separated selectors of a rule, split outside brackets and parentheses."""
    parts, depth, current = [], 0, []
    for char in rule.selector:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [part for part in parts if part]


def custom_properties_of(rule):
    """name -> value of the custom properties a rule declares (the last one wins, as in CSS)."""
    return {d.name: d.value for d in rule.declarations if d.name.startswith("--")}


def root_rule(rules):
    """The first rule whose selector is exactly `:root`."""
    for rule in rules:
        if rule.selector == ":root":
            return rule
    raise AssertionError("no :root rule")


def duplicated_names(rule):
    """The custom properties a rule declares more than once; the later one wins silently."""
    seen, duplicated = set(), []
    for declaration in rule.declarations:
        if declaration.name.startswith("--"):
            if declaration.name in seen:
                duplicated.append(declaration.name)
            seen.add(declaration.name)
    return duplicated


# --- colours ----------------------------------------------------------------------------

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)"


def _channel(token):
    token = token.strip()
    if token.endswith("%"):
        return float(token[:-1]) * 255 / 100
    return float(token)


def _alpha(token):
    token = token.strip()
    if token.endswith("%"):
        return float(token[:-1]) / 100
    return float(token)


def parse_colour(text, lookup=None, _depth=0):
    """(r, g, b, a) with r, g, b in 0..255 and a in 0..1 out of a colour written as a hex,
    rgb()/rgba(), a named colour, `transparent`, or `var(--name)` resolved through
    `lookup(name) -> text`. Anything else (color-mix, currentcolor, gradients) is a
    ValueError, so that a test that meant to measure a colour cannot silently measure none."""
    if _depth > 20:
        raise ValueError(f"var() loop while reading {text!r}")
    text = text.strip().lower()
    match = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", text)
    if match:
        if lookup is None:
            raise ValueError(f"{text} needs a lookup")
        return parse_colour(lookup(match.group(1)), lookup, _depth + 1)
    if text == "transparent":
        return (0.0, 0.0, 0.0, 0.0)
    if text in NAMED_COLOURS:
        return (*map(float, NAMED_COLOURS[text]), 1.0)
    match = re.fullmatch(r"#([0-9a-f]{3,8})", text)
    if match:
        digits = match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(char * 2 for char in digits)
        if len(digits) not in (6, 8):
            raise ValueError(f"not a hex colour: {text!r}")
        red, green, blue = (int(digits[k:k + 2], 16) for k in (0, 2, 4))
        alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return (float(red), float(green), float(blue), alpha)
    match = re.fullmatch(r"rgba?\(\s*(.*?)\s*\)", text)
    if match:
        parts = re.split(r"\s*[,/]\s*|\s+", match.group(1))
        if len(parts) in (3, 4) and all(re.fullmatch(_NUMBER + "%?", part) for part in parts):
            alpha = _alpha(parts[3]) if len(parts) == 4 else 1.0
            return (_channel(parts[0]), _channel(parts[1]), _channel(parts[2]), alpha)
    raise ValueError(f"cannot read a colour out of {text!r}")


def composite(foreground, background):
    """What an (r, g, b, a) colour looks like over an opaque (r, g, b) one."""
    red, green, blue, alpha = foreground
    return tuple(alpha * f + (1 - alpha) * b for f, b in zip((red, green, blue), background[:3]))


def mix(first, second, share):
    """`share` (0..1) of `first` mixed into `second`, both (r, g, b): CSS color-mix in srgb."""
    return tuple(share * a + (1 - share) * b for a, b in zip(first, second))


def luminance(rgb):
    def linear(channel):
        value = channel / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    red, green, blue = (linear(channel) for channel in rgb[:3])
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first, second):
    """WCAG 2 contrast ratio of two opaque (r, g, b) colours."""
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# --- the palettes -----------------------------------------------------------------------

def light_rules():
    return parse_css(read_static("style.css"))


def dark_rules():
    return parse_css(read_static("theme-dark.css"))


def light_palette():
    return custom_properties_of(root_rule(light_rules()))


def dark_palette():
    """The dark theme's own custom properties: its first :root block, part one."""
    return custom_properties_of(root_rule(dark_rules()))


def dark_cascade():
    """What a page under the dark theme resolves a custom property to: the light block with
    the dark one over it, since theme-dark.css is linked after style.css and both are :root."""
    return {**light_palette(), **dark_palette()}


def resolver(properties):
    def lookup(name):
        if name not in properties:
            raise KeyError(f"{name} is not defined")
        return properties[name]
    return lookup
