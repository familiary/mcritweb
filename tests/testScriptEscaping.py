#!/usr/bin/python
"""Values crossing into JavaScript must be escaped for a script context.

Two tests with different reach, and they are not redundant:

`test_no_safe_filter_inside_a_script_block` is a lint over the template tree. It is the
ratchet issue #85 asked for - `|safe` disables autoescaping, and Jinja's autoescaping is
*HTML* escaping anyway, which is not what a `<script>` body needs. `|tojson` is the only
filter that escapes correctly there, and it is what AGENTS.md mandates. The lint exists
because the alternative is an exception list in the documentation that grows back.

`test_a_family_name_cannot_break_out_of_a_script_string` is a render, and it is the one
that would have caught the actual bug: family names are backend data, a contributor
chooses them, and two templates dropped them into a quoted JS string literal. A lint
alone would not have shown that the value was attacker-controlled rather than an id.
"""

import logging
import os
import re

import pytest
from markupsafe import escape
from mcrit.storage.FamilyEntry import FamilyEntry

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PACKAGE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcritweb")
TEMPLATE_ROOT = os.path.join(PACKAGE_ROOT, "templates")

#: `|safe`, `| safe`, `|  safe` - the spacing is why a grep for "|safe" found only eight
#: of the fifteen sites that existed when #85 was written.
SAFE_FILTER = re.compile(r"\|\s*safe\b")
SCRIPT_BLOCK = re.compile(r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL)

#: A name that terminates a double-quoted JS string, closes the script element and opens
#: its own. Flask's `|tojson` escapes `"` and `<`, so none of it survives as markup.
BREAKOUT_NAME = 'evil" </script><script>alert(1)</script>'

#: A name that needs no angle bracket at all: the type-ahead interpolates a suggestion
#: into double-quoted `data-label`/`data-value` attributes, so a quote of either kind
#: is enough to add an attribute of its own there.
ATTRIBUTE_NAME = "evil' onmouseover='window.__pwned=1' \" onfocus=\"window.__pwned=2"


def template_files():
    for directory, _, filenames in os.walk(TEMPLATE_ROOT):
        for filename in sorted(filenames):
            if filename.endswith(".html"):
                yield os.path.join(directory, filename)


def script_block_safe_filters():
    """Every `|safe` occurring inside a `<script>` block, as `path:line` strings."""
    for path in sorted(template_files()):
        with open(path, encoding="utf-8") as template:
            source = template.read()
        relative = os.path.relpath(path, TEMPLATE_ROOT)
        for block in SCRIPT_BLOCK.finditer(source):
            for hit in SAFE_FILTER.finditer(block.group()):
                line = source.count("\n", 0, block.start() + hit.start()) + 1
                yield f"{relative}:{line}"


def test_the_script_block_scan_still_sees_something():
    """A guard on the guard: if the regexes stop matching any script block at all, the
    lint below silently passes on an empty parameter set."""
    blocks = 0
    for path in template_files():
        with open(path, encoding="utf-8") as template:
            blocks += len(SCRIPT_BLOCK.findall(template.read()))
    assert blocks > 10, f"only {blocks} script blocks found - the scan is not watching the tree"


