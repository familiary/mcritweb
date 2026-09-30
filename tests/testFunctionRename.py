"""Renaming a function from its page (fkie-cad/mcritweb#72).

The route posts to McritClient.modifyFunction, which mcrit gained after 1.8.1; against
an older backend the button is disabled and the route refuses with a flash instead of
an AttributeError.
"""
import pytest
from testFunctionPages import MULTI_BLOCK_FUNCTION


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    # the captured corpus answers the version of the backend it was captured from;
    # renaming needs the release after 1.8.1, so the fake reports one
    corpus_mcrit.getVersion = lambda *args, **kwargs: "1.9.0"
    return corpus_mcrit


def _calls(fake, name):
    return [call for call in fake.calls if call[0] == name]


def test_a_contributor_can_rename_a_function(client, as_role, fake_mcrit):
    as_role("contributor")
    response = client.post("/explore/modifyFunction", data={"function_id": MULTI_BLOCK_FUNCTION, "function_name": "  decrypt_config "}, follow_redirects=True)
    assert response.status_code == 200
    assert _calls(fake_mcrit, "modifyFunction") == [("modifyFunction", (MULTI_BLOCK_FUNCTION, "decrypt_config"), {})]
    page = response.data.decode()
    assert "is now named &#39;decrypt_config&#39;" in page or "is now named 'decrypt_config'" in page
    # the page now shows the name and the label history the backend keeps
    assert "decrypt_config" in page
    assert "tester" in page


def test_the_page_offers_the_rename_to_contributors_only(client, as_role, fake_mcrit):
    as_role("visitor")
    page = client.get(f"/explore/functions/{MULTI_BLOCK_FUNCTION}").data.decode()
    assert "renameFunctionModal" not in page
    as_role("contributor", username="second")
    page = client.get(f"/explore/functions/{MULTI_BLOCK_FUNCTION}").data.decode()
    assert 'id="renameFunctionModal"' in page
    assert 'id="button_function_rename"' in page and "disabled" not in page.split('id="button_function_rename"')[0][-200:]


def test_a_visitor_cannot_rename(client, as_role, fake_mcrit):
    as_role("visitor")
    response = client.post("/explore/modifyFunction", data={"function_id": MULTI_BLOCK_FUNCTION, "function_name": "x"})
    assert response.status_code == 403
    assert not _calls(fake_mcrit, "modifyFunction")


@pytest.mark.parametrize("form", [
    {"function_id": "abc", "function_name": "x"},
    {"function_id": "999999", "function_name": "x"},
    {"function_id": str(MULTI_BLOCK_FUNCTION), "function_name": "d\u00e9crypt"},
    {"function_id": str(MULTI_BLOCK_FUNCTION), "function_name": "x" * 257},
])
def test_bad_input_is_refused_without_a_backend_call(client, as_role, fake_mcrit, form):
    as_role("contributor")
    response = client.post("/explore/modifyFunction", data=form, follow_redirects=True)
    assert response.status_code == 200
    assert not _calls(fake_mcrit, "modifyFunction")


def test_an_unchanged_name_is_not_sent(client, as_role, fake_mcrit):
    as_role("contributor")
    current = fake_mcrit.getFunctionById(MULTI_BLOCK_FUNCTION).function_name or ""
    response = client.post("/explore/modifyFunction", data={"function_id": MULTI_BLOCK_FUNCTION, "function_name": current}, follow_redirects=True)
    assert response.status_code == 200
    assert not _calls(fake_mcrit, "modifyFunction")
    assert "already has that name" in response.data.decode()


class _OlderBackend:
    """A client without modifyFunction, as mcrit 1.8.1's McritClient is."""

    def __init__(self, corpus):
        self._corpus = corpus

    def __getattr__(self, name):
        if name == "modifyFunction":
            raise AttributeError(name)
        return getattr(self._corpus, name)


def test_an_older_server_behind_a_new_client_disables_the_button(client, as_role, fake_mcrit, app):
    # the installed McritClient has modifyFunction, the server it talks to does not serve it
    fake_mcrit.getVersion = lambda *args, **kwargs: "1.8.1"
    as_role("contributor")
    page = client.get(f"/explore/functions/{MULTI_BLOCK_FUNCTION}").data.decode()
    assert "needs an MCRIT backend newer than 1.8.1" in page
    response = client.post("/explore/modifyFunction", data={"function_id": MULTI_BLOCK_FUNCTION, "function_name": "x"}, follow_redirects=True)
    assert "cannot rename functions" in response.data.decode()
    assert not _calls(fake_mcrit, "modifyFunction")


def test_an_older_backend_disables_the_button_and_refuses_the_post(client, as_role, fake_mcrit, app):
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: _OlderBackend(fake_mcrit)
    as_role("contributor")
    page = client.get(f"/explore/functions/{MULTI_BLOCK_FUNCTION}").data.decode()
    assert "needs an MCRIT backend newer than 1.8.1" in page
    response = client.post("/explore/modifyFunction", data={"function_id": MULTI_BLOCK_FUNCTION, "function_name": "x"}, follow_redirects=True)
    assert response.status_code == 200
    assert "cannot rename functions" in response.data.decode()
    assert not _calls(fake_mcrit, "modifyFunction")
