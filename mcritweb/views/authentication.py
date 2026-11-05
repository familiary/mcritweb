import datetime
import functools
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import uuid

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from mcritweb import db
from mcritweb.db import ServerInfo, UserColumnSettings, UserFilters, UserInfo, generate_apitoken, is_legacy_apitoken, utc_now
from mcritweb.views.utility import mcrit_server_required

bp = Blueprint('authentication', __name__, url_prefix='/')

#: Every role this application recognises, weakest first - the ladder described in
#: CONTEXT.md. The decorators below and `token_required` compare against these
#: names, so a value outside the list fails every check: an account holding one can
#: reach nothing and no page explains why. Anything writing `user.role` validates
#: against this first. See issue #95.
KNOWN_ROLES = ('pending', 'visitor', 'contributor', 'admin')

#: One message for both halves of a failed login. Saying which half was wrong tells an
#: unauthenticated caller whether an account exists, one request at a time. See #101.
LOGIN_FAILED = 'Incorrect username or password.'

#: One message for a registration that did not produce an account. "That username is
#: taken" is the same disclosure /login was just stopped from making, from a route that
#: is just as anonymous. Nothing is lost by staying quiet: a new account is created
#: `pending` and cannot be used until an admin approves it, so the honest instruction
#: is the same either way.
REGISTRATION_SUBMITTED = ('Registration submitted. An administrator has to approve the account before you can '
                          'log in - if you cannot log in shortly, please contact one.')

#: What a throttled caller is told. Deliberately the same shape as LOGIN_FAILED - it
#: names no account and confirms nothing, so the throttle does not become the oracle
#: that collapsing the two login errors was meant to close.
TOO_MANY_ATTEMPTS = ('Too many failed attempts from this address. Please wait a few minutes '
                     'and try again.')

#: What the second of two simultaneous first registrations is told. The first one set
#: the instance up, so this one is neither admin nor a pending account: the first-user
#: form carries no registration token, and the instance may now require one.
FIRST_USER_TAKEN = ('This instance was set up by another registration a moment ago. No new user was '
                    'created - please register again.')


def registration_token_attempt(username):
    """The name a wrong registration token is recorded under. A username can never
    contain ':', so no successful login carries it, and clear_login_failures - which
    forgets only the failures under the name that just logged in - cannot reset the
    token budget. Recording it under the typed name let anyone with an account guess
    tokens without end, by typing their own name and logging in after every nine."""
    return "register:" + (username or "")


#: Longer than any account name can be (3 to 20 characters, see register() and
#: change_username), so a real one is always logged whole.
LOGGED_NAME_LENGTH = 32


def _name_for_log(username):
    """The typed name for the throttle's log line. It comes from the form as typed, up
    to the form field limit, and a refused attempt is cheap to send: logged whole, every
    one of them could add half a megabyte to the log."""
    if len(username) <= LOGGED_NAME_LENGTH:
        return repr(username)
    return f"{username[:LOGGED_NAME_LENGTH]!r}... ({len(username)} characters)"


def _throttled(username=None):
    """True if this caller has spent their attempts, having logged the fact.

    The log line is the operator's view of an attempt in progress, which is the second
    thing issue #101 asks to decide. It names the account only when the attempts are
    aimed at one - a spray across many names and a run against `admin` want different
    responses, and the count is what tells them apart.
    """
    remote_addr = request.remote_addr
    if not db.login_is_throttled(remote_addr):
        return False
    if username:
        against = db.count_recent_login_failures(remote_addr, username)
        current_app.logger.warning(
            "throttled %s after %d recent failures, %d of them against %s",
            remote_addr, db.count_recent_login_failures(remote_addr), against, _name_for_log(username))
    else:
        current_app.logger.warning(
            "throttled %s after %d recent failures", remote_addr,
            db.count_recent_login_failures(remote_addr))
    return True