def test_no_safe_filter_inside_a_script_block():
    """One assertion rather than a parametrized case per site, because the healthy state
    is *no* sites: an empty parameter set reports as a skip, which reads like the lint
    ran when it had nothing to run on."""
    offenders = sorted(script_block_safe_filters())
    assert not offenders, (
        "|safe inside a <script> block at: " + ", ".join(offenders) + ". Use |tojson: "
        "autoescaping is HTML escaping, which a browser does not decode in script "
        "content, so |safe and plain autoescaping are both wrong there. See AGENTS.md."
    )


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Result and explore pages need real rows, so serve the captured corpus."""
    return corpus_mcrit


@pytest.mark.parametrize("path", ["/explore/families", "/explore/samples", "/data/submit"])
def test_a_family_name_cannot_break_out_of_a_script_string(client, as_role, fake_mcrit, path):
    """A family name is chosen by whoever submits or renames a family, so it is user
    input arriving by way of the backend - exactly what AGENTS.md says must never reach
    `|safe`.

    None of these pages embeds the names in its source any more. The two explore
    listings used to, through `js/ac_family_names.html`, until #77 moved them onto
    `explore.family_names`, which the test below covers; `/data/submit` did through
    `table/submit_or_query_dropzone.html` until #192 moved it onto the same fetch. They
    stay in this list so that re-embedding a name there would have to pass this again.
    """
    family_id, family_entry = next(iter(fake_mcrit._families.items()))
    fake_mcrit._families[family_id] = FamilyEntry.fromDict(
        dict(family_entry.toDict(), family_name=BREAKOUT_NAME)
    )
    as_role("contributor")

    response = client.get(path)

    assert response.status_code == 200
    assert b"<script>alert(1)</script>" not in response.data, (
        f"a crafted family name broke out of the JS string literal on {path}"
    )


@pytest.mark.parametrize("name", [BREAKOUT_NAME, ATTRIBUTE_NAME])
def test_a_family_name_stays_a_string_in_the_type_ahead_response(client, as_role, fake_mcrit, name):
    """Where those names travel since #77: JSON, fetched by `js/ac_family_names.html`.

    A JSON body is not a script context and is not served as one, so part of what this
    pins is that the endpoint stays JSON and the name stays a string *value* in it -
    never concatenated into a document.

    The rest is the sink the transport never covered. `Autocomplete.createItem` in
    `static/autocomplete.js` builds its dropdown by interpolating the label into an HTML
    string, so a name that *is* markup executes there however safely it travelled.
    Since #192 this endpoint is the only way names reach that widget, so it escapes
    them (`mcritweb.autocomplete`, #168) and the raw name must not survive the round
    trip - neither its angle brackets nor, for the attribute sink, its quotes.
    """
    family_id, family_entry = next(iter(fake_mcrit._families.items()))
    fake_mcrit._families[family_id] = FamilyEntry.fromDict(
        dict(family_entry.toDict(), family_name=name)
    )
    as_role("visitor")

    response = client.get("/explore/familyNames?q=evil")

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    escaped = str(escape(name))
    assert response.json["suggestions"] == [{"label": escaped, "value": escaped}]
    for item in response.json["suggestions"]:
        for field in ("label", "value"):
            for character in ('<', '>', '"', "'"):
                assert character not in item[field], (
                    f"the {field} {item[field]!r} still carries {character!r} - the widget "
                    f"writes it into innerHTML and into a double-quoted attribute"
                )


# --- the CFG page's own script ---------------------------------------------------

#: `static/trace_CFG/main_duo.js` is a project fork we maintain (see AGENTS.md), and it
#: assigns into `innerHTML` in four places. Three of them build a `<span>` around a line
#: of block text taken out of the dot graph - which carries the api names smda read out
#: of the analysed binary - so the value being wrapped is attacker-influenced even
#: though the wrapper is not. `main.js` is stock and is deliberately not scanned: it is
#: not ours to change, and it has the same construct.
MAIN_DUO = os.path.join(PACKAGE_ROOT, "static", "trace_CFG", "main_duo.js")

#: The shape all three sinks share: something dropped straight after the `>` that closes
#: a tag opener and straight before the matching `</span>`.
SPAN_INTERPOLATION = re.compile(r">\"\s*\+\s*(?P<value>.+?)\s*\+\s*\"</span>")

#: Whole-line comments. Commented-out code cannot run, and this file has a lot of it -
#: including earlier drafts of the very lines being linted.
JS_LINE_COMMENT = re.compile(r"^\s*//")


def main_duo_lines():
    with open(MAIN_DUO, encoding="utf-8") as script:
        for number, line in enumerate(script, start=1):
            if not JS_LINE_COMMENT.match(line):
                yield number, line


def span_interpolations():
    """Every value interpolated into a `<span>` in main_duo.js, as (line, expression)."""
    for number, line in main_duo_lines():
        for hit in SPAN_INTERPOLATION.finditer(line):
            yield number, hit.group("value").strip()


def test_the_main_duo_scan_still_sees_something():
    """A guard on the guard, as above: the file could be refreshed from upstream, or the
    construct rewritten, and this lint would then pass by finding nothing."""
    found = list(span_interpolations())
    assert len(found) >= 3, (
        f"only {len(found)} span interpolations found in main_duo.js - the scan has "
        "stopped watching the taint highlighters"
    )


def test_block_text_is_escaped_before_it_is_built_into_markup():
    """The tooltip's `innerHTML` sink was closed by switching it to `.text()`. These three
    cannot be: the markup is the point - they wrap a line of code in a coloured span and
    that is what the highlight *is*. So the untrusted half is escaped instead, and this
    lint is what keeps it that way.

    All three are unreachable today: `updateTaint` and `highlightUERs` are driven by
    `#doTaint`, `#myTaintSlider` and `#analysisSelector`, none of which this template
    renders, and they read `nodeToTextGroups`, which only `setupTrace()` fills and which
    nothing calls. That is precisely the argument this change refused to accept for the
    tooltip, so it is not accepted here either: `usePanel`'s comment invites someone to
    wire `setupTrace` up, and the sink must already be shut when they do.
    """
    unescaped = [f"main_duo.js:{number} interpolates {value}"
                 for number, value in span_interpolations()
                 if not value.startswith("escapeHtml(")]
    assert not unescaped, (
        "text is built into markup without escaping at: " + "; ".join(unescaped)
        + ". Wrap the value in escapeHtml() - these strings are assigned into innerHTML."
    )
