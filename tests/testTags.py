"""Tags on families, samples and functions (familiary/mcrit#53).

mcrit keeps the tags and decides what a tag may be; MCRITweb shows them as links to the
search for what carries each, edits them where it already edits the entry, and lists the
tags in use on the statistics page. All of it has to stay out of the way against an mcrit
without tags: an installed package from before them, or a current one connected to an
older backend that answers the tag routes with 404.
"""
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fixtureData import CorpusMcritClient, RawResponse
from mcrit.storage.FamilyEntry import FamilyEntry
from mcrit.storage.SampleEntry import SampleEntry

import mcritweb.views.tags as tags_module

try:
    import mcrit.libs.tags as mcrit_tags
except ImportError:
    mcrit_tags = None

FAMILY_ID = 1
SAMPLE_ID = 0
FUNCTION_ID = 6

#: mcrit.libs.tags' rule, for an installed mcrit that predates it - see mcrit_tag_rule
STAND_IN_TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9 ._:\-]{0,63}$")


def _stand_in_normalize_tags(tags):
    normalized = []
    for tag in tags:
        if not isinstance(tag, str) or not STAND_IN_TAG_PATTERN.fullmatch(tag.strip().lower()):
            raise ValueError(f"invalid tag {tag!r}")
        normalized.append(tag.strip().lower())
    return list(dict.fromkeys(normalized))


@pytest.fixture(autouse=True)
def mcrit_tag_rule(monkeypatch):
    """What a tag may be is mcrit.libs.tags' to say, and the mcrit release CI installs may
    predate it. Then a copy of its rule stands in, so this module tests the same behaviour
    against either package; a test about a package without tags removes it again."""
    if mcrit_tags is None:
        monkeypatch.setattr(tags_module, "normalizeTags", _stand_in_normalize_tags)
        monkeypatch.setattr(tags_module, "MAX_TAGS_PER_REQUEST", 100)


