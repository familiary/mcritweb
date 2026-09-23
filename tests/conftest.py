"""Shared fixtures for the mcritweb test suite.

Every fixture here is offline: the app runs against a throwaway SQLite database and
a fake MCRIT backend substituted through the MCRIT_CLIENT_FACTORY config key, so no
test needs a running mcrit-server. See issue #88.
"""

import pytest
from werkzeug.security import generate_password_hash

from mcritweb import create_app
from mcritweb.db import ServerInfo, UserInfo, init_db

#: The MinHash-relevant configuration this fake instance claims. `MinHashIndex.addImportData`
#: compares an export's `config.shingler` / `config.minhash` against the receiving instance's
#: own hashes and bare-`return`s when either differs (or when `config.version <= "0.0.0"`), so
#: the server answers `{"status": "successful", "data": null}` and the client hands the view a
#: `None` report. Modelling that refusal here is what lets a test drive it - from the view's
#: side it is otherwise indistinguishable from an upload that was never MCRIT data.
FAKE_SHINGLER_HASH = "shingler-config-hash-of-this-instance"
FAKE_MINHASH_HASH = "minhash-config-hash-of-this-instance"


class FakeMcritClient:
    """Stand-in for McritClient.

    Deliberately not a MagicMock: an auto-mock returns further mocks, which render
    happily in Jinja and let template tests pass without asserting anything real.
    This returns the same shapes the backend does, and raises a named
    NotImplementedError for anything a test has not taught it yet, so gaps surface
    as actionable failures rather than silent success.
    """

    #: Set on an instance to make every call answer the way a backend that rejected the
    #: apitoken does. A class attribute rather than an instance one because `__getattr__`
    #: below answers anything it cannot find with a callable, so a missing flag would read
    #: as truthy. Only `addImportData` honours it so far.
    refuses_authentication = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        #: see CorpusMcritClient.raw - modelled for getSampleBySha256 only
        self.raw = bool(kwargs.get("raw_responses"))
        self.calls = []

    def raw_variant(self):
        """The same backend answering in raw mode, as get_client(raw_responses=True)
        hands back. Shares state - `calls` included - with the original, so a test can
        still assert on what was asked.

        Built by hand rather than with copy.copy: the catch-all __getattr__ below
        answers copy's __setstate__ lookup with NotImplementedError.
        """
        clone = object.__new__(type(self))
        clone.__dict__.update(self.__dict__)
        clone.raw = True
        return clone

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    # --- shapes the views expect -------------------------------------------------

    @staticmethod
    def empty_search_result():
        return {
            "search_results": {},
            "cursor": {"forward": None, "backward": None},
            "id_match": None,
            "sha_match": None,
        }

    # --- the handful of methods the default pages touch --------------------------

    def getQueueData(self, *args, **kwargs):
        self._record("getQueueData", *args, **kwargs)
        return []

    def search_samples(self, *args, **kwargs):
        self._record("search_samples", *args, **kwargs)
        return self.empty_search_result()

    def search_families(self, *args, **kwargs):
        self._record("search_families", *args, **kwargs)
        return self.empty_search_result()

    def search_functions(self, *args, **kwargs):
        self._record("search_functions", *args, **kwargs)
        return self.empty_search_result()

    def getFamilies(self, *args, **kwargs):
        self._record("getFamilies", *args, **kwargs)
        return {}

    def getSampleById(self, *args, **kwargs):
        self._record("getSampleById", *args, **kwargs)
        return None

    def getFamily(self, *args, **kwargs):
        self._record("getFamily", *args, **kwargs)
        return None

    def getStatus(self, *args, **kwargs):
        self._record("getStatus", *args, **kwargs)
        return {}

    def getQueueStatistics(self, *args, **kwargs):
        """Category -> {state: count}. data.jobs sums over it, so None is not a
        shape it can survive - it iterates the result without checking."""
        self._record("getQueueStatistics", *args, **kwargs)
        return {}

    def getMatchesForPicBlockHash(self, *args, **kwargs):
        self._record("getMatchesForPicBlockHash", *args, **kwargs)
        return {}

    def getSampleBySha256(self, *args, **kwargs):
        """Nothing is in the corpus by default. That is the branch that lets an upload
        carry on to the backend, rather than short-circuiting as a known sample.

        Raw mode answers 404 rather than None: a caller in raw mode is asking for the
        status precisely because None cannot tell "absent" from "the call failed".
        """
        self._record("getSampleBySha256", *args, **kwargs)
        if getattr(self, "raw", False):
            from fixtureData import RawResponse
            return RawResponse(404)
        return None

    def addBinarySample(self, binary, **kwargs):
        """What a dropzone submit ends at. The real client's type hint claims
        Tuple[SampleEntry, str], but it returns handle_response() straight through and
        `data.submit` uses the result as a scalar job id - so this returns one."""
        self._record("addBinarySample", binary, **kwargs)
        return FAKE_JOB_ID

    def requestMatchesForUnmappedBinary(self, binary, **kwargs):
        """Where `analyze.query` ends for an ordinary upload, once the per-role size cap
        in QUERY_UPLOAD_LIMITS has let it through. Returns a job id, like the other
        submitters."""
        self._record("requestMatchesForUnmappedBinary", binary, **kwargs)
        return FAKE_JOB_ID

    def requestMatchesForMappedBinary(self, binary, base_address, **kwargs):
        """The 'dumped' and 'smda' branches of the same route."""
        self._record("requestMatchesForMappedBinary", binary, base_address, **kwargs)
        return FAKE_JOB_ID

    def requestMatchesForSmdaReport(self, smda_report, **kwargs):
        """The '.smda' branch of `analyze.query`: the view parses the upload into an
        SmdaReport and hands the object over, so nothing about the file survives past
        here except what the view chose to keep on disk."""
        self._record("requestMatchesForSmdaReport", smda_report, **kwargs)
        return FAKE_JOB_ID

    def addImportData(self, import_data):
        """The dropzone upload path: `data.import_view` parses the uploaded file and
        hands the parsed object straight here.

        The real client raises on anything that is not a dict before it reaches the
        wire, so this one does too - otherwise a view that uploads the wrong shape
        would look like it worked. What comes back is either the import report the
        server builds (MinHashIndex.addImportData), which `import_complete.html`
        renders as a table of counters, or `None` - and `None` is the interesting
        half, because it is what *four* different things look like from the view:

        * the index refusing an incompatible export - `config.version <= "0.0.0"`, or
          either config hash differing from this instance's - where it bare-`return`s
          and the server still answers `{"status": "successful", "data": null}`;
        * the index raising on something that is not an export: it reads `config`,
          `content`, `family_mapping` and `sample_entries` unguarded, so a missing key
          is a KeyError, a non-dict `config` a TypeError, and both are a 500 - which
          `handle_response` reports as `None` as well;
        * a rejected or missing apitoken, which falcon's AuthMiddleware answers with a
          401 that `handle_response` has no branch for at all;
        * any other backend failure, down to the database being unreachable.

        All four are modelled here, the last two through `refuses_authentication`, so
        that a test can hold the view to not claiming a cause it cannot tell apart.
        """
        self._record("addImportData", import_data)
        if not isinstance(import_data, dict):
            raise ValueError("Can only forward dictionaries with export data.")
        if self.refuses_authentication:
            return None
        try:
            config = import_data["config"]
            if config["version"] <= "0.0.0":
                return None
            if config["shingler"] != FAKE_SHINGLER_HASH:
                return None
            if config["minhash"] != FAKE_MINHASH_HASH:
                return None
            # everything the index goes on to read before it can count anything
            import_data["content"]["is_compressed"]
            family_mapping = import_data["family_mapping"]
            sample_entries = import_data["sample_entries"]
            function_entries = import_data["function_entries"]
        except (KeyError, TypeError):
            # not an export, whatever else it is: over there this is a 500
            return None
        return {
            "num_samples_imported": len(sample_entries),
            "num_samples_skipped": 0,
            "num_functions_imported": len(function_entries),
            "num_functions_skipped": 0,
            "num_families_imported": len(family_mapping),
            "num_families_skipped": 0,
            "escaper_mismatch": False,
        }

    def getVersion(self, *args, **kwargs):
        self._record("getVersion", *args, **kwargs)
        return "0.0.0-fake"

    def __getattr__(self, name):
        def _unimplemented(*args, **kwargs):
            raise NotImplementedError(
                f"FakeMcritClient has no '{name}'. Add it to tests/conftest.py, "
                f"returning whatever shape the real McritClient returns."
            )
        return _unimplemented