#: A real hash to check a password against when the username does not exist, so that
#: "no such user" costs what "wrong password" costs. The message alone does not close
#: the hole: password hashing is deliberately slow, so skipping it is measurable.
#:
#: The method matters as much as the fact of hashing. check_password_hash costs whatever
#: the *stored* hash asks for, and werkzeug's default has moved across the versions this
#: app has been pinned to - measured here on werkzeug 3.1.8, one check costs 66 ms for
#: pbkdf2:sha256:260000 (its 2.2 default, which is what this repo pinned until #27),
#: 150 ms for pbkdf2:sha256:600000, and 93 ms for scrypt:32768:8:1. A dummy built with
#: today's default therefore equalises nothing on a database whose rows predate the
#: upgrade - it just moves the tell. So the dummy is built with the method the user
#: table actually uses, and rebuilt if that answer changes.
#:
#: Built on first use rather than at import, because every app start - and every test
#: that builds one - would otherwise pay for a hash nobody needs.
_ABSENT_USER_PASSWORD_HASH = None
_ABSENT_USER_HASH_METHOD = None


def _spend_a_password_check(password):
    """Do the work a real password check would, and throw the answer away."""
    global _ABSENT_USER_PASSWORD_HASH, _ABSENT_USER_HASH_METHOD
    method = db.get_stored_password_hash_method()
    if _ABSENT_USER_PASSWORD_HASH is None or _ABSENT_USER_HASH_METHOD != method:
        secret = secrets.token_urlsafe(32)
        # an empty table has no method to match; the default is as good an answer as
        # any, and there is no account to be told apart from in the first place
        try:
            _ABSENT_USER_PASSWORD_HASH = (generate_password_hash(secret, method=method) if method
                                          else generate_password_hash(secret))
        except ValueError:
            # the stored hashes name something werkzeug can verify but not generate - a
            # legacy md5$/sha1$ hash, or a column written by something other than this
            # app. Falling back costs the timing match for those rows; raising would
            # make /login 500 for absent usernames *only*, which is both a denial of
            # service and a perfect existence oracle - strictly worse than the leak
            # this whole change is closing.
            current_app.logger.warning("Cannot generate a %r hash for the absent-user check; using the default", method)
            _ABSENT_USER_PASSWORD_HASH = generate_password_hash(secret)
        _ABSENT_USER_HASH_METHOD = method
    check_password_hash(_ABSENT_USER_PASSWORD_HASH, password)


def _rehash_if_stale(user_info, password):
    """Move a verified password onto the current hashing method.

    Only reachable with a password that has just been checked, so the plaintext is in
    hand and the rewrite is safe. This is what lets the table converge on one cost:
    without it, a database carrying a mix of old and new hashes keeps a timing
    difference between accounts that _spend_a_password_check cannot match with one dummy.
    """
    global _ABSENT_USER_PASSWORD_HASH, _ABSENT_USER_HASH_METHOD
    stored_method = user_info.password.split("$", 1)[0]
    if stored_method == _current_hash_method():
        return False
    user_info.password = generate_password_hash(password)
    # the dummy was built to match the old method, so it is now the odd one out
    _ABSENT_USER_PASSWORD_HASH = None
    _ABSENT_USER_HASH_METHOD = None
    return True


_CURRENT_HASH_METHOD = None


def _current_hash_method():
    """The method a hash generated right now would carry. One hash per process."""
    global _CURRENT_HASH_METHOD
    if _CURRENT_HASH_METHOD is None:
        _CURRENT_HASH_METHOD = generate_password_hash(secrets.token_urlsafe(8)).split("$", 1)[0]
    return _CURRENT_HASH_METHOD

#: The roles whose API token is worth anything. `token_required` is the authority; the
#: settings page reads it too, so the page cannot show a token to someone whose token
#: does not work, or - the way it went wrong - hide one that does. `pending` gets
#: nothing, as in the web UI.
API_ROLES = ('visitor', 'contributor', 'admin')


@bp.before_app_request
def set_is_first_user():
    g.first_user = db.is_first_user()


@bp.before_app_request
def set_operation_mode():
    # a request run inside an already-pushed app context (as the tests do) shares its
    # `g`, so drop anything built from the server settings before this request
    g.pop('server_info', None)
    g.pop('mcrit_client', None)
    if not g.first_user:
        g.operation_mode = db.get_server_info().operation_mode


