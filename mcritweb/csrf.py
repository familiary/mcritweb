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

What the real extension adds, and this does not: signed and time-limited tokens and a
referrer check on HTTPS. Neither is the primary defence, which is why this is a
reasonable stop-gap rather than a permanent answer.

The token a page carries is masked afresh every time it is rendered, the way Rails masks
its own: a random pad, then the session token XORed with it. Pages are gzipped
(`mcritweb.compression`), and a secret that is identical in every response, compressed
next to input the page reflects, is what a BREACH attack reads out of a response's length.
A masked token is never the same twice, so there is nothing to read.

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
    """Return this session's token, masked afresh, minting the token on first use.

    Exposed to templates as `csrf_token()`, so `{{ csrf_token() }}` works the same
    way it would under flask-wtf.
    """
    if FIELD_NAME not in session:
        session[FIELD_NAME] = secrets.token_hex(32)
    return mask(session[FIELD_NAME])


def mask(token):
    """`token` hidden behind a fresh random pad, as hex: the pad, then the token XOR the pad."""
    raw = token.encode()
    pad = secrets.token_bytes(len(raw))
    return (pad + bytes(a ^ b for a, b in zip(pad, raw))).hex()


def unmask(submitted, length):
    """The token a masked submission of a `length`-byte token carries, as bytes.

    Anything not shaped like one is returned as it came, so the bare session token, which
    a page rendered before tokens were masked still holds, is compared as it always was.
    """
    if len(submitted) == 4 * length:
        try:
            data = bytes.fromhex(submitted)
        except ValueError:
            return submitted.encode()
        return bytes(a ^ b for a, b in zip(data[:length], data[length:]))
    return submitted.encode()


def _submitted_token(read_form=True):
    """Pull the token out of the headers, then - unless `read_form` is false - the form
    body.

    Headers first, because reading `request.form` reads and parses the whole body, and
    this runs before any view. The upload dropzone sends its token as a header, and the
    query route never has its form read here (see CsrfProtect.header_only), so
    `analyze.query` can refuse an upload over the role's cap from its declared length
    before the body is taken in, wherever the token was put.
    """
    for header in HEADER_NAMES:
        token = request.headers.get(header)
        if token:
            return token
    if not read_form:
        return None
    return request.form.get(FIELD_NAME) or None


def validate_csrf(read_form=True):
    """True when the request carries this session's token, masked or bare."""
    expected = session.get(FIELD_NAME)
    submitted = _submitted_token(read_form)
    if not expected or not submitted:
        return False
    expected = expected.encode()
    # constant-time: the token is a secret being compared against attacker input
    return hmac.compare_digest(expected, unmask(submitted, len(expected)))


class CsrfProtect:
    """Rejects any unsafe-method request that does not carry the session's token."""

    def __init__(self, app=None):
        self._exempt_views = set()
        self._exempt_blueprints = set()
        self._header_only_views = set()
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

    def header_only(self, view):
        """Take a view's token from a header only, and never look for it in the body.

        For an upload route with a per-role cap. Looking in the form parses the whole
        body, up to MAX_CONTENT_LENGTH, and this check runs before the view, which is
        where an upload over the caller's cap is refused from its declared length. The
        caller's role is not known yet at this point, so the cap cannot be applied here
        instead. The dropzone, the only thing posting to such a route, sends a header.

        flask-wtf has no counterpart; see ADR-0002.
        """
        self._header_only_views.add(f"{view.__module__}.{view.__name__}")
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
        name = f"{view.__module__}.{view.__name__}" if view is not None else None
        if name in self._exempt_views:
            return
        if not validate_csrf(read_form=name not in self._header_only_views):
            abort(400, description="The CSRF token is missing or invalid.")
