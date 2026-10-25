#!/usr/bin/python
"""Yes/no values drawn as icons carry a word for assistive technology.

A Font Awesome icon is a private-use character drawn from `content:`; a screen reader
reads it as nothing or as "unknown character". Where a tick or a cross is the whole value
of a cell - library or not, MinHash or PicHash match, unique or not - the cell was empty to
it. `status_icon` in `table/links.html` hides the glyph and puts the word beside it. The
link hunt's family-unique matches were marked by bold type alone.
"""

import pytest
from fixtureData import job_id_of
from testAccessibility import CONTRIBUTOR_PAGES, VISITOR_PAGES, Page

#: the pages with yes/no cells: the lists, the pickers, a sample's and a function's page,
#: and every kind of result table
STATUS_PAGES = [path for path in VISITOR_PAGES + CONTRIBUTOR_PAGES if path != "/help"]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def icon_only_cells(html):
    """The table cells whose value is an icon with no text a screen reader can reach. An
    icon inside a link or a button is that control's label, and testAccessibility.py
    checks that the control is named. The pickers' info icon is a hover tooltip's anchor
    (`data-hint`), whose text is the tooltip's business, not a value of its own."""
    page = Page(html)
    found = []
    for cell in page.all("td"):
        icons = [element for element in page.elements if element.tag == "i" and _inside(element, cell)
                 and "fa-" in (element.attrs.get("class") or "") and not _in_control(element, cell)]
        if icons and not cell.text_content().strip():
            found.append(icons[0].attrs.get("class"))
    return found


def _inside(element, ancestor):
    node = element.parent
    while node is not None:
        if node is ancestor:
            return True
        node = node.parent
    return False


def _in_control(element, cell):
    node = element.parent
    while node is not None and node is not cell:
        if node.tag in ("a", "button") or "data-hint" in node.attrs:
            return True
        node = node.parent
    return False


@pytest.mark.parametrize("path", STATUS_PAGES)
def test_no_cell_is_an_icon_alone(client, as_role, path):
    as_role("contributor")
    response = client.get(path, follow_redirects=path == "/data/jobs")
    assert response.status_code == 200

    assert icon_only_cells(response.get_data(as_text=True)) == []


def test_the_checker_sees_an_icon_alone():
    """Without this, a parser that found no cells would pass every page above."""
    bare = '<table><tr><td><i class="fa-solid fa-square-check"></i></td></tr></table>'
    worded = ('<table><tr><td><i class="fa-solid fa-square-check" aria-hidden="true"></i>'
              '<span class="visually-hidden">Yes</span></td></tr></table>')
    hidden = '<table><tr><td><i class="fa-solid fa-square-check" aria-hidden="true"></i></td></tr></table>'

    assert icon_only_cells(bare) == ["fa-solid fa-square-check"]
    assert icon_only_cells(hidden) == ["fa-solid fa-square-check"]
    assert icon_only_cells(worded) == []


@pytest.mark.parametrize("value, word, icon", [(True, "Yes", "fa-square-check"), (False, "No", "fa-times-circle")])
def test_the_status_icon_says_yes_or_no(app, value, word, icon):
    source = "{% from 'table/links.html' import status_icon %}{{ status_icon(value) }}"
    with app.test_request_context("/"):
        rendered = app.jinja_env.from_string(source).render(value=value)
    page = Page(rendered)

    glyph = page.all("i")[0]
    assert icon in glyph.attrs["class"] and glyph.attrs.get("aria-hidden") == "true"
    assert [span.text for span in page.all("span") if "visually-hidden" in span.classes()] == [word]


def test_a_family_unique_link_says_so_beside_the_bold(client, as_role):
    """"Bold matches are also unique with this other family": a screen reader does not
    announce bold, so the word goes with it."""
    as_role("visitor")
    page = Page(client.get(f"/data/linkhunt/{job_id_of('matches_for_sample')}").get_data(as_text=True))

    bold = page.all("b")
    assert bold, "the fixture's link hunt no longer has a family-unique match"
    for marked in bold:
        assert "(unique)" in marked.text_content(), marked.attrs
        assert [span for span in marked.children if "visually-hidden" in span.classes()]
