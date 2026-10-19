#!/usr/bin/python
"""Keep references to numbered architecture decisions in sync with their files."""

import logging
import re
from pathlib import Path

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs" / "adr"
ADR_FILE = re.compile(r"(?P<number>\d{4})-[a-z0-9-]+\.md")
ADR_PATH = re.compile(r"docs/adr/[A-Za-z0-9._-]+")
ADR_LINK = re.compile(r"\[ADR[- ](?P<number>\d{4})\]\((?P<target>[^)\s]+)\)")
ADR_NUMBER = re.compile(r"(?<!\[)\bADR[- ](?P<number>\d{4})\b")


def source_files():
    yield from ROOT.glob("*.md")
    for directory, suffixes in (("docs", {".md"}), ("mcritweb", {".py", ".html"}), ("tests", {".py"})):
        for path in (ROOT / directory).rglob("*"):
            if path.suffix in suffixes and path != Path(__file__).resolve():
                yield path


def references(pattern):
    for path in source_files():
        content = path.read_text(encoding="utf-8")
        for match in pattern.finditer(content):
            line = content.count("\n", 0, match.start()) + 1
            yield path, f"{path.relative_to(ROOT)}:{line}", match


def test_adr_paths_name_existing_files():
    missing = [
        f"{where}: {match.group()}"
        for _, where, match in references(ADR_PATH)
        if not (ROOT / match.group().rstrip(".")).is_file()
    ]
    assert missing == []


def test_adr_link_text_matches_target_number():
    wrong = []
    for path, where, match in references(ADR_LINK):
        target = path.parent / match.group("target").partition("#")[0]
        file_match = ADR_FILE.fullmatch(target.name)
        if not target.is_file() or file_match is None or file_match.group("number") != match.group("number"):
            wrong.append(f"{where}: {match.group()}")
    assert wrong == []


def test_bare_adr_numbers_exist():
    known = {match.group("number") for path in ADR_DIR.iterdir() if (match := ADR_FILE.fullmatch(path.name))}
    unknown = [
        f"{where}: {match.group()}"
        for _, where, match in references(ADR_NUMBER)
        if match.group("number") not in known
    ]
    assert unknown == []


def test_each_check_finds_references_to_check():
    """So that none of the checks above can pass by matching nothing."""
    for pattern in (ADR_PATH, ADR_LINK, ADR_NUMBER):
        assert any(True for _ in references(pattern)), pattern.pattern
