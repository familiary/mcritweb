"""Fail when a manual UI action or backend client operation loses its API counterpart."""

import inspect
import re

from mcrit.client.McritClient import McritClient

from mcritweb.views import api
from mcritweb.views.api_coverage import PUBLIC_API_OPERATIONS, UI_OPERATIONS


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
