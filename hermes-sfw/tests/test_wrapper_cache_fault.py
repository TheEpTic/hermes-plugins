"""Regression tests for launcher firewall-binary cache faults.

A sfw npm/pnpm install is a *launcher*: ``dist/sfw.mjs`` downloads a platform
firewall binary into ``<package root>/.sfw-cache/<release>/<asset>`` and points
``.sfw-cache/latest`` at it. When that link does not resolve — a provisioned
tree copied under a new home leaves an absolute link pointing at the old
location — every routed command dies inside the launcher ("Failed to prepare
firewall binary") before the package manager runs. The plugin must skip such an
install in favour of a working one, and when there is none, report the fault
and its repair instead of letting the failure look like a dependency block.
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
# Provisioning-time absolute link target, i.e. what a stale link holds.
STALE_TARGET = "/mnt/skel/home/gotavex/.npm-global/lib/node_modules/sfw/.sfw-cache/v1.15.1/sfw-free-linux-x86_64"


class Layout:
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
def layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path)


@pytest.fixture
def stale_layout(layout: Layout) -> Layout:
    """Layout with the reported incident shape: cached asset + stale absolute link."""
    layout.add_cached_asset()
    layout.link_latest(STALE_TARGET)
    return layout


def _real_binary(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    real = directory / "sfw"
    real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    real.chmod(0o755)
    return real


def _resolve_env(which_hit: str, candidates: list) -> tuple:
    return (
        patch("hermes_sfw.resolve.shutil.which", return_value=which_hit),
        patch("hermes_sfw.manager.SFWManager._known_candidates", return_value=candidates),
    )


class TestWrapperCacheFault:
    def test_stale_absolute_link_is_the_reported_fault(self, stale_layout: Layout) -> None:
        fault = stale_layout.manager().wrapper_cache_fault()
        assert fault is not None and fault.binary == str(stale_layout.shim)
        assert "dangling symlink" in fault.reason and STALE_TARGET in fault.reason
        assert fault.cached_asset is not None
        assert fault.repair == f"ln -sfn {fault.cached_asset} {stale_layout.latest}"
        assert f"ln -sfn {fault.cached_asset} {stale_layout.latest}" in fault.note()

    def test_missing_and_relative_links_are_faults(self, layout: Layout) -> None:
        asset = layout.add_cached_asset()
        assert layout.manager().wrapper_cache_fault().repair == f"ln -sfn {asset} {layout.latest}"
        layout.link_latest("../gone/sfw-free-linux-x86_64")
        fault = layout.manager().wrapper_cache_fault()
        assert fault is not None and "dangling symlink" in fault.reason

    def test_healthy_and_empty_states_are_not_faults(self, layout: Layout, tmp_path: Path) -> None:
        asset = layout.add_cached_asset()
        layout.link_latest(str(asset))
        assert layout.manager().wrapper_cache_fault() is None
        fresh = Layout(tmp_path / "fresh")
        assert not fresh.cache_dir.exists()
        assert fresh.manager().wrapper_cache_fault() is None
        fresh.cache_dir.mkdir(parents=True)
        assert fresh.manager().wrapper_cache_fault() is None
        real = _real_binary(tmp_path / ".local" / "bin")
        assert SFWManager(SFWConfig(sfw_bin=str(real))).wrapper_cache_fault() is None
        gone = SFWManager(SFWConfig(sfw_bin=str(tmp_path / "nowhere" / "sfw")))
        assert gone.wrapper_cache_fault() is None


class TestResolutionPrefersUsableInstall:
    def test_faulty_path_hit_skipped_for_working_candidate(
        self, stale_layout: Layout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        healthy = _real_binary(tmp_path / ".local" / "bin")
        monkeypatch.setenv("HOME", str(tmp_path))
        which, cands = _resolve_env(str(stale_layout.shim), [healthy])
        with which, cands:
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            assert mgr.sfw_path == str(healthy) and mgr.wrapper_cache_fault() is None

    def test_faulty_launcher_returned_when_it_is_all_there_is(self, stale_layout: Layout) -> None:
        """Never silently become "not installed": the failure must stay explainable."""
        which, cands = _resolve_env(str(stale_layout.shim), [])
        with which, cands:
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            assert mgr.sfw_path == str(stale_layout.shim)
            assert mgr.wrapper_cache_fault() is not None

    def test_healthy_path_hit_wins_over_candidates(self, layout: Layout, tmp_path: Path) -> None:
        layout.link_latest(str(layout.add_cached_asset()))
        other = _real_binary(tmp_path / ".local" / "bin")
        which, cands = _resolve_env(str(layout.shim), [other])
        with which, cands:
            assert SFWManager(SFWConfig(sfw_bin="sfw")).sfw_path == str(layout.shim)


class TestBootstrapFailureNote:
    def test_names_fault_and_repair(self, stale_layout: Layout) -> None:
        asset = stale_layout.cache_dir / "v1.15.1" / ASSET_NAME
        note = stale_layout.manager().bootstrap_failure_note(f"stdout\n{BOOTSTRAP_ERROR}\n")
        assert note is not None
        assert "cannot prepare its firewall binary" in note
        assert f"ln -sfn {asset} {stale_layout.latest}" in note
        assert "not a dependency block" in note

    def test_unrelated_failure_untouched(self, stale_layout: Layout) -> None:
        assert stale_layout.manager().bootstrap_failure_note("error: could not compile `x`") is None

    def test_faultless_note_still_actionable(self, tmp_path: Path) -> None:
        mgr = SFWManager(SFWConfig(sfw_bin=str(tmp_path / "nowhere" / "sfw")))
        note = mgr.bootstrap_failure_note(BOOTSTRAP_ERROR)
        assert note is not None and "action=status" in note


class TestRunCommandAnnotation:
    @pytest.mark.parametrize(
        "stdout,stderr,code,expect",
        [
            (b"", BOOTSTRAP_ERROR.encode(), 1, True),
            (b"Fetched 0 crates", b"", 0, False),
        ],
    )
    def test_annotation_matches_outcome(
        self, stale_layout: Layout, stdout: bytes, stderr: bytes, code: int, expect: bool
    ) -> None:
        proc = MagicMock()
        proc.communicate.return_value = (stdout, stderr)
        proc.returncode = code
        with patch("hermes_sfw.output.subprocess.Popen", return_value=proc):
            result = stale_layout.manager().run_command("cargo fetch")
        if expect:
            assert result.exit_code == 1 and "ln -sfn" in result.stderr
        else:
            assert "hermes-sfw:" not in result.stderr


class TestTerminalTransformHook:
    def test_annotates_only_bootstrap_failure(self, stale_layout: Layout) -> None:
        with patch.object(hermes_sfw, "_manager", stale_layout.manager()):
            annotated = _annotate_sfw_bootstrap_failure(output=BOOTSTRAP_ERROR)
            untouched = _annotate_sfw_bootstrap_failure(output="Compiling eternal-core v0.1.0")
        assert annotated is not None and annotated.startswith(BOOTSTRAP_ERROR)
        assert "ln -sfn" in annotated and untouched is None

    def test_non_string_output_ignored(self, stale_layout: Layout) -> None:
        with patch.object(hermes_sfw, "_manager", stale_layout.manager()):
            assert _annotate_sfw_bootstrap_failure(output=None) is None

    def test_hooks_registered(self) -> None:
        registered: dict[str, list] = {}

        class Ctx:
            def register_tool(self, **kwargs: object) -> None:
                pass

            def register_hook(self, name: str, fn: object) -> None:
                registered.setdefault(name, []).append(fn)

        with patch.object(hermes_sfw, "_manager", None):
            hermes_sfw.register(Ctx())
        assert "transform_terminal_output" in registered and "pre_tool_call" in registered

    def test_manifest_declares_registered_hooks(self) -> None:
        manifest = Path("src/hermes_sfw/plugin.yaml").read_text(encoding="utf-8")
        assert "  - pre_tool_call\n" in manifest
        assert "  - transform_terminal_output\n" in manifest


class TestStatusReportsFault:
    def test_fault_and_healthy_status(self, layout: Layout) -> None:
        asset = layout.add_cached_asset()
        layout.link_latest(STALE_TARGET)
        payload = json.loads(handle_sfw(layout.manager())({"action": "status"}))
        assert payload["success"] is True and payload["installed"] is True
        assert payload["usable"] is False
        assert payload["cache_fault"]["cached_asset"] == str(asset)
        assert payload["cache_fault"]["repair"] == f"ln -sfn {asset} {layout.latest}"
        layout.latest.unlink()
        layout.link_latest(str(asset))
        plain = json.loads(handle_sfw(layout.manager())({"action": "status"}))
        assert plain["installed"] is True
        assert "cache_fault" not in plain and "usable" not in plain
