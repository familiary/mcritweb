"""Loads tests/fixtures/ and serves it as a backend.

This module is data plumbing, not tests. It is deliberately not named `test*.py` so
pytest does not collect it.

The fixtures are real reports from a live MCRIT instance - three malware families
across seven samples, plus six MSVC library samples, all dated pre-2015 - captured
by `tests/fixtures/regenerate.py`. They are the backend's wire format, so this
module deserializes them exactly the way `McritClient` does. If a shape here drifts
from the real client, the fixtures are still right and this file is wrong.

`CorpusMcritClient` inherits the strict fake's failure mode on purpose: a method
nobody has taught it still raises NotImplementedError naming itself, so the next gap
is a message rather than a silently empty page.
"""

import json
import pathlib
import re

import mcrit.matchers.MatcherFlags as MatcherFlags
from mcrit.config.MinHashConfig import MinHashConfig
from mcrit.minhash.MinHash import MinHash
from mcrit.queue.LocalQueue import Job
from mcrit.storage.FamilyEntry import FamilyEntry
from mcrit.storage.FunctionEntry import FunctionEntry
from mcrit.storage.SampleEntry import SampleEntry

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# fixture name -> the job method it was produced by, for tests that want to say
# "the cross compare report" instead of carrying an instance-specific job id
REPORTS = (
    "matches_for_sample",
    "matches_for_sample_vs",
    "matches_for_query",
    "cross_compare",
    "unique_blocks",
    # the three maintenance jobs result_maintenance.html knows, one per branch of the template
    "maintenance_rebuild_index",
    "maintenance_recalculate_pichashes",
    "maintenance_recalculate_minhashes",
)


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def job_id_of(report):
    """The job id a report fixture was captured under."""
    return load(f"{report}.job")["_id"]["$oid"]


#: The sample the captured 1-vs-N report (`matches_for_sample`) was produced for, and
#: the sample the reference functions belong to.
MATCHED_SAMPLE_ID = 0


# --- the search/cursor protocol ------------------------------------------------
#
# Modelled on its observable contract, not its encoding. mcrit's cursor is a
# serialised sort key (`MinimalSearchCursor`), and reproducing that here would copy
# an implementation no test cares about. What the views do depend on is the shape
# around it, and that is what these reproduce:
#
#   {"search_results": {id: entry_dict}, "cursor": {"forward": str|None,
#    "backward": str|None}, "id_match": dict|None[, "sha_match": dict|None]}
#
#   * `forward` is set only while results remain after this page
#   * `backward` is set only once the caller has left the first page
#   * handing a token back yields the adjacent page
#   * `search_results` values are **dicts**, as they arrive off the wire - the
#     views call `SampleEntry.fromDict` on them, and a fake handing back entry
#     objects would let code that forgot to pass here
#
# Matching is a case-insensitive substring test over the fields a person would
# search by. mcrit's own parser handles `field:value` expressions and ranges; a
# test that needs those needs the real backend, not this.

#: Opaque to the caller, which is the whole point - the views must not read it.
CURSOR_PREFIX = "fixture-cursor:"

FAMILY_FIELDS = ("family_name",)
SAMPLE_FIELDS = ("filename", "family", "sha256", "version", "component")
FUNCTION_FIELDS = ("function_name",)


def _encode_cursor(index, is_forward):
    return f"{CURSOR_PREFIX}{'f' if is_forward else 'b'}:{index}"


def _decode_cursor(cursor):
    if not isinstance(cursor, str) or not cursor.startswith(CURSOR_PREFIX):
        return None
    direction, _, index = cursor[len(CURSOR_PREFIX):].partition(":")
    return direction, int(index)


def _text_of(entry, fields):
    return " ".join(str(getattr(entry, field, "") or "") for field in fields)


