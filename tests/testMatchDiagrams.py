#!/usr/bin/python
"""The match diagram is rendered by the route that serves it, not by the result page.

Rendering it inline in `data.result_matches_for_sample_or_query`, before
render_template, was the single largest cost of the first view of a result page -
a full PIL render plus a `getFunctionsBySampleId` round trip, in front of HTML the
diagram is not part of. It has had its own route and its own <img> request all
along, so the work belongs there. See issue #68.

What these tests hold onto: the page must no longer pay for it, the image route
must produce the same picture on demand, and every way the render can fail must end
in a missing image rather than in a 500 or a half-written file that gets served.
"""

import copy
import io
import logging
import os
import re
import unittest

import pytest
from fixtureData import job_id_of
from mcrit.storage.MatchingResult import MatchingResult
from PIL import Image

from mcritweb.views.data import DIAGRAM_FILENAME_RE, match_diagram_size
from mcritweb.views.MatchReportRenderer import MatchReportRenderer, count_diagram_blocks, stacked_diagram_size

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def diagrams_dir(app):
    return os.sep.join([app.instance_path, "cache", "diagrams"])


def cached_diagrams(app):
    return sorted(os.listdir(diagrams_dir(app)))


# --- the page no longer renders it ------------------------------------------------

@pytest.mark.parametrize("query", ["", "?famid=1", "?samid=1", "?funid=0"])
def test_result_page_does_not_render_the_diagram(client, as_role, app, corpus_mcrit, query):
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('matches_for_sample')}{query}")

    assert response.status_code == 200
    assert cached_diagrams(app) == [], "the page rendered a diagram it does not serve"
    called = [name for name, _args, _kwargs in corpus_mcrit.calls]
    assert "getFunctionsBySampleId" not in called, "the renderer's backend call is still on the page path"


