#!/usr/bin/python
"""The mcrit floor is declared twice, and the two declarations have to agree.

`setup.py` is what `pip install -e .` reads and `requirements.txt` is what
`pip install -r` reads, and AGENTS.md says the two move together. The admin page's
three repair jobs raised `requirements.txt` to mcrit 1.9.0 and left `setup.py` at
1.5.3, so an editable install could resolve an mcrit without the client methods those
buttons call - an AttributeError, and a 500, on the first click.
"""

import logging
import pathlib
import re
import unittest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: McritClient methods the admin maintenance routes call, and the first mcrit release
#: that has each (danielplohmann/mcrit: 0d443d3, 6036436 and 67c6681, all in v1.9.0).
MAINTENANCE_METHODS_SINCE = {
    "rebuildPicBlockHashIndex": (1, 9, 0),
    "repairMinHashes": (1, 9, 0),
    "recomputeFamilyStats": (1, 9, 0),
}


def _version(text):
    return tuple(int(part) for part in text.split("."))


def setup_py_specifier():
    # the operator is what tells the dependency from `name="mcritweb"`
    pin = re.search(r"['\"]mcrit\s*([<>=!~][^'\"]*)['\"]", (ROOT / "setup.py").read_text(encoding="utf8"))
    assert pin, "setup.py no longer names mcrit - this test reads the wrong place now"
    return pin.group(1).replace(" ", "")


def requirements_specifier():
    pin = re.search(r"^\s*mcrit\s*([<>=!~]\S*)\s*$", (ROOT / "requirements.txt").read_text(encoding="utf8"), re.MULTILINE)
    assert pin, "requirements.txt no longer names mcrit - this test reads the wrong place now"
    return pin.group(1)


def test_setup_py_and_requirements_txt_ask_for_the_same_mcrit():
    assert setup_py_specifier() == requirements_specifier()


def test_the_floor_has_every_client_method_the_admin_page_calls():
    floor = re.search(r">=([\d.]+)", setup_py_specifier())
    assert floor, f"mcrit{setup_py_specifier()} states no floor"
    for method, since in MAINTENANCE_METHODS_SINCE.items():
        assert _version(floor.group(1)) >= since, f"mcrit {floor.group(1)} has no McritClient.{method}"


def test_the_installed_mcrit_has_them():
    """The table above, checked against the mcrit the suite runs on rather than trusted."""
    from mcrit.client.McritClient import McritClient

    for method in MAINTENANCE_METHODS_SINCE:
        assert callable(getattr(McritClient, method, None)), method


if __name__ == "__main__":
    unittest.main()
