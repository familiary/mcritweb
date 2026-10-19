#!/usr/bin/python
"""A cross compare's row in a jobs table lists the samples of the unnamed family too.

`job_description` in templates/table/job_row.html groups a cross compare's samples by
family, and skipped every sample whose `family_id` was falsy - which family 0 is. Every
mcrit storage keeps family 0 as the unnamed family, and a sample submitted without a
family lands in it, so the row of a cross compare over such samples listed fewer samples
than the count it opens with. Every other description in that file shows family 0 as
"Unnamed", through `format_family_name`.

Rendered here straight from the macro: the corpus's family 0 holds no samples.
"""

import json
import logging
import re
import types

from mcrit.queue.LocalQueue import Job

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: A family's line in the description, once runs of whitespace are collapsed to one space.
FAMILY_LINE = re.compile(r'<a href="/explore/families/(\d+)">([^<]*)</a> \| <i class="fa-solid fa-virus " title="Sample ID"></i> ([^<]*)<br />')


def cross_job(sample_ids):
    """A cross compare over `sample_ids`, stored as the queue stores one: its first
    argument maps each sample id to the job that matched it."""
    params = json.dumps({"0": {str(sample_id): "%024x" % sample_id for sample_id in sample_ids}})
    return Job({"_id": "c" * 24, "number": 1, "payload": {"method": "combineMatchesToCross", "params": params, "file_params": "{}", "descriptor": None}}, None)


def sample(family_id, family):
    """What the description reads of a sample."""
    return types.SimpleNamespace(family_id=family_id, family=family)


def family_lines(app, job, samples_by_id):
    source = "{% from 'table/job_row.html' import job_description %}{{ job_description(job, {}, samples_by_id) }}"
    with app.test_request_context("/"):
        rendered = app.jinja_env.from_string(source).render(job=job, samples_by_id=samples_by_id)
    return FAMILY_LINE.findall(re.sub(r"\s+", " ", rendered))


def test_samples_of_the_unnamed_family_are_listed(app):
    samples_by_id = {5: sample(0, ""), 6: sample(1, "win.citadel"), 7: sample(0, "")}

    lines = family_lines(app, cross_job([5, 6, 7]), samples_by_id)

    assert lines == [("0", "Unnamed", "5, 7"), ("1", "win.citadel", "6")]


def test_a_sample_the_backend_no_longer_has_is_still_left_out(app):
    lines = family_lines(app, cross_job([5, 404]), {5: sample(0, "")})

    assert lines == [("0", "Unnamed", "5")]


def test_a_sample_without_a_family_id_is_still_left_out(app):
    """The guard the check was written for: an entry that names no family at all."""
    lines = family_lines(app, cross_job([5, 6]), {5: sample(None, ""), 6: sample(1, "win.citadel")})

    assert lines == [("1", "win.citadel", "6")]
