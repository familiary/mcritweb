#!/usr/bin/python
"""The rest of issue #63 that lives in this repository: what every page downloads.

Three things a page used to pay for on every load and no longer does: the unminified
Bootstrap build (196 KB where the minified one is 157), all 1,948 Font Awesome glyph
rules (140 KB where the icons the templates use fit in a 46 KB subset), and Dropzone
(115 KB of script plus its stylesheet) for a drop overlay that most visits never
open - it now loads on the first drag. The subset is generated, so a test keeps it
honest: every icon a template names has its glyph, and the committed file is what the
script would write today.
"""

import logging
import pathlib
import re
import sys
import unittest

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import subset_fontawesome  # noqa: E402

TEMPLATES = ROOT / "mcritweb" / "templates"
STATIC = ROOT / "mcritweb" / "static"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


# --- the Font Awesome subset -----------------------------------------------------

def test_every_icon_the_templates_use_has_its_glyph_in_the_subset():
    css = (STATIC / "css" / "fontawesome-subset.css").read_text()
    present = set(subset_fontawesome.GLYPH_RULE.findall(css))
    missing = sorted(subset_fontawesome.used_icons() - present)
    assert missing == [], f"icons used by a template but cut from the subset - run scripts/subset_fontawesome.py: {missing}"


def test_the_committed_subset_is_what_the_script_generates():
    """The ratchet: adding an icon to a template without regenerating fails here, not
    as a blank square in a browser."""
    generated = subset_fontawesome.subset((STATIC / "css" / "all.css").read_text(), subset_fontawesome.used_icons())
    assert (STATIC / "css" / "fontawesome-subset.css").read_text() == generated, "run scripts/subset_fontawesome.py"


def test_the_subset_is_a_fraction_of_the_full_file():
    full = (STATIC / "css" / "all.css").stat().st_size
    subset = (STATIC / "css" / "fontawesome-subset.css").stat().st_size
    assert subset < full / 2, f"{subset} of {full} bytes"


def test_the_subset_keeps_the_font_faces_and_the_style_classes():
    css = (STATIC / "css" / "fontawesome-subset.css").read_text()
    assert css.count("@font-face") >= 2, "the solid and regular faces must survive"
    for selector in (".fa-solid", ".fa-regular", ".fa-xs", ".fa,"):
        assert selector in css, selector


# --- what base.html loads --------------------------------------------------------

def test_base_loads_the_minified_bootstrap_and_the_icon_subset():
    base = (TEMPLATES / "base.html").read_text()
    assert "bootstrap-5.0.2-dist/css/bootstrap.min.css" in base
    assert "css/fontawesome-subset.css" in base
    assert (STATIC / "bootstrap-5.0.2-dist" / "css" / "bootstrap.min.css").exists()


def test_the_minified_bootstrap_keeps_the_licence_and_the_rules():
    plain = (STATIC / "bootstrap-5.0.2-dist" / "css" / "bootstrap.min.css").read_text()
    full = (STATIC / "bootstrap-5.0.2-dist" / "css" / "bootstrap.css").read_text()
    assert "/*!" in plain[:40] and "Bootstrap v5.0.2" in plain[:200]
    assert len(plain) < len(full)
    # a coarse equivalence: the minifier drops whitespace and comments, never rules
    assert plain.count("{") == re.sub(r"/\*(?!!).*?\*/", "", full, flags=re.S).count("{")


# --- Dropzone on demand ----------------------------------------------------------

def test_a_listing_page_does_not_download_dropzone(client, as_role):
    """The overlay's markup is on the page; the library is not, until a drag."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    assert re.search(r'<script src="[^"]*dropzone[^"]*"', page) is None, "Dropzone is loaded up front"
    assert re.search(r'<link[^>]*dropzone\.min\.css', page) is None, "Dropzone's stylesheet is loaded up front"
    assert "function mcritLoadDropzone" in page
    assert 'class="dropzone-pending"' in page, "the form must stay out of Dropzone's auto-discovery"
    # the options flask-dropzone renders are parked in a plain variable until the
    # library is there to receive them
    assert "window.mcritDropzoneOptions = {" in page and "Dropzone.options.myDropzone = {" not in page


def test_the_upload_pages_load_dropzone_up_front(client, as_role):
    as_role("contributor")
    for path in ("/analyze/query", "/data/submit", "/data/import"):
        page = client.get(path).get_data(as_text=True)
        assert 'src="/static/dropzone.min.js"' in page, f"{path} does not load Dropzone"
        assert "dropzone.min.css" in page, f"{path} does not load Dropzone's stylesheet"
        assert 'class="dropzone"' in page, f"{path} does not render the form for auto-discovery"


def test_the_lazy_loader_attaches_by_hand_and_runs_the_handlers_once(client, as_role):
    """Read as code: the loader disables auto-discovery, attaches with the recorded
    options, then runs the handler block that waited for it - and a second caller
    while the script is in flight is queued rather than starting a second download."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    loader = page[page.index("function mcritLoadDropzone"):page.index("</script>", page.index("function mcritLoadDropzone"))]
    for needle in ("Dropzone.autoDiscover = false", "new Dropzone(element, window.mcritDropzoneOptions)", "mcritInitDropzone()", "mcritDropzoneLoading.push(done)", 'classList.add("dropzone")'):
        assert needle in loader, needle
    assert "function mcritInitDropzone()" in page
    assert "$(document).ready(mcritInitDropzone)" not in page, "the handlers must wait for the library on a lazy page"


def test_the_upload_pages_run_the_handlers_on_ready(client, as_role):
    as_role("contributor")
    page = client.get("/analyze/query").get_data(as_text=True)
    assert "$(document).ready(mcritInitDropzone)" in page


if __name__ == "__main__":
    unittest.main()
