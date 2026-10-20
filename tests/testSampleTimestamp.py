"""A sample submitted without a timestamp has none, and Jinja prints a None as "None".

mcrit stores such a sample's timestamp as null; once mcrit reads it back as None
(familiary/mcrit fix/sample-search-sort), the start page's latest samples, through the
`date` filter, and the query sample page printed it.
"""

import copy
import datetime
import re

import pytest
from mcrit.storage.SampleEntry import SampleEntry


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def test_the_date_filter_leaves_a_missing_timestamp_blank(app):
    date = app.jinja_env.filters["date"]
    assert date(datetime.datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02"
    assert date("2026-01-02T03-04-05") == "2026-01-02"
    assert date(None) == ""


def _without_timestamp(fake_mcrit, sample_id):
    sample = copy.deepcopy(fake_mcrit._samples[0])
    sample.sample_id = sample_id
    sample.timestamp = None
    fake_mcrit._samples[sample_id] = sample
    return sample


def test_the_query_sample_page_leaves_a_missing_timestamp_blank(client, as_role, fake_mcrit):
    as_role("visitor")
    _without_timestamp(fake_mcrit, -1)

    page = client.get("/explore/samples/-1").get_data(as_text=True)

    cell = re.search(r"Timestamp</t[hd]>\s*<td[^>]*>(.*?)</td>", page, re.S)
    assert cell is not None, "the query sample page has no timestamp row"
    assert cell.group(1).strip() == ""


def test_the_latest_samples_leave_a_missing_timestamp_blank(client, as_role, fake_mcrit):
    # the start page's "latest samples" table is the one that shows a sample's timestamp
    sample_dict = next(iter(fake_mcrit._samples.values())).toDict()
    sample_dict["timestamp"] = None
    try:
        SampleEntry.fromDict(sample_dict)
    except TypeError:
        # the start page reads samples with mcrit's SampleEntry.fromDict, which fails on a missing
        # timestamp in mcrit releases without familiary/mcrit#272; MCRITweb can't get past that
        pytest.skip("the installed mcrit's SampleEntry.fromDict can't read a missing timestamp (familiary/mcrit#272)")
    as_role("visitor")
    for sample in fake_mcrit._samples.values():
        sample.timestamp = None

    page = client.get("/").get_data(as_text=True)

    assert 'class="sample-row' in page
    assert "<td>None</td>" not in page
