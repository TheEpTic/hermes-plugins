"""Tests for ssh_tools.manager — models, registry, sessions, execution, audit."""

from __future__ import annotations

import contextlib
import getpass
import json
import signal
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ssh_tools.config import SSHConfig
from ssh_tools.manager import SSHManager
from ssh_tools.models import Machine, Session

from .conftest import _make_manager

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def _active_session(age: timedelta = timedelta(minutes=5)) -> Session:
    now = datetime.now(UTC)
    return Session(
        id="s1",
        machine="host1",
        started=now.isoformat(),
        last_active=(now - age).isoformat(),
        status="active",
    )


def test_machine_model() -> None:
    m = Machine(name="test", host="10.0.0.1", user="admin", port=2222, aliases=["t"], tags=["dev"])
    m2 = Machine.from_dict("test", m.to_dict())
    assert (m2.name, m2.host, m2.user, m2.port, m2.aliases, m2.tags) == (
        "test",
        "10.0.0.1",
        "admin",
        2222,
        ["t"],
        ["dev"],
    )
    defaults = Machine(name="x", host="1.2.3.4")
    assert (
        defaults.user,
        defaults.port,
        defaults.key,
        defaults.aliases,
        defaults.tags,
        defaults.description,
        defaults.added,
    ) == (getpass.getuser(), 22, "", None, None, "", "")


def test_session_model() -> None:
    s = Session(id="s1", machine="h", pid=123, control_path="/tmp/c.sock", status="active")
    s2 = Session.from_dict("s1", s.to_dict())
    assert (s2.id, s2.machine, s2.pid, s2.control_path, s2.status) == (
        "s1",
        "h",
        123,
        "/tmp/c.sock",
        "active",
    )


@pytest.mark.parametrize(
    "age,expected",
    [
        (timedelta(minutes=5), ("5m 0s", True)),
        (timedelta(seconds=30), ("30s", True)),
        (timedelta(hours=2, minutes=15), ("2h 15m", True)),
        (None, ("unknown", False)),  # closed session: idle is unknowable
    ],
)
def test_session_idle_human(age: timedelta | None, expected: tuple[str, bool]) -> None:
    session = (
        _active_session(age) if age is not None else Session(id="s1", machine="h", status="closed")
    )
    assert (session.idle_human, session.idle_seconds is not None) == expected


def test_session_idle_seconds_bounds() -> None:
    s = _active_session(timedelta(minutes=5))
    assert s.idle_seconds is not None and 290 <= s.idle_seconds <= 310
    for last_active in ("", "not-a-date"):
        bad = Session(id="s1", machine="h", status="active", last_active=last_active)
        assert bad.idle_seconds is None


# ---------------------------------------------------------------------------
# machine registry
# ---------------------------------------------------------------------------


def test_registry_crud(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="host1", host="10.0.0.1", user="admin", port=2222, aliases=["h1"]))
    mgr.add_machine(Machine(name="b", host="2.2.2.2"))
    assert [mgr.resolve_name(n) for n in ("h1", "host1", "nope")] == ["host1", "host1", None]
    assert mgr.get_machine("h1").name == "host1"
    assert set(mgr.list_machines()) == {"host1", "b"}
    assert (mgr.remove_machine("h1"), mgr.remove_machine("nope")) == (True, False)
    assert mgr.get_machine("nope") is None
    # persist + overwrite keeps original added timestamp
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    mgr.add_machine(
        Machine(name="h", host="2.2.2.2", user="new", added="2026-01-01T00:00:00+00:00")
    )
    got2 = mgr.get_machine("h")
    assert got2 is not None and got2.host == "2.2.2.2"
    kept = _make_manager(tmp_path).get_machine("h")
    assert kept is not None and kept.host == "2.2.2.2"


def test_validate_machine_name() -> None:
    from ssh_tools.validate import validate_machine_name

    for good in ("myserver", "web-01", "grid1.example.com", "a"):
        assert validate_machine_name(good) is None
    for bad in ("../../etc/passwd", "my server", "test*", "", "a" * 65):
        assert validate_machine_name(bad) is not None


