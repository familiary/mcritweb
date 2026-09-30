#!/usr/bin/python
"""What a table says when it has no rows. Issue #65.

Every table used to hard-code one sentence per type, so a sample with no functions
was told to "upload your first sample" and the cross-compare tab of a job list full
of other jobs was told to create "your first job". The message is now the caller's to
choose, with the old text as the fallback.
"""

import logging
import re
import unittest
from pathlib import Path

import pytest
from markupsafe import escape

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

import mcritweb  # noqa: E402  - imported after logging is silenced

GENERIC_SAMPLE_PROMPT = "Click here to upload your first sample"
GENERIC_JOB_PROMPT = "Click here to create your first job"


def _queueable_job_methods():
    """Every job method the backend can run - the source of truth for the job list.

    A job category reaches the page as the `method` the backend reports in
    `getQueueStatistics()` / `getQueueData(method=...)`, and those are exactly the
    `@Remote`-marked methods of `mcrit.Worker.Worker`. It is derived from the code
    that implements the jobs, so unlike the two hand-written lists nearby it cannot
    silently fall behind: `Job.method_types` in mcrit already omits
    `recalculateMinHashes` and `recalculatePicHashes`, which MCRITweb's own jobs menu
    offers, and the menu in `views/data.py` in turn omits `getMatchesForSampleVsGroup`
    and `doDbCleanup`, which the worker runs.
    """
    from mcrit.Worker import Worker

    methods = {name for name in dir(Worker) if getattr(getattr(Worker, name, None), "remote", False)}
    # A floor, because everything below is driven by this set. If mcrit ever renames the
    # marker, or wraps @Remote without carrying the attribute across, an empty set would
    # make the completeness test pass with nothing to check and generate zero rendered
    # cases, while the reverse test blamed jobs.html for every one of its good keys -
    # loud failure, pointing at the wrong file. Fail here, where the coupling broke. The
    # bounds are deliberately loose: this is a smoke alarm, not a count of the methods.
    assert 15 <= len(methods) <= 30, (
        f"expected mcrit.Worker.Worker to carry a normal number of @Remote job methods, "
        f"found {len(methods)}: {sorted(methods)}. Has the marker been renamed or wrapped?"
    )
    return methods


QUEUEABLE_JOB_METHODS = sorted(_queueable_job_methods())

JOBS_TEMPLATE = Path(mcritweb.__file__).parent / "templates" / "jobs.html"

_MAP_BLOCK = re.compile(r"set job_category_empty_states = \{(.*?)\n\s*\} %\}", re.S)
# One entry: a quoted key at the start of a line, then whatever it maps to. Both quote
# styles, because Jinja takes either. Deliberately not anchored on the value being a
# tuple - a key whose value has been broken into some other shape is still a key, and
# reporting it as absent would send the reader hunting for a line that is right there.
_MAP_ENTRY = re.compile(r"""^[ \t]*(?P<q>["'])(?P<key>\w+)(?P=q)[ \t]*:[ \t]*(?P<value>.*)$""", re.M)
_MESSAGE = re.compile(r"""^\(\s*(?P<q>["'])(?P<message>.*?)(?P=q)""")
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)


def parse_empty_state_map(template_body):
    """The categories `jobs.html` has a sentence for, and what each one says.

    Returns {category: message}, the message being None where the entry is not a
    (message, link) tuple.

    Comments are stripped before the map is located, not after, and that ordering is
    the point: commenting out the whole `{% set %}` is the one way to disable the map
    that Jinja still renders - every category quietly falls back to the generic prompt
    - and a parser that read the map out of the comment would call it complete. (An
    individual entry cannot be commented out in place: `{# #}` inside the dict literal
    is a Jinja syntax error, so that mutation takes the page down loudly instead.)
    """
    block = _MAP_BLOCK.search(_JINJA_COMMENT.sub("", template_body))
    assert block is not None, "the per-category empty-state map has moved or been renamed"
    body = block.group(1)
    entries = {}
    for match in _MAP_ENTRY.finditer(body):
        message = _MESSAGE.match(match.group("value"))
        entries[match.group("key")] = message.group("message") if message else None
    return entries


