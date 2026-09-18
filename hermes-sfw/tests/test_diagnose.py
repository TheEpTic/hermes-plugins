"""Tests for SFW-2 (self-diagnose) and SFW-5 (version layer labeling)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_sfw.manager import SFWConfig, SFWManager

CANDIDATES = (
    ".local/share/pnpm/sfw",
    ".local/share/pnpm/bin/sfw",
    ".local/bin/sfw",
    ".npm-global/bin/sfw",
    ".cargo/bin/sfw",
)


def _write(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _versioned(path: Path, version: str) -> Path:
    return _write(
        path,
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  echo "{version}"\n'
        "else\n"
        "  exit 0\n"
        "fi\n",
    )


def _shim(target: Path, interpreter: str = "sh") -> Path:
    """pnpm-style wrapper: ``sfw`` next to ``sfw.mjs`` with a cmd-shim marker."""
    return _write(
        target.parent / "sfw",
        "#!/bin/sh\n" f'exec {interpreter} "{target}" "$@"\n' f"# cmd-shim-target={target}\n",
    )


@contextmanager
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """HOME + which isolation so discovery tests ignore the real host fs."""
    monkeypatch.setenv("HOME", str(tmp_path))
    with (
        patch("hermes_sfw.resolve.Path.home", return_value=tmp_path),
        patch("hermes_sfw.resolve.shutil.which", return_value=None),
    ):
        yield


@contextmanager
def _isolated_candidates(tmp_path: Path) -> Iterator[None]:
    """Deterministic candidate list inside tmp, ignoring the real host fs."""
    with (
        patch(
            "hermes_sfw.manager.SFWManager._known_candidates",
            return_value=[tmp_path / c for c in CANDIDATES],
        ),
        patch("hermes_sfw.resolve.shutil.which", return_value=None),
    ):
        yield


class TestDiagnoseMissingBinary:
    def test_no_binary_anywhere(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with _isolated_candidates(tmp_path):
            report = SFWManager(SFWConfig(sfw_bin="sfw")).diagnose()
        assert report.healthy is False and report.binary is None and report.version is None
        assert "binary" in report.why.lower()
        assert report.checked == [str(tmp_path / c) for c in CANDIDATES]
        assert report.errors == []

    def test_configured_override_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "custom" / "sfw"
        report = SFWManager(SFWConfig(sfw_bin=str(missing))).diagnose()
        assert report.healthy is False and report.binary is None
        assert "override" in report.why.lower() and str(missing) in report.why


class TestDiagnoseBrokenShim:
    def test_shim_with_missing_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shim = _shim(tmp_path / ".local" / "share" / "pnpm" / "sfw.mjs")
        with _isolated_home(tmp_path, monkeypatch):
            report = SFWManager(SFWConfig(sfw_bin="sfw")).diagnose()
        assert report.healthy is False and report.binary == str(shim)
        assert report.version is None
        assert "shim" in report.why.lower() and "missing" in report.why.lower()

    def test_broken_shim_not_selected_by_find(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """find only returns resolving shims; broken ones are skipped."""
        shim = _shim(tmp_path / ".local" / "share" / "pnpm" / "sfw.mjs")
        monkeypatch.setenv("HOME", str(tmp_path))
        with _isolated_candidates(tmp_path):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            assert mgr.sfw_path is None and mgr.is_installed() is False and shim.exists()


class TestDiagnoseHealthyInstall:
    def test_candidate_binary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sfw_bin = _versioned(
            tmp_path / ".local" / "bin" / "sfw", "Socket Firewall Free, version 1.15.0"
        )
        with _isolated_home(tmp_path, monkeypatch):
            report = SFWManager(SFWConfig(sfw_bin="sfw")).diagnose()
        assert report.healthy is True and report.binary == str(sfw_bin)
        assert report.version == "Socket Firewall Free, version 1.15.0"
        assert report.errors == []

    def test_direct_override_and_version_failure(self, tmp_path: Path) -> None:
        healthy = _versioned(tmp_path / "sfw", "Socket Firewall Free, version 2.0.6")
        report = SFWManager(SFWConfig(sfw_bin=str(healthy))).diagnose()
        assert report.healthy is True and report.version == "Socket Firewall Free, version 2.0.6"
        assert report.errors == []
        broken = _write(tmp_path / "broken", "#!/bin/sh\nexit 3\n")
        failed = SFWManager(SFWConfig(sfw_bin=str(broken))).diagnose()
        assert failed.healthy is False and failed.binary == str(broken)
        assert failed.version is None and "version" in failed.why.lower()


class TestDiagnoseLayerLabeling:
    def test_shim_and_binary_layers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = _versioned(
            tmp_path / ".local" / "share" / "pnpm" / "sfw.mjs",
            "Socket Firewall Free, version 1.15.0",
        )
        shim = _shim(target)
        with _isolated_home(tmp_path, monkeypatch):
            report = SFWManager(SFWConfig(sfw_bin="sfw")).diagnose()
        assert report.healthy is True and report.binary == str(shim)
        assert report.binary_kind == "npm-shim" and report.target == str(target)
        real = _versioned(tmp_path / "real-sfw", "Socket Firewall Free, version 1.15.0")
        direct = SFWManager(SFWConfig(sfw_bin=str(real))).diagnose()
        assert direct.healthy is True and direct.binary_kind == "binary"
        assert direct.target is None


class TestVersionLayerReporting:
    def test_labels_shim_binary_and_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _versioned(
            tmp_path / ".local" / "share" / "pnpm" / "sfw.mjs",
            "Socket Firewall Free, version 1.15.0",
        )
        shim = _shim(target)
        with _isolated_home(tmp_path, monkeypatch):
            info = SFWManager(SFWConfig(sfw_bin="sfw")).get_version_info()
        assert info["version"] == "Socket Firewall Free, version 1.15.0"
        assert info["binary"] == str(shim) and info["binary_kind"] == "npm-shim"
        assert info["target"] == str(target)
        real = _versioned(tmp_path / "real-sfw", "Socket Firewall Free, version 2.0.6")
        direct = SFWManager(SFWConfig(sfw_bin=str(real))).get_version_info()
        assert direct["version"] == "Socket Firewall Free, version 2.0.6"
        assert direct["binary_kind"] == "binary" and direct["target"] is None
        gone = SFWManager(
            SFWConfig(sfw_bin=str(tmp_path / "nonexistent" / "sfw"))
        ).get_version_info()
        assert gone["version"] is None and gone["binary"] is None
        assert gone["binary_kind"] is None and gone["target"] is None
