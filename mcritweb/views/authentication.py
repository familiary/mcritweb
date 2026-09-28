import functools
import os
import re
import secrets
import sqlite3
import uuid

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from mcritweb import db
from mcritweb.db import DEFAULT_THEME, ServerInfo, UserColumnSettings, UserFilters, UserInfo, generate_apitoken, utc_now
from mcritweb.views.utility import get_session_user_id

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
            "throttled %s after %d recent failures, %d of them against %r",
            remote_addr, db.count_recent_login_failures(remote_addr), against, username)
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
    if not g.first_user:
        server_info = ServerInfo.fromDb()
        g.operation_mode = server_info.operation_mode


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
    server_info = ServerInfo.fromDb()
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
        if _throttled(username):
            error = TOO_MANY_ATTEMPTS
        elif not username:
            error = 'Username is required.'
        elif re.match(r"^(?=[a-zA-Z0-9._]{3,20}$)(?!.*[_.]{2})[^_.].*[^_.]$", username) is None:
            error = "Username has wrong format. Must be 3-20 characters, alphanumeric with dots and underscores allowed, but cannot start or end with dots/underscores, nor contain two of them in a row."
        elif username.lower() in ["guest", "mcritweb", "mcrit", "admin", "root", "system", "test", "demo"]:
            error = "Username is reserved."
        elif not password:
            error = 'Password is required.'
        elif not password == request.form['inputPassword2']:
            error = 'The passwords do not match. No new user was created.'
        elif is_registration_token_required and server_info.registration_token != provided_registration_token:
            error = 'Invalid registration token provided. No new user was created.'
            db.record_failed_login(request.remote_addr, username)
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
                try:
                    server_info.saveToDb()
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
        if user_info is None:
            _spend_a_password_check(password)
            error = LOGIN_FAILED
        elif not check_password_hash(user_info.password, password):
            error = LOGIN_FAILED
        if error is None:
            session.clear()
            session['user_id'] = user_info.user_id
            user_info.last_login = utc_now()
            rehashed = _rehash_if_stale(user_info, password)
            user_info.saveToDb(withPassword=rehashed)
            db.clear_login_failures(request.remote_addr)
            return redirect(url_for('index'))
        db.record_failed_login(request.remote_addr, username)
        flash(error, category='error')
    return render_template('login.html')


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get('user_id')
    if user_id is None:
        g.user = None
    else:
        user_info = UserInfo.fromDb(user_id=user_id)
        g.user = user_info
    # the palette every template renders against. Resolved once here rather than read
    # off g.user in base.html, because the pages a logged-out caller sees - login,
    # register, the index - still have to name a theme (#70).
    g.theme = g.user.theme if g.user is not None else DEFAULT_THEME


def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('authentication.login'))
        return view(**kwargs)
    return wrapped_view


@bp.route('/settings')
@login_required
def settings():
    user_id = get_session_user_id()
    if user_id is None:
        return redirect(url_for('index'))
    user_info = UserInfo.fromDb(user_id=user_id)
    user_filters = UserFilters.fromDb(user_id)
    user_column_settings = UserColumnSettings.fromDb(user_id)
    # if we don't have them yet, create them
    if user_filters is None:
        user_filters = UserFilters.fromDict(user_id, {})
        user_filters.saveToDb()
    if user_column_settings is None:
        user_column_settings = UserColumnSettings.fromDict(user_id, {})
        user_column_settings.saveToDb()
    return render_template('settings.html', user_info=user_info, user_filters=user_filters, user_column_settings=user_column_settings.toUserColumnSettings(), can_use_api=user_info is not None and user_info.role in API_ROLES)

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
        return view(**kwargs)
    return wrapped_view


@bp.route('/logout')
@login_required
def logout():
    session.clear()
    flash('You\'re logged out now', category='success')
    return redirect(url_for('index'))