def multi_user(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if not g.first_user and g.operation_mode == 'single':
            flash('You are in single user mode, no need to register a user.', category='error')
            return redirect(url_for('index'))
        return view(**kwargs)
    return wrapped_view


@bp.route('/register', methods=('GET','POST'))
@multi_user
def register():
    user_id = session.get("user_id", None)
    if user_id is not None and not g.first_user:
        error = 'You already have a registered account.'
        flash(error, category='error')
        return redirect(url_for('index'))
    server_info = db.get_server_info()
    is_registration_token_required = False
    if server_info:
        is_registration_token_required = server_info.registration_token not in [None, ""]
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['inputPassword1']
        provided_registration_token = ""
        if 'registrationToken' in request.form:
            provided_registration_token = request.form['registrationToken']
        error = None
        # the guessable secret on this route is the registration token, so it is metered
        # by the same counter as /login. Ordinary validation slips - a short username, a
        # password typed twice differently - deliberately do NOT record an attempt: they
        # are not guesses, and counting them would lock people out of their own signup.
        if _throttled(registration_token_attempt(username)):
            error = TOO_MANY_ATTEMPTS
        elif not username:
            error = 'Username is required.'
        elif not db.is_well_formed_username(username):
            error = "Username has wrong format. Must be 3-20 characters, alphanumeric with dots and underscores allowed, but cannot start or end with dots/underscores, nor contain two of them in a row."
        elif db.is_reserved_username(username):
            error = "Username is reserved."
        elif not password:
            error = 'Password is required.'
        elif not password == request.form['inputPassword2']:
            error = 'The passwords do not match. No new user was created.'
        elif is_registration_token_required and server_info.registration_token != provided_registration_token:
            error = 'Invalid registration token provided. No new user was created.'
            db.record_failed_login(request.remote_addr, registration_token_attempt(username))
        if error is None:
            user_info = UserInfo()
            user_info.username = username
            user_info.password = generate_password_hash(password)
            # TODO make it configurable what the default role for new users should be, but stick with pending for now
            user_info.role = "pending"
            if g.first_user:
                user_info.role = "admin"
                server_info = ServerInfo()
                server_info.url = request.form['url']
                server_info.operation_mode = request.form['operationMode']
                server_info.registration_token = request.form['setRegistrationToken'] if request.form['setRegistrationToken'] else ""
                server_info.server_token = request.form['mcritServerToken'] if request.form['mcritServerToken'] else ""
                server_info.server_uuid = str(uuid.uuid4())
                server_info.server_version = current_app.config['MCRITWEB_VERSION']
                # g.first_user was decided before this view ran, and two registrations can
                # both get here while the table is empty. Take the write lock and ask again,
                # so only one of them becomes admin and sets the server settings. The lock
                # is held until the user row below commits, or until the request ends and
                # close_db() closes the connection, which discards whatever was not committed.
                db.get_db().execute("BEGIN IMMEDIATE;")
                if not db.is_first_user():
                    # this request still has g.first_user set and would render the setup
                    # form again; the next one sees the instance as it now is
                    flash(FIRST_USER_TAKEN, category='error')
                    return redirect(url_for('authentication.register'))
            if g.first_user:
                try:
                    server_info.saveToDb(commit=False)
                except Exception:
                    # never surface the exception text: /register is unauthenticated and the
                    # message can carry the database path or SQL fragments
                    current_app.logger.exception("Failed to persist server settings during first-user registration")
                    error = "Server values invalid. Please check the server settings and try again."
            if error is None:
                user_info.registered = utc_now()
                user_info.last_login = 'no login'
                user_info.apitoken = generate_apitoken()
                try:
                    user_info.saveToDb()
                except sqlite3.IntegrityError:
                    # the username is taken. Saying so here would hand back exactly the
                    # answer /login was just stopped from giving, from a route that is
                    # equally anonymous and equally unthrottled. Both outcomes leave by
                    # the same door with the same message. See #101.
                    current_app.logger.info("Registration rejected: requested username is already in use")
                if g.first_user:
                    flash('Registration complete - you can log in now.', category='success')
                else:
                    flash(REGISTRATION_SUBMITTED, category='info')
                return redirect(url_for("authentication.login"))
        flash(error, category='error')
    proposed_registration_token = ""
    if g.first_user:
        proposed_registration_token = str(uuid.uuid4())
    query_token = request.args.get('token')
    if query_token is None or not re.match(r"^[a-zA-Z0-9._\-]{3,36}$", query_token):
        query_token = ""
    default_server = os.environ.get('MCRIT_DEFAULT_SERVER', "http://127.0.0.1:8000")
    return render_template("register.html", default_mcrit_server=default_server, is_registration_token_required=is_registration_token_required, proposed_registration_token=proposed_registration_token, query_token=query_token)


@bp.route('/login', methods=('GET', 'POST'))
def login():
    user_id = session.get("user_id", None)
    if user_id is not None and not g.first_user:
        error = 'You are logged in.'
        flash(error, category='info')
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['inputPassword']

        if _throttled(username):
            # before the password check, so a throttled caller costs no hashing either -
            # the dummy check below is deliberately expensive and would otherwise make
            # this route the cheapest way to spend the server's CPU
            flash(TOO_MANY_ATTEMPTS, category='error')
            return render_template('login.html')

        user_info = UserInfo.fromDb(username=username)
        error = None
        # an empty password never signs in, whatever an account holds - one could be set by
        # a password change before it refused one. It costs the check an unknown name does,
        # so the time the answer takes says nothing about whether the name exists
        if user_info is None or not password:
            _spend_a_password_check(password)
            error = LOGIN_FAILED
        elif not check_password_hash(user_info.password, password):
            error = LOGIN_FAILED
        if error is None:
            session.clear()
            session['user_id'] = user_info.user_id
            user_info.last_login = utc_now()
            verified_password = user_info.password
            rehashed = _rehash_if_stale(user_info, password)
            # after the rehash, which puts a new hash for the same password in user_info
            session[PASSWORD_MARK] = password_mark(user_info)
            user_info.saveLogin(verified_password=verified_password if rehashed else None)
            db.clear_login_failures(request.remote_addr, username)
            return redirect(url_for('index'))
        db.record_failed_login(request.remote_addr, username)
        flash(error, category='error')
    return render_template('login.html')


#: The session key holding `password_mark()` of the password the session logged in with.
PASSWORD_MARK = 'password_mark'


def password_mark(user_info):
    """A digest of the account's stored password hash, keyed with SECRET_KEY.

    The session is a signed cookie, not a server-side record, so logging out elsewhere
    cannot reach a copy of it. What can be checked is whether the password it was issued
    for is still the account's password: the session carries this mark, and a password
    change makes every mark issued before it stop matching. Keyed, because the cookie is
    signed but readable, and an unkeyed digest of the hash would be something to test
    password guesses against. It names the account it was issued for, so a session whose
    user_id was set without logging in - the test suite does that - is not taken for one
    whose password changed.
    """
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    return f"{user_info.user_id}:" + hmac.new(key, user_info.password.encode(), hashlib.sha256).hexdigest()


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get('user_id')
    if user_id is None:
        g.user = None
    else:
        user_info = UserInfo.fromDb(user_id=user_id)
        mark = session.get(PASSWORD_MARK)
        # a session from before marks existed has none, and gets one at the end of this
        # request; it cannot have had one removed, since the cookie is signed
        if user_info is not None and isinstance(mark, str) and mark.startswith(f"{user_id}:") \
                and not hmac.compare_digest(mark, password_mark(user_info)):
            # the password was changed after this session logged in
            session.clear()
            user_info = None
        g.user = user_info


@bp.after_app_request
def mark_the_session_with_the_current_password(response):
    """Give a session that got this far the mark of the account's current password.

    That covers two cases. A session from before marks existed is marked on its first
    request, so a later password change ends it too. And a request that changed the
    account's own password - /admin/change_password - keeps its own session, while every
    other session of the account still carries the old mark and ends on its next request.
    """
    user_info = g.get('user')
    if user_info is not None and session.get(PASSWORD_MARK) != password_mark(user_info):
        session[PASSWORD_MARK] = password_mark(user_info)
    return response


def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('authentication.login'))
        return view(**kwargs)
    return wrapped_view