def test_result_page_still_points_at_the_diagram(client, as_role):
    """The <img> has to keep naming the file the route knows how to build."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    response = client.get(f"/data/result/{job_id}")

    assert f"/data/diagrams/{job_id}.png".encode() in response.data


# --- the route renders it ---------------------------------------------------------

@pytest.mark.parametrize(
    "report, suffix",
    [
        ("matches_for_sample", ""),
        ("matches_for_sample", "-famid_1"),
        ("matches_for_sample", "-samid_1"),
        ("matches_for_sample", "-funid_0"),
        ("matches_for_sample_vs", ""),
        ("matches_for_query", ""),
    ],
)
def test_diagram_route_renders_on_demand(client, as_role, app, report, suffix):
    """Every name a result template can ask for, plus one it cannot: the 1-vs-1 page
    shows no diagram, so nothing links to `<vs job>.png`. The route draws it anyway
    if it is asked for by hand, which is the one thing it does that the old inline
    rendering did not - a diagram no page displays, for a report it can be drawn
    from. Recorded here rather than special-cased: telling that job kind apart needs
    the job record, which the report itself does not carry.
    """
    as_role("visitor")
    filename = f"{job_id_of(report)}{suffix}.png"
    response = client.get(f"/data/diagrams/{filename}")

    assert response.status_code == 200
    assert response.data.startswith(PNG_MAGIC)
    assert cached_diagrams(app) == [filename], "the render was not cached under the name it was asked for"


def test_diagram_route_serves_the_cached_file_without_re_rendering(client, as_role, app, corpus_mcrit):
    as_role("visitor")
    filename = f"{job_id_of('matches_for_sample')}.png"
    first = client.get(f"/data/diagrams/{filename}")
    corpus_mcrit.calls.clear()
    second = client.get(f"/data/diagrams/{filename}")

    assert first.data == second.data
    assert corpus_mcrit.calls == [], "a cached diagram still went to the backend"


def test_diagram_route_does_not_need_the_result_cache(client, as_role, app):
    """Nothing guarantees the page was visited first, so the route has to be able to
    fetch the report itself."""
    as_role("visitor")
    results_dir = os.sep.join([app.instance_path, "cache", "results"])
    assert os.listdir(results_dir) == []

    response = client.get(f"/data/diagrams/{job_id_of('matches_for_sample')}.png")

    assert response.status_code == 200
    assert response.data.startswith(PNG_MAGIC)


# --- the picture is the one the page used to render -------------------------------

#: The user's stored UserFilters, as `data.result` hands them to setFilterValues.
NO_USER_FILTERS = {
    "filter_direct_min_score": None,
    "filter_direct_nonlib_min_score": None,
    "filter_frequency_min_score": None,
    "filter_frequency_nonlib_min_score": None,
    "filter_unique_only": None,
    "filter_exclude_own_family": None,
    "filter_family_name": None,
    "filter_function_min_score": None,
    "filter_function_max_score": None,
    "filter_function_offset": None,
    "filter_max_num_families": None,
    "filter_min_num_samples": None,
    "filter_max_num_samples": None,
    "filter_exclude_library": None,
    "filter_exclude_pic": None,
    "filter_func_unique": None,
}

#: ...and a user who has set one that bites. All-None filters narrow nothing, so on
#: their own they cannot tell a renderer that reads the filtered lists from one that
#: does not: both sides of the assertion below would hold the same matches either
#: way. This one leaves the page's copy holding fewer function matches than the
#: route's fresh copy - which test_the_narrowing_user_filter_still_narrows checks,
#: because a regenerated fixture could quietly take that away again.
NARROWING_USER_FILTERS = dict(NO_USER_FILTERS, filter_function_min_score=90)


def render_to_bytes(matching_result, **filters):
    renderer = MatchReportRenderer()
    renderer.processReport(matching_result)
    buffer = io.BytesIO()
    renderer.renderStackedDiagram(**filters).save(buffer, format="PNG")
    return buffer.getvalue()


def test_the_narrowing_user_filter_still_narrows(app, corpus_mcrit):
    """NARROWING_USER_FILTERS is only worth parametrising over while it bites, and
    what makes it bite is a threshold against the captured report. Regenerating the
    fixtures could put every match on one side of it and turn half the parametrisation
    below back into tautologies, with nothing failing to say so."""
    report = corpus_mcrit.getResultForJob(job_id_of("matches_for_sample"))
    with app.test_request_context("/"):
        result = MatchingResult.fromDict(report)
        result.setFilterValues(dict(NARROWING_USER_FILTERS))
        result.getUniqueFamilyMatchInfoForSample(None)
        result.applyFilterValues()

        assert 0 < len(result.filtered_function_matches) < len(result.function_matches)


@pytest.mark.parametrize("user_filters", [NO_USER_FILTERS, NARROWING_USER_FILTERS], ids=["no user filters", "a user filter that bites"])
@pytest.mark.parametrize(
    "filters, narrow",
    [
        # the unfiltered and function-filtered branches rendered before calling
        # filterToFunctionId, so the user's stored filters are all that had been
        # applied to the page's copy by then - which is why those two rows narrow
        # nothing here, and why the user_filters axis above is what gives them teeth
        ({}, lambda result: None),
        ({"filtered_family_id": 1}, lambda result: result.filterToFamilyId(1)),
        ({"filtered_sample_id": 1}, lambda result: result.filterToSampleId(1)),
        ({"filtered_function_id": 0}, lambda result: None),
    ],
)
def test_the_diagram_does_not_depend_on_the_filtering_the_page_applies(app, corpus_mcrit, filters, narrow, user_filters):
    """Why the route may re-read the report instead of being handed the page's copy.

    The page narrows a MatchingResult - the user's stored filters, then
    filterToFamilyId or filterToSampleId - before it used to render. The renderer
    reads only the unfiltered `function_matches`, `library_matches` and
    `reference_sample_entry`, so both produce the same image. If a future mcrit makes
    the renderer look at the filtered lists, this is where that shows up.
    """
    report = corpus_mcrit.getResultForJob(job_id_of("matches_for_sample"))
    with app.test_request_context("/"):
        as_the_page_had_it = MatchingResult.fromDict(report)
        as_the_page_had_it.setFilterValues(dict(user_filters))
        as_the_page_had_it.getUniqueFamilyMatchInfoForSample(None)
        as_the_page_had_it.applyFilterValues()
        narrow(as_the_page_had_it)

        assert render_to_bytes(as_the_page_had_it, **filters) == render_to_bytes(MatchingResult.fromDict(report), **filters)


# --- everything that can go wrong -------------------------------------------------

@pytest.mark.parametrize(
    "filename",
    [
        "ffffffffffffffffffffffff.png",                       # no such job
        "6a74660af8b8d2c6f83664f1-famid_notanumber.png",      # not a filter we emit
        "not a diagram.png",
        "../results/anything.json",
        "%2e%2e/results/anything.json",
        "*.png",                                              # glob syntax, if it ever reached one
        "[a-z].png",
    ],
)
def test_a_diagram_nobody_can_render_is_a_404_not_a_500(client, as_role, app, filename):
    as_role("visitor")
    response = client.get(f"/data/diagrams/{filename}")

    assert response.status_code in (301, 400, 404), response.status_code
    assert cached_diagrams(app) == [], "a failed render left a file behind"


@pytest.mark.parametrize("suffix", ["-famid_9999", "-samid_9999", "-funid_9999"])
def test_a_filter_the_page_would_have_rejected_draws_nothing(client, as_role, app, suffix):
    """The page checked isFamilyId / isSampleId, and looked a function id up in the
    report, before it linked to a diagram. A URL typed by hand reaches the renderer
    directly, so the route has to check the same things - or the cache fills with
    diagrams of families and samples that do not exist."""
    as_role("visitor")
    response = client.get(f"/data/diagrams/{job_id_of('matches_for_sample')}{suffix}.png")

    assert response.status_code == 404
    assert cached_diagrams(app) == []


@pytest.mark.parametrize("suffix", ["-famid_0001", "-samid_01", "-funid_-0"])
def test_a_filter_id_the_app_spells_differently_renders_nothing(client, as_role, app, suffix):
    """int() folds "0001", "01" and "-0" onto the names create_match_diagram writes -
    "-famid_1", "-samid_1", "-funid_0" - so the file rendered is never the file that
    was asked for, and the on-disk short-circuit in `diagram_file` tests the name that
    was asked for. Each of these was therefore a URL that fetched the report and
    rebuilt a whole MatchingResult on every single hit, 404ed, and could never be
    answered from disk, and the filter-id grammar spelled about eighteen of them per
    id, behind nothing but @visitor_required. On master the route did no work at all
    for any of them.
    """
    as_role("visitor")
    response = client.get(f"/data/diagrams/{job_id_of('matches_for_sample')}{suffix}.png")

    assert response.status_code == 404
    assert cached_diagrams(app) == [], "rendered a diagram under a name it then 404ed for"


@pytest.mark.parametrize("report", ["cross_compare", "unique_blocks"])
def test_a_job_that_is_not_a_match_report_does_not_render_a_diagram(client, as_role, app, report):
    """`/data/diagrams/<job_id>.png` takes any job id, and only some jobs carry a
    report a diagram can be drawn from."""
    as_role("visitor")
    response = client.get(f"/data/diagrams/{job_id_of(report)}.png")

    assert response.status_code == 404
    assert cached_diagrams(app) == [], "a failed render left a file behind"


def test_a_query_report_has_no_function_filtered_diagram(client, as_role, app):
    """A query's functions are not in the corpus, so there is nothing to lay a
    function-filtered diagram out over - as before, none is produced."""
    as_role("visitor")
    response = client.get(f"/data/diagrams/{job_id_of('matches_for_query')}-funid_-1.png")

    assert response.status_code == 404
    assert cached_diagrams(app) == []


def test_a_render_that_never_starts_is_a_404_not_a_500(client, as_role, app, monkeypatch):
    """A render that raises before writing anything. Nothing to clean up, so this is
    only about the page: the route swallows it and the browser gets a missing image,
    where the inline render used to take the whole result page down with it."""
    as_role("visitor")

    def explode(*args, **kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(MatchReportRenderer, "renderStackedDiagram", explode)
    response = client.get(f"/data/diagrams/{job_id_of('matches_for_sample')}.png")

    assert response.status_code == 404
    assert cached_diagrams(app) == []


class HalfWrittenImage:
    """Stands in for the PIL image create_match_diagram renders, and dies with some
    bytes already written - a full disk, or the worker being killed mid-encode.

    PIL's Image.save takes either an open file or a path, and so does this one: that
    is what lets the test be pointed at an implementation that writes in place and
    still tell the difference, rather than passing on both.
    """

    def save(self, target, format=None):
        if hasattr(target, "write"):
            target.write(PNG_MAGIC)
            target.flush()
        else:
            with open(target, "wb") as fout:
                fout.write(PNG_MAGIC)
        raise OSError("no space left on device")


def test_a_render_that_dies_half_way_through_leaves_no_partial_file(client, as_role, app, monkeypatch):
    """The diagram is written to a temporary file and renamed, so a render that dies
    part way through cannot leave something that later looks like a cached diagram.

    That matters more here than for the report cache: the file would be served by
    this very route, as a truncated PNG, and browser-cached - and nothing invalidates
    a cached diagram, so it would stay wrong until someone emptied the directory.
    """
    as_role("visitor")
    monkeypatch.setattr(MatchReportRenderer, "renderStackedDiagram", lambda self, **kwargs: HalfWrittenImage())
    response = client.get(f"/data/diagrams/{job_id_of('matches_for_sample')}.png")

    assert response.status_code == 404
    assert cached_diagrams(app) == []


# --- the filename grammar ---------------------------------------------------------

@pytest.mark.parametrize(
    "filename, expected",
    [
        ("6a74660af8b8d2c6f83664f1.png", ("6a74660af8b8d2c6f83664f1", None, None)),
        ("6a74660af8b8d2c6f83664f1-famid_7.png", ("6a74660af8b8d2c6f83664f1", "famid", "7")),
        ("6a74660af8b8d2c6f83664f1-samid_0.png", ("6a74660af8b8d2c6f83664f1", "samid", "0")),
        ("6a74660af8b8d2c6f83664f1-funid_-3.png", ("6a74660af8b8d2c6f83664f1", "funid", "-3")),
        # a local queue hands out uuid4 job ids, which contain the same dash the
        # filter suffix is joined with - the id must not swallow the suffix
        ("2f1a9b04-1f6d-4a1e-9a1c-0c5f2f9b8e77.png", ("2f1a9b04-1f6d-4a1e-9a1c-0c5f2f9b8e77", None, None)),
        ("2f1a9b04-1f6d-4a1e-9a1c-0c5f2f9b8e77-famid_7.png",
         ("2f1a9b04-1f6d-4a1e-9a1c-0c5f2f9b8e77", "famid", "7")),
    ],
)
def test_diagram_filenames_are_parsed_back_into_their_parts(filename, expected):
    match = DIAGRAM_FILENAME_RE.match(filename)
    assert match is not None
    assert (match.group("job_id"), match.group("filter_kind"), match.group("filter_id")) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "6a74660af8b8d2c6f83664f1-famid_0001.png",
        "6a74660af8b8d2c6f83664f1-samid_01.png",
        "6a74660af8b8d2c6f83664f1-funid_-0.png",
        "6a74660af8b8d2c6f83664f1-funid_-01.png",
    ],
)
def test_a_filter_id_that_is_not_how_the_app_spells_it_is_not_read_as_a_filter(filename):
    """These are not names this route ever writes, and reading them as filters is what
    made them uncacheable - see the route-level test above. Off the filter grammar
    they are just an odd job id, which costs a backend miss and no report parse,
    exactly like every other name nobody has a job for."""
    match = DIAGRAM_FILENAME_RE.match(filename)

    assert match is not None, "the job id accepts these characters, so the name still parses"
    assert match.group("filter_kind") is None, match.groupdict()


@pytest.mark.parametrize(
    "filename",
    [
        "../secrets.png",
        "sub/dir.png",
        "job.id.png",
        "job id.png",
        "*.png",
        "job.jpg",
        ".png",
        "6a74660af8b8d2c6f83664f1-famid_7.png.png",
        "a" * 65 + ".png",
        # `$` would take this: it matches before a trailing newline
        "6a74660af8b8d2c6f83664f1.png\n",
    ],
)
def test_a_filename_the_app_never_wrote_is_not_parsed(filename):
    assert DIAGRAM_FILENAME_RE.match(filename) is None


# --- the page reserves the box the diagram will land in ---------------------------

#: The <img> the four result templates build through templates/match_diagram.html.
DIAGRAM_IMG_RE = re.compile(r'<div class="mcrit-diagram">\s*<img [^>]*>')


def diagram_img(response):
    match = DIAGRAM_IMG_RE.search(response.data.decode())
    assert match is not None, "the page shows no diagram"
    return match.group(0)


def report_trimmed_to_the_fixture_pool(corpus_mcrit, report):
    """The report with only the functions the fixtures kept an entry for.

    `tests/fixtures/regenerate.py` captures the first 100 functions of a reference
    sample, while the report summarises all of them - so against this corpus the two
    sources of instruction counts describe different sets, and only here. On a live
    backend they are one list: mcrit's MatcherSample/MatcherVs summarise the report
    from `getFunctionsBySampleId(sample_id)`, which is what the renderer then fetches
    again. Trimming restores that equality so the assertion is about the arithmetic
    rather than about the fixture.
    """
    result_json = corpus_mcrit.getResultForJob(job_id_of(report))
    sample_id = result_json["info"]["sample"]["sample_id"]
    pool = {entry.function_id for entry in corpus_mcrit.getFunctionsBySampleId(sample_id)}
    trimmed = copy.deepcopy(result_json)
    trimmed["matches"]["functions"] = [summary for summary in result_json["matches"]["functions"] if abs(summary["fid"]) in pool]
    return trimmed


@pytest.mark.parametrize(
    "num_instructions_per_function, num_blocks",
    [
        # one block per ten instructions, plus a separator block between functions,
        # and nothing at all for a function too short to be matched
        ([], -1),
        ([9], -1),
        ([10], 1),
        ([10, 25], 4),
        ([100, 9, 10], 12),
    ],
)
def test_the_block_count_is_what_the_diagram_draws(num_instructions_per_function, num_blocks):
    assert count_diagram_blocks(num_instructions_per_function) == num_blocks


@pytest.mark.parametrize(
    "num_blocks, size",
    [
        # 240 blocks fit across the 2400px band, and each further row of three bands
        # adds 27px. An empty report comes to -1 blocks and still gets a one-row frame.
        (-1, (2440, 107)),
        (239, (2440, 107)),
        (240, (2440, 134)),
        (959, (2440, 188)),
        (960, (2440, 215)),
    ],
)
def test_the_image_size_is_what_the_block_count_implies(num_blocks, size):
    assert stacked_diagram_size(num_blocks) == size


@pytest.mark.parametrize("report", ["matches_for_sample", "matches_for_sample_vs"])
def test_the_reserved_size_is_the_size_the_diagram_renders_to(app, corpus_mcrit, report):
    """The whole point of the reservation: it has to be the picture's real size.

    match_diagram_size reads the per-function instruction counts out of the report;
    MatchReportRenderer reads them back off the backend. The two share the arithmetic
    - pinned by the two tests above - but not the data, and nothing but this holds
    those together.
    """
    trimmed = report_trimmed_to_the_fixture_pool(corpus_mcrit, report)
    with app.test_request_context("/"):
        renderer = MatchReportRenderer()
        renderer.processReport(MatchingResult.fromDict(trimmed))
        rendered = renderer.renderStackedDiagram()

    assert match_diagram_size(trimmed) == rendered.size


@pytest.mark.parametrize("suffix", ["-famid_1", "-samid_1", "-funid_0"])
def test_every_filtered_diagram_is_the_size_of_the_unfiltered_one(client, as_role, suffix):
    """Why one size serves all four result templates.

    _calculateOutputMap lays a block out for every function of the reference sample
    whichever filter is set - the filters only decide the colours - so all four
    variants come out the same size. If that stopped holding, the three filtered
    pages would be reserving the unfiltered diagram's box.
    """
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    unfiltered = client.get(f"/data/diagrams/{job_id}.png")
    filtered = client.get(f"/data/diagrams/{job_id}{suffix}.png")

    assert Image.open(io.BytesIO(unfiltered.data)).size == Image.open(io.BytesIO(filtered.data)).size


@pytest.mark.parametrize("query", ["", "?famid=1", "?funid=0"])
def test_the_result_page_reserves_the_diagram_box(client, as_role, corpus_mcrit, query):
    """Without width and height the browser gives the <img> no room until the bytes
    arrive, and every table below it jumps once they do - the diagram is only async
    now because the page stopped rendering it."""
    as_role("visitor")
    report = corpus_mcrit.getResultForJob(job_id_of("matches_for_sample"))
    width, height = match_diagram_size(report)

    response = client.get(f"/data/result/{job_id_of('matches_for_sample')}{query}")

    assert response.status_code == 200
    assert f'width="{width}" height="{height}"' in diagram_img(response)


def test_a_query_report_reserves_nothing(client, as_role):
    """A query's reference sample is not stored, so MatchReportRenderer has no
    function entries for it and draws an empty diagram. Reserving a box for that
    would be a guess, and the <img> is left to size itself."""
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('matches_for_query')}")

    assert response.status_code == 200
    assert "width=" not in diagram_img(response)


if __name__ == "__main__":
    unittest.main()
