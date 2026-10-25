#!/usr/bin/python
"""What a screen reader is told while a job, an upload or a page load is under way.

A live region is read out when its text changes, and only one the screen reader already
knew of; a region that is created, or shown, with its text in it is often not read at
all. So each status the pages announce is in place and rendered, empty, before it is
written to: the import's box sits in a region that is there from the start, the upload
button's spinner is a picture with its meaning in a status beside the button, and
page_loading.js makes its status when it runs rather than with the overlay
(tests/testPageStructureBrowser.py drives that one). A job's progress, a number in a
cell, is also a native progress bar, which assistive technology reports as one.
"""

import pytest
from testAccessibility import Page
from testJobOverview import PARENT_JOB_ID, CountingJobs, job_data, running


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def by_id(page, element_id):
    found = [element for element in page.elements if element.attrs.get("id") == element_id]
    assert len(found) == 1, element_id
    return found[0]


def ancestors(element):
    node = element.parent
    while node is not None:
        yield node
        node = node.parent


def test_the_upload_spinner_is_a_picture_and_its_status_sits_beside_the_button(client, as_role):
    as_role("contributor")
    page = Page(client.get("/data/submit").get_data(as_text=True))

    spinner = by_id(page, "submit-spinner")
    assert "role" not in spinner.attrs and spinner.attrs.get("aria-hidden") == "true"
    status = by_id(page, "submit-status")
    assert status.attrs.get("role") == "status" and not status.text_content().strip()
    assert "button" not in [node.tag for node in ancestors(status)]


def test_the_import_status_is_a_region_rendered_before_it_says_anything(client, as_role):
    as_role("admin")
    page = Page(client.get("/data/import").get_data(as_text=True))

    box = by_id(page, "import-status")
    assert "role" not in box.attrs
    region = box.parent
    assert region.attrs.get("role") == "status"
    assert "display:none" not in (region.attrs.get("style") or "").replace(" ", "")


def test_a_running_job_s_progress_is_a_progress_bar(app, client, as_role):
    jobs = CountingJobs(running(job_data(PARENT_JOB_ID, 10, params='{"0": 7}')), [])
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: jobs
    as_role("visitor")
    page = Page(client.get(f"/data/jobs/{PARENT_JOB_ID}").get_data(as_text=True))

    bars = [element for element in by_id(page, "job-progress").children if element.tag == "progress"]
    assert len(bars) == 1
    assert bars[0].attrs.get("value") == "25.00" and bars[0].attrs.get("max") == "100"
    assert bars[0].attrs.get("aria-label") == "Job progress"
