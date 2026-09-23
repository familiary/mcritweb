#!/usr/bin/env python
"""Rebuilds tests/fixtures/ from a live MCRIT backend.

    python tests/fixtures/regenerate.py [http://127.0.0.1:8000]

Talks to the mcrit server directly rather than through MCRITweb, because these
fixtures are the *backend's* wire format - what `McritClient` hands to the views.

The instance it reads needs one finished job of each matching type. Jobs are found
by method name and recency, not by id, so this runs against any instance that has
them. It refuses to write a fixture it could not find rather than leaving a stale
one in place.

Function entries are captured in two pools, because the views need them two ways:

  functions_reference_<id>  the first 100 functions of a report's reference sample,
                            with their `xcfg` intact. MatchingResult.clusterLinkHunt
                            Result() builds an SmdaFunction from every entry it is
                            handed, so a stripped graph here is not a smaller fixture
                            but a KeyError.
  functions_matched         every function id the 1-vs-1 result page looks up by id,
                            with `xcfg` dropped - that path reads offsets and never
                            reconstructs a graph. It has to be complete, because
                            data.py indexes the lookup directly rather than
                            tolerating a miss.

Only the filtered result views (?funid=, ?samid=, ?famid=) reach for matched entries
beyond that set. A test that exercises them needs this script to widen the pool
first.

Unique blocks are trimmed too: 6124 blocks -> the first 250, enough for the block
table to paginate several pages. `statistics` is left untouched and so describes the
full run rather than the subset.

Everything else is stored exactly as received.
"""

import json
import pathlib
import sys

import requests

HERE = pathlib.Path(__file__).parent

# fixture name -> the job method that produces it
REPORTS = {
    "matches_for_sample": "getMatchesForSample",
    "matches_for_sample_vs": "getMatchesForSampleVs",
    "matches_for_query": "getMatchesForMappedBinary",
    "cross_compare": "combineMatchesToCross",
    "unique_blocks": "getUniqueBlocks",
    # maintenance jobs: their results are small and shaped per method (a dict, a dict, an int)
    "maintenance_rebuild_index": "rebuildIndex",
    "maintenance_recalculate_pichashes": "recalculatePicHashes",
    "maintenance_recalculate_minhashes": "recalculateMinHashes",
}

FUNCTIONS_PER_REFERENCE_SAMPLE = 100
UNIQUE_BLOCKS_KEPT = 250


def fetch(server, path):
    response = requests.get(f"{server}{path}", timeout=60)
    response.raise_for_status()
    return response.json()["data"]


def write(name, data):
    path = HERE / f"{name}.json"
    path.write_text(json.dumps(data))
    print(f"  {name:32} {path.stat().st_size:10,}B")


def newest_finished_job(jobs, method):
    """The most recent finished job for a method, or None."""
    candidates = [
        job for job in jobs
        if job.get("payload", {}).get("method") == method and job.get("finished_at") and job.get("result")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda job: job["number"])


def reference_functions(server, sample_id):
    """The head of a sample's functions, graphs included."""
    functions = fetch(server, f"/samples/{sample_id}/functions")
    return dict(sorted(functions.items(), key=lambda kv: int(kv[0]))[:FUNCTIONS_PER_REFERENCE_SAMPLE])


def matched_functions(server, function_ids):
    """Entries looked up by id, without their graphs."""
    if not function_ids:
        return {}
    body = ",".join(str(function_id) for function_id in sorted(function_ids))
    response = requests.post(f"{server}/functions", data=body, timeout=120)
    response.raise_for_status()
    entries = response.json()["data"]
    for entry in entries.values():
        entry["xcfg"] = {}
    return entries


def vs_lookup_ids(result):
    """The ids the 1-vs-1 page resolves, computed the same way data.py does."""
    from mcrit.storage.MatchingResult import MatchingResult
    matching_result = MatchingResult.fromDict(result)
    return {match.matched_function_id for match in matching_result.filtered_function_matches}


def trim_unique_blocks(result):
    blocks = result["unique_blocks"]
    result["unique_blocks"] = dict(list(blocks.items())[:UNIQUE_BLOCKS_KEPT])
    return result


def main():
    server = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    print(f"reading {server}")

    print("corpus:")
    write("status", fetch(server, "/status"))
    write("version", fetch(server, "/version"))
    write("families", fetch(server, "/families"))
    write("samples", fetch(server, "/samples"))
    write("queue", fetch(server, "/jobs?limit=25"))
    write("queue_statistics", fetch(server, "/jobs/stats/"))

    print("reports:")
    jobs = fetch(server, "/jobs?limit=200")
    missing = []
    reference_sample_ids = set()
    lookup_function_ids = set()
    for name, method in REPORTS.items():
        job = newest_finished_job(jobs, method)
        if job is None:
            missing.append(f"{name} (no finished {method} job)")
            continue
        job_id = job["_id"]["$oid"]
        result = fetch(server, f"/jobs/{job_id}/result")
        if name == "unique_blocks":
            result = trim_unique_blocks(result)
        write(f"{name}.job", job)
        write(f"{name}.result", result)

        info = result.get("info", {}) if isinstance(result, dict) else {}
        sample_id = info.get("sample", {}).get("sample_id")
        # a query report has no sample of its own (its id is negative)
        if isinstance(sample_id, int) and sample_id >= 0:
            reference_sample_ids.add(sample_id)
        if method == "getMatchesForSampleVs":
            lookup_function_ids |= vs_lookup_ids(result)

    print("functions:")
    for sample_id in sorted(reference_sample_ids):
        write(f"functions_reference_{sample_id}", reference_functions(server, sample_id))
    write("functions_matched", matched_functions(server, lookup_function_ids))

    if missing:
        print("\nNOT WRITTEN - run these on the instance first:")
        for entry in missing:
            print(f"  {entry}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
