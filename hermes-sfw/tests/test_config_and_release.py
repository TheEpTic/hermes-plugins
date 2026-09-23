"""Config validation, version queries, repair strings and manifest drift."""

from __future__ import annotations

import shlex
import tomllib
from pathlib import Path

import pytest

from hermes_sfw.manager import SFWConfig, SFWManager
from hermes_sfw.resolve import cache_fault

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("timeout", [0, -1, 1.5, "300", True, None])
def test_timeout_must_be_positive_int(timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout"):
        SFWConfig(timeout=timeout)  # type: ignore[arg-type]


@pytest.mark.parametrize("sfw_bin", ["", "   ", None])
def test_sfw_bin_must_be_non_empty(sfw_bin: object) -> None:
    with pytest.raises(ValueError, match="sfw_bin"):
        SFWConfig(sfw_bin=sfw_bin)  # type: ignore[arg-type]


def _script(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_failing_version_query_reports_no_version(tmp_path: Path) -> None:
    broken = _script(tmp_path / "sfw", "#!/bin/sh\necho 'fatal: cannot start' >&2\nexit 3\n")
    mgr = SFWManager(SFWConfig(sfw_bin=str(broken)))
    assert mgr.get_version() is None
    report = mgr.diagnose()
    assert report.healthy is False and report.version is None


def test_diagnose_queries_the_binary_it_reports(tmp_path: Path) -> None:
    good = _script(tmp_path / "sfw", "#!/bin/sh\necho 'sfw 9.9.9'\n")
    report = SFWManager(SFWConfig(sfw_bin=str(good))).diagnose()
    assert report.healthy is True and report.binary == str(good)
    assert report.version == "sfw 9.9.9"


def test_repair_command_is_shell_quoted(tmp_path: Path) -> None:
    from tests.test_wrapper_cache_fault import Layout

    layout = Layout(tmp_path / "my tools")
    asset = layout.add_cached_asset()
    layout.link_latest("/gone/sfw-free")
    fault = cache_fault(str(layout.entry))  # launcher entry under a spaced path
    assert fault is not None
    assert shlex.split(fault.repair) == ["ln", "-sfn", str(asset), str(layout.latest)]


def test_manifest_version_matches_package() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = (ROOT / "src" / "hermes_sfw" / "plugin.yaml").read_text(encoding="utf-8")
    line = next(row for row in manifest.splitlines() if row.startswith("version:"))
    assert line.split(":", 1)[1].strip().strip("\"'") == pyproject["project"]["version"]
