#!/usr/bin/python
"""Conditional icon attributes must not decide whether the start tag closes."""

import copy
import logging
from html.parser import HTMLParser
from pathlib import Path

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

TEMPLATES = Path(__file__).resolve().parent.parent / "mcritweb" / "templates"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def test_conditional_icon_tags_close_after_the_condition():
    """Check every one-line icon attribute switch, including templates not rendered here."""
    checked = []
    for path in TEMPLATES.rglob("*.html"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "<i {% if " not in line or "{% else %}" not in line:
                continue
            checked.append(f"{path.relative_to(TEMPLATES)}:{number}")
            _, endif, after = line.partition("{% endif %}")
            assert endif and after.lstrip().startswith(">"), checked[-1]
    assert checked, "no conditional icon tags were checked"


class IconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.open_icons = 0
        self.stray_attributes = []

    def handle_starttag(self, tag, attrs):
        if tag == "i":
            self.open_icons += 1
            self.stray_attributes.extend(name for name, _ in attrs if name in {"<", "i"})

    def handle_endtag(self, tag):
        if tag == "i":
            self.open_icons -= 1


@pytest.mark.parametrize("is_library", [True, False])
def test_sample_row_library_icon_is_well_formed(app, fake_mcrit, is_library):
    sample = copy.copy(next(iter(fake_mcrit._samples.values())))
    sample.is_library = is_library
    source = "{% from 'table/table.html' import sample_table %}{{ sample_table([sample]) }}"
    with app.test_request_context("/"):
        rendered = app.jinja_env.from_string(source).render(sample=sample)

    icons = IconParser()
    icons.feed(rendered)
    icons.close()

    assert ("fa-square-check" if is_library else "fa-times-circle") in rendered
    assert icons.stray_attributes == []
    assert icons.open_icons == 0
