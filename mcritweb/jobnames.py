"""One display name per job method.

The queue identifies a job by the RPC entry point that created it -
`getMatchesForSample`, `combineMatchesToCross`. That is the right name for a log line
and the wrong one for a heading, and until now every page answered the question
differently: the job list wrote "Match 1vN", the job overview printed
`getMatchesForSample(0, 2)`, two result pages printed the bare method, and four more
printed `matching_result.method` - an attribute `MatchingResult` does not have, so those
headings rendered as nothing at all. See issue #39.

The names below are exactly the ones `templates/table/job_row.html` was already using,
so the job list reads the same as before; everything else now agrees with it. The RPC
name has not been hidden anywhere - `job_column_table` still shows the full
`parameters`, arguments and all, directly beneath every heading this feeds.

Kept free of Flask and mcrit imports on purpose (see issue #88): it is a lookup table,
and a test should not have to build an app to check it.
"""

import json

JOB_METHOD_NAMES = {
    # matching
    "getMatchesForSample": "Match 1vN",
    "getMatchesForSampleVs": "Match 1v1",
    "getMatchesForSampleVsGroup": "Match 1vGroup",
    "combineMatchesToCross": "CrossCompare",
    # queries
    "getMatchesForUnmappedBinary": "Match Binary (unmapped)",
    "getMatchesForMappedBinary": "Match Binary (mapped)",
    "getMatchesForSmdaReport": "Match SMDA Report",
    # blocks
    "getUniqueBlocks": "UniqueBlocks",
    # minhashing and index maintenance
    "updateMinHashesForSample": "Update MinHash",
    "updateMinHashes": "Update all missing MinHashes",
    "rebuildIndex": "Rebuild full Index",
    "rebuildPicBlockHashIndex": "Rebuild PicBlockHash Index",
    "recalculatePicHashes": "Recalculate PicHashes",
    "recalculateMinHashes": "Recalculate MinHashes and Index",
    "repairMinHashes": "Repair MinHashes",
    "recomputeFamilyStats": "Recompute Family Statistics",
    # collection changes
    "addBinarySample": "Add Binary",
    "deleteSample": "Delete Sample",
    "modifySample": "Modify Sample",
    "deleteFamily": "Delete Family",
    "modifyFamily": "Modify Family",
    # maintenance
    "doDbCleanup": "Database Cleanup",
}


def job_method_name(method):
    """The display name for a job method.

    A method this table does not know is shown as-is rather than as a placeholder: a
    new job type added in the backend should still be identifiable here, and the raw
    name is more use than "Unknown". An absent method is the only case with nothing to
    fall back on.
    """
    if not method:
        return "Unknown job"
    return JOB_METHOD_NAMES.get(method, method)


#: The names MCRITweb's own forms give the `band_matches_required` setting, indexed by its
#: value - the slider in compare.html and the dropzone use the same four.
MINHASH_MATCHING_NAMES = ("Off", "Fast", "Standard", "Complete")

#: How a job's named options read on a page, keyed by the parameter name mcrit's queue
#: stores them under. Anything not listed is shown as `name: value`.
OPTION_LABELS = {
    "band_matches_required": "MinHash matching",
    "minhash_threshold": "MinHash threshold",
    "pichash_size": "PicHash size",
    "force_recalculation": "forced recalculation",
    "exclude_self_matches": "self matches excluded",
    "sample_group_only": "sample group only",
    "family_id": "family",
}


def _split_params(job):
    """The positional arguments of a job in call order and its named options, read from
    the queue's own record rather than from `Job.parameters`, which flattens the two into
    one anonymous list - which is how `getMatchesForMappedBinary(None, 31850496, 2)` came
    to be the task line of a query result page (issue #40)."""
    positional, named = [], {}
    try:
        payload = job.payload
    except (KeyError, AttributeError, TypeError):
        payload = None
    if not isinstance(payload, dict) or not payload.get("params"):
        return positional, named
    try:
        params = json.loads(payload["params"])
    except (TypeError, ValueError):
        return positional, named
    if not isinstance(params, dict):
        return positional, named
    indexed = {}
    for key, value in params.items():
        try:
            indexed[int(key)] = value
        except (TypeError, ValueError):
            named[key] = value
    positional = [indexed[index] for index in sorted(indexed)]
    return positional, named


def _format_option(name, value):
    if name == "band_matches_required" and isinstance(value, int) and 0 <= value < len(MINHASH_MATCHING_NAMES):
        return f"{OPTION_LABELS[name]}: {MINHASH_MATCHING_NAMES[value]}"
    if isinstance(value, bool):
        return OPTION_LABELS.get(name, name) if value else None
    return f"{OPTION_LABELS.get(name, name)}: {value}"


def _sample_word(count):
    return "1 sample" if count == 1 else f"{count} samples"


def _short_sha(job):
    sha256 = getattr(job, "sha256", None)
    return sha256[:8] if isinstance(sha256, str) and sha256 else None


def _task_details(job, positional):
    """What a job was asked to do, in the terms its own page uses: sample ids, a base
    address, a family - never the RPC call with its file placeholders and unnamed ints."""
    method = getattr(job, "method", None)
    arg = positional[0] if positional else None
    if method in ("getMatchesForSample", "updateMinHashesForSample", "modifySample", "deleteSample"):
        return f"sample {arg}" if arg is not None else None
    if method == "getMatchesForSampleVs" and len(positional) >= 2:
        return f"sample {positional[0]} vs. sample {positional[1]}"
    if method == "getMatchesForSampleVsGroup" and len(positional) >= 2 and isinstance(positional[1], list):
        return f"sample {positional[0]} vs. {_sample_word(len(positional[1]))}"
    if method == "combineMatchesToCross" and isinstance(arg, dict):
        return _sample_word(len(arg))
    if method == "getUniqueBlocks" and isinstance(arg, list):
        return "samples " + ", ".join(str(sample_id) for sample_id in arg) if arg else "no samples"
    if method in ("deleteFamily", "modifyFamily"):
        return f"family {arg}" if arg is not None else None
    if method == "getMatchesForMappedBinary":
        parts = []
        sha = _short_sha(job)
        if sha:
            parts.append(sha)
        if len(positional) >= 2 and isinstance(positional[1], int):
            parts.append(f"mapped at 0x{positional[1]:x}")
        return " | ".join(parts) or None
    if method in ("getMatchesForUnmappedBinary", "getMatchesForSmdaReport", "addBinarySample"):
        parts = [part for part in (_short_sha(job), getattr(job, "filename", None)) if part]
        return " | ".join(parts) or None
    return None


def job_task(job):
    """One readable line for what a job does, for the "Task:" row of a job's page.

    Reads `Job.method` and the queue's parameter record. The display name comes from the
    same table the job list uses, then what the job was run on, then any options that
    were set - `Match Binary (mapped) | 53832f5c | mapped at 0x1e60000 | MinHash matching:
    Standard` where the raw call read `getMatchesForMappedBinary(None, 31850496, 2)`.
    A job whose record cannot be read still gets its name.
    """
    positional, named = _split_params(job)
    parts = [job_method_name(getattr(job, "method", None))]
    details = _task_details(job, positional)
    if details:
        parts.append(details)
    for name in sorted(named):
        option = _format_option(name, named[name])
        if option:
            parts.append(option)
    return " | ".join(parts)
