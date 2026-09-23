#!/usr/bin/python
"""No script in this repository's own markup assigns to an undeclared name.

An assignment with no `var`, `let` or `const` creates a property on `window`. Two
pages, or two script blocks on one page, then share a variable neither of them meant
to - and `families_ac` and `myDropzone` really are written from two different blocks
that land on the same page. Issue #61.

This is a ratchet: the list below is empty and may only stay empty. Adding a name to
it is a regression, not a note.

Scope is deliberately this repository's own JavaScript - the inline blocks in
`templates/` and `static/post_action.js`. The vendored libraries under `static/`
(jQuery, DataTables, Dropzone, ...) carry their own licences and are not ours to
reformat; see AGENTS.md.
"""

import logging
import pathlib
import re
import unittest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

ROOT = pathlib.Path(__file__).resolve().parents[1] / "mcritweb"

#: Names that are properties of the browser or of a library, not variables of ours.
NOT_OURS = {"window", "document", "this", "self"}

#: An assignment that starts a line: `name = value`, but not `name == value`,
#: `name => ...`, `a.name = ...` (the regex is anchored) or `name: value`.
ASSIGNMENT = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*=\s*(?![=>])")


def javascript_sources():
    """(label, text) for every piece of JavaScript this repository wrote."""
    for path in sorted((ROOT / "templates").rglob("*.html")):
        text = path.read_text()
        in_script = False
        block = []
        for number, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            if "</script" in lowered:
                in_script = False
                continue
            if in_script:
                block.append((number, line))
            if "<script" in lowered and "src=" not in lowered:
                in_script = True
        if block:
            yield path, text, block

    post_action = ROOT / "static" / "post_action.js"
    text = post_action.read_text()
    yield post_action, text, list(enumerate(text.splitlines(), 1))


#: A binding form that introduces a name.
DECLARATION = re.compile(r"\b(?:var|let|const|function|class)\s+([A-Za-z_$][\w$]*)")

#: A parameter list, named or anonymous.
FUNCTION_PARAMETERS = re.compile(r"\bfunction\b\s*[A-Za-z_$][\w$]*\s*\(([^)]*)\)|\bfunction\b\s*\(([^)]*)\)")
ARROW_PARAMETERS = re.compile(r"\(([^)]*)\)\s*=>|\b([A-Za-z_$][\w$]*)\s*=>")

#: `for (name in ...)` / `for (name of ...)`, which binds for the loop body.
LOOP_BINDING = re.compile(r"for\s*\(\s*([A-Za-z_$][\w$]*)\s+(?:in|of)\b")


def brace_regions(text):
    """(start, end) for every `{ ... }` in the source, strings and comments skipped.

    Not a parser. It exists so a declaration can be attributed to a *scope* rather than
    to the file, and the two things it must not do are miscount a brace inside a string
    literal and choke on a regex containing one. It is deliberately conservative:
    anything it cannot attribute falls back to file scope, which is the old behaviour.
    """
    depth, open_at, regions = 0, [], []
    index, length = 0, len(text)
    while index < length:
        character = text[index]
        if character in "\"'`":
            quote = character
            index += 1
            while index < length and text[index] != quote:
                index += 2 if text[index] == "\\" else 1
        elif character == "/" and index + 1 < length and text[index + 1] == "/":
            while index < length and text[index] != "\n":
                index += 1
        elif character == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index)
            index = length if end < 0 else end + 1
        elif character == "{":
            depth += 1
            open_at.append(index)
        elif character == "}":
            if open_at:
                regions.append((open_at.pop(), index))
            depth = max(0, depth - 1)
        index += 1
    return regions


def declaration_scopes(name, text):
    """Where `name` is bound. `None` stands for the top level of this source.

    Searching the whole file for a `var` - which is what this used to do - clears an
    assignment in one scope because of a declaration in a completely different one. A
    leak inside `function a()` was hidden by a `var` of the same name inside
    `function b()`, which is exactly the class of bug issue #61 is about, so the ratchet
    was weaker than it claimed. A declaration now only clears an assignment that falls
    inside the same braces.
    """
    regions = brace_regions(text)

    def innermost(position):
        enclosing = [(start, end) for start, end in regions if start < position < end]
        return min(enclosing, key=lambda region: region[1] - region[0]) if enclosing else None

    scopes = []
    for match in DECLARATION.finditer(text):
        if match.group(1) == name:
            scopes.append(innermost(match.start()))
    for match in LOOP_BINDING.finditer(text):
        if match.group(1) == name:
            scopes.append(innermost(match.start()))
    # a parameter binds in the body that follows its list, not in the enclosing scope
    for pattern in (FUNCTION_PARAMETERS, ARROW_PARAMETERS):
        for match in pattern.finditer(text):
            parameters = next((group for group in match.groups() if group is not None), "")
            names = [p.strip().split("=")[0].strip().lstrip(".") for p in parameters.split(",")]
            if name not in names:
                continue
            body = text.find("{", match.end())
            body_region = next((region for region in regions if region[0] == body), None)
            scopes.append(body_region if body_region else innermost(match.start()))
    return scopes