def _sort_key(entry, sort_by, default_sort):
    """Fall back to the default field, as mcrit does for an unknown sort_by."""
    value = getattr(entry, sort_by, None) if sort_by else None
    if value is None:
        value = getattr(entry, default_sort)
    # ids and names both occur, and mixing them in one comparison is a TypeError
    return (isinstance(value, str), str(value) if isinstance(value, str) else value)


def _id_match(entries, search_term):
    """mcrit answers the entry directly when the term is one of its ids."""
    try:
        term = int(search_term, 16) if search_term.startswith("0x") else int(search_term)
    except (AttributeError, ValueError):
        return None
    if term > 0xFFFFFFFF:
        return None
    entry = entries.get(term)
    return entry.toDict() if entry else None


def _page(entries, search_term, fields, default_sort, sort_by, is_ascending, cursor, limit):
    """One page of a search, plus the cursors either side of it."""
    needle = (search_term or "").lower()
    matched = [entry for entry in entries.values() if needle in _text_of(entry, fields).lower()]
    matched.sort(key=lambda entry: _sort_key(entry, sort_by, default_sort), reverse=not is_ascending)

    start = 0
    decoded = _decode_cursor(cursor)
    if decoded is not None:
        direction, index = decoded
        start = index if direction == "f" else max(0, index - limit)
    page = matched[start:start + limit]

    return {
        "search_results": {getattr(entry, default_sort): entry.toDict() for entry in page},
        "cursor": {
            "forward": _encode_cursor(start + limit, True) if start + limit < len(matched) else None,
            "backward": _encode_cursor(start, False) if start > 0 else None,
        },
    }


class RawResponse:
    """Enough of a requests.Response for a caller reading a raw-mode answer.

    `McritClient(raw_responses=True)` returns the response untouched instead of running
    it through `handle_response`, which is the only way a caller can tell "404, not in
    the collection" from "the call failed" - handle_response maps both to None. Only the
    methods that a view actually asks for in raw mode model this; see the note on
    CorpusMcritClient.raw.
    """

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return {"status": "successful", "data": self._payload}

def _job_state(document):
    """mongoqueue._identifyJobState, transcribed - `state=` is filtered on it."""
    if document["started_at"] and document["locked_by"] and not (document["finished_at"] or document["terminated"]):
        return "in_progress"
    if document["attempts_left"] == 0 and not document["finished_at"] and not document["terminated"]:
        return "failed"
    if not document["finished_at"] and not document["locked_by"] and not document["terminated"]:
        return "queued"
    if document["finished_at"] and not document["terminated"]:
        return "finished"
    if document["terminated"]:
        return "terminated"
    return "unknown"


