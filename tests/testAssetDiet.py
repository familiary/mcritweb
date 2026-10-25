#!/usr/bin/python
"""The rest of issue #63 that lives in this repository: what every page downloads.

Three things a page used to pay for on every load and no longer does: the unminified
Bootstrap build (196 KB where the minified one is 157), all 2,447 Font Awesome glyph
rules (140 KB where the icons the templates use fit in a 22 KB subset), and Dropzone
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
import minify_dropzone  # noqa: E402
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


#: a glyph rule in either of all.css's spellings, written out here rather than taken from
#: the script, so that a script that misses one spelling cannot also hide it from this test
ANY_GLYPH_RULE = re.compile(r"\.fa-([a-z0-9-]+)::?before")


def test_every_glyph_rule_the_subset_keeps_draws_an_icon_in_use():
    """all.css writes its brand icons' rules with one colon, `.fa-github:before`, and a
    pattern that knew only `::before` kept all 499 of them, half the subset's bytes."""
    rules, _ = subset_fontawesome.split_rules((STATIC / "css" / "fontawesome-subset.css").read_text())
    used = subset_fontawesome.used_icons()
    unused = [selector.strip() for selector, _ in rules
              if ANY_GLYPH_RULE.findall(selector) and not set(ANY_GLYPH_RULE.findall(selector)) & used]
    assert unused == [], f"{len(unused)} glyph rules for icons no template uses, e.g. {unused[:3]}"


def test_the_glyph_rule_pattern_knows_both_spellings():
    assert subset_fontawesome.GLYPH_RULE.findall(".fa-house::before, .fa-github:before") == ["house", "github"]


def test_the_subset_is_a_fraction_of_the_full_file():
    full = (STATIC / "css" / "all.css").stat().st_size
    subset = (STATIC / "css" / "fontawesome-subset.css").stat().st_size
    assert subset < full / 4, f"{subset} of {full} bytes"


def test_the_subset_keeps_the_font_faces_and_the_style_classes():
    css = (STATIC / "css" / "fontawesome-subset.css").read_text()
    assert css.count("@font-face") >= 2, "the solid and regular faces must survive"
    for selector in (".fa-solid", ".fa-regular", ".fa-xs", ".fa,"):
        assert selector in css, selector


# --- what base.html loads --------------------------------------------------------

def test_base_loads_the_bootstrap_subset_and_the_icon_subset():
    """The minified Bootstrap build stays as the source scripts/subset_bootstrap.py cuts
    the subset from."""
    base = (TEMPLATES / "base.html").read_text()
    assert "css/bootstrap-subset.css" in base
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
    assert '/static/upload_form.js' in page, "the page does not queue the upload form's script, which holds the loader"
    assert re.search(r'data-dropzone-js="/static/dropzone-patched\.min\.js(\?v=\w+)?"', page), "the loader is not told where Dropzone is"
    assert 'class="dropzone-pending"' in page, "the form must stay out of Dropzone's auto-discovery"
    # the options flask-dropzone renders are parked in a plain variable until the
    # library is there to receive them
    assert "window.mcritDropzoneOptions = {" in page and "Dropzone.options.myDropzone = {" not in page


@pytest.mark.parametrize(("role", "path"), [("visitor", "/explore/samples"), ("visitor", "/settings"), ("contributor", "/analyze/query"), ("contributor", "/data/submit")])
def test_the_upload_form_and_the_family_type_ahead_are_static_files(client, as_role, role, path):
    """Their code was inline on every page, about 10 KB of it before compression: on the
    settings page, which is sent uncompressed for its API token, and on a larger result
    page that put the first paint a round trip later. The browser fetches each file
    once now, queued after the first paint (ADR-0021)."""
    as_role(role)
    page = client.get(path).get_data(as_text=True)
    for code in ("function show_extended", "function mcritInitDropzone", "function mcritLoadDropzone", "attachFamilyAutocomplete", "showDropModal"):
        assert code not in page, f"{path} still carries {code}"
    assert "/static/upload_form.js" in page and "/static/family_names.js" in page