def format_relative_time(dt, now=None):
    """Format a datetime into a human-readable relative string."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        if dt == "no login" or not dt:
            return "Never"
        try:
            from mcritweb.db import parse_timestamp
            dt = parse_timestamp(dt)
        except Exception:
            return dt
    if not isinstance(dt, datetime.datetime):
        return str(dt)
    if now is None:
        now = utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.UTC)
    delta = now - dt
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "in the future"
    if total_seconds < 60:
        return "just now"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    if days < 365:
        months = max(1, days // 30)
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = max(1, days // 365)
    return f"{years} year{'s' if years != 1 else ''} ago"


def format_file_size(size_bytes):
    """Format byte size into human-readable B, KiB, or MiB."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KiB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MiB"


def format_ttl(seconds):
    """Format remaining seconds TTL into human-readable duration string."""
    if seconds <= 0:
        return "Expired"
    days = int(seconds) // 86400
    rem = int(seconds) % 86400
    hours = rem // 3600
    rem = rem % 3600
    minutes = rem // 60
    if days > 0:
        return f"{days}d {hours}h remaining"
    if hours > 0:
        return f"{hours}h {minutes}m remaining"
    return f"{minutes}m remaining"


def get_role_capabilities():
    """Build role capabilities matrix dynamically reflecting application config."""
    limits = current_app.config.get("QUERY_UPLOAD_LIMITS", {})
    visitor_cap = limits.get("visitor")
    if visitor_cap is None:
        visitor_cap_str = "Capped (1 MiB)"
    elif visitor_cap == 0:
        visitor_cap_str = "Restricted"
    elif visitor_cap % (1024 * 1024) == 0:
        visitor_cap_str = f"Capped ({visitor_cap // (1024 * 1024)} MiB)"
    elif visitor_cap % 1024 == 0:
        visitor_cap_str = f"Capped ({visitor_cap // 1024} KiB)"
    else:
        visitor_cap_str = f"Capped ({format_file_size(visitor_cap)})"

    return [
        {
            "name": "Corpus Exploration & Matching",
            "description": "Browse families, samples, functions, and launch 1vsN, 1vs1, and cross-comparison analyses.",
            "admin": "Full Access",
            "contributor": "Full Access",
            "visitor": "Full Access",
            "pending": "None",
        },
        {
            "name": "Query Uploads (Ephemeral)",
            "description": "Upload binary files or SMDA reports for on-the-fly similarity matching without adding to corpus.",
            "admin": "Uncapped",
            "contributor": "Uncapped",
            "visitor": visitor_cap_str,
            "pending": "None",
        },
        {
            "name": "Binary Sample Submissions",
            "description": "Permanently submit binary samples to be indexed and stored into the MCRIT corpus.",
            "admin": "Allowed",
            "contributor": "Allowed",
            "visitor": "Restricted",
            "pending": "Restricted",
        },
        {
            "name": "Function & Family Curation",
            "description": "Rename functions and modify family assignments across stored corpus samples.",
            "admin": "Allowed",
            "contributor": "Allowed",
            "visitor": "Restricted",
            "pending": "Restricted",
        },
        {
            "name": "Corpus Import & Export",
            "description": "Export analysis data and import full or partial MCRIT corpus packages.",
            "admin": "Allowed",
            "contributor": "Allowed",
            "visitor": "Restricted",
            "pending": "Restricted",
        },
        {
            "name": "REST API Access",
            "description": "Query and interact programmatically with the backend using a personal API token.",
            "admin": "Submit & Curate",
            "contributor": "Submit & Curate",
            "visitor": "Query & Match",
            "pending": "Restricted",
        },
        {
            "name": "Server & User Administration",
            "description": "Configure backend endpoints, manage user accounts and roles, and run maintenance jobs.",
            "admin": "Allowed",
            "contributor": "Restricted",
            "visitor": "Restricted",
            "pending": "Restricted",
        },
    ]


