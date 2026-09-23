#!/usr/bin/python
"""The family-name type-ahead renders suggestions as HTML, so its data must be escaped.

`static/autocomplete.js` is vendored third-party code - byte-for-byte upstream
`gch1p/bootstrap-5-autocomplete` at 5ce977959b12, see AGENTS.md. Its `createItem`
builds each suggestion by interpolating the label and the value into an HTML string,
and `ce()` assigns that string to `innerHTML`. A family name is chosen by whoever
submits or renames a family, so a name that *is* markup becomes script the moment
someone opens the edit-family/edit-sample modal and types a matching character. Two
sinks, not one: the button's text content, and the `data-label`/`data-value`
attributes the same value is interpolated into unescaped.

`|tojson` at the two call sites protects the transport - the name reaches the browser
as a correct JS string - and that is all it protects. The sink is in JavaScript, one
`innerHTML` further on, and issue #85's ratchet in `testScriptEscaping.py` does not
reach it. AGENTS.md forbids patching a vendored asset, so the escaping is done where
the data is built: the templates hand the widget names that are already HTML-escaped.

That fix has a known, bounded cost, pinned below by
`test_the_highlighter_severs_an_entity` rather than left to a comment nobody rereads:
the widget slices the *escaped* label at offsets it measured in that same escaped
string, so a lookup straddling an entity splits it and the suggestion renders as
`R&amp;D` instead of `R&D`. Display only - the label is escaped either way, and
`data-label` still round-trips to the original - and it cannot be fixed from the call
site, because `item.label` (`autocomplete.js:110`) is the single value the matcher
(`:114`), the highlight index (`:70-72`), the rendered slices (`:75-77`) and the
attribute (`:90`) all read. See AGENTS.md for the full argument.

Four tests, deliberately not redundant:

`test_the_type_ahead_data_is_html_escaped` is offline and runs everywhere. It decodes
the JS string literals the page actually emits and asserts none of them still carries
raw markup - not just `<`, but `"` and `'` too, since `data-label="..."` is an
attribute and a bare quote there is an event handler.

`test_every_type_ahead_builds_its_data_through_the_escaping_filter` is the ratchet.
Escaping at the call site is only as good as the next call site, and it checks the
data *expression* rather than looking for the filter's name somewhere in the block -
a comment mentioning the filter used to be enough to satisfy it.

`test_a_family_name_cannot_execute_in_the_type_ahead` drives a real browser, because
the sink is a browser behaviour and no amount of reading the response proves it is
shut. Two payloads at once - one that opens a tag, one that breaks out of the
`data-label` attribute - and it asserts neither ran, both still read as text, the
prefix is still highlighted, and selecting one puts the *unescaped* name in the field.

`test_the_highlighter_severs_an_entity` is the honest record of the cost above.

The browser tests need playwright with a chromium build; without either they skip
rather than fail, so the offline pair is what CI is guaranteed to run.
"""

import json
import os
import re
import threading

import pytest
from markupsafe import escape
from mcrit.storage.FamilyEntry import FamilyEntry

#: Opens a tag from the button's text content. `<img src=q onerror=...>` needs no
#: `<script>` element, so it fires from an `innerHTML` assignment where a script tag
#: would not.
TAG_PAYLOAD = 'zz<img src=q onerror="window.__pwned=1">'

#: Breaks out of `data-label="..."` at `autocomplete.js:90` without ever using `<`.
#: A fix that escaped only the angle brackets would shut the first payload and leave
#: this one wide open, which is why both are typed in the same pass.
ATTRIBUTE_PAYLOAD = 'zz" onmouseover="window.__pwned=2'

#: Both payloads share a prefix so one lookup renders both suggestions.
PAYLOADS = (TAG_PAYLOAD, ATTRIBUTE_PAYLOAD)
LOOKUP = "zz"

#: A name that is not an attack at all - it just contains a character the escaping has
#: to encode - and a lookup that ends in the middle of the resulting entity.
ENTITY_NAME = "R&D"
ENTITY_LOOKUP = "R&"

