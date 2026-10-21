#!/usr/bin/python
"""Nothing reaches for jQuery before it has run, now that it loads after the first paint.

`base.html` queues jQuery with `later()` (issue #63, ADR-0018, ADR-0021), and the queue
is loaded once the page has been painted, after DOMContentLoaded. So an inline script
that calls `$` there and then - `$(document).ready(...)` and `$(fn)` are such calls, and
so is a binding like `$("#x").on(...)` at the top level of a block - stops with "$ is
not defined", and with it everything after it in the same block; and so does a
DOMContentLoaded or load listener, or a `window.onload`, that calls it. Those scripts
hand their work to `whenReady(fn)` instead, which base.html defines above every other
script. A function declaration that uses `$` in its body is fine where it is: it runs
when an onclick= or another script calls it. The same holds for everything else a
queued script defines - Bootstrap, SortableJS, the type-ahead, `postTo()` - so the scan
looks for all of those, not only for `$`.

The same goes for a library: a plain `<script src>` runs while the page parses, so one
that extends jQuery has to be queued like jQuery itself, and after it.

The checks read the templates as source, so that a template no test renders is covered
too, and every page the accessibility checks render, so that what macros and
flask-dropzone write into a page is covered as well. Telling "runs now" from "runs
later" takes more than a regex, so there is a small scanner below. It is not a parser:
it knows strings, comments, regex literals and brackets, which function bodies run at
once (the top level, an immediately invoked function - async ones included - a callback
handed to anything that does not keep it for an event, timers and promises among them,
and a declared function that code running at once calls, applies or hands on) and which
do not (a declaration nobody calls yet, an event handler, a method of an object or a
class). It follows a name only to a function declaration: a function expression or an
arrow kept in a variable, or a method, that code running at once then calls is taken to
run later. No template writes one. The tests at the end hold it to that.
"""

import collections
import logging
import pathlib
import re

import pytest
from testAccessibility import ADMIN_PAGES, ANONYMOUS_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "mcritweb"
TEMPLATE_ROOT = PACKAGE_ROOT / "templates"
STATIC_ROOT = PACKAGE_ROOT / "static"

Token = collections.namedtuple("Token", "kind text position")

JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
JINJA_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
SCRIPT_ELEMENT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
SCRIPT_TYPE = re.compile(r"""\btype\s*=\s*["']?([^"'\s>]+)""", re.I)
SCRIPT_SOURCE = re.compile(r"""\bsrc\s*=\s*["']([^"']*)["']""", re.I)
STATIC_FILENAME = re.compile(r"""filename\s*=\s*['"]([^'"]+)['"]""")
STYLE_BLOCK = re.compile(r"{%-?\s*block style\s*-?%}(.*?){%-?\s*endblock", re.S)
HEAD = re.compile(r"<head\b[^>]*>(.*?)</head\s*>", re.S | re.I)

KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "typeof", "with", "in", "of",
            "new", "delete", "void", "throw", "case", "do", "else", "instanceof", "yield", "await",
            "var", "let", "const"}

#: after one of these, a `/` starts a regex literal rather than a division
REGEX_MAY_FOLLOW = set("(,=:[!&|?{};+-*%<>~^") | KEYWORDS - {"function"}

#: Callees known to keep a function they are handed for an event - a DOM event, or the
#: queued scripts having run (whenReady) - rather than call it there and then. A timer or
#: a promise is not one of them: a setTimeout callback or a `.then` can run while the
#: queued scripts are still being fetched. A function handed to anything else (forEach, a
#: constructor, a call whose name is not known) is taken to run at once, which errs
#: towards a finding.
KEEPS_IT_FOR_LATER = {"whenReady", "$", "jQuery", "addEventListener", "on", "one", "ready",
                      "click", "submit", "change"}

#: ...except for the events that come before the queue has run: it starts loading after
#: DOMContentLoaded, and load, and the pageshow right after it, can fire first when
#: nothing else was left to fetch
BEFORE_THE_QUEUE = {"DOMContentLoaded", "load", "pageshow", "readystatechange"}