class TaggingCorpus(CorpusMcritClient):
    """The captured corpus behind a backend with tags, answering as McritClient does:
    the tags after the change, None for a refused request, {tag: count} from getTags."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.stored_tags = {"family": {}, "sample": {}, "function": {}}
        #: None from addTags and removeTags, as the client answers a 400
        self.refuses_changes = False

    def _tagged(self, entity, entry):
        if entry is not None:
            entry.tags = list(self.stored_tags[entity].get(getattr(entry, f"{entity}_id"), []))
        return entry

    def getFamily(self, family_id, *args, **kwargs):
        return self._tagged("family", super().getFamily(family_id, *args, **kwargs))

    def getSampleById(self, sample_id, *args, **kwargs):
        return self._tagged("sample", super().getSampleById(sample_id, *args, **kwargs))

    def getFunctionById(self, function_id, *args, **kwargs):
        return self._tagged("function", super().getFunctionById(function_id, *args, **kwargs))

    def search_samples(self, search_term="", *args, **kwargs):
        """The family page asks for `family_id:<id> <query>`, which the corpus' substring
        search cannot read; this one prefix is answered as mcrit would, so that page has rows."""
        prefix = re.match(r"family_id:(\d+) ?", search_term or "")
        if prefix is None:
            return super().search_samples(search_term, *args, **kwargs)
        result = super().search_samples(search_term[prefix.end():], *args, **kwargs)
        family_id = int(prefix.group(1))
        result["search_results"] = {key: value for key, value in result["search_results"].items() if value["family_id"] == family_id}
        return result

    def _typed_search(self, entity, entry_class, data):
        """A page of `data` as the typed search methods answer it, entries tagged. An entry
        class of an mcrit package without tags drops the field, so it is put back here."""
        if data is None:
            return None
        entries = {int(key): self._tagged(entity, entry_class.fromDict(value)) for key, value in data["search_results"].items()}
        id_match = self._tagged(entity, entry_class.fromDict(data["id_match"])) if data.get("id_match") else None
        return SimpleNamespace(entries=entries, cursor=data["cursor"], id_match=id_match, sha_match=None)

    def searchFamilies(self, search_term, cursor=None, is_ascending=True, sort_by=None, limit=None):
        return self._typed_search("family", FamilyEntry, self.search_families(search_term, cursor=cursor, is_ascending=is_ascending, sort_by=sort_by, limit=limit or 100))

    def searchSamples(self, search_term, cursor=None, is_ascending=True, sort_by=None, limit=None):
        return self._typed_search("sample", SampleEntry, self.search_samples(search_term, cursor=cursor, is_ascending=is_ascending, sort_by=sort_by, limit=limit or 100))

    def _answer(self, entity, entity_id, tags):
        """The resulting tags, or in raw mode - the API passthrough's - the response mcrit's
        TagResource sends, whose data names the entity beside them."""
        if not self.raw:
            return tags
        if tags is None:
            return RawResponse(400)
        return RawResponse(200, {"entity": entity, "entity_id": entity_id, "tags": tags})

    def addTags(self, entity, entity_id, tags):
        self._record("addTags", entity, entity_id, list(tags))
        if self.refuses_changes:
            return self._answer(entity, entity_id, None)
        stored = self.stored_tags[entity].setdefault(entity_id, [])
        stored.extend(tag for tag in tags if tag not in stored)
        return self._answer(entity, entity_id, list(stored))

    def removeTags(self, entity, entity_id, tags):
        self._record("removeTags", entity, entity_id, list(tags))
        if self.refuses_changes:
            return self._answer(entity, entity_id, None)
        stored = self.stored_tags[entity].setdefault(entity_id, [])
        stored[:] = [tag for tag in stored if tag not in tags]
        return self._answer(entity, entity_id, list(stored))

    def getTags(self, entity):
        self._record("getTags", entity)
        counts = {}
        for tags in self.stored_tags[entity].values():
            for tag in tags:
                counts[tag] = counts.get(tag, 0) + 1
        counts = dict(sorted(counts.items()))
        return RawResponse(200, {"entity": entity, "tags": counts}) if self.raw else counts


@pytest.fixture
def tagging_mcrit():
    return TaggingCorpus()


@pytest.fixture
def fake_mcrit(tagging_mcrit):
    return tagging_mcrit


def _calls(fake, *names):
    return [call for call in fake.calls if call[0] in names]


def _tag_links(page):
    """{tag text: parsed query} of every tag badge on the page"""
    links = re.findall(r'<a class="badge rounded-pill bg-secondary text-white text-decoration-none" href="([^"]+)">([^<]*)</a>', page)
    return {text: parse_qs(urlparse(href.replace("&amp;", "&")).query) for href, text in links}


# --- showing ---------------------------------------------------------------------

@pytest.mark.parametrize("entity, entity_id, path", [
    ("family", FAMILY_ID, f"/explore/families/{FAMILY_ID}"),
    ("sample", SAMPLE_ID, f"/explore/samples/{SAMPLE_ID}"),
    ("function", FUNCTION_ID, f"/explore/functions/{FUNCTION_ID}"),
])
def test_an_entry_page_shows_its_tags_as_links_to_the_search(client, as_role, tagging_mcrit, entity, entity_id, path):
    """Nothing showed a tag anywhere, so mcrit kept them where no reader of the page
    could see them. Each links to the search for everything carrying it: quoted, since a
    tag may hold spaces and colons, and across all three kinds, functions included -
    `tags:` is an index lookup in mcrit, not the scan a plain function search is."""
    tagging_mcrit.stored_tags[entity][entity_id] = ["packed", "source:vt", "two words"]
    as_role("visitor")
    page = client.get(path).data.decode()
    links = _tag_links(page)
    assert list(links) == ["packed", "source:vt", "two words"]
    assert links["source:vt"] == {"query": ['tags:"source:vt"'], "type": ["family,sample,function"]}
    assert links["two words"]["query"] == ['tags:"two words"']


def test_a_tag_from_the_corpus_is_text_not_markup(client, as_role, tagging_mcrit):
    """Tags come from whoever tagged, or from an import; mcrit refuses markup in a tag, but
    a page must not depend on the backend for that. The name is escaped where it is shown,
    in the edit modal's data attribute, and in the search query it links to."""
    hostile = '"><script>alert(1)</script>'
    tagging_mcrit.stored_tags["sample"][SAMPLE_ID] = [hostile]
    as_role("contributor")
    page = client.get(f"/explore/samples/{SAMPLE_ID}").data.decode()
    assert "<script>alert(1)</script>" not in page
    assert "&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert 'data-bs-tags="&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in page
    # and the query quotes it the way mcrit's parser reads an escaped quote
    assert tags_module.tag_search_query(hostile) == 'tags:"\\"><script>alert(1)</script>"'


