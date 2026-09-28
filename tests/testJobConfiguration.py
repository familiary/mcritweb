#!/usr/bin/python
"""Getting from a job back to the form it was submitted from - issue #55's other half.

"Rerun job" repeats a job exactly; this is the link for changing something first. It
reopens the analyze page behind the job with the job's own inputs already filled in,
so a comparison can be widened, narrowed or re-matched without retyping it.

The arguments come from the same recovery the rerun uses (`rerun_request`), so the
three methods are the same three, and a job whose request cannot be rebuilt gets no
link either. What is extra here is that the *form* has to be able to show them:

  * `analyze.cross_compare` preselects from `samples=` outright, and its two
    checkboxes cover `sample_group_only`.
  * `analyze.compare` and `analyze.compare_versus` do not. They highlight `selected`
    only when the sample is among the search results in front of them - and
    compare.html falls back to selecting the *first* row when it is not, so a bare
    `selected=` would quietly point the form at a different sample. Each link
    therefore carries the search that puts the sample on the page: mcrit answers a
    sample id with that sample (`id_match`) wherever it would otherwise fall in the
    paging. That is what test_the_link_survives_the_sample_being_off_the_search_page
    pins down.
  * The "Minhash Matching" slider is the only matching control any of them has, so a
    job carrying a parameter it cannot express - or a band value the slider has no
    position for - gets no link rather than a form that misdescribes it.

`force_recalculation` is deliberately not preselected: it is consumed before a job's
payload is written, so no job records whether it was forced and either choice would
be an invention.
"""

import copy
import logging
import re
import unittest

import pytest
from fixtureData import altered_job, inject_job, job_id_of

from mcritweb.views.params import parse_band_range

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """The captured corpus. Nothing here queues anything, so the request* methods the
    rerun tests add are not needed."""
    return corpus_mcrit


def configuration_link(page):
    """The 'Modify configuration' href on a job page, or None."""
    match = re.search(r'href="(/analyze/[^"]*)"[^>]*>\s*<i class="fa-solid fa-sliders">', page)
    return match.group(1).replace("&amp;", "&") if match else None


def link_for(client, job_id):
    return configuration_link(client.get(f"/data/jobs/{job_id}").get_data(as_text=True))


def sample_ids_on(page):
    """The sample ids of the rows a compare page is offering."""
    return re.findall(r'class="sample-id">(\d+)</th>', page)


def crowd_out_first_page(fake, count=15):
    """Add enough samples that a search page no longer holds all of them.

    The captured corpus has ten samples and a page holds ten, so nothing in it is
    ever off-page - and being off-page is exactly the case the link's search exists
    for. The clones are copies of a real entry under unused, higher ids, so a
    descending sort puts them all in front of the originals.
    """
    template = fake._samples[0]
    for offset in range(count):
        clone = copy.deepcopy(template)
        clone.sample_id = 100 + offset
        fake._samples[clone.sample_id] = clone


# --- which jobs get a link -------------------------------------------------------

@pytest.mark.parametrize(
    "report, expected",
    [
        ("matches_for_sample", "/analyze/compare?query=0&selected=0&minhashBandRange=2"),
        ("matches_for_sample_vs",
         "/analyze/compare_versus?query_a=1&selected_a=1&query_b=3&selected_b=3&minhashBandRange=2"),
        ("cross_compare", "/analyze/cross_compare?samples=0,2,4,6,1&onlySelected=true&minhashBandRange=2"),
    ],
)
def test_the_job_page_links_to_the_form_the_job_came_from(client, as_role, report, expected):
    as_role("visitor")

    assert link_for(client, job_id_of(report)) == expected


@pytest.mark.parametrize("report", ["matches_for_query", "unique_blocks"])
def test_no_link_where_the_request_cannot_be_rebuilt(client, as_role, report):
    """The same exclusions the rerun makes: a query job's binary is only in the
    backend's GridFS, and unique blocks is not submitted from a form that could
    preselect anything."""
    as_role("visitor")

    assert link_for(client, job_id_of(report)) is None


def test_a_collection_job_is_not_linked_to_a_comparison_form(client, as_role, fake_mcrit):
    as_role("visitor")
    job_id = next(entry["_id"]["$oid"] for entry in fake_mcrit._queue if entry["payload"]["method"] == "addBinarySample")

    assert link_for(client, job_id) is None


def test_the_link_is_a_link_and_not_a_button(client, as_role):
    """The opposite of the rerun control next to it: this one queues nothing, it only
    reopens a form, so it is a plain href and not a POST."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    page = client.get(f"/data/jobs/{job_id}").get_data(as_text=True)

    assert configuration_link(page) is not None
    assert 'data-post="/analyze/' not in page


def test_a_job_still_running_is_still_linked(client, as_role, fake_mcrit):
    """Unlike the rerun, which would queue the same work twice. Opening the form of a
    job that is still in the queue, to submit a variation of it, costs nothing."""
    as_role("visitor")
    running = altered_job("matches_for_sample", "bbbbbbbbbbbbbbbbbbbbbbb1")
    running["finished_at"] = None
    running["progress"] = 0.5
    job_id = inject_job(fake_mcrit, running)

    assert link_for(client, job_id) == "/analyze/compare?query=0&selected=0&minhashBandRange=2"


# --- what the linked form actually shows -----------------------------------------

def test_the_1vsn_form_opens_with_the_jobs_sample_selected(client, as_role):
    """`selected` is what compare.html's createJob() puts in the URL it submits, so
    this is the whole preselection."""
    as_role("visitor")
    page = client.get(link_for(client, job_id_of("matches_for_sample"))).get_data(as_text=True)

    assert "var selected = '0'" in page
    assert "0" in sample_ids_on(page)


def test_the_1vs1_form_opens_with_both_samples_selected(client, as_role):
    """Both sides, and the right way round: getMatchesForSampleVs renders position 0
    as the report's own sample and position 1 as the one it is compared against."""
    as_role("visitor")
    page = client.get(link_for(client, job_id_of("matches_for_sample_vs"))).get_data(as_text=True)

    assert "var selected_a = '1'" in page
    assert "var selected_b = '3'" in page