def _empty_state_messages():
    return parse_empty_state_map(JOBS_TEMPLATE.read_text(encoding="utf-8"))


def _empty_state_map_keys():
    return set(_empty_state_messages())


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


class EmptyBackend:
    """A reachable backend that holds nothing, so every table renders its empty state."""

    def __init__(self, corpus):
        self._corpus = corpus

    def __getattr__(self, name):
        return getattr(self._corpus, name)

    @staticmethod
    def _empty_search():
        return {"search_results": {}, "cursor": {"forward": None, "backward": None},
                "id_match": None, "sha_match": None}

    def search_samples(self, *args, **kwargs):
        return self._empty_search()

    def search_families(self, *args, **kwargs):
        return self._empty_search()

    def search_functions(self, *args, **kwargs):
        return self._empty_search()

    def getFunctionsBySampleId(self, *args, **kwargs):
        return []

    def getQueueData(self, *args, **kwargs):
        return []


@pytest.fixture
def empty_mcrit(corpus_mcrit):
    return EmptyBackend(corpus_mcrit)


# --- the collection listings keep the first-run prompt ---------------------------

def test_an_empty_collection_still_invites_a_first_upload(client, as_role, app, empty_mcrit):
    """The generic message is right exactly once, and this is when."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")

    page = client.get("/explore/samples").get_data(as_text=True)

    assert GENERIC_SAMPLE_PROMPT in page


def test_a_search_that_matched_nothing_does_not_blame_an_empty_collection(client, as_role, app, empty_mcrit):
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")

    page = client.get("/explore/samples?query=nothinghere").get_data(as_text=True)

    # Jinja escapes the quotes it renders; the browser shows them as quotes
    assert "No samples match &#34;nothinghere&#34;." in page
    assert GENERIC_SAMPLE_PROMPT not in page


@pytest.mark.parametrize("path,noun", [
    ("/explore/families", "families"),
    ("/explore/functions", "functions"),
])
def test_the_other_listings_do_the_same(client, as_role, app, empty_mcrit, path, noun):
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")

    page = client.get(f"{path}?query=nothinghere").get_data(as_text=True)

    assert f"No {noun} match &#34;nothinghere&#34;." in page


# --- the detail pages ------------------------------------------------------------

def test_a_sample_with_no_functions_does_not_ask_you_to_upload_one(client, as_role, app, empty_mcrit, corpus_mcrit):
    """The sample is right there. Telling its page to upload a first sample is absurd."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")
    sample = next(iter(corpus_mcrit._samples.values()))

    page = client.get(f"/explore/samples/{sample.sample_id}").get_data(as_text=True)

    assert "This sample has no functions on record." in page
    assert GENERIC_SAMPLE_PROMPT not in page


def test_a_family_with_no_samples_says_so(client, as_role, app, empty_mcrit, corpus_mcrit):
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")
    family = next(iter(corpus_mcrit._families.values()))

    page = client.get(f"/explore/families/{family.family_id}").get_data(as_text=True)

    assert "This family has no samples." in page
    assert GENERIC_SAMPLE_PROMPT not in page


# --- the job list, which is what the issue names ---------------------------------
#
# The first pass at this listed four categories by hand, which is how the map came to
# be missing two of them: a hand-written list of cases cannot notice a case nobody
# wrote down. The cases below are enumerated from `mcrit.Worker.Worker` instead, so a
# job method added to the backend - or a tab added here - fails the suite rather than
# quietly shipping "create your first job" to somebody whose queue is full.

class QueueOfEveryKind:
    """A backend that has run one job of every kind, and none of them on this page.

    A category only reaches the jobs page when the backend reports it in
    `getQueueStatistics()`, so a fake that reports nothing cannot exercise a tab at
    all - `views.data.jobs` reads `statistics[active_category]` directly.
    """

    def __init__(self, corpus):
        self._corpus = corpus

    def __getattr__(self, name):
        return getattr(self._corpus, name)

    def getQueueStatistics(self, *args, **kwargs):
        return {method: {"queued": 0, "in_progress": 0, "finished": 1}
                for method in QUEUEABLE_JOB_METHODS}

    def getQueueData(self, *args, **kwargs):
        return []