@pytest.mark.parametrize("path", [f"/explore/families/{FAMILY_ID}", f"/explore/samples/{SAMPLE_ID}", f"/explore/functions/{FUNCTION_ID}"])
def test_an_untagged_entry_shows_no_tag_line(client, as_role, path):
    as_role("visitor")
    page = client.get(path).data.decode()
    assert "tag-badges" not in page


# --- what is offered, and against what ---------------------------------------------

EDIT_PAGES = [
    ("/explore/families", 'id="family_tags"'),
    ("/explore/samples", 'id="sample_tags"'),
    (f"/explore/families/{FAMILY_ID}", 'id="sample_tags"'),
    (f"/explore/samples/{SAMPLE_ID}", 'id="sample_tags"'),
    (f"/explore/functions/{FUNCTION_ID}", 'id="function_tags"'),
]


@pytest.mark.parametrize("path, field", EDIT_PAGES)
def test_a_contributor_can_edit_tags_where_the_entry_is_edited(client, as_role, path, field):
    """The family and sample modals are where those entries are edited, so the tags field
    is there; a function has no modal of its own, and gets one beside its rename."""
    as_role("contributor")
    page = client.get(path).data.decode()
    assert field in page
    assert page.count(field) == 1


@pytest.mark.parametrize("path, field", EDIT_PAGES)
def test_a_visitor_is_offered_no_tag_editing_and_costs_no_question(client, as_role, tagging_mcrit, path, field):
    """The routes are contributor-only, and a visitor's page does not ask the backend
    whether it stores tags: the role is checked first."""
    as_role("visitor")
    page = client.get(path).data.decode()
    assert field not in page
    assert not _calls(tagging_mcrit, "getTags")


def test_the_modal_buttons_carry_the_tags_the_modal_is_filled_with(client, as_role, tagging_mcrit):
    tagging_mcrit.stored_tags["sample"][SAMPLE_ID] = ["packed", "reviewed"]
    as_role("contributor")
    page = client.get(f"/explore/samples/{SAMPLE_ID}").data.decode()
    assert 'data-bs-tags="packed, reviewed"' in page
    page = client.get(f"/explore/functions/{FUNCTION_ID}").data.decode()
    assert 'id="button_function_tags"' in page
    # and the rows of the two listings, whose modals are filled the same way
    tagging_mcrit.stored_tags["family"][FAMILY_ID] = ["apt"]
    page = client.get("/explore/families").data.decode()
    assert f'data-bs-familyid="{FAMILY_ID}"' in page and 'data-bs-tags="apt"' in page
    page = client.get("/explore/samples").data.decode()
    assert 'data-bs-tags="packed, reviewed"' in page


def test_the_backend_is_asked_once_per_page(client, as_role, tagging_mcrit):
    as_role("contributor")
    client.get(f"/explore/families/{FAMILY_ID}")
    assert len(_calls(tagging_mcrit, "getTags")) == 1


class _BackendWithoutTags(TaggingCorpus):
    """A current McritClient against an mcrit server without the tag routes: each answers
    404, which the client hands back as None."""

    def getTags(self, entity):
        self._record("getTags", entity)
        return None


@pytest.mark.parametrize("path, field", EDIT_PAGES)
def test_nothing_is_offered_against_a_backend_without_tags(app, client, as_role, path, field):
    backend = _BackendWithoutTags()
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend.raw_variant() if kwargs.get("raw_responses") else backend
    as_role("contributor")
    response = client.get(path)
    assert response.status_code == 200
    assert field not in response.data.decode()
    assert 'id="button_function_tags"' not in response.data.decode()


@pytest.mark.parametrize("path, field", EDIT_PAGES)
def test_nothing_is_offered_by_a_client_without_tag_methods(app, client, as_role, path, field):
    """The captured corpus alone is what an mcrit package from before tags looks like:
    no addTags, removeTags or getTags on the client."""
    backend = CorpusMcritClient()
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend.raw_variant() if kwargs.get("raw_responses") else backend
    as_role("contributor")
    response = client.get(path)
    assert response.status_code == 200
    assert field not in response.data.decode()


