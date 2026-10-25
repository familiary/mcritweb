#!/usr/bin/python
"""The filter forms' number fields take numbers, and a default filter that is not one is
refused rather than stored as 0.

Every field of the result, link hunt, unique blocks and default filter forms was free text.
The views parse them with `parse_integer_query_param` and `parse_integer_post_param`, which
read anything else as no value: on a result page the filter was silently dropped, and the
settings page stored the default filter as 0 (or 100 for the maximum score) and reported
"Default filters successfully changed". A phone opened a full keyboard for a 0-100 score.
"""

from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of

from mcritweb.db import UserFilters


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.inputs = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name"):
            self.inputs[attrs["name"]] = attrs


SCORE = {"type": "number", "inputmode": "numeric", "min": "0", "max": "100", "step": "1"}
COUNT = {"type": "number", "inputmode": "numeric", "min": "0", "step": "1"}

#: page -> field -> the constraint attributes it must carry
PAGES = {
    f"/data/result/{job_id_of('matches_for_sample')}": {
        "filter_direct_min_score": SCORE, "filter_frequency_nonlib_min_score": SCORE,
        "filter_function_max_score": SCORE, "filter_max_num_families": COUNT,
        "filter_function_offset": {"type": "text", "pattern": "0x[0-9a-fA-F]+|[0-9]+"},
        "filter_family_name": {"type": "text", "pattern": None},
    },
    f"/data/result/{job_id_of('matches_for_sample')}?famid=1": {
        "filter_min_num_samples": COUNT, "filter_max_num_samples": COUNT,
    },
    f"/data/linkhunt/{job_id_of('matches_for_sample')}": {
        "filter_link_score": SCORE, "filter_min_size": COUNT, "filter_unpenalized_family_count": COUNT,
        "filter_min_offset": {"type": "text", "pattern": "0x[0-9a-fA-F]+|[0-9]+"},
        "filter_exclude_samples": {"type": "text", "pattern": r"[0-9]+(\s*,\s*[0-9]+)*"},
    },
    f"/data/result/{job_id_of('unique_blocks')}?tab=yara": {
        "min_score": SCORE, "max_block_length": COUNT, "required_per_sample": COUNT, "min_bytes": COUNT,
    },
    "/settings": {"filter_direct_min_score": SCORE, "filter_function_max_score": SCORE, "filter_max_num_families": COUNT},
}


@pytest.mark.parametrize("path", sorted(PAGES))
def test_a_number_field_takes_what_its_view_parses(client, as_role, path):
    as_role("visitor")
    inputs = Inputs(client.get(path).get_data(as_text=True)).inputs
    for name, expected in PAGES[path].items():
        assert {attr: inputs[name].get(attr) for attr in expected} == expected, (path, name)


def stored(app, user_id):
    with app.app_context():
        return UserFilters.fromDb(user_id).toDict()


def flashes(client):
    with client.session_transaction() as session:
        return session.get("_flashes", [])


@pytest.mark.parametrize("field, typed", [
    ("filter_direct_min_score", "abc"),
    ("filter_function_max_score", "9999"),
    ("filter_frequency_min_score", "-1"),
    ("filter_function_min_score", "50.5"),
    ("filter_max_num_families", "many"),
])
def test_a_default_filter_that_is_not_a_number_is_refused(app, client, as_role, field, typed):
    user_id = as_role("visitor")
    client.get("/settings")
    before = stored(app, user_id)

    response = client.post("/admin/change_default_filter", data={field: typed, "filter_direct_nonlib_min_score": "20"})

    assert response.status_code == 302
    assert stored(app, user_id) == before, "the refused filter was stored"
    [(category, message)] = flashes(client)
    assert category == "error" and message.startswith("Default filters not changed")
    # and the settings page opens on the form, with what was typed and the message beside the field
    inputs = Inputs(client.get("/settings").get_data(as_text=True)).inputs
    assert inputs[field].get("aria-invalid") == "true"
    assert inputs["filter_direct_nonlib_min_score"].get("value") == "20"


def test_a_default_filter_of_numbers_is_stored(app, client, as_role):
    user_id = as_role("visitor")
    client.post("/admin/change_default_filter", data={"filter_direct_min_score": "40", "filter_function_max_score": "100",
                                                      "filter_max_num_families": "", "filter_exclude_pic": "on"})
    filters = stored(app, user_id)
    assert (filters["filter_direct_min_score"], filters["filter_function_max_score"], filters["filter_max_num_families"],
            filters["filter_exclude_pic"]) == (40, 100, 0, True)
    assert [category for category, _ in flashes(client)] == ["success"]
