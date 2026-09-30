"""Server, session and local-path plumbing shared across the view modules.

Request-parameter parsing lives in params.py; the function-diff block comparison
lives in functiondiff.py. See issue #88.
"""

import collections
import functools
import math
import os
import re
import shutil
import threading
import time

import requests
from flask import current_app, flash, g, redirect, session, url_for

from mcritweb import db
from mcritweb.db import ServerInfo, UserColumnSettings


def get_server_url():
    server_info = ServerInfo.fromDb()
    return server_info.url


def get_server_token():
    server_info = ServerInfo.fromDb()
    return server_info.server_token


# (connect, read) seconds for the reachability probe. Without a timeout, requests
# waits indefinitely, so an unresponsive backend hangs the page until the WSGI or
# NGINX timeout fires - 300s in the docker-mcrit deployment.
SERVER_PROBE_TIMEOUT = (3.05, 10)


def default_server_probe():
    """True if the configured MCRIT server answers and accepts our token.

    NOTE: this is a blocking HTTP round-trip, performed on every request to a route
    decorated with mcrit_server_required.
    """
    result = requests.get(
        f"{get_server_url()}/",
        headers={"username":"mcritweb", "apitoken": get_server_token()},
        timeout=SERVER_PROBE_TIMEOUT,
    )
    return result.status_code != 401


#: The last probe answer. Module-level, so it is per worker process - two workers can
#: briefly disagree, which is the cost of not making this round-trip on every request
#: to all 36 decorated routes. See issue #89.
#:
#: Two timestamps, because they answer different questions. `answered_at` is when the
#: probe came back, and it is what the TTL is measured from: taking it before the call
#: would make every entry `probe_duration` seconds old at birth, so a slow backend -
#: precisely the case where a per-request round-trip hurts - would get little or no
#: caching. `started_at` is when the probe went out, and it is what decides which of two
#: concurrent answers to keep: the probe that *started* later observed the more recent
#: state, even if it finished first.
#:
#: `error` holds a transport failure instead of an answer, and exactly one of the two
#: is ever set. An unreachable backend is the expensive case - the probe sits out its
#: 3.05s connect timeout - so it is the one that most needs not to be repeated.
_ProbeAnswer = collections.namedtuple("_ProbeAnswer", "server_url started_at answered_at was_up error")
_probe_cache = None
_probe_cache_lock = threading.Lock()

#: Bumped by forget_server_probe. A probe that was already in flight when the settings
#: changed carries the old generation and its answer is discarded rather than written
#: over the invalidation - without this, an admin who has just corrected a bad token
#: keeps seeing the failure for up to the full TTL, which is the one thing
#: forget_server_probe exists to prevent. The probe runs on 36 routes, so on a busy
#: instance one is nearly always in flight when the settings are saved.
_probe_generation = 0


def forget_server_probe():
    """Drop the cached answer. Called when the server settings change, so an operator
    who has just fixed the URL or token does not wait out the TTL to see it work."""
    global _probe_cache, _probe_generation
    with _probe_cache_lock:
        _probe_cache = None
        _probe_generation += 1


def _remember(fresh, generation):
    """Store `fresh` unless something newer already knows better."""
    global _probe_cache
    with _probe_cache_lock:
        if generation == _probe_generation and (_probe_cache is None or _probe_cache.started_at <= fresh.started_at):
            _probe_cache = fresh


def probe_server(probe, ttl, server_url):
    """`probe()`, but at most once per `ttl` seconds per backend URL.

    A transport failure is cached and replayed like any other answer. It is the case
    the cache exists for: a backend that answers 401 costs one round-trip, while one
    that blackholes the connection costs the full 3.05s connect timeout, on every
    request to all 36 decorated routes. Leaving that uncached would have meant the
    cache saved nothing in precisely the situation issue #89 opens with. The cost is
    the same bounded staleness already accepted for a cached "down": for up to `ttl`
    after the backend comes back, callers still see the failure.

    Only `requests.RequestException` is cached. Anything else out of `probe()` is a
    fault in this application rather than a report about the backend - a failed read
    of the server settings, say - and repeating a wrong answer for `ttl` seconds is
    not an improvement on raising it.

    Keyed by URL so pointing the instance at another backend re-probes immediately;
    every other settings change - a token correction, say - calls forget_server_probe().

    The lock is not held across the probe. Serialising every request on all 36 decorated
    routes behind one HTTP call with a 13-second timeout would be worse than the problem
    this cache is solving. Two threads arriving on a cold cache both probe; the write is
    reconciled under the lock afterwards, against both the generation and the incumbent's
    start time, so neither a concurrent invalidation nor an older answer is lost.
    """
    if ttl <= 0:
        return probe()
    with _probe_cache_lock:
        cached = _probe_cache
        generation = _probe_generation
    if cached is not None and cached.server_url == server_url and time.monotonic() - cached.answered_at < ttl:
        if cached.error is not None:
            # cleared first because raising one stored object repeatedly appends this
            # request's frames to its traceback each time, and the entry is replayed on
            # every request for the length of the TTL.
            raise cached.error.with_traceback(None)
        return cached.was_up

    started_at = time.monotonic()
    try:
        answer = probe()
    except requests.RequestException as failure:
        _remember(_ProbeAnswer(server_url, started_at, time.monotonic(), None, failure), generation)
        raise
    _remember(_ProbeAnswer(server_url, started_at, time.monotonic(), answer, None), generation)
    return answer


