"""Session-bound CSRF protection for cookie-authenticated requests.

Every state-changing route in this application authenticates through the session
cookie, which the browser attaches to *any* request it makes - including one a
third-party page triggered. `POST`-only (issue #84) stops a prefetch or an `<img>`
tag from firing a write, but it does not stop a form on an attacker's page from
submitting to us with the victim's cookie. This module is what stops that: a random
token minted per session, echoed by every form, and required on every unsafe method.

The public surface deliberately matches the part of `flask-wtf`'s `CSRFProtect` this
application would use - the `csrf_token()` template global, the `csrf_token` form
field, the `X-CSRFToken` header, and `exempt()`. The Flask pin that made adopting the
extension awkward was lifted in issue #27, so that swap is now unblocked (ADR-0002);
keeping the names identical makes it an import change instead of a sweep through
every template again.

What the real extension adds, and this does not: signed and time-limited tokens, a
referrer check on HTTPS, and per-response token variation. None of them are the
primary defence, which is why this is a reasonable stop-gap rather than a permanent
answer.

Token strength rests on the session cookie's signature, so it rests on `SECRET_KEY`.
See `mcritweb.secret_key`.
"""

import hmac
import secrets

from flask import Blueprint, abort, current_app, request, session

#: Form field and session key. `flask-wtf` calls this WTF_CSRF_FIELD_NAME.
FIELD_NAME = "csrf_token"

#: Accepted request headers, for callers that send JSON rather than a form body.
HEADER_NAMES = ("X-CSRFToken", "X-CSRF-Token")

#: Methods defined as safe by RFC 9110 - they must not change state, so they carry
#: no token. A route that writes on GET is a bug this cannot cover; see issue #97.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: flask-wtf's switch, spelled its way on purpose: the test suite already sets it,
#: and an instance/config.py that turns protection off must keep meaning the same
#: thing after issue #27 swaps this module for the extension.
ENABLED_KEY = "WTF_CSRF_ENABLED"


def generate_csrf():
    """Return this session's token, minting one on first use.

    Exposed to templates as `csrf_token()`, so `{{ csrf_token() }}` works the same
    way it would under flask-wtf.
    """
    if FIELD_NAME not in session:
        session[FIELD_NAME] = secrets.token_hex(32)
    return session[FIELD_NAME]


def _submitted_token():
    """Pull the token out of the headers, then the form body.

    Headers first, because reading `request.form` reads and parses the whole body, and
    this runs before any view. The upload dropzone sends its token as a header, so
    `analyze.query` can still refuse an upload over the role's cap from its declared
    length before the body is taken in.
    """
    for header in HEADER_NAMES:
        token = request.headers.get(header)
        if token:
            return token
    return request.form.get(FIELD_NAME) or None


def validate_csrf():
    """True when the request carries this session's token."""
    expected = session.get(FIELD_NAME)
    submitted = _submitted_token()
    if not expected or not submitted:
        return False
    # constant-time: the token is a secret being compared against attacker input
    return hmac.compare_digest(expected, submitted)


class CsrfProtect:
    """Rejects any unsafe-method request that does not carry the session's token."""

    def __init__(self, app=None):
        self._exempt_views = set()
        self._exempt_blueprints = set()
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        app.config.setdefault(ENABLED_KEY, True)
        app.jinja_env.globals[FIELD_NAME] = generate_csrf
        # Flask-Dropzone looks for exactly this key before it will send a token with
        # an upload, and flask-wtf registers itself under it. Both dropzones get
        # their header from here rather than from anything hand-written.
        app.extensions["csrf"] = self
        app.before_request(self._protect)

    def exempt(self, view):
        """Skip CSRF for a view function or an entire blueprint.

        Only correct for endpoints that do **not** authenticate through the session
        cookie - the `/api` blueprint authenticates by `apitoken` header, which a
        cross-site request cannot supply, so it has nothing to forge.
        """
        if isinstance(view, Blueprint):
            self._exempt_blueprints.add(view.name)
        else:
            self._exempt_views.add(f"{view.__module__}.{view.__name__}")
        return view

    def _protect(self):
        if not current_app.config[ENABLED_KEY]:
            return
        if request.method in SAFE_METHODS:
            return
        if request.endpoint is None:
            # no route matched; let the 404 happen rather than masking it as a 400
            return
        if request.blueprint in self._exempt_blueprints:
            return
        view = current_app.view_functions.get(request.endpoint)
        if view is not None and f"{view.__module__}.{view.__name__}" in self._exempt_views:
            return
        if not validate_csrf():
            abort(400, description="The CSRF token is missing or invalid.")
