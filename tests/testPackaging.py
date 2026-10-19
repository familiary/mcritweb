"""pyproject.toml is the one place the version and the dependencies are declared; these tests hold
the two places that have to agree with it - requirements.txt, kept for deployments installing from
a checkout, and the version the running application reports - to it."""

import tomllib
from pathlib import Path

from mcritweb.views.utility import get_mcritweb_version

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _project():
    # the suite runs from a checkout, so a missing pyproject.toml is a moved one, not a wheel
    assert PYPROJECT.exists(), f"{PYPROJECT} is gone - this test names the wrong file now"
    return tomllib.loads(PYPROJECT.read_text())["project"]


def test_requirements_txt_mirrors_the_declared_dependencies():
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    requirements = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    assert requirements == _project()["dependencies"]


def test_the_running_version_is_the_packaged_version():
    assert get_mcritweb_version() == _project()["version"]
