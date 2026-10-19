# Deserialize search results at every call site, until `McritClient` does it

---
status: accepted — the real fix belongs to `mcrit`, see "What mcrit should change"
---

Issue #64 is titled "McritClient should return objects (search results)" and carries no
description. `McritClient` is `mcrit`'s code, not this repository's, so the issue as
written cannot be closed here. This ADR records what was found, so the part that belongs
upstream can be filed against `mcrit` with a specification rather than a title.

Checked against `mcrit` at 9cce1bb (2026-08-25, version 1.8.0); `requirements.txt` asks
for `mcrit>=1.5.3` and the environment this was run in resolved to 1.8.1.

## What is actually inconsistent

`mcrit/client/McritClient.py` has one accessor that returns raw wire dicts and a dozen
that return entries. The three search methods are the exception:

```python
search_families  = functools.partialmethod(_search_base, "families")
search_samples   = functools.partialmethod(_search_base, "samples")
search_functions = functools.partialmethod(_search_base, "functions")
```

`_search_base` ends `return handle_response(response)` — the decoded JSON, untouched.
Every other method that could return an entry deserializes before handing it back:

| Returns entries | Returns raw dicts |
| --- | --- |
| `getFamily`, `getFamilies` | `search_families` |
| `getSampleById`, `getSampleBySha256`, `getSamples`, `getSamplesByFamilyId`, `addBinarySample` | `search_samples` |
| `getFunctionById`, `getFunctions`, `getFunctionsByIds`, `getFunctionsBySampleId` | `search_functions` |
| `getQueueData`, `getJobData`, `getResultForJob`, `getJobForResult` (`Job`) | — |

The server is not the reason. `MinHashIndex.getFamilySearchResults`,
`getFunctionSearchResults` and `getSampleSearchResults` hold `FamilyEntry` /
`FunctionEntry` / `SampleEntry` objects and call `.toDict()` on them on the way out, the
same as every other endpoint. The types exist on both ends and are dropped in the middle.

The payload is `{"search_results": {id: entry_dict}, "cursor": {...}, "id_match":
dict|None}`, plus `"sha_match": dict|None` for samples. All three of `search_results`,
`id_match` and `sha_match` are entry dicts, and all three need the same treatment.

There is a second, quieter half. Every other accessor opens with `if self.raw: return
response`, honouring the `raw_responses=True` constructor flag; `_search_base` does not
check it at all. This repository's `/api` blueprint is a passthrough built on exactly
that flag (`get_client(username=username, raw_responses=True)` in `views/api.py`), and
AGENTS.md tells the next person to add a regex branch there when the client gains a
method. A search branch added today would be the one branch where the flag is ignored.

## Consequence: every consumer re-implements the same loop

`mcrit`'s own CLI does it — `McritConsole._handle_search` calls `FamilyEntry.fromDict`,
`SampleEntry.fromDict` and `FunctionEntry.fromDict` in three adjacent loops. MCRITweb
does it in ten places: `explore.families`, `explore.samples`, `explore.functions`,
`explore.family_by_id`, `explore.sample_by_id`, the three branches of `explore.search`,
`analyze.get_unique_samples_from_search_result` (shared by `analyze.compare`,
`analyze.cross_compare` and `analyze.compare_versus`), and the landing page in
`mcritweb/__init__.py`.

It is not a cosmetic duplication, because a missed call is silent. Jinja falls back from
attribute lookup to item lookup, and the wire keys equal the entry attribute names, so a
row built from a raw dict renders — until it reaches a field the deserializer transforms
or a property that only the class has:

- `FunctionEntry.offset` and `SampleEntry.base_addr` are two's-complement encoded on the
  wire and run through `decode_two_complement` in `fromDict`. A function mapped at or
  above the sign bit renders `0x-80000000` from the dict and `0xffffffff80000000` from
  the entry. That was the live bug in `explore.functions` this issue's PR fixed.
- `SampleEntry.timestamp` is a string on the wire and a `datetime` on the entry.
- `getShortSha256()`, `getShortFilename()` have no key of that name at all, so a raw
  dict reaching a sample row raises `UndefinedError` and takes the page down.

## What mcrit should change

Make the three search methods return entries, and honour `raw_responses` while doing it.
Concretely, in `mcrit/client/McritClient.py`:

1. Give `_search_base` the entry class for the kind it is searching, and add the
   `if self.raw: return response` guard every other accessor already has.
2. Map `search_results` values, and `id_match` / `sha_match` when not `None`, through
   that class's `fromDict`. Keep the `cursor` sub-dict exactly as it is: it is an opaque
   token and no caller may read inside it.
3. `search_families` → `FamilyEntry`, `search_samples` → `SampleEntry`,
   `search_functions` → `FunctionEntry`.

The `search_results` keys stay as they are; only the values change. This is a breaking
change for callers, which is why it wants a version bump and a note in `mcrit`'s
CHANGELOG rather than a quiet fix. The callers known here are
`McritConsole._handle_search` in the same tree and the ten MCRITweb sites listed above;
`mcrit-plugin` is a sibling client of the same backend and was not checked.

## What MCRITweb does in the meantime

Deserialize at every call site, and pin it. That is worth doing whether or not `mcrit`
ever changes, and costs nothing if it does: `fromDict` on an entry that is already an
entry is the only thing that would then need removing, one line at a time, guarded by
the tests below.

- The rule is stated in AGENTS.md § Testing: `search_results` values are dicts
  as they arrive off the wire, and the views must call `.fromDict` on them.
- `tests/fixtureData.py` enforces it from the fake's side. `_page` serializes the corpus
  entries with `toDict()` on every search, so a view that forgot the call fails here
  rather than passing against a fake that was kinder than the wire.
  `test_search_results_are_dicts_not_entries` pins that property of the fake itself.
- `tests/testFunctionListingEntries.py` pins the function listings, using an offset above
  the sign bit as the field whose rendering differs.
- `test_the_analyze_pages_render_entries_rather_than_wire_dicts` pins the three analyze
  pages through `getShortSha256()`, which a raw dict cannot answer.

When `mcrit` makes the change, this ADR is the checklist of what to unwind, and the
tests above are what says it is safe.