#: the names a script can reach the global object by: `window.jQuery`, `self.$`
GLOBAL_OBJECT = ("window", "globalThis", "self")

#: what can come right before a method's name in an object literal or a class body:
#: `{ run() {...} }`, `, get x() {...}`, `static make() {...}`, `async load() {...}`
BEFORE_A_METHOD_NAME = {"{", ",", ";", "}", "get", "set", "static", "async", "*"}


def blanked(match):
    """A match replaced by whitespace of the same shape, so offsets and lines survive."""
    return re.sub(r"[^\n]", " ", match.group())


def code_of(path):
    """The template with its Jinja and HTML comments blanked out."""
    return HTML_COMMENT.sub(blanked, JINJA_COMMENT.sub(blanked, path.read_text()))


def without_jinja(source):
    """Jinja tags out of a template's script: an expression becomes `0` - it is a value
    there, usually inside a string or a |tojson - and a statement disappears, which
    keeps both branches of an {% if %}."""
    def replace(match):
        shape = blanked(match)
        return "0" + shape[1:] if match.group().startswith("{{") else shape
    return JINJA_TAG.sub(replace, source)


def inline_scripts(code):
    """(offset, JavaScript) for each inline script in a template or a rendered page."""
    for match in SCRIPT_ELEMENT.finditer(code):
        attributes = match.group(1)
        if SCRIPT_SOURCE.search(attributes):
            continue
        kind = SCRIPT_TYPE.search(attributes)
        if kind and kind.group(1).lower() not in ("text/javascript", "application/javascript"):
            # the cross compare's lazy panes travel as application/json
            continue
        yield match.start(2), match.group(2)


def external_scripts(code):
    """(offset, the tag, its src) for each `<script src>`."""
    for match in re.finditer(r"<script\b[^>]*>", code, re.I):
        source = SCRIPT_SOURCE.search(match.group())
        if source:
            yield match.start(), match.group(), source.group(1)


def is_deferred(tag):
    return re.search(r"\sdefer\b", tag) is not None


# --- the scanner -----------------------------------------------------------------------

def string_end(source, start):
    quote, index = source[start], start + 1
    while index < len(source):
        character = source[index]
        if character == "\\":
            index += 2
            continue
        if character == quote:
            return index + 1
        if quote == "`" and source.startswith("${", index):
            index = substitution_end(source, index + 2)
            continue
        if quote != "`" and character == "\n":
            return index
        index += 1
    return len(source)


def substitution_end(source, index):
    depth = 1
    while index < len(source) and depth:
        character = source[index]
        if character in "'\"`":
            index = string_end(source, index)
            continue
        depth += {"{": 1, "}": -1}.get(character, 0)
        index += 1
    return index


def regex_end(source, start):
    index, in_class = start + 1, False
    while index < len(source):
        character = source[index]
        if character == "\\":
            index += 2
            continue
        if character == "\n":
            return index
        if in_class:
            in_class = character != "]"
        elif character == "[":
            in_class = True
        elif character == "/":
            index += 1
            while index < len(source) and source[index].isalnum():
                index += 1
            return index
        index += 1
    return len(source)


