#!/usr/bin/python
"""The named orderings of the cross compare matrix, as pure functions.

`testResultPages.py` renders them through the real page over the captured report,
which is what proves the feature. These cover the inputs that report does not have:
a job with a single sample, an ordering asked for with nothing to order, and the two
fields the sort reads - family and version - arriving as something other than a
string. Both come out of an analysed binary, so "the backend always sends a str" is
an assumption rather than a guarantee, and a sort that raises takes the result page
with it.

Issue #42.
"""

import logging
import unittest

import pytest

from mcritweb.views.cross_compare import CROSS_ORDERINGS, family_sort_key, order_sample_ids

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


class Sample:
    """Only the three attributes the orderings read, so a test can state odd ones."""

    def __init__(self, sample_id, family="", version=""):
        self.sample_id = sample_id
        self.family = family
        self.version = version


def test_the_offered_orderings_are_the_ones_implemented():
    assert CROSS_ORDERINGS == ("clustered", "sample_id", "family")


def test_clustered_is_whatever_the_backend_computed():
    """It is the one ordering that cannot be derived from the samples, so it is passed
    through untouched - including the string ids the report uses."""
    samples = [Sample(2), Sample(0), Sample(1)]
    assert order_sample_ids(samples, "clustered", ["2", "0", "1"]) == ["2", "0", "1"]


def test_a_method_without_a_clustered_sequence_asks_for_no_ordering():
    """The view leaves the samples as they are for a None order, rather than dropping
    the matrix - which is what it did before named orderings existed."""
    assert order_sample_ids([Sample(0)], "clustered", None) is None


@pytest.mark.parametrize("ordering", CROSS_ORDERINGS)
def test_ordering_nothing_yields_nothing(ordering):
    assert order_sample_ids([], ordering, []) == []


@pytest.mark.parametrize("ordering", CROSS_ORDERINGS)
def test_a_job_with_one_sample_orders_to_that_sample(ordering):
    assert order_sample_ids([Sample(7, "win.x", "1.0")], ordering, ["7"]) in ([7], ["7"])


def test_sample_id_sorts_numerically_not_as_text():
    """The ids are ints and have to compare as ints - "10" sorts before "2"."""
    samples = [Sample(10), Sample(2), Sample(1)]
    assert order_sample_ids(samples, "sample_id") == [1, 2, 10]


def test_sample_id_keeps_query_samples_ahead_of_the_corpus():
    """A query sample carries a negative id."""
    samples = [Sample(3), Sample(-1), Sample(0)]
    assert order_sample_ids(samples, "sample_id") == [-1, 0, 3]


def test_family_groups_by_name_then_version():
    samples = [
        Sample(0, "win.citadel", "1.3.5.1"),
        Sample(1, "win.citadel", "1.3.4.0"),
        Sample(4, "win.vmzeus", "3.x"),
        Sample(6, "win.dridex", ""),
    ]
    assert order_sample_ids(samples, "family") == [1, 0, 6, 4]


def test_family_ignores_case_so_a_rename_does_not_split_a_family():
    samples = [Sample(0, "WIN.citadel"), Sample(1, "win.Citadel"), Sample(2, "win.dridex")]
    assert order_sample_ids(samples, "family") == [0, 1, 2]


def test_family_falls_back_to_the_sample_id_for_an_exact_tie():
    """Without a total order the matrix would reshuffle between two loads of the same
    page, since sorted() only promises stability against the input order."""
    samples = [Sample(9, "win.x", "1.0"), Sample(3, "win.x", "1.0"), Sample(5, "win.x", "1.0")]
    assert order_sample_ids(samples, "family") == [3, 5, 9]


@pytest.mark.parametrize("value", [None, "", 0, 12345, 1.5])
def test_a_family_or_version_that_is_not_a_string_still_sorts(value):
    samples = [Sample(1, value, value), Sample(0, "win.x", "1.0")]
    assert sorted(order_sample_ids(samples, "family")) == [0, 1]
    assert isinstance(family_sort_key(samples[0])[0], str)


if __name__ == "__main__":
    unittest.main()
