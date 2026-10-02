#!/usr/bin/python
"""Parity guardrails across templates and styling for MCRITweb.

Prevents UI/UX regressions:
- Prohibits deprecated `<center>` tags in Jinja templates (use Bootstrap 5 flex/text-center).
- Prohibits deprecated `thead-light` classes in templates (Bootstrap 5 standard is `table-light`).
- Verifies that form labels match actual input IDs (guards against `for="block_count"` copy-paste bugs).
- Checks for well-formed `<i>` icon tags inside `<th>` table headers.
- Verifies absence of hardcoded global `min-width: 900px` in `style.css`.
- Prohibits informal `:(` emoticons in user-facing error templates.
"""

import os
import re

PACKAGE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcritweb")
TEMPLATE_ROOT = os.path.join(PACKAGE_ROOT, "templates")
STATIC_ROOT = os.path.join(PACKAGE_ROOT, "static")


def template_files():
    for directory, _, filenames in os.walk(TEMPLATE_ROOT):
        for filename in sorted(filenames):
            if filename.endswith(".html"):
                yield os.path.join(directory, filename)


def test_no_center_tags_in_templates():
    """No template should use deprecated <center> or </center> markup."""
    center_tag_re = re.compile(r"</?center\b", re.IGNORECASE)
    violations = []
    for filepath in template_files():
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        matches = center_tag_re.findall(content)
        if matches:
            relpath = os.path.relpath(filepath, PACKAGE_ROOT)
            violations.append(f"{relpath}: found {len(matches)} <center> tags")
    assert not violations, "Found deprecated <center> tags in templates:\n" + "\n".join(violations)


def test_no_thead_light_in_templates():
    """Bootstrap 5 standard for table headers is table-light, not thead-light."""
    thead_light_re = re.compile(r'\bclass="[^"]*thead-light[^"]*"', re.IGNORECASE)
    violations = []
    for filepath in template_files():
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        matches = thead_light_re.findall(content)
        if matches:
            relpath = os.path.relpath(filepath, PACKAGE_ROOT)
            violations.append(f"{relpath}: found {len(matches)} thead-light classes")
    assert not violations, "Found deprecated thead-light classes in templates:\n" + "\n".join(violations)


def test_no_mismatched_block_count_label():
    """Ensure no templates have orphaned for='block_count' labels without matching input id."""
    for_re = re.compile(r'for=["\']block_count["\']')
    id_re = re.compile(r'id=["\']block_count["\']')
    violations = []
    for filepath in template_files():
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        if for_re.search(content) and not id_re.search(content):
            relpath = os.path.relpath(filepath, PACKAGE_ROOT)
            violations.append(f"{relpath}: has for='block_count' without matching id='block_count'")
    assert not violations, "Found orphaned for='block_count' attributes:\n" + "\n".join(violations)


def test_no_unclosed_th_icons():
    """Ensure <i> icon tags inside <th> elements are properly closed with </i>."""
    unclosed_re = re.compile(r'<th\b[^>]*>(?:(?!</th>).)*<i\b(?:(?!</i>|</th>).)*</th>', re.IGNORECASE | re.DOTALL)
    violations = []
    for filepath in template_files():
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        matches = unclosed_re.findall(content)
        if matches:
            relpath = os.path.relpath(filepath, PACKAGE_ROOT)
            violations.append(f"{relpath}: found unclosed <i> inside <th>: {matches[0][:60]}")
    assert not violations, "Found unclosed <i> elements inside <th> headers:\n" + "\n".join(violations)


def test_style_css_responsive_body():
    """Ensure style.css does not enforce a rigid min-width: 900px on the body."""
    style_path = os.path.join(STATIC_ROOT, "style.css")
    with open(style_path, encoding="utf-8") as f:
        content = f.read()
    assert "min-width: 900px" not in content, "style.css still contains min-width: 900px"


def test_no_informal_emoticons_in_templates():
    """Ensure user-facing error templates use standard professional alerts without ':(' emoticons."""
    emoticon_re = re.compile(r":\(")
    violations = []
    for filepath in template_files():
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        if emoticon_re.search(content):
            relpath = os.path.relpath(filepath, PACKAGE_ROOT)
            violations.append(f"{relpath}: contains ':(' emoticon")
    assert not violations, "Found informal ':(' emoticons in templates:\n" + "\n".join(violations)


def test_base_html_accessibility_and_semantics():
    """Verify base.html declares html lang, main landmark, and static image dimensions."""
    base_path = os.path.join(TEMPLATE_ROOT, "base.html")
    with open(base_path, encoding="utf-8") as f:
        content = f.read()
    assert '<html lang="en">' in content, "base.html is missing <html lang='en'>"
    assert '<main class="content">' in content, "base.html is missing <main class='content'>"
    assert 'width="459" height="150"' in content, "base.html brand logo is missing explicit dimensions"
    assert 'width="187" height="52"' in content, "base.html FKIE logo is missing explicit dimensions"

