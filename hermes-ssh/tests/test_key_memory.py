"""Regression tests for SSH-1 (host key trust remediation) and SSH-2 (per-host key memory)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from ssh_tools.handlers import handle_ssh_terminal
from ssh_tools.models import Machine

from .conftest import _make_manager

_HOST_KEY = "Host key verification failed."
_DENIED = "Permission denied (publickey)."


def _result(code: int, stderr: str = "", stdout: str = "ok\n") -> MagicMock:
    return MagicMock(returncode=code, stdout=stdout, stderr=stderr)


def _with_machine(tmp_path: Path, key: str = "") -> Any:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1", key=key))
    return mgr


def _via_handler(mgr: Any) -> dict:
    return json.loads(handle_ssh_terminal(mgr)({"machine": "h", "command": "echo hi"}))


def _polled_bg(mgr: Any, code: int, stderr: bytes) -> dict:
    proc = MagicMock(pid=12345, returncode=None)
    proc.poll.return_value = None
    with patch("ssh_tools.exec.subprocess.Popen", return_value=proc):
        bg = mgr.run_command("h", "cmd", background=True)
    proc.poll.return_value = proc.returncode = code
    proc.stdout.read.return_value = b"done\n" if code == 0 else b""
    proc.stderr.read.return_value = stderr
    return mgr.poll_session(bg["session_id"])


@pytest.mark.parametrize("entrypoint", ["handler", "run_command", "test_machine", "background"])
def test_host_key_failure_includes_remediation(tmp_path: Path, entrypoint: str) -> None:
    """Every path that can hit a new host must tell the agent how to fix trust."""
    mgr = _with_machine(tmp_path)
    stub = _result(255, _HOST_KEY)
    with patch("ssh_tools.exec.subprocess.run", return_value=stub):
        if entrypoint == "handler":
            result = _via_handler(mgr)
        elif entrypoint == "run_command":
            result = mgr.run_command("h", "echo hi")
        elif entrypoint == "test_machine":
            result = mgr.test_machine("h")
        else:
            result = _polled_bg(_with_machine(tmp_path / "bg"), 255, _HOST_KEY.encode())
    assert result["success"] is False
    assert "accept-new" in result["error"] and "ssh-keyscan" in result["error"]
    assert "keys_attempted" not in result  # not a key-auth failure
    if entrypoint == "run_command":
        assert result["exit_code"] == 255 and "1.1.1.1" in result["error"]


def test_non_host_key_failure_not_mislabeled(tmp_path: Path) -> None:
    mgr = _with_machine(tmp_path)
    with patch("ssh_tools.exec.subprocess.run", return_value=_result(255, _DENIED)):
        result = mgr.run_command("h", "echo hi")
    assert "accept-new" not in result["stderr"] and "accept-new" not in result.get("error", "")


@pytest.mark.parametrize("key,expected", [("~/.ssh/custom_key", "~/.ssh/custom_key"), ("", "")])
def test_successful_auth_persists_key(tmp_path: Path, key: str, expected: str) -> None:
    mgr = _with_machine(tmp_path, key)
    with patch("ssh_tools.exec.subprocess.run", return_value=_result(0)):
        result = mgr.run_command("h", "echo hi")
    assert result["key_used"] == expected
    reloaded = _make_manager(tmp_path).get_machine("h")  # durable across restarts
    assert reloaded is not None and reloaded.key == expected


def test_failed_auth_not_remembered(tmp_path: Path) -> None:
    mgr = _with_machine(tmp_path, "~/.ssh/wrong_key")
    with patch("ssh_tools.exec.subprocess.run", return_value=_result(255, _DENIED)):
        result = mgr.run_command("h", "echo hi")
    assert result["success"] is False and "key_used" not in result
    assert mgr.get_machine("h").key == "~/.ssh/wrong_key"  # stored key untouched


@pytest.mark.parametrize(
    "key,sync_expected",
    [("~/.ssh/id_ed25519", ["~/.ssh/id_ed25519"]), ("", ["~/.ssh/id_ed25519", "~/.ssh/id_rsa"])],
)
def test_failed_auth_reports_attempted_keys(tmp_path: Path, key: str, sync_expected: list) -> None:
    mgr = _with_machine(tmp_path, key)
    with patch("ssh_tools.exec.subprocess.run", return_value=_result(255, _DENIED)):
        result = mgr.run_command("h", "echo hi")
    assert result["success"] is False and result["keys_attempted"] == sync_expected
    assert _polled_bg(
        _with_machine(tmp_path / "bg", key or "~/.ssh/bg_key"), 255, _DENIED.encode()
    )["keys_attempted"] == (sync_expected if key else ["~/.ssh/bg_key"])


def test_failed_auth_reported_via_handler(tmp_path: Path) -> None:
    mgr = _with_machine(tmp_path, "~/.ssh/id_rsa")
    with patch("ssh_tools.exec.subprocess.run", return_value=_result(255, _DENIED)):
        result = _via_handler(mgr)
    assert result["keys_attempted"] == ["~/.ssh/id_rsa"]


def test_background_success_reports_key_used(tmp_path: Path) -> None:
    result = _polled_bg(_with_machine(tmp_path, "~/.ssh/bg_key"), 0, b"")
    assert result["success"] is True and result["key_used"] == "~/.ssh/bg_key"