def test_nothing_is_offered_by_an_mcrit_package_without_the_tag_rule(client, as_role, tagging_mcrit, monkeypatch):
    monkeypatch.setattr(tags_module, "normalizeTags", None)
    as_role("contributor")
    page = client.get("/explore/families").data.decode()
    assert 'id="family_tags"' not in page
    assert not _calls(tagging_mcrit, "getTags")


# --- saving ------------------------------------------------------------------------

def _family_form(tagging_mcrit, **fields):
    family = tagging_mcrit.getFamily(FAMILY_ID)
    form = {"family_id": str(FAMILY_ID), "family_new_name": family.family_name}
    if family.is_library:
        form["family_is_library"] = ""
    form.update(fields)
    return form


def test_a_family_keeps_the_tags_the_form_names(client, as_role, tagging_mcrit):
    """The difference is removed and added rather than the list replaced - mcrit has no
    replace-all, so a tag someone else added meanwhile survives - and a tags-only change
    sends no modification job."""
    tagging_mcrit.stored_tags["family"][FAMILY_ID] = ["apt", "reviewed"]
    as_role("contributor")
    response = client.post("/explore/modifyFamily", data=_family_form(tagging_mcrit, family_tags=" Reviewed, packed ,, source:vt"), follow_redirects=True)
    assert response.status_code == 200
    assert _calls(tagging_mcrit, "removeTags", "addTags") == [
        ("removeTags", ("family", FAMILY_ID, ["apt"]), {}),
        ("addTags", ("family", FAMILY_ID, ["packed", "source:vt"]), {}),
    ]
    assert tagging_mcrit.stored_tags["family"][FAMILY_ID] == ["reviewed", "packed", "source:vt"]
    page = response.data.decode()
    assert f"Tags of family {FAMILY_ID} saved." in page
    assert "Nothing to change." not in page
    assert not _calls(tagging_mcrit, "modifyFamily")


def test_an_emptied_field_removes_every_tag(client, as_role, tagging_mcrit):
    tagging_mcrit.stored_tags["family"][FAMILY_ID] = ["apt", "reviewed"]
    as_role("contributor")
    client.post("/explore/modifyFamily", data=_family_form(tagging_mcrit, family_tags=""))
    assert _calls(tagging_mcrit, "removeTags", "addTags") == [("removeTags", ("family", FAMILY_ID, ["apt", "reviewed"]), {})]


def test_a_form_without_the_field_changes_no_tags(client, as_role, tagging_mcrit):
    """Not every modal has the field - result_cross.html carries its own sample modal - and
    one that never showed tags must not read as asking to clear them."""
    tagging_mcrit.stored_tags["family"][FAMILY_ID] = ["apt"]
    as_role("contributor")
    response = client.post("/explore/modifyFamily", data=_family_form(tagging_mcrit), follow_redirects=True)
    assert not _calls(tagging_mcrit, "removeTags", "addTags")
    assert "Nothing to change." in response.data.decode()


def test_an_invalid_tag_refuses_the_whole_form(client, as_role, tagging_mcrit):
    """Checked before anything is written, so a rename in the same form is not half-done
    with the tags refused afterwards."""
    tagging_mcrit.stored_tags["family"][FAMILY_ID] = ["apt"]
    as_role("contributor")
    response = client.post("/explore/modifyFamily", data=_family_form(tagging_mcrit, family_new_name="renamed", family_tags="fine, $where"), follow_redirects=True)
    assert "invalid tag" in response.data.decode()
    assert not _calls(tagging_mcrit, "removeTags", "addTags", "modifyFamily")


def test_a_refused_change_says_so(client, as_role, tagging_mcrit):
    tagging_mcrit.refuses_changes = True
    as_role("contributor")
    response = client.post("/explore/modifyFamily", data=_family_form(tagging_mcrit, family_tags="packed"), follow_redirects=True)
    assert f"MCRIT did not accept the tags of family {FAMILY_ID}." in response.data.decode()


def test_tags_posted_to_a_backend_without_them_are_refused(app, client, as_role):
    backend = _BackendWithoutTags()
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend.raw_variant() if kwargs.get("raw_responses") else backend
    as_role("contributor")
    response = client.post("/explore/modifyFamily", data=_family_form(backend, family_tags="packed"), follow_redirects=True)
    assert "does not store tags" in response.data.decode()
    assert not _calls(backend, "addTags", "removeTags", "modifyFamily")