ROLE_CAPABILITIES = [
    {
        "name": "Corpus Exploration & Matching",
        "description": "Browse families, samples, functions, and launch 1vsN, 1vs1, and cross-comparison analyses.",
        "admin": "Full Access",
        "contributor": "Full Access",
        "visitor": "Full Access",
        "pending": "None",
    },
    {
        "name": "Query Uploads (Ephemeral)",
        "description": "Upload binary files or SMDA reports for on-the-fly similarity matching without adding to corpus.",
        "admin": "Uncapped",
        "contributor": "Uncapped",
        "visitor": "Capped (1 MiB)",
        "pending": "None",
    },
    {
        "name": "Binary Sample Submissions",
        "description": "Permanently submit binary samples to be indexed and stored into the MCRIT corpus.",
        "admin": "Allowed",
        "contributor": "Allowed",
        "visitor": "Restricted",
        "pending": "Restricted",
    },
    {
        "name": "Function & Family Curation",
        "description": "Rename functions and modify family assignments across stored corpus samples.",
        "admin": "Allowed",
        "contributor": "Allowed",
        "visitor": "Restricted",
        "pending": "Restricted",
    },
    {
        "name": "Corpus Import & Export",
        "description": "Export analysis data and import full or partial MCRIT corpus packages.",
        "admin": "Allowed",
        "contributor": "Allowed",
        "visitor": "Restricted",
        "pending": "Restricted",
    },
    {
        "name": "REST API Access",
        "description": "Query and interact programmatically with the backend using a personal API token.",
        "admin": "Submit & Curate",
        "contributor": "Submit & Curate",
        "visitor": "Query & Match",
        "pending": "Restricted",
    },
    {
        "name": "Server & User Administration",
        "description": "Configure backend endpoints, manage user accounts and roles, and run maintenance jobs.",
        "admin": "Allowed",
        "contributor": "Restricted",
        "visitor": "Restricted",
        "pending": "Restricted",
    },
]