def test_add_machine_rejects_bad_name_and_unsafe_fields(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    with pytest.raises(ValueError, match=r"alphanumeric|invalid"):
        mgr.add_machine(Machine(name="../../etc", host="1.1.1.1"))
    for bad in [
        Machine(name="h1", host="bad host"),
        Machine(name="h2", host="-oProxyCommand=evil"),
        Machine(name="h3", host="1.1.1.1", user="root@evil"),
        Machine(name="h4", host="1.1.1.1", port=0),
        Machine(name="h5", host="1.1.1.1", port=65536),
        Machine(name="h6", host="1.1.1.1", aliases=["../../etc"]),
        Machine(name="h7", host="1.1.1.1", key="bad\x00key"),
    ]:
        with pytest.raises(ValueError):
            mgr.add_machine(bad)


# ---------------------------------------------------------------------------
# session tracking
# ---------------------------------------------------------------------------


def test_session_lifecycle(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id="s1", machine="host1", pid=100))
    mgr.register_session(Session(id="s2", machine="host1", pid=200))
    s = mgr.get_session("s1")
    assert s is not None and s.started and s.last_active and s.status == "active"
    mgr.touch_session("s1")
    mgr.touch_session("s1")
    assert mgr.get_session("s1").command_count == 2
    mgr.close_session("s2")
    assert set(mgr.list_sessions(status="")) == {"s1", "s2"}
    assert set(mgr.list_sessions("closed")) == {"s2"}
    mgr.remove_session("s2")
    assert mgr.get_session("s2") is None
    mgr.register_session(Session(id="s3", machine="host1", started="2026-01-01T00:00:00+00:00"))
    assert mgr.get_session("s3").started == "2026-01-01T00:00:00+00:00"


def _aged_session_blob(
    mgr: Any, sid: str, age: timedelta, *, status: str = "active", started: str | None = None
) -> None:
    """Write a session with an aged timestamp straight into the sessions blob."""
    stamp = (datetime.now(UTC) - age).isoformat()
    fresh = datetime.now(UTC).isoformat()
    _rewrite_sessions_blob(
        mgr,
        lambda sessions: sessions.update(
            {
                sid: {
                    "machine": "host1",
                    "pid": 101,
                    "started": started or (stamp if status == "closed" else fresh),
                    "last_active": stamp if status == "active" else fresh,
                    "status": status,
                }
            }
        ),
    )


@pytest.mark.parametrize(
    "age,expected",
    [(timedelta(hours=1), 1), (timedelta(minutes=1), 0), (timedelta(minutes=30), 0)],
)
def test_cleanup_idle(tmp_path: Path, age: timedelta, expected: int) -> None:
    mgr = _make_manager(tmp_path)
    _aged_session_blob(mgr, "s1", age)
    assert mgr.cleanup_idle(max_idle_minutes=30)["count"] == expected


def test_cleanup_idle_empty(tmp_path: Path) -> None:
    assert _make_manager(tmp_path).cleanup_idle(max_idle_minutes=30) == {
        "count": 0,
        "killed": [],
    }


