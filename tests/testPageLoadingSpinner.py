#!/usr/bin/python
"""The pagination spinner is wired at two ends, and both are easy to lose.

`static/page_loading.js` watches pagination clicks that navigate the whole page and
draws a Bootstrap spinner over the outgoing one. It reaches a page through
`base.html`, and the JS-driven half of the widget - `pagination_js_helper`, which
assigns `window.location` itself and so is invisible to a click listener - has to
announce its navigation by hand.

Neither end is observable from Python: whether the spinner *appears* is a browser
question. What is checkable here is that the wiring is still there, which is the part
a merge drops silently. base.html gains and loses script tags constantly, and the
helper is called from three page templates besides the widget.
"""

import logging
import os
import re
import unittest

import pytest
from fixtureData import job_id_of

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PACKAGE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcritweb")
SCRIPT = os.path.join(PACKAGE_ROOT, "static", "page_loading.js")
WIDGET = os.path.join(PACKAGE_ROOT, "templates", "table", "pagination_widget.html")

#: the body of pagination_js_helper, from its opening line to its closing brace
HELPER = re.compile(r"function pagination_js_helper\b.*?\n    \}", re.DOTALL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Pagination widgets only render where there are rows, so serve the corpus."""
    return corpus_mcrit


def test_the_spinner_script_exists():
    assert os.path.isfile(SCRIPT), f"{SCRIPT} is gone - base.html would 404 on it"


@pytest.mark.parametrize(
    "path",
    [
        "/explore/families",
        "/explore/samples",
        "/explore/functions",
        "/data/jobs",
        "/analyze/compare",
        f"/data/result/{job_id_of('matches_for_sample')}",
        f"/data/linkhunt/{job_id_of('matches_for_sample')}",
    ],
)
def test_a_page_that_paginates_also_loads_the_spinner(client, as_role, path):
    as_role("admin")

    response = client.get(path)

    assert response.status_code == 200
    assert b'class="pagination' in response.data, (
        f"{path} no longer renders a pagination widget - this case has stopped "
        "guarding anything, point it at a page that does"
    )
    assert b"page_loading.js" in response.data, (
        f"{path} paginates but does not load page_loading.js, so a click on it gives "
        "no feedback at all until the next page paints. base.html loads the script."
    )


def test_the_js_pagination_helper_announces_its_own_navigation():
    """`pagination_js_helper` navigates by assigning `window.location`, which no click
    listener can see, so it has to raise the spinner itself - before it navigates."""
    with open(WIDGET, encoding="utf-8") as template:
        source = template.read()
    body = HELPER.search(source)
    assert body is not None, "pagination_js_helper moved or was renamed"

    call = body.group().find("mcritwebPageLoading")
    navigation = body.group().find("window.location.href")
    assert call != -1, (
        "pagination_js_helper does not raise the spinner, so the page-size select and "
        "the onclick pagination on the analyze pages navigate with no feedback"
    )
    assert call < navigation, "the spinner has to go up before the navigation starts"


def test_the_sort_headers_still_navigate_the_way_the_script_expects():
    """`sortable_header_col` puts its target in an inline `onclick` rather than an
    href, so `page_loading.js` reads the URL back out of the attribute to decide
    whether the click loads anything. That is a coupling to the macro's shape: if
    the assignment is rewritten, sorting silently stops showing a spinner."""
    with open(WIDGET, encoding="utf-8") as template:
        source = template.read()
    header = source[source.find("macro sortable_header_col"):]
    assert header, "sortable_header_col moved or was renamed"

    assert "window.location.href='" in header, (
        "the sort headers no longer navigate by assigning window.location.href to a "
        "single-quoted string; HEADER_TARGET in static/page_loading.js reads that "
        "shape and now matches nothing, so a sort click shows no spinner"
    )
    with open(SCRIPT, encoding="utf-8") as script:
        assert "HEADER_TARGET" in script.read()


if __name__ == "__main__":
    unittest.main()
