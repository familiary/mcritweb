#!/usr/bin/python
"""The link hunt's filter form, submitted with fields left empty.

The form sends every field, and an empty one parses to None. Both presets set the
unpenalized family count, but a filter the user fills in passed None on to mcrit's
getLinkHuntResults, which compares the count to an int: a 500.
"""

import logging
import re
import unittest

import pytest
from fixtureData import job_id_of

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.mark.parametrize("given_filter", [{"filter_link_score": "5"}, {"filter_min_score": "70"}])
def test_a_filter_with_the_family_count_left_empty_uses_the_default(client, as_role, given_filter):
    as_role("visitor")
    response = client.get(
        f"/data/linkhunt/{job_id_of('matches_for_sample')}",
        query_string={"filter_button_action": "filter", "filter_unpenalized_family_count": "", **given_filter},
    )
    assert response.status_code == 200
    # the form shows the count that was applied
    assert re.search(r"name=.filter_unpenalized_family_count.[^>]*value=.?2\b", response.get_data(as_text=True))


def test_a_family_count_that_is_given_is_kept(client, as_role):
    as_role("visitor")
    response = client.get(
        f"/data/linkhunt/{job_id_of('matches_for_sample')}",
        query_string={"filter_button_action": "filter", "filter_link_score": "5", "filter_unpenalized_family_count": "3"},
    )
    assert response.status_code == 200
    assert re.search(r"name=.filter_unpenalized_family_count.[^>]*value=.?3\b", response.get_data(as_text=True))


def field_values(page):
    """{name: value} of the form's text fields; a field without a value maps to None."""
    return {match.group("name"): match.group("value") for match in
            re.finditer(r"<input[^>]*name=.(?P<name>filter_\w+).(?:[^>]*value=\"(?P<value>[^\"]*)\")?", page)}


@pytest.mark.parametrize("given, shown", [
    # the link score field printed the link score whenever the *library* score was set, so it
    # showed nothing for the one filter the field is for, and the next submit dropped it
    ({"filter_link_score": "40"}, "40"),
    # and with the library score alone it printed the link score it did not have: "None"
    ({"filter_lib_min_score": "50"}, None),
])
def test_the_link_score_field_shows_the_link_score(client, as_role, given, shown):
    as_role("visitor")
    response = client.get(f"/data/linkhunt/{job_id_of('matches_for_sample')}",
                          query_string={"filter_button_action": "filter", **given})
    assert response.status_code == 200
    assert field_values(response.get_data(as_text=True))["filter_link_score"] == shown


if __name__ == "__main__":
    unittest.main()
