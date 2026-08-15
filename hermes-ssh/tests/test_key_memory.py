"""Regression tests for SSH-1 (host key trust remediation) and SSH-2 (per-host key memory).

SSH-1: a first connection to a new host fails with `Host key verification failed`
(exit 255). The error surfaced to the agent must include the exact remediation step
(accept-new seeding / pre-seeding guidance) instead of a bare failure.

SSH-2: the plugin must remember the working key per host after the first successful
authentication (persisted through the manager's storage accessors), and failed
authentications must report which keys were attempted so the agent never brute-forces
default/id_ed25519/id_rsa blindly.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from ssh_tools.handlers import handle_ssh_terminal
from ssh_tools.models import Machine

from .conftest import _make_manager

_HOST_KEY_STDERR = "Host key verification failed."


def _ok_result(**extra: object) -> MagicMock:
    result = MagicMock()
    result.returncode = 0
    result.stdout = "ok\n"
    result.stderr = ""
    for key, value in extra.items():
        setattr(result, key, value)
    return result


def _failed_result(stderr: str, returncode: int = 255) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = ""
    result.stderr = stderr
    return result


# ---------------------------------------------------------------------------
# SSH-1 — host key verification failure surfaces exact remediation
# ---------------------------------------------------------------------------


def test_host_key_failure_includes_remediation_guidance(tmp_path: Path) -> None:
    """A failed first connect must tell the agent how to fix host key trust."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))
    handler = handle_ssh_terminal(mgr)

    with patch("ssh_tools.manager.subprocess.run", return_value=_failed_result(_HOST_KEY_STDERR)):
        result = json.loads(handler({"machine": "h", "command": "echo hi"}))

    assert result["success"] is False
    assert "Host key verification failed" in result["error"]
    assert "accept-new" in result["error"]
    assert "ssh-keyscan" in result["error"]
    assert "StrictHostKeyChecking" in result["error"]


def test_run_command_host_key_failure_direct_remediation(tmp_path: Path) -> None:
    """run_command itself must carry remediation guidance, not just the handler."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    with patch("ssh_tools.manager.subprocess.run", return_value=_failed_result(_HOST_KEY_STDERR)):
        result = mgr.run_command("h", "echo hi")

    assert result["success"] is False
    assert result["exit_code"] == 255
    assert "accept-new" in result["error"]
    assert "ssh-keyscan" in result["error"]
    assert "1.1.1.1" in result["error"]


def test_non_host_key_failure_not_mislabeled(tmp_path: Path) -> None:
    """Failures unrelated to host key verification must not gain remediation text."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    with patch(
        "ssh_tools.manager.subprocess.run",
        return_value=_failed_result("Permission denied (publickey)."),
    ):
        result = mgr.run_command("h", "echo hi")

    assert result["success"] is False
    # Raw stderr is preserved and no host-key remediation text is invented
    assert "accept-new" not in result["stderr"]
    assert "accept-new" not in result.get("error", "")


def test_host_key_failure_remediation_on_test_machine(tmp_path: Path) -> None:
    """test_machine must also surface host-key remediation to the agent."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    with patch("ssh_tools.manager.subprocess.run", return_value=_failed_result(_HOST_KEY_STDERR)):
        result = mgr.test_machine("h")

    assert result["success"] is False
    assert "accept-new" in result["error"]
    assert "ssh-keyscan" in result["error"]


# ---------------------------------------------------------------------------
# SSH-2 — per-host key memory
# ---------------------------------------------------------------------------


def test_successful_auth_persists_key_per_machine(tmp_path: Path) -> None:
    """After a successful connection, the working key is remembered per machine."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1", key="~/.ssh/custom_key"))

    with patch("ssh_tools.manager.subprocess.run", return_value=_ok_result()):
        result = mgr.run_command("h", "echo hi")

    assert result["success"] is True
    assert result["key_used"] == "~/.ssh/custom_key"

    # Persisted through the manager's storage accessors: reload machines
    reloaded = mgr.get_machine("h")
    assert reloaded is not None
    assert reloaded.key == "~/.ssh/custom_key"


def test_key_memory_survives_manager_restart(tmp_path: Path) -> None:
    """The remembered key must be durable across manager instances."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1", key="~/.ssh/id_ed25519"))

    with patch("ssh_tools.manager.subprocess.run", return_value=_ok_result()):
        mgr.run_command("h", "echo hi")

    # Fresh manager over the same data dir — key must still be remembered
    fresh = _make_manager(tmp_path)
    machine = fresh.get_machine("h")
    assert machine is not None
    assert machine.key == "~/.ssh/id_ed25519"


def test_key_memory_records_machine_without_key(tmp_path: Path) -> None:
    """Machines registered without a key keep that fact recorded on success."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))  # key defaults to ""

    with patch("ssh_tools.manager.subprocess.run", return_value=_ok_result()):
        result = mgr.run_command("h", "echo hi")

    assert result["success"] is True
    assert result["key_used"] == ""

    reloaded = mgr.get_machine("h")
    assert reloaded is not None
    assert reloaded.key == ""


