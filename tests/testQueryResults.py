#!/usr/bin/python
"""What a query result page says about the binary that was queried - issue #40.

A Query is a sample matched against the collection without being stored in it, so
the report the backend sends back carries no identity for it: `info.sample` arrives
with `sample_id: -1` and empty `family`, `version`, `component` and `filename` - see
`tests/fixtures/matches_for_query.result.json`, captured from a live instance.

The result page rendered those empty fields as if they were a stored sample's, which
is two separate complaints in that issue:

  * the filename showed as "-". It is empty in the report because none of the
    backend's query endpoints (`/query`, `/query/binary`, `/query/binary/mapped`)
    accepts one, so MCRITweb is the only place that ever sees the name the file was
    uploaded under - and it dropped that name after reading the bytes.
  * the empty family and version were rendered anyway, so a query was presented as a
    member of family 0, linked and captioned "Unnamed", with a blank version and
    component beside it. None of those fields exist for a query.

The escaping test is here rather than in testScriptEscaping.py because remembering
the filename is what makes it reachable: a filename is chosen by whoever uploads it,
a query may be run by a `visitor`, and it lands in a `clipboard_btn` whose value went
into an inline `onclick` as a single-quoted JavaScript literal. HTML autoescaping
does not save that - the parser decodes `&#39;` back to `'` before the handler is
compiled as script.
"""

import io
import logging
import re
from html import unescape

import pytest
from fixtureData import job_id_of, load
from mcrit.storage.SampleEntry import SampleEntry

from mcritweb.db import get_query_filename, remember_query_filename

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: What conftest's fakes answer for a queueing call.
JOB_ID = "0123456789abcdef01234567"

#: Every inline event handler in a rendered page.
INLINE_HANDLER = re.compile(r"\son[a-z]+=\"([^\"]*)\"")


@pytest.fixture
def on_corpus(app, corpus_mcrit):
    """Serve the captured reports from this app for the tests that render one.

    Swapped in per test rather than by overriding `fake_mcrit` for the module, the way
    testResultPages.py does: `CorpusMcritClient` knows nothing about the queueing calls
    `/analyze/query` makes, and the strict fake this leaves in place for the rest of
    the module does.
    """
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: corpus_mcrit
    return corpus_mcrit


