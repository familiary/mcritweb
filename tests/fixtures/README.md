# Test fixtures

Real MCRIT reports, captured from a live backend so the result pages can be rendered
offline. Everything here is the **backend's** wire format — what `McritClient` hands
to the views — not MCRITweb's.

`tests/fixtureData.py` loads them and serves them through `CorpusMcritClient`.

## The corpus

Thirteen samples on a local `docker-mcrit` instance:

| family | samples | note |
|---|---|---|
| win.citadel | 3 | versions 1.3.5.1, 1.3.4.0, 0.0.1.1 |
| win.vmzeus | 2 | 2.x and 3.x |
| win.dridex | 2 | |
| MSVC | 6 | `is_library`, 2003–2013 x86, ~330k functions |

The library family is what makes these fixtures worth having: library matching and
the non-library scores only mean anything against a corpus that contains one.

All samples predate 2015, so nothing here is redacted — filenames and hashes are as
captured.

## What is here

| file | content |
|---|---|
| `status`, `version` | backend identity, for the admin server page |
| `families`, `samples` | the whole corpus, untrimmed |
| `queue`, `queue_statistics` | 17 finished jobs, for the jobs page |
| `matches_for_sample.*` | 1-vs-corpus match report, sample 0 |
| `matches_for_sample_vs.*` | 1-vs-1 match report, samples 1 and 3 |
| `matches_for_query.*` | query report — `is_query`, negative sample id |
| `cross_compare.*` | cross compare over five samples |
| `unique_blocks.*` | unique blocks for a family, with its YARA rule |
| `maintenance_*.*` | the three maintenance jobs (`rebuildIndex`, `recalculatePicHashes`, `recalculateMinHashes`) and their results, one per branch of `result_maintenance.html` |
| `functions_reference_<id>` | first 100 functions of a reference sample, graphs intact |
| `functions_matched` | every function the 1-vs-1 page resolves by id, graphs dropped |

Each report is a `.job` and a `.result` pair: `data.result()` dispatches on
`job_info.parameters`, so the job record is as load-bearing as the report.

## Regenerating

```bash
python tests/fixtures/regenerate.py [http://127.0.0.1:8000]
```

It finds jobs by method name and recency rather than by id, so it runs against any
instance that has one finished job of each type. If a type is missing it says so and
writes nothing for it, rather than leaving a stale fixture in place.

Two things are trimmed, and both trims are load-bearing rather than cosmetic — read
the module docstring in `regenerate.py` before changing them. In short: the reference
function pool keeps its `xcfg`, because `clusterLinkHuntResult()` rebuilds an
`SmdaFunction` from every entry it is given; and the by-id pool must be *complete*
for the reports it serves, because `data.py` indexes that lookup directly instead of
tolerating a miss.

Widen the pools when a test needs the filtered result views (`?funid=`, `?samid=`,
`?famid=`), which reach for matched entries beyond what is captured here.
