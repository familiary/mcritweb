"""The rows of the Statistics page, built from the backend's `/status` answer.

`MinHashIndex.getStatus` answers `{"status": {...}}` with a flat dict whose keys grew
from release to release: mcrit 1.12 reports 13 of them, 1.13 adds the per-architecture
escaper fingerprints and the stale block hash count, 1.16 the stale and unrehashable
PicHash counts. The page used to print the first twelve keys as they came, raw names
and all, which on 1.13 cut off the very rows that say a maintenance job is due.

Each known key gets a label and a section here. A key this module does not know is
still shown, under "Other", so a newer backend is never silently trimmed. Everything
returned is plain text; the template escapes it.
"""

from importlib.metadata import PackageNotFoundError, version

#: (key, label) per section, in the order the page shows them.
CORPUS_FIELDS = (
    ("num_families", "Families"),
    ("num_samples", "Samples"),
    ("num_functions", "Functions"),
    ("num_pichashes", "Unique PicHashes"),
)
INDEX_FIELDS = (
    ("storage_type", "Storage"),
    ("num_bands", "MinHash bands"),
    ("minhash_compatibility_threshold", "MinHash compatibility threshold"),
    ("db_state", "Database revision"),
    # MongoDbStorage stamps it with datetime.now(UTC) on every release from 1.12 on
    ("db_timestamp", "Last change (UTC)"),
)

#: Maintenance signals: key, what a nonzero count means, and what an operator does about it.
#: Taken from the comments next to each count in mcrit's MinHashIndex.getStatus and the
#: storage queries behind them.
MAINTENANCE_COUNTS = (
    (
        "num_samples_with_stale_minhashes",
        "samples were MinHashed by an smda older than the compatibility threshold, so their matches are not comparable with newer samples.",
        "An admin can run Repair stale MinHashes under Server Management.",
    ),
    (
        "num_samples_with_stale_picblockhashes",
        "non-Intel samples have block hashes that were escaped as Intel code.",
        "An admin can run Recalculate PicHashes under Server Management.",
    ),
    (
        "num_samples_with_stale_pichashes",
        "samples have PicHashes from an smda older than the compatibility threshold.",
        "An admin can run Recalculate PicHashes under Server Management.",
    ),
    (
        "num_samples_pichash_unrehashable",
        "samples cannot have their PicHashes recalculated, because the disassembly of some of their functions is gone.",
        "Recalculating does not help for these.",
    ),
)

#: Keys shown elsewhere on the page, or deliberately not as a row of their own.
_VERSION_KEYS = {"smda_version"}
_ESCAPER_KEYS = {"escaper_fingerprint", "escaper_fingerprints"}
_FLAG_KEYS = {"inline_xcfg_remaining"}
KNOWN_KEYS = (
    {key for key, _ in CORPUS_FIELDS}
    | {key for key, _ in INDEX_FIELDS}
    | {key for key, _, _ in MAINTENANCE_COUNTS}
    | _VERSION_KEYS
    | _ESCAPER_KEYS
    | _FLAG_KEYS
)


def mcrit_client_version():
    """The version of the mcrit package MCRITweb talks to the backend with, or "unknown".

    Shown next to the backend's own version because the two are installed separately
    and can drift: requirements.txt only sets a floor.
    """
    try:
        return version("mcrit")
    except PackageNotFoundError:
        return "unknown"


def display_value(value):
    """A status value as text: counts with thousands separators, flags as yes/no."""
    if value is None:
        return "not reported"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, dict):
        return ", ".join(f"{key}: {display_value(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(display_value(item) for item in value)
    return str(value)


def _rows(status, fields):
    rows = []
    for key, label in fields:
        if key not in status:
            continue
        value = status[key]
        # "2026-09-23T20:30:12" as mcrit's _DATETIME_FORMAT writes it, shown with a space
        if key == "db_timestamp" and isinstance(value, str):
            value = value.replace("T", " ", 1)
        rows.append((label, display_value(value)))
    return rows


def _escaper_rows(status):
    fingerprints = status.get("escaper_fingerprints")
    if isinstance(fingerprints, dict) and fingerprints:
        return [(architecture, display_value(fingerprint)) for architecture, fingerprint in fingerprints.items()]
    # before 1.13 the backend reported Intel's fingerprint only
    if "escaper_fingerprint" in status:
        return [("intel", display_value(status["escaper_fingerprint"]))]
    return []


def _maintenance_notes(status):
    notes = []
    for key, meaning, action in MAINTENANCE_COUNTS:
        count = status.get(key)
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            notes.append((f"{count:,} {meaning}", action))
    if status.get("inline_xcfg_remaining") is True:
        notes.append((
            "The functions collection still holds disassembly inline, so the split that frees its space has not finished.",
            "This is done on the backend host with mcrit's migrate_xcfg_split migration.",
        ))
    return notes


def statistics_page(status_answer, mcritweb_version, mcrit_version):
    """Everything statistics.html renders, from getStatus()'s answer and the versions.

    `mcrit_version` is backend_version()'s answer, which is "unknown" already when the
    backend does not give one; `mcritweb_version` is the app's MCRITWEB_VERSION, None when unset.

    `status_answer` is what McritClient.getStatus() returns: `{"status": {...}}`, or
    None when the backend could not answer.
    """
    status = status_answer.get("status") if isinstance(status_answer, dict) else None
    known = status or {}
    versions = [
        ("MCRITweb", mcritweb_version or "unknown"),
        ("MCRIT backend", mcrit_version),
        ("mcrit client library", mcrit_client_version()),
        ("SMDA (backend)", display_value(known["smda_version"]) if "smda_version" in known else "not reported"),
    ]
    return {
        "has_status": status is not None,
        "versions": versions,
        "corpus": _rows(known, CORPUS_FIELDS),
        "index": _rows(known, INDEX_FIELDS),
        "escapers": _escaper_rows(known),
        "maintenance": _maintenance_notes(known),
        "other": [(key, display_value(value)) for key, value in known.items() if key not in KNOWN_KEYS],
    }
