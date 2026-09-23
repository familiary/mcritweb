# Export -> import is a faithful copier, and is not what breaks the CFG view

---
status: accepted — measurement for #67; the remaining work is upstream in mcrit
---

Issue #67 asked four questions about export -> import and answered none of them. The
change that carries it ([#150](https://github.com/danielplohmann/mcritweb/pull/150))
guarded `explore.fetchDotGraph` and `functiondiff.get_matches_node_colors` against a
`FunctionEntry` with no `xcfg`, and said in its own commit message that the cause was
"the export path drops the xcfg in the first place".

**That is wrong.** It was written from the code, not from a run. This ADR records what
running it actually showed, so the next person does not go looking in `getExportData`.

Everything below was measured against **mcrit 1.8.1** and **smda 4.5.0** on Python
3.11.9, with `MINHASH_POOL_INDEXING` off, `calculate_matches=False`, and
`tests/library_report.smda` from the mcrit checkout (1 sample, 3 functions, 73 basic
blocks, 40 picblockhashes) — the smallest report that still carries both an `xcfg` and
picblockhashes.

## 1. What changes besides remapped ids

**Nothing.** Four round trips, each `getExportData` on one instance ->
`addImportData` on a *fresh* one, comparing every `FamilyEntry`, `SampleEntry` and
`FunctionEntry` field, matching functions by `sha256@offset` because the ids are the
thing allowed to move:

| storage | how the export travelled | differences beyond `function_id`/`sample_id`/`family_id` |
| --- | --- | --- |
| `MemoryStorage` | the dict, in process | none |
| `MemoryStorage` | `json.dumps` -> `json.loads` | `xcfg["blocks"]` keys `int` -> `str`, ×3 functions |
| `MongoDbStorage` (real mongod) | the dict, in process | none |
| `MongoDbStorage` (real mongod) | `compress_data=True`, then JSON | none |
| live mcrit server over HTTP | `GET /export?compress=True`, `json.dumps` to a file, `POST /import` | none |

The one difference is JSON's, not mcrit's: JSON object keys are strings, so a
`SmdaFunction.toDict()` block map keyed by integer offsets comes back keyed by decimal
strings. It is invisible under MongoDB, which serialises the `xcfg` to a JSON blob on
the way in anyway (`MongoDbStorage._encodeXcfg`), so its block keys are already strings
before any export. `MemoryStorage.getUniqueBlocks` already accepts either spelling and
says so in a comment; `SmdaFunction.fromDict` accepts either.

The field-by-field diff cannot see the *band* index, which is where the only import bug
this project ever really had lived, so that was checked separately: every imported
function's own minhash was looked up through `getCandidatesForMinHash`, and all 3 found
themselves, before and after.

That check is not paranoia. Until **mcrit b1a240c ("bugfix for import", 2023-08-11)**,
`addImportData` built each `MinHash` from `function_entry.function_id` *before*
`importFunctionEntries` reassigned it, so every band entry on the importing instance
pointed at the exporting instance's ids. An export made in 2022 — when #67 was filed —
did introduce an unexpected change beyond remapped ids, and it was this one. It is
fixed; the current code builds the minhashes from the list `importFunctionEntries`
returns.

## 2. Are picblockhashes gone after export -> import

**No.** 40 before, 40 after, in every configuration in the table above, including the
live-server HTTP round trip. They survive because `FunctionEntry.toDict` carries
`picblockhashes` and `importFunctionEntries` writes them back through the same
`_encodePichash` the add-sample path uses.

The derived lookups work on the importing instance too, which is the part that would
have failed if the hashes had been carried but not indexed:

```
getMatchesForPicBlockHash(0x816da97373fe8cb8) -> 1 hit
getMatchesForPicHash(0xf9ce9cce9d13662b)      -> 1 hit
getUniqueBlocks -> 40 total, 40 characteristic, 40 unique
```

## 3. Why the CFG view breaks

Not because of the import. **`Worker.updateMinHashesForSample` calls
`storage.deleteXcfgForSampleId(sample_id)` whenever `STORAGE_DROP_DISASSEMBLY` is set**
(`mcrit/Worker.py:321`) — a supported deployment setting for instances that only need
to match, not to display. The entry then holds `xcfg == {}` permanently, and
`FunctionEntry.toSmdaFunction()` raises `ValueError: serialized function is incomplete`
on it. mcrit knows: `MinHashIndex.getFunctionGraph` is a `NotImplementedError` whose
note reads "xcfg might be deleted".

Export *propagates* that. Re-running the MongoDB round trip with
`STORAGE_DROP_DISASSEMBLY=1` on the exporting side: source holds 0 functions with an
`xcfg`, the export carries `"xcfg": {}`, the importing instance faithfully stores `{}`,
and `toSmdaFunction()` raises on all 3 functions on both sides. Still no difference
between the two instances — the graph was already gone before anything was exported.

So the observation in #67 is real and the diagnosis in it was not: whoever imports the
data is simply the first person to open a CFG view on functions whose disassembly the
*exporting* instance had already discarded, and the import gets the blame.

The second, unrelated way to hold such an entry is asking for one:
`getFunctionById(function_id, with_xcfg=False)` returns `xcfg = None`, and
`mcritweb/views/api.py` forwards a caller-supplied `with_xcfg` query parameter. mcrit
keeps `None` ("not requested") and `{}` ("disassembly dropped") deliberately distinct;
the guard added by #150 treats both the same, which is right for a view.

### A second bug found while measuring this

With the disassembly dropped, `getUniqueBlocks` does not degrade — it dies:

```
File "mcrit/storage/MongoDbStorage.py", line 1750, in getUniqueBlocks
    candidate_picblockhashes[picblockhash]["instructions"] = entry["xcfg"]["blocks"][str(block_offset)]
KeyError: 'blocks'
```

`_attachXcfgBlobs` and its inline sibling at line 1747 normalise a missing blob to
`"{}"`, and the next line indexes `["blocks"]` into it unconditionally. The block hashes
themselves are still there and still correct, so the whole result is lost for the sake
of the instruction text attached to each block. `MemoryStorage.getUniqueBlocks`
(`mcrit/storage/MemoryStorage.py:886-888`) has the same shape: it skips `xcfg is None`
but not `xcfg == {}`, then reads `entry.xcfg["blocks"]`.

This is not reachable from mcritweb's side — the job fails in the backend and mcritweb
only ever sees a failed job — so it is recorded here rather than worked around.

## 4. Can import and addSample be unified

Partly, and the useful part is small. What the two paths share is the tail: assign ids
from the counter, build the document, insert it, keep the family statistics. What they
cannot share is the head, and should not: the add-sample path *computes* `pichash` and
`picblockhashes` from an `SmdaFunction` (`MongoDbStorage._getFunctionDocument`,
`MemoryStorage._addFunction`), while the import path deliberately *trusts* the values in
the export, because recomputing them is the expensive thing an import exists to avoid.
Collapsing those two into one function would mean a flag that switches the body, which
is not an improvement.

The duplication that is worth removing is narrower and entirely mechanical:

- **`importFunctionEntry` (singular) has no callers.** Not in mcrit, not in mcritweb,
  not in either test suite — `MinHashIndex.addImportData` only ever calls the bulk
  `importFunctionEntries`. Both copies (`MongoDbStorage.py:855`,
  `MemoryStorage.py:407`) are near-identical to the bulk version's loop body and are
  dead, as is the `StorageInterface.py:179` declaration that keeps them in step.
  Deleting them removes two of the four places the next change has to land.
- **The `"mcrit-import"` label synthesis is written out four times** — in each of those
  two dead singular methods and again in each bulk one. It is also the one behavioural
  difference between the paths: the add-sample path attaches labels through
  `storage.updateFunctionLabels(smda_report, username)` from `MinHashIndex.addReport`,
  and the import path synthesises a `FunctionLabelEntry(function_name, "mcrit-import")`
  only when the imported entry arrives with none. On a round trip of current data that
  branch never fires (measured: 3 labels before, 3 after, identical); it exists for
  exports old enough to predate labels.
- **`importSampleEntry` repeats the `_updateFamilyStats` + `_updateDbState` tail of
  `addSmdaReport`** verbatim. Both are correct today, which is exactly the kind of thing
  that stops being true silently.

There is one asymmetry worth naming rather than merging: `addReport` reaches the band
index through the `updateMinHashesForSample` *job*, `addImportData` writes it inline
with `storage.addMinHashes`. That is why `STORAGE_DROP_DISASSEMBLY` never fires on an
import — an imported sample arrives already hashed, `getUnhashedFunctions` skips it, and
the job that would drop the disassembly is never queued. An instance configured to drop
disassembly therefore keeps the `xcfg` of everything it imports, which is the opposite
of what the operator asked for. Nobody has complained; it is written down here because
it is the kind of divergence sub-item (4) was worried about.

## Where a fix belongs

All of it is in mcrit; nothing above is fixable in this repository. Named precisely so
the upstream issue can carry it:

| what | where | change |
| --- | --- | --- |
| `getUniqueBlocks` dies on a dropped `xcfg` | `mcrit/storage/MongoDbStorage.py:1750` | `blocks = entry["xcfg"].get("blocks", {})`, then skip offsets it does not hold, as `MemoryStorage` already does for the int/str key mismatch |
| same, in the other backend | `mcrit/storage/MemoryStorage.py:886-888` | widen `if entry.xcfg is None` to `if not entry.xcfg`, which covers the `{}` that `deleteXcfgForSampleId` writes |
| dead duplicate | `mcrit/storage/MongoDbStorage.py:855`, `mcrit/storage/MemoryStorage.py:407`, `mcrit/storage/StorageInterface.py:179` | delete `importFunctionEntry`; it has no callers |
| the round-trip test sub-item (1) asked for | mcrit's `tests/` | `MemoryStorage` needs no Mongo, so `getExportData -> addImportData -> compare` runs offline in that suite; the comparison must key functions by `sha256@offset` and must check the band index separately, since a field diff cannot see it |

The scripts these numbers came from are throwaway, but the shape is worth keeping:
index one report, snapshot every entry, export, import into a fresh instance, snapshot
again, diff every field with `function_id`/`sample_id`/`family_id` excluded, then ask
every function to find itself in the band index.

## What this changes here

Only what this repository was saying out loud. The stand-in dot graph #150 serves for an
entry with no `xcfg` told the user the graph "is not part of exported data, so functions
that were added through an import cannot be displayed here" — a wrong cause, shown in
the UI, pointing at the wrong place to look. It now says the backend discards the graph
when it is configured not to keep disassembly. The guards themselves were already right
and are unchanged: an entry with no `xcfg` still cannot be drawn, whatever emptied it.
