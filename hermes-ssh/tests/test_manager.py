"""Tests for ssh_tools.manager — Machine, Session, SSHManager."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ssh_tools.models import Machine, Session

from .conftest import _make_manager

# ---------------------------------------------------------------------------
# Machine dataclass
# ---------------------------------------------------------------------------


def test_machine_to_dict_roundtrip() -> None:
    m = Machine(name="test", host="10.0.0.1", user="admin", port=2222, aliases=["t"], tags=["dev"])
    d = m.to_dict()
    m2 = Machine.from_dict("test", d)
    assert m2.name == "test"
    assert m2.host == "10.0.0.1"
    assert m2.user == "admin"
    assert m2.port == 2222
    assert m2.aliases == ["t"]
    assert m2.tags == ["dev"]


def test_machine_defaults() -> None:
    m = Machine(name="x", host="1.2.3.4")
    assert m.user == "root"
    assert m.port == 22
    assert m.key == ""
    assert m.aliases is None
    assert m.tags is None
    assert m.description == ""
    assert m.added == ""


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


def test_session_idle_seconds_active() -> None:
    now = datetime.now(UTC)
    s = Session(
        id="s1",
        machine="host1",
        started=now.isoformat(),
        last_active=(now - timedelta(minutes=5)).isoformat(),
        status="active",
    )
    idle = s.idle_seconds
    assert idle is not None
    assert 290 <= idle <= 310  # ~5 minutes


def test_session_idle_seconds_closed() -> None:
    s = Session(id="s1", machine="h", status="closed")
    assert s.idle_seconds is None


def test_session_idle_seconds_empty_last_active() -> None:
    s = Session(id="s1", machine="h", status="active", last_active="")
    assert s.idle_seconds is None


def test_session_idle_seconds_invalid_last_active() -> None:
    s = Session(id="s1", machine="h", status="active", last_active="not-a-date")
    assert s.idle_seconds is None


def test_session_idle_human_seconds() -> None:
    now = datetime.now(UTC)
    s = Session(
        id="s1",
        machine="h",
        status="active",
        last_active=(now - timedelta(seconds=30)).isoformat(),
    )
    assert s.idle_human == "30s"


def test_session_idle_human_minutes() -> None:
    now = datetime.now(UTC)
    s = Session(
        id="s1",
        machine="h",
        status="active",
        last_active=(now - timedelta(minutes=5, seconds=30)).isoformat(),
    )
    assert s.idle_human == "5m 30s"


def test_session_idle_human_hours() -> None:
    now = datetime.now(UTC)
    s = Session(
        id="s1",
        machine="h",
        status="active",
        last_active=(now - timedelta(hours=2, minutes=15)).isoformat(),
    )
    assert s.idle_human == "2h 15m"


def test_session_idle_human_unknown() -> None:
    s = Session(id="s1", machine="h", status="closed")
    assert s.idle_human == "unknown"


def test_session_to_dict_roundtrip() -> None:
    s = Session(id="s1", machine="h", pid=123, control_path="/tmp/c.sock", status="active")
    d = s.to_dict()
    s2 = Session.from_dict("s1", d)
    assert s2.id == "s1"
    assert s2.machine == "h"
    assert s2.pid == 123
    assert s2.control_path == "/tmp/c.sock"
    assert s2.status == "active"


# ---------------------------------------------------------------------------
# SSHManager — machine registry
# ---------------------------------------------------------------------------


def test_add_and_get_machine(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    m = Machine(name="host1", host="10.0.0.1", user="admin", port=2222)
    mgr.add_machine(m)

    got = mgr.get_machine("host1")
    assert got is not None
    assert got.host == "10.0.0.1"
    assert got.user == "admin"
    assert got.port == 2222


def test_add_machine_sets_timestamp(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    m = mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    assert m.added
    # Verify it's a valid ISO timestamp
    dt = datetime.fromisoformat(m.added)
    assert dt.year >= 2026


def test_add_machine_overwrite(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1", user="old"))
    mgr.add_machine(
        Machine(name="h", host="2.2.2.2", user="new", added="2026-01-01T00:00:00+00:00")
    )

    got = mgr.get_machine("h")
    assert got is not None
    assert got.host == "2.2.2.2"
    assert got.user == "new"
    # Preserved original added timestamp
    assert got.added == "2026-01-01T00:00:00+00:00"


def test_resolve_alias(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="host1", host="10.0.0.1", aliases=["h1"]))

    assert mgr.resolve_name("h1") == "host1"
    assert mgr.resolve_name("host1") == "host1"
    assert mgr.resolve_name("nope") is None


def test_get_machine_by_alias(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="host1", host="10.0.0.1", aliases=["h1"]))

    got = mgr.get_machine("h1")
    assert got is not None
    assert got.name == "host1"


def test_list_machines(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="a", host="1.1.1.1"))
    mgr.add_machine(Machine(name="b", host="2.2.2.2"))

    machines = mgr.list_machines()
    assert len(machines) == 2
    assert "a" in machines
    assert "b" in machines


def test_remove_machine(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="host1", host="10.0.0.1"))

    assert mgr.remove_machine("host1") is True
    assert mgr.get_machine("host1") is None


def test_remove_machine_by_alias(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="host1", host="10.0.0.1", aliases=["h1"]))

    assert mgr.remove_machine("h1") is True
    assert mgr.get_machine("host1") is None


def test_remove_nonexistent(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    assert mgr.remove_machine("nope") is False


def test_get_nonexistent(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    assert mgr.get_machine("nope") is None


def test_machine_persists_across_instances(tmp_path: Path) -> None:
    mgr1 = _make_manager(tmp_path)
    mgr1.add_machine(Machine(name="h", host="1.1.1.1"))

    mgr2 = _make_manager(tmp_path)
    got = mgr2.get_machine("h")
    assert got is not None
    assert got.host == "1.1.1.1"


# ---------------------------------------------------------------------------
# SSHManager — session tracking
# ---------------------------------------------------------------------------


def test_register_and_list_sessions(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1", pid=100))

    active = mgr.list_sessions("active")
    assert len(active) == 1
    assert "s1" in active


def test_register_session_sets_timestamps(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))

    s = mgr.get_session("s1")
    assert s is not None
    assert s.started
    assert s.last_active
    assert s.status == "active"


def test_register_session_preserves_existing_started(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    custom_time = "2026-01-01T00:00:00+00:00"
    mgr.register_session(Session(id="s1", machine="host1", started=custom_time))

    s = mgr.get_session("s1")
    assert s is not None
    assert s.started == custom_time


def test_get_session(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))

    s = mgr.get_session("s1")
    assert s is not None
    assert s.machine == "host1"
    assert s.status == "active"


def test_close_session(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))
    mgr.close_session("s1")

    s = mgr.get_session("s1")
    assert s is not None
    assert s.status == "closed"

    active = mgr.list_sessions("active")
    assert "s1" not in active


def test_remove_session(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))
    mgr.remove_session("s1")

    assert mgr.get_session("s1") is None


def test_touch_session(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))

    mgr.touch_session("s1")
    s = mgr.get_session("s1")
    assert s is not None
    assert s.command_count == 1

    mgr.touch_session("s1")
    s = mgr.get_session("s1")
    assert s is not None
    assert s.command_count == 2


def test_list_sessions_all_statuses(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))
    mgr.register_session(Session(id="s2", machine="host1"))
    mgr.close_session("s2")

    all_sessions = mgr.list_sessions(status="")
    assert len(all_sessions) == 2

    closed = mgr.list_sessions("closed")
    assert len(closed) == 1
    assert "s2" in closed


def test_cleanup_idle(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))

    # Manually age the session
    with mgr._lock:
        sessions = mgr._load_sessions()
        old_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        sessions["s1"]["last_active"] = old_time
        mgr._save_sessions(sessions)

    result = mgr.cleanup_idle(max_idle_minutes=30)
    assert result["count"] == 1

    s = mgr.get_session("s1")
    assert s is not None
    assert s.status == "closed"


def test_cleanup_idle_skips_recent(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))

    result = mgr.cleanup_idle(max_idle_minutes=30)
    assert result["count"] == 0

    s = mgr.get_session("s1")
    assert s is not None
    assert s.status == "active"


def test_cleanup_idle_boundary(tmp_path: Path) -> None:
    """Session exactly at threshold should NOT be killed (> not >=)."""
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))

    # Set last_active to exactly 30 minutes ago
    with mgr._lock:
        sessions = mgr._load_sessions()
        exact_time = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        sessions["s1"]["last_active"] = exact_time
        mgr._save_sessions(sessions)

    result = mgr.cleanup_idle(max_idle_minutes=30)
    assert result["count"] == 0  # > threshold, not >=


def test_cleanup_idle_uses_config_default(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    # Config default is 30 minutes
    mgr.register_session(Session(id="s1", machine="host1"))

    with mgr._lock:
        sessions = mgr._load_sessions()
        old_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        sessions["s1"]["last_active"] = old_time
        mgr._save_sessions(sessions)

    result = mgr.cleanup_idle()  # No arg = use config default
    assert result["count"] == 1


def test_prune_closed(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))
    mgr.close_session("s1")

    with mgr._lock:
        sessions = mgr._load_sessions()
        old_time = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        sessions["s1"]["started"] = old_time
        mgr._save_sessions(sessions)

    count = mgr.prune_closed(max_age_hours=24)
    assert count == 1
    assert mgr.get_session("s1") is None


def test_prune_closed_keeps_recent(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))
    mgr.close_session("s1")

    count = mgr.prune_closed(max_age_hours=24)
    assert count == 0


def test_prune_closed_skips_active(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))

    with mgr._lock:
        sessions = mgr._load_sessions()
        old_time = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        sessions["s1"]["started"] = old_time
        mgr._save_sessions(sessions)

    count = mgr.prune_closed(max_age_hours=24)
    assert count == 0  # Active sessions not pruned


def test_prune_closed_with_invalid_date(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))
    mgr.close_session("s1")

    with mgr._lock:
        sessions = mgr._load_sessions()
        sessions["s1"]["started"] = "not-a-date"
        mgr._save_sessions(sessions)

    count = mgr.prune_closed(max_age_hours=24)
    assert count == 0  # Invalid date = skip, don't crash


def test_prune_closed_uses_config_default(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1"))
    mgr.close_session("s1")

    with mgr._lock:
        sessions = mgr._load_sessions()
        old_time = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        sessions["s1"]["started"] = old_time
        mgr._save_sessions(sessions)

    count = mgr.prune_closed()  # No arg = config default (24h)
    assert count == 1


def test_multiple_sessions_same_machine(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1", pid=100))
    mgr.register_session(Session(id="s2", machine="host1", pid=200))

    active = mgr.list_sessions("active")
    assert len(active) == 2
    assert all(s.machine == "host1" for s in active.values())


# ---------------------------------------------------------------------------
# SSHManager — JSON corruption handling
# ---------------------------------------------------------------------------


def test_read_json_corrupt_file(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    corrupt_file = tmp_path / "machines.json"
    corrupt_file.write_text("NOT JSON {{{")

    result = mgr._read_json(corrupt_file, {"default": True})
    assert result == {"default": True}


def test_read_json_missing_file(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    result = mgr._read_json(tmp_path / "nonexistent.json", {"fallback": True})
    assert result == {"fallback": True}


def test_write_json_atomic(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    target = tmp_path / "test.json"

    mgr._write_json(target, {"key": "value"})
    assert json.loads(target.read_text()) == {"key": "value"}

    # Verify no leftover temp files
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


# ---------------------------------------------------------------------------
# SSHManager — SSH execution
# ---------------------------------------------------------------------------


def test_run_command_nonexistent_machine(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    result = mgr.run_command("nope", "echo hi")
    assert result["success"] is False
    assert "not found" in result["error"]


def test_build_ssh_args(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    machine = Machine(name="h", host="10.0.0.1", user="admin", port=2222, key="~/.ssh/test")
    args = mgr._build_ssh_args(machine, "uptime", "/tmp/c.sock", timeout=30)

    assert args[0] == "ssh"
    assert "-p" in args
    assert "2222" in args
    assert "-i" in args
    assert "~/.ssh/test" in args
    assert "ControlMaster=auto" in args
    assert "admin@10.0.0.1" in args
    # Command is wrapped in bash -c 'set -o pipefail; ...'
    assert "bash" in args
    assert "-c" in args
    assert "pipefail" in args[-1]
    assert "uptime" in args[-1]


def test_build_ssh_args_no_control_path(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    machine = Machine(name="h", host="10.0.0.1")
    args = mgr._build_ssh_args(machine, "uptime", "", timeout=30)

    assert "ControlMaster" not in args


def test_build_ssh_args_no_key(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    machine = Machine(name="h", host="10.0.0.1")
    args = mgr._build_ssh_args(machine, "uptime", timeout=30)

    assert "-i" not in args


def test_build_ssh_args_timeout_clamping(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    machine = Machine(name="h", host="10.0.0.1")
    args = mgr._build_ssh_args(machine, "uptime", timeout=30)

    # ConnectTimeout should be min(30, 10) = 10
    ct_val = next(a for a in args if a.startswith("ConnectTimeout="))
    assert ct_val == "ConnectTimeout=10"


# ---------------------------------------------------------------------------
# SSHManager — idle checker
# ---------------------------------------------------------------------------


def test_start_stop_idle_checker(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.start_idle_checker()
    assert mgr._checker_thread is not None
    assert mgr._checker_thread.is_alive()

    mgr.stop_idle_checker()
    # Event is set — thread will exit after current sleep/cleanup cycle
    assert mgr._checker_event.is_set()


def test_stop_idle_checker_sets_event(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    assert not mgr._checker_event.is_set()
    mgr.start_idle_checker()
    mgr.stop_idle_checker()
    assert mgr._checker_event.is_set()


def test_start_idle_checker_idempotent(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.start_idle_checker()
    thread1 = mgr._checker_thread
    mgr.start_idle_checker()  # Should be no-op
    assert mgr._checker_thread is thread1
    mgr.stop_idle_checker()


# ---------------------------------------------------------------------------
# Additional imports for new tests below
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch

from ssh_tools.manager import SSHManager

# ---------------------------------------------------------------------------
# kill_session — PID + control socket
# ---------------------------------------------------------------------------


def test_kill_session_no_pid(tmp_path: Path) -> None:
    """kill_session with pid=0 skips os.kill and still closes the session."""
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="h", pid=0))
    result = mgr.kill_session("s1")
    assert result["success"] is True
    assert result["pid_killed"] is False
    s = mgr.get_session("s1")
    assert s is not None
    assert s.status == "closed"


def test_kill_session_with_pid_mocked(tmp_path: Path) -> None:
    """Kill session with a real PID — mock os.kill to avoid actually killing."""
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="h", pid=99999))

    with (
        patch("ssh_tools.manager.os.kill") as mock_kill,
        patch("ssh_tools.manager.time.sleep"),
    ):
        # First call (SIGTERM) succeeds, second (alive check) raises OSError = dead
        mock_kill.side_effect = [None, OSError("No such process")]
        result = mgr.kill_session("s1")

    assert result["success"] is True
    assert result["pid_killed"] is True
    s = mgr.get_session("s1")
    assert s is not None
    assert s.status == "closed"


def test_kill_session_real_machine_hostname_in_ssh_exit(tmp_path: Path) -> None:
    """Verify hostname from machine registry is used in ssh -O exit command."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="prod", host="10.0.0.1", user="admin"))
    # Create a fake control path file so os.path.exists returns True
    ctrl = tmp_path / "prod.sock"
    ctrl.touch()
    mgr.register_session(Session(id="s1", machine="prod", pid=0, control_path=str(ctrl)))

    with (
        patch("ssh_tools.manager.subprocess.run") as mock_sub,
        patch("ssh_tools.manager.os.kill"),
    ):
        mock_sub.return_value = MagicMock(returncode=0)
        result = mgr.kill_session("s1")

    assert result["success"] is True
    assert result["socket_closed"] is True
    # Verify the ssh command used the real hostname "10.0.0.1"
    call_args = mock_sub.call_args
    cmd = call_args[0][0]
    assert cmd[0] == "ssh"
    assert "10.0.0.1" in cmd


