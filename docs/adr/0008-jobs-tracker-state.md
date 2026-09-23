# What is actually blocking the Jobs tracker

---
status: accepted — recorded 2026-08-30
---

Issue #57 is a meta-issue collecting job-related work. Trackers earn their keep when they
say which children are blocked and why, so this records the state of each live bullet as
measured against `df53db9` with **mcrit 1.8.1** installed as the backend, rather than
leaving it to be re-derived.

**#28 (closed) is genuinely done.** The jobs page builds a per-method nested menu from
`getQueueStatistics()` (`data.py:733-761`), rendered as dropdowns — visible in the
rendered page as `getMatchesForSample (1)`, `getMatchesForSampleVs (1)`,
`combineMatchesToCross (3)`.

**#51's search half was dead code, and the obvious fix would have shipped a broken
feature.** `data.py:700` read `query = request.form['Search']` and passed it to the
template, but `getQueueData` was never given a `filter`, and the search box itself was
inside a Jinja comment (`jobs.html:139-148`). Measured before the fix: `POST /data/jobs`
with no `Search` field answered **400**; with one, it returned the same 85 rows as the
unfiltered page.

The trap: `McritClient.getQueueData(…, filter=…)` exists, but
mcrit 1.8.1's `mcrit/queue/QueueRemoteCalls.py:37-42` applies the filter **after** paging —
`[job for job in self.queue.get_jobs(start_index, limit, method, state, ascending) if
filter in job.parameters]`. So `start=0, limit=25, filter=x` answers "the matches among
jobs 0–24", not "the first 25 matches". Wiring it would have produced "page 3 of 40,
showing 2 results", because MCRITweb's `Pagination` is sized from `getQueueStatistics()`,
which has no filter concept at all.

That is why the search is implemented by fetching the category unpaged and filtering in
MCRITweb, and why the POST route is gone rather than repaired.

**#46 (cross duration) and #55 (rerun), #41 (long names), #39 (inconsistent names)** each
have their own change. **#47 (queue cache)** is blocked upstream — see ADR-0005.

## Consequences

Anyone returning to #51 should not "just pass `filter=`". The backend filter is unusable
with a `limit` until `QueueRemoteCalls.getQueueData` filters before paging, and that is an
mcrit change. The line numbers above are mcrit 1.8.1's; `setup.py` only requires
`mcrit>=1.5.3`, so re-check them against the version actually installed before concluding
that the block has lifted - the shape to look for is a `filter` applied to the result of
`self.queue.get_jobs(start_index, limit, ...)` rather than passed into it.

## Outcome

Keep #57 open as a tracker. Its own actionable content — the dead `POST /data/jobs`
handler and the commented-out search markup — is removed by the #51 change; what remains
under it is the children, each tracked separately.
