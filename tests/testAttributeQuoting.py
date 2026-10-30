#!/usr/bin/python
"""Template values printed into an HTML attribute are quoted.

Jinja's autoescape turns `&`, `<`, `>`, `"` and `'` into entities, which keeps a value inside
a quoted attribute. It leaves spaces and `=` alone, so an unquoted `value={{ ... }}` ends at
the first space and whatever follows becomes attributes of its own. The result page's family
filter is reflected from the query string, so a link carrying
`?filter_family_name=x autofocus onfocus=...` ran script in the session of whoever opened it,
with a visitor's login.
"""

import pathlib
import re
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "mcritweb" / "templates"

#: an attribute whose value starts with a template expression and no quote: `name={{`, after
#: whitespace or after the `%}` of an `{% if %}` that guards it. A `?samid={{` inside a quoted
#: URL is not one.
UNQUOTED = re.compile(r"(?:^|(?<=[\s}]))[\w:-]+=\{\{", re.MULTILINE)

PAYLOAD = 'x autofocus onfocus=document.title="PWNED"'


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


class _Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and "id" in attributes:
            self.inputs[attributes["id"]] = attributes


def inputs_of(page):
    parser = _Inputs()
    parser.feed(page)
    return parser.inputs


def test_no_template_prints_an_unquoted_attribute_value():
    offenders = [f"{path.relative_to(TEMPLATES)}:{text[:match.start()].count(chr(10)) + 1}: {match.group()}"
                 for path in sorted(TEMPLATES.rglob("*.html")) for text in [path.read_text(encoding="utf-8")]
                 for match in UNQUOTED.finditer(text)]
    assert offenders == [], "quote these: " + "; ".join(offenders)


def test_the_scan_finds_an_unquoted_value_however_it_is_guarded():
    for template in ('<input value={{ x }}>', '<input {% if x %}value={{ x }}{% endif %}>', '<a\n  href={{ x }}>'):
        assert UNQUOTED.search(template), template
    for template in ('<input value="{{ x }}">', "<a href='{{ x }}'>", "'/data/result?samid={{ s }}'"):
        assert not UNQUOTED.search(template), template


def test_a_reflected_family_filter_stays_the_value_of_its_field(client, as_role):
    as_role("visitor")
    page = client.get(f"/data/result/{job_id_of('matches_for_sample')}", query_string={"filter_family_name": PAYLOAD})
    assert page.status_code == 200
    field = inputs_of(page.get_data(as_text=True))["filter_family_name"]
    assert field.get("value") == PAYLOAD
    assert "onfocus" not in field and "autofocus" not in field, field