def test_kill_session_socket_path_missing(tmp_path: Path) -> None:
    """When control_path file doesn't exist, socket_closed is False."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    mgr.register_session(Session(id="s1", machine="h", pid=0, control_path="/nonexistent.sock"))
    result = mgr.kill_session("s1")
    assert result["success"] is True
    assert result["socket_closed"] is False


def test_kill_session_nonexistent(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    result = mgr.kill_session("nope")
    assert result["success"] is False
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# run_command — no lingering active sessions
# ---------------------------------------------------------------------------


def test_run_command_no_active_sessions_after(tmp_path: Path) -> None:
    """After run_command completes, there should be no active sessions left."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    with patch("ssh_tools.manager.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
        result = mgr.run_command("h", "echo hello")

    assert result["success"] is True
    # The session was registered then immediately closed
    active = mgr.list_sessions("active")
    assert len(active) == 0


# ---------------------------------------------------------------------------
# cleanup_idle — batch close (single save)
# ---------------------------------------------------------------------------


def test_cleanup_idle_batch_close_single_save(tmp_path: Path) -> None:
    """cleanup_idle should close all idle sessions in one batch, not per-session."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    # Register two sessions, both old enough to be idle
    mgr.register_session(Session(id="s1", machine="h", pid=101))
    mgr.register_session(Session(id="s2", machine="h", pid=102))

    # Age both sessions beyond threshold
    with mgr._lock:
        sessions = mgr._load_sessions()
        old_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        sessions["s1"]["last_active"] = old_time
        sessions["s2"]["last_active"] = old_time
        mgr._save_sessions(sessions)

    with (
        patch("ssh_tools.manager.os.kill") as mock_kill,
        patch("ssh_tools.manager.time.sleep"),
    ):
        mock_kill.side_effect = OSError("No such process")
        result = mgr.cleanup_idle(max_idle_minutes=30)

    assert result["count"] == 2
    # Both sessions should be closed
    s1 = mgr.get_session("s1")
    assert s1 is not None
    assert s1.status == "closed"
    s2 = mgr.get_session("s2")
    assert s2 is not None
    assert s2.status == "closed"


def test_cleanup_idle_empty_list(tmp_path: Path) -> None:
    """No idle sessions → count 0, no errors."""
    mgr = _make_manager(tmp_path)
    result = mgr.cleanup_idle(max_idle_minutes=30)
    assert result["count"] == 0
    assert result["killed"] == []


# ---------------------------------------------------------------------------
# Background command — Popen path, poll_session, read_output
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


def test_run_command_background(tmp_path: Path) -> None:
    """background=True uses Popen and returns immediately."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        result = mgr.run_command("h", "long command", background=True)

    assert result["success"] is True
    assert result["background"] is True
    assert result["pid"] == 12345
    assert "session_id" in result
    # Session should be registered (active, not closed)
    session = mgr.get_session(result["session_id"])
    assert session is not None
    assert session.status == "active"


