#!/usr/bin/python
"""The user manual is served from its markdown source, and only from there - #91.

It used to exist twice: `docs/manual/README.md` for readers on GitHub, and a
hand-written Jinja duplicate for `/help` in the running app, with nothing keeping
them in agreement. The screenshots were stored twice for the same reason. An edit
to either copy silently diverged from the other, and the in-app copy is the one
users actually see.

Most of what is worth asserting here is that the second copy has not come back, so
several of these tests read the repository rather than a response.
"""

import logging
import pathlib
import re
import unittest

import pytest

from mcritweb.manual import MANUAL_PATH, MISSING_MANUAL, render

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = REPOSITORY / "mcritweb" / "templates" / "help.html"

#: Four templates link to `url_for('help') + '#search'`. The anchor only exists
#: because the renderer runs markdown's `toc` extension.
LINKED_ANCHORS = ("search",)


# --- the page ---------------------------------------------------------------------

def test_the_manual_is_public(client):
    """No login: it is linked from the navbar and explains how to register."""
    assert client.get("/help").status_code == 200


def test_the_page_carries_the_manual_text(client):
    page = client.get("/help").get_data(as_text=True)
    source = MANUAL_PATH.read_text(encoding="utf-8")

    first_sentence = source.split("\n\n")[1].strip()
    assert first_sentence in page, "the rendered page does not contain the markdown's opening line"


@pytest.mark.parametrize("anchor", LINKED_ANCHORS)
def test_the_anchors_other_templates_link_to_exist(client, anchor):
    """Losing markdown's `toc` extension would break these silently - the link
    still resolves, it just lands at the top of the page."""
    page = client.get("/help").get_data(as_text=True)
    assert f'id="{anchor}"' in page


def test_the_search_section_names_the_cheap_form_of_a_function_search(client):
    """Issue #76. A plain term is a case-insensitive substring match against every
    function name, which mcrit cannot serve from an index and cannot cut short when
    nothing matches; a `function_name:` term is an equality match and can be. The
    combined search page defers the plain one and says so, but the only place that
    can tell a user how to *avoid* paying for it is the manual - so this pins that
    the advice is there, and reachable from the `#search` anchor the search pages
    link to."""
    page = client.get("/help").get_data(as_text=True)
    search_section = page.split('id="search"', 1)[-1].split('id="families"', 1)[0]

    assert "function_name:memcpy" in search_section, "the manual does not show the exact form"
    assert "substring" in search_section, "the manual does not say what a plain term matches"


def test_the_screenshots_are_served_and_reachable(client):
    """The markdown says `images/x.png`, relative to itself, which is what makes it
    render on GitHub. In the app that has to be rewritten to a real URL."""
    page = client.get("/help").get_data(as_text=True)
    sources = re.findall(r'<img[^>]*src="([^"]+)"', page)

    assert sources, "the manual rendered without any screenshots"
    assert not any(source.startswith("images/") for source in sources), "a relative link was left unrewritten"
    for source in sorted(set(sources)):
        assert client.get(source).status_code == 200, f"{source} is linked but not served"


def test_an_unknown_screenshot_is_a_404_not_a_500(client):
    assert client.get("/help/images/nothing.png").status_code == 404


def test_the_image_route_does_not_serve_files_outside_the_manual(client):
    """send_from_directory refuses traversal; this pins that it stays that way."""
    assert client.get("/help/images/../../setup.py").status_code in (301, 308, 400, 404)


# --- the duplicate stays gone -----------------------------------------------------

def test_the_template_holds_no_documentation():
    """It is the frame around the rendered markdown. Prose reappearing in it is the
    duplication of #91 coming back, one paragraph at a time."""
    template = TEMPLATE.read_text(encoding="utf-8")

    assert "{{ manual }}" in template, "the template no longer renders the markdown"
    assert template.count("<p>") == 0, "prose has been written into the template again"
    assert len(template.splitlines()) < 30, "the template has grown a second copy of the manual"


