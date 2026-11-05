"""Administration operations shared by the browser and token API."""

from flask import current_app

from mcritweb import db
from mcritweb.db import UserInfo, generate_apitoken, is_legacy_apitoken
from mcritweb.views.authentication import KNOWN_ROLES
from mcritweb.views.memo import clear_app_memos
from mcritweb.views.utility import ensure_local_data_paths, forget_server_probe


def change_role(user_id, role):
    if user_id == 1:
        raise PermissionError("The root account must remain an administrator.")
    if role not in KNOWN_ROLES:
        raise ValueError("Unknown role.")
    user = UserInfo.fromDb(user_id=user_id)
    if user is None:
        raise LookupError("That user no longer exists.")
    user.role = role
    user.saveToDb()


def delete_account(user_id):
    if user_id == 1:
        raise PermissionError("The root account cannot be deleted.")
    database = db.get_db()
    with database:
        database.execute("DELETE FROM user_filters WHERE user_id = ?;", (user_id,))
        database.execute("DELETE FROM user_column_settings WHERE user_id = ?;", (user_id,))
        database.execute("DELETE FROM user WHERE id = ?;", (user_id,))


def rotate_legacy_tokens():
    database = db.get_db()
    rotated = 0
    with database:
        for user in db.get_all_user_info():
            if is_legacy_apitoken(user.apitoken):
                # Do not overwrite a token its owner rotated since the read.
                rotated += database.execute(
                    "UPDATE user SET apitoken = ? WHERE id = ? AND apitoken = ?;",
                    (generate_apitoken(), user.user_id, user.apitoken),
                ).rowcount
    return rotated


def update_connection(url, token):
    server = db.get_server_info()
    changed = server.url != url or server.server_token != token
    if changed:
        server.url = url
        server.server_token = token
        server.saveToDb()
        forget_server_probe()
    return changed


def clear_reset_state():
    forget_server_probe()
    ensure_local_data_paths(current_app, clear_data=True)
    # A reset restarts the backend's id counters.
    clear_app_memos(current_app)