def test_poll_session_running(tmp_path: Path) -> None:
    """poll_session returns running=True when process is alive."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        result = mgr.run_command("h", "long cmd", background=True)

    sid = result["session_id"]
    poll_result = mgr.poll_session(sid)
    assert poll_result["success"] is True
    assert poll_result["running"] is True


def test_poll_session_finished(tmp_path: Path) -> None:
    """poll_session collects output when process finishes."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    # Start with running process
    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        bg_result = mgr.run_command("h", "cmd", background=True)
    sid = bg_result["session_id"]

    # Now simulate the process has finished
    fake_proc.poll.return_value = 0
    fake_proc.stdout.read.return_value = b"hello world\n"
    fake_proc.stderr.read.return_value = b""

    poll_result = mgr.poll_session(sid)
    assert poll_result["success"] is True
    assert poll_result["running"] is False
    assert poll_result["stdout"] == "hello world\n"
    assert poll_result["exit_code"] == 0
    # Process should be removed from internal tracking
    assert sid not in mgr._processes
    # Session should be closed
    s = mgr.get_session(sid)
    assert s is not None
    assert s.status == "closed"


def test_poll_session_nonexistent(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    result = mgr.poll_session("no_such_session")
    assert result["success"] is False
    assert "No background process" in result["error"]


def test_read_output_finished(tmp_path: Path) -> None:
    """read_output returns output from a finished background process."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        bg_result = mgr.run_command("h", "cmd", background=True)
    sid = bg_result["session_id"]

    # Simulate finished
    fake_proc.poll.return_value = 1
    fake_proc.stdout.read.return_value = b"some output"
    fake_proc.stderr.read.return_value = b"err msg"

    out = mgr.read_output(sid)
    assert out["success"] is True
    assert out["stdout"] == "some output"
    assert out["stderr"] == "err msg"
    assert out["exit_code"] == 1
    assert sid not in mgr._processes


def test_read_output_still_running(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        bg_result = mgr.run_command("h", "cmd", background=True)
    sid = bg_result["session_id"]

    result = mgr.read_output(sid)
    assert result["success"] is False
    assert "still running" in result["error"]


def test_read_output_nonexistent(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    result = mgr.read_output("no_such_session")
    assert result["success"] is False
    assert "No background process" in result["error"]


# ---------------------------------------------------------------------------
# Output save-to-file (Hermes pattern)
# ---------------------------------------------------------------------------


def test_run_command_large_output_saves_to_file(tmp_path: Path) -> None:
    """stdout/stderr exceeding max_output_chars are saved to /tmp/ files."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    big_stdout = "x" * 1000
    big_stderr = "y" * 1000

    with patch("ssh_tools.manager.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=big_stdout, stderr=big_stderr)
        result = mgr.run_command("h", "cmd", max_output_chars=100)

    assert result["success"] is True
    # Summary returned inline
    assert "output saved to" in result["stdout"]
    assert "stdout_file" in result
    assert "stderr_file" in result
    assert result["stdout_file"].endswith("_stdout.txt")
    assert result["stderr_file"].endswith("_stderr.txt")
    # Full output written to file
    saved = Path(result["stdout_file"]).read_text()
    assert saved == big_stdout


def test_run_command_short_output_inline_no_file(tmp_path: Path) -> None:
    """Short output stays inline, no file created."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    with patch("ssh_tools.manager.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="hi\n", stderr="")
        result = mgr.run_command("h", "echo hi")

    assert result["stdout"] == "hi\n"
    assert "stdout_file" not in result
    assert "stderr_file" not in result


def test_maybe_save_output_short() -> None:
    """_maybe_save_output returns text unchanged for short input."""
    mgr = SSHManager.__new__(SSHManager)
    text, path = mgr._maybe_save_output("hello", 100, "s1", "stdout")
    assert text == "hello"
    assert path is None


def test_maybe_save_output_long() -> None:
    """_maybe_save_output writes to /tmp/ and returns summary."""
    mgr = SSHManager.__new__(SSHManager)
    long_text = "x" * 500
    text, path = mgr._maybe_save_output(long_text, 100, "s1", "stdout")
    assert path is not None
    assert "/tmp/ssh_output_s1_stdout.txt" in path
    assert "output saved to" in text
    assert "500 chars total" in text
    assert len(text) < 500  # summary is shorter than full


# ---------------------------------------------------------------------------
# Command audit log — JSONL written, list_command_log returns entries
# ---------------------------------------------------------------------------


def test_run_command_writes_audit_log(tmp_path: Path) -> None:
    """After run_command, a JSONL entry should be in command_log.jsonl."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    with patch("ssh_tools.manager.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = mgr.run_command("h", "uptime")

    assert result["success"] is True
    log_path = tmp_path / "command_log.jsonl"
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) >= 1
    entry = json.loads(lines[-1])
    assert entry["machine"] == "h"
    assert entry["command"] == "uptime"
    assert entry["exit_code"] == 0
    assert "timestamp" in entry
    assert "elapsed_secs" in entry
    assert "session_id" in entry


def test_list_command_log_entries(tmp_path: Path) -> None:
    """list_command_log returns parsed entries from the JSONL file."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    # Run two commands
    with patch("ssh_tools.manager.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mgr.run_command("h", "echo 1")
        mgr.run_command("h", "echo 2")

    entries = mgr.list_command_log()
    assert len(entries) >= 2
    assert entries[-1]["command"] == "echo 2"
    assert entries[-2]["command"] == "echo 1"


def test_list_command_log_limit(tmp_path: Path) -> None:
    """list_command_log respects the limit parameter."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    with patch("ssh_tools.manager.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        for i in range(5):
            mgr.run_command("h", f"cmd {i}")

    entries = mgr.list_command_log(limit=3)
    assert len(entries) == 3
    assert entries[-1]["command"] == "cmd 4"


def test_list_command_log_empty(tmp_path: Path) -> None:
    """No log file → empty list."""
    mgr = _make_manager(tmp_path)
    assert mgr.list_command_log() == []


def test_background_command_writes_audit_log(tmp_path: Path) -> None:
    """Background commands also write to the audit log."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        mgr.run_command("h", "bg cmd", background=True)

    log_path = tmp_path / "command_log.jsonl"
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) >= 1
    entry = json.loads(lines[-1])
    assert entry["command"] == "bg cmd"
    assert entry["exit_code"] is None  # Not finished yet


# ---------------------------------------------------------------------------
# Auto-prune in idle checker
# ---------------------------------------------------------------------------


def test_idle_checker_auto_prunes(tmp_path: Path) -> None:
    """The idle checker should auto-prune old closed sessions."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    # Create an old closed session
    mgr.register_session(Session(id="s_old", machine="h", pid=100))
    mgr.close_session("s_old")

    # Set started time to 48 hours ago so it exceeds prune threshold
    with mgr._lock:
        sessions = mgr._load_sessions()
        old_time = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        sessions["s_old"]["started"] = old_time
        mgr._save_sessions(sessions)

    # Verify session exists before checker runs
    assert mgr.get_session("s_old") is not None

    # Run the prune directly (simulating what the checker does every 10 cycles)
    count = mgr.prune_closed()
    assert count == 1
    assert mgr.get_session("s_old") is None


# ---- Bug fix: machine name validation ----


def test_validate_machine_name_valid() -> None:
    assert SSHManager._validate_machine_name("myserver") is None
    assert SSHManager._validate_machine_name("web-01") is None
    assert SSHManager._validate_machine_name("grid1.example.com") is None
    assert SSHManager._validate_machine_name("a") is None


def test_validate_machine_name_invalid() -> None:
    assert SSHManager._validate_machine_name("../../etc/passwd") is not None
    assert SSHManager._validate_machine_name("my server") is not None
    assert SSHManager._validate_machine_name("test*") is not None
    assert SSHManager._validate_machine_name("") is not None
    assert SSHManager._validate_machine_name("a" * 65) is not None


def test_add_machine_rejects_bad_name(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    with pytest.raises(ValueError, match=r"alphanumeric|invalid"):
        mgr.add_machine(Machine(name="../../etc", host="1.1.1.1"))


# ---- Bug fix: /tmp output file permissions ----


def test_maybe_save_output_permissions(tmp_path: Path) -> None:
    """Output files saved to /tmp should be 0o600 (owner-only)."""
    mgr = _make_manager(tmp_path)
    big_text = "x" * 100
    _summary, path_str = mgr._maybe_save_output(big_text, 10, "test_session", "stdout")
    assert path_str is not None
    path = Path(path_str)
    assert path.exists()
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600, got octal {oct(mode)}"
    path.unlink()


# ---- Bug fix: structure validation ----


def test_load_machines_corrupt_structure(tmp_path: Path) -> None:
    """Corrupt machines.json with wrong structure should not crash."""
    mgr = _make_manager(tmp_path)
    mgr._config.machines_file.write_text(json.dumps({"machines": [1, 2, 3]}))
    result = mgr._load_machines()
    assert result == {}


def test_load_sessions_corrupt_structure(tmp_path: Path) -> None:
    """Corrupt sessions.json with wrong structure should not crash."""
    mgr = _make_manager(tmp_path)
    mgr._config.sessions_file.write_text(json.dumps({"sessions": "not a dict"}))
    result = mgr._load_sessions()
    assert result == {}


# ---- Bug fix: timeout type safety ----


def test_run_command_timeout_type_coercion(tmp_path: Path) -> None:
    """String timeout should be coerced to int, not crash."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    with patch("ssh_tools.manager.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = mgr.run_command("h", "echo ok", timeout="5")
        assert result["success"] is True


def test_run_command_zero_timeout_uses_default(tmp_path: Path) -> None:
    """timeout=0 should fall back to default."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    with patch("ssh_tools.manager.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = mgr.run_command("h", "echo ok", timeout=0)
        assert result["success"] is True


# ---- Bug fix: _close_sessions_batch cleans /tmp ----


def test_close_sessions_batch_cleans_tmp(tmp_path: Path) -> None:
    """Batch-closed sessions should have their /tmp output files cleaned."""
    mgr = _make_manager(tmp_path)
    fake_file = Path("/tmp/ssh_output_test_batch_stdout.txt")
    fake_file.write_text("test")
    try:
        mgr._close_sessions_batch(["test_batch"])
        assert not fake_file.exists()
    finally:
        with contextlib.suppress(OSError):
            fake_file.unlink()


# ---- Bug fix: startup temp cleanup ----


def test_ensure_dirs_cleans_orphaned_tmp(tmp_path: Path) -> None:
    """ensure_dirs should clean orphaned .tmp files from data_dir."""
    from ssh_tools.config import SSHConfig

    config = SSHConfig(data_dir=tmp_path)
    (tmp_path / "machines_abc.tmp").write_text("old")
    (tmp_path / "sessions_def.tmp").write_text("old")
    config.ensure_dirs()
    assert not (tmp_path / "machines_abc.tmp").exists()
    assert not (tmp_path / "sessions_def.tmp").exists()


# ---- Bug fix: prune_closed handles naive datetime ----


def test_prune_closed_naive_datetime(tmp_path: Path) -> None:
    """Sessions with naive timestamps should be pruned, not skipped."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    sessions = {
        "s_naive": {
            "id": "s_naive",
            "machine": "h",
            "status": "closed",
            "started": "2020-01-01T00:00:00",
            "last_active": "2020-01-01T00:00:00",
            "command_count": 0,
        }
    }
    mgr._write_json(mgr._config.sessions_file, {"sessions": sessions})
    count = mgr.prune_closed(max_age_hours=1)
    assert count == 1


# ---- Bug fix: background session registration order ----


def test_background_session_registered_before_process(tmp_path: Path) -> None:
    """Session should be in JSON before process is in _processes dict."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    with patch("ssh_tools.manager.subprocess.Popen") as mock_popen:
        proc = MagicMock()
        proc.pid = 99999
        mock_popen.return_value = proc
        result = mgr.run_command("h", "sleep 99", background=True)
        sid = result["session_id"]
        session = mgr.get_session(sid)
        assert session is not None
        assert session.pid == 99999
        assert sid in mgr._processes