@pytest.mark.parametrize("path", ["/analyze/query", "/data/submit", "/data/import"])
def test_the_upload_pages_queue_dropzone_and_attach_it_with_their_options(client, as_role, path):
    """A tag in the body had the first paint share the network with the library's 77 KB,
    which put Lighthouse's largest paint on mobile 0.7 s later. It is queued after the paint now,
    and dropzone_attach.js, queued right after it, attaches the form with the options the
    page parked, before auto-discovery could attach it without them."""
    as_role("contributor")
    page = client.get(path).get_data(as_text=True)
    assert re.search(r'<script[^>]* src="[^"]*dropzone[^"]*"', page) is None, f"{path} loads Dropzone with a tag"
    queued = re.findall(r'later\("([^"?]+)(?:\?v=\w+)?"\)', page)
    assert "/static/dropzone-patched.min.js" in queued, f"{path} does not queue the patched Dropzone"
    assert queued.index("/static/dropzone_attach.js") == queued.index("/static/dropzone-patched.min.js") + 1, queued
    assert re.search(r'<link[^>]*dropzone\.min\.css', page), f"{path} does not link Dropzone's stylesheet"
    # styled as it will be from the first paint, and parked options instead of an
    # assignment to a Dropzone that is not there yet
    assert re.search(r'<form[^>]* class="dropzone"[^>]* id="myDropzone"', page), f"{path} does not render the form as a drop zone"
    assert "window.mcritDropzoneOptions = {" in page and "Dropzone.options.myDropzone" not in page


def test_no_template_loads_the_unminified_dropzone():
    """dropzone.js is the source dropzone-patched.min.js is written from; compiling its
    343 KB was most of the upload pages' long task after the first paint."""
    loading = sorted(path.name for path in TEMPLATES.rglob("*.html") if "filename='dropzone.js'" in path.read_text())
    assert loading == [], f"{loading} load static/dropzone.js; load dropzone-patched.min.js"


def test_the_minified_dropzone_is_the_patched_source_minified():
    """scripts/minify_dropzone.py writes it from dropzone.js; a change to one without the other
    would serve a Dropzone without it, or without the pre-fill patch."""
    pytest.importorskip("rjsmin", reason="rjsmin is not installed: python -m pip install rjsmin==1.3.0")
    written = (STATIC / "dropzone-patched.min.js").read_text(encoding="utf-8")
    assert written == minify_dropzone.minified((STATIC / "dropzone.js").read_text(encoding="utf-8")), (
        "dropzone-patched.min.js is stale: run python scripts/minify_dropzone.py"
    )


def test_the_minified_dropzone_keeps_the_patch_and_the_notices():
    """Read without rjsmin: the patch's two helpers and its field are there, the licence
    notices the comments carried are in the header, and the file is a third smaller."""
    written = (STATIC / "dropzone-patched.min.js").read_text(encoding="utf-8")
    for needle in ("const bufferToText=", "const sliceAroundKey=", "header_metadata", 'Dropzone.version="5.9.2"'):
        assert needle in written, needle
    for notice in ("Matias Meno, MIT License", "Diego Perini (MIT License)", "Denis Pushkarev (MIT License)"):
        assert notice in written.split("*/", 1)[0], notice
    assert len(written) < 0.7 * (STATIC / "dropzone.js").stat().st_size


def test_the_attach_file_attaches_before_auto_discovery_with_the_parked_options():
    script = (STATIC / "dropzone_attach.js").read_text()
    assert script.index("Dropzone.autoDiscover = false") < script.index("new Dropzone(element, window.mcritDropzoneOptions)")
    assert "!element.dropzone" in script, "a zone attached already would be attached twice"


def test_the_lazy_loader_attaches_by_hand_and_runs_the_handlers_once(client, as_role):
    """Read as code: the loader disables auto-discovery, attaches with the recorded
    options, then runs the handler block that waited for it - and a second caller
    while the script is in flight is queued rather than starting a second download."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    script = (STATIC / "upload_form.js").read_text()
    loader = script[script.index("function mcritLoadDropzone"):script.index("\n}\n", script.index("function mcritLoadDropzone"))]
    for needle in ("Dropzone.autoDiscover = false", "new Dropzone(element, window.mcritDropzoneOptions)", "mcritInitDropzone()", "mcritDropzoneLoading.push(done)", 'classList.add("dropzone")'):
        assert needle in loader, needle
    assert "function mcritInitDropzone()" in script
    # the file runs the handlers on ready only for a form that does not wait for the library
    assert re.search(r'<form id="dropzone-additional-fields-form"[^>]* data-lazy[ >]', page), "the handlers must wait for the library on a lazy page"
    assert "if (form.dataset.lazy === undefined) {\n            mcritInitDropzone();" in script


def test_the_upload_pages_run_the_handlers_on_ready(client, as_role):
    as_role("contributor")
    page = client.get("/analyze/query").get_data(as_text=True)
    assert '/static/upload_form.js' in page
    form = re.search(r'<form id="dropzone-additional-fields-form"[^>]*>', page).group(0)
    assert "data-lazy" not in form, form


if __name__ == "__main__":
    unittest.main()
