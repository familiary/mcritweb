"""Sorting for the match tables on the result pages (issue #50).

The sort happens here, in the view, and not in the browser: the result tables are
server-paginated, so a client-side sort - DataTables, as `jobs.html` uses it - would
order the ten or hundred rows the page happens to be showing and leave the rest of the
list where it was. That reads as a correctness bug, not as a sort.

It does not need a backend round trip either. A `MatchingResult` arrives whole and is
already materialised in memory: `getBestSampleMatchesPerFamily`, `getSampleMatches` and
`getAggregatedFunctionMatches` each build the *entire* list and only then slice it for
the requested page, so asking them for the full list and sorting it here costs a
`sorted()` over rows that were being built anyway.

The keys below are keyed by the column ids of `UserColumnSettings._default_settings`,
the same ids the row macros in `templates/table/match_row.html` dispatch on, so a
header cell and its sort order cannot drift apart. A column with no entry here is
simply not sortable, and an unknown `sort` parameter leaves the natural order alone.

Every key takes the row *and* the `MatchingResult`, because three columns are not
properties of the row at all - the unique score, the library flag and the uniqueness
flag are all looked up on the result.
"""


def _text(value):
    """Sort key for a text column.

    Case-folded, because the alternative orders `Zeus` before `apt1` and reads as a
    bug. `None` sorts with the empty strings rather than raising.
    """
    return (value or "").lower()


def _offset(value):
    """Sort key for an offset that may not have been resolved.

    `matched_offset` stays `None` when the backend no longer knows the matched
    function (see `assign_matched_offsets`); those rows sort before offset 0.
    """
    return -1 if value is None else value


#: The family/library/sample match tables - rows are `MatchedSampleEntry`.
FAMILY_SAMPLE_SORT_KEYS = {
    "family_name": lambda row, result: _text(row.family),
    "version": lambda row, result: _text(row.version),
    "sample_id": lambda row, result: row.sample_id,
    "sha256": lambda row, result: _text(row.sha256),
    "filename": lambda row, result: _text(row.filename),
    "bitness": lambda row, result: row.bitness,
    "num_functions": lambda row, result: row.num_functions,
    "num_minhash": lambda row, result: row.matched_functions_minhash,
    "num_pichash": lambda row, result: row.matched_functions_pichash,
    "num_library": lambda row, result: row.matched_functions_library,
    "direct_score": lambda row, result: row.matched_percent_score_weighted,
    "direct_nonlib_score": lambda row, result: row.matched_percent_nonlib_score_weighted,
    "frequency_score": lambda row, result: row.matched_percent_frequency_weighted,
    "frequency_nonlib_score": lambda row, result: row.matched_percent_nonlib_frequency_weighted,
    "uniq_score": lambda row, result: result.getUniqueFamilyMatchInfoForSample(row.sample_id)["unique_score"],
}

#: The aggregated function match table - rows are the dicts built by
#: `MatchingResult.getAggregatedFunctionMatches`, whose keys differ from the column ids.
AGGREGATED_FUNCTION_SORT_KEYS = {
    "matched_function_id": lambda row, result: row["function_id"],
    "offset": lambda row, result: row["offset"],
    "num_bytes": lambda row, result: row["num_bytes"],
    "num_matched_families": lambda row, result: row["num_families_matched"],
    "num_matched_samples": lambda row, result: row["num_samples_matched"],
    "num_matched_functions": lambda row, result: row["num_functions_matched"],
    "best_score": lambda row, result: row["best_score"],
    "num_minhash": lambda row, result: row["minhash_matches"],
    "num_pichash": lambda row, result: row["pichash_matches"],
    "is_library_match": lambda row, result: bool(row["library_matches"]),
    "is_unique_match": lambda row, result: bool(row["is_family_unique"]),
}

#: The function-to-function match tables - rows are `MatchedFunctionEntry`.
#: `family_name_b` and `sample_id_b` only appear on `result_compare_function.html`,
#: which keeps its own header markup but sorts through the same keys.
MATCHED_FUNCTION_SORT_KEYS = {
    "function_id_a": lambda row, result: row.function_id,
    "offset_a": lambda row, result: row.offset,
    "offset_b": lambda row, result: _offset(row.matched_offset),
    "function_id_b": lambda row, result: row.matched_function_id,
    "family_name_b": lambda row, result: _text(result.getFamilyNameByFamilyId(row.matched_family_id)),
    "sample_id_b": lambda row, result: row.matched_sample_id,
    "num_bytes": lambda row, result: row.num_bytes,
    "best_score": lambda row, result: row.matched_score,
    "is_minhash_match": lambda row, result: bool(row.match_is_minhash),
    "is_pichash_match": lambda row, result: bool(row.match_is_pichash),
    "is_library_match": lambda row, result: bool(result.hasLibraryMatch(row.function_id)),
    # getFamilyIdsMatchedByFunctionId answers 0, not an empty set, for a function it
    # does not know - so this cannot just call len()
    "is_unique_match": lambda row, result: len(result.getFamilyIdsMatchedByFunctionId(row.function_id) or ()) == 1,
}


def sorted_page(rows, pagination, sort_keys, matching_result):
    """The rows of `pagination`'s current page, in the order it asks for.

    `sorted` is stable, so rows the chosen column cannot tell apart keep the order the
    backend delivered them in - which is the score ordering the pages show by default.
    """
    sort_key = sort_keys.get(pagination.sort_by)
    if sort_key is not None:
        rows = sorted(rows, key=lambda row: sort_key(row, matching_result), reverse=not pagination.is_ascending)
    return rows[pagination.start_index:pagination.start_index + pagination.limit]