@pytest.fixture
def every_kind_of_job(corpus_mcrit):
    return QueueOfEveryKind(corpus_mcrit)


# The guards below read the map out of the template, so the reader has to be able to
# trust the reader. Each of these was a real misread of the first version of it, and
# each fails in the direction that is hardest to notice: a message reported as missing
# when it is present, or a category reported as covered when its message is gone.

def _map_template(entries):
    """The smallest thing shaped like the map in jobs.html."""
    return "{% set job_category_empty_states = {\n" + entries + "\n  } %}\n"


def test_the_map_parser_reads_an_entry_written_with_either_quote():
    parsed = parse_empty_state_map(_map_template(
        '    "doubleQuoted":  ("No double quoted jobs yet.", None),\n'
        "    'singleQuoted':  ('No single quoted jobs yet.', None),"
    ))

    assert parsed == {"doubleQuoted": "No double quoted jobs yet.",
                      "singleQuoted": "No single quoted jobs yet."}


def test_the_map_parser_does_not_read_a_map_that_has_been_commented_out():
    """The vacuous pass this is here to stop.

    Wrapping the `{% set %}` in `{# #}` still renders - Jinja just drops it - and every
    category silently goes back to "create your first job". A parser that read the map
    out of the comment would report it complete while nothing on the page had a message.
    """
    commented_out = "{#\n" + _map_template('    "live":  ("No live jobs yet.", None),') + "#}\n"

    with pytest.raises(AssertionError, match="moved or been renamed"):
        parse_empty_state_map(commented_out)


def test_the_map_parser_keeps_a_key_whose_value_is_not_a_tuple():
    """A key is a key. Dropping it would report a message as missing while it is sitting
    in the file, sending the next reader to add a duplicate."""
    parsed = parse_empty_state_map(_map_template(
        '    "notATuple":  "No jobs yet.",'
    ))

    assert set(parsed) == {"notATuple"}, "the key is present, whatever shape its value is in"
    assert parsed["notATuple"] is None, "but there is no (message, link) pair to read"


def test_the_map_parser_says_so_when_the_map_is_gone():
    with pytest.raises(AssertionError, match="moved or been renamed"):
        parse_empty_state_map("{% set something_else = {} %}")


def test_the_empty_state_map_covers_every_job_the_backend_can_queue():
    """The map is hand-written; this is what keeps it honest.

    Reported as a set rather than one case at a time so the failure names every
    category that is missing, not just the first one.
    """
    missing = set(QUEUEABLE_JOB_METHODS) - _empty_state_map_keys()

    assert not missing, (
        "job categories with no empty-state message in jobs.html, so each falls back "
        f"to \"{GENERIC_JOB_PROMPT}\": {sorted(missing)}"
    )


def test_the_empty_state_map_has_no_message_for_a_job_that_does_not_exist():
    """The other direction: a message for a method the worker no longer runs is dead
    text, and usually a typo in the key."""
    unknown = _empty_state_map_keys() - set(QUEUEABLE_JOB_METHODS)

    assert not unknown, f"jobs.html has empty-state messages for unknown job methods: {sorted(unknown)}"


@pytest.mark.parametrize("category", QUEUEABLE_JOB_METHODS)
def test_each_job_category_says_what_it_is_missing(client, as_role, app, every_kind_of_job, category):
    """Rendered, not read off the map: this is the sentence the reader actually gets.

    Asserted as a presence and not only as the absence of the generic prompt - a
    redirect or an error page contains neither sentence and would satisfy a
    `not in page` on its own.
    """
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: every_kind_of_job
    as_role("visitor")

    response = client.get(f"/data/jobs?active={category}")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    expected = _empty_state_messages().get(category)
    assert expected, f"{category} has no empty-state message in jobs.html"
    assert str(escape(expected)) in page, f"{category} does not render its own message"
    assert GENERIC_JOB_PROMPT not in page, f"{category} fell back to the generic prompt"


