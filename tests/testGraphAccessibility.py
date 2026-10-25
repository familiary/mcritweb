#!/usr/bin/python
"""What assistive technology is told about the control flow graphs.

An <svg> with no role or name is exposed as an image named by all the text inside it:
every block's code, run together into one paragraph. On a function's page the code panel
beside the graph holds the same code as text, so the graph is hidden there and the panel,
which scrolls, can take focus. The comparison page has no such panel, so each graph is one
image named after its function and described by the hint above the graphs, and the
blocks drawn inside it are hidden.
tests/testPageStructureBrowser.py checks the drawn graph's accessibility tree.
"""

import pytest
from testAccessibility import Page


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def by_id(page, element_id):
    found = [element for element in page.elements if element.attrs.get("id") == element_id]
    assert len(found) == 1, element_id
    return found[0]


def test_the_function_page_hides_the_graph_its_code_panel_stands_in_for(client, as_role):
    as_role("visitor")
    page = Page(client.get("/explore/functions/84").get_data(as_text=True))

    assert by_id(page, "graphContainer").attrs.get("aria-hidden") == "true"
    panel = by_id(page, "xcfg_text_right")
    assert panel.attrs.get("tabindex") == "0"
    # focusable, so it has a role, and a region is named
    assert panel.attrs.get("role") == "region" and panel.attrs.get("aria-label") == "Code of function 84"


def test_each_comparison_graph_is_a_named_image(client, as_role):
    as_role("visitor")
    page = Page(client.get("/data/matches/function/84/943").get_data(as_text=True))

    names = []
    for graph in ("graphContainer_a", "graphContainer_b", "graphContainer_c"):
        svg = by_id(page, graph)
        assert svg.attrs.get("role") == "img", graph
        names.append(svg.attrs.get("aria-label"))
        hint = by_id(page, svg.attrs.get("aria-describedby"))
        assert "Hover a block" in hint.text_content()
        # Chromium keeps an image's children in its tree; the blocks the viewer draws into
        # this group are kept out of it
        assert [child.attrs.get("aria-hidden") for child in svg.children if child.tag == "g"] == ["true"], graph
    assert names == ["Control flow graph of function A, 84", "Control flow graph of function B, 943",
                     "Combined control flow graph of functions 84 and 943"]