@bp.route('/settings')
@login_required
def settings():
    # login_required has settled that g.user holds this session's row
    user_info = g.user
    user_id = user_info.user_id
    user_filters = UserFilters.fromDb(user_id)
    user_column_settings = UserColumnSettings.fromDb(user_id)
    # if we don't have them yet, create them
    if user_filters is None:
        user_filters = UserFilters.fromDict(user_id, {})
        user_filters.saveToDb()
    if user_column_settings is None:
        user_column_settings = UserColumnSettings.fromDict(user_id, {})
        user_column_settings.saveToDb()

    registered_formatted = user_info.registered.strftime("%Y-%m-%d %H:%M UTC") if user_info.registered else ""
    registered_relative = format_relative_time(user_info.registered)
    if user_info.last_login and user_info.last_login != "no login":
        last_login_formatted = user_info.last_login.strftime("%Y-%m-%d %H:%M UTC")
        last_login_relative = format_relative_time(user_info.last_login)
    else:
        last_login_formatted = "Never"
        last_login_relative = "No prior login recorded"

    return render_template(
        'settings.html',
        user_info=user_info,
        user_filters=user_filters,
        user_column_settings=user_column_settings.toUserColumnSettings(),
        can_use_api=user_info.role in API_ROLES,
        legacy_apitoken=is_legacy_apitoken(user_info.apitoken),
        registered_formatted=registered_formatted,
        registered_relative=registered_relative,
        last_login_formatted=last_login_formatted,
        last_login_relative=last_login_relative,
    )