#: The families the names are written onto. Not family 0 - that one has an empty name
#: in the corpus, and an empty name is a different edge case.
POISONED_FAMILY_IDS = (1, 2)
ENTITY_FAMILY_ID = 3

#: Pages carrying the family type-ahead: the four explore pages reach it through
#: `js/ac_family_names.html`, `/data/submit` through the `submit_or_query_dropzone`
#: macro. Both call sites, because each builds the widget's data for itself.
TYPE_AHEAD_PAGES = (
    "/explore/families",
    "/explore/samples",
    "/explore/families/1",
    "/explore/samples/0",
    "/data/submit",
)

#: Of those, the ones that still put the names *in the page*. #146 moved
#: `js/ac_family_names.html` onto a fetch of `explore.family_names`, so the four
#: explore pages carry no names to inspect in their markup - the escaping for them
#: happens in that endpoint, and testScriptEscaping.py pins it there. The markup
#: assertion below would otherwise pass by finding nothing, which is why it fails
#: loudly when a page it names carries no payload at all.
EMBEDDED_NAME_PAGES = ("/data/submit",)

#: The pages the browser test can drive. `/explore/families/<id>` is missing on
#: purpose: it includes the same `js/ac_family_names.html` partial, but its sample
#: table comes back empty under the offline corpus - `fixtureData._page` does not
#: model mcrit's `field:value` query parser - so the edit modal, and with it the
#: field to type into, is never rendered. The markup test above still covers it.
BROWSER_PAGES = tuple(page for page in TYPE_AHEAD_PAGES if page != "/explore/families/1")

#: The field the widget is attached to, per call site. `#family` is the dropzone's.
FIELD_SELECTOR = "#family_new_name, #sample_family_name, #family"

#: Characters that must not survive into a value the widget renders. `<` and `>` open
#: a tag in the button's text; `"` and `'` end the attribute at `autocomplete.js:90`,
#: and an attribute break needs no angle bracket at all. `&` is deliberately absent -
#: escaping *produces* `&`, so forbidding it would fail on correct output.
MARKUP_CHARACTERS = ('<', '>', '"', "'")

SCRIPT_BLOCK = re.compile(r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL)
JS_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')

PACKAGE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcritweb")
TEMPLATE_ROOT = os.path.join(PACKAGE_ROOT, "templates")
STATIC_ROOT = os.path.join(PACKAGE_ROOT, "static")


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """The type-ahead is built from the backend's family list, so serve real rows."""
    return corpus_mcrit


def rename_family(backend, family_id, name):
    entry = backend._families[family_id]
    backend._families[family_id] = FamilyEntry.fromDict(dict(entry.toDict(), family_name=name))


@pytest.fixture
def poisoned_backend(fake_mcrit):
    """Rename corpus families to the payloads, the way `explore.modifyFamily` would."""
    for family_id, payload in zip(POISONED_FAMILY_IDS, PAYLOADS):
        rename_family(fake_mcrit, family_id, payload)
    rename_family(fake_mcrit, ENTITY_FAMILY_ID, ENTITY_NAME)
    return fake_mcrit


def autocomplete_scripts(markup):
    """Every `<script>` block on the page that constructs the type-ahead."""
    return [block for block in SCRIPT_BLOCK.findall(markup) if "new Autocomplete(" in block]


@pytest.mark.parametrize("path", EMBEDDED_NAME_PAGES)
def test_the_type_ahead_data_is_html_escaped(client, as_role, poisoned_backend, path):
    """No string handed to the widget may still contain raw markup.

    Asserted on the decoded values rather than on the response bytes: `|tojson` writes
    `<` as `\\u003c` and `&` as `\\u0026`, so a substring search over the raw response
    cannot tell an escaped name from an unescaped one.
    """
    as_role("admin")

    response = client.get(path)
    assert response.status_code == 200
    markup = response.data.decode()

    scripts = autocomplete_scripts(markup)
    assert scripts, f"no autocomplete script block found on {path} - the scan missed it"

    seen = set()
    for script in scripts:
        for literal in JS_STRING.findall(script):
            value = json.loads(literal)
            for character in MARKUP_CHARACTERS:
                assert character not in value, (
                    f"{path} hands the type-ahead {value!r}, which still carries "
                    f"{character!r} - the widget writes it into innerHTML and into a "
                    f"data-label attribute, and both read it as markup"
                )
            seen.add(value)

    for payload in PAYLOADS:
        assert str(escape(payload)) in seen, (
            f"the poisoned family name {payload!r} never reached the type-ahead on "
            f"{path} - the test is not exercising what it claims to"
        )


