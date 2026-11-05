#!/usr/bin/python
"""Asserts the whole route surface against the policy declared in routePolicy.py.

Six tests, all offline:

1. every caller below a route's declared role is rejected
1b. the caller *at* that role is admitted - the other half of the same boundary
1c. no route answers a caller it is supposed to serve with a 5xx or an exception
2. the policy table and `app.url_map` describe the same set of routes
3. no `@<role>_required` is written above its `@bp.route`, where it never runs
4. no GET writes anything unless the table says so

Tests 1, 2 and 3 need no backend at all, because authorization is settled before the
view body runs. The rest swap in the permissive fake from conftest: with the strict
one, a view that would have written can abort on a raised NotImplementedError first
and then look innocent, and 1c would be asserting that the fake is complete rather
than that the route works.

1 and 1b together pin the boundary from both sides. Either alone is satisfied by a
route that is simply broken - one because a broken route rejects everyone, the other
because it would admit everyone if the decorator were missing.
"""

import ast
import logging
import pathlib
import sqlite3
import unittest

import pytest
from routePolicy import (
    ADMIN,
    APITOKEN,
    CONTRIBUTOR,
    IN_VIEW_GUARD,
    KNOWN_INERT_DECORATORS,
    LOGGED_IN,
    MUTATING_CLIENT_CALLS,
    PUBLIC,
    ROLE_ORDER,
    ROUTE_POLICY,
    VISITOR,
    WRITES_ON_GET,
)

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

AUTH_DECORATORS = {
    "login_required",
    "visitor_required",
    "contributor_required",
    "admin_required",
    "token_required",
    "multi_user",
}

# Which session role exercises each policy level. 'pending' is the weakest thing a
# logged-in caller can be, which makes it the right probe for LOGGED_IN.
CALLER_FOR = {PUBLIC: None, LOGGED_IN: "pending", VISITOR: "visitor", CONTRIBUTOR: "contributor", ADMIN: "admin"}
CALLERS = [None, "pending", "visitor", "contributor", "admin"]

# Values for URL parameters. Deliberately ids that do not exist, so a request that
# does get past the gate cannot damage anything the test set up.
FILLERS = {
    "user_id": "9999", "family_id": "9999", "sample_id": "9999", "function_id": "9999",
    "sample_id_a": "9999", "sample_id_b": "9998", "function_id_a": "9999", "function_id_b": "9998",
    "job_id": "0123456789abcdef", "role": "visitor", "tab": "overview",
    "filename": "nothing.png", "picblockhash": "0123456789abcdef",
    "type": "sample", "item_id": "9999", "api_path": "version",
}

# At least this many routes must run to completion in the write detector. A fake
# that degrades until every view crashes early would otherwise pass it silently.
MIN_ROUTES_REACHING_A_RESPONSE = 30


@pytest.fixture
def fake_mcrit(recording_mcrit):
    """Wire the app in this module to the permissive fake (see conftest)."""
    return recording_mcrit


# --- helpers ---------------------------------------------------------------------

def _level(role):
    return ROLE_ORDER.index(role)


def _caller_level(caller):
    return _level(PUBLIC) if caller is None else _level({"pending": LOGGED_IN}.get(caller, caller))


def _is_below(caller, min_role):
    """Does this caller sit below what the route requires?"""
    if min_role == APITOKEN:
        # no session role can satisfy a header-token gate
        return True
    return _caller_level(caller) < _level(min_role)


def _target(app, endpoint):
    """A concrete URL and method for an endpoint, from the first rule that owns it."""
    rule = next(r for r in app.url_map.iter_rules() if r.endpoint == endpoint)
    path = str(rule)
    for argument in rule.arguments:
        token = next(seg for seg in path.split("/") if seg.startswith("<") and seg.endswith(">") and argument in seg)
        path = path.replace(token, FILLERS.get(argument, "1"))
    methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
    return path, ("GET" if "GET" in methods else methods[0]), methods


def _use_session(client, user_id):
    with client.session_transaction() as test_session:
        test_session.clear()
        if user_id is not None:
            test_session["user_id"] = user_id


def _is_rejection(endpoint, response):
    if response.status_code == 403:
        return True
    if response.status_code == 302:
        location = response.headers.get("Location", "")
        if "/login" in location:
            return True
        if endpoint in IN_VIEW_GUARD and location in ("/", "http://localhost/"):
            return True
    return False


def _seed_default_rows(app, user_ids):
    """Create the per-user default rows that the app otherwise creates lazily.

    `utility.get_user_column_setup()` writes a user_column_settings row on first use,
    and `authentication.settings` does the same for user_filters. Without seeding,
    whichever route happens to run first for a given user is blamed for that write,
    which makes the detector below order-dependent and hides real findings behind it.
    """
    from mcritweb.db import UserColumnSettings, UserFilters
    with app.app_context():
        for user_id in user_ids:
            UserFilters.fromDict(user_id, {}).saveToDb()
            UserColumnSettings.fromDict(user_id, {}).saveToDb()


