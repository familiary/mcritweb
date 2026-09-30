#!/usr/bin/python
"""Recomputes mcrit's six sample scores from the function-level match report.

Issue #7 asks whether the nonlib frequency score is calculated correctly. The
arithmetic lives in mcrit (`MatcherInterface._aggregateMatchSampleSummary`), but the
captured reports carry both the inputs (`matches.functions`) and the outputs
(`matches.samples[*].matched`), so the claim is checkable from here without a backend.

This is the executable half of docs/adr/0009-nonlib-frequency-score.md. It is a
characterisation test, not a regression guard on any change in this repo: it fails if
a re-captured fixture no longer agrees with the formula the ADR verified, which is
exactly when that ADR needs revisiting.

The reimplementation below is deliberately a transcription rather than a call into
mcrit - a test that recomputes a value by invoking the code under test proves only
that the code is deterministic.
"""

import logging
import math
import unittest
from collections import defaultdict

import pytest
from fixtureData import load

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

# mcrit/matchers/MatcherFlags.py
IS_MINHASH_FLAG = 1
IS_PICHASH_FLAG = 1 << 1
IS_LIBRARY_FLAG = 1 << 2

#: MinHashConfig.MINHASH_FN_MIN_INS - the threshold mcrit sums the denominator over.
MINHASH_FN_MIN_INS = 10

KINDS = ("unweighted", "score_weighted", "frequency_weighted")

REPORTS = ("matches_for_sample", "matches_for_query", "matches_for_sample_vs")


def denominators(functions):
    """(matchable bytes, nonlibrary bytes) - the two divisors mcrit uses.

    A function counts toward the total once it is big enough to be matched at all;
    the nonlibrary total then drops every function that matched a library sample
    *anywhere* in the corpus, which is the same set the `nonlib_` numerators skip.
    """
    matchable = sum(f["num_bytes"] for f in functions if f["num_instructions"] >= MINHASH_FN_MIN_INS)
    library = sum(f["num_bytes"] for f in functions if any(m[-1] & IS_LIBRARY_FLAG for m in f["matches"]))
    return matchable, matchable - library


def family_adjustment(functions):
    """Divisor per function: how many families it turns up in, log-binned."""
    return {
        f["fid"]: (1 if len({m[0] for m in f["matches"]}) < 3 else 1 + int(math.log(len({m[0] for m in f["matches"]}), 2)))
        for f in functions
    }


def recompute(report):
    """{foreign sample id: {kind: percent}}, from the function-level report alone."""
    functions = report["matches"]["functions"]
    by_fid = {f["fid"]: f for f in functions}
    matchable, nonlibrary = denominators(functions)
    adjustment = family_adjustment(functions)

    per_sample = defaultdict(dict)
    for f in functions:
        has_library = any(m[-1] & IS_LIBRARY_FLAG for m in f["matches"])
        for family_id, sample_id, function_id, score, flags in f["matches"]:
            if not flags & (IS_MINHASH_FLAG | IS_PICHASH_FLAG):
                continue
            # one contribution per (own function, foreign sample), at its best score
            best = per_sample[sample_id].get(f["fid"], (0.0, has_library))[0]
            per_sample[sample_id][f["fid"]] = (max(best, score), has_library)

    percents = {}
    for sample_id, contributions in per_sample.items():
        counted = dict.fromkeys(KINDS, 0.0)
        counted.update(dict.fromkeys(["nonlib_" + kind for kind in KINDS], 0.0))
        for fid, (score, has_library) in contributions.items():
            increments = {
                "unweighted": by_fid[fid]["num_bytes"],
                "score_weighted": by_fid[fid]["num_bytes"] * score / 100.0,
            }
            increments["frequency_weighted"] = increments["score_weighted"] / adjustment[fid]
            for kind in KINDS:
                counted[kind] += increments[kind]
                if not has_library:
                    counted["nonlib_" + kind] += increments[kind]
        percents[sample_id] = {
            kind: (100.0 * counted[kind] / matchable if matchable else 0.0) for kind in KINDS
        }
        percents[sample_id].update(
            {"nonlib_" + kind: (100.0 * counted["nonlib_" + kind] / nonlibrary if nonlibrary else 0.0) for kind in KINDS}
        )
    return percents


@pytest.mark.parametrize("report_name", REPORTS)
def test_sample_percentages_are_reproducible_from_the_function_report(report_name):
    """Every `matched.percent` cell mcrit shipped is the formula applied to the
    function matches it shipped alongside it - so the value the page shows is the
    value the arithmetic produces, and issue #7's symptom is not a lost numerator.
    """
    report = load(f"{report_name}.result")
    recomputed = recompute(report)

    checked = 0
    for entry in report["matches"]["samples"]:
        got = recomputed.get(entry["sample_id"], {})
        for kind in list(KINDS) + ["nonlib_" + kind for kind in KINDS]:
            assert entry["matched"]["percent"][kind] == pytest.approx(got.get(kind, 0.0), abs=1e-9), (
                f"{report_name} sample {entry['sample_id']} {kind}"
            )
            checked += 1
    assert checked == 6 * len(report["matches"]["samples"])


@pytest.mark.parametrize("report_name", REPORTS)
def test_the_two_denominators_differ_and_neither_is_the_sample_binweight(report_name):
    """The plain and `nonlib_` columns are normalised against different totals, and
    the hover text on all four names a third number - the sample's full binweight -
    as if it were the divisor. Recovering the real divisor per column from the bytes
    and the percent mcrit shipped is what makes that checkable from the wire format.
    """
    report = load(f"{report_name}.result")
    binweight = report["info"]["sample"]["binweight"]
    matchable, nonlibrary = denominators(report["matches"]["functions"])

    assert matchable < binweight, "functions below the threshold should be excluded"
    assert nonlibrary <= matchable

    for entry in report["matches"]["samples"]:
        for kind in KINDS:
            for name, expected in ((kind, matchable), ("nonlib_" + kind, nonlibrary)):
                percent = entry["matched"]["percent"][name]
                if not percent:
                    continue
                recovered = 100.0 * entry["matched"]["bytes"][name] / percent
                assert recovered == pytest.approx(expected, rel=1e-9), (
                    f"{report_name} sample {entry['sample_id']} {name} divides by {recovered}"
                )
                assert recovered != pytest.approx(binweight, rel=1e-9)


if __name__ == "__main__":
    unittest.main()
