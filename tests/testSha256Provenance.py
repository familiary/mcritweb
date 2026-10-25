"""The sample page says where a sample's sha256 comes from (familiary/mcrit#270, #297).

mcrit records `SampleEntry.sha256_verified`: True for a sample it disassembled from an
uploaded binary, False for one indexed from a submitted report or an import - whose sha256
is only what the report states - and None for one stored before that was recorded or for a
query. An mcrit package that predates the field builds entries without the attribute at all
(its `SampleEntry.fromDict` drops the key), so the page reads it with the `getattr` filter and
must say nothing for it, as for None.

A page that says nothing is also what the page did before this, so each test that expects
silence first checks that the same page does speak for a known state.
"""

import copy
import re

import pytest

VERIFIED = "verified against the uploaded binary"
UNVERIFIED = "taken from a submitted report or an import"
NOT_CHECKED = "not checked against a binary"

#: a sample that has no `sha256_verified` attribute, as one from an older mcrit has none
MISSING = object()


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def sample_page(client, fake_mcrit, sha256_verified):
    sample = fake_mcrit._samples[0]
    if sha256_verified is MISSING:
        vars(sample).pop("sha256_verified", None)
        assert not hasattr(sample, "sha256_verified")
    else:
        sample.sha256_verified = sha256_verified
    return client.get("/explore/samples/0").get_data(as_text=True)


def sha256_cell(page):
    cell = re.search(r"SHA256 \| Filename: </th>\s*<td[^>]*>(.*?)</td>", page, re.S)
    assert cell is not None, "the sample page has no SHA256 row"
    return cell.group(1)


def test_a_verified_sample_says_it_was_checked_against_the_binary(client, as_role, fake_mcrit):
    as_role("visitor")

    cell = sha256_cell(sample_page(client, fake_mcrit, True))

    assert VERIFIED in cell
    assert UNVERIFIED not in cell
    assert NOT_CHECKED not in cell


def test_an_unverified_sample_says_its_sha256_was_not_checked(client, as_role, fake_mcrit):
    as_role("visitor")

    cell = sha256_cell(sample_page(client, fake_mcrit, False))

    assert UNVERIFIED in cell
    assert NOT_CHECKED in cell
    assert VERIFIED not in cell


def test_a_sample_of_unknown_provenance_says_nothing(client, as_role, fake_mcrit):
    as_role("visitor")
    assert UNVERIFIED in sample_page(client, fake_mcrit, False)

    page = sample_page(client, fake_mcrit, None)

    assert sha256_cell(page)
    for phrase in (VERIFIED, UNVERIFIED, NOT_CHECKED):
        assert phrase not in page


def test_an_entry_without_the_field_says_nothing(client, as_role, fake_mcrit):
    as_role("visitor")
    assert UNVERIFIED in sample_page(client, fake_mcrit, False)

    page = sample_page(client, fake_mcrit, MISSING)

    assert sha256_cell(page)
    for phrase in (VERIFIED, UNVERIFIED, NOT_CHECKED):
        assert phrase not in page


SAMPLE_COLUMN_TABLE = (
    "{% from 'table/column_table.html' import sample_column_table %}"
    "{{ sample_column_table(*pairs, **options) }}"
)


def render_columns(app, pairs, **options):
    with app.test_request_context("/"):
        return app.jinja_env.from_string(SAMPLE_COLUMN_TABLE).render(pairs=pairs, options=options)


def test_only_a_table_that_asks_for_the_provenance_shows_it(app, fake_mcrit):
    """The same table renders on the result pages, whose samples come out of stored job
    results; they stay as they were."""
    sample = copy.deepcopy(fake_mcrit._samples[0])
    sample.sha256_verified = False

    asked = render_columns(app, [("Sample Info", sample)], show_sha256_provenance=True)
    unasked = render_columns(app, [("Sample Info", sample)])

    assert UNVERIFIED in asked
    assert UNVERIFIED not in unasked


def test_each_column_shows_the_provenance_of_its_own_sample(app, fake_mcrit):
    verified = copy.deepcopy(fake_mcrit._samples[0])
    verified.sha256_verified = True
    unverified = copy.deepcopy(fake_mcrit._samples[1])
    unverified.sha256_verified = False
    unknown = copy.deepcopy(fake_mcrit._samples[0])
    vars(unknown).pop("sha256_verified", None)

    page = render_columns(app, [("A", verified), ("B", unverified), ("C", unknown)], show_sha256_provenance=True)

    assert page.count(VERIFIED) == 1
    assert page.count(UNVERIFIED) == 1
    assert page.index(VERIFIED) < page.index(UNVERIFIED)
