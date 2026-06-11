"""Tests for hermes-sfw plugin."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from hermes_sfw.handlers import handle_sfw
from hermes_sfw.manager import SFWConfig, SFWManager, SFWResult


def _call(manager: SFWManager, params: dict[str, Any]) -> dict[str, Any]:
    """Helper to call handler and parse JSON response."""
    handler = handle_sfw(manager)
    raw: str = handler(params)
    result: dict[str, Any] = json.loads(raw)
    return result


# ---------------------------------------------------------------------------
# Status action
# ---------------------------------------------------------------------------


class TestStatus:
    """Tests for sfw status action."""

    def test_status_installed(self, manager: SFWManager) -> None:
        result = _call(manager, {"action": "status"})
        assert result["success"] is True
        assert result["installed"] is True
        assert result["version"] is not None

    def test_status_not_installed(self, tmp_path: Path) -> None:
        config = SFWConfig(sfw_bin=str(tmp_path / "nonexistent" / "sfw"))
        mgr = SFWManager(config)
        result = _call(mgr, {"action": "status"})
        assert result["success"] is True
        assert result["installed"] is False
        assert result["version"] is None


# ---------------------------------------------------------------------------
# Run action
# ---------------------------------------------------------------------------


class TestRun:
    """Tests for sfw run action."""

    def test_run_missing_command(self, manager: SFWManager) -> None:
        result = _call(manager, {"action": "run"})
        assert result["success"] is False
        assert "command" in result["error"]

    def test_run_echo(self, manager: SFWManager, mock_subprocess) -> None:
        mock_subprocess.return_value.stdout = "hello\n"
        result = _call(manager, {"action": "run", "command": "npm install express"})
        assert result["success"] is True
        assert "hello" in result.get("stdout", "")

    def test_run_not_installed(self, tmp_path: Path) -> None:
        config = SFWConfig(sfw_bin=str(tmp_path / "nonexistent" / "sfw"))
        mgr = SFWManager(config)
        result = _call(mgr, {"action": "run", "command": "echo hi"})
        assert result["success"] is False
        assert "not installed" in result.get("stderr", "").lower()

    def test_run_verbose_flag(self, manager: SFWManager) -> None:
        with patch("hermes_sfw.manager.subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            _call(manager, {"action": "run", "command": "npm install express", "verbose": True})

            call_args = mock_run.call_args[0][0]
            assert "--verbose" in call_args

    def test_run_workdir_forwarded(self, tmp_path: Path, manager: SFWManager) -> None:
        with patch("hermes_sfw.manager.subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            _call(
                manager,
                {
                    "action": "run",
                    "command": "npm install express",
                    "workdir": str(tmp_path),
                },
            )

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["cwd"] == str(tmp_path)

    def test_run_invalid_command_syntax(self, manager: SFWManager) -> None:
        result = _call(manager, {"action": "run", "command": 'echo "unclosed'})
        assert result["success"] is False
        stderr = result.get("stderr", "").lower()
        assert "invalid" in stderr or "syntax" in stderr or "parse" in stderr or "closing" in stderr

    def test_run_disallowed_command(self, manager: SFWManager) -> None:
        result = _call(manager, {"action": "run", "command": "cat /etc/passwd"})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestValidation:
    """Tests for input validation."""

    def test_missing_action(self, manager: SFWManager) -> None:
        result = _call(manager, {})
        assert result["success"] is False
        assert "action" in result["error"]

    def test_unknown_action(self, manager: SFWManager) -> None:
        result = _call(manager, {"action": "bogus"})
        assert result["success"] is False
        assert "Unknown action" in result["error"]


# ---------------------------------------------------------------------------
# _parse_output — direct unit tests
# ---------------------------------------------------------------------------


class TestParseOutput:
    """Direct tests for SFWManager._parse_output."""

    def test_empty_output(self) -> None:
        blocked, installed = SFWManager._parse_output("")
        assert blocked == []
        assert installed == []

    def test_blocked_package_with_keyword(self) -> None:
        blocked, installed = SFWManager._parse_output("🔴 blocked malicious-pkg")
        assert "malicious-pkg" in blocked

    def test_blocked_package_text(self) -> None:
        blocked, installed = SFWManager._parse_output("blocked: evil-trojan")
        assert "evil-trojan" in blocked

    def test_blocked_emoji(self) -> None:
        blocked, installed = SFWManager._parse_output("🚫 forbidden-pkg")
        assert "forbidden-pkg" in blocked

    def test_installed_package_green(self) -> None:
        blocked, installed = SFWManager._parse_output("🟢 installed express")
        assert "express" in installed

    def test_installed_package_text(self) -> None:
        blocked, installed = SFWManager._parse_output("installed: safe-pkg")
        assert "safe-pkg" in installed

    def test_installed_package_added(self) -> None:
        blocked, installed = SFWManager._parse_output("🟢 installed express")
        assert "express" in installed

    def test_mixed_blocked_and_installed(self) -> None:
        output = "🔴 blocked evil-pkg\n🟢 installed safe-pkg"
        blocked, installed = SFWManager._parse_output(output)
        assert "evil-pkg" in blocked
        assert "safe-pkg" in installed

    def test_no_match_lines_ignored(self) -> None:
        blocked, installed = SFWManager._parse_output("npm WARN deprecated foo@1.0.0")
        assert blocked == []
        assert installed == []

    def test_blocked_in_package_name_no_false_positive(self) -> None:
        """'blocked' as a substring in a package name should not match."""
        blocked, installed = SFWManager._parse_output("Installing blocked-utils successfully")
        # Should NOT appear in blocked list
        assert "blocked-utils" not in blocked
        assert "successfully" not in blocked

    def test_added_in_package_name_no_false_positive(self) -> None:
        """'added' as a substring in a package name should not match."""
        blocked, installed = SFWManager._parse_output("Removed added-package from cache")
        assert "added-package" not in installed

    def test_full_line_not_dumped_as_package(self) -> None:
        """Lines with keywords but no package after should be skipped."""
        blocked, installed = SFWManager._parse_output("Something is blocked")
        # 'blocked' is at end of line, no next token → should not add anything
        assert blocked == []

    def test_natural_language_no_false_positive(self) -> None:
        """Natural language containing 'blocked' should not trigger detection."""
        blocked, installed = SFWManager._parse_output("the package was blocked")
        assert blocked == []
        assert installed == []

    def test_keyword_with_trailing_punctuation(self) -> None:
        """Keywords followed by colon should still match."""
        blocked, installed = SFWManager._parse_output("blocked: evil-pkg")
        assert "evil-pkg" in blocked

    def test_multiple_blocked_packages(self) -> None:
        output = "🔴 blocked pkg-a\n🔴 blocked pkg-b"
        blocked, installed = SFWManager._parse_output(output)
        assert "pkg-a" in blocked
        assert "pkg-b" in blocked

    def test_empty_lines_skipped(self) -> None:
        blocked, installed = SFWManager._parse_output("\n\n\n")
        assert blocked == []
        assert installed == []


# ---------------------------------------------------------------------------
# Timeout / OSError handling
# ---------------------------------------------------------------------------


class TestTimeout:
    """Tests for subprocess timeout."""

    def test_run_command_timeout(self, tmp_path: Path) -> None:
        sfw_bin = tmp_path / "sfw"
        sfw_bin.write_text("#!/bin/bash\nsleep 100\n", encoding="utf-8")
        sfw_bin.chmod(0o755)

        config = SFWConfig(sfw_bin=str(sfw_bin), timeout=1)
        mgr = SFWManager(config)
        result = mgr.run_command("npm install express")

        assert result.success is False
        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()


class TestOSError:
    """Tests for OS-level errors."""

    def test_run_command_oserror(self, tmp_path: Path) -> None:
        sfw_bin = tmp_path / "sfw"
        sfw_bin.mkdir()  # directory, not executable

        config = SFWConfig(sfw_bin=str(sfw_bin))
        mgr = SFWManager(config)
        result = mgr.run_command("npm install express")

        assert result.success is False
        assert result.exit_code == -1


# ---------------------------------------------------------------------------
# SFWResult.to_dict
# ---------------------------------------------------------------------------


class TestSFWResultToDict:
    """Tests for SFWResult serialization."""

    def test_all_fields_populated(self) -> None:
        r = SFWResult(
            success=True,
            command="npm install",
            stdout="ok",
            stderr="",
            exit_code=0,
            blocked=["evil"],
            installed=["safe"],
        )
        d = r.to_dict()
        assert d["blocked"] == ["evil"]
        assert d["installed"] == ["safe"]
        assert "stderr" not in d  # empty stderr omitted

    def test_optional_fields_empty(self) -> None:
        r = SFWResult(
            success=True,
            command="echo hi",
            stdout="hi",
            stderr="",
            exit_code=0,
        )
        d = r.to_dict()
        assert "blocked" not in d
        assert "installed" not in d


# ---------------------------------------------------------------------------
# _find_sfw edge cases
# ---------------------------------------------------------------------------


class TestFindSfw:
    """Tests for binary location logic."""

    def test_custom_path_exists(self, tmp_path: Path) -> None:
        sfw_bin = tmp_path / "sfw"
        sfw_bin.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        sfw_bin.chmod(0o755)

        mgr = SFWManager(SFWConfig(sfw_bin=str(sfw_bin)))
        assert mgr.is_installed()
        assert mgr.sfw_path == str(sfw_bin)

    def test_custom_path_not_exists(self, tmp_path: Path) -> None:
        mgr = SFWManager(SFWConfig(sfw_bin=str(tmp_path / "nope")))
        assert not mgr.is_installed()
        assert mgr.sfw_path is None

    def test_default_search_finds_path(self, tmp_path: Path) -> None:
        sfw_bin = tmp_path / "sfw"
        sfw_bin.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        sfw_bin.chmod(0o755)

        with patch("hermes_sfw.manager.shutil.which", return_value=str(sfw_bin)):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            assert mgr.is_installed()

    def test_default_search_not_found(self) -> None:
        with patch("hermes_sfw.manager.shutil.which", return_value=None):
            with patch("hermes_sfw.manager.Path") as MockPath:
                # Make all candidate.exists() return False
                instance = MockPath.return_value
                instance.__truediv__ = lambda self, x: instance
                instance.exists.return_value = False
                MockPath.home.return_value = instance
                MockPath.side_effect = lambda *a, **kw: instance

                mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
                assert not mgr.is_installed()


# ---------------------------------------------------------------------------
# get_version edge cases
# ---------------------------------------------------------------------------


class TestGetVersion:
    """Tests for version detection."""

    def test_get_version_not_installed(self) -> None:
        mgr = SFWManager(SFWConfig(sfw_bin="/nonexistent/sfw"))
        assert mgr.get_version() is None

    def test_get_version_oserror(self, tmp_path: Path) -> None:
        sfw_bin = tmp_path / "sfw"
        sfw_bin.mkdir()  # directory, not executable

        mgr = SFWManager(SFWConfig(sfw_bin=str(sfw_bin)))
        version = mgr.get_version()
        assert version is None

    def test_get_version_installed(self, manager: SFWManager) -> None:
        version = manager.get_version()
        assert version is not None
        assert isinstance(version, str)