def test_key_memory_not_recordered_on_failure(tmp_path: Path) -> None:
    """A failed authentication must not be recorded as the remembered key."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1", key="~/.ssh/wrong_key"))

    with patch(
        "ssh_tools.manager.subprocess.run",
        return_value=_failed_result("Permission denied (publickey)."),
    ):
        result = mgr.run_command("h", "echo hi")

    assert result["success"] is False
    assert "key_used" not in result
    # The stored key must be left untouched
    reloaded = mgr.get_machine("h")
    assert reloaded is not None
    assert reloaded.key == "~/.ssh/wrong_key"


def test_failed_auth_reports_attempted_keys(tmp_path: Path) -> None:
    """Failed auth must report exactly which key was attempted."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1", key="~/.ssh/id_ed25519"))

    with patch(
        "ssh_tools.manager.subprocess.run",
        return_value=_failed_result("Permission denied (publickey)."),
    ):
        result = mgr.run_command("h", "echo hi")

    assert result["success"] is False
    assert result["keys_attempted"] == ["~/.ssh/id_ed25519"]


def test_failed_auth_reports_default_identities_when_no_key(tmp_path: Path) -> None:
    """Without a registered key, the default identities ssh tries are reported."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))  # key defaults to ""

    with patch(
        "ssh_tools.manager.subprocess.run",
        return_value=_failed_result("Permission denied (publickey)."),
    ):
        result = mgr.run_command("h", "echo hi")

    assert result["success"] is False
    assert result["keys_attempted"] == ["~/.ssh/id_ed25519", "~/.ssh/id_rsa"]


def test_failed_auth_reports_attempted_keys_via_handler(tmp_path: Path) -> None:
    """The ssh_terminal handler must surface attempted keys to the agent."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1", key="~/.ssh/id_rsa"))
    handler = handle_ssh_terminal(mgr)

    with patch(
        "ssh_tools.manager.subprocess.run",
        return_value=_failed_result("Permission denied (publickey)."),
    ):
        result = json.loads(handler({"machine": "h", "command": "echo hi"}))

    assert result["success"] is False
    assert result["keys_attempted"] == ["~/.ssh/id_rsa"]


def test_host_key_failure_does_not_report_attempted_keys(tmp_path: Path) -> None:
    """A host-key failure is not a key-auth failure; keys_attempted stays absent."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1", key="~/.ssh/custom"))

    with patch("ssh_tools.manager.subprocess.run", return_value=_failed_result(_HOST_KEY_STDERR)):
        result = mgr.run_command("h", "echo hi")

    assert result["success"] is False
    assert "keys_attempted" not in result


# ---------------------------------------------------------------------------
# SSH-1/SSH-2 — background path (polled output must behave like sync output)
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


def test_background_host_key_failure_includes_remediation(tmp_path: Path) -> None:
    """Polled background output must also carry host-key remediation."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1"))

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        bg = mgr.run_command("h", "cmd", background=True)
    sid = bg["session_id"]

    fake_proc.poll.return_value = 255
    fake_proc.returncode = 255
    fake_proc.stdout.read.return_value = b""
    fake_proc.stderr.read.return_value = b"Host key verification failed."

    result = mgr.poll_session(sid)
    assert result["success"] is False
    assert "accept-new" in result["error"]
    assert "ssh-keyscan" in result["error"]


def test_background_auth_failure_reports_attempted_keys(tmp_path: Path) -> None:
    """Polled background auth failures must report which keys were attempted."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1", key="~/.ssh/id_ed25519"))

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        bg = mgr.run_command("h", "cmd", background=True)
    sid = bg["session_id"]

    fake_proc.poll.return_value = 255
    fake_proc.returncode = 255
    fake_proc.stdout.read.return_value = b""
    fake_proc.stderr.read.return_value = b"Permission denied (publickey)."

    result = mgr.poll_session(sid)
    assert result["success"] is False
    assert result["keys_attempted"] == ["~/.ssh/id_ed25519"]


def test_background_success_reports_key_used(tmp_path: Path) -> None:
    """Polled background success must report which key authenticated."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="h", host="1.1.1.1", key="~/.ssh/bg_key"))

    fake_proc = _fake_running_popen()
    with patch("ssh_tools.manager.subprocess.Popen", return_value=fake_proc):
        bg = mgr.run_command("h", "cmd", background=True)
    sid = bg["session_id"]

    fake_proc.poll.return_value = 0
    fake_proc.returncode = 0
    fake_proc.stdout.read.return_value = b"done\n"
    fake_proc.stderr.read.return_value = b""

    result = mgr.poll_session(sid)
    assert result["success"] is True
    assert result["key_used"] == "~/.ssh/bg_key"