def js_tokens(source):
    """Names, numbers, punctuation, and each string, template or regex literal as one
    token; comments and whitespace dropped."""
    tokens, index, length = [], 0, len(source)
    while index < length:
        character = source[index]
        previous = tokens[-1] if tokens else None
        if character.isspace():
            index += 1
        elif source.startswith("//", index):
            end = source.find("\n", index)
            index = length if end < 0 else end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
        elif character in "'\"`":
            end = string_end(source, index)
            tokens.append(Token("string", source[index:end], index))
            index = end
        elif character == "/" and (previous is None or previous.kind in ("punct", "name") and previous.text in REGEX_MAY_FOLLOW):
            end = regex_end(source, index)
            tokens.append(Token("regex", source[index:end], index))
            index = end
        elif character.isalpha() or character in "_$":
            end = index + 1
            while end < length and (source[end].isalnum() or source[end] in "_$"):
                end += 1
            tokens.append(Token("name", source[index:end], index))
            index = end
        elif character.isdigit():
            end = index + 1
            while end < length and (source[end].isalnum() or source[end] in "._"):
                end += 1
            tokens.append(Token("number", source[index:end], index))
            index = end
        else:
            text = next((p for p in ("=>", "...", "?.") if source.startswith(p, index)), character)
            tokens.append(Token("punct", text, index))
            index += len(text)
    return tokens


def bracket_pairs(tokens):
    """index of an opening bracket -> index of its closing one, and the other way round"""
    pairs, open_at = {}, []
    for index, token in enumerate(tokens):
        if token.kind != "punct":
            continue
        if token.text in "([{":
            open_at.append(index)
        elif token.text in ")]}" and open_at:
            opener = open_at.pop()
            pairs[opener], pairs[index] = index, opener
    return pairs


def is_punct(tokens, index, text):
    return 0 <= index < len(tokens) and tokens[index].kind == "punct" and tokens[index].text == text


def is_invoked(tokens, after):
    """Whether what ends just before `after` is called right there: `()`, `.call`, `.apply`."""
    return is_punct(tokens, after, "(") or (
        is_punct(tokens, after, ".") and after + 1 < len(tokens) and tokens[after + 1].text in ("call", "apply"))


def callee(tokens, opener):
    """The name the `(` at `opener` calls: None if it does not open a call at all, ""
    if what it calls has no name of its own, like `f()(...)`."""
    before = tokens[opener - 1] if opener > 0 else None
    if before is None:
        return None
    if before.kind == "name":
        if before.text in KEYWORDS or opener > 1 and tokens[opener - 2].text == "function":
            return None
        return before.text
    if before.kind == "punct" and before.text in ")]":
        return ""
    return None


def is_method_name(tokens, pairs, index):
    """Whether tokens[index] names a method being defined, `name(...) {`, rather than a
    function being called: its body runs when something calls the method."""
    token = tokens[index]
    if token.kind != "name" or token.text in KEYWORDS or index == 0:
        return False
    if not is_punct(tokens, index + 1, "(") or index + 1 not in pairs:
        return False
    return is_punct(tokens, pairs[index + 1] + 1, "{") and tokens[index - 1].text in BEFORE_A_METHOD_NAME


def listens_before_the_queue(tokens, opener):
    """Whether the call the `(` at `opener` opens is addEventListener for an event that
    comes before the queued scripts have run."""
    if callee(tokens, opener) != "addEventListener" or opener + 1 >= len(tokens):
        return False
    event = tokens[opener + 1]
    return event.kind == "string" and event.text[1:-1] in BEFORE_THE_QUEUE


def is_the_onload_handler(tokens, first):
    """Whether what starts at tokens[first] is assigned to the window's `onload`:
    `window.onload = ...` or `onload = ...`, not `xhr.onload = ...`."""
    if not (is_punct(tokens, first - 1, "=") and first > 1 and tokens[first - 2].text == "onload"):
        return False
    if is_punct(tokens, first - 3, "."):
        return first > 3 and tokens[first - 4].text in GLOBAL_OBJECT
    return not is_punct(tokens, first - 3, "=")


