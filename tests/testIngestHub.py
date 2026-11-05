#!/usr/bin/python
"""`/data/ingest`: the query, submit and import forms behind one tab row.

A reading route. Each tab is a page of its own and each form posts where the page it came
from posts - analyze.query, data.submit, data.import_view - with the role checks on those
routes. The first version rendered the query tab with the form-type radios, which post to
data.submit_or_query, a contributor route, so the visitors the tab is for were refused;
rendered the import zone without the config that carries the CSRF token, so every import
was refused with a 400; and loaded the upload macro's head script on tabs that showed a
visitor a notice instead of the zone, so those tabs threw on every load. The query, submit
and import pages stay as they are; the hub links to them.
"""

import io
import logging
import re

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

FORM_ACTION = re.compile(r'<form action="([^"]+)" method="post" class="dropzone"')


def form_action(html):
    match = FORM_ACTION.search(html)
    return match.group(1) if match else None


def own_block(html):
    """The page's own markup: what follows it is base.html's drop overlay."""
    return html[html.index("<h1>Ingest</h1>"):html.index('id="submitFileModal"') if 'id="submitFileModal"' in html else len(html)]


# --- the tabs and where their forms post ----------------------------------------------------------

def test_the_query_tab_is_the_default_and_posts_to_the_query_route(client, as_role):
    """`select_form_type=True` rendered the radios that post to data.submit_or_query, a
    contributor route: a visitor's query ended in a 403. As query.html, the form posts
    to analyze.query and carries no form-type radio."""
    as_role("visitor")
    response = client.get("/data/ingest")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<a class="nav-link active" href="/data/ingest/query" aria-current="page">Query</a>' in html
    assert form_action(html) == "/analyze/query"
    assert 'name="form_type"' not in html and 'id="submit-dropzone" disabled>Query' in html


@pytest.mark.parametrize("role", ["contributor", "admin"])
def test_the_submit_tab_posts_to_the_submit_route_with_its_fields(client, as_role, role):
    as_role(role)
    html = client.get("/data/ingest/submit").get_data(as_text=True)
    assert form_action(html) == "/data/submit"
    assert 'id="family"' in html and 'id="version"' in html


@pytest.mark.parametrize("role", ["contributor", "admin"])
def test_the_import_tab_is_the_import_pages_zone(client, as_role, role):
    """The zone was rendered without dropzone.config, so the upload went out without
    the CSRF header and the status script, and nothing happened after a drop. The zone,
    its config and its script come from one macro, shared with the import page."""
    as_role(role)
    tab = client.get("/data/ingest/import").get_data(as_text=True)
    page = client.get("/data/import").get_data(as_text=True)
    assert form_action(tab) == form_action(page) == "/data/import"
    for piece in ('id="import-status-text"', "function showImportStatus", "Upload accepted - loading the import report", "Dropzone.options.myDropzone"):
        assert piece in tab and piece in page, piece


@pytest.mark.parametrize("tab", ["submit", "import"])
def test_a_visitor_is_told_what_the_role_lacks_and_loads_no_zone_script(client, as_role, tab):
    """The form would post to a contributor route and be refused. And the upload macro's
    head script reaches for Dropzone, which only the zone's own script defines: loaded
    on a tab without the zone it threw a ReferenceError on every visit."""
    as_role("visitor")
    html = client.get(f"/data/ingest/{tab}").get_data(as_text=True)
    assert "needs a contributor account" in html
    assert form_action(html) is None
    assert "Dropzone.forElement" not in html and "dropzone.js" not in html


def test_the_page_links_the_three_pages_it_gathers(client, as_role):
    as_role("contributor")
    block = own_block(client.get("/data/ingest").get_data(as_text=True))
    for href in ("/analyze/query", "/data/submit", "/data/import"):
        assert f'href="{href}"' in block, href
    visitor_block = own_block(client.get("/data/ingest").get_data(as_text=True))
    assert visitor_block


def test_a_visitor_is_linked_to_the_query_page_only(client, as_role):
    as_role("visitor")
    block = own_block(client.get("/data/ingest").get_data(as_text=True))
    assert 'href="/analyze/query"' in block
    assert 'href="/data/submit"' not in block and 'href="/data/import"' not in block


def test_an_unknown_tab_is_a_404(client, as_role):
    """It used to render the query tab for any name, which made a typo a page."""
    as_role("visitor")
    assert client.get("/data/ingest/nothing").status_code == 404


def test_the_page_has_no_style_block_and_no_script_of_its_own(client, as_role):
    as_role("visitor")
    block = own_block(client.get("/data/ingest/submit").get_data(as_text=True))
    assert "<style" not in block and "onclick=" not in block
    assert re.findall(r"<script(?![^>]*\bsrc=)", block) == []


# --- who may reach what --------------------------------------------------------------------

def test_a_signed_out_reader_is_sent_to_login(client, make_user):
    make_user(role="visitor")
    response = client.get("/data/ingest")
    assert response.status_code == 302 and response.headers["Location"].endswith("/login")


def test_a_pending_account_is_refused(client, as_role):
    as_role("pending")
    assert client.get("/data/ingest").status_code == 403


@pytest.mark.parametrize("tab", ["", "/query", "/submit", "/import"])
def test_the_page_accepts_no_post(client, as_role, tab):
    as_role("admin")
    assert client.post(f"/data/ingest{tab}", data={"form_type": "query_form"}).status_code == 405


def test_a_visitor_queries_through_the_route_the_tab_posts_to(client, as_role, fake_mcrit):
    as_role("visitor")
    response = client.post("/analyze/query", data={"options": "unmapped", "file": (io.BytesIO(b"MZquery"), "query.exe")},
                           content_type="multipart/form-data")
    assert response.status_code == 202
    assert [call[0] for call in fake_mcrit.calls] == ["requestMatchesForUnmappedBinary"]


# --- the menu entries --------------------------------------------------------------------------

@pytest.mark.parametrize("role, offered", [("pending", False), ("visitor", True), ("contributor", True), ("admin", True)])
def test_the_menu_offers_the_hub_and_the_directory_to_the_roles_that_can_open_them(client, as_role, role, offered):
    """Both entries were shown to a pending account, whose click ended in a 403."""
    as_role(role)
    html = client.get("/profile").get_data(as_text=True)
    assert ('href="/data/ingest"' in html) == offered
    assert ('href="/explore/users"' in html) == offered