def test_tags_posted_through_a_client_without_them_are_refused(app, client, as_role):
    backend = CorpusMcritClient()
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend.raw_variant() if kwargs.get("raw_responses") else backend
    as_role("contributor")
    response = client.post("/explore/modifyFunctionTags", data={"function_id": "6", "function_tags": "packed"}, follow_redirects=True)
    assert "The installed mcrit package cannot tag" in response.data.decode()


def test_a_long_change_is_split_at_mcrits_request_limit(client, as_role, tagging_mcrit):
    as_role("contributor")
    many = [f"t{number}" for number in range(150)]
    client.post("/explore/modifyFamily", data=_family_form(tagging_mcrit, family_tags=", ".join(many)))
    assert [call[1][2] for call in _calls(tagging_mcrit, "addTags")] == [many[:100], many[100:]]


def test_a_sample_keeps_the_tags_the_form_names(client, as_role, tagging_mcrit):
    sample = tagging_mcrit.getSampleById(SAMPLE_ID)
    tagging_mcrit.stored_tags["sample"][SAMPLE_ID] = ["old"]
    as_role("contributor")
    form = {"sample_id": str(SAMPLE_ID), "sample_family_name": sample.family, "sample_version": sample.version, "sample_tags": "new"}
    if sample.is_library:
        form["sample_is_library"] = ""
    response = client.post("/explore/modifySample", data=form, follow_redirects=True)
    assert _calls(tagging_mcrit, "removeTags", "addTags") == [
        ("removeTags", ("sample", SAMPLE_ID, ["old"]), {}),
        ("addTags", ("sample", SAMPLE_ID, ["new"]), {}),
    ]
    assert f"Tags of sample {SAMPLE_ID} saved." in response.data.decode()
    assert not _calls(tagging_mcrit, "modifySample")


def test_a_function_keeps_the_tags_the_form_names(client, as_role, tagging_mcrit):
    as_role("contributor")
    response = client.post("/explore/modifyFunctionTags", data={"function_id": str(FUNCTION_ID), "function_tags": "crypto, Config"}, follow_redirects=True)
    assert _calls(tagging_mcrit, "removeTags", "addTags") == [("addTags", ("function", FUNCTION_ID, ["crypto", "config"]), {})]
    page = response.data.decode()
    assert f"Tags of function {FUNCTION_ID} saved." in page
    # back on the function page, which shows them
    assert list(_tag_links(page)) == ["crypto", "config"]
    response = client.post("/explore/modifyFunctionTags", data={"function_id": str(FUNCTION_ID), "function_tags": "config, crypto"}, follow_redirects=True)
    assert "Nothing to change." in response.data.decode()
    assert len(_calls(tagging_mcrit, "addTags", "removeTags")) == 1


@pytest.mark.parametrize("function_id", ["abc", "-3", "999999"])
def test_function_tags_need_a_stored_function(client, as_role, tagging_mcrit, function_id):
    """Query functions have negative ids and carry no tags in mcrit."""
    as_role("contributor")
    response = client.post("/explore/modifyFunctionTags", data={"function_id": function_id, "function_tags": "x"}, follow_redirects=True)
    assert "No valid function_id received." in response.data.decode()
    assert not _calls(tagging_mcrit, "addTags", "removeTags")


def test_a_visitor_cannot_tag(client, as_role, tagging_mcrit):
    as_role("visitor")
    assert client.post("/explore/modifyFunctionTags", data={"function_id": str(FUNCTION_ID), "function_tags": "x"}).status_code == 403
    assert client.post("/explore/modifyFamily", data={"family_id": str(FAMILY_ID), "family_tags": "x"}).status_code == 403
    assert not _calls(tagging_mcrit, "addTags", "removeTags")


# --- the tags in use ---------------------------------------------------------------

