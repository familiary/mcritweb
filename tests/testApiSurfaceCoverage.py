"""Fail when a manual UI action or backend client operation loses its API counterpart."""

import inspect
import re

from mcrit.client.McritClient import McritClient
from routePolicy import ADMIN, CONTRIBUTOR, LOGGED_IN, PUBLIC, ROLE_ORDER, ROUTE_POLICY, VISITOR

from mcritweb.views import api, api_coverage
from mcritweb.views.api_coverage import PUBLIC_API_OPERATIONS, UI_OPERATIONS

#: An ApiRoute role in routePolicy's terms, so the two can be ranked by ROLE_ORDER.
API_ROLE_POLICY = {'user': LOGGED_IN, 'visitor': VISITOR, 'contributor': CONTRIBUTOR, 'admin': ADMIN}

#: UI routes that may map to a looser API operation, each with the reason. Keep it
#: narrow: an entry names one route and one operation, never a whole route.
LOOSER_API_OPERATIONS = {
    # The submit page also queries, and its query half is the same operation as
    # analyze.query, which is visitor-level; only its corpus-submission half needs a
    # contributor, and that half maps to the contributor-level samples routes.
    ('data.submit_or_query', 'POST', 'query'):
        'analyze.query offers the same query to a visitor; only submitting needs a contributor.',
}


def test_every_manual_ui_route_has_a_declared_api_equivalent(app):
    modules = {'authentication', 'admin', 'explore', 'analyze', 'data'}
    routes = {rule.endpoint for rule in app.url_map.iter_rules() if rule.endpoint.split('.')[0] in modules}
    assert routes == set(UI_OPERATIONS), f'Add concrete API counterparts for: {routes - set(UI_OPERATIONS)}; stale declarations: {set(UI_OPERATIONS) - routes}'
    for endpoint, operations in UI_OPERATIONS.items():
        assert operations, endpoint
        for method, path in operations:
            if (method, path) in PUBLIC_API_OPERATIONS:
                assert any(rule.rule == '/api/' + path and method in rule.methods for rule in app.url_map.iter_rules())
            else:
                assert any(route.method == method and re.fullmatch(route.path, path) for route in api.ROUTES), (endpoint, method, path)


def test_every_backend_client_operation_has_a_route_or_a_client_only_explanation():
    methods = {name for name, method in inspect.getmembers(McritClient, callable) if not name.startswith('_')}
    routed = {route.client_method for route in api.ROUTES if route.client_method}
    aliases = {name for name, target in api.CLIENT_METHOD_ALIASES.items() if target in routed}
    covered = routed | aliases | set(api.CLIENT_CONFIGURATION_METHODS)
    assert methods <= covered, f'Provide an API operation for these McritClient methods: {methods - covered}'
    assert all(api.CLIENT_CONFIGURATION_METHODS.values())


def test_all_route_roles_are_explicit_and_valid():
    assert inspect.signature(api.ApiRoute).parameters['role'].default is inspect.Parameter.empty
    assert all(route.role in ('user', 'visitor', 'contributor', 'admin') for route in api.ROUTES)
    # All entries with an unsafe corpus operation must be at least contributor.
    for route in api.ROUTES:
        if route.client_method in {'addReport', 'addBinarySample', 'modifySample', 'modifyFamily', 'modifyFunction', 'deleteSample', 'deleteFamily', 'deleteJob', 'deleteQueueData', 'addImportData', 'addTags', 'removeTags'}:
            assert route.role in ('contributor', 'admin'), route


def api_role(method, path):
    """The least privileged caller of an API operation, in routePolicy's terms."""
    if (method, path) in PUBLIC_API_OPERATIONS:
        return PUBLIC
    route = next(route for route in api.ROUTES if route.method == method and re.fullmatch(route.path, path))
    return API_ROLE_POLICY[route.role]


def looser_api_operations(ui_operations):
    """(endpoint, UI role, method, path, API role) for every operation a token can
    reach with less than the UI route that maps to it requires."""
    found = []
    for endpoint, operations in ui_operations.items():
        assert endpoint in ROUTE_POLICY, f'Record {endpoint} in tests/routePolicy.py first.'
        ui_role = ROUTE_POLICY[endpoint][0]
        for method, path in operations:
            role = api_role(method, path)
            if ROLE_ORDER.index(role) < ROLE_ORDER.index(ui_role):
                found.append((endpoint, ui_role, method, path, role))
    return found


def test_no_api_operation_needs_less_than_the_ui_route_it_stands_for():
    """The API must not be the cheap way around a UI role check: every operation a
    UI route maps to needs at least the role that route does."""
    found = {(endpoint, method, path) for endpoint, _, method, path, _ in looser_api_operations(UI_OPERATIONS)}
    assert found == set(LOOSER_API_OPERATIONS), f'Unexplained looser operations: {found - set(LOOSER_API_OPERATIONS)}; stale exceptions: {set(LOOSER_API_OPERATIONS) - found}'
    assert all(LOOSER_API_OPERATIONS.values())


def test_an_admin_route_mapped_to_a_visitor_operation_is_caught(monkeypatch):
    """The check above must see the case it exists for: an admin-only UI action
    declared as covered by a visitor-level read."""
    monkeypatch.setitem(api_coverage.UI_OPERATIONS, 'admin.reset_server', [('GET', 'status')])
    assert looser_api_operations(api_coverage.UI_OPERATIONS) == [
        ('admin.reset_server', ADMIN, 'GET', 'status', VISITOR),
        ('data.submit_or_query', CONTRIBUTOR, 'POST', 'query', VISITOR),
    ]
