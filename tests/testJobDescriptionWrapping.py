#!/usr/bin/python
"""Long job descriptions have somewhere to break. Issue #41.

The description column of the jobs table can hold a token with no spaces in it - the
filename of an upload, a SHA-256, or the raw `job.parameters` string that
`job_description` falls back to for a method it does not recognise. Without an
`overflow-wrap`, that token cannot be broken, so it widens the column and pushes the
whole table past the edge of the viewport.

CSS is not renderable here, so these pin the two things that rot: the cell carrying
the class the rule targets, and the rule being there and staying scoped.
"""

import logging
import pathlib
import re
import unittest

import pytest
from fixtureData import job_id_of

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

STYLESHEET = pathlib.Path(__file__).resolve().parents[1] / "mcritweb" / "static" / "style.css"
JOB_ROW = pathlib.Path(__file__).resolve().parents[1] / "mcritweb" / "templates" / "table" / "job_row.html"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def test_every_description_cell_can_break_a_long_token():
    """job_row renders the description in two places - the per-method table and the
    "all jobs" one - and both need the class."""
    markup = JOB_ROW.read_text()

    rendering_cells = [line for line in markup.splitlines() if "job_description(job," in line and "<td" in line]
    assert len(rendering_cells) == 2, "job_row renders the description twice"
    for cell in rendering_cells:
        assert "job-description" in cell


def test_the_stylesheet_lets_that_cell_break():
    css = STYLESHEET.read_text()

    rule = re.search(r"table\.table td\.job-description\s*\{([^}]*)\}", css)
    assert rule is not None, "no rule for the description cell"
    assert "overflow-wrap" in rule.group(1)
    assert "anywhere" in rule.group(1), "break-word will not break a token alone on its line"


def test_the_class_reaches_a_rendered_jobs_page(client, as_role):
    as_role("visitor")

    page = client.get("/data/jobs").get_data(as_text=True)

    assert "job-description" in page
    assert "job-cell job-description" in page, "the click handler's class is still there"


def test_an_unrecognised_job_method_still_renders_its_raw_parameters(client, as_role, fake_mcrit, monkeypatch):
    """The fallback branch is where the longest strings come from, so it is the one
    the wrapping is for. It must also still be reachable."""
    import copy

    from mcrit.queue.LocalQueue import Job

    as_role("visitor")
    job_data = copy.deepcopy(fake_mcrit.getJobData(job_id_of("matches_for_sample"))._data)
    job_data["payload"]["method"] = "someFutureMethodWithAVeryLongNameIndeed"
    monkeypatch.setattr(fake_mcrit, "getQueueData", lambda *args, **kwargs: [Job(job_data, None)])

    page = client.get("/data/jobs").get_data(as_text=True)

    assert "someFutureMethodWithAVeryLongNameIndeed" in page
    assert "job-description" in page


@pytest.mark.parametrize(
    "path",
    [
        "/data/jobs/{job_id}",          # job_overview.html -> job_column_table
        "/data/result/{job_id}",        # result pages -> matching_result_job_column_table
        "/data/linkhunt/{job_id}",
    ],
)
def test_the_task_row_wraps_wherever_it_is_shown(client, as_role, path):
    """The jobs list is not the only place the raw parameters string appears. The
    "Task:" row of job_column_table prints the same string, and that macro reaches the
    job overview and - through matching_result_job_column_table - every result page. A
    120-character filename in an addBinarySample job widens those tables past the
    viewport exactly as it used to widen the list."""
    as_role("visitor")

    page = client.get(path.format(job_id=job_id_of("matches_for_sample"))).get_data(as_text=True)

    assert "getMatchesForSample(0, 2)" in page, "the Task row is not on this page"
    assert 'class="job-description">getMatchesForSample(0, 2)' in page


if __name__ == "__main__":
    unittest.main()


# --- the other two places the same string widens the page ---------------------
#
# Measured with Chromium against a job whose filename is one 120-character unbroken
# token, at a 1280x800 viewport, on /data/jobs/<id>:
#
#     h1 rule disabled (master):        document.scrollWidth = 3649
#     h1 rule as shipped here:          document.scrollWidth = 1638
#
# A heading that overflows does not enlarge its own bounding box - it only pushes
# scrollWidth out - so it is invisible to an element-overflow scan and very visible to a
# reader. That is why it survived the first pass.

def test_the_content_headings_can_break_a_long_token():
    """job_overview.html and job_in_progress.html print the raw job.parameters as their
    <h1>. Scoped to section.content so the navbar brand and the manual are untouched."""
    css = STYLESHEET.read_text()

    assert "section.content h1 {" in css
    rule = css.split("section.content h1 {", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: anywhere" in rule


def test_the_error_message_is_not_wrapped_in_invalid_markup():
    """The template carried `<div white-space: pre-wrap;>` - three bogus attributes, not
    a style - around a <pre>, which is white-space: pre and so never wrapped at all."""
    template = (STYLESHEET.parent.parent / "templates" / "table" / "column_table.html").read_text()

    assert "<div white-space: pre-wrap;>" not in template, "invalid attributes are still there"
    assert 'class="job-error"' in template


def test_the_error_message_rule_lets_a_traceback_wrap():
    css = STYLESHEET.read_text()

    assert "table.table td .job-error {" in css
    rule = css.split("table.table td .job-error {", 1)[1].split("}", 1)[0]
    assert "white-space: pre-wrap" in rule
    assert "overflow-wrap: anywhere" in rule