def runs_at_once(tokens, pairs, first, last, enclosing):
    """Whether the function expression in tokens[first..last] runs as soon as it is
    reached: invoked on the spot, or handed to a call that does not keep it for later.
    `enclosing` is the bracket the expression sits in, if any."""
    if is_invoked(tokens, last + 1):
        return True
    if is_punct(tokens, first - 1, "(") and pairs.get(first - 1) == last + 1 and callee(tokens, first - 1) is None:
        # (function () {...})() and its kin
        return is_invoked(tokens, last + 2)
    if (is_punct(tokens, first - 1, "(") or is_punct(tokens, first - 1, ",")) and enclosing is not None and is_punct(tokens, enclosing, "("):
        name = callee(tokens, enclosing)
        if name is not None:
            return name not in KEEPS_IT_FOR_LATER or listens_before_the_queue(tokens, enclosing)
    # assigned, returned, a property of an object: it runs when something calls it - and
    # the window's onload can be called before the queue has run
    return is_the_onload_handler(tokens, first)


def walk(tokens, pairs, start, end, at_once):
    """For each token in [start, end), whether it runs as soon as the code at `start`
    does - which is while the page parses when `at_once` - and the function
    declarations met on the way, as name -> (opening, closing brace), and for each token
    the bracket it sits in."""
    now, bodies, declarations, inside = {}, {}, {}, {}
    # frames: (index the frame ends at, runs at once, its opening bracket, ends without a bracket)
    frames = [(end, at_once, None, False)]
    for index in range(start, end):
        while len(frames) > 1 and frames[-1][3] and index >= frames[-1][0]:
            frames.pop()
        current, opener = frames[-1][1], frames[-1][2]
        token = tokens[index]
        now[index], inside[index] = current, opener
        if is_method_name(tokens, pairs, index):
            bodies[pairs[index + 1] + 1] = False
        elif token.kind == "name" and token.text == "function":
            # an `async function` starts at its `async`
            first = index - 1 if index > 0 and tokens[index - 1].text == "async" else index
            cursor = index + 1 + is_punct(tokens, index + 1, "*")
            name = tokens[cursor].text if cursor < len(tokens) and tokens[cursor].kind == "name" else None
            cursor += name is not None
            if not is_punct(tokens, cursor, "(") or cursor not in pairs:
                continue
            body = pairs[cursor] + 1
            if not is_punct(tokens, body, "{") or body not in pairs:
                continue
            previous = tokens[first - 1] if first > 0 else None
            if name is not None and (previous is None or previous.kind == "punct" and previous.text in ";{}"):
                bodies[body] = False
                declarations[name] = (body, pairs[body])
            else:
                bodies[body] = current and runs_at_once(tokens, pairs, first, pairs[body], opener)
        elif token.kind == "punct" and token.text == "=>":
            first = pairs.get(index - 1, index - 1) if is_punct(tokens, index - 1, ")") else index - 1
            if first > 0 and tokens[first - 1].text == "async":
                first -= 1
            if is_punct(tokens, index + 1, "{") and index + 1 in pairs:
                bodies[index + 1] = current and runs_at_once(tokens, pairs, first, pairs[index + 1], opener)
            else:
                # an expression body ends where the argument or statement it is part of does
                cursor = index + 1
                while cursor < end and not (tokens[cursor].kind == "punct" and tokens[cursor].text in ",;)]}"):
                    nested = tokens[cursor].kind == "punct" and tokens[cursor].text in "([{" and cursor in pairs
                    cursor = pairs[cursor] + 1 if nested else cursor + 1
                frames.append((cursor, current and runs_at_once(tokens, pairs, first, cursor - 1, opener), opener, True))
        elif token.kind == "punct" and token.text in "([{" and index in pairs:
            frames.append((pairs[index], bodies.get(index, current), index, False))
        elif token.kind == "punct" and token.text in ")]}" and len(frames) > 1 and not frames[-1][3] and frames[-1][0] == index:
            frames.pop()
    return now, declarations, inside


#: What the queued scripts define, under the names a page's own script reaches them by:
#: jQuery, Bootstrap, SortableJS (settings), the type-ahead widget, post_action.js and
#: page_loading.js. None of it exists yet while an inline script runs. csrfToken() is not
#: among them: base.html defines it in its first script.
DEFERRED_GLOBALS = ("$", "jQuery", "bootstrap", "Sortable", "Autocomplete", "postTo", "mcritwebPageLoading")


