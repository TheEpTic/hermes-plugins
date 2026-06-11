"""Tests for ssh_tools.handlers — handler functions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import ssh_tools
from ssh_tools.handlers import handle_ssh_machines, handle_ssh_sessions, handle_ssh_terminal
from ssh_tools.handlers.slash import create_slash_handler
from ssh_tools.models import Machine, Session

from .conftest import _make_manager

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# handle_ssh_terminal
# ---------------------------------------------------------------------------


def test_ssh_terminal_missing_machine(tmp_path: Path) -> None:
    handler = handle_ssh_terminal(_make_manager(tmp_path))
    result = json.loads(handler({"command": "echo hi"}))
    assert result["success"] is False
    assert "machine is required" in result["error"]


def test_ssh_terminal_missing_command(tmp_path: Path) -> None:
    handler = handle_ssh_terminal(_make_manager(tmp_path))
    result = json.loads(handler({"machine": "host1"}))
    assert result["success"] is False
    assert "command is required" in result["error"]


def test_ssh_terminal_nonexistent_machine(tmp_path: Path) -> None:
    handler = handle_ssh_terminal(_make_manager(tmp_path))
    result = json.loads(handler({"machine": "nope", "command": "echo hi"}))
    assert result["success"] is False
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# handle_ssh_machines
# ---------------------------------------------------------------------------


def test_machines_list_empty(tmp_path: Path) -> None:
    handler = handle_ssh_machines(_make_manager(tmp_path))
    result = json.loads(handler({"action": "list"}))
    assert result["success"] is True
    assert result["count"] == 0


def test_machines_list_with_data(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h1", host="1.1.1.1", user="admin", tags=["test"]))
    handler = handle_ssh_machines(mgr)

    result = json.loads(handler({"action": "list"}))
    assert result["success"] is True
    assert result["count"] == 1
    assert "h1" in result["machines"]
    assert result["machines"]["h1"]["host"] == "1.1.1.1"
    assert result["machines"]["h1"]["tags"] == ["test"]


def test_machines_add(tmp_path: Path) -> None:
    handler = handle_ssh_machines(_make_manager(tmp_path))
    result = json.loads(
        handler(
            {
                "action": "add",
                "name": "host1",
                "host": "10.0.0.1",
                "user": "admin",
                "port": 2222,
            }
        )
    )
    assert result["success"] is True
    assert result["machine"]["host"] == "10.0.0.1"


def test_machines_add_missing_fields(tmp_path: Path) -> None:
    handler = handle_ssh_machines(_make_manager(tmp_path))
    result = json.loads(handler({"action": "add"}))
    assert result["success"] is False
    assert "required" in result["error"]


def test_machines_add_missing_host(tmp_path: Path) -> None:
    handler = handle_ssh_machines(_make_manager(tmp_path))
    result = json.loads(handler({"action": "add", "name": "h1"}))
    assert result["success"] is False
    assert "required" in result["error"]


def test_machines_remove(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="host1", host="10.0.0.1"))
    handler = handle_ssh_machines(mgr)

    result = json.loads(handler({"action": "remove", "name": "host1"}))
    assert result["success"] is True


def test_machines_remove_missing_name(tmp_path: Path) -> None:
    handler = handle_ssh_machines(_make_manager(tmp_path))
    result = json.loads(handler({"action": "remove"}))
    assert result["success"] is False
    assert "required" in result["error"]


def test_machines_inspect(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="host1", host="10.0.0.1", aliases=["h1"]))
    handler = handle_ssh_machines(mgr)

    result = json.loads(handler({"action": "inspect", "name": "h1"}))
    assert result["success"] is True
    assert result["name"] == "host1"


def test_machines_inspect_missing_name(tmp_path: Path) -> None:
    handler = handle_ssh_machines(_make_manager(tmp_path))
    result = json.loads(handler({"action": "inspect"}))
    assert result["success"] is False
    assert "required" in result["error"]


def test_machines_inspect_nonexistent(tmp_path: Path) -> None:
    handler = handle_ssh_machines(_make_manager(tmp_path))
    result = json.loads(handler({"action": "inspect", "name": "nope"}))
    assert result["success"] is False
    assert "not found" in result["error"]


def test_machines_unknown_action(tmp_path: Path) -> None:
    handler = handle_ssh_machines(_make_manager(tmp_path))
    result = json.loads(handler({"action": "bogus"}))
    assert result["success"] is False
    assert "Unknown action" in result["error"]


# ---------------------------------------------------------------------------
# handle_ssh_sessions
# ---------------------------------------------------------------------------


def test_sessions_list_empty(tmp_path: Path) -> None:
    handler = handle_ssh_sessions(_make_manager(tmp_path))
    result = json.loads(handler({"action": "list"}))
    assert result["success"] is True
    assert result["count"] == 0


def test_sessions_list_with_data(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))
    handler = handle_ssh_sessions(mgr)

    result = json.loads(handler({"action": "list"}))
    assert result["success"] is True
    assert result["count"] == 1
    assert "s1" in result["sessions"]
    assert "idle_secs" in result["sessions"]["s1"]
    assert "idle_human" in result["sessions"]["s1"]


def test_sessions_kill_missing_id(tmp_path: Path) -> None:
    handler = handle_ssh_sessions(_make_manager(tmp_path))
    result = json.loads(handler({"action": "kill"}))
    assert result["success"] is False
    assert "session_id is required" in result["error"]


def test_sessions_kill_nonexistent(tmp_path: Path) -> None:
    handler = handle_ssh_sessions(_make_manager(tmp_path))
    result = json.loads(handler({"action": "kill", "session_id": "nope"}))
    assert result["success"] is False


def test_sessions_cleanup(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    handler = handle_ssh_sessions(mgr)

    result = json.loads(handler({"action": "cleanup"}))
    assert result["success"] is True
    assert result["cleaned"] == 0


def test_sessions_prune(tmp_path: Path) -> None:
    handler = handle_ssh_sessions(_make_manager(tmp_path))
    result = json.loads(handler({"action": "prune"}))
    assert result["success"] is True
    assert result["pruned"] == 0


def test_sessions_unknown_action(tmp_path: Path) -> None:
    handler = handle_ssh_sessions(_make_manager(tmp_path))
    result = json.loads(handler({"action": "bogus"}))
    assert result["success"] is False
    assert "Unknown action" in result["error"]


# ---------------------------------------------------------------------------
# Slash command handler (create_slash_handler)
# ---------------------------------------------------------------------------


def test_slash_no_args_shows_help(tmp_path: Path) -> None:
    ssh_tools._manager = _make_manager(tmp_path)
    _handle_slash = create_slash_handler(ssh_tools._get_manager)
    result = _handle_slash("")
    assert result is not None
    assert "ssh" in result.lower() or "SSH" in result


def test_slash_help_keyword(tmp_path: Path) -> None:
    ssh_tools._manager = _make_manager(tmp_path)
    _handle_slash = create_slash_handler(ssh_tools._get_manager)
    result = _handle_slash("help")
    assert result is not None
    assert "ssh" in result.lower() or "SSH" in result


def test_slash_test_no_machines(tmp_path: Path) -> None:
    ssh_tools._manager = _make_manager(tmp_path)
    _handle_slash = create_slash_handler(ssh_tools._get_manager)
    result = _handle_slash("test")
    assert result is not None
    assert "No machines" in result


def test_slash_cleanup_no_idle(tmp_path: Path) -> None:
    ssh_tools._manager = _make_manager(tmp_path)
    _handle_slash = create_slash_handler(ssh_tools._get_manager)
    result = _handle_slash("cleanup")
    assert result is not None
    assert "No idle" in result


def test_slash_unknown_machine(tmp_path: Path) -> None:
    ssh_tools._manager = _make_manager(tmp_path)
    _handle_slash = create_slash_handler(ssh_tools._get_manager)
    result = _handle_slash("nonexistent")
    assert result is not None
    assert "not found" in result.lower()


def test_slash_inspect_machine(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(
        Machine(
            name="host1", host="10.0.0.1", user="admin", port=2222, aliases=["h1"], tags=["dev"]
        )
    )
    ssh_tools._manager = mgr
    _handle_slash = create_slash_handler(ssh_tools._get_manager)
    result = _handle_slash("host1")
    assert result is not None
    assert "host1" in result
    assert "10.0.0.1" in result
    assert "admin" in result


def test_slash_inspect_by_alias(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="host1", host="10.0.0.1", aliases=["h1"]))
    ssh_tools._manager = mgr
    _handle_slash = create_slash_handler(ssh_tools._get_manager)
    result = _handle_slash("h1")
    assert result is not None
    assert "host1" in result


# ---------------------------------------------------------------------------
# ssh_terminal -- background, poll, read_output
# ---------------------------------------------------------------------------


def _fake_running_popen() -> MagicMock:
    """Create a mock Popen that is still running."""
    proc = MagicMock()
    proc.pid = 12345
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.poll.return_value = None
    proc.returncode = None
    return proc


def test_ssh_terminal_background(tmp_path: Path) -> None:
    """ssh_terminal with background=True returns session info immediately."""
    from unittest.mock import patch

    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    handler = handle_ssh_terminal(mgr)

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        result = json.loads(handler({"machine": "h", "command": "sleep 10", "background": True}))

    assert result["success"] is True
    assert result["session_id"] is not None
    assert result["pid"] == 12345
    assert result["status"] == "running"


def test_ssh_terminal_poll(tmp_path: Path) -> None:
    """ssh_terminal poll param checks background session status."""
    from unittest.mock import patch

    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    handler = handle_ssh_terminal(mgr)

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        start = json.loads(handler({"machine": "h", "command": "cmd", "background": True}))
    sid = start["session_id"]

    # Still running
    result = json.loads(handler({"machine": "h", "command": "", "poll": sid}))
    assert result["success"] is True
    assert result["running"] is True

    # Now finished
    fake_proc.poll.return_value = 0
    fake_proc.stdout.read.return_value = b"done"
    fake_proc.stderr.read.return_value = b""
    result = json.loads(handler({"machine": "h", "command": "", "poll": sid}))
    assert result["success"] is True
    assert result["running"] is False
    assert result["exit_code"] == 0


def test_ssh_terminal_read_output(tmp_path: Path) -> None:
    """ssh_terminal read_output param reads completed background output."""
    from unittest.mock import patch

    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    handler = handle_ssh_terminal(mgr)

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        start = json.loads(handler({"machine": "h", "command": "cmd", "background": True}))
    sid = start["session_id"]

    fake_proc.poll.return_value = 0
    fake_proc.stdout.read.return_value = b"result data"
    fake_proc.stderr.read.return_value = b""

    out_result = json.loads(handler({"machine": "h", "command": "", "read_output": sid}))
    assert out_result["success"] is True
    assert out_result["stdout"] == "result data"


# ---------------------------------------------------------------------------
# ssh_sessions -- poll action
# ---------------------------------------------------------------------------


def test_sessions_poll(tmp_path: Path) -> None:
    """ssh_sessions poll action checks a background session."""
    from unittest.mock import patch

    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    handler = handle_ssh_sessions(mgr)
    term_handler = handle_ssh_terminal(mgr)

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        start = json.loads(term_handler({"machine": "h", "command": "cmd", "background": True}))
    sid = start["session_id"]

    result = json.loads(handler({"action": "poll", "session_id": sid}))
    assert result["success"] is True
    assert result["running"] is True


def test_sessions_poll_missing_id(tmp_path: Path) -> None:
    handler = handle_ssh_sessions(_make_manager(tmp_path))
    result = json.loads(handler({"action": "poll"}))
    assert result["success"] is False
    assert "session_id is required" in result["error"]


def test_sessions_poll_nonexistent(tmp_path: Path) -> None:
    handler = handle_ssh_sessions(_make_manager(tmp_path))
    result = json.loads(handler({"action": "poll", "session_id": "nope"}))
    assert result["success"] is False


# ---------------------------------------------------------------------------
# ssh_sessions -- read_output action
# ---------------------------------------------------------------------------


def test_sessions_read_output(tmp_path: Path) -> None:
    """ssh_sessions read_output reads from a completed background session."""
    from unittest.mock import patch

    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    handler = handle_ssh_sessions(mgr)
    term_handler = handle_ssh_terminal(mgr)

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        start = json.loads(term_handler({"machine": "h", "command": "cmd", "background": True}))
    sid = start["session_id"]

    fake_proc.poll.return_value = 0
    fake_proc.stdout.read.return_value = b"output here"
    fake_proc.stderr.read.return_value = b""

    result = json.loads(handler({"action": "read_output", "session_id": sid}))
    assert result["success"] is True
    assert result["stdout"] == "output here"


def test_sessions_read_output_missing_id(tmp_path: Path) -> None:
    handler = handle_ssh_sessions(_make_manager(tmp_path))
    result = json.loads(handler({"action": "read_output"}))
    assert result["success"] is False
    assert "session_id is required" in result["error"]


# ---- Bug fix: poll/read_output without machine/command ----


def test_ssh_terminal_poll_without_machine(tmp_path: Path) -> None:
    """poll should work without machine/command"""
    from ssh_tools.handlers.terminal import handle_ssh_terminal

    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    with patch("ssh_tools.manager.subprocess.Popen") as mock_popen:
        proc = MagicMock()
        proc.pid = 12345
        proc.poll.return_value = None
        mock_popen.return_value = proc
        bg = json.loads(
            handle_ssh_terminal(mgr)({"machine": "h", "command": "sleep 99", "background": True})
        )
        sid = bg["session_id"]
    handler_fn = handle_ssh_terminal(mgr)
    result = json.loads(handler_fn({"poll": sid}))
    assert result["success"] is True
    assert result["running"] is True


def test_ssh_terminal_read_output_without_machine(tmp_path: Path) -> None:
    """read_output should work without machine/command"""
    from ssh_tools.handlers.terminal import handle_ssh_terminal

    mgr = _make_manager(tmp_path)
    result = json.loads(handle_ssh_terminal(mgr)({"read_output": "nonexistent"}))
    assert result["success"] is False
    assert "No background process" in result["error"]


# ---- Hermes approval integration ----


def test_approval_not_available_commands_pass(tmp_path: Path) -> None:
    """When Hermes approval system is not installed, commands execute normally."""
    from unittest.mock import patch as mock_patch

    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    handler = handle_ssh_terminal(mgr)
    with (
        mock_patch("ssh_tools.handlers.terminal._check_approval", return_value=None),
        mock_patch("ssh_tools.manager.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = json.loads(handler({"machine": "h", "command": "echo test"}))
        assert result["success"] is True


def test_approval_denies_dangerous_command(tmp_path: Path) -> None:
    """When approval system denies, command is not executed."""
    from unittest.mock import patch as mock_patch

    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    handler = handle_ssh_terminal(mgr)
    deny_result = {"approved": False, "message": "BLOCKED: recursive delete flagged"}
    with (
        mock_patch("ssh_tools.handlers.terminal._check_approval", return_value=deny_result),
        mock_patch("ssh_tools.manager.subprocess.run") as mock_run,
    ):
        result = json.loads(handler({"machine": "h", "command": "rm -rf /home"}))
        assert result["success"] is False
        assert "BLOCKED" in result["error"]
        mock_run.assert_not_called()


def test_approval_allows_safe_command(tmp_path: Path) -> None:
    """When approval system approves, command executes normally."""
    from unittest.mock import patch as mock_patch

    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    handler = handle_ssh_terminal(mgr)
    approve_result = {"approved": True, "message": None}
    with (
        mock_patch("ssh_tools.handlers.terminal._check_approval", return_value=approve_result),
        mock_patch("ssh_tools.manager.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = json.loads(handler({"machine": "h", "command": "ls"}))
        assert result["success"] is True
        mock_run.assert_called()


def test_approval_none_passes_through(tmp_path: Path) -> None:
    """When _check_approval returns None (approved), command executes."""
    from unittest.mock import patch as mock_patch

    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    handler = handle_ssh_terminal(mgr)
    with (
        mock_patch("ssh_tools.handlers.terminal._check_approval", return_value=None),
        mock_patch("ssh_tools.manager.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = json.loads(handler({"machine": "h", "command": "whoami"}))
        assert result["success"] is True


def test_approval_does_not_block_poll(tmp_path: Path) -> None:
    """Poll/read_output should bypass approval checks entirely."""
    from unittest.mock import patch as mock_patch

    mgr = _make_manager(tmp_path)
    handler = handle_ssh_terminal(mgr)
    with mock_patch("ssh_tools.handlers.terminal._check_approval") as mock_check:
        result = json.loads(handler({"read_output": "nonexistent"}))
        assert result["success"] is False
        mock_check.assert_not_called()
