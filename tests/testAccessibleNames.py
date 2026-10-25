#!/usr/bin/python
"""A control's name says which row it acts on.

An icon-only link or button is named by its `aria-label`, or by its `title`. Every row of
a match table carried the same "Filter by this function", every job row the same "Delete
job" and "Show result", every copy button "Copy to Clipboard": a list of links, or a walk
with Tab, read out a hundred identical entries that each go somewhere else. Two controls
may share a name only when they do the same thing.

Not checked here: the numbered pagination links, which repeat per table, and links named
by the id they show, which the column header around them qualifies.
"""

import collections

import pytest
from testAccessibility import ADMIN_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES, Page


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def what_it_does(control):
    """Where a link goes, or what a button acts on. A dialog's opener says what it opens
    the dialog for in its other data-bs- attributes, the sample an edit button edits; a
    button whose handler finds its target from where it sits, `deleteSample(this)`, acts
    on its own row."""
    for attribute in ("href", "data-post", "data-clipboard-value"):
        if control.attrs.get(attribute):
            return control.attrs[attribute]
    if control.attrs.get("data-bs-target"):
        return tuple(sorted((name, value) for name, value in control.attrs.items()
                            if name.startswith("data-bs-") and name != "data-bs-toggle"))
    return id(control)


def left_out(control):
    """A pagination link, or a control in a tab pane not shown: the same user's menu sits
    in the "All users" pane and in the one for their role, never both on screen."""
    node = control.parent
    while node is not None:
        classes = node.classes()
        if "pagination" in classes or ("tab-pane" in classes and "active" not in classes):
            return True
        node = node.parent
    return False


def shared_names(html):
    """{name: number of different targets} for the labelled controls whose name repeats."""
    targets = collections.defaultdict(set)
    for control in Page(html).all("a", "button"):
        # a closed modal's controls are not reachable while another's are; a menu's items
        # are named by their text, which the menu's own toggle qualifies
        if control.in_hidden_container() or left_out(control):
            continue
        name = (control.attrs.get("aria-label") or "").strip()
        if not name and control.tag == "button" and not control.text_content().strip():
            name = (control.attrs.get("title") or "").strip()
        if name:
            targets[name].add(what_it_does(control))
    return {name: len(found) for name, found in targets.items() if len(found) > 1}


@pytest.mark.parametrize("role, path", [("visitor", path) for path in VISITOR_PAGES]
                         + [("contributor", path) for path in CONTRIBUTOR_PAGES]
                         + [("admin", path) for path in ADMIN_PAGES])
def test_no_two_labelled_controls_share_a_name_and_differ_in_what_they_do(client, as_role, role, path):
    as_role(role)
    response = client.get(path, follow_redirects=path == "/data/jobs")
    assert response.status_code == 200

    assert shared_names(response.get_data(as_text=True)) == {}


def test_the_checker_sees_a_shared_name():
    """Without this, a page with no labelled controls would pass the test above."""
    page = ('<a aria-label="Filter" href="?funid=1"></a><a aria-label="Filter" href="?funid=2"></a>'
            '<a aria-label="Same" href="/x"></a><a aria-label="Same" href="/x"></a>'
            '<button title="Copy" data-clipboard-value="a"></button><button title="Copy" data-clipboard-value="b"></button>'
            '<button aria-label="Remove" onclick="drop(this)"></button><button aria-label="Remove" onclick="drop(this)"></button>'
            '<a aria-label="Edit" data-bs-target="#edit" data-bs-sampleid="1"></a><a aria-label="Edit" data-bs-target="#edit" data-bs-sampleid="2"></a>'
            '<a aria-label="API" data-bs-target="#api"></a><a aria-label="API" data-bs-target="#api"></a>'
            '<ul class="pagination"><li><a aria-label="Next page" href="?p=2"></a></li></ul>'
            '<ul class="pagination"><li><a aria-label="Next page" href="?q=2"></a></li></ul>')

    assert shared_names(page) == {"Filter": 2, "Copy": 2, "Remove": 2, "Edit": 2}
