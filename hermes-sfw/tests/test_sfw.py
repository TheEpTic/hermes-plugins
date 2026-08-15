"""Tests for hermes-sfw plugin."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hermes_sfw.handlers import handle_sfw
from hermes_sfw.manager import SFWConfig, SFWManager, SFWResult, _MAX_LIST_ENTRIES


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

    def test_run_echo(self, manager: SFWManager, mock_popen) -> None:
        mock_popen.return_value.communicate.return_value = (b"hello\n", b"")
        result = _call(manager, {"action": "run", "command": "npm install express"})
        assert result["success"] is True
        assert "hello" in result.get("stdout", "")

    def test_run_not_installed(self, tmp_path: Path) -> None:
        config = SFWConfig(sfw_bin=str(tmp_path / "nonexistent" / "sfw"))
        mgr = SFWManager(config)
        result = _call(mgr, {"action": "run", "command": "echo hi"})
        assert result["success"] is False
        assert "not installed" in result.get("stderr", "").lower()

    def test_run_verbose_flag(self, manager: SFWManager, mock_popen) -> None:
        _call(
            manager,
            {"action": "run", "command": "npm install express", "verbose": True},
        )

        call_args = mock_popen.call_args
        args = call_args[0][0]
        assert "--verbose" in args

    def test_run_workdir_forwarded(self, tmp_path: Path, manager: SFWManager, mock_popen) -> None:
        _call(
            manager,
            {
                "action": "run",
                "command": "npm install express",
                "workdir": str(tmp_path),
            },
        )

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["cwd"] == str(tmp_path)

    def test_run_invalid_command_syntax(self, manager: SFWManager) -> None:
        result = _call(manager, {"action": "run", "command": 'echo "unclosed'})
        assert result["success"] is False
        stderr = result.get("stderr", "").lower()
        assert "invalid" in stderr or "syntax" in stderr or "parse" in stderr or "closing" in stderr

    def test_run_disallowed_command(self, manager: SFWManager) -> None:
        result = _call(manager, {"action": "run", "command": "cat /etc/passwd"})
        assert result["success"] is False

    def test_run_non_string_command(self, manager: SFWManager) -> None:
        """Non-string command from LLM hallucination should be rejected."""
        handler = handle_sfw(manager)
        raw = handler({"action": "run", "command": 123})
        result = json.loads(raw)
        assert result["success"] is False
        assert "string" in result["error"].lower()

    def test_run_non_string_bool_command(self, manager: SFWManager) -> None:
        """Boolean command from LLM hallucination should be rejected."""
        handler = handle_sfw(manager)
        raw = handler({"action": "run", "command": True})
        result = json.loads(raw)
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

    def test_approval_denial_blocks_execution(self, manager: SFWManager) -> None:
        denial = {"approved": False, "message": "BLOCKED: approval required"}
        with (
            patch("hermes_sfw.handlers.sfw.check_approval", return_value=denial),
            patch("hermes_sfw.manager.subprocess.Popen") as popen,
        ):
            result = _call(manager, {"action": "run", "command": "npm install left-pad"})
        assert result["success"] is False
        assert "BLOCKED" in result["error"]
        popen.assert_not_called()

    def test_approval_required_without_explicit_denial_blocks_execution(
        self, manager: SFWManager
    ) -> None:
        """Approval state must never fall through to command execution."""
        pending = {"status": "approval_required", "message": "awaiting approval"}
        with (
            patch("hermes_sfw.handlers.sfw.check_approval", return_value=pending),
            patch("hermes_sfw.manager.subprocess.Popen") as popen,
        ):
            result = _call(manager, {"action": "run", "command": "npm install left-pad"})
        assert result["success"] is False
        assert "awaiting approval" in result["error"]
        popen.assert_not_called()


# ---------------------------------------------------------------------------
# Command validation — path separators and maxLength
# ---------------------------------------------------------------------------


class TestCommandValidation:
    """Tests for command prefix allowlist edge cases."""

    def test_reject_absolute_path(self, manager: SFWManager) -> None:
        """Absolute path should be rejected (finding 1)."""
        result = _call(manager, {"action": "run", "command": "/usr/bin/pip install foo"})
        assert result["success"] is False
        assert "path separator" in result.get("stderr", "").lower()

    def test_reject_relative_path(self, manager: SFWManager) -> None:
        """Relative path should be rejected."""
        result = _call(manager, {"action": "run", "command": "../pip install foo"})
        assert result["success"] is False

    def test_reject_command_too_long(self, manager: SFWManager) -> None:
        """Commands exceeding maxLength should be rejected server-side."""
        long_cmd = "npm install " + "x" * 1100
        result = _call(manager, {"action": "run", "command": long_cmd})
        assert result["success"] is False
        assert "too long" in result.get("stderr", "").lower()

    def test_accept_bare_command(self, manager: SFWManager, mock_popen) -> None:
        """Bare command names should be accepted."""
        _call(manager, {"action": "run", "command": "npm install express"})
        assert mock_popen.called

    def test_reject_empty_command(self, manager: SFWManager) -> None:
        """Empty command string should be rejected."""
        result = _call(manager, {"action": "run", "command": ""})
        assert result["success"] is False
        assert "empty" in result.get("stderr", "").lower()

    def test_reject_npx_command(self, manager: SFWManager) -> None:
        """npx can execute arbitrary package code and should stay blocked."""
        result = _call(manager, {"action": "run", "command": "npx cowsay hello"})
        assert result["success"] is False
        assert "not allowed" in result.get("stderr", "").lower()

    @pytest.mark.parametrize(
        "command",
        [
            "npm exec sh",
            "npm run postinstall",
            "pnpm dlx cowsay hi",
            "yarn run build",
            "uv run python evil.py",
            "cargo run",
            "rustup run stable sh",
            # option arguments used to be mistaken for the command verb.
            "npm --prefix /tmp exec sh",
            "npm --prefix=/tmp run-script x",
            "uv --project /tmp run sh",
            "uv --project=/tmp run sh",
            "cargo --manifest-path /tmp/Cargo.toml run",
            "rustup toolchain run stable sh",
        ],
    )
    def test_reject_execution_and_option_hidden_subcommands(
        self, manager: SFWManager, command: str
    ) -> None:
        result = _call(manager, {"action": "run", "command": command})
        assert result["success"] is False
        assert "not allowed" in result.get("stderr", "").lower()

    @pytest.mark.parametrize(
        "command",
        ["npm install express", "uv pip install flask", "cargo fetch"],
    )
    def test_accept_documented_dependency_operations(
        self, manager: SFWManager, mock_popen, command: str
    ) -> None:
        _call(manager, {"action": "run", "command": command})
        assert mock_popen.called

    def test_reject_null_bytes_in_command(self, manager: SFWManager) -> None:
        """Null bytes in command should be rejected."""
        result = _call(manager, {"action": "run", "command": "npm install\x00evil"})
        assert result["success"] is False

    def test_reject_non_string_workdir(self, manager: SFWManager) -> None:
        """Non-string workdir should be rejected."""
        handler = handle_sfw(manager)
        raw = handler({"action": "run", "command": "npm install x", "workdir": [1, 2]})
        result = json.loads(raw)
        assert result["success"] is False
        assert "string" in result["error"].lower()


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

    # --- New tests for ANSI stripping ---

    def test_ansi_stripped_from_blocked(self) -> None:
        """ANSI escape codes should be stripped before parsing."""
        blocked, installed = SFWManager._parse_output("\x1b[31m🔴 blocked malicious-pkg\x1b[0m")
        assert blocked == ["malicious-pkg"]

    def test_ansi_stripped_package_name_clean(self) -> None:
        """Package names should not contain ANSI artifacts."""
        blocked, installed = SFWManager._parse_output("\x1b[32m🟢 installed express\x1b[0m")
        assert installed == ["express"]

    def test_ansi_wrapped_keyword_still_matches(self) -> None:
        """Keywords wrapped in ANSI should still be detected after stripping."""
        blocked, installed = SFWManager._parse_output("\x1b[1mblocked\x1b[0m evil-pkg")
        assert "evil-pkg" in blocked

    # --- List capping ---

    def test_blocked_list_capped(self) -> None:
        """Blocked list should be capped at _MAX_LIST_ENTRIES."""
        output = "\n".join(f"🔴 blocked pkg-{i}" for i in range(100))
        blocked, installed = SFWManager._parse_output(output)
        assert len(blocked) == _MAX_LIST_ENTRIES + 1  # 50 entries + "... and N more"
        assert "and 50 more" in blocked[-1]

    def test_installed_list_capped(self) -> None:
        """Installed list should be capped at _MAX_LIST_ENTRIES."""
        output = "\n".join(f"🟢 installed pkg-{i}" for i in range(100))
        blocked, installed = SFWManager._parse_output(output)
        assert len(installed) == _MAX_LIST_ENTRIES + 1
        assert "and 50 more" in installed[-1]

    def test_list_not_capped_under_limit(self) -> None:
        """Lists under the cap should not be truncated."""
        output = "\n".join(f"🔴 blocked pkg-{i}" for i in range(10))
        blocked, installed = SFWManager._parse_output(output)
        assert len(blocked) == 10


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

    def test_default_search_finds_pnpm_root_shim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pnpm global shim can live directly under the pnpm home."""
        sfw_bin = tmp_path / ".local" / "share" / "pnpm" / "sfw"
        sfw_bin.parent.mkdir(parents=True)
        sfw_bin.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        sfw_bin.chmod(0o755)
        monkeypatch.setenv("HOME", str(tmp_path))

        with patch("hermes_sfw.manager.shutil.which", return_value=None):
            mgr = SFWManager(SFWConfig(sfw_bin="sfw"))
            assert mgr.is_installed()
            assert mgr.sfw_path == str(sfw_bin)

    def test_manager_rechecks_configured_path_after_initial_miss(self, tmp_path: Path) -> None:
        """All manager operations must see a binary installed after construction."""
        sfw_bin = tmp_path / "sfw"
        mgr = SFWManager(SFWConfig(sfw_bin=str(sfw_bin), timeout=5))
        assert not mgr.is_installed()

        sfw_bin.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then\n'
            '  printf "Socket Firewall Free, version 1.15.0\\n"\n'
            "else\n"
            '  printf "🟢 installed express\\n"\n'
            "fi\n",
            encoding="utf-8",
        )
        sfw_bin.chmod(0o755)

        assert mgr.is_installed()
        assert mgr.get_version() == "Socket Firewall Free, version 1.15.0"
        result = mgr.run_command("npm install express")
        assert result.success
        assert result.installed == ["express"]

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