#: A job id shaped like the backend's, for fakes that have to answer one.
FAKE_JOB_ID = "0123456789abcdef01234567"

#: Client methods that queue backend work and answer a job id. A view that gets None
#: from one of these dies in url_for building the redirect to the job page, which
#: reads as a broken route rather than as the gap in the fake that it is.
QUEUEING_METHODS = ("request", "delete", "schedule", "update", "rebuild", "recalculate")


class RecordingMcritClient(FakeMcritClient):
    """A fake that never raises: unknown methods record the call and return None.

    The strict fake above is the right default, because a raised NotImplementedError
    names the gap. It is the wrong tool for asking "did this request write anything",
    since a view that would have written can abort on the raise before it gets there
    and then look innocent. This variant lets the view run on and records what it
    reached for, at the cost of telling you nothing about response shapes.

    Two shapes it does commit to, both for the same reason - "returns None" is not a
    thing the real client ever does for them, so answering None would make this fake
    say something false rather than say nothing:

      * a queueing call answers a job id;
      * `isSampleId` answers a bool, and answers True, because a fake that denies
        every sample id makes every view validating one take its not-found branch,
        which is the opposite of letting the view run on. A test that cares about a
        *missing* id overrides the method for that id.

    Deliberately only `isSampleId`, not every `is*Id`. Extending it to `isFunctionId`
    lets `data.match_functions` past its guard and into `match_info["function_entry_a"]`
    on the None this fake answers `getMatchFunctionVs` with - a TypeError, and a real
    defect in that view (a backend that cannot answer takes the page down rather than
    reporting it) that is nothing to do with the route this commitment exists for.
    Widening this is the right thing to do together with guarding that view, not
    before it.
    """

    def __getattr__(self, name):
        def _permissive(*args, **kwargs):
            self._record(name, *args, **kwargs)
            if name.startswith(QUEUEING_METHODS):
                return FAKE_JOB_ID
            if name == "isSampleId":
                return True
            return None
        return _permissive