def test_the_cross_compare_form_opens_with_every_sample_of_the_job(client, as_role):
    """And with "Compare only selected samples" ticked, because the captured job's
    children are getMatchesForSampleVsGroup - a group-only comparison. That box is
    not a caching hint, it decides which comparison runs (#97)."""
    as_role("visitor")
    page = client.get(link_for(client, job_id_of("cross_compare"))).get_data(as_text=True)

    assert re.search(r"\[\]\.concat\(\[0, 2, 4, 6, 1\],", page)
    assert 'id="only_selected" checked' in page


def test_a_cross_compare_of_whole_samples_does_not_tick_only_selected(client, as_role, fake_mcrit):
    """The other child method, and so the other value of `sample_group_only`. Ticking
    it here would submit a narrower comparison than the job on screen."""
    as_role("visitor")
    for child_id in fake_mcrit.getJobData(job_id_of("cross_compare")).all_dependencies:
        child = fake_mcrit._queued_by_id[child_id]
        child["payload"]["method"] = "getMatchesForSample"
        child["payload"]["params"] = '{"band_matches_required": 2, "0": 0}'

    page = client.get(link_for(client, job_id_of("cross_compare"))).get_data(as_text=True)

    assert 'id="only_selected" checked' not in page
    assert 'id="only_selected" ' in page


def test_the_link_survives_the_sample_being_off_the_search_page(client, as_role, fake_mcrit):
    """The reason the link carries a search at all.

    Sorted the other way round, the job's sample is no longer among the results a
    compare page would list - and compare.html then selects the *first* row it does
    list. The search on the link is what keeps the sample on the page: without it the
    form would open pointing at somebody else's sample while looking preselected.
    """
    as_role("visitor")
    crowd_out_first_page(fake_mcrit)
    link = link_for(client, job_id_of("matches_for_sample"))

    page = client.get(f"{link}&ascending=false").get_data(as_text=True)

    assert "0" in sample_ids_on(page), "the job's sample is not on the page it was linked to"
    assert "var selected = '0'" in page


# --- the matching parameters -----------------------------------------------------

@pytest.mark.parametrize(
    "band_matches_required, slider_position, label",
    [(0, 0, "Off"), (4, 1, "Fast"), (2, 2, "Standard"), (1, 3, "Complete")],
)
def test_the_slider_opens_on_the_setting_the_job_ran_with(
    client, as_role, fake_mcrit, band_matches_required, slider_position, label
):
    """A round trip through the one table both directions read: the position on the
    link has to be the position that asks for the job's own band_matches_required, or
    resubmitting an untouched form silently changes the matching."""
    as_role("visitor")
    job_id = inject_job(fake_mcrit, altered_job(
        "matches_for_sample", "bbbbbbbbbbbbbbbbbbbbbbb2",
        params='{"band_matches_required": %d, "0": 0}' % band_matches_required,
    ))

    link = link_for(client, job_id)

    assert link.endswith(f"minhashBandRange={slider_position}")
    page = client.get(link).get_data(as_text=True)
    assert f"&nbsp;{label}" in page


def test_the_slider_position_on_the_link_parses_back_to_the_jobs_own_value(client, as_role):
    """The other half of the same round trip, taken through the parser the analyze
    routes actually use rather than through the rendered label."""
    as_role("visitor")
    link = link_for(client, job_id_of("matches_for_sample"))

    with client.application.test_request_context(link):
        from flask import request
        assert parse_band_range(request) == 2


@pytest.mark.parametrize(
    "params",
    [
        '{"band_matches_required": 2, "minhash_threshold": 40, "0": 0}',
        '{"band_matches_required": 2, "pichash_size": 8, "0": 0}',
        '{"0": 0}',
        '{"band_matches_required": 3, "0": 0}',
    ],
    ids=["minhash-threshold", "pichash-size", "no-band-matching", "band-value-off-the-slider"],
)
def test_no_link_to_a_form_that_cannot_show_the_job(client, as_role, fake_mcrit, params):
    """These jobs are all rerunnable - the rerun passes the parameters straight back
    to the backend - but no analyze page has a control for them. `minhash_threshold`
    and `pichash_size` would be dropped by a form that never sets them; a job that
    recorded no band matching took the backend's own default, which the slider cannot
    reproduce because it always submits a position; and a band value with no position
    cannot be shown at all. In each case the form would claim to be the job's
    configuration while describing a different comparison.
    """
    as_role("visitor")
    job_id = inject_job(fake_mcrit, altered_job("matches_for_sample", "bbbbbbbbbbbbbbbbbbbbbbb3", params=params))

    assert link_for(client, job_id) is None


def test_a_cross_compare_missing_a_child_is_not_linked(client, as_role, fake_mcrit):
    """Its matching parameters live on the children, so a deleted one takes them with
    it - the same reason it cannot be rerun."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    fake_mcrit._queued_by_id.pop(fake_mcrit.getJobData(job_id).all_dependencies[0])

    assert link_for(client, job_id) is None


if __name__ == "__main__":
    unittest.main()