def reaches(tokens, index, names=DEFERRED_GLOBALS, pairs=None):
    """One of `names` as a value: not a property of something else - but a property of
    the global object, `window.jQuery` or `self["$"]`, is - not an object literal's key
    or a method's name, and not the operand of a `typeof`, which is how a library asks
    whether it is there."""
    token = tokens[index]
    if token.kind == "string":
        if token.text[1:-1] not in names or not (is_punct(tokens, index - 1, "[") and is_punct(tokens, index + 1, "]")):
            return False
        start = index - 2
        if start < 0 or tokens[start].text not in GLOBAL_OBJECT:
            return False
    elif token.kind == "name" and token.text in names:
        start = index
        if is_punct(tokens, index - 1, "."):
            if index < 2 or tokens[index - 2].text not in GLOBAL_OBJECT:
                return False
            start = index - 2
        elif is_punct(tokens, index + 1, ":") and (is_punct(tokens, index - 1, "{") or is_punct(tokens, index - 1, ",")):
            # `{ $: 1 }`
            return False
        elif pairs is not None and is_method_name(tokens, pairs, index):
            return False
    else:
        return False
    return not (start > 0 and tokens[start - 1].text == "typeof")


def parse_time_uses(source):
    """Offsets in `source` where jQuery, or anything else a deferred script defines, is
    reached while the page parses.

    `source` is the JavaScript of one page, or of one template - its inline scripts
    joined, so that a function declared in one of them and called at the top level of
    another is followed into."""
    tokens = js_tokens(without_jinja(source))
    pairs = bracket_pairs(tokens)
    _, declarations, _ = walk(tokens, pairs, 0, len(tokens), False)
    found, followed, pending = set(), set(), [(0, len(tokens))]
    while pending:
        start, end = pending.pop()
        now, _, inside = walk(tokens, pairs, start, end, True)
        for index, runs in now.items():
            if not runs:
                continue
            token = tokens[index]
            if reaches(tokens, index, pairs=pairs):
                found.add(token.position)
            elif (token.kind == "name" and token.text in declarations and token.text not in followed
                  and not is_punct(tokens, index - 1, ".") and not (index > 0 and tokens[index - 1].text == "function")
                  and (is_invoked(tokens, index + 1) or is_handed_on(tokens, index, inside[index])
                       or is_the_onload_handler(tokens, index) and is_punct(tokens, index + 1, ";"))):
                # f(), f.call(...), f.apply(...) - or f handed to a call that runs it, or
                # to a timer, which can fire before the queued scripts have run, or made
                # the window's onload
                followed.add(token.text)
                body, close = declarations[token.text]
                pending.append((body + 1, close))
    return sorted(found)


def is_handed_on(tokens, index, opener):
    """Whether the name at `index` is a whole argument of the call `opener` opens, and
    that call is not known to keep it for an event: `setTimeout(poll, 10)`, `forEach(f)`."""
    if opener is None or not is_punct(tokens, opener, "("):
        return False
    if not (is_punct(tokens, index - 1, "(") or is_punct(tokens, index - 1, ",")):
        return False
    if not (is_punct(tokens, index + 1, ")") or is_punct(tokens, index + 1, ",")):
        return False
    name = callee(tokens, opener)
    return name is not None and (name not in KEEPS_IT_FOR_LATER or listens_before_the_queue(tokens, opener))


def findings_in(code, label):
    """`label:line: text` for each place in `code` (a template or a page) where an
    inline script reaches a deferred script while the page parses."""
    joined, segments = "", []
    for offset, source in inline_scripts(code):
        segments.append((len(joined), offset))
        joined += source + "\n;\n"
    lines = code.splitlines()
    findings = []
    for position in parse_time_uses(joined):
        start, offset = max(segment for segment in segments if segment[0] <= position)
        line = code.count("\n", 0, offset + position - start) + 1
        findings.append(f"{label}:{line}: {lines[line - 1].strip()}")
    return findings


