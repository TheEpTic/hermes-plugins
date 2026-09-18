"""Regression tests for launcher firewall-binary cache faults.

A sfw npm/pnpm install is a *launcher*: ``dist/sfw.mjs`` downloads a platform
firewall binary into ``<package root>/.sfw-cache/<release>/<asset>`` and points
``.sfw-cache/latest`` at it. When that link does not resolve — a provisioned
tree copied under a new home leaves an absolute link pointing at the old
location — every routed command dies inside the launcher ("Failed to prepare
firewall binary") before the package manager runs. The plugin must skip such an
install in favour of a working one, and when there is none, report the fault and
its repair instead of letting the failure look like a dependency block.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import hermes_sfw
from hermes_sfw import _annotate_sfw_bootstrap_failure
from hermes_sfw.handlers.sfw import handle_sfw
from hermes_sfw.manager import SFWConfig, SFWManager

ASSET_NAME = "sfw-free-linux-x86_64"
BOOTSTRAP_ERROR = (
    "[sfw] Failed to prepare firewall binary: Unable to fetch latest release "
    "and no valid cached release found."
)
# A path from the provisioning-time tree, i.e. what a stale absolute link holds.
STALE_TARGET = "/mnt/skel/home/gotavex/.npm-global/lib/node_modules/sfw/.sfw-cache/v1.15.1/sfw-free-linux-x86_64"


class _Layout:
    """A fake npm-global install of the sfw launcher."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "npm-global" / "lib" / "node_modules" / "sfw"
        self.entry = self.root / "dist" / "sfw.mjs"
        self.entry.parent.mkdir(parents=True, exist_ok=True)
        self.entry.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        self.shim = tmp_path / "npm-global" / "bin" / "sfw"
        self.shim.parent.mkdir(parents=True, exist_ok=True)
        self.shim.write_text(
            f'#!/bin/sh\nexec node "{self.entry}" "$@"\n# cmd-shim-target={self.entry}\n',
            encoding="utf-8",
        )
        self.shim.chmod(0o755)

    @property
    def cache_dir(self) -> Path:
        return self.root / ".sfw-cache"

    @property
    def latest(self) -> Path:
        return self.cache_dir / "latest"

    def add_cached_asset(self, name: str = ASSET_NAME) -> Path:
        asset = self.cache_dir / "v1.15.1" / name
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"\x7fELF fake firewall binary")
        asset.chmod(0o755)
        return asset

    def link_latest(self, target: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        os.symlink(target, self.latest)

    def manager(self) -> SFWManager:
        return SFWManager(SFWConfig(sfw_bin=str(self.shim)))


@pytest.fixture
def layout(tmp_path: Path) -> _Layout:
    return _Layout(tmp_path)


class TestWrapperCacheFault:
    def test_dangling_latest_with_cached_asset_is_a_fault(self, layout: _Layout) -> None:
        """The reported incident: an absolute link left over from provisioning."""
        asset = layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)

        fault = layout.manager().wrapper_cache_fault()

        assert fault is not None
        assert fault.binary == str(layout.shim)
        assert "dangling symlink" in fault.reason
        assert STALE_TARGET in fault.reason
        assert fault.cached_asset == str(asset)
        assert fault.repair == f"ln -sfn {asset} {layout.latest}"
        assert f"ln -sfn {asset} {layout.latest}" in fault.note()

    def test_missing_latest_with_cached_asset_is_a_fault(self, layout: _Layout) -> None:
        asset = layout.add_cached_asset()

        fault = layout.manager().wrapper_cache_fault()

        assert fault is not None
        assert "missing" in fault.reason
        assert fault.repair == f"ln -sfn {asset} {layout.latest}"

    def test_relative_dangling_latest_is_a_fault(self, layout: _Layout) -> None:
        layout.add_cached_asset()
        layout.link_latest("../gone/sfw-free-linux-x86_64")

        fault = layout.manager().wrapper_cache_fault()

        assert fault is not None
        assert "dangling symlink" in fault.reason

    def test_resolving_latest_is_healthy(self, layout: _Layout) -> None:
        asset = layout.add_cached_asset()
        layout.link_latest(str(asset))

        assert layout.manager().wrapper_cache_fault() is None

    def test_fresh_install_is_not_a_fault(self, layout: _Layout) -> None:
        """No cache yet: the launcher downloads on first use, so nothing is broken."""
        assert not layout.cache_dir.exists()

        assert layout.manager().wrapper_cache_fault() is None

    def test_empty_cache_dir_is_not_a_fault(self, layout: _Layout) -> None:
        layout.cache_dir.mkdir(parents=True)

        assert layout.manager().wrapper_cache_fault() is None

    def test_real_binary_has_no_cache_layer(self, tmp_path: Path) -> None:
        real = tmp_path / "sfw"
        real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        real.chmod(0o755)

        mgr = SFWManager(SFWConfig(sfw_bin=str(real)))

        assert mgr.wrapper_cache_fault() is None

    def test_missing_binary_is_not_a_cache_fault(self, tmp_path: Path) -> None:
        mgr = SFWManager(SFWConfig(sfw_bin=str(tmp_path / "nowhere" / "sfw")))

        assert mgr.wrapper_cache_fault() is None


