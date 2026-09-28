"""The "Matching Method Statistics" table, over the matches a page is actually showing.

A match report carries one job-wide `match_aggregation`, computed by the backend in
`MatcherInterface._summarizeMatches`. `MatchingResult.fromDict` copies that dict
through verbatim and no filter in `applyFilterValues()` touches it, so a result page
narrowed to one family or sample used to state the numbers of the whole job -
issue #38. On the win.dridex view of the captured 1-vs-corpus report that meant
claiming 756 functions / 151654 bytes where the honest answer is 4 / 249.

Four of the five fields are recomputable here without asking the backend anything:
they are counts over the very function matches the report already contains, so
aggregating the *narrowed* list describes exactly what is on screen. The functions
below mirror `_summarizeMatches` field for field, and
`tests/testMatchingStatistics.py` asserts that they reproduce the backend's own
numbers exactly, for every captured report, whenever nothing is filtered. That
equivalence is what makes this presentation rather than a second opinion on the
matching, which this repository does not get to have.

`num_self_matches` is the exception. Matches of the reference sample against itself
are dropped from the report's function list by `_summarizeMatches` before it is
serialized, so nothing here can recompute them. It is carried through job-wide and
the table labels it as such, rather than passing a job-wide number off as a filtered
one.
"""


def _on_screen_function_matches(matching_result):
    """The function matches left standing by everything the page narrowed.

    `applyFilterValues()` keeps two lists and splits its filters between them: the
    sample-level ones (`filter_family_name`, the score thresholds, `filter_unique_only`,
    `filter_exclude_own_family`) rebind `filtered_sample_matches` only, the
    function-level ones (`filter_exclude_pic`, `filter_func_unique`, the offset and
    score filters) rebind `filtered_function_matches` only. Counting one list alone
    therefore misses half the narrowings, which is how six of the nine filters used to
    leave this table job-wide.

    So a match is on screen when *both* survived: its own function is still in the
    function match list, and the sample it matched is still in the sample table. That
    is the definition `?famid=` and `?samid=` already imply - `filterToFamilyId` and
    `filterToSampleId` narrow both lists in lockstep - and extending it to the
    sample-level filters is what makes the two routes to the same view agree. On the
    captured 1-vs-corpus report, `?famid=3` and `filter_family_name=win.dridex` show
    the identical two samples; before this intersection the first said 4 own functions
    and the second said 756.

    The cost is that for those six filters the statistics now describe less than the
    function table below them, which mcrit still renders job-wide. That is the honest
    direction of the two: the page's subject is the samples it is willing to show, and
    a summary that outruns it is the bug in the issue.

    Unfiltered this cannot drop anything, so it does not disturb the equivalence with
    the backend above: `_aggregateMatchSampleSummary` derives the report's sample list
    from the same function matches, so every `matched_sample_id` is in it by
    construction.
    """
    sample_ids_on_screen = {sample_match.sample_id for sample_match in matching_result.filtered_sample_matches}
    return [
        function_match
        for function_match in matching_result.filtered_function_matches
        if function_match.matched_sample_id in sample_ids_on_screen
    ]


def _aggregate(function_matches):
    """Aggregate one matching method's share of a list of MatchedFunctionEntry."""
    # every entry is one (own function, matched function) pair, so the per-function
    # byte size has to be de-duplicated by own function id - the same thing the
    # backend gets from summing over a set of function ids. Summation order differs
    # from the backend's (a set there, first-seen order here), which is exact rather
    # than merely close because binweights are whole numbers far below 2**53.
    num_bytes_by_function_id = {}
    matched_function_ids = set()
    library_function_ids = set()
    for function_match in function_matches:
        num_bytes_by_function_id[function_match.function_id] = function_match.num_bytes
        matched_function_ids.add(function_match.matched_function_id)
        if function_match.match_is_library:
            library_function_ids.add(function_match.function_id)
    return {
        "num_own_functions_matched": len(num_bytes_by_function_id),
        "num_foreign_functions_matched": len(matched_function_ids),
        "num_own_functions_matched_as_library": len(library_function_ids),
        "bytes_matched": sum(num_bytes_by_function_id.values()),
    }


def matching_statistics(matching_result):
    """Statistics for the matches `matching_result` currently exposes.

    Returns the two per-method dicts the statistics table renders, plus `is_filtered`
    - whether the page is showing a subset of the job, which is what makes the
    job-wide `num_self_matches` need a label.
    """
    function_matches = _on_screen_function_matches(matching_result)
    statistics = {
        "minhash": _aggregate(match for match in function_matches if match.match_is_minhash),
        "pichash": _aggregate(match for match in function_matches if match.match_is_pichash),
        # measured against the report as it arrived, so this is "the page is showing a
        # subset of the job" and not "the user typed something into a filter box" - a
        # filter that happens to exclude nothing leaves the table honestly unlabelled.
        "is_filtered": len(function_matches) != len(matching_result.function_matches),
    }
    for method in ("minhash", "pichash"):
        statistics[method]["num_self_matches"] = matching_result.match_aggregation[method]["num_self_matches"]
    return statistics
