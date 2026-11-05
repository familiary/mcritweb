"""Account changes shared by the settings forms and token API."""

import sqlite3

from werkzeug.security import generate_password_hash

from mcritweb import db
from mcritweb.db import UserInfo, generate_apitoken


class AccountActionError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def check_current_password(user, password, error_message='Incorrect Password!'):
    # Imports are local because administration's forms use the actions in this module.
    from mcritweb.views.administration import _password_checks_out
    from mcritweb.views.authentication import TOO_MANY_ATTEMPTS, _throttled
    if _throttled(user.username):
        raise AccountActionError(TOO_MANY_ATTEMPTS, 429)
    if not _password_checks_out(user, password):
        raise AccountActionError(error_message, 403)


def rename(user, username, password):
    if not db.is_well_formed_username(username):
        raise AccountActionError('Username has invalid format.')
    if db.is_reserved_username(username):
        raise AccountActionError('Username is reserved.')
    check_current_password(user, password)
    if UserInfo.fromDb(username=username) is not None:
        raise AccountActionError('Username is already taken!', 409)
    old_username = user.username
    user.username = username
    try:
        user.saveToDb()
    except sqlite3.IntegrityError:
        user.username = old_username
        raise AccountActionError('Username is already taken!', 409) from None


def change_password(user, old_password, password, confirmation):
    if not password:
        raise AccountActionError('Password is required.')
    if password != confirmation:
        raise AccountActionError('The entered passwords do not match!')
    check_current_password(user, old_password, 'Incorrect password!')
    user.password = generate_password_hash(password)
    user.apitoken = generate_apitoken()
    user.saveToDb()
