"""The shipped manifest version must track the package version.

`hermes plugins list` displays the manifest's `version`, so a stale value
misreports what is installed.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_manifest_version_matches_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    manifest = (ROOT / "src" / "hermes_jev_compact" / "plugin.yaml").read_text()
    match = re.search(r"^version:\s*[\"']?([^\"'\s]+)", manifest, re.MULTILINE)
    assert match is not None, "plugin.yaml has no version"
    assert match.group(1) == project
