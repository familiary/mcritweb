#!/usr/bin/python
"""Which result templates are actually rendered by the suite, and by what.

Issue #99: `testResultPages.py` covered five report types, asserting HTTP 200 and the
absence of an error template. That proves a template compiles and that data reached it;
it cannot see a template that is never rendered at all, and eight of the eighteen
result/job templates were in that position.

This file measures coverage rather than assuming it. `flask.template_rendered` reports
what Flask actually rendered for a request, so `RENDERED_BY` below is checked against
the app's own behaviour, and `test_every_result_template_is_accounted_for` fails when a
new template appears with no entry - which is the ratchet the issue asks for, rather
than a one-time sweep that rots.

Two bugs fell out of writing it, both fixed in the same change and both invisible to a
status-code assertion:

  * the cross-compare custom-order path passed `job_info=result_json` - the result dict,
    not the Job - so `result_corrupted.html` rendered an empty job id and a "Delete job
    data" link built from nothing.
  * `result_compare_function.html` printed "Showing matches against family:
    {{ matching_result.getFamilyNameByFamilyId(famid) }}" while `famid` was never passed
    to it, and the page is filtered by *function* across all families anyway.
"""

import logging
import pathlib
import unittest
from contextlib import contextmanager

import pytest
from fixtureData import REPORTS, job_id_of
from flask import template_rendered

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

TEMPLATE_ROOT = pathlib.Path(__file__).parent.parent / "mcritweb" / "templates"

#: funids whose matched functions are all inside the captured lookup pool. The pool was
#: built from the 1v1 job (tests/fixtures/regenerate.py), so most 1vN funids resolve to
#: entries the capture does not hold and correctly render result_corrupted instead.
CLEAN_FUNID = {"matches_for_sample": 2, "matches_for_sample_vs": 875, "matches_for_query": 270}

#: url -> the template it must render. Every entry is checked against what Flask
#: actually rendered, not against a status code.
RENDERED_BY = [
    ("/data/result/{matches_for_sample}", "result_compare_all.html"),
    ("/data/result/{matches_for_query}", "result_compare_all.html"),
    ("/data/result/{matches_for_sample_vs}", "result_compare_vs.html"),
    ("/data/result/{cross_compare}", "result_cross.html"),
    ("/data/result/{unique_blocks}", "result_unique_blocks.html"),
    ("/data/result/{matches_for_sample}?famid=1", "result_compare_family.html"),
    ("/data/result/{matches_for_sample_vs}?famid=1", "result_compare_family.html"),
    ("/data/result/{matches_for_query}?famid=1", "result_compare_family.html"),
    ("/data/result/{matches_for_sample_vs}?samid=3", "result_compare_sample.html"),
    ("/data/result/{matches_for_sample}?funid=2", "result_compare_function.html"),
    ("/data/result/{matches_for_sample_vs}?funid=875", "result_compare_function.html"),
    ("/data/result/{matches_for_query}?funid=270", "result_compare_function.html"),
    ("/data/result/{matches_for_sample}?samid=3", "result_corrupted.html"),
    ("/data/result/{cross_compare}?custom=999", "result_corrupted.html"),
    ("/data/result/ffffffffffffffffffffffff", "result_invalid.html"),
    # #106 split what linkhunt answers: an id no job was ever stored under is
    # invalid, and "incompatible" is reserved for a job that exists and simply
    # holds no link report - cross_compare is not in LINKHUNTABLE_METHODS.
    ("/data/linkhunt/ffffffffffffffffffffffff", "result_invalid.html"),
    ("/data/linkhunt/{cross_compare}", "result_incompatible.html"),
    ("/data/result/{maintenance_rebuild_index}", "result_maintenance.html"),
    ("/data/result/{maintenance_recalculate_pichashes}", "result_maintenance.html"),
    ("/data/result/{maintenance_recalculate_minhashes}", "result_maintenance.html"),
    ("/data/jobs/{matches_for_sample}", "job_overview.html"),
    ("/data/jobs/ffffffffffffffffffffffff", "job_invalid.html"),
]

