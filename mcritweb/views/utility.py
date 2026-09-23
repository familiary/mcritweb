"""Server, session and local-path plumbing shared across the view modules.

Request-parameter parsing lives in params.py; the function-diff block comparison
lives in functiondiff.py. See issue #88.
"""

import functools
import os
import re
import shutil

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


def mcrit_server_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        # resolved from config so tests can substitute it, in the same way the
        # backend client itself is substituted via MCRIT_CLIENT_FACTORY (issue #88)
        probe = current_app.config.get("MCRIT_SERVER_PROBE", default_server_probe)
        try:
            if not probe():
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
