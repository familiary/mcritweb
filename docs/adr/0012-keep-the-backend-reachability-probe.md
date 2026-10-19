# Keep the backend reachability probe, cached; the token check is what it is for

---
status: accepted — the probe stays, behind a short TTL. Removing it is blocked on the
two preconditions under "When the probe can be removed".
---

Issue #89 asks whether `mcrit_server_required` should keep making a blocking HTTP
round-trip to the backend before every request to a decorated route, and offers two
answers: a short TTL cache as *"the cheap win"*, or *"remove the probe, handle errors
at the call site"* as *"the real fix"*, coupled with #43. It says the choice belongs
to whoever owns the error-handling story rather than to a drive-by change, so this
records the choice with the measurements behind it.

**The decision: keep the probe, cache it for `MCRIT_SERVER_PROBE_TTL` seconds
(default 5), and cache the transport failure as well as the answer. Do not remove
it.** The two options in the issue are not alternatives, because the probe and
call-site error handling detect *different failures*. #43 owns the backend that
cannot be reached. The probe owns the backend that can be reached and refuses our
token, and today nothing else in this application can see that at all.

## What the cache buys

The probe runs on 36 routes — `analyze.py` 10, `data.py` 12, `explore.py` 13,
`api.py` 1 — which is most of the application.

Counted through the app with a probe that increments a counter, an eight-page
browsing session (`/explore/families`, `/explore/samples`, `/explore/functions`,
`/explore/families`, `/data/jobs`, `/explore/statistics`, `/explore/samples`,
`/explore/families`):

| `MCRIT_SERVER_PROBE_TTL` | round-trips |
| --- | --- |
| `0` — the behaviour before this change | 8 |
| `5` — the default | 1 |

What one round-trip costs, running `default_server_probe` unchanged against a
loopback HTTP server that answers instantly, 200 samples, four repetitions:

- probe: **1.6–1.9 ms median**, p90 2.3–15.3 ms, max ~27 ms
- cache hit: **0.0004 ms median**

End to end, the same app rendering `/explore/families` against fixture data, three
repetitions: **5.7–5.9 ms median at TTL 0, 2.8–3.0 ms cached.** So on a page whose own
work is fake-fast the probe is about half the server-side time. That ratio is the
wrong way to read it — against a real backend the view's own queries dominate. The
number to carry is the flat one: **roughly 2 ms and one TCP connection per request,
on every decorated route, to re-answer a question answered milliseconds earlier.**
`default_server_probe` calls bare `requests.get` with no session, so each probe is a
fresh connect, and it reads `ServerInfo` from SQLite twice (URL and token) on the way.

Loopback is the floor. The `docker-mcrit` reference deployment puts mcrit-server in a
neighbouring container, so a real deployment pays this plus a hop.

## The case the cache was not covering, and now is

The first version of this cache stored only answers that arrived; a probe that raised
was re-run every time. That left the cache saving nothing in exactly the situation
issue #89 opens with. A backend that answers 401 costs one round-trip. A backend that
blackholes the connection costs the whole connect timeout — `SERVER_PROBE_TIMEOUT` is
`(3.05, 10)` — and it costs it *on every request to all 36 routes*, so an outage made
every page in the application slow rather than just wrong.

Measured with a probe that sleeps and then raises `ConnectTimeout`, standing in for
the connect timeout at 1/10th scale, five requests at TTL 60:

| | probes | elapsed |
| --- | --- | --- |
| failure not cached | 5 | 1.58 s |
| failure cached | 1 | 0.30 s |

At the real 3.05 s connect timeout that is 15.25 s of blocking across a five-request
burst, reduced to 3.05 s and thereafter one probe per TTL. `requests.RequestException`
is now stored and replayed like any other answer. Only that exception: anything else
out of the probe is a fault in this application rather than a report about the
backend, and repeating a wrong answer for the length of the TTL is not an improvement
on raising it.

The stored exception is replayed with `.with_traceback(None)`. A plain `raise` of one
stored object appends the raising frame to its traceback every time — measured at one
frame per replay, unbounded — and this object is replayed on every request for the
length of the TTL.

## Why the probe cannot simply be removed

The issue's "real fix" is to drop the pre-flight probe and handle failures where the
call happens. That is right about the backend being *unreachable*, and #43 builds
exactly that. It does not reach the other failure, and the other failure is the one
the probe was written for.

