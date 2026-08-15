"""Tests for SFW-2 (self-diagnose) and SFW-5 (version layer labeling)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_sfw.manager import SFWConfig, SFWManager

# Known shim/cache locations walked by diagnose(), in discovery order.
SHIM_CANDIDATES = (
    ".local/share/pnpm/sfw",
    ".local/share/pnpm/bin/sfw",
    ".local/bin/sfw",
    ".npm-global/bin/sfw",
    ".cargo/bin/sfw",
)


def _write_binary(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _version_script(version: str) -> str:
    return (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  echo "{version}"\n'
        "else\n"
        "  exit 0\n"
        "fi\n"
    )


def _make_shim(target: Path, interpreter: str = "sh") -> Path:
    """Create a pnpm-style wrapper shim pointing at a real target.

    The shim is created in the same directory as the target (mirroring npm's
    cmd-shim, which drops ``sfw`` next to ``sfw.mjs``). The content mirrors the
    cmd-shim format: an exec line referencing the target plus a
    ``cmd-shim-target`` marker, both of which the manager parses. The default
    ``interpreter`` is ``sh`` so the version query runs the target as a shell
    script; pass ``node`` when testing the real-world node shim shape.
    """
    shim = target.parent / "sfw"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(
        "#!/bin/sh\n" f'exec {interpreter} "{target}" "$@"\n' f"# cmd-shim-target={target}\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


class TestDiagnoseMissingBinary:
    """SFW-2: no binary anywhere -> broken with a clear reason."""

    def test_reports_no_binary_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        # Isolate from the host machine's real filesystem so the result is
        # deterministic even when sfw is installed under /usr/local/bin.
        with (
            patch(
                "hermes_sfw.manager.SFWManager._known_candidates",
                return_value=[tmp_path / c for c in SHIM_CANDIDATES],
            ),
            patch("hermes_sfw.manager.shutil.which", return_value=None),
        ):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            report = mgr.diagnose()

        assert report.healthy is False
        assert report.binary is None
        assert report.version is None
        assert "binary" in report.why.lower()
        assert report.checked == [str(tmp_path / c) for c in SHIM_CANDIDATES]
        assert report.errors == []

    def test_reports_configured_override_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "custom" / "sfw"
        mgr = SFWManager(SFWConfig(sfw_bin=str(missing)))
        report = mgr.diagnose()

        assert report.healthy is False
        assert report.binary is None
        assert "override" in report.why.lower()
        assert str(missing) in report.why


class TestDiagnoseBrokenShim:
    """SFW-2: a shim exists but its target is missing."""

    def test_reports_shim_with_missing_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Shim at the candidate location (~/.local/share/pnpm/sfw) whose real
        # target (~/.local/share/pnpm/sfw.mjs) does not exist.
        shim = _make_shim(tmp_path / ".local" / "share" / "pnpm" / "sfw.mjs")
        monkeypatch.setenv("HOME", str(tmp_path))
        # Isolate from the host machine's real filesystem.
        with (
            patch("hermes_sfw.manager.Path.home", return_value=tmp_path),
            patch("hermes_sfw.manager.shutil.which", return_value=None),
        ):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            report = mgr.diagnose()

        assert report.healthy is False
        assert report.binary == str(shim)
        assert report.version is None
        assert "shim" in report.why.lower()
        assert "missing" in report.why.lower()

    def test_broken_shim_not_selected_by_find(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_find_sfw only returns shims that resolve; broken shims must be skipped."""
        shim = _make_shim(tmp_path / ".local" / "share" / "pnpm" / "sfw.mjs")
        monkeypatch.setenv("HOME", str(tmp_path))
        # Isolate from the host machine's real filesystem.
        with (
            patch(
                "hermes_sfw.manager.SFWManager._known_candidates",
                return_value=[tmp_path / c for c in SHIM_CANDIDATES],
            ),
            patch("hermes_sfw.manager.shutil.which", return_value=None),
        ):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            assert mgr.sfw_path is None
            assert mgr.is_installed() is False
            assert shim.exists()


