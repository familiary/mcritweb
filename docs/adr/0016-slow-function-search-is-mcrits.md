# The slow function search: what is left of #76 is mcrit's

---
status: accepted — the MCRITweb side is complete; the remainder belongs upstream in
[fkie-cad/mcrit](https://github.com/fkie-cad/mcrit)
---

Issue #76 is "searching for functions is painfully slow (~30s) on larger database if no
results are found", raised with "not sure how to mitigate, though". This records where
the cost actually is, what this repository did about it, what it declined to do and why,
and what mcrit would have to change for the issue to be closed rather than narrowed.

Line references are to mcrit 1.8.0 (`9cce1bb`).

## Where the time goes

A search term with no field prefix parses to a `SearchTermNode`
(`SearchQueryParser._build_tree_one_filter`, `mcrit/index/SearchQueryParser.py:143-149`).
`SearchFieldResolver.visitSearchTermNode` (`mcrit/index/SearchQueryTree.py:138-145`) then
expands it into one `SearchConditionNode` per searchable field, always with the operator
`?`, and `MongoSearchTranspiler.visitSearchConditionNode`
(`mcrit/storage/MongoDbStorage.py:96-97`) turns `?` into

```python
value = re.compile(re.escape(node.value), re.IGNORECASE)
```

— an **unanchored, case-insensitive** regex. For functions the field list is a single
entry, `function_name` (`MongoDbStorage.findFunctionByString:1822-1825`), and the query is
issued as

```python
self._getDb().functions.find(query, {"_id": 0}, sort=sort_list, limit=max_num_results)
```

(`MongoDbStorage.py:1828`), where `max_num_results` is `limit + 1`
(`MinHashIndex._getSearchResultTemplate:568`) and `sort_list` is the caller's sort, which
for every MCRITweb function search defaults to `function_id`.

Two properties of that query together are the whole issue:

1. **The predicate cannot be served from an index.** `_ensureIndexAndUnknownFamily`
   (`MongoDbStorage.py:194`) *does* create an index on `function_name`
   (`MongoDbStorage.py:205`). This is where the function search differs from the sample
   search, where the earlier reading in this campaign holds exactly — `filename`,
   `family`, `component` and `version` have no index at all. For functions the index is
   there and does not help: MongoDB can derive index bounds from a `$regex` only when the
   pattern is a case-sensitive prefix expression, and this one is neither anchored nor
   case-sensitive, so the index can at best be walked end to end.
2. **`limit` is the only thing that ever stops the walk.** When matches are dense the
   query stops as soon as `limit + 1` documents pass the filter. When nothing matches
   there is nothing to stop at and the whole collection is read. That is exactly the
   asymmetry the issue reports: slow **when no results are found**.

The second point is also why a **minimum term length is the wrong instrument**, and it is
worth stating plainly because it is the obvious first idea. The cost does not grow with
the term; it grows with how *rare* the term is. A one-character term matches almost
everything and returns from the first few documents. A twelve-character term that matches
nothing pays the full scan. A length floor would reject the cheap queries and admit the
expensive ones.

mcrit has no such floor anywhere. The rule that looks like one —
`("sha256", lambda search_term: len(search_term) >= 3)` at
`MongoDbStorage.findSampleByString:1814` — is the opposite kind of rule: a
`conditional_search_fields` entry *adds* the sha256 field to a **sample** search once the
term is long enough to be worth matching against a hash. It widens a search, it does not
gate one, and it does not apply to functions.

## What MCRITweb already did

PR #146 (issue #77) removed `function` from `DEFAULT_SEARCH_TYPES`
(`mcritweb/views/explore.py:32`). Before that, `/explore/search` ran all three collection
searches sequentially whenever the caller named no type, so the function scan was charged
to every navbar search and to every pagination click that landed back on the search page
with only a family or sample table on it. The omission is not silent: `search.html`
renders a "Functions — Not searched. **Search functions too**" block where the results
would have been, carrying the collections already searched, and says the wait is longest
when there is nothing to find. `tests/testExplorePageCalls.py` pins all three halves —
that an unqualified search calls only `search_families` and `search_samples`, that the
page says so, and that ticking the box still searches functions.

This ADR adds the one remaining thing MCRITweb can honestly offer: the manual
(`docs/manual/README.md`, the `#search` section every search bar links to) now says that a
plain term is a case-insensitive substring match while a prefixed term such as
`function_name:memcpy` is an equality match — which mcrit *can* answer from the
`function_name` index. The fast query already exists and users had no way to know it was
there; the manual's operand list did not even list `=`, and described `?` as "interpret as
regular expression" when the value is `re.escape`d and so is a literal substring.

Neither change makes the scan faster. Together they mean the scan is paid only by someone
who asked for it, having been told what they are asking for and how to ask a cheaper
question instead.

## What was considered and rejected

- **A term-length floor in MCRITweb** — rejected above: it is anti-correlated with the
  cost, and it would refuse queries mcrit answers quickly.
- **Warning on the page that runs the search.** A banner above the function results
  renders *after* the thirty seconds. The only warning that can help is the pre-emptive
  one, which is the link in `search.html`.
- **Suggesting `function_name:<term>` after a fruitless search.** Also too late, and
  actively misleading: the exact form is *narrower*, so it cannot find what the substring
  search just failed to find.
- **Sending `sort_by=function_name` instead of `function_id`.** Tempting, because sorting
  by the field being filtered could in principle let the planner scan the `function_name`
  index without fetching every document. It does not survive contact:
  `MinHashIndex._get_sort_data:610-620` appends `function_id` as the tiebreaker, so the
  requested sort is `[function_name, function_id]`, and there is no compound index for it.
  It would also change the visible order of function results for everyone to buy an
  unmeasured constant factor. No MongoDB instance is available in this environment to
  measure it, and an unmeasured performance change is not one to ship.
- **Issuing the three collection searches concurrently.** Total wall time would go from
  `30s + ε` to `30s`.
- **A page-level loading indicator.** Real, but a different concern (issue #60) on a
  different branch, and it addresses "appears hung", not "is slow". This branch has no
  such indicator and deliberately does not grow one here.

## What mcrit would have to change

In rough order of cost to implement against value delivered:

1. **Give the query a time budget.** `findFunctionByString` could pass `max_time_ms` to
   `find`, and `StatusResource._respond_search` (`mcrit/server/StatusResource.py:125`)
   already knows how to turn a failed search into a 4xx with a message rather than a 500.
   The pattern is established in the same class as `findFunctionByString`:
   `hasInlineXcfgRemaining`
   (`MongoDbStorage.py:189`) bounds its own probe at `max_time_ms=5000` precisely because
   it is a scan. This changes no semantics and converts the worst case from a thirty-second
   hang into a prompt "that search was too broad to finish — narrow it with
   `function_name:`". It does not make any search succeed faster.
2. **Make prefix search indexable.** A case-insensitive collation
   (`strength=2`) on the `functions.function_name` index, with the query issued under the
   same collation and the regex anchored (`^` + `re.escape(term)`), gives real index
   bounds. The equivalent without collation is a stored lower-cased shadow field, indexed,
   queried with an anchored case-sensitive regex. Either turns *substring* search into
   *prefix* search for functions, which is a product decision, not a refactor: matching
   `memcpy` inside `__imp_memcpy` is a large part of why the current behaviour is
   substring.
3. **Accept that unanchored substring search cannot be indexed** and, if it must stay,
   scope it — for instance requiring function searches to be qualified by `sample_id` or
   `family_id` (both indexed, `MongoDbStorage.py:203-204`) unless the caller opts in to a
   full scan. MCRITweb already issues the qualified form on the single-sample page
   (`explore.py:318`, `sample_id:<id> <query>`), which is why that page is fast.

A full-text or n-gram index is the textbook answer to unanchored substring search, but
mangled and decorated symbol names tokenise badly, so `$text` is unlikely to be a drop-in.

## Consequences

- Issue #76 cannot be closed by this repository. It should be reassigned upstream with
  the three options above, or split: the MCRITweb half is done and is what PR #146 and
  this ADR describe.
- A user who explicitly ticks *Functions* still waits. That is intentional — the
  alternative is refusing a query mcrit is willing to answer.
- If mcrit adopts option 1, `search.html`'s "Not searched" block and the manual paragraph
  stay correct as written; if it adopts option 2, the manual's description of `?` and of
  plain terms has to be revisited, because the semantics would have changed.
