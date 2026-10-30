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
#: set by admin.regenerate_apitoken, so that the settings page shows the new token at once
SHOW_APITOKEN_NOW = 'show_apitoken_now'


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
    # a refused registration comes back with what was typed, apart from the passwords and the
    # server token, and with its message beside the field it concerns
    entered = {}
    field_error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['inputPassword1']
        provided_registration_token = ""
        if 'registrationToken' in request.form:
            provided_registration_token = request.form['registrationToken']
        error = None
        error_field = None
        # the guessable secret on this route is the registration token, so it is metered
        # by the same counter as /login. Ordinary validation slips - a short username, a
        # password typed twice differently - deliberately do NOT record an attempt: they
        # are not guesses, and counting them would lock people out of their own signup.
        if _throttled(registration_token_attempt(username)):
            error = TOO_MANY_ATTEMPTS
        elif not username:
            error, error_field = 'Username is required.', 'username'
        elif not db.is_well_formed_username(username):
            error, error_field = "Username has wrong format. Must be 3-20 characters, alphanumeric with dots and underscores allowed, but cannot start or end with dots/underscores, nor contain two of them in a row.", 'username'
        elif db.is_reserved_username(username):
            error, error_field = "Username is reserved.", 'username'
        elif not password:
            error, error_field = 'Password is required.', 'inputPassword1'
        elif not password == request.form['inputPassword2']:
            error, error_field = 'The passwords do not match. No new user was created.', 'inputPassword2'
        elif is_registration_token_required and server_info.registration_token != provided_registration_token:
            error, error_field = 'Invalid registration token provided. No new user was created.', 'registrationToken'
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
        if error_field:
            field_error = {"fields": [error_field], "message": error}
        entered = {name: request.form[name] for name in ("username", "registrationToken", "url", "setRegistrationToken", "operationMode")
                   if name in request.form}
    proposed_registration_token = ""
    if g.first_user:
        # the token the form carried, which its admin may have copied already, rather than a new one
        proposed_registration_token = entered.get("setRegistrationToken", str(uuid.uuid4()))
    query_token = request.args.get('token')
    if query_token is None or not re.match(r"^[a-zA-Z0-9._\-]{3,36}$", query_token):
        query_token = ""
    query_token = entered.get("registrationToken", query_token)
    default_server = entered.get("url", os.environ.get('MCRIT_DEFAULT_SERVER', "http://127.0.0.1:8000"))
    return render_template("register.html", default_mcrit_server=default_server, is_registration_token_required=is_registration_token_required, proposed_registration_token=proposed_registration_token, query_token=query_token,
                           entered=entered, field_error=field_error)


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
            return render_template('login.html', username=username)

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
        # one message for both fields, as it says nothing about which of them was wrong; the
        # name stays, the password field is emptied and focused
        return render_template('login.html', username=username,
                               field_error={"fields": ["username", "current-password"], "message": error})
    return render_template('login.html')


#: The session key holding `password_mark()` of the account as the session logged in.
PASSWORD_MARK = 'password_mark'


def password_mark(user_info):
    """A digest of the account's stored password hash and session generation, keyed
    with SECRET_KEY.

    The session is a signed cookie, not a server-side record, so ending a session cannot
    reach a copy of it. What can be checked is whether the account is still as it was
    when the session logged in: the session carries this mark, and a password change, or
    a logout moving the session generation on, makes every mark issued before it stop
    matching. Keyed, because the cookie is signed but readable, and an unkeyed digest of
    the hash would be something to test password guesses against.
    """
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    marked = f"{user_info.session_generation}:{user_info.password}"
    return hmac.new(key, marked.encode(), hashlib.sha256).hexdigest()


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get('user_id')
    if user_id is None:
        g.user = None
    else:
        user_info = UserInfo.fromDb(user_id=user_id)
        mark = session.get(PASSWORD_MARK)
        # a session from before marks existed has none. It cannot be told apart from a
        # copy that outlived a password change or a logout, so it ends the same way, and
        # its user logs in once more
        if user_info is not None and not (isinstance(mark, str) and hmac.compare_digest(mark, password_mark(user_info))):
            session.clear()
            user_info = None
        g.user = user_info
    # the colour theme every page renders with; a logged-out page has the default (#70)
    g.theme = g.user.theme if g.user is not None else db.DEFAULT_THEME


@bp.after_app_request
def mark_the_session_with_the_current_password(response):
    """Give a session that got this far the mark of the account as it is now.

    A request that changed the account's own password - /admin/change_password - keeps
    its own session this way, while every other session of the account still carries the
    old mark and ends on its next request.
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


#: The session key carrying a refused settings form to the settings page its view redirects
#: to: the tab it is on, the fields the message concerns, the message, and what was typed.
SETTINGS_FORM_ERROR = 'settings_form_error'

#: Longer than any value a settings form field takes, so a value worth typing again is kept
#: whole, while the session cookie is not made to carry whatever was posted.
KEPT_VALUE_LENGTH = 64


def refuse_settings_form(tab, fields, message, values=None):
    """Answer a refused settings form. The message is flashed, as it always was, and also
    shown beside the fields it concerns on the settings page the redirect leads to, which
    opens on the form's tab with what was typed - never a password - still in it."""
    flash(message, category='error')
    kept = {name: value for name, value in (values or {}).items() if len(value) <= KEPT_VALUE_LENGTH}
    session[SETTINGS_FORM_ERROR] = {"tab": tab, "fields": list(fields), "message": message, "typed": kept}
    # settings.html needs a context these views do not assemble - the settings view builds it
    return redirect(url_for('authentication.settings'))


