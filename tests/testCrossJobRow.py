#!/usr/bin/python
"""A cross compare's row in a jobs table lists its samples by family, in one pass.

`job_description` in templates/table/job_row.html groups a cross compare's samples by
family. It used to rebuild a family's whole list for every sample it added to it -
`grouped_samples.get(family_id, []) + [sample_id]`, quadratic in the size of the family -
and, for every family it printed, walked the whole grouping again in an empty loop left
over from an earlier shape of the template. Neither changed what the row says: the empty
loop printed nothing but its own indentation, once per family for every family. Issue
#198.

Rendered here straight from the macro, for jobs larger than the corpus has - its one
cross compare is five samples in three families.
"""

import json
import logging
import re
import types

from jinja2 import nodes
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


def sample(family_id):
    """What the description reads of a sample."""
    return types.SimpleNamespace(family_id=family_id, family=f"family-{family_id}")


def describe(app, job, samples_by_id):
    source = "{% from 'table/job_row.html' import job_description %}{{ job_description(job, {}, samples_by_id) }}"
    with app.test_request_context("/"):
        return app.jinja_env.from_string(source).render(job=job, samples_by_id=samples_by_id)


def test_the_samples_are_listed_by_family(app):
    """Families in the order the job first names one of their samples, samples in the
    order the job names them, and a sample the backend no longer has left out."""
    samples_by_id = {11: sample(1), 21: sample(2), 12: sample(1), 31: sample(3), 22: sample(2), 13: sample(1)}
    job = cross_job([11, 21, 12, 404, 31, 22, 13])

    lines = FAMILY_LINE.findall(re.sub(r"\s+", " ", describe(app, job, samples_by_id)))

    assert lines == [("1", "family-1", "11, 12, 13"), ("2", "family-2", "21, 22"), ("3", "family-3", "31")]


def test_the_row_does_not_grow_with_the_square_of_its_families(app):
    """What the empty loop did show: its indentation, once for every family, after each
    family's line. The lines from one family to the next may not depend on how many
    families there are."""
    def gaps_between_families(num_families):
        samples_by_id = {family_id: sample(family_id) for family_id in range(1, num_families + 1)}
        lines = describe(app, cross_job(samples_by_id), samples_by_id).splitlines()
        starts = [number for number, line in enumerate(lines) if 'title="Family ID"' in line]
        assert len(starts) == num_families
        return {later - earlier for earlier, later in zip(starts, starts[1:])}

    assert gaps_between_families(2) == gaps_between_families(40)


def test_a_family_is_appended_to_rather_than_copied_for_every_sample(app):
    """`family + [sample_id]` copies the list it extends, so doing it for every sample
    is quadratic in the size of the family. The rows read the same either way, so this
    reads the template: no list may be concatenated in the cross compare's branch."""
    source = app.jinja_loader.get_source(app.jinja_env, "table/job_row.html")[0]
    branch = next(
        node for node in app.jinja_env.parse(source).find_all(nodes.If)
        if isinstance(node.test, nodes.Compare)
        and any(isinstance(operand.expr, nodes.Const) and operand.expr.value == "combineMatchesToCross" for operand in node.test.ops)
    )

    concatenations = [
        addition for statement in branch.body for addition in statement.find_all(nodes.Add)
        if isinstance(addition.left, nodes.List) or isinstance(addition.right, nodes.List)
    ]

    assert not concatenations, f"a list is concatenated on line {concatenations[0].lineno} of job_row.html"
