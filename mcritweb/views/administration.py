import json
import re

import requests
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from mcritweb import db
from mcritweb.db import KNOWN_THEMES, ServerInfo, UserColumnSettings, UserFilters, UserInfo, generate_apitoken
from mcritweb.views.authentication import KNOWN_ROLES, admin_required, login_required, multi_user
from mcritweb.views.client import get_client
from mcritweb.views.params import parse_checkbox_post_param, parse_integer_post_param
from mcritweb.views.utility import get_mcritweb_version_from_setup, get_session_user_id

bp = Blueprint('admin', __name__, url_prefix='/admin')

@bp.route('/change_username' , methods=('GET', 'POST'))
@login_required
def change_username():
    # validate user_id:
    try:
        user_id = int(session['user_id'])
        if user_id < 1:
            raise ValueError
    except Exception:
        return redirect(url_for('index'))
    new_username = request.form['username']
    password = request.form['inputPassword1']
    error_msg = None
    user_info = UserInfo.fromDb(user_id=session['user_id'])
    if re.match(r"^(?=[a-zA-Z0-9._]{3,20}$)(?!.*[_.]{2})[^_.].*[^_.]$", new_username) is None:
        error_msg = "Username has invalid format."
    if  not check_password_hash(user_info.password, password):
        error_msg = 'Incorrect Password!'
    user_with_name = UserInfo.fromDb(username=new_username)
    if user_with_name is not None:
        error_msg = 'Username is already taken!'
    if error_msg is None:
        user_info.username = new_username
        user_info.saveToDb()
        flash('Username successfully changed', category='success')
        return redirect(url_for('index'))
    flash(error_msg, category='error')
    # settings.html needs a context this view does not assemble - let the settings
    # view build it, so the flash is the only thing this one has to carry over
    return redirect(url_for('authentication.settings'))


@bp.route('/change_password' , methods=('GET', 'POST'))
@login_required
def change_password():
    # validate user_id:
    try:
        user_id = int(session['user_id'])
        if user_id < 1:
            raise ValueError
    except Exception:
        return redirect(url_for('index'))
    new_password = request.form['inputPassword3']
    old_password = request.form['inputPassword2']
    error_msg = None
    user_info = UserInfo.fromDb(user_id=session['user_id'])
    if not check_password_hash(user_info.password, old_password):
        error_msg = 'Incorrect password!'
    if not new_password == request.form['inputPassword4']:
        error_msg = 'The entered passwords do not match!'
    if error_msg is None:
        user_info.password = generate_password_hash(new_password)
        user_info.saveToDb(withPassword=True)
        flash('Password successfully changed', category='success')
        return redirect(url_for('index'))
    flash(error_msg, category='error')
    return redirect(url_for('authentication.settings'))


@bp.route('/change_default_filter' , methods=('POST',))
@login_required
def change_default_filter():
    user_id = get_session_user_id()
    if user_id is None:
        flash('User ID was not recognized', category='error')
        return redirect(url_for('index'))
    # generic filtering on family/sample results
    filter_direct_min_score = parse_integer_post_param(request, "filter_direct_min_score")
    filter_direct_nonlib_min_score = parse_integer_post_param(request, "filter_direct_nonlib_min_score")
    filter_frequency_min_score = parse_integer_post_param(request, "filter_frequency_min_score")
    filter_frequency_nonlib_min_score = parse_integer_post_param(request, "filter_frequency_nonlib_min_score")
    filter_unique_only = parse_checkbox_post_param(request, "filter_unique_only")
    filter_exclude_own_family = parse_checkbox_post_param(request, "filter_exclude_own_family")
    # generic filtering of function results
    filter_function_min_score = parse_integer_post_param(request, "filter_function_min_score")
    filter_function_max_score = parse_integer_post_param(request, "filter_function_max_score")
    filter_max_num_families = parse_integer_post_param(request, "filter_max_num_families")
    filter_exclude_library = parse_checkbox_post_param(request, "filter_exclude_library")
    filter_exclude_pic = parse_checkbox_post_param(request, "filter_exclude_pic")
    filter_values = {
        "filter_direct_min_score": 0 if filter_direct_min_score is None else filter_direct_min_score,
        "filter_direct_nonlib_min_score": 0 if filter_direct_nonlib_min_score is None else filter_direct_nonlib_min_score,
        "filter_frequency_min_score": 0 if filter_frequency_min_score is None else filter_frequency_min_score,
        "filter_frequency_nonlib_min_score": 0 if filter_frequency_nonlib_min_score is None else filter_frequency_nonlib_min_score,
        "filter_unique_only": filter_unique_only,
        "filter_exclude_own_family": filter_exclude_own_family,
        "filter_function_min_score": 0 if filter_function_min_score is None else filter_function_min_score,
        "filter_function_max_score": 100 if filter_function_max_score is None else filter_function_max_score,
        "filter_max_num_families": 0 if filter_max_num_families is None else filter_max_num_families,
        "filter_exclude_library": filter_exclude_library,
        "filter_exclude_pic": filter_exclude_pic,
    }
    user_filters = UserFilters.fromDict(user_id, filter_values)
    user_filters.saveToDb()
    flash('Default filters successfully changed', category='success')
    return redirect(url_for('authentication.settings'))