def test_cleanup_idle_batch_marks_orphaned(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    for sid in ("s1", "s2"):
        _aged_session_blob(mgr, sid, timedelta(hours=1))
    with (
        patch("ssh_tools.exec.time.sleep"),
        patch("ssh_tools.exec.os.kill", side_effect=OSError("gone")),
    ):
        result = mgr.cleanup_idle(max_idle_minutes=30)
    assert result["count"] == 2
    assert all(mgr.get_session(s).status == "orphaned" for s in ("s1", "s2"))


def test_prune_closed_old(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    _aged_session_blob(mgr, "s1", timedelta(hours=48), status="closed")
    assert mgr.prune_closed(max_age_hours=24) == 1
    _aged_session_blob(mgr, "s2", timedelta(0), status="closed", started="2020-01-01T00:00:00")
    assert mgr.prune_closed(max_age_hours=24) == 1  # naive datetime still prunes


def test_prune_closed_keeps(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    _aged_session_blob(mgr, "fresh", timedelta(seconds=1), status="closed")
    _aged_session_blob(mgr, "active", timedelta(hours=48), status="active")
    _aged_session_blob(mgr, "bad", timedelta(0), status="closed", started="not-a-date")
    assert mgr.prune_closed(max_age_hours=24) == 0
    assert set(mgr.list_sessions(status="")) == {"fresh", "active", "bad"}


def test_prune_uses_config_default(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    _aged_session_blob(mgr, "old", timedelta(hours=48), status="closed")
    assert mgr.prune_closed() == 1  # config default 24h
    assert mgr.get_session("old") is None


# ---------------------------------------------------------------------------
# json persistence
# ---------------------------------------------------------------------------


def _rewrite_sessions_blob(mgr: Any, mutate: Any) -> None:
    from ssh_tools.helpers import read_json, write_json_atomic

    path = mgr._config.sessions_file
    blob = read_json(path, {"sessions": {}})
    mutate(blob["sessions"])
    write_json_atomic(path, blob)


@pytest.mark.parametrize(
    "filename,loader,blob",
    [
        ("machines_file", "list_machines", {"machines": [1, 2, 3]}),
        ("sessions_file", "list_sessions", {"sessions": "not a dict"}),
    ],
)
def test_load_corrupt_structure_resets(
    tmp_path: Path, filename: str, loader: str, blob: dict
) -> None:
    mgr = _make_manager(tmp_path)
    getattr(mgr._config, filename).write_text(json.dumps(blob))
    assert getattr(mgr, loader)() == {}


def test_json_helpers_roundtrip(tmp_path: Path) -> None:
    from ssh_tools.helpers import read_json, write_json_atomic

    corrupt = tmp_path / "machines.json"
    corrupt.write_text("NOT JSON {{{")
    assert read_json(corrupt, {"default": True}) == {"default": True}
    assert read_json(tmp_path / "missing.json", {"fallback": True}) == {"fallback": True}
    target = tmp_path / "test.json"
    write_json_atomic(target, {"key": "value"})
    assert json.loads(target.read_text()) == {"key": "value"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_close_sessions_batch_cleans_output_dir(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    fake = mgr._config.output_dir / "ssh_output_test_batch_stdout.txt"
    fake.write_text("test")
    try:
        mgr.close_session("test_batch")
        assert not fake.exists()
    finally:
        with contextlib.suppress(OSError):
            fake.unlink()


def test_ensure_dirs_cleans_orphaned_tmp(tmp_path: Path) -> None:
    config = SSHConfig(data_dir=tmp_path)
    (tmp_path / "machines_abc.tmp").write_text("old")
    (tmp_path / "sessions_def.tmp").write_text("old")
    config.ensure_dirs()
    assert not (tmp_path / "machines_abc.tmp").exists()
    assert not (tmp_path / "sessions_def.tmp").exists()


# ---------------------------------------------------------------------------
# ssh execution
# ---------------------------------------------------------------------------


def test_run_command_validation(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    missing = mgr.run_command("nope", "echo hi")
    assert missing["success"] is False and "not found" in missing["error"]
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    assert mgr.run_command("h", "", timeout=30)["success"] is False


def _h(tmp_path: Path) -> Any:
    """Manager with a registered host h at 1.1.1.1."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    return mgr


def test_build_ssh_args(tmp_path: Path) -> None:
    from ssh_tools.exec import build_ssh_args

    mgr = _make_manager(tmp_path)
    machine = Machine(name="h", host="10.0.0.1", user="admin", port=2222, key="~/.ssh/test")
    args = build_ssh_args(mgr.config, machine, "uptime", "/tmp/c.sock", timeout=30)
    assert args[0] == "ssh" and "2222" in args and "~/.ssh/test" in args
    assert "ControlMaster=auto" in args and "admin@10.0.0.1" in args
    assert "bash" in args and "pipefail" in args[-1] and "uptime" in args[-1]
    assert "ConnectTimeout=10" in args  # min(30, 10)
    bare = build_ssh_args(mgr.config, Machine(name="h", host="10.0.0.1"), "uptime")
    assert "ControlMaster" not in str(bare) and "-i" not in bare


def _ran(mgr: Any, *args: Any, stdout: str = "ok", code: int = 0, **kw: Any) -> dict:
    """One sync run under a faked subprocess.run; asserts no session leaked."""
    with patch("ssh_tools.exec.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=code, stdout=stdout, stderr="")
        result = mgr.run_command(*args, **kw)
    assert mgr.list_sessions("active") == {}
    return result


def test_run_command_sync_and_coercions(tmp_path: Path) -> None:
    mgr = _h(tmp_path)
    assert _ran(mgr, "h", "echo ok")["success"] is True
    assert _ran(mgr, "h", "echo ok", timeout="5")["success"] is True
    assert _ran(mgr, "h", "echo ok", timeout=0)["success"] is True
    assert _ran(mgr, "h", "echo ok", timeout=-1)["success"] is True
    bad = _ran(mgr, "h", "echo ok", timeout="abc")
    assert bad["success"] is False and "timeout" in bad["error"]
    # main behaviour: a bool timeout is garbage and raises, not silently coerced.
    bool_bad = _ran(mgr, "h", "echo ok", timeout=True)
    assert bool_bad["success"] is False and "positive integer" in bool_bad["error"]


def test_run_command_clamps_output(tmp_path: Path) -> None:
    mgr = _h(tmp_path)
    result = _ran(mgr, "h", "echo ok", stdout="x" * 600_000)
    assert len(result["stdout"]) < 600_000 and "stdout_file" in result
    clamped = _ran(mgr, "h", "echo ok", stdout="y" * 100)
    assert clamped["stdout"] == "y" * 100  # under the clamp, stays inline


@pytest.mark.parametrize("text,limit", [("hello", 100), ("x" * 100, 10)])
def test_maybe_save_output(tmp_path: Path, text: str, limit: int) -> None:
    mgr = _make_manager(tmp_path)
    summary, path = mgr._exec._maybe_save_output(text, limit, "s1", "stdout")
    if len(text) <= limit:
        assert (summary, path) == (text, None)
    else:
        assert path is not None and "output saved to" in summary
        assert (Path(path).stat().st_mode & 0o777) == 0o600
        Path(path).unlink()


def test_output_sizes_inline_vs_file(tmp_path: Path) -> None:
    mgr = _h(tmp_path)
    with patch("ssh_tools.exec.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="x" * 1000, stderr="y" * 1000)
        result = mgr.run_command("h", "cmd", max_output_chars=100)
    assert "output saved to" in result["stdout"]
    assert result["stdout_file"].endswith("_stdout.txt")
    assert Path(result["stdout_file"]).read_text() == "x" * 1000
    short = _ran(mgr, "h", "echo hi", stdout="hi\n")
    assert short["stdout"] == "hi\n" and "stdout_file" not in short


# ---------------------------------------------------------------------------
# background processes
# ---------------------------------------------------------------------------


def _fake_running_popen() -> MagicMock:
    return MagicMock(
        pid=12345,
        stdout=MagicMock(),
        stderr=MagicMock(),
        returncode=None,
        **{"poll.return_value": None},
    )


def _bg(tmp_path: Path, cmd: str = "cmd", **kw: Any) -> tuple[Any, str, Any]:
    """Start a faked bg command; returns (mgr, session_id, proc)."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    proc = _fake_running_popen()
    with patch("ssh_tools.exec.subprocess.Popen", return_value=proc):
        started = mgr.run_command("h", cmd, background=True, **kw)
    return mgr, started["session_id"], proc


def test_run_command_background(tmp_path: Path) -> None:
    mgr, sid, proc = _bg(tmp_path, "long command")
    assert proc.pid == 12345 and mgr.get_session(sid).pid == 12345
    assert mgr.poll_session(sid)["running"] is True


def test_background_uses_spool_files_not_pipes(tmp_path: Path) -> None:
    mgr = _h(tmp_path)
    with patch("ssh_tools.exec.subprocess.Popen", return_value=_fake_running_popen()) as popen:
        started = mgr.run_command("h", "verbose", background=True)
    assert started["session_id"].startswith("ssh_h_")
    kwargs = popen.call_args.kwargs
    assert kwargs["stdout"] is not subprocess.PIPE and kwargs["stderr"] is not subprocess.PIPE


@pytest.mark.parametrize(
    "exit_code,stream,expected",
    [(0, "stdout", "hello world\n"), (1, "stderr", "command failed\n")],
)
def test_poll_finished(tmp_path: Path, exit_code: int, stream: str, expected: str) -> None:
    mgr, sid, proc = _bg(tmp_path)
    proc.poll.return_value = exit_code
    proc.stdout.read.return_value = b"hello world\n" if exit_code == 0 else b""
    proc.stderr.read.return_value = b"" if exit_code == 0 else b"command failed\n"
    result = mgr.poll_session(sid)
    assert result["success"] is (exit_code == 0)
    assert result["running"] is False
    assert result[stream] == expected
    assert mgr.poll_session(sid)["success"] is False  # collected once
    assert mgr.get_session(sid).status == "closed"


def test_poll_and_read_errors(tmp_path: Path) -> None:
    mgr = _h(tmp_path)
    with patch("ssh_tools.exec.subprocess.Popen", return_value=_fake_running_popen()):
        sid = mgr.run_command("h", "sleep", background=True)["session_id"]
    assert "No background process" in mgr.poll_session("nope")["error"]
    assert "No background process" in mgr.read_output("nope")["error"]
    assert mgr.poll_session(sid)["running"] is True
    running = mgr.read_output(sid)
    assert running["success"] is False and "still running" in running["error"]


def test_audit_log_modes(tmp_path: Path) -> None:
    mgr = _h(tmp_path)
    _ran(mgr, "h", "echo 1")
    _ran(mgr, "h", "echo 2")
    entries = mgr.list_command_log()
    assert entries[-2]["command"] == "echo 1" and entries[-1]["command"] == "echo 2"
    assert {"timestamp", "elapsed_secs", "session_id"} <= entries[-1].keys()
    assert len(mgr.list_command_log(limit=1)) == 1
    assert _make_manager(tmp_path / "fresh").list_command_log() == []
    with patch("ssh_tools.exec.subprocess.Popen", return_value=_fake_running_popen()):
        mgr.run_command("h", "bg cmd", background=True)
    assert mgr.list_command_log()[-1]["exit_code"] is None


def test_audit_redaction_and_metadata(tmp_path: Path) -> None:
    redacted = SSHManager(SSHConfig(data_dir=tmp_path, audit_log_mode="redacted"))
    redacted.add_machine(Machine(name="h", host="1.1.1.1"))
    _ran(redacted, "h", "TOKEN=super-secret deploy --password hunter2")
    entry = redacted.list_command_log()[-1]
    assert "super-secret" not in entry["command"] and "hunter2" not in entry["command"]
    assert "<redacted>" in entry["command"] and len(entry["command_sha256"]) == 64
    meta = SSHManager(SSHConfig(data_dir=tmp_path, audit_log_mode="metadata"))
    _ran(meta, "h", "GITHUB_TOKEN=short deploy")
    _ran(meta, "h", "GITHUB_TOKEN=a-much-longer-secret deploy")
    first, second = meta.list_command_log(limit=2)
    assert "command" not in first
    assert first["command_length"] == second["command_length"]
    assert first["command_sha256"] == second["command_sha256"]
    off = SSHManager(SSHConfig(data_dir=tmp_path / "off", audit_log_mode="off"))
    off.add_machine(Machine(name="h", host="1.1.1.1"))
    _ran(off, "h", "echo private")
    assert not (tmp_path / "off" / "command_log.jsonl").exists()


@pytest.mark.parametrize(
    ("command", "secret"),
    [
        ("GITHUB_TOKEN=ghp_leak deploy", "ghp_leak"),
        ("AWS_SECRET_ACCESS_KEY='secret with spaces' deploy", "secret with spaces"),
        ('tool --api-key "flag secret value"', "flag secret value"),
        ('curl -H "Authorization: Bearer ***" https://example.com', "bearer-leak"),
        ('curl -H "X-Api-Key: *** https://example.com', "header-leak"),
        ("curl https://user:url-password@example.com", "url-password"),
    ],
)
def test_redact_command(command: str, secret: str) -> None:
    from ssh_tools.audit import redact_command

    redacted = redact_command(command)
    assert secret not in redacted and "<redacted>" in redacted


# ---------------------------------------------------------------------------
# kill_session
# ---------------------------------------------------------------------------


def _tracked_session(tmp_path: Path, sid: str = "s1", status: int | None = None) -> Any:
    mgr = _make_manager(tmp_path)
    mgr.register_session(Session(id=sid, machine="h", pid=99999))
    proc = MagicMock(pid=99999)
    proc.poll.return_value = status
    mgr._exec._processes[sid] = proc
    return mgr


@pytest.mark.parametrize(
    "tracked,success,fragment",
    [(True, True, "pid_killed"), (False, False, "orphaned")],
)
def test_kill_session_tracked_vs_untracked(
    tmp_path: Path, tracked: bool, success: bool, fragment: str
) -> None:
    """Tracked procs get SIGTERM; untracked pids are never signalled (orphaned)."""
    mgr = _tracked_session(tmp_path) if tracked else _make_manager(tmp_path)
    if not tracked:
        mgr.register_session(Session(id="s1", machine="h", pid=99999))
    with patch("ssh_tools.exec.os.killpg") as mock_killpg:
        result = mgr.kill_session("s1")
    assert result["success"] is success and fragment in json.dumps(result)
    if tracked:
        mock_killpg.assert_called_once_with(99999, signal.SIGTERM)
        assert mgr.get_session("s1").status == "closed"
    else:
        mock_killpg.assert_not_called()
        assert mgr.get_session("s1").status == "orphaned"


def test_kill_session_socket_and_missing(tmp_path: Path) -> None:
    mgr = _tracked_session(tmp_path, status=0)
    with patch("ssh_tools.exec.subprocess.run") as mock_sub:
        result = mgr.kill_session("s1")
    assert result["success"] is True and result["socket_closed"] is False
    mock_sub.assert_not_called()
    mgr.register_session(Session(id="s2", machine="h", pid=0, control_path="/nonexistent.sock"))
    dead = mgr.kill_session("s2")
    assert dead["success"] is False and dead["status"] == "orphaned"
    assert "not found" in mgr.kill_session("nope")["error"]


# ---------------------------------------------------------------------------
# idle checker
# ---------------------------------------------------------------------------


def test_idle_checker_lifecycle(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    assert not mgr._checker_event.is_set()
    mgr.start_idle_checker()
    thread1 = mgr._checker_thread
    assert thread1 is not None and thread1.is_alive()
    mgr.start_idle_checker()  # no-op
    assert mgr._checker_thread is thread1
    mgr.stop_idle_checker()
    assert mgr._checker_event.is_set()


# ---------------------------------------------------------------------------
# background spool lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("large", [True, False])
def test_spool_lifecycle(tmp_path: Path, large: bool) -> None:
    """Large bg output keeps its spool file; short output deletes both spools."""
    mgr, sid, proc = _bg(tmp_path, "command", max_output_chars=10 if large else 50_000)
    stdout_path, stderr_path, _ = mgr._exec._outputs[sid]
    stdout_path.write_text("x" * 100 if large else "done")
    stderr_path.write_text("")
    proc.poll.return_value = proc.returncode = 0
    result = mgr.poll_session(sid)
    if large:
        assert Path(result["stdout_file"]).read_text() == "x" * 100
        assert not stderr_path.exists()
    else:
        assert result["stdout"] == "done" and "stdout_file" not in result
        assert not stdout_path.exists() and not stderr_path.exists()