def templates():
    return sorted(TEMPLATE_ROOT.rglob("*.html"))


# --- the inline scripts -------------------------------------------------------------------

def test_no_template_reaches_a_deferred_script_while_the_page_parses():
    """One assertion over the whole tree, as in testScriptEscaping.py: the healthy
    state is no sites, and an empty parameter set would report as a skip."""
    findings = [finding for path in templates() for finding in findings_in(code_of(path), path.relative_to(TEMPLATE_ROOT))]

    assert findings == [], (
        "jQuery and the other deferred scripts have not run yet while the page parses - "
        "hand this to whenReady(function () {...}) instead (base.html, ADR-0018):\n" + "\n".join(findings))


def test_the_scan_still_sees_the_scripts():
    """A guard on the guard: if the extraction stops matching, the lint above passes on
    nothing. Most of these blocks use jQuery from a whenReady callback."""
    sources = [source for path in templates() for _, source in inline_scripts(code_of(path))]
    assert len(sources) > 25, f"only {len(sources)} inline scripts found"
    assert sum("whenReady(" in source for source in sources) > 15


def page_cases():
    return ([("anonymous", path) for path in ANONYMOUS_PAGES]
            + [("visitor", path) for path in VISITOR_PAGES]
            + [("contributor", path) for path in CONTRIBUTOR_PAGES]
            + [("admin", path) for path in ADMIN_PAGES])


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def rendered(client, make_user, as_role, role, path):
    if role == "anonymous":
        make_user("admin")
    else:
        as_role(role)
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200, path
    # both CFG pages carry a commented-out second jQuery
    return HTML_COMMENT.sub(blanked, response.get_data(as_text=True))


@pytest.mark.parametrize(("role", "path"), page_cases())
def test_no_page_reaches_a_deferred_script_while_it_parses(client, make_user, as_role, role, path):
    """The same check on what the browser receives: macros expanded, and the scripts
    flask-dropzone writes (the dropzone options, the import page's progress handlers)
    included. whenReady itself is on every page, so its body is followed into as well."""
    page = rendered(client, make_user, as_role, role, path)

    assert findings_in(page, path) == []


@pytest.mark.parametrize(("role", "path"), page_cases())
def test_no_page_has_a_script_in_head_that_blocks_the_first_paint(client, make_user, as_role, role, path):
    """The point of the change: a plain `<script src>` in <head> holds back the first
    paint until it has been fetched and run. CSS still does - it has to, for the page
    to be painted with its styles - but no script does."""
    head = HEAD.search(rendered(client, make_user, as_role, role, path)).group(1)

    blocking = [tag for _, tag, _ in external_scripts(head) if not is_deferred(tag)]
    assert blocking == []


@pytest.mark.parametrize("path", templates(), ids=lambda path: str(path.relative_to(TEMPLATE_ROOT)))
def test_no_template_puts_a_blocking_script_in_head(path):
    """The source-side twin of the check above, for the templates no test renders:
    base.html's <head>, and every page's {% block style %}, which base.html renders into it."""
    code = code_of(path)
    heads = [match.group(1) for match in HEAD.finditer(code)] + [match.group(1) for match in STYLE_BLOCK.finditer(code)]

    blocking = [tag for head in heads for _, tag, _ in external_scripts(head) if not is_deferred(tag)]
    assert blocking == []


# --- the libraries ------------------------------------------------------------------------

#: The one plain script that names jQuery, and why it may: Dropzone attaches a
#: `$.fn.dropzone` plugin if, and only if, jQuery is already there when it runs. On the
#: upload pages, where it is a plain script, jQuery is not there yet and the plugin is
#: not attached; everywhere else the drop overlay's loader inserts it after
#: DOMContentLoaded, jQuery is there, and it is. Nothing calls it either way.
NAMES_JQUERY_BUT_DOES_NOT_NEED_IT = {"dropzone.js": 'if (typeof jQuery !== "undefined" && jQuery !== null) {'}