@bp.route('/change_theme' , methods=('POST',))
@login_required
def change_theme():
    user_id = get_session_user_id()
    if user_id is None:
        flash('User ID was not recognized', category='error')
        return redirect(url_for('index'))
    theme = request.form.get('theme')
    if theme not in KNOWN_THEMES:
        flash('Unknown theme requested', category='error')
        return redirect(url_for('authentication.settings'))
    user_info = UserInfo.fromDb(user_id=user_id)
    user_info.theme = theme
    user_info.saveToDb()
    flash(f'Theme successfully changed to {theme}', category='success')
    return redirect(url_for('authentication.settings'))


@bp.route('/change_column_settings' , methods=('GET', 'POST'))
@login_required
def change_column_settings():

    user_id = get_session_user_id()
    if user_id is None:
        flash('User ID was not recognized', category='error')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        try:
            # Read JSON data from form field
            column_settings_json = request.form.get('column_settings')
            if column_settings_json:
                json_data = json.loads(column_settings_json)
                # Create column_settings dict in the expected format
                column_settings_dict = {'column_settings': json_data}
                user_column_settings = UserColumnSettings.fromDict(user_id, column_settings_dict)
                user_column_settings.saveToDb()
                flash('Default column settings successfully changed', category='success')
            else:
                flash('No column settings data received', category='error')

        except Exception as e:
            flash(f'Error processing column settings {str(e)}.', category='error')

    # For GET request, redirect to the sortable config page
    return redirect(url_for('authentication.settings'))

@bp.route('/reset_column_settings', methods=('POST',))
@login_required
def reset_column_settings():
    user_id = get_session_user_id()
    if user_id is None:
        flash('User ID was not recognized', category='error')
        return redirect(url_for('index'))
    
    try:
        # Reset to default settings by creating a UserColumnSettings with defaults and saving
        user_column_settings = UserColumnSettings(user_id)
        user_column_settings.saveToDb()
        flash('Column settings reset to default successfully!', category='success')
    except Exception as e:
        flash(f'Error resetting column settings: {str(e)}', category='error')
    
    return redirect(url_for('authentication.settings'))

@bp.route('/regenerate_apitoken', methods=('POST',))
@login_required
def regenerate_apitoken():
    """Issue the caller a new API token, replacing the one they have.

    Only ever touches the caller's own row - the user id comes from the session, not
    from the request - so this needs no more than a session. Until now a token could
    not be replaced at all: deleting the account was the only way to retire one, which
    is not a thing you can ask of someone whose token has leaked. See issue #100.
    """
    user_id = get_session_user_id()
    if user_id is None:
        flash('User ID was not recognized', category='error')
        return redirect(url_for('index'))
    user_info = UserInfo.fromDb(user_id=user_id)
    if user_info is None:
        flash('User ID was not recognized', category='error')
        return redirect(url_for('index'))
    user_info.apitoken = generate_apitoken()
    user_info.saveToDb()
    flash('A new API token was generated. Anything using the old one has to be updated.', category='success')
    return redirect(url_for('authentication.settings'))

@bp.route('/users/')
@bp.route('/users/<tab>')
@admin_required
@multi_user
def users(tab = None):
    g.all_users = get_users()
    if tab is None:
        return render_template("users.html", active='all')    
    return render_template("users.html", active=tab)


def get_users():
    user_infos = db.get_all_user_info()
    return user_infos


@bp.route('/change_user_role/<int:user_id>/<role>/<tab>', methods=('POST',))
@admin_required
def change_user_role(user_id, role, tab):
    # root user is always admin
    if user_id == 1:
        return redirect(url_for('admin.users', tab=tab))
    # an unrecognised role is written straight into the column and then fails every
    # decorator, leaving an account that can reach nothing for no visible reason
    if role not in KNOWN_ROLES:
        flash(f'"{role}" is not a role.', category='error')
        return redirect(url_for('admin.users', tab=tab))
    # others can be changed - but the account may have been deleted since this page
    # was rendered, and fromDb answers None rather than raising. See issue #95.
    user_info = UserInfo.fromDb(user_id=user_id)
    if user_info is None:
        flash('That user no longer exists.', category='error')
        return redirect(url_for('admin.users', tab=tab))
    user_info.role = role
    user_info.saveToDb()
    return redirect(url_for('admin.users', tab=tab))


