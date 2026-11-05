import json

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from mcritweb import db
from mcritweb.db import UserColumnSettings, UserFilters, UserInfo, generate_apitoken, is_legacy_apitoken
from mcritweb.views.account_actions import AccountActionError
from mcritweb.views.account_actions import change_password as set_password
from mcritweb.views.account_actions import rename as rename_account
from mcritweb.views.admin_actions import change_role, clear_reset_state, delete_account, rotate_legacy_tokens, update_connection
from mcritweb.views.authentication import (
    KNOWN_ROLES,
    admin_required,
    login_required,
    multi_user,
)
from mcritweb.views.client import backend_version, get_client
from mcritweb.views.params import parse_checkbox_post_param, parse_integer_post_param
from mcritweb.views.utility import get_session_user_id, mcrit_server_required

bp = Blueprint('admin', __name__, url_prefix='/admin')


def _password_checks_out(user_info, password):
    """Check the signed-in caller's current password, metered as a login is.

    A settings form that asks for the password is a password guess like one at /login:
    whoever holds a stolen or copied session could otherwise try password after password
    here, unthrottled, and learn one that may open other accounts too. So a wrong one is
    recorded against the same per-address budget, and a right one clears this account's
    failures, as a successful login does.
    """
    if check_password_hash(user_info.password, password):
        db.clear_login_failures(request.remote_addr, user_info.username)
        return True
    db.record_failed_login(request.remote_addr, user_info.username)
    return False

@bp.route('/change_username' , methods=('POST',))
@login_required
def change_username():
    new_username = request.form['username']
    password = request.form['inputPassword1']
    # g.user is the caller's row, read fresh for this request by load_logged_in_user
    user_info = g.user
    error_msg = None
    try:
        rename_account(user_info, new_username, password)
    except AccountActionError as error:
        error_msg = str(error)
    if error_msg is None:
        flash('Username successfully changed', category='success')
        return redirect(url_for('index'))
    flash(error_msg, category='error')
    # settings.html needs a context this view does not assemble - let the settings
    # view build it, so the flash is the only thing this one has to carry over
    return redirect(url_for('authentication.settings'))


@bp.route('/change_password' , methods=('POST',))
@login_required
def change_password():
    new_password = request.form['inputPassword3']
    old_password = request.form['inputPassword2']
    user_info = g.user
    error_msg = None
    try:
        set_password(user_info, old_password, new_password, request.form['inputPassword4'])
    except AccountActionError as error:
        error_msg = str(error)
    if error_msg is None:
        flash('Password successfully changed. Your API token was replaced too; anything using the old one has to be updated.', category='success')
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
    user_info = g.user
    user_info.apitoken = generate_apitoken()
    user_info.saveToDb()
    flash('A new API token was generated. Anything using the old one has to be updated.', category='success')
    return redirect(url_for('authentication.settings'))

@bp.route('/regenerate_legacy_apitokens', methods=('POST',))
@admin_required
def regenerate_legacy_apitokens():
    """Replace every pre-1.5.0 (32-character) API token with a fresh one.

    The pre-1.5.0 tokens stay valid until their owner happens to regenerate, and
    nothing used to move them on - issue #250. This is the admin-side bulk answer for
    the cutover moment: it rotates all of them at once. That breaks whatever uses the
    tokens, which is why the button that leads here asks for confirmation first.
    """
    rotated = rotate_legacy_tokens()
    if rotated:
        flash(f'{rotated} pre-1.5.0 API token(s) were regenerated. Anything using them has to be updated.', category='success')
    else:
        flash('No account holds a pre-1.5.0 API token.', category='info')
    return redirect(url_for('admin.users'))

@bp.route('/users/')
@bp.route('/users/<tab>')
@admin_required
@multi_user
def users(tab = None):
    g.all_users = get_users()
    g.users_by_role = group_users_by_role(g.all_users)
    # pre-1.5.0 (32-character) API tokens stay valid until their owner regenerates, and
    # nothing used to tell anyone about them - issue #250. The template marks the rows
    # and offers a regenerate-all action for the ones nobody has moved on yet.
    legacy_token_user_ids = {user_info.user_id for user_info in g.all_users if is_legacy_apitoken(user_info.apitoken)}
    if tab is None:
        return render_template("users.html", active='all', legacy_token_user_ids=legacy_token_user_ids)
    return render_template("users.html", active=tab, legacy_token_user_ids=legacy_token_user_ids)


def get_users():
    user_infos = db.get_all_user_info()
    return user_infos


def group_users_by_role(user_infos):
    """One list per role in KNOWN_ROLES, each in the order given, for the role tabs of
    users.html. A user with any other role is only listed under "all" (#95)."""
    grouped = {role: [] for role in KNOWN_ROLES}
    for user_info in user_infos:
        if user_info.role in grouped:
            grouped[user_info.role].append(user_info)
    return grouped


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
    change_role(user_id, role)
    return redirect(url_for('admin.users', tab=tab))