# --- the ratchet ------------------------------------------------------------------

#: Comments are stripped first, or the lint is satisfied by prose: the filter's own
#: name appears in the explanatory comment above each call site, so a copied-then-
#: broken call site used to pass on the strength of the comment it was copied with.
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"^[ \t]*//.*$", re.MULTILINE)

#: `data:` inside a `new Autocomplete(...)` options object, and the argument of any
#: `.setData(...)` call - the two ways data reaches the widget. `setData` is matched
#: with its dot so the vendored method *definition* is not taken for a call.
DATA_OPTION = re.compile(r"new\s+Autocomplete\s*\((?:[^()]|\([^()]*\))*?\bdata\s*:\s*([^,\n}]+)")
SET_DATA = re.compile(r"\.setData\s*\(\s*([^,\n)]+)")

#: A name bound to a filtered expression: `var families_ac = {{ x|autocomplete_items|tojson }}`.
FILTERED_BINDING = re.compile(
    r"(?:var|let|const)?\s*([A-Za-z_$][\w$]*)\s*=\s*\{\{[^{}]*\|\s*autocomplete_items\b"
)

#: The expression itself being a filtered Jinja expression, passed inline.
FILTERED_EXPRESSION = re.compile(r"\{\{[^{}]*\|\s*autocomplete_items\b")

#: `data: []` - a widget built empty and filled by a later setData, which is checked
#: on its own merits. An empty literal carries no names, so it cannot carry markup.
EMPTY_LITERAL = re.compile(r"\[\s*\]")

#: Files whose data reaches the widget already escaped because a *server-side* caller
#: built it, not the template filter. The filter stopped being the only way in when
#: #146 moved the family type-ahead onto a fetch, and the ratchet cannot follow data
#: across that boundary - it says so in its own docstring. Each entry therefore names
#: where the escaping happens and the test that pins it, and the entries are checked
#: for staleness below so this cannot quietly become the exception list AGENTS.md
#: warns grows back.
ESCAPED_AT_THE_SOURCE = {
    "templates/js/ac_family_names.html": (
        "fetched from explore.family_names, which answers {label, value} items built by "
        "mcritweb.autocomplete.autocomplete_items - the same function behind the filter. "
        "Pinned by testScriptEscaping.py::"
        "test_a_family_name_stays_a_string_in_the_type_ahead_response."
    ),
}


def strip_comments(source):
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", JINJA_COMMENT.sub("", source)))


def unfiltered_data_expressions(source):
    """The expressions in one file that reach the widget without going through the
    filter. Split out from the walk so the lint's own regression test can drive it."""
    stripped = strip_comments(source)
    safe_names = set(FILTERED_BINDING.findall(stripped))
    for pattern in (DATA_OPTION, SET_DATA):
        for match in pattern.finditer(stripped):
            expression = match.group(1).strip()
            if FILTERED_EXPRESSION.search(expression) or expression in safe_names:
                continue
            if EMPTY_LITERAL.fullmatch(expression):
                # a widget constructed with no data at all. Whatever fills it later is
                # a setData call, which this same walk checks on its own merits.
                continue
            yield stripped.count("\n", 0, match.start()) + 1, expression