@bp.route('/profile')
@login_required
@mcrit_server_required
def profile():
    target_user = g.user
    is_own_profile = True
    is_admin = (g.user.role == 'admin')
    reg = target_user.registered
    if isinstance(reg, str):
        try:
            from mcritweb.db import parse_timestamp
            reg = parse_timestamp(reg)
        except Exception:
            reg = None
    registered_formatted = reg.strftime("%Y-%m-%d %H:%M UTC") if hasattr(reg, "strftime") else (str(target_user.registered) if target_user.registered else "")
    registered_relative = format_relative_time(target_user.registered)

    if is_own_profile or is_admin:
        last_login = target_user.last_login
        if last_login and last_login != "no login":
            if isinstance(last_login, str):
                try:
                    from mcritweb.db import parse_timestamp
                    last_login = parse_timestamp(last_login)
                except Exception:
                    last_login = None
            last_login_formatted = last_login.strftime("%Y-%m-%d %H:%M UTC") if hasattr(last_login, "strftime") else str(target_user.last_login)
            last_login_relative = format_relative_time(target_user.last_login)
        else:
            last_login_formatted = "Never"
            last_login_relative = "No prior login recorded"
    else:
        last_login_formatted = None
        last_login_relative = None

    if not is_own_profile and not is_admin:
        display_user = UserInfo()
        display_user.user_id = target_user.user_id
        display_user.username = target_user.username
        display_user.role = target_user.role
        display_user.registered = target_user.registered
        display_user.last_login = None
        display_user.apitoken = ""
        display_user.password = ""
    else:
        display_user = target_user

    show_developer_tab = is_own_profile
    show_ephemeral = (is_own_profile or is_admin)

    can_use_api = (target_user.role in API_ROLES) if is_own_profile else False
    legacy_token = is_legacy_apitoken(target_user.apitoken) if is_own_profile else False
    try:
        from mcritweb.views.utility import get_server_url
        server_url = get_server_url() or request.host_url.rstrip('/')
    except Exception:
        server_url = request.host_url.rstrip('/')
    api_base_url = f"{request.host_url.rstrip('/')}/api"

    # Query Activity & Job Ledger
    raw_user_jobs = []
    job_count = 0
    jobs_unavailable = False
    try:
        from mcritweb.views.client import get_client
        client = get_client()
        raw_jobs = client.getQueueData(username=target_user.username, ascending=False)
        if raw_jobs is None:
            jobs_unavailable = True
        else:
            raw_user_jobs = list(raw_jobs)
    except Exception as e:
        current_app.logger.warning("Could not fetch jobs for profile of %s: %s", target_user.username, e)
        jobs_unavailable = True

    submission_jobs = []
    analysis_jobs = []

    for job in raw_user_jobs:
        method = getattr(job, "method", "")
        if method == "addBinarySample":
            filename = None
            sha256 = None
            try:
                filename = job.filename
            except Exception:
                pass
            try:
                sha256 = job.sha256
            except Exception:
                pass
            sample_id = None
            try:
                sample_id = job.sample_id
            except Exception:
                pass
            submission_jobs.append({
                "job": job,
                "job_id": getattr(job, "job_id", ""),
                "filename": filename,
                "sha256": sha256,
                "sample_id": sample_id,
                "is_finished": getattr(job, "is_finished", False),
                "is_failed": getattr(job, "is_failed", False),
                "created_at": getattr(job, "created_at", None),
            })
        else:
            if is_own_profile or is_admin:
                analysis_jobs.append(job)

    public_submission_count = len(submission_jobs)
    indexed_sample_count = sum(1 for s in submission_jobs if s.get("is_finished") and s.get("sample_id"))

    if not (is_own_profile or is_admin):
        user_jobs = []
        job_count = public_submission_count
        finished_count = indexed_sample_count
        running_count = sum(1 for s in submission_jobs if not s.get("is_finished") and not s.get("is_failed"))
        failed_count = sum(1 for s in submission_jobs if s.get("is_failed"))
    else:
        user_jobs = raw_user_jobs
        job_count = len(analysis_jobs)
        finished_count = sum(1 for j in analysis_jobs if getattr(j, "is_finished", False))
        failed_count = sum(1 for j in analysis_jobs if getattr(j, "is_failed", False))
        running_count = sum(1 for j in analysis_jobs if not getattr(j, "is_finished", False) and not getattr(j, "is_failed", False))

    # Ephemeral Query Uploads workspace
    query_uploads = []
    if show_ephemeral:
        uploads_dir = os.path.join(current_app.instance_path, "temp", "uploads")
        if os.path.isdir(uploads_dir):
            target_job_ids = {str(getattr(j, "job_id", "")) for j in raw_user_jobs}
            from mcritweb.db import get_query_filename
            from mcritweb.views.utility import QUERY_UPLOAD_JOB_ID

            for entry in os.scandir(uploads_dir):
                if not entry.is_file():
                    continue
                job_id = entry.name
                if not QUERY_UPLOAD_JOB_ID.match(job_id):
                    continue
                if not is_admin and job_id not in target_job_ids:
                    continue
                try:
                    st = entry.stat()
                    created_dt = datetime.datetime.fromtimestamp(st.st_mtime, tz=datetime.UTC)
                    orig_filename = get_query_filename(job_id)
                    query_uploads.append({
                        "job_id": job_id,
                        "filename": orig_filename or f"query_{job_id[:8]}",
                        "size_bytes": st.st_size,
                        "size_formatted": format_file_size(st.st_size),
                        "created_dt": created_dt,
                        "created_formatted": created_dt.strftime("%Y-%m-%d %H:%M UTC"),
                        "created_relative": format_relative_time(created_dt),
                    })
                except OSError:
                    continue
            query_uploads.sort(key=lambda u: u["created_dt"], reverse=True)

    allowed_tabs = ['overview', 'submissions']
    if is_own_profile or is_admin:
        allowed_tabs.extend(['jobs', 'uploads'])
    if is_own_profile:
        allowed_tabs.append('developer')

    active_tab = request.args.get('tab', 'overview').lower()
    if active_tab not in allowed_tabs:
        active_tab = 'overview'

    return render_template(
        'profile.html',
        active_tab=active_tab,
        user_info=display_user,
        target_user=display_user,
        is_own_profile=is_own_profile,
        is_admin=is_admin,
        show_developer_tab=show_developer_tab,
        show_ephemeral=show_ephemeral,
        registered_formatted=registered_formatted,
        registered_relative=registered_relative,
        last_login_formatted=last_login_formatted,
        last_login_relative=last_login_relative,
        can_use_api=can_use_api,
        legacy_apitoken=legacy_token,
        server_url=server_url,
        api_base_url=api_base_url,
        capabilities=get_role_capabilities(),
        user_jobs=user_jobs,
        analysis_jobs=analysis_jobs,
        submission_jobs=submission_jobs,
        public_submission_count=public_submission_count,
        indexed_sample_count=indexed_sample_count,
        job_count=job_count,
        finished_count=finished_count,
        running_count=running_count,
        failed_count=failed_count,
        jobs_unavailable=jobs_unavailable,
        query_uploads=query_uploads,
    )

