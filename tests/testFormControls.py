#!/usr/bin/python
"""Every check box and radio button is a Bootstrap form-check, and the filter forms are built
from templates/table/filter_fields.html's macros.

The result pages, the link hunt and the settings page laid their check boxes out as grid
cells, `<input class="col-1" type="checkbox">` and a `<label class="col-3">`, which stretched a
box to a twelfth of the row and set it a column away from its label; their Filter buttons sat
in the row bare, stretched across it on two pages. The edit dialogs spaced a bare box from its
label with `&nbsp;&nbsp;`, and the CFG viewer used the browser's own boxes. Now each box is a
`form-check-input` that style.css sizes from --control-check-size, beside a label that names it.
tests/testFormControlsBrowser.py measures the result in Chromium; this module holds the markup
where CI can, since CI has no browser.
"""

import re
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of
from themeCss import TEMPLATES

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Controls(HTMLParser):
    """The check boxes and radio buttons of a page, each with the classes of its ancestors,
    the labels by their `for`, and the children of every filter form's row."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.stack, self.boxes, self.labels, self.row_children = [], [], {}, []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        parent = self.stack[-1] if self.stack else None
        if parent and "row" in parent[1] and any("form-inline" in c for _, c in self.stack):
            self.row_children.append((tag, classes, attrs.get("type")))
        if tag == "input" and attrs.get("type") in ("checkbox", "radio"):
            self.boxes.append({"attrs": attrs, "classes": classes, "ancestors": [c for _, c in self.stack]})
        if tag == "label" and attrs.get("for"):
            self.labels[attrs["for"]] = classes
        if tag not in VOID:
            self.stack.append((tag, classes))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


#: the pages with check boxes or radio buttons, as a contributor sees them
PAGES = [
    f"/data/result/{job_id_of('matches_for_sample')}",
    f"/data/result/{job_id_of('matches_for_sample')}?famid=1",
    f"/data/result/{job_id_of('matches_for_query')}",
    f"/data/result/{job_id_of('matches_for_sample_vs')}",
    f"/data/result/{job_id_of('matches_for_sample_vs')}?samid=3",
    f"/data/result/{job_id_of('matches_for_sample_vs')}?funid=880",
    f"/data/result/{job_id_of('cross_compare')}",
    f"/data/linkhunt/{job_id_of('matches_for_sample')}",
    "/settings",
    "/explore/samples",
    "/explore/families",
    "/explore/samples/0",
    "/explore/functions/84",
    "/explore/search?query=citadel&type=sample",
    "/analyze/compare",
    "/analyze/cross_compare",
    "/data/submit",
    "/data/matches/function/84/943",
]
PAGE_IDS = [re.sub(r"[0-9a-f]{24}", "<job>", path) for path in PAGES]

#: a filter form's row holds the macros' cells and hidden fields, nothing else
FILTER_CELLS = {"filter-field", "filter-checks", "filter-actions"}


@pytest.fixture
def page(client, as_role):
    as_role("contributor")

    def render(path):
        response = client.get(path)
        assert response.status_code == 200, path
        return Controls(response.get_data(as_text=True))

    return render


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_every_check_box_is_a_form_check_and_none_is_a_grid_cell(page, path):
    controls = page(path)

    assert controls.boxes, f"{path} renders no check box or radio button"
    for box in controls.boxes:
        name = box["attrs"].get("id") or box["attrs"].get("name")
        assert "form-check-input" in box["classes"], (path, name, box["classes"])
        assert not [c for c in box["classes"] if re.fullmatch(r"col(-\w+)*", c)], (path, name, "a grid cell")
        assert "style" not in box["attrs"], (path, name, "spaced by a style of its own")


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_a_labelled_box_sits_in_a_form_check_with_a_form_check_label(page, path):
    """A box with a label beside it is laid out by .form-check, which keeps the box at the
    label's first line; the picker tables' boxes have no label and are left to their cells."""
    controls = page(path)

    for box in controls.boxes:
        box_id = box["attrs"].get("id")
        wrapped = any("form-check" in classes for classes in box["ancestors"][-1:])
        labelled = box_id in controls.labels or any("form-check" in classes for classes in box["ancestors"])
        if not labelled:
            continue
        assert wrapped, (path, box_id, "a labelled box outside a .form-check")
        if box_id in controls.labels:
            assert "form-check-label" in controls.labels[box_id], (path, box_id, controls.labels[box_id])


#: the pages with a filter form; the settings page's has its headings in the row as well
FILTER_PAGES = [path for path in PAGES if "/data/linkhunt/" in path or ("/data/result/" in path and job_id_of("cross_compare") not in path)]


@pytest.mark.parametrize("path", FILTER_PAGES, ids=[re.sub(r"[0-9a-f]{24}", "<job>", path) for path in FILTER_PAGES])
def test_a_filter_form_row_holds_only_the_macros_cells(page, path):
    controls = page(path)

    assert controls.row_children, f"{path} has no filter form"
    stray = [(tag, classes) for tag, classes, kind in controls.row_children
             if kind != "hidden" and not FILTER_CELLS & set(classes)]
    assert stray == [], f"{path}: a control in the row that no macro laid out"


def test_the_default_filter_form_row_holds_only_the_macros_cells(page):
    controls = page("/settings")
    stray = [(tag, classes) for tag, classes, kind in controls.row_children
             if kind != "hidden" and tag != "h2" and not FILTER_CELLS & set(classes)]
    assert stray == []


@pytest.mark.parametrize("checked", [True, False], ids=["ticked", "not ticked"])
def test_a_filter_box_sends_what_the_view_reads_and_shows_the_filter_in_force(client, as_role, checked):
    """No value attribute: a ticked box sends "on", which parse_checkbox_query_param reads,
    and the box comes back showing the filter the page applied."""
    as_role("contributor")
    query = {"filter_button_action": "filter", **({"filter_exclude_pic": "on"} if checked else {})}
    html = client.get(f"/data/result/{job_id_of('matches_for_sample')}", query_string=query).get_data(as_text=True)

    boxes = [b["attrs"] for b in Controls(html).boxes if b["attrs"].get("name") == "filter_exclude_pic"]
    assert boxes and all("value" not in b for b in boxes)
    assert all(("checked" in b) == checked for b in boxes)


TEMPLATE_BOX = re.compile(r"<input\b[^>]*\btype=[\"'](?:checkbox|radio)[\"'][^>]*>")


@pytest.mark.parametrize("template", sorted(p.relative_to(TEMPLATES).as_posix() for p in TEMPLATES.rglob("*.html")))
def test_no_template_writes_a_box_as_a_grid_cell_or_with_its_own_spacing(template):
    """The rendered pages above miss a template no fixture reaches."""
    source = (TEMPLATES / template).read_text(encoding="utf-8")
    source = re.sub(r"<!--.*?-->", "", source, flags=re.S)

    for box in TEMPLATE_BOX.findall(source):
        assert "form-check-input" in box, (template, box)
        assert not re.search(r"class=[\"'][^\"']*\bcol-\d", box), (template, box)
        assert "style=" not in box, (template, box)


def test_the_home_page_tables_are_not_held_to_two_thirds_inline(client, as_role):
    """Two thirds of a phone squeezed the job table's Type column to four letters a line;
    style.css gives the class two thirds from 1200px only."""
    as_role("contributor")
    html = client.get("/").get_data(as_text=True)

    assert 'class="table table-hover table-half"' in html
    assert "width: 66%" not in html


def test_the_settings_tab_bar_starts_at_the_gutter_with_its_panes(client, as_role):
    as_role("contributor")
    html = client.get("/settings").get_data(as_text=True)
    bar = re.search(r'<ul class="([^"]*)" id="pills-tab"', html)

    assert bar and "justify-content-center" not in bar.group(1).split()
