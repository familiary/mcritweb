#!/usr/bin/python
"""The MinHash matching parameter a job was submitted with, shown in its results.

A job records `band_matches_required` in its payload, and `parse_band_range` is the
only thing that ever writes it from the UI - so the value maps back to the label the
submit form showed. Nothing else about the matching configuration is recoverable
(see the helper's docstring), so these pin both halves: the value that is there, and
silence for the jobs where it is not. Issue #32.
"""

import inspect
import json
import logging
import pathlib
import re
import unittest

import pytest
from fixtureData import job_id_of, load
from mcrit.matchers.MatcherInterface import MatcherInterface
from mcrit.queue.LocalQueue import Job

from mcritweb.views.params import (
    BAND_RANGE_ARG_TO_VALUE,
    BAND_RANGE_LABELS,
    get_minhash_matching_label,
)

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

TEMPLATES = pathlib.Path(__file__).parent.parent / "mcritweb" / "templates"
SUBMIT_TEMPLATES = [
    TEMPLATES / "compare.html",
    TEMPLATES / "compare_versus.html",
    TEMPLATES / "cross_compare.html",
    TEMPLATES / "table" / "submit_or_query_dropzone.html",
]


def job_with_params(params, method="getMatchesForSample"):
    """A real mcrit Job - that is what the views hand the template, and its own
    accessors are part of what the helper has to survive."""
    return Job({"_id": "0" * 24, "payload": {"method": method, "params": params}}, None)


def job_from_fixture(report):
    return Job(load(f"{report}.job"), None)


# --- the helper ------------------------------------------------------------------


def test_the_slider_positions_stay_distinguishable():
    """Reading a job back only works while the positions map to distinct values - two
    positions sharing one would make the label a coin flip, and this is the only thing
    that would say so."""
    assert len(set(BAND_RANGE_ARG_TO_VALUE.values())) == len(BAND_RANGE_ARG_TO_VALUE)
    assert sorted(BAND_RANGE_ARG_TO_VALUE) == list(range(len(BAND_RANGE_LABELS)))


def test_band_off_is_a_different_mode_from_band_complete():
    """Pins the premise a code comment in params.py rests on, in the code that owns it.

    That comment used to say "Off" (0) and "Complete" (1) behave identically in mcrit,
    citing the `band_matches_required <= 1` branch in
    `MongoDbStorage._getCandidatesForMinHashesNumpy`. It was wrong and had never been
    true: `MatcherInterface._getMatchesRoutineInner` wraps the whole minhash stage in
    `if self._band_matches_required > 0`, so that branch is only reached when the stage
    runs at all. At 0 mcrit does pichash-only matching; at 1 it does full minhash
    matching keeping every candidate.

    The distinction is invisible from this repository - nothing here can observe which
    matching mcrit performed - so the only durable check is that the guard still exists
    upstream. If mcrit ever drops it, the two positions really would collapse, and this
    test is what should say so rather than a reader rediscovering it.
    """
    source = inspect.getsource(MatcherInterface._getMatchesRoutineInner)

    assert "self._band_matches_required > 0" in source, (
        "mcrit no longer skips the minhash stage at band_matches_required == 0. "
        "If that is deliberate, 'Off' and 'Complete' may now be the same mode and the "
        "comment in mcritweb/views/params.py needs revisiting.")


@pytest.mark.parametrize("template", SUBMIT_TEMPLATES, ids=lambda path: path.name)
def test_the_labels_are_the_ones_the_submit_forms_show(template):
    """The submit forms carry their own copy of the labels. If one of them is renamed
    there, a results page reporting the old name would be worse than reporting
    nothing - so the two copies have to stay identical."""
    slider_mapping = re.search(r"minhash_slider_mapping = (\[[^\]]*\])", template.read_text())
    assert slider_mapping, f"{template.name} no longer declares minhash_slider_mapping"
    assert json.loads(slider_mapping.group(1)) == BAND_RANGE_LABELS