_UNSET = object()
_ttl_warned_about = _UNSET


def probe_ttl_from_config():
    """`MCRIT_SERVER_PROBE_TTL` as a usable number of seconds, or 0 if it is not one.

    Resolved and coerced *outside* the decorator's try block, which is the whole point.
    A config file is hand-written, so `MCRIT_SERVER_PROBE_TTL = "5"` is an easy thing to
    write. Left inside the try, the `ttl > 0` comparison raises TypeError, the broad
    `except Exception` below reads that as the backend being unreachable, and every one
    of the 36 decorated routes redirects with "No connection to the MCRIT server" -
    while the undecorated index renders happily against the same healthy backend, and
    nothing is logged.

    A quoted number is read as the number: the defect is the misreported outage, not the
    quoting, and "5" says what it means. Anything else costs the cache and nothing else -
    fall back to 0, the un-cached behaviour this feature replaced, and name the key in
    the log so an operator can find it.

    `inf` and `nan` have to be refused explicitly, because `float()` accepts both and
    neither is caught by a `< 0` test. They are not merely useless here, they are worse
    than the bug this function fixes: at `inf` the entry never expires, and since
    `probe_server` replays a cached `RequestException` by re-raising it, one transient
    blip would pin "No connection to the MCRIT server" on all 36 routes for the life of
    the worker process. At `nan` every comparison is False, so the fast path is skipped,
    the cache is written under the lock on every request, and it can never hit.

    `bool` is refused for the same reason a reader would be surprised by it: `True` is
    not a one-second cache, it is somebody expecting an on/off switch.
    """
    raw = current_app.config.get("MCRIT_SERVER_PROBE_TTL", 0)
    if isinstance(raw, bool):
        return _unusable_ttl(raw, "is a boolean, and this key is a number of seconds")
    try:
        ttl = float(raw)
    except (TypeError, ValueError):
        return _unusable_ttl(raw, "is not a number")
    if not math.isfinite(ttl):
        return _unusable_ttl(raw, "is not a finite number")
    if ttl < 0:
        return _unusable_ttl(raw, "is negative")
    return ttl


def _unusable_ttl(raw, complaint):
    """0, plus one log line per distinct bad value rather than one per request.

    The decorator runs on 36 routes, so warning unconditionally would turn a
    one-character config typo into an unbounded stream on the hot request path. Keyed on
    the value, so correcting the config - or breaking it a second, different way - is
    still reported.
    """
    global _ttl_warned_about
    if repr(raw) != repr(_ttl_warned_about):
        _ttl_warned_about = raw
        current_app.logger.warning(
            "MCRIT_SERVER_PROBE_TTL is %r, which %s - probing the backend on every "
            "request instead of caching the answer. Set it to a number of seconds, or "
            "remove it to take the default.", raw, complaint)
    return 0


def mcrit_server_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        # resolved from config so tests can substitute it, in the same way the
        # backend client itself is substituted via MCRIT_CLIENT_FACTORY (issue #88)
        probe = current_app.config.get("MCRIT_SERVER_PROBE", default_server_probe)
        # both of these are resolved outside the try on purpose. A bad config value and
        # a database that will not open are faults in this application; reported from
        # inside, the `except Exception` below would dress either up as a backend
        # outage - the exact defect probe_ttl_from_config exists to fix.
        ttl = probe_ttl_from_config()
        server_url = get_server_url() if ttl > 0 else None
        try:
            if not probe_server(probe, ttl, server_url):
                flash('Connected to MCRIT server but could not authenticate - Did you configure a token in the server settings?', category='error')
                return redirect(url_for('index'))
        except Exception:
            flash('No connection to the MCRIT server', category='error')
            return redirect(url_for('index'))
        return view(**kwargs)
    return wrapped_view


def get_session_user_id():
    try:
        user_id = int(session['user_id'])
        if user_id > 0:
            return user_id
    except Exception:
        return None