def _fingerprint(database_path):
    """Everything in the database, so any write at all shows up as a difference."""
    connection = sqlite3.connect(database_path)
    try:
        tables = sorted(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))
        return {table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    finally:
        connection.close()


def _routed_functions():
    """Every view function that carries a @bp.route, with its decorators split on it."""
    for source in sorted(pathlib.Path("mcritweb").rglob("*.py")):
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            names = [ast.unparse(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list]
            if not any(name.startswith("bp.route") for name in names):
                continue
            index = next(i for i, name in enumerate(names) if name.startswith("bp.route"))
            yield f"{source.stem}.{node.name}", names[:index], names[index + 1:]


# --- 1. every caller below a route's declared role is rejected --------------------

@pytest.mark.parametrize("caller", CALLERS, ids=lambda c: c or "anonymous")
def test_routes_reject_callers_below_their_policy(app, client, as_role, caller):
    user_ids = {role: as_role(role, username=f"policy{role}") for role in ("pending", "visitor", "contributor", "admin")}

    admitted = []
    for endpoint, (min_role, _writes) in sorted(ROUTE_POLICY.items()):
        if not _is_below(caller, min_role):
            continue
        path, method, _methods = _target(app, endpoint)
        _use_session(client, user_ids.get(caller))
        try:
            response = client.open(path, method=method)
        except Exception as exc:
            # reaching the view body at all means the gate let this caller through
            admitted.append(f"{endpoint} ({method} {path}) raised {type(exc).__name__} inside the view")
            continue
        if not _is_rejection(endpoint, response):
            location = response.headers.get("Location", "")
            admitted.append(f"{endpoint} ({method} {path}) answered {response.status_code} {location}".strip())

    assert not admitted, (
        f"{len(admitted)} route(s) admitted a caller below their declared policy "
        f"({caller or 'anonymous'}):\n  " + "\n  ".join(admitted)
    )


# --- 1b. the caller at a route's declared role is admitted ------------------------

def test_routes_admit_a_caller_at_their_declared_role(app, client, as_role):
    """The other half of the boundary.

    Test 1 proves nobody below the line gets in. On its own that is satisfied by a
    route that rejects everyone - including by being broken. This proves the line is
    where the table says it is, by showing the weakest sufficient caller gets past.

    `token_required` routes are excluded: no session role satisfies a header gate,
    and testApiTokens.py covers them.
    """
    user_ids = {role: as_role(role, username=f"admit{role}") for role in ("pending", "visitor", "contributor", "admin")}

    refused = []
    for endpoint, (min_role, _writes) in sorted(ROUTE_POLICY.items()):
        if min_role == APITOKEN:
            continue
        path, method, _methods = _target(app, endpoint)
        _use_session(client, user_ids.get(CALLER_FOR[min_role]))
        try:
            response = client.open(path, method=method)
        except Exception:
            # an exception means the view body ran, so the gate admitted the caller;
            # whether the body then worked is the next test's business
            continue
        if _is_rejection(endpoint, response):
            location = response.headers.get("Location", "")
            refused.append(f"{endpoint} ({method} {path}) refused {min_role} with {response.status_code} {location}".strip())

    assert not refused, (
        f"{len(refused)} route(s) refused the role routePolicy.py says may call them:\n  " +
        "\n  ".join(refused)
    )


# --- 1c. no route answers a legitimate caller with a server error -----------------

def test_no_route_answers_its_own_role_with_a_server_error(app, client, as_role):
    """Every route, called the way the table says it may be, produces a response.

    This is a smoke test, not a functional one: the fake backend answers empty
    shapes, so a 200 here says the view and its template survived that data, not
    that either is correct. What it does catch is the recurring failure in this
    project's history - a page that raises instead of rendering. It found four when
    it was written: two views returning None on an empty form, one falling off the
    end for an unknown export type, and one iterating a backend answer it had not
    checked.

    404 and 400 are fine; the ids in FILLERS are deliberately absent, and several
    routes want a form body this test does not send.
    """
    user_ids = {role: as_role(role, username=f"smoke{role}") for role in ("pending", "visitor", "contributor", "admin")}

    broken = []
    for endpoint, (min_role, _writes) in sorted(ROUTE_POLICY.items()):
        if min_role == APITOKEN:
            continue
        path, method, _methods = _target(app, endpoint)
        _use_session(client, user_ids.get(CALLER_FOR[min_role]))
        try:
            response = client.open(path, method=method)
        except Exception as exc:
            broken.append(f"{endpoint} ({method} {path}) raised {type(exc).__name__}: {exc}")
            continue
        if response.status_code >= 500:
            broken.append(f"{endpoint} ({method} {path}) answered {response.status_code}")

    assert not broken, (
        f"{len(broken)} route(s) failed for a caller they are supposed to serve:\n  " +
        "\n  ".join(broken)
    )


# --- 2. the table and the url_map describe the same routes ------------------------

def test_every_route_has_a_declared_policy(app):
    live = {rule.endpoint for rule in app.url_map.iter_rules()}
    declared = set(ROUTE_POLICY)

    assert not live - declared, (
        "route(s) with no entry in routePolicy.py: " + ", ".join(sorted(live - declared)) +
        "\nAdd a row saying who may call them and whether they write."
    )
    assert not declared - live, (
        "routePolicy.py names route(s) that no longer exist: " + ", ".join(sorted(declared - live))
    )


# --- 3. no auth decorator sits above its route ------------------------------------

def test_no_authorization_decorator_is_written_above_its_route():
    """`@bp.route` is applied first and registers the undecorated function, so an
    auth decorator written above it wraps a name Flask never sees."""
    inert = {
        name for name, above, _below in _routed_functions()
        if any(decorator in AUTH_DECORATORS for decorator in above)
    }

    assert inert <= KNOWN_INERT_DECORATORS, (
        "authorization decorator(s) written above @bp.route, where they never run: " +
        ", ".join(sorted(inert - KNOWN_INERT_DECORATORS)) +
        "\nMove the decorator below @bp.route."
    )


def test_every_route_carries_a_live_decorator_or_is_declared_public():
    """A route with no decorator below @bp.route enforces nothing by itself."""
    undecorated = {
        name for name, _above, below in _routed_functions()
        if not any(decorator in AUTH_DECORATORS for decorator in below)
    }
    expected = {
        endpoint.split(".")[-1] for endpoint, (min_role, _writes) in ROUTE_POLICY.items()
        if min_role in (PUBLIC, LOGGED_IN)
    }

    surprising = {name for name in undecorated if name.split(".")[-1] not in expected}
    assert not surprising, (
        "route(s) with no live authorization decorator that the table does not "
        "declare public or in-view guarded: " + ", ".join(sorted(surprising))
    )


# --- 4. no GET writes anything unless the table says so ---------------------------

def test_get_requests_do_not_write_unless_declared(app, client, as_role, recording_mcrit):
    """The one test here that can find something nobody wrote down.

    For every route not declared as writing on GET, issue a GET as a caller the
    policy admits and assert that nothing moved: no mutating backend call, and a
    byte-for-byte identical database.
    """
    user_ids = {role: as_role(role, username=f"write{role}") for role in ("pending", "visitor", "contributor", "admin")}
    _seed_default_rows(app, user_ids.values())
    database_path = app.config["DATABASE"]

    unexpected_writes = []
    reached_a_response = 0
    for endpoint, (min_role, writes) in sorted(ROUTE_POLICY.items()):
        if writes == WRITES_ON_GET or min_role == APITOKEN:
            continue
        path, _method, methods = _target(app, endpoint)
        if "GET" not in methods:
            continue

        _use_session(client, user_ids.get(CALLER_FOR[min_role]))
        recording_mcrit.calls.clear()
        before = _fingerprint(database_path)
        try:
            client.get(path)
            reached_a_response += 1
        except Exception:
            # a view that cannot render with the fake still has to not have written
            pass
        after = _fingerprint(database_path)

        called = {name for name, _args, _kwargs in recording_mcrit.calls}
        mutating = sorted(called & MUTATING_CLIENT_CALLS)
        if mutating:
            unexpected_writes.append(f"{endpoint}: GET called {', '.join(mutating)} on the backend")
        if before != after:
            changed = sorted(table for table in after if before.get(table) != after[table])
            unexpected_writes.append(f"{endpoint}: GET changed the database ({', '.join(changed)})")

    assert not unexpected_writes, (
        f"{len(unexpected_writes)} route(s) write on GET without declaring it:\n  " +
        "\n  ".join(unexpected_writes) +
        "\nEither add the method guard, or record the route as WRITES_ON_GET."
    )
    assert reached_a_response >= MIN_ROUTES_REACHING_A_RESPONSE, (
        f"only {reached_a_response} routes rendered a response, below the floor of "
        f"{MIN_ROUTES_REACHING_A_RESPONSE}. The fake has drifted far enough that this "
        f"test is no longer exercising the views it claims to."
    )


def test_first_page_visit_creates_the_callers_default_rows(app, client, as_role):
    """Documents the lazy initialization the detector above seeds away.

    It is a property of `utility.get_user_column_setup()`, shared by every
    table-rendering view, rather than of any single route - which is why it is not
    recorded per row in routePolicy.py.
    """
    user_id = as_role("visitor", username="lazyvisitor")
    database_path = app.config["DATABASE"]

    assert _fingerprint(database_path)["user_column_settings"] == []
    client.get("/explore/families")
    rows = _fingerprint(database_path)["user_column_settings"]

    assert [row[1] for row in rows] == [user_id]


if __name__ == "__main__":
    unittest.main()