class CorpusMcritClient:
    """Serves the captured corpus in the types the real client returns."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        #: raw_responses is modelled for getSampleBySha256 only, because that is the one
        #: place a view needs the status code rather than the parsed value. Every other
        #: method ignores it and answers parsed, so a new raw-mode caller has to teach
        #: this fake about its method rather than getting a wrong shape quietly.
        self.raw = bool(kwargs.get("raw_responses"))
        self.calls = []
        self._samples = {int(k): SampleEntry.fromDict(v) for k, v in load("samples").items()}
        self._families = {int(k): FamilyEntry.fromDict(v) for k, v in load("families").items()}
        # two pools: reference-sample functions keep their control flow graph, the
        # by-id lookup pool does not. See tests/fixtures/regenerate.py.
        self._functions_by_sample = {}
        for path in sorted(FIXTURES.glob("functions_reference_*.json")):
            sample_id = int(path.stem.rsplit("_", 1)[1])
            entries = {int(k): FunctionEntry.fromDict(v) for k, v in json.loads(path.read_text()).items()}
            self._functions_by_sample[sample_id] = entries
        self._functions = {fid: entry for pool in self._functions_by_sample.values() for fid, entry in pool.items()}
        self._functions.update({int(k): FunctionEntry.fromDict(v) for k, v in load("functions_matched").items()})
        self._jobs = {job_id_of(report): (load(f"{report}.job"), load(f"{report}.result")) for report in REPORTS}
        self._queue = load("queue")
        # the queue holds every job, the report pool only the five with a captured
        # result - a real backend answers getJobData for both, and the cross compare
        # page asks it for each of its five children
        self._queue_by_id = {job["_id"]["$oid"]: job for job in self._queue}

    def raw_variant(self):
        """The same backend answering in raw mode - see FakeMcritClient.raw_variant."""
        clone = object.__new__(type(self))
        clone.__dict__.update(self.__dict__)
        clone.raw = True
        return clone

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    # --- server ------------------------------------------------------------------

    # Both of these answer with the wrapped dict, because that is what the real client
    # answers with: MinHashIndex.getStatus returns {"status": {...}} and getVersion
    # returns {"version": "..."}, StatusResource puts each under "data", and
    # handle_response hands "data" back untouched. Unwrapping here once more used to
    # make /explore/statistics render an empty table under test while working in
    # production - and hid that the admin page renders getVersion()'s dict verbatim.
    def getStatus(self, *args, **kwargs):
        self._record("getStatus", *args, **kwargs)
        return load("status")

    def getVersion(self, *args, **kwargs):
        self._record("getVersion", *args, **kwargs)
        return load("version")

    # --- families ----------------------------------------------------------------

    def getFamilies(self, *args, **kwargs):
        self._record("getFamilies", *args, **kwargs)
        return self._families

    def getFamily(self, family_id, *args, **kwargs):
        self._record("getFamily", family_id, *args, **kwargs)
        return self._families.get(int(family_id))

    def isFamilyId(self, family_id, *args, **kwargs):
        self._record("isFamilyId", family_id, *args, **kwargs)
        return int(family_id) in self._families

    # --- samples -----------------------------------------------------------------

    def getSamples(self, *args, **kwargs):
        self._record("getSamples", *args, **kwargs)
        return self._samples

    def getSampleById(self, sample_id, *args, **kwargs):
        self._record("getSampleById", sample_id, *args, **kwargs)
        return self._samples.get(int(sample_id))

    def isSampleId(self, sample_id, *args, **kwargs):
        self._record("isSampleId", sample_id, *args, **kwargs)
        return int(sample_id) in self._samples

    def getSampleBySha256(self, sha256, *args, **kwargs):
        self._record("getSampleBySha256", sha256, *args, **kwargs)
        for sample in self._samples.values():
            if sample.sha256 == sha256:
                return RawResponse(200, sample.toDict()) if self.raw else sample
        return RawResponse(404) if self.raw else None

    # --- functions ---------------------------------------------------------------

    def getFunctionsBySampleId(self, sample_id, *args, **kwargs):
        self._record("getFunctionsBySampleId", sample_id, *args, **kwargs)
        # only the reference pool, so callers that rebuild a graph get entries that
        # still have one
        return list(self._functions_by_sample.get(int(sample_id), {}).values())

    def getFunctionsByIds(self, function_ids, *args, **kwargs):
        self._record("getFunctionsByIds", function_ids, *args, **kwargs)
        return {int(fid): self._functions[int(fid)] for fid in function_ids if int(fid) in self._functions}

    def getFunctionById(self, function_id, *args, **kwargs):
        self._record("getFunctionById", function_id, *args, **kwargs)
        return self._functions.get(int(function_id))

    def isFunctionId(self, function_id, *args, **kwargs):
        self._record("isFunctionId", function_id, *args, **kwargs)
        return int(function_id) in self._functions

    # --- direct matching ---------------------------------------------------------

    def getMatchesForPicHash(self, pichash, summary=False, *args, **kwargs):
        """(family, sample, function) for every function sharing this pichash.

        Mirrors QueryResource.on_get_query_pichash[_summary]: the summary is three
        cardinalities, not the tuples, and it is what the function tables render.
        """
        self._record("getMatchesForPicHash", pichash, summary=summary)
        matches = {
            (entry.family_id, entry.sample_id, entry.function_id)
            for entry in self._functions.values()
            if entry.pichash == pichash
        }
        if summary:
            return {
                "families": len({match[0] for match in matches}),
                "samples": len({match[1] for match in matches}),
                "functions": len({match[2] for match in matches}),
            }
        return [list(match) for match in sorted(matches)]

    def getMatchFunctionVs(self, function_id_a, function_id_b, *args, **kwargs):
        """The four entries and the match tuple the function-vs page is built from.

        Scored the way MinHashIndex.getMatchesFunctionVs does it - the minhashes are
        in the captured entries, so the score is computed here rather than invented,
        and the flags follow the same three rules.
        """
        self._record("getMatchFunctionVs", function_id_a, function_id_b)
        entry_a = self._functions.get(int(function_id_a))
        entry_b = self._functions.get(int(function_id_b))
        if entry_a is None or entry_b is None:
            return None
        sample_a = self._samples[entry_a.sample_id]
        sample_b = self._samples[entry_b.sample_id]
        bits = MinHashConfig().MINHASH_SIGNATURE_BITS
        minhash_a = entry_a.getMinHash(minhash_bits=bits).minhash
        minhash_b = entry_b.getMinHash(minhash_bits=bits).minhash
        score = None
        if minhash_a and minhash_b:
            score = MinHash.calculateMinHashScore(minhash_a, minhash_b, minhash_bits=bits)
        flags = 0
        flags += MatcherFlags.IS_MINHASH_FLAG if score is not None and score >= MinHashConfig().MINHASH_MATCHING_THRESHOLD else 0
        flags += MatcherFlags.IS_PICHASH_FLAG if entry_a.pichash == entry_b.pichash else 0
        flags += MatcherFlags.IS_LIBRARY_FLAG if sample_b.is_library else 0
        match_tuple = [entry_b.family_id, entry_b.sample_id, entry_b.function_id, score, flags]
        return {
            "function_entry_a": entry_a.toDict(),
            "function_entry_b": entry_b.toDict(),
            "sample_entry_a": sample_a.toDict(),
            "sample_entry_b": sample_b.toDict(),
            # serialised by hand: MatchedFunctionEntry.toDict() of mcrit <= 1.8.1 re-encodes
            # the flag booleans with their bit values (danielplohmann/mcrit#155), which
            # turns a pichash match into a library one on the way out
            "match_entry": {
                "fid": int(function_id_a),
                "num_bytes": entry_a.binweight,
                "offset": entry_a.offset,
                "matches": match_tuple,
            },
        }

    def getMatchesForPicBlockHash(self, picblockhash, summary=False, *args, **kwargs):
        self._record("getMatchesForPicBlockHash", picblockhash, summary=summary, *args, **kwargs)
        hits = [entry for entry in self._functions.values() if any(block["hash"] == picblockhash for block in entry.picblockhashes)]
        return {
            "families": len({entry.family_id for entry in hits}),
            "samples": len({entry.sample_id for entry in hits}),
            "functions": len(hits),
        }

    # --- job submission ----------------------------------------------------------

    def requestMatchesForSample(self, sample_id, *args, **kwargs):
        """mcrit deduplicates by descriptor and answers the job it already has.

        The corpus holds exactly one captured 1-vs-N job, for the sample its reference
        functions belong to, so that is the only submission this can answer. Anything
        else is a gap in the fixtures rather than a job, and says so.
        """
        self._record("requestMatchesForSample", sample_id, *args, **kwargs)
        if int(sample_id) != MATCHED_SAMPLE_ID:
            raise NotImplementedError(
                f"The corpus has no captured 1-vs-N job for sample {sample_id}, only for "
                f"sample {MATCHED_SAMPLE_ID}. Capture one with tests/fixtures/regenerate.py."
            )
        return job_id_of("matches_for_sample")

    # --- jobs and results --------------------------------------------------------

    def getJobData(self, job_id, *args, **kwargs):
        self._record("getJobData", job_id, *args, **kwargs)
        entry = self._jobs.get(job_id)
        if entry:
            return Job(entry[0], None)
        job = self._queue_by_id.get(job_id)
        return Job(job, None) if job else None

    def getResultForJob(self, job_id, *args, **kwargs):
        self._record("getResultForJob", job_id, *args, **kwargs)
        entry = self._jobs.get(job_id)
        return entry[1] if entry else None

    def getQueueData(self, start=0, limit=0, method=None, filter=None, state=None, ascending=False):
        """The queue, narrowed the way mcrit narrows it - including where it does so
        badly, because callers have to cope with that.

        `queue.json` is captured newest-first, which is what `ascending=False` means.
        `method` is a mongo query on `payload.method` and so applies *before* start and
        limit; `state` is filtered in python over the whole collection and then sliced,
        which is only a performance difference. `filter` is the odd one out: mcrit
        applies it as a substring test over `Job.parameters` *after* start and limit
        (`QueueRemoteCalls.getQueueData`), so it drops non-matches out of an already
        paged slice rather than paging the matches. Reproduced deliberately - a caller
        that combines `filter` with `limit` must not look correct here."""
        # every parameter recorded by name, as data.jobs actually passes them: a call
        # assertion should not depend on which ones this line happened to forward
        # positionally.
        self._record("getQueueData", start=start, limit=limit, method=method, filter=filter, state=state, ascending=ascending)
        documents = self._queue if not ascending else list(reversed(self._queue))
        if method is not None:
            documents = [entry for entry in documents if entry["payload"]["method"] == method]
        if state is not None:
            documents = [entry for entry in documents if _job_state(entry) == state]
        start = start if isinstance(start, int) and start > 0 else 0
        documents = documents[start:start + limit] if isinstance(limit, int) and limit > 0 else documents[start:]
        jobs = [Job(entry, None) for entry in documents]
        if isinstance(filter, str):
            jobs = [job for job in jobs if filter in job.parameters]
        return jobs

    def getQueueStatistics(self, *args, **kwargs):
        self._record("getQueueStatistics", *args, **kwargs)
        return load("queue_statistics")

    # --- search ------------------------------------------------------------------

    def search_families(self, search_term="", cursor=None, is_ascending=True, sort_by=None, limit=100, *args, **kwargs):
        self._record("search_families", search_term, cursor=cursor, is_ascending=is_ascending, sort_by=sort_by, limit=limit)
        result = _page(self._families, search_term, FAMILY_FIELDS, "family_id", sort_by, is_ascending, cursor, limit)
        result["id_match"] = _id_match(self._families, search_term)
        return result

    def search_samples(self, search_term="", cursor=None, is_ascending=True, sort_by=None, limit=100, *args, **kwargs):
        self._record("search_samples", search_term, cursor=cursor, is_ascending=is_ascending, sort_by=sort_by, limit=limit)
        result = _page(self._samples, search_term, SAMPLE_FIELDS, "sample_id", sort_by, is_ascending, cursor, limit)
        result["id_match"] = _id_match(self._samples, search_term)
        result["sha_match"] = None
        if re.match(r"^[a-fA-F0-9]{64}$", search_term or ""):
            match = self.getSampleBySha256(search_term)
            result["sha_match"] = match.toDict() if match else None
        return result

    def search_functions(self, search_term="", cursor=None, is_ascending=True, sort_by=None, limit=100, *args, **kwargs):
        self._record("search_functions", search_term, cursor=cursor, is_ascending=is_ascending, sort_by=sort_by, limit=limit)
        result = _page(self._functions, search_term, FUNCTION_FIELDS, "function_id", sort_by, is_ascending, cursor, limit)
        result["id_match"] = _id_match(self._functions, search_term)
        return result

    def __getattr__(self, name):
        def _unimplemented(*args, **kwargs):
            raise NotImplementedError(
                f"CorpusMcritClient has no '{name}'. Add it to tests/fixtureData.py, "
                f"returning whatever shape the real McritClient returns."
            )
        return _unimplemented