def front_end_sources():
    """Templates and scripts that name the widget.

    Scanning `static/` matters - a `new Autocomplete(` there would otherwise be
    invisible - but `setData` is a name other libraries use for something else
    entirely: SortableJS calls `dataTransfer.setData("Text", ...)` eight times. A file
    that never mentions `Autocomplete` cannot be feeding one, and that test is
    self-maintaining, where a list of vendored filenames to skip is the exception list
    AGENTS.md warns grows back. The vendored widget is skipped explicitly: it *defines*
    `setData` rather than calling one, and it is not ours to lint either way.
    """
    for root, suffix in ((TEMPLATE_ROOT, ".html"), (STATIC_ROOT, ".js")):
        for directory, _, filenames in os.walk(root):
            for filename in sorted(filenames):
                if not filename.endswith(suffix):
                    continue
                path = os.path.join(directory, filename)
                if os.path.abspath(path) == os.path.join(STATIC_ROOT, "autocomplete.js"):
                    continue
                with open(path, encoding="utf-8") as handle:
                    source = handle.read()
                if "Autocomplete" in source:
                    yield path, source


def test_every_type_ahead_builds_its_data_through_the_escaping_filter():
    """A ratchet, in the shape of `testScriptEscaping.py`'s.

    The escaping lives at the call sites because `static/autocomplete.js` is vendored
    and may not be patched, which means a third call site added without it reopens the
    hole in full. The browser test below only knows the pages that exist today.

    This resolves the data *expression* - a bare name has to be bound to a filtered
    expression somewhere in the same file - rather than asking whether the filter's
    name appears anywhere in the block, which a comment satisfies. It covers
    `setData` as well as the constructor, and `static/*.js` as well as the templates.
    What it cannot follow is data built in one file and consumed in another; nothing
    does that today.
    """
    offenders = []
    exempt_and_still_needed = set()
    for path, source in front_end_sources():
        relative = os.path.relpath(path, PACKAGE_ROOT).replace(os.sep, "/")
        for line, expression in unfiltered_data_expressions(source):
            if relative in ESCAPED_AT_THE_SOURCE:
                exempt_and_still_needed.add(relative)
                continue
            offenders.append(f"{relative}:{line} ({expression})")
    offenders.sort()

    stale = set(ESCAPED_AT_THE_SOURCE) - exempt_and_still_needed
    assert stale == set(), (
        "ESCAPED_AT_THE_SOURCE names a file that now builds its data through the "
        f"filter, or no longer feeds a widget at all: {sorted(stale)}. Drop the entry - "
        "an exemption nothing needs is how the list stops being read."
    )

    assert not offenders, (
        "a type-ahead is built from data the escaping filter did not produce, at: "
        + ", ".join(offenders) + ". autocomplete.js renders each suggestion through "
        "innerHTML and into a data-label attribute, so pass the names through "
        "|autocomplete_items - |tojson only protects the transport."
    )


#: Each of these passed the ratchet's first version. They are the review findings
#: turned into cases, so the lint cannot quietly regress to a substring search.
RATCHET_BYPASSES = {
    "a comment naming the filter": (
        "<script>\n// built with |autocomplete_items\n"
        "var ac_data = {{ families|tojson }};\n"
        "new Autocomplete(field, {data: ac_data, threshold: 1});\n</script>"
    ),
    "setData with raw names": (
        "<script>\nvar ok = {{ families|autocomplete_items|tojson }};\n"
        "new Autocomplete(field, {data: ok});\nac.setData(rawFamilies);\n</script>"
    ),
    "built in one block, consumed in another": (
        "<script>\nvar d = {{ families|tojson }};\n</script>\n"
        "<script>\nnew Autocomplete(field, {data: d});\n</script>"
    ),
}

#: And these must stay quiet, or the ratchet is just noise.
RATCHET_ACCEPTS = {
    "inline filtered expression": (
        "<script>\nnew Autocomplete(field, {data: {{ families|autocomplete_items|tojson }}});\n</script>"
    ),
    "filtered binding, used later": (
        "<script>\nvar families_ac = {{ family_names|autocomplete_items|tojson }};\n"
        "new Autocomplete(field, {data: families_ac, maximumItems: 5});\n"
        "families_ac.push;\nac.setData(families_ac);\n</script>"
    ),
}


