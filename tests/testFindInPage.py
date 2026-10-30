#!/usr/bin/python
"""Find in page reaches the closed panels of an accordion, where the browser can.

A closed Bootstrap panel is display: none, which the browser's search skips, so a
PicBlockHash looked for with Ctrl+F on a function's page was not found until its panel was
opened. static/collapsed_find.js marks closed panels hidden="until-found" where the browser
supports it and opens a panel a search lands in; style.css undoes Bootstrap's display: none
for marked panels only. tests/testPageStructureBrowser.py drives it; this keeps CI on the
wiring. The tab panes are left alone: a find landing in one would switch the tab under the
reader, and the cross compare's panes are empty until first shown.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "mcritweb"
QUEUED = re.compile(r"<script nonce=\"\{\{ csp_nonce\(\) \}\}\">later\(\{\{ url_for\('static', filename='collapsed_find\.js'\)\|tojson \}\}\)</script>")


def test_every_page_with_a_closed_panel_queues_the_script():
    with_panels = [path.name for path in (ROOT / "templates").rglob("*.html")
                   if re.search(r'class="accordion-collapse collapse"', path.read_text())]

    assert sorted(with_panels) == ["single_function.html", "single_query_sample.html"]
    for name in with_panels:
        assert QUEUED.search((ROOT / "templates" / name).read_text()), name


def test_only_a_browser_with_until_found_marks_the_panels():
    """Without it, hidden="until-found" is plain hidden, which the open panel would keep."""
    script = (ROOT / "static" / "collapsed_find.js").read_text()

    assert re.search(r'if \(!\("onbeforematch" in HTMLElement\.prototype\)\) \{\s*return;', script)


def test_a_marked_panel_is_displayed_for_the_search_to_reach():
    css = (ROOT / "static" / "style.css").read_text()

    rule = re.search(r'\.accordion-collapse\[hidden="until-found"\]\s*\{([^}]*)\}', css)
    assert rule and "display: block !important" in rule.group(1)