def get_username(request=None):
    username = "guest"
    if g.user is not None:
        username =  g.user.username
    elif request and "apitoken" in request.headers:
        provided_token = request.headers.get("apitoken", "")
        username_by_token = db.get_username_by_apitoken(provided_token)
        if username_by_token is not None:
            username = username_by_token
    elif request and "username" in request.headers:
        username = request.headers.get("username")
    return username


def get_user_column_setup(table_type:str):
    if table_type not in UserColumnSettings._default_settings.keys():
        raise Exception(f"Unknown table type for user column settings: {table_type}")
    # load user column setup from database
    user_id = get_session_user_id()
    user_column_settings = UserColumnSettings.fromDb(user_id)
    # if we don't have them yet, create them
    if user_column_settings is None:
        user_column_settings = UserColumnSettings(user_id)
        user_column_settings.saveToDb()
    ucs_dict = user_column_settings.toUserColumnSettings()
    return ucs_dict[table_type]["active"]

#: A backend-issued job id, as it is allowed to appear in a path: the two shapes the
#: two queue implementations actually answer, and nothing else. MongoQueue hands back
#: `str(ObjectId)` and LocalQueue `str(uuid.uuid4())`.
#:
#: Matching the shape rather than merely excluding separators is what keeps three
#: things out. A path cannot walk out of the folder. `MongoQueue.put` returns None on
#: an unacknowledged insert and `QueueRemoteCalls.submitPayloadQueue` wraps its answer
#: in `str()`, so a backend failing that way hands out the literal string "None" - one
#: name, shared by every such failure, which is the collision this whole change exists
#: to remove. And a Windows device name - NUL, CON, AUX, COM1 - is a valid filename
#: that opens the device instead of a file, so `open(".../uploads/NUL", "wb")` stores
#: nothing at all and says nothing about it.
#:
#: `\Z` and not `$`, which would also match before a trailing newline.
QUERY_UPLOAD_JOB_ID = re.compile(
    r"^(?:[0-9a-fA-F]{24}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z")


def query_upload_path(app, job_id):
    """Where `analyze.query` keeps the file one query job was run for, or None.

    The single definition of that name, because two sides depend on it agreeing: the
    query route writes the file, and promoting a query to a sample (issue #9) reads it
    back to resubmit the same bytes. It is keyed by job id because that is what both
    of them have and neither of them can choose - the id is issued by the backend when
    the job is queued, after the upload has been accepted.

    It used to be keyed by a sha256 instead, and that is the bug this replaces: for an
    .smda upload the hash was read out of the uploaded report's own `sha256` field, so
    any visitor could name the file after another user's query and overwrite it. A
    digest of the uploaded bytes fixes that half, but leaves the read side unable to
    find anything - a query report records the sample's declared hash, not a hash of
    the bytes that were posted, so there is no path from a job to that name.

    Returns None for anything that is not a usable job id, so the caller decides what
    an unpromotable job should say rather than this raising. An id that does not look
    like one means a backend that is not answering properly, not a name to repair.
    """
    if not isinstance(job_id, str) or not QUERY_UPLOAD_JOB_ID.match(job_id):
        return None
    return os.sep.join([app.instance_path, "temp", "uploads", job_id])


def ensure_local_data_paths(app, clear_data=False):
    # nuke both cache and temp folders
    nuke_paths = [
        app.instance_path + os.sep + "cache",
        app.instance_path + os.sep + "temp"
    ]
    # ensure the instance and cache folders exists
    ensure_paths = [
        app.instance_path + os.sep + "cache" + os.sep + "diagrams",
        app.instance_path + os.sep + "cache" + os.sep + "results",
        app.instance_path + os.sep + "temp" + os.sep + "reports",
        app.instance_path + os.sep + "temp" + os.sep + "diagrams",
        app.instance_path + os.sep + "temp" + os.sep + "uploads",
    ]
    if clear_data:
        for path in nuke_paths:
            shutil.rmtree(path)
    for path in ensure_paths:
        try:
            os.makedirs(path)
        except FileExistsError:
            pass


def get_mcritweb_version_from_setup():
    this_file_path = str(os.path.abspath(__file__))
    project_root = str(os.path.abspath(os.sep.join([this_file_path, "..", "..", ".."])))
    setup_path = os.path.abspath(os.sep.join([project_root, "setup.py"]))
    mcritweb_version = None
    with open(setup_path) as fin:
        for line in fin.readlines():
            line = line.strip()
            match = re.search(r'version="(?P<version_str>\d+\.\d+\.\d+)",', line)
            if match:
                mcritweb_version = match.group("version_str")
    return mcritweb_version