def test_a_category_the_backend_has_never_run_is_not_a_server_error(client, as_role, app, every_kind_of_job):
    """`active` is a query parameter, so it is whatever somebody put in the URL.

    The view sized its pagination with `statistics[active_category]`, which raises
    KeyError - a 500 - for any category the backend has not reported. An old bookmark
    for a category whose jobs have since been deleted is enough to hit it, and so is a
    typo. It is an empty list, not an error.
    """
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: every_kind_of_job
    as_role("visitor")

    response = client.get("/data/jobs?active=nosuchmethod")

    assert response.status_code == 200


@pytest.mark.parametrize("category,expected", [
    ("combineMatchesToCross", "No cross compare jobs yet."),
    ("getMatchesForSampleVs", "No 1 vs 1 matching jobs yet."),
    ("getMatchesForSample", "No 1 vs N matching jobs yet."),
    ("getUniqueBlocks", "No unique blocks jobs yet."),
])
def test_the_wording_names_the_kind_of_job(client, as_role, app, empty_mcrit, category, expected):
    """Completeness is enumerated above; these pin the actual sentences."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")

    page = client.get(f"/data/jobs?active={category}").get_data(as_text=True)

    assert expected in page
    assert GENERIC_JOB_PROMPT not in page


def test_a_state_filter_reports_the_state(client, as_role, app, empty_mcrit):
    """Filtering to "failed" and seeing none is not an empty queue."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")

    page = client.get("/data/jobs?state=failed").get_data(as_text=True)

    assert "No jobs are in state &#34;failed&#34;." in page


def test_a_category_with_nowhere_to_start_one_offers_no_link(client, as_role, app, empty_mcrit):
    """A dead link is worse than no link. Unique blocks are started from a row, so
    there is no page to send anyone to."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")

    page = client.get("/data/jobs?active=getUniqueBlocks").get_data(as_text=True)

    assert "They are started from a family or sample row." in page


def test_the_message_is_escaped(client, as_role, app, empty_mcrit):
    """The search term reaches the empty state, and a search term is whatever
    somebody typed."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")

    page = client.get("/explore/samples?query=%3Cimg+src%3Dx+onerror%3Dalert(1)%3E").get_data(as_text=True)

    assert "<img src=x onerror=alert(1)>" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page


if __name__ == "__main__":
    unittest.main()


# --- tables that sit under a search box --------------------------------------
#
# These three were missed on the first pass, and they are the worst offenders: each has
# a search field immediately above it, so "No samples available. Click here to upload
# your first sample" is shown to someone whose *search* missed on a full collection. The
# message is not merely unhelpful there, it is false.

@pytest.mark.parametrize(
    "path,query_param",
    [
        ("/analyze/compare", "query"),
        ("/analyze/compare_versus", "query_a"),
        ("/analyze/compare_versus", "query_b"),
        ("/analyze/cross_compare", "query"),
    ],
)
def test_a_selection_page_search_that_missed_does_not_blame_an_empty_collection(client, as_role, path, query_param):
    as_role("visitor")

    page = client.get(f"{path}?{query_param}=zzznothingmatchesthis").get_data(as_text=True)

    assert "upload your first sample" not in page
    assert "No sample matches &#34;zzznothingmatchesthis&#34;." in page


@pytest.mark.parametrize("path", ["/analyze/compare", "/analyze/compare_versus", "/analyze/cross_compare"])
def test_the_same_page_without_a_search_still_offers_the_upload(client, as_role, path, monkeypatch, fake_mcrit):
    """The old message is right when the collection really is empty - the point is to
    stop saying it when it is not."""
    as_role("visitor")
    # a plain dict, not hasattr(fake_mcrit, ...): the strict fake's catch-all
    # __getattr__ raises rather than answering False
    monkeypatch.setattr(fake_mcrit, "search_samples", lambda *args, **kwargs: {
        "search_results": {}, "cursor": {"forward": None, "backward": None},
        "id_match": None, "sha_match": None,
    })

    page = client.get(path).get_data(as_text=True)

    assert "upload your first sample" in page