# ---------------------------------------------------------------------------
# Workdir validation edge cases
# ---------------------------------------------------------------------------


class TestWorkdirValidation:
    """Tests for workdir validation edge cases."""

    def test_symlink_loop_raises_value_error(self, tmp_path: Path) -> None:
        """Circular symlinks should raise ValueError, not RuntimeError."""
        # Create a true circular symlink: a -> b -> a
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.symlink_to(b)
        b.symlink_to(a)

        sfw_bin = tmp_path / "sfw"
        sfw_bin.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        sfw_bin.chmod(0o755)

        mgr = SFWManager(SFWConfig(sfw_bin=str(sfw_bin)))
        result = mgr.run_command("npm install express", workdir=str(a))
        assert result.success is False
        assert "invalid" in result.stderr.lower() or "working directory" in result.stderr.lower()

    def test_workdir_tilde_expanded(self, tmp_path: Path, manager: SFWManager, mock_popen) -> None:
        """~ should be expanded in workdir."""
        _call(
            manager,
            {
                "action": "run",
                "command": "npm install express",
                "workdir": "~",
            },
        )
        # Should not fail — ~ resolves to home dir
        assert mock_popen.called


# ---------------------------------------------------------------------------
# Unicode handling
# ---------------------------------------------------------------------------


class TestUnicodeHandling:
    """Tests for non-UTF-8 output handling."""

    def test_binary_output_no_crash(self, tmp_path: Path) -> None:
        """Binary/non-UTF-8 output should not crash the manager."""
        sfw_bin = tmp_path / "sfw"
        sfw_bin.write_bytes(b"#!/bin/bash\nprintf '\\x80\\x81\\x82\\xff\\xfe'\n")
        sfw_bin.chmod(0o755)

        mgr = SFWManager(SFWConfig(sfw_bin=str(sfw_bin)))
        result = mgr.run_command("npm install express")
        # Should not raise UnicodeDecodeError
        assert isinstance(result.stdout, str)


def test_direct_dependency_guard_blocks_terminal_installs() -> None:
    from hermes_sfw import _guard_direct_dependency_operation

    result = _guard_direct_dependency_operation("terminal", {"command": "npm install express"})
    assert result is not None
    assert result["action"] == "block"
    assert "sfw" in result["message"]


def test_direct_dependency_guard_ignores_non_dependency_commands() -> None:
    from hermes_sfw import _guard_direct_dependency_operation

    assert _guard_direct_dependency_operation("terminal", {"command": "npm run build"}) is None
    assert _guard_direct_dependency_operation("terminal", {"command": "git status"}) is None
    assert _guard_direct_dependency_operation("read_file", {"command": "npm install x"}) is None


def test_direct_dependency_guard_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_sfw import _guard_direct_dependency_operation

    monkeypatch.setenv("HERMES_SFW_ENFORCE_DIRECT", "off")
    assert (
        _guard_direct_dependency_operation("terminal", {"command": "pip install requests"}) is None
    )
