# Search indexes belong to mcrit — but MCRITweb decides which fields get sorted

---
status: accepted — blocked upstream, with one thing to say on the issue
---

Issue #59 asks for a compound index so that searching while sorted by something other
than the id is not ~10× slower. **The index is mcrit's**, and this repository cannot add
one.

`MongoDbStorage._createIndices` (`storage/MongoDbStorage.py:197-213`) creates only
single-field indexes. On `functions` those are `function_id`, `sample_id`, `family_id`,
`function_name`, `_pichash` and `_picblockhashes.*`. A search is an unanchored
case-insensitive regex (`MongoSearchTranspiler.visitSearchConditionNode`,
`storage/MongoDbStorage.py:97`: `re.compile(re.escape(node.value), re.IGNORECASE)`)
combined with a sort taken from the cursor (`findFunctionByString`, `:1822`). Filtering
on one field while sorting on another, with no compound index, means Mongo cannot serve
both from one index and falls back to a blocking in-memory sort. The reported factor is
entirely plausible; it cannot be confirmed here, because this environment has no MongoDB.

## Consequences

There is one thing MCRITweb owns that belongs on the issue, because it determines *which*
compound indexes are worth creating: **MCRITweb is what exercises the bad path, and it
chooses the fields.** `sortable_header_col` offers four sortable
function columns (`templates/table/function_row.html:38-53`) — `function_name`, `offset`,
`num_instructions` and `num_blocks` — and only the first of them is indexed. The other
three have **no index at all**. So a compound
index should cover the field pairs these headers actually offer, rather than a guess.

MCRITweb already gets the other half of this right: it renders `pic_hash` as a
non-sortable header, matching `_UNSORTABLE_FIELDS = ("pichash",)` at `MongoDbStorage.py:1760`.

## Outcome

Ask `danielplohmann/mcrit` for the compound indexes, naming `_createIndices` and the
three unindexed fields above. If the sortable-header set here ever changes, that list
changes with it — which is the one way this decision could go stale from this side.
