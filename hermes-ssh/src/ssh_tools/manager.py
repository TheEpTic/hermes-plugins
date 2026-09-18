"""SSHManager — thin facade over registry, sessions, executor, and audit log.

Owns shared state (config, locks, store, idle checker) and composes the
focused components. Public methods delegate; see registry.py, sessions.py,
exec.py, and audit.py for the actual logic.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

from .audit import AuditLog, redact_command
from .config import DEFAULT_CONFIG, SSHConfig
from .exec import Executor, build_ssh_args
from .helpers import read_json, write_json_atomic
from .models import Machine, Session
from .registry import MachineRegistry
from .sessions import SessionStore
from .storage import EncryptedStore
from .validate import validate_machine_name

logger = logging.getLogger(__name__)

__all__ = ["SSHManager"]

# Backwards-compatible alias for tests importing the private seam.
_redact_command = redact_command


class SSHManager:
    """Owns all SSH plugin state: machines, sessions, connections.

    Thread-safe. Designed for injection and testing with a custom config.
    """

    def __init__(self, config: SSHConfig | None = None) -> None:
        self._config = config or DEFAULT_CONFIG
        self._lock = threading.Lock()
        self._checker_thread: threading.Thread | None = None
        self._checker_event = threading.Event()
        self._config.ensure_dirs()
        self._store = EncryptedStore(self._config.data_dir)
        # Auto-migrate plaintext machines.json to encrypted
        self._store.migrate_plaintext("machines.json")
        self._registry = MachineRegistry(self._config, self._store, self._lock)
        self._sessions = SessionStore(self._config, self._lock)
        self._audit = AuditLog(self._config.data_dir, self._config.audit_log_mode)
        self._exec = Executor(self._config, self._registry, self._sessions, self._audit)

    @property
    def config(self) -> SSHConfig:
        return self._config

    @property
    def _audit_log_path(self) -> Path:
        return self._audit.path

    # ----- Backwards-compatible views into the executor (kept for tests) -----

    @property
    def _processes(self) -> dict[str, Any]:
        return self._exec._processes

    @property
    def _background_outputs(self) -> dict[str, tuple[Path, Path, int]]:
        return self._exec._outputs

    @property
    def _background_meta(self) -> dict[str, tuple[str, str, float, float]]:
        return self._exec._meta

    # ----- JSON persistence (backwards-compatible seams for tests) -----

    def _read_json(self, path: Path, default: Any) -> Any:
        return read_json(path, default)

    def _write_json(self, path: Path, data: Any) -> None:
        write_json_atomic(path, data)

    # ----- Machine registry -----

    _validate_machine_name = staticmethod(validate_machine_name)

    def _load_machines(self) -> dict[str, dict[str, Any]]:
        return self._registry._load()

    def _save_machines(self, machines: dict[str, dict[str, Any]]) -> None:
        self._registry._save(machines)

    def list_machines(self) -> dict[str, Machine]:
        return self._registry.list_machines()

    def get_machine(self, name: str) -> Machine | None:
        return self._registry.get(name)

    def resolve_name(self, name: str) -> str | None:
        """Resolve a name or alias to canonical machine name."""
        return self._registry.resolve_name(name)

    def add_machine(self, machine: Machine) -> Machine:
        """Add or update a machine. Returns the stored machine."""
        return self._registry.add(machine)

    def remove_machine(self, name: str) -> bool:
        return self._registry.remove(name)

    def test_machine(self, name: str) -> dict[str, Any]:
        return self._exec.test_machine(name)

    # ----- Session tracking -----

    def _load_sessions(self) -> dict[str, dict[str, Any]]:
        return self._sessions._load()

    def _save_sessions(self, sessions: dict[str, dict[str, Any]]) -> None:
        self._sessions._save(sessions)

    def list_sessions(self, status: str = "active") -> dict[str, Session]:
        return self._sessions.list_sessions(status)

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def register_session(self, session: Session) -> None:
        self._sessions.register(session)

    def touch_session(self, session_id: str) -> None:
        self._sessions.touch(session_id)

    def _cleanup_output_files(self, session_id: str) -> None:
        self._sessions.cleanup_output_files(session_id)

    def close_session(self, session_id: str, *, cleanup_output_files: bool = True) -> None:
        if cleanup_output_files:
            self._sessions.cleanup_output_files(session_id)
        self._exec.detach_process(session_id)
        self._sessions.mark(session_id, "closed")

    def remove_session(self, session_id: str) -> None:
        self._sessions.remove(session_id)

    def kill_session(self, session_id: str) -> dict[str, Any]:
        """Kill a background SSH process tracked by this manager instance.

        Persisted PIDs are never signalled: after a Hermes restart they may
        identify an unrelated recycled process. Shared ControlMaster sockets
        are connection state and are deliberately left alive.
        """
        result = self._exec.kill(session_id)
        if result.get("success"):
            self.close_session(session_id)
        return result

    def _mark_session_orphaned(self, session_id: str) -> None:
        self._sessions.mark(session_id, "orphaned")

    def cleanup_idle(self, max_idle_minutes: int | None = None) -> dict[str, Any]:
        """Kill all sessions idle for more than max_idle_minutes."""
        threshold = (max_idle_minutes or self._config.idle_timeout_minutes) * 60
        active = self._sessions.list_sessions("active")
        to_kill = [
            sid
            for sid, session in active.items()
            if session.idle_seconds is not None and session.idle_seconds > threshold
        ]
        killed = [
            {"session_id": sid, "machine": active[sid].machine, **self.kill_session(sid)}
            for sid in to_kill
        ]
        return {"killed": killed, "count": len(killed)}

    def _close_sessions_batch(self, session_ids: list[str]) -> None:
        """Mark multiple sessions as closed in a single file write."""
        self._sessions.mark_many(session_ids, "closed")

    def prune_closed(self, max_age_hours: int | None = None) -> int:
        """Remove closed sessions older than max_age_hours."""
        return self._sessions.prune_closed(max_age_hours, self._config.closed_prune_hours)

    # ----- SSH execution -----

    def _build_ssh_args(
        self, machine: Machine, command: str, control_path: str = "", timeout: int = 30
    ) -> list[str]:
        return build_ssh_args(self._config, machine, command, control_path, timeout)

    def run_command(
        self,
        machine_name: str,
        command: str,
        timeout: object | None = None,
        new_session: bool = False,
        background: bool = False,
        max_output_chars: object = 50_000,
    ) -> dict[str, Any]:
        """Run a command on a remote machine via SSH.

        Args:
            machine_name: Name or alias of the target machine.
            command: Shell command to execute remotely.
            timeout: Override for the command timeout (seconds).
            new_session: If True, skip SSH multiplexing / control socket.
            background: If True, launch via Popen and return immediately.
            max_output_chars: Truncate stdout/stderr beyond this length.
        """
        return self._exec.run_command(
            machine_name, command, timeout, new_session, background, max_output_chars
        )

    def _maybe_save_output(
        self, text: str, max_chars: int, session_id: str, stream: str
    ) -> tuple[str, str | None]:
        return self._exec._maybe_save_output(text, max_chars, session_id, stream)

    def _log_command(
        self,
        machine: str,
        command: str,
        exit_code: int | None,
        elapsed: float,
        session_id: str,
    ) -> None:
        self._audit.log_command(machine, command, exit_code, elapsed, session_id)

    # ----- Background process helpers -----

    def poll_session(self, session_id: str) -> dict[str, Any]:
        """Check whether a background process is running and collect it when complete."""
        return self._exec.poll(session_id, self.close_session)

    def read_output(self, session_id: str) -> dict[str, Any]:
        """Read output from a completed background process."""
        return self._exec.read_output(session_id, self.close_session)

    # ----- Audit log -----

    def list_command_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the last *limit* entries from the command audit log."""
        return self._audit.tail(limit)

    # ----- Background idle checker -----

    def start_idle_checker(self) -> None:
        if self._checker_thread is not None and self._checker_thread.is_alive():
            return

        _prune_counter: list[int] = [0]  # mutable counter shared by closure
        _PRUNE_EVERY = 10  # prune every 10 idle-check cycles

        def _loop() -> None:
            while not self._checker_event.is_set():
                with contextlib.suppress(Exception):
                    self.cleanup_idle()
                _prune_counter[0] += 1
                if _prune_counter[0] >= _PRUNE_EVERY:
                    _prune_counter[0] = 0
                    with contextlib.suppress(Exception):
                        self.prune_closed()
                self._checker_event.wait(self._config.idle_check_interval)

        self._checker_event.clear()
        self._checker_thread = threading.Thread(target=_loop, daemon=True)
        self._checker_thread.start()

    def stop_idle_checker(self) -> None:
        self._checker_event.set()
        thread = self._checker_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self._config.idle_check_interval + 1))
        self._checker_thread = None