@pytest.mark.parametrize(
    "band_matches_required, label",
    [(0, "Off"), (4, "Fast"), (2, "Standard"), (1, "Complete")],
)
def test_every_slider_setting_maps_back_to_its_label(band_matches_required, label):
    """The four values parse_band_range can emit, and the labels the submit forms
    show for them."""
    job = job_with_params(json.dumps({"band_matches_required": band_matches_required, "0": 0}))
    assert get_minhash_matching_label(job) == label


@pytest.mark.parametrize("report", ["matches_for_sample", "matches_for_sample_vs", "matches_for_query"])
def test_the_captured_jobs_report_the_setting_they_were_submitted_with(report):
    assert get_minhash_matching_label(job_from_fixture(report)) == "Standard"


def test_a_job_that_recorded_no_setting_has_no_label():
    """Jobs from before the setting existed, and jobs submitted through the CLI."""
    assert get_minhash_matching_label(job_with_params('{"0": 0}')) is None


@pytest.mark.parametrize("report", ["cross_compare", "unique_blocks"])
def test_jobs_that_never_carry_the_setting_have_no_label(report):
    """A cross compare holds only the ids of its child getMatchesForSampleVs jobs;
    the setting lives on those. Guessing from the server's current default would be
    wrong for every job older than the last config change."""
    assert get_minhash_matching_label(job_from_fixture(report)) is None


def test_a_value_the_slider_cannot_produce_is_not_given_a_slider_label():
    """The API accepts any integer for band_matches_required, so a job can carry a
    value no label covers. Reporting it verbatim is honest; rounding it to the
    nearest slider position would name a setting nobody selected."""
    label = get_minhash_matching_label(job_with_params('{"band_matches_required": 3, "0": 0}'))
    assert label is not None
    assert label not in BAND_RANGE_LABELS
    assert "3" in label


def test_a_boolean_is_not_read_as_a_band_count():
    """True == 1 in a dict lookup, which would silently name it 'Complete'."""
    assert get_minhash_matching_label(job_with_params('{"band_matches_required": true, "0": 0}')) is None


def test_malformed_params_yield_no_label_rather_than_an_exception():
    job = job_with_params("{this is not json")
    # Job.arguments - which the 'Task:' row above ours already renders through
    # job_info.parameters - raises on exactly this payload. The helper must not add
    # a second way for the same job to break a page.
    with pytest.raises(json.JSONDecodeError):
        job.arguments
    assert get_minhash_matching_label(job) is None


def test_a_job_without_a_payload_yields_no_label():
    assert get_minhash_matching_label(Job({"_id": "0" * 24}, None)) is None


def test_params_that_are_not_an_object_yield_no_label():
    assert get_minhash_matching_label(job_with_params("[2]")) is None


def test_an_explicit_null_yields_no_label():
    assert get_minhash_matching_label(job_with_params('{"band_matches_required": null, "0": 0}')) is None


# --- the pages -------------------------------------------------------------------


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


def shows_setting(response, label):
    """The macro renders the label into the cell next to its row heading - asserting
    on the bare word would pass on any page that happens to contain it."""
    rendered = " ".join(response.data.decode().split())
    return f'MinHash Matching: </td> <td valign="middle">{label}</td>' in rendered


@pytest.mark.parametrize("report", ["matches_for_sample", "matches_for_sample_vs", "matches_for_query"])
def test_the_result_page_shows_the_setting(client, as_role, report):
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of(report)}")
    assert response.status_code == 200
    assert shows_setting(response, "Standard")


def test_the_job_page_shows_the_setting(client, as_role):
    as_role("visitor")
    response = client.get(f"/data/jobs/{job_id_of('matches_for_sample')}")
    assert response.status_code == 200
    assert shows_setting(response, "Standard")


def test_a_page_for_a_job_without_the_setting_shows_no_row(client, as_role):
    """The cross compare is the job type that renders this table without ever carrying
    the setting - result_unique_blocks.html builds its own job table and never reaches
    the macro, so that one is covered at the helper above."""
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('cross_compare')}")
    assert response.status_code == 200
    assert b"MinHash Matching" not in response.data


if __name__ == "__main__":
    unittest.main()