@bp.route('/settings')
@login_required
def settings():
    # login_required has settled that g.user holds this session's row
    user_info = g.user
    form_error = session.pop(SETTINGS_FORM_ERROR, None)
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
    # the token is not printed: the page fetches it from admin.show_apitoken when asked,
    # and at once, this one time, after a regenerate
    show_apitoken_now = session.pop(SHOW_APITOKEN_NOW, False)
    if form_error and form_error["tab"] == "default-filter":
        # a refused default filter shows what was typed, not what is stored
        user_filters = UserFilters.fromDict(user_id, form_error["typed"])
    return render_template('settings.html', user_info=user_info, user_filters=user_filters, user_column_settings=user_column_settings.toUserColumnSettings(), can_use_api=user_info.role in API_ROLES, legacy_apitoken=is_legacy_apitoken(user_info.apitoken), only_admin=db.is_only_admin(user_info), show_apitoken_now=show_apitoken_now,
                           form_error=form_error, open_tab=form_error["tab"] if form_error else "info")

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


@bp.route('/logout', methods=['GET', 'POST'])
@login_required
def logout():
    if request.method != 'POST':
        # ending every session of the account is a write, so a link - one on another
        # site, followed with the Lax cookie, among them - only gets to ask
        return render_template('logout.html', can_use_api=g.user.role in API_ROLES)
    # clearing the cookie only ends this browser's copy of the session, and a copy taken
    # elsewhere would stay valid for PERMANENT_SESSION_LIFETIME. Moving the generation on
    # ends every session of the account, this one included
    g.user.endSessions()
    # the API token is left as it is, since replacing it would break every client using
    # it on each logout; whoever may have copied it is pointed at /settings instead
    can_use_api = g.user.role in API_ROLES
    # and nobody is logged in for the rest of this request, so the cleared session is
    # not given a mark on its way out
    g.user = None
    session.clear()
    if can_use_api:
        flash('You\'re logged out now, in every browser. Your API token still works: if it may have been copied, issue a new one on Settings.', category='success')
    else:
        flash('You\'re logged out now', category='success')
    return redirect(url_for('index'))


#: What the root account is told if it asks to be deleted. Its settings page offers no
#: form, so only a request made by hand gets here.
ROOT_ACCOUNT_KEPT = ('This account cannot be deleted: it is the first one registered here, '
                     'which stays an administrator so that the instance always has one.')

#: And if the root account is gone or demoted and this is the one admin left.
LAST_ADMIN_KEPT = ('This account cannot be deleted: it is the only administrator, and the '
                   'instance needs one. Make another account an administrator first.')


@bp.route('/delete_account', methods=('POST',))
@login_required
def delete_account():
    """Delete the caller's own account, as an admin deleting it from the users page would.

    The password is asked for again, so that a session left open on a shared machine, or
    a copy of its cookie, cannot end the account on its own. That makes the check a
    password guess like one at /login, and it is metered by the same throttle: otherwise
    whoever holds a stolen session could guess at the password here without limit.
    """
    user_info = g.user
    # the last admin: every other admin can go and leave the root account in place, so
    # it is the only one whose deletion could leave the server without an admin. Refused
    # before the password check, so that asking costs no guess
    if user_info.is_root:
        flash(ROOT_ACCOUNT_KEPT, category='error')
        return redirect(url_for('authentication.settings'))
    # the root account is only a convention about one row: if it was demoted or removed
    # by hand, the admin left must stay too
    if db.is_only_admin(user_info):
        flash(LAST_ADMIN_KEPT, category='error')
        return redirect(url_for('authentication.settings'))
    if _throttled(user_info.username):
        flash(TOO_MANY_ATTEMPTS, category='error')
        return redirect(url_for('authentication.settings'))
    password = request.form.get('password', '')
    if not password or not check_password_hash(user_info.password, password):
        db.record_failed_login(request.remote_addr, user_info.username)
        flash('Incorrect password. Your account was not deleted.', category='error')
        return redirect(url_for('authentication.settings'))
    db.delete_user(user_info.user_id)
    db.clear_login_failures(request.remote_addr, user_info.username)
    # with the row gone, every other session of the account, a copied cookie included,
    # finds nobody to sign in on its next request. This one ends now, as a logout's does
    g.user = None
    session.clear()
    flash('Your account was deleted, together with its settings and its API token.', category='success')
    return redirect(url_for('authentication.login'))
