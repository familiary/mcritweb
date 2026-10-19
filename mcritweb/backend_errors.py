"""What a page says when a call to the MCRIT backend fails. See issue #43.

Three kinds of failure reach a view, and each gets its own answer:

- The request never completes: `requests` raises (no connection, a timeout, a broken
  answer). Nothing in a view catches that, so `register` turns it into a 503 page
  instead of a 500 with a stack trace. Only `requests.RequestException` is caught: a
  handler wide enough to take a TypeError from a template would report our own bugs as
  the backend's.
- The backend answers with a failure of its own (a 500, a status the client does not
  know, a 2xx that is not "successful"). The default client answers None for that, the
  same None it answers for "not found", and most call sites rely on it: a list, a batch
  lookup or an annotation leaves the missing part out and the page still renders. Those
  keep the default client. A call site that cannot do without the answer, or that tells
  the reader "not found" on a None, goes through `with_server_errors(client)`, a copy of
  the client in mcrit's `raise_server_errors` mode. Its server failures raise
  `McritServerError`, which becomes a 502 page, and its None then means only that the
  backend refused the request or does not have what was asked for.
- That None, where the view cannot carry on: `require_result` turns it into a 404 page
  that says what is missing.
"""

import copy

import requests
from flask import current_app, render_template
from mcrit.client.McritClient import McritClient, McritServerError

#: what the handlers in `register` answer for, to re-raise from a broad `except`
BACKEND_FAILURES = (requests.RequestException, McritServerError)


def with_server_errors(client):
    """`client`, raising `McritServerError` where it would answer None for a failure on
    the backend's side. A refused request or an unknown id still answers None.

    A copy, because get_client() hands every view of the request the same instance, and
    the call sites that degrade on None must keep getting it. A raw-response client hands
    the response back untouched in either mode, and a test's fake backend is not a
    McritClient; both are returned as they are.
    """
    if not isinstance(client, McritClient) or client.raw or client.raise_server_errors:
        return client
    raising = copy.copy(client)
    raising.raise_server_errors = True
    return raising


class NoResultFromBackend(Exception):
    """The backend answered None where the view cannot carry on without a value."""

    def __init__(self, what):
        super().__init__(f"the MCRIT server did not return {what}")
        #: what was asked for, phrased to follow "did not return"
        self.what = what


def require_result(result, what):
    """`result`, or a 404 page naming `what` if the backend answered None.

    Only for a call made through `with_server_errors`: then a None means the request was
    refused or named something that is not there, and the page says exactly that. A
    server failure has already raised by the time this sees the answer.
    """
    if result is None:
        raise NoResultFromBackend(what)
    return result


def is_timeout(error):
    """A backend that answered too slowly, as opposed to one that could not be reached.
    Timeout covers ConnectTimeout and ReadTimeout both."""
    return isinstance(error, requests.exceptions.Timeout)


def message_for(error):
    """What to tell a person about a backend call that did not complete."""
    if is_timeout(error):
        return "The MCRIT server did not respond in time"
    return "No connection to the MCRIT server"


def status_for(error):
    """What to tell a program: 504 for a backend that ran out of time, 502 for one that
    could not be reached or answered unusably."""
    return 504 if is_timeout(error) else 502


def register(app):
    """Answer a failed backend call with a page rather than a stack trace.

    The pages are rendered in place, not redirected to the index: index() calls the
    backend itself, so a backend that fails those calls would fail the redirect target
    too. Rendering also keeps the URL the reader was on. The API blueprint has its own
    handler for transport failures, in views/api.py, since a caller there cannot read a
    page.
    """

    @app.errorhandler(requests.RequestException)
    def backend_unavailable(error):
        current_app.logger.warning("MCRIT backend call failed: %r", error)
        return render_template("backend_unavailable.html", reason=message_for(error)), 503

    @app.errorhandler(McritServerError)
    def backend_failed(error):
        current_app.logger.warning("MCRIT backend failed a request: %s", error)
        return render_template("backend_server_error.html", status_code=error.status_code, message=error.message), 502

    @app.errorhandler(NoResultFromBackend)
    def backend_returned_nothing(error):
        current_app.logger.info("MCRIT backend returned no %s", error.what)
        return render_template("backend_no_result.html", what=error.what), 404
