"""The page a person is shown for a 400, 403, 404 or 405, the CSRF check's 400 among them.

Werkzeug answers these with a bare document of its own: a title and a sentence, without the
site's layout, its stylesheets or the colour-scheme <meta>, so an account on the dark theme
was shown a white page. They are rendered in base.html now. Nothing on the page asks the
backend: it is drawn from the request, the session and the local database, so it can be shown
whatever state the backend is in.

The API and a script that asks for JSON keep Werkzeug's answer, which is what they had: they
act on the status, and a page is nothing they can read.
"""

from flask import g, render_template, request

#: a request the app could not take (the CSRF check's refusal is one), a page the account's role
#: does not reach, an address with nothing at it, a method the route does not take
CODES = (400, 403, 404, 405)


def wants_a_page():
    """Whether the request comes from a person's browser rather than a program."""
    if request.path == "/api" or request.path.startswith("/api/"):
        return False
    accepted = request.accept_mimetypes
    return accepted.accept_html or not accepted.accept_json


def register(app):
    def error_page(error):
        if not wants_a_page():
            return error.get_response()
        if "theme" not in g:
            # the CSRF check runs before the hook that loads the account, and the page is
            # drawn in the account's theme
            from mcritweb.views.authentication import load_logged_in_user

            load_logged_in_user()
        # the error's own headers but its type: a 405's Allow above all
        headers = [(name, value) for name, value in error.get_headers() if name.lower() != "content-type"]
        return render_template("http_error.html", error=error), error.code, headers

    for code in CODES:
        app.register_error_handler(code, error_page)