@bp.route('/delete_user/<int:user_id>', methods=('POST',))
@bp.route('/delete_user/<int:user_id>/<tab>', methods=('POST',))
@admin_required
def delete_user(user_id, tab = None):
    # root user is not deletable
    if user_id == 1:
        return redirect(url_for('admin.users', tab=tab))
    # others can be deleted, along with everything keyed to them
    delete_account(user_id)
    return redirect(url_for('admin.users', tab=tab))


@bp.route('/server')
@admin_required
def server():
    server_info = db.get_server_info()
    operation_mode_str = "Multi-User" if server_info.operation_mode == "multi" else "Single-User"
    running_server_version = current_app.config['MCRITWEB_VERSION']
    client = get_client()
    mcrit_version = backend_version(client)
    return render_template('admin_server.html', operation_mode=operation_mode_str, server_info=server_info, running_version=running_server_version, mcrit_version=mcrit_version)


@bp.route('/change_server' , methods=('POST',))
@admin_required
def change_server():
    new_url = request.form.get('mcrit_server_url', '')
    new_token = request.form.get('mcrit_server_token', '')
    if update_connection(new_url, new_token):
        flash('Server information successfully changed', category='success')
    else:
        flash('No information needed change', category='success')
    server_info = db.get_server_info()
    operation_mode_str = "Multi-User" if server_info.operation_mode == "multi" else "Single-User"
    running_server_version = current_app.config['MCRITWEB_VERSION']
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
    clear_reset_state()
    flash('A reset of MCRIT was successfully performed.', category='success')
    return redirect(url_for('index'))


def _redirect_to_scheduled_job(job_id, scheduled_message):
    """Send the admin to the job they just scheduled, or say it was not scheduled.

    A backend that refuses the request - out of workers, or simply older than the route -
    answers None through McritClient, and url_for then raises a BuildError for the missing
    job_id: a 500 on the admin page, where a message belongs. One that cannot be reached at
    all raises instead, which `mcrit_server_required` on each route turns into its usual
    message before the request is made. testRoutePolicy caught
    it on the three routes below that mcrit 1.9.0 made possible; the three older ones had the
    same hole and only ever avoided it because the backend answered.
    """
    if job_id is None:
        flash('The backend did not accept the job, so nothing was scheduled.', category='error')
        return redirect(url_for('admin.server'))
    flash(scheduled_message, category='success')
    return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3))


@bp.route('/schedule_rebuild_index' , methods=('POST',))
@admin_required
@mcrit_server_required
def schedule_rebuild_index():
    client = get_client()
    return _redirect_to_scheduled_job(client.rebuildIndex(), 'A job for rebuilding the MinHash Index has been scheduled.')


@bp.route('/schedule_recalc_pichashes' , methods=('POST',))
@admin_required
@mcrit_server_required
def schedule_recalc_pichashes():
    client = get_client()
    return _redirect_to_scheduled_job(client.recalculatePicHashes(), 'A job for recalculating all PicHashes has been scheduled.')


@bp.route('/schedule_recalc_minhashes' , methods=('POST',))
@admin_required
@mcrit_server_required
def schedule_recalc_minhashes():
    client = get_client()
    return _redirect_to_scheduled_job(client.recalculateMinHashes(), 'A job for recalculating and indexing all MinHashes has been scheduled.')


# The three below arrived with mcrit 1.9.0. Until now they could only be started against the
# backend directly, so an operator running MCRITweb had no way to repair a corpus from the
# interface that told them it needed repairing - /status reports stale minhashes, the family
# counters and the picblockhash index, and every one of the repairs was out of reach.
@bp.route('/schedule_repair_minhashes' , methods=('POST',))
@admin_required
@mcrit_server_required
def schedule_repair_minhashes():
    client = get_client()
    return _redirect_to_scheduled_job(client.repairMinHashes(), 'A job for repairing the MinHashes of samples hashed by an older smda has been scheduled.')


@bp.route('/schedule_recompute_family_stats' , methods=('POST',))
@admin_required
@mcrit_server_required
def schedule_recompute_family_stats():
    client = get_client()
    return _redirect_to_scheduled_job(client.recomputeFamilyStats(), 'A job for recomputing the family statistics has been scheduled.')


@bp.route('/schedule_rebuild_picblockhash_index' , methods=('POST',))
@admin_required
@mcrit_server_required
def schedule_rebuild_picblockhash_index():
    client = get_client()
    return _redirect_to_scheduled_job(client.rebuildPicBlockHashIndex(), 'A job for rebuilding the PicBlockHash index has been scheduled.')