**mcrit's client does not surface a rejected token.** `handle_response` in
`mcrit/client/McritClient.py` branches on 500/501, 400/404/410 and 200/202. A 401
matches none of them, so it falls through and returns `None` — the same `None` that
means "no such sample". No exception is raised, because at the HTTP level nothing went
wrong. Call-site handling built on `requests.RequestException`, which is what #43
catches and is the correct narrowness for it, cannot see this at all.

Measured, with a client whose every call returns `None` — a faithful stand-in for a
backend that is up and refusing our token:

| | with the probe | with the probe removed |
| --- | --- | --- |
| `/explore/families` | 200 + flash | **500** |
| `/explore/samples` | 200 + flash | **500** |
| `/data/jobs` | 200 + flash | **500** |
| `/explore/statistics` | 200 + flash | 200, empty, indistinguishable from no data |

The flash is `Connected to MCRIT server but could not authenticate - Did you configure
a token in the server settings?`, which names the cause and the fix. Removing the
probe trades that for three stack traces and one page that quietly lies. `401` appears
exactly once in this repository — in `default_server_probe`. There is no second place
that knows.

## What the probe is *not* buying, and should not be credited with

Its own failure path does not work, and this is worth recording so nobody defends the
probe on a benefit it does not deliver. When the probe raises, the decorator flashes
"No connection to the MCRIT server" and redirects to the index. Measured with
`TESTING` off: `/explore/families` → 302 to `/` → **500**. `index()` is not decorated,
but for any logged-in non-pending user it calls `getQueueData`, `getSampleById`,
`getFamily` and `search_samples` itself — against the same dead backend. All 35
decorated pages sit behind `visitor_required` or `contributor_required`, both of which
exclude `pending`, so *every* user who can reach one takes that branch of `index()`.
The friendly message is queued into a session and then never rendered, because the 500
is Werkzeug's default error page rather than `index.html`.

So for an unreachable backend the probe currently converts one 500 into a redirect and
then a 500. #43's rendered `backend_unavailable.html`, which keeps the URL and carries
a status that means what happened, is strictly better there. The two changes compose
cleanly precisely because they are aimed at different failures.

## When the probe can be removed

Two things must be true first. Neither is true today, and neither is in scope for #89.

1. **A rejected token must be distinguishable from an empty result.** Either mcrit's
   `handle_response` learns to raise on 401 — the option #43 calls "have mcrit raise
   semantically meaningful exceptions" — or mcritweb wraps `McritClient` and raises its
   own. Until then, deleting the probe deletes the only detection of a misconfigured
   token.
2. **The failure path must not redirect to a page that shares the failure.** #43
   replaces the redirect with a rendered page for the transport case; the same has to
   hold for whatever reports a rejected token.

Once both hold, the probe is redundant — it is a time-of-check-to-time-of-use pattern,
it can pass and the real call still fail, and the views need the handling regardless —
and removing it saves the round-trip this ADR is otherwise paying to reduce.

## Consequences

The cache is a module global, so it is **per worker process**. With N gunicorn workers
the answer is reached up to N times per TTL, and two workers can briefly disagree.
Per-request caching would buy nothing, since one request only ever hits one decorated
route.

Staleness is accepted in both directions and bounded by the TTL:

- **stale "up"** — the backend goes down and, for up to 5 s, users get past the check
  and meet the failure inside the view instead of at the gate. With #43 landed that is
  a rendered page rather than a stack trace, which narrows this cost considerably.
- **stale "down"** — the backend comes back and users keep seeing the error for up to
  5 s.

`forget_server_probe()` drops the entry, and is called when the server settings change
and when a new app is created. A probe already in flight when that happens carries the
old generation and its answer is discarded rather than written over the invalidation,
so an operator who has just corrected a bad token does not keep seeing the failure.
The entry is also keyed by server URL, so pointing the instance at another backend
re-probes at once.

`MCRIT_SERVER_PROBE_TTL = 0` restores the pre-#89 behaviour exactly, and is the escape
hatch if the staleness turns out to matter more than the round-trip somewhere.

`tests/testServerProbe.py` holds all of the above, including the counts in the first
table and the two staleness edges.