@pytest.mark.parametrize("description", sorted(RATCHET_BYPASSES))
def test_the_ratchet_rejects_the_ways_around_it(description):
    findings = list(unfiltered_data_expressions(RATCHET_BYPASSES[description]))
    assert findings, f"the ratchet still lets through: {description}"


@pytest.mark.parametrize("description", sorted(RATCHET_ACCEPTS))
def test_the_ratchet_accepts_a_correct_call_site(description):
    findings = list(unfiltered_data_expressions(RATCHET_ACCEPTS[description]))
    assert not findings, f"the ratchet wrongly flags: {description} -> {findings}"


# --- the browser ------------------------------------------------------------------

def session_cookie_value(app, user_id):
    """A signed session cookie for a browser, matching what `as_role` sets on the
    test client."""
    from flask.sessions import SecureCookieSessionInterface

    serializer = SecureCookieSessionInterface().get_signing_serializer(app)
    return serializer.dumps({"user_id": user_id})


@pytest.fixture
def live_url(app):
    """The app on a real socket. Playwright needs one; the test client is not a server."""
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


#: Read back after the widget has rendered. `textContent` rather than `innerText`
#: because the dropdown may be hidden, and a hidden element has no inner text.
#: `getAttributeNames` is what catches an attribute break - an injected handler is a
#: real attribute on the button, and no amount of reading its text would show it.
MENU_SNAPSHOT = """
(selector) => {
  const field = document.querySelector(selector);
  const menu = field.nextSibling;
  const items = [...menu.querySelectorAll('.dropdown-item')];
  return {
    texts: items.map(item => item.textContent),
    labels: items.map(item => item.getAttribute('data-label')),
    values: items.map(item => item.getAttribute('data-value')),
    attributes: items.map(item => item.getAttributeNames().join(',')),
    elements: menu.querySelectorAll('*:not(.dropdown-item):not(span)').length,
    highlights: menu.querySelectorAll('.dropdown-item span.text-primary').length,
  };
}
"""

TYPE_INTO_FIELD = """
([selector, lookup]) => {
  const field = document.querySelector(selector);
  field.value = lookup;
  field.dispatchEvent(new Event('input'));
}
"""

HAS_SUGGESTION = """
([selector, wanted]) => {
  const field = document.querySelector(selector);
  const menu = field && field.nextSibling;
  return !!menu && menu.querySelectorAll('.dropdown-item').length >= wanted;
}
"""

#: Selecting a suggestion must hand back the *original* name, not the escaped one:
#: the widget reads `data-label` off the button, and the browser decoded the entities
#: when it parsed that attribute. The click is dispatched on the button rather than on
#: the highlight span because the vendored widget reads `e.target` - clicking the
#: highlighted characters themselves loses the label, which is an upstream bug and not
#: this one.
SELECT_FIRST_SUGGESTION = """
(selector) => {
  const field = document.querySelector(selector);
  field.nextSibling.querySelector('.dropdown-item').click();
  return field.value;
}
"""


def drive_type_ahead(sync_api, app, live_url, user_id, path, lookup, expected_items):
    """Open `path` in a browser, type `lookup`, and report what the widget rendered."""
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - a missing browser is a skip, not a failure
            pytest.skip(f"playwright has no chromium installed: {exc}")
        try:
            context = browser.new_context()
            context.add_cookies([{
                "name": "session",
                "value": session_cookie_value(app, user_id),
                "url": live_url,
            }])
            page = context.new_page()
            page.goto(live_url + path)
            page.wait_for_selector(FIELD_SELECTOR, state="attached", timeout=15000)

            page.evaluate(TYPE_INTO_FIELD, [FIELD_SELECTOR, lookup])
            page.wait_for_function(HAS_SUGGESTION, arg=[FIELD_SELECTOR, expected_items], timeout=10000)

            executed = True
            try:
                page.wait_for_function("() => window.__pwned !== undefined", timeout=2000)
            except sync_api.TimeoutError:
                executed = False

            snapshot = page.evaluate(MENU_SNAPSHOT, FIELD_SELECTOR)
            snapshot["executed"] = executed
            snapshot["selected"] = page.evaluate(SELECT_FIRST_SUGGESTION, FIELD_SELECTOR)
            return snapshot
        finally:
            browser.close()