def test_the_screenshots_are_stored_once():
    duplicate = REPOSITORY / "mcritweb" / "static" / "images" / "help"
    assert not duplicate.exists(), f"{duplicate} is a second copy of docs/manual/images/"


def test_every_screenshot_the_manual_references_exists():
    """A missing file renders as a broken image on GitHub and a 404 in the app."""
    source = MANUAL_PATH.read_text(encoding="utf-8")
    referenced = set(re.findall(r"!\[[^\]]*\]\(images/([^)\s]+)", source))

    assert referenced, "the manual references no screenshots at all"
    for filename in sorted(referenced):
        assert (MANUAL_PATH.parent / "images" / filename).is_file(), f"{filename} is referenced but missing"


def test_no_screenshot_is_unreferenced():
    """The other direction, so deleting a section does not leave its image behind."""
    source = MANUAL_PATH.read_text(encoding="utf-8")
    referenced = set(re.findall(r"!\[[^\]]*\]\(images/([^)\s]+)", source))
    stored = {path.name for path in (MANUAL_PATH.parent / "images").iterdir() if path.is_file()}

    assert not stored - referenced, f"screenshots no section uses: {sorted(stored - referenced)}"


# --- the renderer -----------------------------------------------------------------

def test_the_render_is_cached_per_prefix():
    """Same input, same object - the parse should happen once per edit, not once
    per request."""
    assert render("/help/images/") is render("/help/images/")


def test_a_missing_manual_explains_itself_instead_of_raising(monkeypatch):
    """/help is public and linked from the navbar, so an incomplete checkout should
    not answer it with a server error."""
    monkeypatch.setattr("mcritweb.manual.MANUAL_PATH", pathlib.Path("/nonexistent/README.md"))
    assert render("/help/images/") is MISSING_MANUAL




# --- the manual against the code it describes ------------------------------------
#
# The unique blocks section went stale silently: this branch added a whole selection
# page and five rule parameters, and the manual went on describing the single-sample
# cubes button as the only way in. Prose cannot be kept honest by review alone, so the
# few facts in it that are also constants somewhere get pinned to their source.


def test_the_manual_states_the_selection_cap_that_the_view_enforces():
    """A number in prose is the first thing to rot. This one is enforced in one place,
    so the manual can be held to it."""
    from mcritweb.views.analyze import MAX_SELECTED_SAMPLES

    assert f"{MAX_SELECTED_SAMPLES} samples" in MANUAL_PATH.read_text(encoding="utf8"), \
        f"the manual does not state the real selection cap of {MAX_SELECTED_SAMPLES}"


def test_the_manual_documents_every_yara_parameter_the_page_offers():
    """Each of these is an input the reader can type into. One that nothing explains is
    the reason the section needed rewriting in the first place."""
    manual = MANUAL_PATH.read_text(encoding="utf8")
    template = (REPOSITORY / "mcritweb" / "templates" / "result_unique_blocks.html").read_text(encoding="utf8")

    offered = {name for name in ("min_ins", "max_ins", "min_bytes", "max_bytes",
                                 "condition_required")
               if f"name='{name}'" in template or f'name="{name}"' in template}
    assert offered, "the YARA rule form no longer names its inputs the way this test reads them"

    described = "instructions" in manual and "bytes" in manual and "condition" in manual
    assert described, f"the manual does not describe the rule parameters: {sorted(offered)}"


def test_the_manual_names_the_menu_entry_that_reaches_the_selection_page():
    """`base.html` is what a reader clicks; the manual has to name the same thing."""
    manual = MANUAL_PATH.read_text(encoding="utf8")
    base_template = (REPOSITORY / "mcritweb" / "templates" / "base.html").read_text(encoding="utf8")

    assert "analyze.unique_blocks" in base_template, \
        "the Analyze menu no longer links the unique blocks selection page"
    assert "Unique Blocks" in manual


if __name__ == "__main__":
    unittest.main()