#: templates this suite still cannot reach offline, and why. Shrinking this is the work;
#: an entry here is a stated gap rather than a silent one.
UNCOVERED = {
    "result_empty.html":
        "needs a finished job with a falsy result. testResultPages.py drives it through "
        "a monkeypatched backend rather than a URL, so it has no entry here.",
    "result_compare_function_vs.html":
        "needs CorpusMcritClient.getMatchFunctionVs and getMatchesForPicHash, plus "
        "whatever views/functiondiff.py reaches for. The largest of these gaps.",
    "job_in_progress.html":
        "needs an unfinished job. testResultPages.py drives it through a purpose-built "
        "backend rather than a corpus URL.",
    "job_corrupted.html": "no view renders it; reachable only through data.job_by_id's "
                          "corrupted branch, which the corpus cannot stage.",
    "job_deleted.html": "no view renders it at all - see the grep in the test below.",
    "job_failed.html":
        "needs a failed or terminated job (#106 gave linkhunt a branch for one). Every\n"
        "corpus fixture was captured from a job that finished, so no URL reaches it.",
}


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@contextmanager
def records_templates(app):
    """What Flask actually rendered, rather than what we assume it did."""
    rendered = []

    def record(sender, template, **extra):
        rendered.append(template.name)

    template_rendered.connect(record, app)
    try:
        yield rendered
    finally:
        template_rendered.disconnect(record, app)


def job_ids():
    return {name: job_id_of(name) for name in REPORTS}


@pytest.mark.parametrize("url,template", RENDERED_BY, ids=[url for url, _ in RENDERED_BY])
def test_the_url_renders_the_template_it_is_supposed_to(app, client, as_role, url, template):
    as_role("visitor")

    with records_templates(app) as rendered:
        response = client.get(url.format(**job_ids()))

    assert response.status_code == 200, f"{url} -> {response.status_code}"
    assert template in rendered, f"{url} rendered {rendered}, not {template}"


def test_every_result_template_is_accounted_for():
    """The ratchet. A new result_*.html or job_*.html either gets a URL here or an
    explicit reason in UNCOVERED - it cannot quietly arrive with no coverage."""
    on_disk = {path.name for path in TEMPLATE_ROOT.glob("result_*.html")}
    on_disk |= {path.name for path in TEMPLATE_ROOT.glob("job_*.html")}
    covered = {template for _, template in RENDERED_BY}

    unaccounted = on_disk - covered - set(UNCOVERED)
    assert unaccounted == set(), f"no coverage and no stated reason: {sorted(unaccounted)}"

    stale = set(UNCOVERED) - on_disk
    assert stale == set(), f"UNCOVERED names a template that no longer exists: {sorted(stale)}"

    both = covered & set(UNCOVERED)
    assert both == set(), f"listed as uncovered but actually covered: {sorted(both)}"


def test_job_deleted_really_is_unreachable():
    """UNCOVERED claims no view renders job_deleted.html. Claims like that rot silently,
    so it is checked rather than asserted in a comment."""
    package = TEMPLATE_ROOT.parent
    renders = [path for path in package.rglob("*.py")
               if "job_deleted.html" in path.read_text()]

    assert renders == [], f"job_deleted.html is rendered after all, by {renders}"


# --- the two bugs the coverage above surfaced ---------------------------------

def test_a_cross_compare_with_a_bad_custom_order_names_the_job(client, as_role):
    """result_corrupted.html was handed the result dict instead of the Job, so it
    rendered an empty job id and a "Delete job data" link with nothing in it."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")

    page = client.get(f"/data/result/{job_id}?custom=999").get_data(as_text=True)

    assert "are corrupted" in page, "the premise: this is the corrupted path"
    assert job_id in page, "the page does not say which job it is talking about"
    assert f"/data/jobs/{job_id}/delete" in page, "the delete link points at nothing"


def test_the_function_page_does_not_talk_about_a_family_it_was_not_given(client, as_role):
    """The page filters by function across all families, and `famid` was never passed to
    it - so it printed "Showing matches against family:" and then stopped."""
    as_role("visitor")

    page = client.get(f"/data/result/{job_id_of('matches_for_sample')}?funid=2").get_data(as_text=True)

    assert "Showing matches against family:" not in page


if __name__ == "__main__":
    unittest.main()
