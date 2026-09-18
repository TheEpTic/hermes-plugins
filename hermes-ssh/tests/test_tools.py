"""Tests for ssh_tools.handlers — handler functions."""

from __future__ import annotations

import getpass
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
import ssh_tools
from ssh_tools.handlers import handle_ssh_machines, handle_ssh_sessions, handle_ssh_terminal
from ssh_tools.handlers.slash import create_slash_handler
from ssh_tools.models import Machine, Session
from ssh_tools.schemas import SSH_MACHINES_SCHEMA

from .conftest import _make_manager

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _call(handler: Callable[[dict[str, Any]], str], params: dict[str, Any]) -> dict:
    return json.loads(handler(params))


def _slash(tmp_path: Path) -> Callable[[str], str | None]:
    ssh_tools._manager = _make_manager(tmp_path)
    return create_slash_handler(ssh_tools._get_manager)


def _fake_running_popen() -> MagicMock:
    return MagicMock(
        pid=12345,
        stdout=MagicMock(),
        stderr=MagicMock(),
        returncode=None,
        **{"poll.return_value": None},
    )


def _add_h(tmp_path: Path):
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    return mgr


def _ran_via_terminal(mgr: Any, approval: Any, command: str) -> tuple[dict, Any]:
    """Run one sync command through the terminal handler with faked approval+ssh."""
    with (
        patch("ssh_tools.handlers.terminal.check_approval", return_value=approval),
        patch("ssh_tools.exec.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = _call(handle_ssh_terminal(mgr), {"machine": "h", "command": command})
    return result, mock_run


@pytest.mark.parametrize(
    "params,fragment",
    [
        ({"command": "echo hi"}, "machine is required"),
        ({"machine": "host1"}, "command is required"),
        ({"machine": "nope", "command": "echo hi"}, "not found"),
        ({"machine": 123, "command": "echo hi"}, "machine"),
        ({"machine": "h", "command": ""}, "command"),
        ({"machine": "h", "command": "echo hi", "background": "yes"}, "background"),
        ({"poll": "ssh_h_12345678"}, "machine"),  # poll is sessions-only
    ],
)
def test_terminal_validation(tmp_path: Path, params: dict, fragment: str) -> None:
    result = _call(handle_ssh_terminal(_make_manager(tmp_path)), params)
    assert result["success"] is False and fragment in result["error"]


def test_terminal_background(tmp_path: Path) -> None:
    mgr = _add_h(tmp_path)
    with patch("ssh_tools.exec.subprocess.Popen", return_value=_fake_running_popen()):
        result = _call(
            handle_ssh_terminal(mgr),
            {"machine": "h", "command": "sleep 10", "background": True},
        )
    assert result["success"] is True and result["pid"] == 12345
    assert result["status"] == "running" and result["session_id"] is not None


def test_machines_add_list_remove(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    assert _call(handle_ssh_machines(mgr), {"action": "list"})["count"] == 0
    result = _call(
        handle_ssh_machines(mgr),
        {"action": "add", "name": "host1", "host": "10.0.0.1", "user": "admin", "port": 2222},
    )
    assert result["success"] is True and result["machine"]["host"] == "10.0.0.1"
    listed = _call(handle_ssh_machines(mgr), {"action": "list"})
    assert listed["count"] == 1 and listed["machines"]["host1"]["host"] == "10.0.0.1"
    assert _call(handle_ssh_machines(mgr), {"action": "remove", "name": "host1"})["success"] is True


def test_machines_add_defaults_local_user(tmp_path: Path) -> None:
    result = _call(
        handle_ssh_machines(_make_manager(tmp_path)),
        {"action": "add", "name": "host1", "host": "10.0.0.1"},
    )
    assert result["machine"]["user"] == getpass.getuser()


@pytest.mark.parametrize(
    "params,fragment",
    [
        ({"action": "add"}, "required"),
        ({"action": "add", "name": "h1"}, "required"),
        ({"action": "add", "name": "h1", "host": "bad host"}, "Host"),
        ({"action": "add", "name": "h1", "host": "1.1.1.1", "port": 0}, "Port"),
        ({"action": "remove"}, "required"),
        ({"action": "inspect"}, "required"),
        ({"action": "inspect", "name": "nope"}, "not found"),
        ({"action": "bogus"}, "Unknown action"),
    ],
)
def test_machines_errors(tmp_path: Path, params: dict, fragment: str) -> None:
    result = _call(handle_ssh_machines(_make_manager(tmp_path)), params)
    assert result["success"] is False and fragment in result["error"]


def test_machines_inspect_by_alias(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="host1", host="10.0.0.1", aliases=["h1"]))
    result = _call(handle_ssh_machines(mgr), {"action": "inspect", "name": "h1"})
    assert result["success"] is True and result["name"] == "host1"


def test_machines_schema_no_root_default() -> None:
    user = SSH_MACHINES_SCHEMA["parameters"]["properties"]["user"]
    assert "default" not in user and "current local user" in user["description"]


def test_sessions_list(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    result = _call(handle_ssh_sessions(mgr), {"action": "list"})
    assert result["success"] is True and result["count"] == 0 and "idle_secs" not in result
    mgr.register_session(Session(id="s1", machine="host1"))
    result = _call(handle_ssh_sessions(mgr), {"action": "list"})
    assert result["count"] == 1
    entry = result["sessions"]["s1"]
    assert entry["idle_human"] == f"{entry['idle_secs']}s"


def test_sessions_cleanup_shortcut(tmp_path: Path) -> None:
    assert _call(handle_ssh_sessions(_make_manager(tmp_path)), {"action": "cleanup"}) == {
        "success": True,
        "cleaned": 0,
        "details": [],
    }


@pytest.mark.parametrize(
    "params,fragment",
    [
        ({"action": "kill"}, "session_id is required"),
        ({"action": "poll"}, "session_id is required"),
        ({"action": "read_output"}, "session_id is required"),
        ({"action": "kill", "session_id": "nope"}, "not found"),
        ({"action": "poll", "session_id": "nope"}, "No background process"),
        ({"action": "bogus"}, "Unknown action"),
    ],
)
def test_sessions_errors(tmp_path: Path, params: dict, fragment: str) -> None:
    result = _call(handle_ssh_sessions(_make_manager(tmp_path)), params)
    assert result["success"] is False and fragment in result["error"]


def _bg_manager(tmp_path: Path):
    mgr = _add_h(tmp_path)
    proc = _fake_running_popen()
    with patch("ssh_tools.exec.subprocess.Popen", return_value=proc):
        start = _call(
            handle_ssh_terminal(mgr),
            {"machine": "h", "command": "cmd", "background": True},
        )
    return mgr, start["session_id"], proc


def test_sessions_poll_and_read_output(tmp_path: Path) -> None:
    """ssh_sessions poll checks running; read_output reads completed sessions."""
    mgr, sid, proc = _bg_manager(tmp_path)
    poll = _call(handle_ssh_sessions(mgr), {"action": "poll", "session_id": sid})
    assert poll["success"] is True and poll["running"] is True
    proc.poll.return_value = 0
    proc.stdout.read.return_value = b"output here"
    proc.stderr.read.return_value = b""
    out = _call(handle_ssh_sessions(mgr), {"action": "read_output", "session_id": sid})
    assert out["success"] is True and out["stdout"] == "output here"


def test_sessions_bypass_approval(tmp_path: Path) -> None:
    with patch("ssh_tools.handlers.terminal.check_approval") as mock_check:
        _call(
            handle_ssh_sessions(_make_manager(tmp_path)),
            {"action": "read_output", "session_id": "nonexistent"},
        )
    mock_check.assert_not_called()


@pytest.mark.parametrize("approval", [None, {"approved": True, "message": None}])
def test_approval_allows(tmp_path: Path, approval: Any) -> None:
    """None or approved approval passes the command through."""
    mgr = _add_h(tmp_path)
    result, mock_run = _ran_via_terminal(mgr, approval, "ls")
    assert result["success"] is True
    mock_run.assert_called()


def test_approval_denies_without_executing(tmp_path: Path) -> None:
    mgr = _add_h(tmp_path)
    deny = {"approved": False, "message": "BLOCKED: recursive delete flagged"}
    result, mock_run = _ran_via_terminal(mgr, deny, "rm -rf /home")
    assert result["success"] is False and "BLOCKED" in result["error"]
    mock_run.assert_not_called()


def test_approval_mode_off_bypasses(tmp_path: Path) -> None:
    mgr = _add_h(tmp_path)
    danger = lambda *_a, **_k: {"status": "approval_required"}  # noqa: E731
    with (
        patch("ssh_tools.approval._approval_functions", return_value=(danger, lambda: "off")),
        patch("ssh_tools.exec.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = _call(handle_ssh_terminal(mgr), {"machine": "h", "command": "rm -rf /tmp/test"})
    assert result["success"] is True
    mock_run.assert_called_once()


def test_approval_required_names_commands(tmp_path: Path) -> None:
    """gateway wording tells the user to reply with /approve or /deny."""
    mgr = _add_h(tmp_path)
    waiting = {
        "approved": False,
        "status": "approval_required",
        "description": "recursive delete",
        "message": "approval required: recursive delete. the user must reply with "
        "/approve or /deny.",
    }
    with (
        patch("ssh_tools.handlers.terminal.check_approval", return_value=waiting),
        patch("ssh_tools.exec.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = _call(handle_ssh_terminal(mgr), {"machine": "h", "command": "rm -rf /tmp/test"})
    assert "/approve" in result["error"] and "/deny" in result["error"]
    mock_run.assert_not_called()
    # unknown names get their own error, distinct from unknown slash subcommands
    assert "not found" in _slash(tmp_path)("nonexistent").lower()


@pytest.mark.parametrize(
    "args,fragment",
    [("", "ssh"), ("help", "ssh"), ("test", "No machines"), ("cleanup", "No idle")],
)
def test_slash_builtins(tmp_path: Path, args: str, fragment: str) -> None:
    result = _slash(tmp_path)(args)
    assert result is not None
    assert fragment in result or fragment in result.lower()


@pytest.mark.parametrize(
    "name,fields",
    [
        ("host1", ["host1", "10.0.0.1", "admin"]),
        ("h1", ["host1"]),  # alias resolves
    ],
)
def test_slash_inspect(tmp_path: Path, name: str, fields: list) -> None:
    mgr = _add_h(tmp_path)
    mgr.add_machine(
        Machine(
            name="host1",
            host="10.0.0.1",
            user="admin",
            port=2222,
            aliases=["h1"],
            tags=["dev"],
        )
    )
    ssh_tools._manager = mgr
    result = create_slash_handler(ssh_tools._get_manager)(name)
    assert result is not None
    assert all(f in result for f in fields)