def plain_scripts():
    """Every static file a template loads with a plain, not deferred, `<script src>`."""
    loaded = set()
    for path in templates():
        for _, tag, _ in external_scripts(code_of(path)):
            filename = STATIC_FILENAME.search(tag)
            if filename and not is_deferred(tag):
                loaded.add(filename.group(1))
    return sorted(loaded)


def names_jquery(filename):
    """Whether a script reaches for jQuery anywhere at all, as a value. Minified files
    name their own locals `$`, so for them only the full name counts - which is how a
    jQuery plugin gets hold of it, `factory(jQuery)`."""
    tokens = js_tokens((STATIC_ROOT / filename).read_text())
    names = ("jQuery",) if filename.endswith(".min.js") else ("$", "jQuery")
    return any(reaches(tokens, index, names) for index in range(len(tokens)))


def test_the_plain_scripts_are_the_expected_ones():
    """So that the check below cannot pass on an empty list."""
    assert {"dropzone.js", "trace_CFG/main.js", "trace_CFG/main_duo.js", "trace_CFG/lib/d3.min.js"} <= set(plain_scripts())


@pytest.mark.parametrize("filename", plain_scripts())
def test_a_plain_script_does_not_need_jquery(filename):
    """A plain script runs while the page parses, before the deferred jQuery. The CFG
    scripts are the bulk of these, and they do not use jQuery, although ADR-0015 counted
    two of them among its users: every `$(...)` in trace_CFG/main.js and main_duo.js is
    in a comment. A library that does use it - jQuery UI - has to be deferred, after jQuery."""
    if filename in NAMES_JQUERY_BUT_DOES_NOT_NEED_IT:
        source = (STATIC_ROOT / filename).read_text()
        start = source.index(NAMES_JQUERY_BUT_DOES_NOT_NEED_IT[filename])
        end = source.index("\n}", start)
        outside = [token for token in js_tokens(source) if token.text == "jQuery" and not start <= token.position < end]
        assert outside == [], f"{filename} names jQuery outside its guard"
        callers = [path.name for path in templates() if ".dropzone(" in code_of(path)]
        assert callers == [], f"{callers} call $.fn.dropzone, which the upload pages no longer attach"
        return
    assert not names_jquery(filename), f"{filename} uses jQuery, which loads after the first paint: queue it with later(), after jquery.js"


def test_the_library_check_would_catch_jquery_ui():
    assert names_jquery("jquery-ui.js")
    assert not names_jquery("trace_CFG/main.js")


# --- the scanner, held to what it claims --------------------------------------------------