@bp.route('/delete_user/<int:user_id>', methods=('POST',))
@bp.route('/delete_user/<int:user_id>/<tab>', methods=('POST',))
@admin_required
def delete_user(user_id, tab = None):
    # root user is not deletable
    if user_id == 1:
        return redirect(url_for('admin.users', tab=tab))
    # others can be deleted, along with everything keyed to them
    database = db.get_db()
    database.execute("DELETE FROM user_filters WHERE user_id = ?;", (user_id,))
    database.execute("DELETE FROM user_column_settings WHERE user_id = ?;", (user_id,))
    database.execute("DELETE FROM user WHERE id = ?;", (user_id,))
    database.commit()
    return redirect(url_for('admin.users', tab=tab))


def backend_version(client):
    """The MCRIT backend's version as a string, or "unknown".

    `McritClient.getVersion()` answers with the dict mcrit's own
    `MinHashIndex.getVersion` builds - `{"version": "1.4.3"}` - not a bare string, so
    rendering it directly put `{'version': '1.4.3'}` on the page. It answers None when
    the backend could not be reached or refused, which is not a version either.
    """
    try:
        version = client.getVersion()
    except requests.RequestException:
        # An unreachable backend is exactly when an admin needs this page: it carries the
        # form for correcting the server URL, and `backend_unavailable.html` sends them
        # here to do it. Letting the transport failure out turns that instruction into a
        # dead end - the one page that can fix the outage is the one the outage breaks.
        # Only the transport family is caught; anything else here is our own bug.
        return "unknown"
    if isinstance(version, dict):
        version = version.get("version")
    return version if isinstance(version, str) and version else "unknown"


@bp.route('/server')
@admin_required
def server():
    server_info = ServerInfo.fromDb()
    operation_mode_str = "Multi-User" if server_info.operation_mode == "multi" else "Single-User"
    running_server_version = get_mcritweb_version_from_setup()
    client = get_client()
    mcrit_version = backend_version(client)
    return render_template('admin_server.html', operation_mode=operation_mode_str, server_info=server_info, running_version=running_server_version, mcrit_version=mcrit_version)


@bp.route('/change_server' , methods=('POST',))
@admin_required
def change_server():
    server_info = ServerInfo.fromDb()
    new_url = request.form.get('mcrit_server_url', '')
    new_token = request.form.get('mcrit_server_token', '')
    if server_info.url != new_url or server_info.server_token != new_token:
        server_info.url = new_url
        server_info.server_token = new_token
        server_info.saveToDb()
        flash('Server information successfully changed', category='success')
    else:
        flash('No information needed change', category='success')
    operation_mode_str = "Multi-User" if server_info.operation_mode == "multi" else "Single-User"
    running_server_version = get_mcritweb_version_from_setup()
    client = get_client()
    mcrit_version = backend_version(client)
    return render_template('admin_server.html', operation_mode=operation_mode_str, server_info=server_info, running_version=running_server_version, mcrit_version=mcrit_version)


@bp.route('/reset_server' , methods=('POST',))
@admin_required
def reset_server():
    reset_confirmation = request.form.get('reset_server', '')
    if reset_confirmation != "RESET":
        flash('The reset was not confirmed, so nothing was changed.', category='error')
        return redirect(url_for('admin.server'))
    client = get_client()
    client.respawn()
    from mcritweb.views.utility import ensure_local_data_paths
    ensure_local_data_paths(current_app, clear_data=True)
    # TODO also clean all locally cached data.
    flash('A reset of MCRIT was successfully performed.', category='success')
    return redirect(url_for('index'))


@bp.route('/schedule_rebuild_index' , methods=('POST',))
@admin_required
def schedule_rebuild_index():
    client = get_client()
    job_id = client.rebuildIndex()
    flash('A job for rebuilding the MinHash Index has been scheduled.', category='success')
    return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3))


@bp.route('/schedule_recalc_pichashes' , methods=('POST',))
@admin_required
def schedule_recalc_pichashes():
    client = get_client()
    job_id = client.recalculatePicHashes()
    flash('A job for recalculating all PicHashes has been scheduled.', category='success')
    return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3))


@bp.route('/schedule_recalc_minhashes' , methods=('POST',))
@admin_required
def schedule_recalc_minhashes():
    client = get_client()
    job_id = client.recalculateMinHashes()
    flash('A job for recalculating and indexing all MinHashes has been scheduled.', category='success')
    return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3))
