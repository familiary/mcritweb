#!/usr/bin/python
"""Bootstrap 5.0.2 finds what a control acts on only through `data-bs-*` attributes.

The jobs page's navbar toggler named its menu with Bootstrap 4's `data-target`, which 5
does not read, so below 992px, where the toggler replaces the menu, a click on it opened
nothing. These fail on a Bootstrap 4 name in a template, and on a navbar toggler whose
target is not on its page.

The names checked are the ones Bootstrap 5 needs to find a control's target or to
start one of its components. `data-toggle="tooltip"` is not one of them: Bootstrap 5
starts no tooltip from an attribute, and the app selects those buttons itself to call
`.tooltip()` on them. Nor is the `data-placement="top"` that rides along with one of
them, which asks for what is Bootstrap 5's default anyway.
"""

import logging
import re
from pathlib import Path

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

TEMPLATES = Path(__file__).resolve().parent.parent / "mcritweb" / "templates"

#: Bootstrap 4's data API, which 5.0.2 reads as data-bs-target, data-bs-toggle and so on
BOOTSTRAP4_ATTRIBUTE = re.compile(
    r'\sdata-(?:target|dismiss|parent|slide|slide-to|ride|spy)='
    r'|\sdata-toggle="(?:collapse|dropdown|modal|tab|pill|button|offcanvas|popover)"'
)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """The captured corpus, so the jobs page renders its lists."""
    return corpus_mcrit


def test_no_template_uses_a_bootstrap4_data_attribute():
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            found += [f"{path.relative_to(TEMPLATES)}:{number}: {match.group(0).strip()}" for match in BOOTSTRAP4_ATTRIBUTE.finditer(line)]
    assert found == []


def test_the_self_check_finds_bootstrap4_attributes():
    assert BOOTSTRAP4_ATTRIBUTE.search('<button data-bs-toggle="collapse" data-target="#menu">')
    assert BOOTSTRAP4_ATTRIBUTE.search('<a data-toggle="modal" href="#">')
    assert not BOOTSTRAP4_ATTRIBUTE.search('<button data-bs-toggle="collapse" data-bs-target="#menu">')
    assert not BOOTSTRAP4_ATTRIBUTE.search('<button data-toggle="tooltip" title="Copy">')


@pytest.mark.parametrize("path", ["/data/jobs?active=getMatchesForSample", "/explore/samples"])
def test_every_navbar_toggler_names_a_menu_on_its_page(client, as_role, path):
    as_role("visitor")
    response = client.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    togglers = re.findall(r'<button class="navbar-toggler\b[^"]*"[^>]*>', html)
    # the site's own navbar, and on the jobs page the one over its job lists
    assert len(togglers) == (2 if path.startswith("/data/jobs") else 1)
    for toggler in togglers:
        target = re.search(r'\sdata-bs-target="#([\w-]+)"', toggler)
        assert target, f"{toggler} names no menu Bootstrap 5 reads"
        assert re.search(r'<div class="[^"]*\bcollapse\b[^"]*" id="%s"' % target.group(1), html), f"no collapsible #{target.group(1)} on {path}"