def post_query(client, filename, content=b"MZ\x90\x00"):
    """POST to the query dropzone the way the browser does."""
    return client.post(
        "/analyze/query",
        data={"options": "unmapped", "file": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


def inline_handlers(page):
    """The handlers as the browser sees them - after the HTML parser has decoded the
    attribute value, which is the step that made escaping `'` pointless here."""
    return [unescape(match.group(1)) for match in INLINE_HANDLER.finditer(page)]


def input_sample_table(page):
    """The 'Input Sample' block of a result page, without the match tables below it."""
    return page.split("Input Sample", 1)[1].split("</table>", 1)[0]


# --- remembering the name ---------------------------------------------------------


def test_a_query_upload_is_remembered_under_its_job_id(app, client, as_role):
    """The only record of the name anywhere, since the backend is never told it."""
    as_role("visitor")
    assert post_query(client, "invoice_2022.exe").status_code == 202

    with app.app_context():
        assert get_query_filename(JOB_ID) == "invoice_2022.exe"


def test_a_job_nobody_uploaded_for_has_no_remembered_name(app):
    """The normal state for every job predating this, and for a 1-vs-N result."""
    with app.app_context():
        assert get_query_filename(JOB_ID) is None


def test_a_remembered_name_is_not_overwritten_by_a_later_upload(app, client, as_role):
    """The backend deduplicates a repeated query onto the job it already has, so a
    second upload of the same bytes comes back with the same job id. The name kept is
    the one that job's report was computed for, not whatever a later caller sent."""
    as_role("visitor")
    post_query(client, "first.exe")
    post_query(client, "second.exe")

    with app.app_context():
        assert get_query_filename(JOB_ID) == "first.exe"


@pytest.mark.parametrize("nameless", [None, "", "   ", "​"])
def test_nothing_is_remembered_for_an_upload_that_carries_no_usable_name(app, nameless):
    """A multipart part may carry no filename at all. Storing "" would make the page
    render an empty Filename field instead of the "-" that says "not known"."""
    with app.app_context():
        remember_query_filename(JOB_ID, nameless)
        assert get_query_filename(JOB_ID) is None


@pytest.mark.parametrize(
    "uploaded, stored, reason",
    [
        ("report\r\n.exe", "report.exe", "control characters"),
        ("payload​.exe", "payload.exe", "invisible formatting characters"),
        ("  spaced.exe  ", "spaced.exe", "surrounding whitespace"),
        ("a" * 400 + ".exe", "a" * 255, "a name longer than the field allows"),
    ],
)
def test_a_hostile_filename_is_reduced_to_something_displayable(app, uploaded, stored, reason):
    """A filename is attacker-controlled text that MCRITweb shows to other users. It
    is never used as a path here, so separators are left alone rather than mangled
    into something the uploader would not recognise - but nothing invisible and
    nothing unbounded is kept."""
    with app.app_context():
        remember_query_filename(JOB_ID, uploaded)
        assert get_query_filename(JOB_ID) == stored, reason


# --- showing it on the result page ------------------------------------------------


def query_result_page(client, app, filename=None):
    """The captured query report's result page, optionally with a remembered name."""
    if filename is not None:
        with app.app_context():
            remember_query_filename(job_id_of("matches_for_query"), filename)
    response = client.get(f"/data/result/{job_id_of('matches_for_query')}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_a_query_result_names_the_file_it_was_uploaded_as(client, app, as_role, on_corpus):
    as_role("visitor")
    assert "dumped_module.bin" in query_result_page(client, app, filename="dumped_module.bin")


def test_a_query_result_without_a_remembered_name_still_renders(client, app, as_role, on_corpus):
    """Every query job predating this has no row, and neither does one submitted
    through the API passthrough rather than the dropzone."""
    as_role("visitor")
    assert "Best Family Matches" in query_result_page(client, app)


def test_a_stored_sample_keeps_the_filename_from_its_own_report(client, app, as_role, on_corpus):
    """The lookup is for queries only - a 1-vs-N reference sample is in the collection
    and the backend already sends its name."""
    as_role("visitor")
    with app.app_context():
        remember_query_filename(job_id_of("matches_for_sample"), "not_this_one.exe")
    page = client.get(f"/data/result/{job_id_of('matches_for_sample')}").get_data(as_text=True)

    assert "not_this_one.exe" not in page


def test_a_query_filename_cannot_break_out_of_the_clipboard_handler(client, app, as_role, on_corpus):
    """`clipboard_btn` interpolated its value into a single-quoted JS string inside an
    `onclick`. Jinja escapes `'` to `&#39;`, and the HTML parser turns that back into a
    quote before the handler is compiled as script, so the escaping bought nothing."""
    as_role("visitor")
    page = query_result_page(client, app, filename="a');alert(1);//")

    assert "alert(1)" not in " ".join(inline_handlers(page))


# --- not presenting a query as a stored sample ------------------------------------


def test_a_query_is_not_presented_as_a_member_of_a_family(client, app, as_role, on_corpus):
    """`family_id` is 0 and `family` is "" in a query report, which `format_family_name`
    rendered as a link to family 0 captioned "Unnamed"."""
    as_role("visitor")
    table = input_sample_table(query_result_page(client, app))

    assert "Unnamed" not in table
    assert "Version | Component | Library" not in table


def test_a_stored_reference_sample_still_shows_its_family_and_version(client, app, as_role, on_corpus):
    """The counterpart, so the change above cannot be satisfied by dropping the rows
    for everyone - a 1-vs-N reference sample has all of these."""
    as_role("visitor")
    page = client.get(f"/data/result/{job_id_of('matches_for_sample')}").get_data(as_text=True)

    assert "Version | Component | Library" in input_sample_table(page)


def test_a_table_holding_both_a_query_and_a_stored_sample_keeps_the_columns(app):
    """`sample_column_table` takes one sample or two, and a query filtered to one of
    its matches passes both - the stored one does have a family and a version, so the
    rows are only dropped when every sample in the table is a query.

    Rendered through the macro rather than through a route: reaching that page needs
    every matched function entry the report names, and the captured corpus carries
    only the reference pool (see tests/fixtures/README.md).
    """
    query_entry = SampleEntry.fromDict(load("matches_for_query.result")["info"]["sample"])
    stored_entry = SampleEntry.fromDict(load("samples")["1"])
    template = (
        "{% from 'table/column_table.html' import sample_column_table %}"
        "{{ sample_column_table(*pairs) }}"
    )

    with app.test_request_context("/"):
        render = app.jinja_env.from_string(template).render
        both = render(pairs=[("Reference Sample", query_entry), ("Other Sample", stored_entry)])
        query_only = render(pairs=[("Reference Sample", query_entry)])

    assert "Version | Component | Library" in both
    assert "Version | Component | Library" not in query_only


def test_the_query_column_of_a_mixed_table_still_claims_no_family(app):
    """Keeping the rows for the stored sample must not hand its neighbour a family.

    The first version of this change gated the family and version cells on the table
    as a whole, so a query filtered to one matched sample got the rows back - and with
    them the linked "Unnamed" family 0 this change exists to remove. Found by Codex
    review. Each cell is gated on its own sample now.
    """
    query_entry = SampleEntry.fromDict(load("matches_for_query.result")["info"]["sample"])
    stored_entry = SampleEntry.fromDict(load("samples")["1"])
    template = (
        "{% from 'table/column_table.html' import sample_column_table %}"
        "{{ sample_column_table(*pairs) }}"
    )

    with app.test_request_context("/"):
        both = app.jinja_env.from_string(template).render(
            pairs=[("Reference Sample", query_entry), ("Other Sample", stored_entry)])

    # the stored sample keeps its own identity
    assert "Version | Component | Library" in both
    assert stored_entry.family in both
    # and the query is given none of it: exactly one family link, the stored one's
    family_links = re.findall(r'href="/explore/families/(\d+)"', both)
    assert family_links == [str(stored_entry.family_id)], (
        f"the query column was given a family too: {family_links}")
