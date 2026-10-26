#!/usr/bin/python
"""A comment in an inline script is sent with every copy of the page.

The page is gzipped, but a comment is prose, which compresses worse than the markup around
it, and a table template is rendered once per page, not once per file the browser caches.
The explanation of a script belongs in a Jinja comment, `{# ... #}`, which the template
engine drops, or in a static file, which the browser fetches once.

This is a ratchet: the templates below are the ones that carried JavaScript comment lines
when the comments were counted, with how many, and no template may carry more than its
count. Moving a script to a static file or turning its comments into Jinja ones lowers a
count; do lower it here.
"""

import pathlib

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "mcritweb" / "templates"

#: template -> the lines of its inline scripts that are only a `//` comment
COMMENT_LINES = {
    "admin_server.html": 2, "compare.html": 8, "compare_versus.html": 9, "cross_compare.html": 20,
    "job_overview.html": 7, "jobs.html": 6, "result_compare_function_vs.html": 17,
    "result_cross.html": 22, "result_unique_blocks.html": 13, "settings.html": 8,
    "single_function.html": 10, "single_sample.html": 5, "table/column_table.html": 2,
    "table/family_row.html": 8, "table/function_row.html": 1, "table/links.html": 2,
    "table/sample_row.html": 6, "table/table.html": 3, "unique_blocks.html": 19,
}


def comment_lines(text):
    """The lines inside an inline <script> that start with //."""
    found = 0
    inside = False
    for line in text.split("\n"):
        lowered = line.lower()
        if "</script" in lowered:
            inside = False
        elif "<script" in lowered and "src=" not in lowered and "application/json" not in lowered:
            inside = True
        elif inside and line.strip().startswith("//"):
            found += 1
    return found


def test_no_template_sends_more_script_comments_than_it_did():
    over = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        name = path.relative_to(TEMPLATES).as_posix()
        found = comment_lines(path.read_text(encoding="utf8"))
        if found > COMMENT_LINES.get(name, 0):
            over[name] = (found, COMMENT_LINES.get(name, 0))
    assert over == {}, f"JavaScript comment lines in an inline script, now and allowed; use a Jinja comment: {over}"


def test_the_counter_sees_a_comment_in_an_inline_script():
    page = "<script>\n  // why\n  run();\n</script>\n<script src=\"x.js\"></script>\n// not in a script\n<script>later('a')</script>"
    assert comment_lines(page) == 1
    assert comment_lines("<script nonce=\"x\">\n// a\n// b\n</script>") == 2
