#!/usr/bin/python
"""Row-specific rendering driven by data passed to the template - issue #53.

A view hands a table macro `row_decorations`, a mapping of row id to a decoration
dict, and `_table_base` forwards it to the row macro and the header macro alike. The
two shapes the issue asks for are covered here: a badge marking an id match, and a
full-row tint (the cross-compare selection, which that page used to hand-roll as inline
`style` attributes and which is a state class of the row now, painted from the palette).

The values in these rows come out of a malware corpus and are chosen by whoever
submitted the sample, so the escaping half is not decoration: the tint and the badge
variant are *names* resolved against a table in `row_decoration.html` rather than
interpolated, and the tests below drive both with values that would break out if they
were not.
"""

import logging
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


#: macro -> (fixture attribute holding rows, the id field the row macro looks itself up by)
DECORATED_TABLES = {
    "family_table": ("_families", "family_id"),
    "sample_table": ("_samples", "sample_id"),
    "function_table": ("_functions", "function_id"),
}


def render_table(app, macro, rows, **kwargs):
    source = "{%% from 'table/table.html' import %s %%}{{ %s(rows, row_decorations=row_decorations) }}" % (macro, macro)
    with app.test_request_context("/"):
        return app.jinja_env.from_string(source).render(rows=rows, **kwargs)


def first_rows(backend, attribute, count=2):
    return list(getattr(backend, attribute).values())[:count]


class CellCounter(HTMLParser):
    """Cells per row, so a header and its body cannot silently disagree."""

    def __init__(self):
        super().__init__()
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append(0)
        elif tag in ("th", "td") and self.rows:
            self.rows[-1] += 1


def cells_per_row(rendered):
    counter = CellCounter()
    counter.feed(rendered)
    return counter.rows


def without_csrf(rendered):
    """The family and sample tables embed an edit form, whose token is fresh per
    render, and a script, whose CSP nonce is fresh per response - the two things that
    legitimately differ between two renders."""
    rendered = re.sub(r'(name="csrf_token" value=)"[^"]*"', r"", rendered)
    return re.sub(r'<script nonce="[^"]*"', "<script", rendered)


def row_tags(rendered):
    """The opening <tr> tags. Both tables carry an edit modal whose own markup
    mentions colours and handlers, so an assertion over the whole page proves
    nothing about the rows."""
    return re.findall(r"<tr[^>]*>", rendered)


def tinted_rows(rendered, state):
    """The <tr> tags that carry the state class `row-<state>` as a class of their own
    (not as part of another name)."""
    return [tag for tag in row_tags(rendered)
            if re.search(r'class="[^"]*\brow-%s\b[^"]*"' % state, tag)]


# --- the mechanism ---------------------------------------------------------------

@pytest.mark.parametrize("macro", sorted(DECORATED_TABLES))
def test_a_badge_reaches_the_row_it_names(app, fake_mcrit, macro):
    attribute, id_field = DECORATED_TABLES[macro]
    rows = first_rows(fake_mcrit, attribute)
    decorated = getattr(rows[0], id_field)
    other = getattr(rows[1], id_field)

    rendered = render_table(app, macro, rows, row_decorations={
        decorated: {"badges": [{"text": "ID match", "variant": "success"}]}})

    assert '<span class="badge bg-success">ID match</span>' in rendered
    assert rendered.count("ID match") == 1, f"the badge leaked onto rows other than {decorated}, {other} included"


@pytest.mark.parametrize("macro", sorted(DECORATED_TABLES))
def test_the_badge_column_keeps_the_header_and_the_rows_in_step(app, fake_mcrit, macro):
    """Header and row read the same mapping, so the extra column cannot appear on one
    side only - which would shift every cell in the table one place left."""
    attribute, id_field = DECORATED_TABLES[macro]
    rows = first_rows(fake_mcrit, attribute)
    decorations = {getattr(rows[0], id_field): {"badges": [{"text": "ID match"}]}}

    plain = cells_per_row(render_table(app, macro, rows, row_decorations=None))
    decorated = cells_per_row(render_table(app, macro, rows, row_decorations=decorations))

    assert len(set(plain)) == 1, f"the undecorated table is already ragged: {plain}"
    assert len(set(decorated)) == 1, f"header and rows disagree on the column count: {decorated}"
    assert decorated[0] == plain[0] + 1


@pytest.mark.parametrize("macro", sorted(DECORATED_TABLES))
def test_a_tint_only_decoration_grows_no_badge_column(app, fake_mcrit, macro):
    """Tinting is not a reason to make room for badges nobody passed."""
    attribute, id_field = DECORATED_TABLES[macro]
    rows = first_rows(fake_mcrit, attribute)
    decorations = {getattr(rows[0], id_field): {"tint": "selected"}}

    plain = cells_per_row(render_table(app, macro, rows, row_decorations=None))
    tinted = cells_per_row(render_table(app, macro, rows, row_decorations=decorations))

    assert tinted == plain


@pytest.mark.parametrize("macro", sorted(DECORATED_TABLES))
def test_a_tint_reaches_the_row_it_names(app, fake_mcrit, macro):
    attribute, id_field = DECORATED_TABLES[macro]
    rows = first_rows(fake_mcrit, attribute)
    decorations = {getattr(rows[0], id_field): {"tint": "selected"}}

    rendered = render_table(app, macro, rows, row_decorations=decorations)

    assert len(tinted_rows(rendered, "selected")) == 1
    assert not tinted_rows(rendered, "pending")
    assert not any("background-color" in tag for tag in row_tags(rendered)), "the tint is a class, not an inline colour"