class TestDiagnoseHealthyInstall:
    """SFW-2: a working binary is found and version query succeeds."""

    def test_reports_healthy_with_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bin_dir = tmp_path / ".local" / "bin"
        sfw_bin = bin_dir / "sfw"
        _write_binary(sfw_bin, _version_script("Socket Firewall Free, version 1.15.0"))
        monkeypatch.setenv("HOME", str(tmp_path))
        # Isolate from the host machine's real filesystem.
        with (
            patch("hermes_sfw.manager.Path.home", return_value=tmp_path),
            patch("hermes_sfw.manager.shutil.which", return_value=None),
        ):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            report = mgr.diagnose()

        assert report.healthy is True
        assert report.binary == str(sfw_bin)
        assert report.version == "Socket Firewall Free, version 1.15.0"
        assert report.errors == []

    def test_healthy_direct_override(self, tmp_path: Path) -> None:
        sfw_bin = tmp_path / "sfw"
        _write_binary(sfw_bin, _version_script("Socket Firewall Free, version 2.0.6"))
        mgr = SFWManager(SFWConfig(sfw_bin=str(sfw_bin)))
        report = mgr.diagnose()

        assert report.healthy is True
        assert report.binary == str(sfw_bin)
        assert report.version == "Socket Firewall Free, version 2.0.6"
        assert report.errors == []

    def test_version_query_failure_is_broken(self, tmp_path: Path) -> None:
        """Binary exists but --version fails -> not healthy, reason must say so."""
        sfw_bin = tmp_path / "sfw"
        _write_binary(sfw_bin, "#!/bin/sh\nexit 3\n")
        mgr = SFWManager(SFWConfig(sfw_bin=str(sfw_bin)))
        report = mgr.diagnose()

        assert report.healthy is False
        assert report.binary == str(sfw_bin)
        assert report.version is None
        assert "version" in report.why.lower()


class TestDiagnoseLayerLabeling:
    """SFW-2/SFW-5: diagnose() distinguishes npm shims from real binaries."""

    def test_pnpm_shim_layer_labeled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / ".local" / "share" / "pnpm" / "sfw.mjs"
        _write_binary(target, _version_script("Socket Firewall Free, version 1.15.0"))
        shim = _make_shim(target)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Isolate from the host machine's real filesystem.
        with (
            patch("hermes_sfw.manager.Path.home", return_value=tmp_path),
            patch("hermes_sfw.manager.shutil.which", return_value=None),
        ):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            report = mgr.diagnose()

        assert report.healthy is True
        assert report.binary == str(shim)
        assert report.binary_kind == "npm-shim"
        assert report.target == str(target)

    def test_real_binary_layer_labeled(self, tmp_path: Path) -> None:
        sfw_bin = tmp_path / "sfw"
        _write_binary(sfw_bin, _version_script("Socket Firewall Free, version 1.15.0"))
        mgr = SFWManager(SFWConfig(sfw_bin=str(sfw_bin)))
        report = mgr.diagnose()

        assert report.healthy is True
        assert report.binary == str(sfw_bin)
        assert report.binary_kind == "binary"
        assert report.target is None


class TestVersionLayerReporting:
    """SFW-5: version reporting must make the layer explicit."""

    def test_get_version_info_labels_pnpm_shim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / ".local" / "share" / "pnpm" / "sfw.mjs"
        _write_binary(target, _version_script("Socket Firewall Free, version 1.15.0"))
        shim = _make_shim(target)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Isolate from the host machine's real filesystem.
        with (
            patch("hermes_sfw.manager.Path.home", return_value=tmp_path),
            patch("hermes_sfw.manager.shutil.which", return_value=None),
        ):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            info = mgr.get_version_info()

        assert info["version"] == "Socket Firewall Free, version 1.15.0"
        assert info["binary"] == str(shim)
        assert info["binary_kind"] == "npm-shim"
        assert info["target"] == str(target)

    def test_get_version_info_labels_real_binary(self, tmp_path: Path) -> None:
        sfw_bin = tmp_path / "sfw"
        _write_binary(sfw_bin, _version_script("Socket Firewall Free, version 2.0.6"))
        mgr = SFWManager(SFWConfig(sfw_bin=str(sfw_bin)))
        info = mgr.get_version_info()

        assert info["version"] == "Socket Firewall Free, version 2.0.6"
        assert info["binary"] == str(sfw_bin)
        assert info["binary_kind"] == "binary"
        assert info["target"] is None

    def test_get_version_info_missing_binary(self, tmp_path: Path) -> None:
        mgr = SFWManager(SFWConfig(sfw_bin=str(tmp_path / "nonexistent" / "sfw")))
        info = mgr.get_version_info()

        assert info["version"] is None
        assert info["binary"] is None
        assert info["binary_kind"] is None
        assert info["target"] is None