def test_the_cross_compare_picker_does_not_offer_a_visitor_a_403(client, as_role, monkeypatch, fake_mcrit):
    """The upload prompt is a link to data.submit, which is contributor-only.

    This picker built its empty state by hand rather than through the shared macro, so
    it was the one page still handing a visitor a link that answers 403. It goes
    through `_empty_state` now, which drops the link and keeps the sentence.
    """
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "search_samples", lambda *args, **kwargs: {
        "search_results": {}, "cursor": {"forward": None, "backward": None},
        "id_match": None, "sha_match": None,
    })

    page = client.get("/analyze/cross_compare").get_data(as_text=True)

    assert "upload your first sample" in page, "the sentence still belongs there"
    # asserted on the element, not on an href spelling: the markup this replaced used
    # single quotes, so a `href="..."` check passed against exactly what it should catch
    assert '<span class="text-muted">No samples available' in page, "should be inert text"
    assert ">No samples available. Click here to upload your first sample</a>" not in page,         "a visitor was offered a link that answers 403"


def test_the_cross_compare_picker_still_links_for_a_contributor(client, as_role, monkeypatch, fake_mcrit):
    """The other direction: whoever can actually follow the link still gets it."""
    as_role("contributor")
    monkeypatch.setattr(fake_mcrit, "search_samples", lambda *args, **kwargs: {
        "search_results": {}, "cursor": {"forward": None, "backward": None},
        "id_match": None, "sha_match": None,
    })

    page = client.get("/analyze/cross_compare").get_data(as_text=True)

    assert ">No samples available. Click here to upload your first sample</a>" in page


def test_paging_past_the_end_of_a_search_says_so(client, as_role, fake_mcrit, monkeypatch):
    """A search section renders whenever the request carried a cursor, whether or not
    the slice behind it has rows - so the "next" link on the last page lands on empty
    tables under live headings."""
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "search_samples", lambda *args, **kwargs: {
        "search_results": {}, "cursor": {"current": "c", "forward": None, "backward": "b"},
        "id_match": None, "sha_match": None,
    })

    page = client.get("/explore/search?query=citadel&type=sample&sample_cursor=c").get_data(as_text=True)

    assert "upload your first sample" not in page
    assert "No more samples match &#34;citadel&#34; on this page." in page


# --- an invitation a reader cannot accept is worse than no invitation ----------------
#
# `data.submit` is contributor-only; the navbar has always hidden "Submit binary" from a
# visitor for that reason. An empty table telling a visitor to "click here to upload
# your first sample" and then answering 403 is a worse experience than one that simply
# says the table is empty - so the message stays and the link goes.

SUBMIT_LINK = 'href="/data/submit"'


def test_a_visitor_is_invited_but_not_linked_to_a_page_they_cannot_open(client, as_role, app, empty_mcrit):
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")

    page = client.get("/explore/samples").get_data(as_text=True)

    assert GENERIC_SAMPLE_PROMPT in page, "the message itself is still worth saying"
    assert SUBMIT_LINK not in page, "offered a visitor a link that answers 403"


@pytest.mark.parametrize("role", ["contributor", "admin"])
def test_someone_who_can_submit_still_gets_the_link(client, as_role, app, empty_mcrit, role):
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role(role)

    page = client.get("/explore/samples").get_data(as_text=True)

    assert SUBMIT_LINK in page


def test_the_link_a_visitor_is_denied_really_would_have_been_denied(client, as_role):
    """Pins the premise rather than trusting it: if `data.submit` ever opened up to
    visitors, the gate above becomes wrong and this test says so."""
    as_role("visitor")

    assert client.get("/data/submit").status_code == 403


@pytest.mark.parametrize("path", ["/explore/families", "/explore/functions", "/analyze/compare"])
def test_every_other_empty_state_hides_it_too(client, as_role, app, empty_mcrit, path):
    """The gate is in `_empty_state`, not at the call sites, so this holds for all of
    them - including the ones that were already there before issue #65."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: empty_mcrit
    as_role("visitor")

    assert SUBMIT_LINK not in client.get(path).get_data(as_text=True)