def is_declared_for(name, text, position):
    """Whether a declaration of `name` is in scope at `position`."""
    return any(
        scope is None or scope[0] < position < scope[1]
        for scope in declaration_scopes(name, text)
    )


def line_offsets(text):
    offsets, position = {}, 0
    for number, line in enumerate(text.splitlines(), 1):
        offsets[number] = position
        position += len(line) + 1
    return offsets


def undeclared_assignments():
    findings = []
    for path, text, block in javascript_sources():
        offsets = line_offsets(text)
        for number, line in block:
            match = ASSIGNMENT.match(line)
            if not match:
                continue
            name = match.group(1)
            if name in NOT_OURS or is_declared_for(name, text, offsets[number]):
                continue
            findings.append(f"{path.relative_to(ROOT.parent)}:{number}: {line.strip()}")
    return findings


def test_nothing_assigns_to_an_undeclared_name():
    findings = undeclared_assignments()

    assert findings == [], "undeclared assignment(s):\n" + "\n".join(findings)


def test_the_scanner_reads_something():
    """A scanner that silently matches no files would pass forever."""
    sources = list(javascript_sources())

    assert len(sources) > 5, f"only found {len(sources)} sources of JavaScript"


def at(text, needle):
    """The offset of `needle`, so a scope test can name a position by what is there."""
    position = text.find(needle)
    assert position >= 0, f"{needle!r} is not in the sample"
    return position


def test_the_scanner_would_catch_one():
    """And a scanner that cannot fail is not a ratchet."""
    text = "function f() {\n  leaked = 1;\n}"

    assert not is_declared_for("leaked", text, at(text, "leaked = 1"))
    assert ASSIGNMENT.match("  leaked = 1;")


def test_a_declaration_in_another_scope_does_not_clear_a_leak():
    """The reason this scanner reads scopes at all. Searching the whole source for a
    `var` clears an assignment because of a binding it can never see - and a leak
    hidden by an unrelated same-named local is precisely the bug issue #61 is about."""
    text = "function a() {\n  shared = 1;\n}\nfunction b() {\n  var shared;\n}"

    assert not is_declared_for("shared", text, at(text, "shared = 1"))


def test_a_declaration_in_an_enclosing_scope_does_clear_it():
    text = "var shared;\nfunction a() {\n  shared = 1;\n}"

    assert is_declared_for("shared", text, at(text, "shared = 1"))


def test_a_parameter_is_declared_in_the_body_that_follows_it():
    """`function fill_form(data) { data = JSON.parse(data); }` is legal and common; the
    real markup does exactly this."""
    text = "function fill_form(data) {\n  data = JSON.parse(data);\n}"

    assert is_declared_for("data", text, at(text, "data = JSON"))


def test_a_parameter_does_not_declare_anything_outside_its_own_body():
    text = "function f(value) {}\nfunction g() {\n  value = 1;\n}"

    assert not is_declared_for("value", text, at(text, "value = 1"))


def test_a_brace_inside_a_string_does_not_shift_the_scopes():
    """The region scanner is not a parser, so this is the failure it is most likely to
    have: one unbalanced brace in a string literal and every scope after it is wrong."""
    text = 'function a() {\n  var label = "} {";\n  ok = 1;\n}\nfunction b() {\n  var ok;\n}'

    assert not is_declared_for("ok", text, at(text, "ok = 1"))


def test_the_scanner_does_not_flag_ordinary_code():
    for line, text in [
        ("  const x = 1;", "const x = 1;"),
        ("  x = 1;", "let x;\nx = 1;"),
        ("  x = 1;", "function g(x) {\n  x = 1;\n}"),
        ("  i = 1;", "for (i of things) {\n  i = 1;\n}"),
    ]:
        match = ASSIGNMENT.match(line)
        name = match.group(1) if match else None
        assert name is None or is_declared_for(name, text, at(text, line.strip().rstrip(";"))), f"{line!r} against {text!r}"


if __name__ == "__main__":
    unittest.main()