class TestResolutionPrefersUsableInstall:
    def test_faulty_path_hit_is_skipped_for_a_working_candidate(
        self, layout: _Layout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)
        healthy = tmp_path / ".local" / "bin" / "sfw"
        healthy.parent.mkdir(parents=True)
        healthy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        healthy.chmod(0o755)
        monkeypatch.setenv("HOME", str(tmp_path))
        with (
            patch("hermes_sfw.manager.shutil.which", return_value=str(layout.shim)),
            patch(
                "hermes_sfw.manager.SFWManager._known_candidates",
                return_value=[healthy],
            ),
        ):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))

            assert mgr.sfw_path == str(healthy)
            assert mgr.wrapper_cache_fault() is None

    def test_faulty_launcher_is_returned_when_it_is_all_there_is(
        self, layout: _Layout, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Never silently become "not installed": the failure must stay explainable."""
        layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)
        with (
            patch("hermes_sfw.manager.shutil.which", return_value=str(layout.shim)),
            patch("hermes_sfw.manager.SFWManager._known_candidates", return_value=[]),
        ):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))

            assert mgr.sfw_path == str(layout.shim)
            assert mgr.wrapper_cache_fault() is not None

    def test_healthy_path_hit_wins_over_candidates(
        self, layout: _Layout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asset = layout.add_cached_asset()
        layout.link_latest(str(asset))
        other = tmp_path / ".local" / "bin" / "sfw"
        other.parent.mkdir(parents=True)
        other.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        other.chmod(0o755)
        with (
            patch("hermes_sfw.manager.shutil.which", return_value=str(layout.shim)),
            patch("hermes_sfw.manager.SFWManager._known_candidates", return_value=[other]),
        ):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))

            assert mgr.sfw_path == str(layout.shim)


class TestBootstrapFailureNote:
    def test_note_names_the_fault_and_repair(self, layout: _Layout) -> None:
        asset = layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)
        mgr = layout.manager()

        note = mgr.bootstrap_failure_note(f"stdout\n{BOOTSTRAP_ERROR}\n")

        assert note is not None
        assert "cannot prepare its firewall binary" in note
        assert f"ln -sfn {asset} {layout.latest}" in note
        assert "not a dependency block" in note

    def test_unrelated_failure_is_untouched(self, layout: _Layout) -> None:
        layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)

        assert layout.manager().bootstrap_failure_note("error: could not compile `x`") is None

    def test_note_without_a_determinable_fault_is_still_actionable(self, tmp_path: Path) -> None:
        mgr = SFWManager(SFWConfig(sfw_bin=str(tmp_path / "nowhere" / "sfw")))

        note = mgr.bootstrap_failure_note(BOOTSTRAP_ERROR)

        assert note is not None
        assert "action=status" in note


class TestRunCommandAnnotation:
    def test_bootstrap_failure_stderr_gains_the_repair(self, layout: _Layout) -> None:
        asset = layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)
        mgr = layout.manager()
        proc = MagicMock()
        proc.communicate.return_value = (b"", BOOTSTRAP_ERROR.encode())
        proc.returncode = 1
        with patch("hermes_sfw.manager.subprocess.Popen", return_value=proc):
            result = mgr.run_command("cargo fetch")

        assert result.success is False
        assert result.exit_code == 1
        assert f"ln -sfn {asset} {layout.latest}" in result.stderr

    def test_success_output_is_not_annotated(self, layout: _Layout) -> None:
        layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)
        mgr = layout.manager()
        proc = MagicMock()
        proc.communicate.return_value = (b"Fetched 0 crates", b"")
        proc.returncode = 0
        with patch("hermes_sfw.manager.subprocess.Popen", return_value=proc):
            result = mgr.run_command("cargo fetch")

        assert result.success is True
        assert "hermes-sfw:" not in result.stderr


class TestTerminalTransformHook:
    def test_annotates_only_the_bootstrap_failure(self, layout: _Layout) -> None:
        asset = layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)
        with patch.object(hermes_sfw, "_manager", layout.manager()):
            annotated = _annotate_sfw_bootstrap_failure(output=BOOTSTRAP_ERROR)
            untouched = _annotate_sfw_bootstrap_failure(output="Compiling eternal-core v0.1.0")

        assert annotated is not None
        assert annotated.startswith(BOOTSTRAP_ERROR)
        assert f"ln -sfn {asset} {layout.latest}" in annotated
        assert untouched is None

    def test_non_string_output_is_ignored(self, layout: _Layout) -> None:
        layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)
        with patch.object(hermes_sfw, "_manager", layout.manager()):
            assert _annotate_sfw_bootstrap_failure(output=None) is None

    def test_hook_is_registered(self) -> None:
        registered: dict[str, list] = {}

        class _Ctx:
            def register_tool(self, **kwargs: object) -> None:
                pass

            def register_hook(self, name: str, fn: object) -> None:
                registered.setdefault(name, []).append(fn)

        with patch.object(hermes_sfw, "_manager", None):
            hermes_sfw.register(_Ctx())

        assert "transform_terminal_output" in registered
        assert "pre_tool_call" in registered


class TestStatusReportsFault:
    def test_status_marks_launcher_unusable(self, layout: _Layout) -> None:
        asset = layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)

        payload = json.loads(handle_sfw(layout.manager())({"action": "status"}))

        assert payload["success"] is True
        assert payload["installed"] is True
        assert payload["usable"] is False
        assert payload["cache_fault"]["cached_asset"] == str(asset)
        assert payload["cache_fault"]["repair"] == f"ln -sfn {asset} {layout.latest}"

    def test_status_stays_plain_when_healthy(self, layout: _Layout) -> None:
        asset = layout.add_cached_asset()
        layout.link_latest(str(asset))

        payload = json.loads(handle_sfw(layout.manager())({"action": "status"}))

        assert payload["installed"] is True
        assert "cache_fault" not in payload
        assert "usable" not in payload