@pytest.mark.parametrize("path", BROWSER_PAGES)
def test_a_family_name_cannot_execute_in_the_type_ahead(app, poisoned_backend, live_url, make_user, path):
    sync_api = pytest.importorskip("playwright.sync_api")
    user_id = make_user("admin")
    rendered = drive_type_ahead(sync_api, app, live_url, user_id, path, LOOKUP, len(PAYLOADS))

    assert not rendered["executed"], (
        f"a crafted family name executed from the type-ahead on {path}: typing "
        f"{LOOKUP!r} ran a payload's handler"
    )
    assert rendered["elements"] == 0, (
        f"the type-ahead on {path} turned a family name into {rendered['elements']} "
        f"element(s) instead of text"
    )
    for item_attributes in rendered["attributes"]:
        assert "onmouseover" not in item_attributes, (
            f"a family name injected an event handler attribute on {path}: "
            f"{item_attributes}"
        )
    for payload in PAYLOADS:
        assert payload in rendered["texts"], (
            f"the suggestion for {payload!r} on {path} does not read as text - "
            f"got {rendered['texts']}"
        )
        assert payload in rendered["labels"], (
            f"data-label for {payload!r} on {path} does not round-trip - "
            f"got {rendered['labels']}"
        )
        assert payload in rendered["values"], (
            f"data-value for {payload!r} on {path} does not round-trip - "
            f"got {rendered['values']}"
        )
    assert rendered["highlights"] >= len(PAYLOADS), (
        f"the typed prefix is no longer highlighted on {path} - escaping the data must "
        f"not cost the widget its highlightTyped behaviour"
    )
    assert rendered["selected"] in PAYLOADS, (
        f"selecting the suggestion on {path} put {rendered['selected']!r} in the field "
        f"instead of the family name - the escaping has to be invisible on the way out"
    )


def test_the_highlighter_severs_an_entity(app, poisoned_backend, live_url, make_user):
    """The documented cost of escaping at the call site, pinned so it stays measured.

    `autocomplete.js:70-77` finds the lookup in the escaped label and slices that same
    escaped string at the offsets it found, injecting the highlight `<span>` between
    them. A lookup ending inside an entity therefore cuts the entity in half, and the
    two fragments render as the literal `&amp;` rather than as `&`.

    Everything that matters for security still holds and is asserted here; only the
    visible text is wrong, and only for a name containing one of the five escaped
    characters. It cannot be fixed from the call site - `item.label` is one value, and
    the matcher, the highlighter and the attribute all read it - so it is recorded
    rather than argued away in a comment. If the severing ever stops, this test says
    so, and the caveat in AGENTS.md needs deleting with it.
    """
    sync_api = pytest.importorskip("playwright.sync_api")
    user_id = make_user("admin")
    rendered = drive_type_ahead(
        sync_api, app, live_url, user_id, "/explore/families", ENTITY_LOOKUP, 1
    )

    assert not rendered["executed"], "the entity case must still be inert"
    assert rendered["elements"] == 0, "the entity case must not produce elements"
    assert ENTITY_NAME in rendered["labels"], (
        f"data-label must round-trip even when the highlighter severs the entity - "
        f"got {rendered['labels']}"
    )
    assert rendered["selected"] == ENTITY_NAME, (
        f"selecting must still yield the original name - got {rendered['selected']!r}"
    )

    assert ENTITY_NAME not in rendered["texts"], (
        "the highlighter no longer severs entities - the widget was replaced or "
        "patched, so drop this test and the caveat in AGENTS.md"
    )
    assert "R&amp;D" in rendered["texts"], (
        f"expected the severed entity to render literally - got {rendered['texts']}"
    )