@pytest.fixture
def fake_mcrit():
    """The fake backend instance the app under test will hand to its views."""
    return FakeMcritClient()


@pytest.fixture
def recording_mcrit():
    """The permissive fake. Override `fake_mcrit` with it to wire up the app."""
    return RecordingMcritClient()


@pytest.fixture
def corpus_mcrit():
    """A backend serving the captured reports in tests/fixtures/.

    Override `fake_mcrit` with it to wire up the app:

        @pytest.fixture
        def fake_mcrit(corpus_mcrit):
            return corpus_mcrit
    """
    from fixtureData import CorpusMcritClient
    return CorpusMcritClient()


@pytest.fixture
def app(tmp_path, fake_mcrit):
    """A configured app on a throwaway database, wired to the fake backend."""
    instance_path = tmp_path / "instance"
    instance_path.mkdir()

    application = create_app(
        {
            "DATABASE": str(tmp_path / "mcritweb.sqlite"),
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "WTF_CSRF_ENABLED": False,
            # raw_responses has to be honoured, not swallowed: a view asks for it
            # precisely when a parsed None cannot tell "absent" from "the call failed"
            "MCRIT_CLIENT_FACTORY": (
                lambda **kwargs: fake_mcrit.raw_variant() if kwargs.get("raw_responses") else fake_mcrit
            ),
            # mcrit_server_required otherwise makes a real HTTP call to the backend
            "MCRIT_SERVER_PROBE": lambda: True,
        },
        instance_path=str(instance_path),
    )

    with application.app_context():
        init_db()
        # views reach for the server settings on nearly every request
        server_info = ServerInfo()
        server_info.url = "http://127.0.0.1:8000"
        server_info.operation_mode = "multi"
        server_info.registration_token = ""
        server_info.server_token = ""
        server_info.server_uuid = "test-uuid"
        server_info.server_version = "test"
        server_info.saveToDb()

    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_user(app):
    """Create a user with the given role and return its id."""
    def _make_user(role="admin", username=None):
        username = username or f"{role}user"
        with app.app_context():
            user_info = UserInfo()
            user_info.username = username
            user_info.password = generate_password_hash("password")
            user_info.role = role
            user_info.apitoken = f"apitoken-{role}"
            user_info.saveToDb()
            return UserInfo.fromDb(username=username).user_id
    return _make_user


@pytest.fixture
def as_role(client, make_user):
    """Log the test client in as a user with the given role.

    Note that a user must exist for anything to be reachable at all: with an empty
    user table the app treats the instance as unconfigured and redirects to
    registration.
    """
    def _as_role(role="admin", username=None):
        user_id = make_user(role=role, username=username)
        with client.session_transaction() as test_session:
            test_session["user_id"] = user_id
        return user_id
    return _as_role
