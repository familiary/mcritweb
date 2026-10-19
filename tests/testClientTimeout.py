"""The MCRIT client MCRITweb builds must not wait forever on the backend.

requests waits indefinitely unless told otherwise, so a backend that is down behind a
firewall, or up but hung, used to hold a gunicorn request thread for good - under the
gthread worker docker-mcrit runs, gunicorn's own -t does not reclaim it. The client
factory now gives every client MCRIT_CLIENT_TIMEOUT.
"""

from mcritweb.views.client import default_client_factory


def _build(app, **kwargs):
    """The factory as a view calls it: inside a request, after the request hooks have run."""
    with app.test_request_context("/"):
        app.preprocess_request()
        return default_client_factory(**kwargs)


def test_the_default_client_bounds_its_reads_below_the_proxy(app):
    connect, read = _build(app).timeout
    assert connect == 10
    # docker-mcrit's NGINX gives up after 300 s; stopping first means the user sees
    # MCRITweb's own error rather than a 504 from the proxy
    assert read == 280


def test_an_instance_config_sets_its_own(app):
    app.config["MCRIT_CLIENT_TIMEOUT"] = (5, 60)
    assert _build(app).timeout == (5, 60)


def test_clients_built_with_arguments_get_it_too(app):
    """The API passthrough asks for raw_responses=True and a username of its own."""
    assert _build(app, username="someone", raw_responses=True).timeout == (10, 280)