def test_the_statistics_page_lists_the_tags_in_use(client, as_role, tagging_mcrit):
    tagging_mcrit.stored_tags["family"] = {1: ["apt"], 2: ["apt", "packed"]}
    tagging_mcrit.stored_tags["sample"] = {0: ["packed"]}
    tagging_mcrit.stored_tags["function"] = {6: ["crypto"]}
    as_role("visitor")
    page = client.get("/explore/statistics").data.decode()
    rows = re.findall(r'<th scope="row"><a href="([^"]+)">([^<]+)</a></th>\s*<td class="text-end">(\d+)</td>\s*<td class="text-end">(\d+)</td>\s*<td class="text-end">(\d+)</td>', page)
    assert [row[1:] for row in rows] == [("apt", "2", "0", "0"), ("crypto", "0", "0", "1"), ("packed", "1", "1", "0")]
    assert parse_qs(urlparse(rows[0][0].replace("&amp;", "&")).query)["query"] == ['tags:"apt"']


def test_the_statistics_page_says_when_nothing_is_tagged(client, as_role):
    as_role("visitor")
    page = client.get("/explore/statistics").data.decode()
    assert 'id="tags"' in page
    assert "No family, sample or function carries a tag yet." in page


@pytest.mark.parametrize("backend_class", [CorpusMcritClient, _BackendWithoutTags])
def test_the_statistics_page_has_no_tags_section_without_tags(app, client, as_role, backend_class):
    backend = backend_class()
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend.raw_variant() if kwargs.get("raw_responses") else backend
    as_role("visitor")
    response = client.get("/explore/statistics")
    assert response.status_code == 200
    assert 'id="tags"' not in response.data.decode()


# --- the API passthrough -----------------------------------------------------------

def _token(role):
    return {"apitoken": f"apitoken-{role}"}


def test_a_contributor_token_tags_through_the_api(client, make_user, tagging_mcrit):
    """The passthrough follows McritClient's tag methods on mcrit's own paths, as it does
    every client method, so a token is not left without what the UI offers."""
    make_user("contributor")
    response = client.post(f"/api/samples/{SAMPLE_ID}/tags", headers=_token("contributor"), json={"tags": ["packed", "reviewed"]})
    assert response.status_code == 200
    assert response.get_json()["tags"] == ["packed", "reviewed"]
    response = client.delete(f"/api/samples/{SAMPLE_ID}/tags", headers=_token("contributor"), json={"tags": ["packed"]})
    assert response.get_json()["tags"] == ["reviewed"]
    assert _calls(tagging_mcrit, "addTags", "removeTags") == [
        ("addTags", ("sample", SAMPLE_ID, ["packed", "reviewed"]), {}),
        ("removeTags", ("sample", SAMPLE_ID, ["packed"]), {}),
    ]
    response = client.get("/api/tags?entity=sample", headers=_token("contributor"))
    assert response.get_json()["tags"] == {"reviewed": 1}


def test_a_visitor_token_cannot_tag_through_the_api(client, make_user, tagging_mcrit):
    make_user("visitor")
    for method in (client.post, client.delete):
        assert method(f"/api/functions/{FUNCTION_ID}/tags", headers=_token("visitor"), json={"tags": ["x"]}).status_code == 403
    assert not _calls(tagging_mcrit, "addTags", "removeTags")
    # reading the tags in use is a visitor's, as the statistics page is
    assert client.get("/api/tags?entity=function", headers=_token("visitor")).status_code == 200


@pytest.mark.parametrize("method, path, body, status", [
    ("delete", "samples/0", {"tags": ["x"]}, 405),
    ("delete", "functions/6/tags/extra", {"tags": ["x"]}, 405),
    ("post", "families/1/tags", {"tag": ["x"]}, 400),
    ("post", "families/1/tags", {"tags": {"x": 1}}, 400),
    ("get", "families/1/tags", None, 405),
    ("get", "tags?entity=report", None, 400),
])
def test_a_malformed_api_tag_request_is_refused(client, make_user, tagging_mcrit, method, path, body, status):
    make_user("admin")
    response = getattr(client, method)(f"/api/{path}", headers=_token("admin"), json=body)
    assert response.status_code == status
    assert not _calls(tagging_mcrit, "addTags", "removeTags", "getTags")


def test_the_api_has_no_tags_without_a_client_that_has(app, client, make_user):
    backend = CorpusMcritClient()
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend.raw_variant() if kwargs.get("raw_responses") else backend
    make_user("admin")
    assert client.post("/api/families/1/tags", headers=_token("admin"), json={"tags": ["x"]}).status_code == 501
    assert client.get("/api/tags?entity=family", headers=_token("admin")).status_code == 501