@pytest.mark.parametrize(("source", "expected"), [
    ("$(document).ready(function(){ $('#a').hide(); });", 1),
    ("$(function(){ $('#a'); });", 1),
    ("$('#a').on('click', function(){ $(this).hide(); });", 1),
    ("var j = window.jQuery;", 1),
    ("(function(){ $('#a'); })();", 1),
    ("(function(){ $('#a'); }());", 1),
    ("(() => { $('#a'); })();", 1),
    ("!function(){ $('#a'); }();", 1),
    ("[1, 2].forEach(function(x){ $('#a'); });", 1),
    ("[1, 2].forEach(x => $('#a'));", 1),
    ("function f(){ $('#a'); }\nf();", 1),
    ("function g(){ f(); }\nfunction f(){ $('#a'); }\ng();", 1),
    ("var r = /^{|}$/g; $('#a');", 1),
    ("x = a / b; $('#a');", 1),
    ("var x = {{ data|tojson }}; $(x);", 1),
    ("{% if a %}$('#a');{% endif %}", 1),
    ("new Sortable(document.getElementById('list'), {});", 1),
    ("var tip = new bootstrap.Tooltip(element);", 1),
    ("whenReady(function(){ new Sortable(list, {}); });", 0),
    ("function copy(b){ bootstrap.Tooltip.getInstance(b).show(); }", 0),
    # a timer or a promise can fire while the deferred scripts are still on their way
    ("setTimeout(function(){ $('#a'); }, 0);", 1),
    ("requestAnimationFrame(function(){ $('#a'); });", 1),
    ("fetch(u).then(function(){ $('#a'); });", 1),
    ("Promise.resolve().then(() => $('#a'));", 1),
    ("(async function(){ await x; $('#a'); })();", 1),
    ("(async () => { await x; $('#a'); })();", 1),
    ("function poll(){ $('#a'); }\nwindow.setTimeout(poll, 10);", 1),
    ("async function load(){ $('#a'); }\nload();", 1),
    # the global object by any of its names
    ("var j = globalThis.jQuery;", 1),
    ("var j = window['jQuery'];", 1),
    ("var j = self.$;", 1),
    ("var o = { $ };", 1),
    ("function f(){ $('#a'); }\nf.call(null);", 1),
    ("function f(){ $('#a'); }\nf.apply(null, []);", 1),
    ("whenReady(function(){ $('#a').hide(); });", 0),
    ("whenReady(function(){ [1].forEach(function(){ $('#a'); }); });", 0),
    ("var r = /^{|}$/g; whenReady(function(){ $('#a'); });", 0),
    # the queue starts loading after DOMContentLoaded, and load can come before it has run
    ("document.addEventListener('DOMContentLoaded', function(){ $('#a'); });", 1),
    ("window.addEventListener('load', () => { $('#a'); });", 1),
    ("window.addEventListener('pageshow', function(){ $('#a'); });", 1),
    ("function f(){ $('#a'); }\ndocument.addEventListener('DOMContentLoaded', f);", 1),
    ("function f(){ $('#a'); }\nwindow.onload = f;", 1),
    ("el.addEventListener('click', function(){ $('#a'); });", 0),
    ("function f(){ $('#a'); }\ndocument.addEventListener('click', f);", 0),
    ("xhr.onload = function(){ $('#a'); };", 0),
    ("whenReady(function(){ setTimeout(function(){ $('#a'); }, 0); });", 0),
    # methods run when they are called, and a key is not a value
    ("var o = { run() { $('#a'); } };", 0),
    ("var o = { get x() { return $('#a'); }, set x(v) { $(v); } };", 0),
    ("var o = { async load() { $('#a'); }, *walk() { $('#b'); } };", 0),
    ("class A { m() { $('#a'); } static s() { $('#b'); } }", 0),
    ("var o = { $: 1, jQuery: 2 };", 0),
    ("if (typeof window.jQuery !== 'undefined') {}", 0),
    # job_overview.html's poll: a timer and a promise chain, and no $
    ("(function(){ function poll(){ fetch(u).then(function(r){ return r.json(); }).then(function(s){ window.setTimeout(poll, 5); }); } window.setTimeout(poll, 5); })();", 0),
    ("function f(){ $('#a'); }", 0),
    ("var f = function(){ $('#a'); };", 0),
    ("window.onload = function(){ $('#a'); };", 1),
    ("onload = function(){ $('#a'); };", 1),
    ("new Widget(f, {onInput: function(){ $('#a'); }});", 0),
    ("new Autocomplete(f, {});", 1),
    ("if (typeof jQuery !== 'undefined') {}", 0),
    ("var s = '$(document).ready(';", 0),
    ("var t = `${a} $('x') ${ {b: 1}.b }`;", 0),
    ("obj.$('#a');", 0),
    ("// $('#a')\n/* $('#b') */", 0),
])
def test_the_scanner_tells_now_from_later(source, expected):
    assert len(parse_time_uses(source)) == expected


def test_a_finding_names_its_line():
    page = "<p>\n<script>\n  var a = 1;\n  $(document).ready(function(){});\n</script>"
    assert findings_in(page, "page") == ["page:4: $(document).ready(function(){});"]
