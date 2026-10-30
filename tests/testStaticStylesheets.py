#!/usr/bin/python
"""The project's own stylesheets are served without their comments.

AGENTS.md wants the reason for a rule written next to it, so style.css and theme-dark.css are
full of comments, and a stylesheet is what the first paint waits for: on the merged branches
style.css was 15 KB gzipped, two thirds of it comments, and fourteen pages lost
Performance points on a phone. mcritweb/static_cache.py serves STRIPPED_STYLESHEETS with the
comments removed and leaves the files, and the vendored stylesheets, as they are.
"""

import gzip
import os
import pathlib
import re

import pytest

from mcritweb import static_cache
from mcritweb.static_cache import strip_comments

STATIC = pathlib.Path(__file__).resolve().parents[1] / "mcritweb" / "static"
STYLESHEETS = ["style.css", "theme-dark.css", "navbar.css", "signin.css"]


def outside_strings(css):
    """The stylesheet with its quoted strings and url(...) emptied out, so that what is left
    is the text the CSS parser reads as rules and comments. Written apart from
    strip_comments, with a regular expression, so that the two do not share a mistake."""
    token = re.compile(r'"(?:\\.|[^"\\\n])*"?|\'(?:\\.|[^\'\\\n])*\'?|(?<![\w-])url\((?!\s*["\'])(?:\\.|[^)\\])*\)|\\.|/\*.*?(?:\*/|\Z)', re.S | re.I)
    return token.sub(lambda m: m.group(0) if m.group(0).startswith("/*") else "\0", css)


def without_comments(css):
    return re.sub(r"/\*(?!!).*?(?:\*/|\Z)", "", outside_strings(css), flags=re.S)


def test_the_served_body_is_the_scanner_applied_to_the_file(client):
    for name in STYLESHEETS:
        served = client.get(f"/static/{name}")
        assert served.status_code == 200, name
        assert served.mimetype == "text/css", name
        assert served.get_data(as_text=True) == strip_comments((STATIC / name).read_text()), name


def test_each_stylesheet_that_has_comments_is_smaller(client):
    for name in STYLESHEETS:
        source = (STATIC / name).read_bytes()
        served = client.get(f"/static/{name}").data
        if re.search(rb"/\*(?!!)", source):
            assert len(served) < len(source), name
        else:
            assert served == source, name
    assert len(client.get("/static/style.css").data) < 0.6 * (STATIC / "style.css").stat().st_size
    assert len(client.get("/static/theme-dark.css").data) < 0.8 * (STATIC / "theme-dark.css").stat().st_size


@pytest.mark.parametrize("name", STYLESHEETS)
def test_no_comment_is_left_but_the_ones_marked_with_a_bang(name):
    stripped = strip_comments((STATIC / name).read_text())
    assert not re.search(r"/\*(?!!)", outside_strings(stripped)), name


@pytest.mark.parametrize("name", STYLESHEETS)
def test_the_rules_are_the_same_outside_the_comments(name):
    """The braces and semicolons the parser reads are the ones the file had, so no rule
    was cut and no comment's text was kept as one."""
    source = (STATIC / name).read_text()
    stripped = strip_comments(source)
    before, after = without_comments(source), without_comments(stripped)
    for mark in "{};:,":
        assert after.count(mark) == before.count(mark), (name, mark)
    assert re.sub(r"\s+", "", after) == re.sub(r"\s+", "", before)


def test_only_the_comments_go():
    css = (
        '/* head */\n'
        'a { /* inline */ color: red; /* tail */ }\n'
        '  /* alone\n   on two lines */\n'
        'b::before { content: "/* not a comment */"; }\n'
        "i::after { content: '/*'; }\n"
        'u { background: url(data:image/svg+xml;utf8,<svg a="/*"/>) }\n'
        'v { background: url("a/*b.png") ; background: url( a/*b.png ) }\n'
        'w { margin: 1px/**/2px; width:calc(1px/**/+ 2px) }\n'
        '/*! licence */\n'
        'x { content: "\\"/*"; }\n'
        'y { content: "a\\\n/*" }\n'
        'z\\/* { top: 0 }\n'
        '/* the last one is never closed'
    )
    stripped = strip_comments(css)
    assert stripped == (
        'a {  color: red;  }\n'
        'b::before { content: "/* not a comment */"; }\n'
        "i::after { content: '/*'; }\n"
        'u { background: url(data:image/svg+xml;utf8,<svg a="/*"/>) }\n'
        'v { background: url("a/*b.png") ; background: url( a/*b.png ) }\n'
        'w { margin: 1px 2px; width:calc(1px+ 2px) }\n'
        '/*! licence */\n'
        'x { content: "\\"/*"; }\n'
        'y { content: "a\\\n/*" }\n'
        'z\\/* { top: 0 }\n'
    )