def admin_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('authentication.login'))
        if g.user.role != 'admin':
            abort(403)
        return view(**kwargs)
    return wrapped_view


def contributor_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('authentication.login'))
        if g.user.role != 'admin' and g.user.role != 'contributor':
            abort(403)
        return view(**kwargs)
    return wrapped_view


def visitor_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('authentication.login'))
        if g.user.role != 'admin' and g.user.role != 'contributor' and g.user.role != 'visitor':
            abort(403)
        return view(**kwargs)
    return wrapped_view


def token_required(view):
    """Authenticate an API caller by its `apitoken` header, and apply its role.

    The API is a passthrough to the backend, so a token has to carry the same
    authority the web UI grants the same person - otherwise the quickest way past a
    role check is to stop using the browser. The token's owner lands on `g.api_user`
    for the router to narrow further; `pending` gets nothing, as in the web UI.
    """
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        # requests -> {'apitoken': '{}'.format(apitoken)})
        provided_token = request.headers.get("apitoken", "")
        # check for valid token via DB
        user_id = db.get_user_by_apitoken(provided_token)
        if user_id is None:
            abort(403)
        g.api_user = UserInfo.fromDb(user_id=user_id)
        if g.api_user is None or g.api_user.role not in API_ROLES:
            abort(403)
        # a token that authenticates can still be one the deployment has retired. The
        # pre-1.5.0 (32-character) tokens were left valid by 1.5.0 and would have stayed
        # valid forever; the switch lets an operator pick the cutover moment once their
        # users have regenerated. Issue #250.
        if is_legacy_apitoken(provided_token) and not current_app.config.get("ACCEPT_LEGACY_APITOKENS", True):
            return {
                "error": "This API token has the pre-1.5.0 (32-character) form, which this "
                         "deployment no longer accepts. Regenerate the token on your user "
                         "settings page and update whatever uses it."
            }, 403
        return view(**kwargs)
    return wrapped_view


@bp.route('/logout')
@login_required
def logout():
    session.clear()
    flash('You\'re logged out now', category='success')
    return redirect(url_for('index'))