@pytest.mark.parametrize("macro", sorted(DECORATED_TABLES))
def test_an_undecorated_table_renders_exactly_as_before(app, fake_mcrit, macro):
    attribute, _ = DECORATED_TABLES[macro]
    rows = first_rows(fake_mcrit, attribute)

    without = render_table(app, macro, rows, row_decorations=None)
    empty = render_table(app, macro, rows, row_decorations={})

    assert "badge" not in without
    assert not any("background-color" in tag for tag in row_tags(without))
    assert not tinted_rows(without, "selected") and not tinted_rows(without, "pending")
    assert without_csrf(empty) == without_csrf(without)


# --- escaping: rows carry values chosen by whoever submitted the sample ------------

def test_a_tint_that_is_not_in_the_palette_renders_nothing(app, fake_mcrit):
    """A tint is a name, not CSS. If it were interpolated this would close the style
    attribute and open an onclick handler."""
    rows = first_rows(fake_mcrit, "_samples")
    hostile = 'red;" onclick="alert(1)'

    rendered = render_table(app, "sample_table", rows, row_decorations={
        rows[0].sample_id: {"tint": hostile}})

    assert not any("background-color" in tag for tag in row_tags(rendered))
    assert not tinted_rows(rendered, "selected") and not tinted_rows(rendered, "pending")
    assert "alert(1)" not in rendered


def test_a_badge_variant_outside_the_palette_falls_back(app, fake_mcrit):
    """Same for the variant, which would otherwise be pasted into a class attribute."""
    rows = first_rows(fake_mcrit, "_samples")

    rendered = render_table(app, "sample_table", rows, row_decorations={
        rows[0].sample_id: {"text": "x", "badges": [{"text": "hit", "variant": 'dark" onclick="alert(1)'}]}})

    assert '<span class="badge bg-secondary">hit</span>' in rendered
    assert "alert(1)" not in rendered


def test_badge_text_is_escaped(app, fake_mcrit):
    rows = first_rows(fake_mcrit, "_samples")

    rendered = render_table(app, "sample_table", rows, row_decorations={
        rows[0].sample_id: {"badges": [{"text": "<script>alert(1)</script>"}]}})

    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


# --- example 1: an id match, badged (issue #53) -----------------------------------

def test_the_search_page_badges_an_id_match(client, as_role, fake_mcrit):
    """`explore.search` mixes mcrit's exact hit into the ordinary result rows, where
    nothing told it apart from a text match."""
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))

    page = client.get(f"/explore/search?query={sample.sample_id}&type=sample").get_data(as_text=True)

    assert '<span class="badge bg-success">ID match</span>' in page


def test_the_search_page_badges_a_sha256_match(client, as_role, fake_mcrit):
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))

    page = client.get(f"/explore/search?query={sample.sha256}&type=sample").get_data(as_text=True)

    assert '<span class="badge bg-success">SHA256 match</span>' in page


def test_a_search_without_an_exact_hit_carries_no_badge(client, as_role, fake_mcrit):
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))

    page = client.get(f"/explore/search?query={sample.filename}&type=sample").get_data(as_text=True)

    assert str(sample.sample_id) in page
    assert "badge" not in page


# --- example 2: a full row tinted (issue #53) -------------------------------------

def test_cross_compare_tints_a_selected_row(client, as_role, fake_mcrit):
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))

    page = client.get(f"/analyze/cross_compare?samples={sample.sample_id}").get_data(as_text=True)

    assert '<tr class="parent row-selected">' in page


def test_cross_compare_tints_a_row_clicked_but_not_yet_added(client, as_role, fake_mcrit):
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))

    page = client.get(f"/analyze/cross_compare?cache={sample.sample_id}").get_data(as_text=True)

    assert '<tr class="parent row-pending">' in page


def test_a_selected_row_that_is_also_cached_stays_green(client, as_role, fake_mcrit):
    """Both attributes used to be written into the same tag; the browser took the
    first, which was the green one. One decoration wins now, and it is the same one."""
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))

    page = client.get(
        f"/analyze/cross_compare?samples={sample.sample_id}&cache={sample.sample_id}").get_data(as_text=True)

    assert '<tr class="parent row-selected">' in page
    assert not tinted_rows(page, "pending")


# --- the pickers' scripts and the stylesheet: a state is a class, never a colour (#70) -----

PICKER_PAGES = ["/analyze/compare", "/analyze/compare_versus", "/analyze/cross_compare", "/analyze/unique_blocks"]

STYLE_CSS = Path(__file__).resolve().parent.parent / "mcritweb" / "static" / "style.css"


@pytest.mark.parametrize("path", PICKER_PAGES)
def test_a_picker_toggles_a_class_and_writes_no_colour(client, as_role, fake_mcrit, path):
    """These pages wrote `rgb(240, 240, 240)` and `white` into a row's style attribute
    when it was clicked, which no theme can change. A deselected row loses the class now
    rather than being painted white."""
    as_role("visitor")

    page = client.get(path).get_data(as_text=True)

    assert 'classList.add("row-pending")' in page
    assert 'classList.remove("row-pending")' in page
    assert "backgroundColor" not in page
    assert "rgb(240, 240, 240)" not in page


def test_cross_compare_skips_a_selected_row_by_its_class(client, as_role, fake_mcrit):
    """The handler read `style.backgroundColor` back and compared it with "yellowgreen"."""
    as_role("visitor")

    page = client.get("/analyze/cross_compare").get_data(as_text=True)

    assert 'classList.contains("row-selected")' in page
    assert "yellowgreen" not in page


@pytest.mark.parametrize("state", ["selected", "pending"])
def test_the_stylesheet_paints_a_row_state_from_its_token(state):
    css = STYLE_CSS.read_text()

    assert re.search(r"tr\.row-%s\s*\{\s*background-color:\s*var\(--row-%s\)\s*;?\s*\}" % (state, state), css)


if __name__ == "__main__":
    unittest.main()