@pytest.fixture
def scratch_folder(app, tmp_path, monkeypatch):
    """A copy of the stylesheet to change: the real one is shared with the other tests."""
    (tmp_path / "signin.css").write_bytes((STATIC / "signin.css").read_bytes())
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    return tmp_path / "signin.css"


def test_a_string_with_a_comment_in_it_survives_in_a_served_file(client, scratch_folder):
    scratch_folder.write_bytes(scratch_folder.read_bytes() + b'\n/* gone */\n.probe::before { content: "/* kept */"; }\n')
    body = client.get("/static/signin.css").get_data(as_text=True)
    assert 'content: "/* kept */"' in body and "gone" not in body


def test_vendored_stylesheets_are_served_as_they_are(client):
    for name in ["css/bootstrap-subset.css", "css/fontawesome-subset.css", "hint.css", "dropzone.min.css", "jquery-ui.css"]:
        assert client.get(f"/static/{name}").data == (STATIC / name).read_bytes(), name
    assert not any("/" in name or name.startswith(("bootstrap", "fontawesome", "hint", "dropzone", "jquery")) for name in static_cache.STRIPPED_STYLESHEETS)


def test_the_etag_names_the_body_that_is_sent_and_a_changed_file_changes_it(client, scratch_folder):
    first = client.get("/static/signin.css")
    assert first.headers["ETag"] and first.headers["Last-Modified"]
    assert int(first.headers["Content-Length"]) == len(first.data)
    original = scratch_folder.read_bytes()
    scratch_folder.write_bytes(original + b"\n.probe { top: 1px }\n")
    changed = client.get("/static/signin.css")
    assert b".probe" in changed.data
    assert changed.headers["ETag"] != first.headers["ETag"]
    # the file is edited again to the same size, later: the stamp is the modification time
    scratch_folder.write_bytes(original + b"\n.probe { top: 2px }\n")
    os.utime(scratch_folder, ns=(0, scratch_folder.stat().st_mtime_ns + 7_000_000_000))
    assert b"top: 2px" in client.get("/static/signin.css").data


def test_the_stripped_body_is_made_once_per_version(client, monkeypatch):
    client.get("/static/style.css")
    calls = []
    real = static_cache.strip_comments
    monkeypatch.setattr(static_cache, "strip_comments", lambda css: calls.append(1) or real(css))
    for _ in range(3):
        client.get("/static/style.css")
    assert calls == []


def test_a_stylesheet_is_revalidated_with_the_etag_of_what_was_sent(client):
    plain = client.get("/static/style.css")
    assert client.get("/static/style.css", headers={"If-None-Match": plain.headers["ETag"]}).status_code == 304
    zipped = client.get("/static/style.css", headers={"Accept-Encoding": "gzip"})
    assert zipped.headers["Content-Encoding"] == "gzip" and zipped.headers["ETag"].endswith('-gzip"')
    assert int(zipped.headers["Content-Length"]) == len(zipped.data)
    assert gzip.decompress(zipped.data) == plain.data
    again = client.get("/static/style.css", headers={"Accept-Encoding": "gzip", "If-None-Match": zipped.headers["ETag"]})
    assert again.status_code == 304


def test_the_cache_lifetimes_are_the_same_as_for_any_static_file(client, as_role):
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    for name in ["style.css", "navbar.css"]:
        href = re.search(rf'href="(/static/{name}\?v=\w+)"', page).group(1)
        versioned = client.get(href)
        assert {d.strip() for d in versioned.headers["Cache-Control"].split(",")} == {"public", "max-age=31536000", "immutable"}, name
        assert {d.strip() for d in client.get(f"/static/{name}").headers["Cache-Control"].split(",")} == {"public", "max-age=3600"}, name
    stale = client.get("/static/style.css?v=000000000000")
    assert "immutable" not in stale.headers["Cache-Control"]
