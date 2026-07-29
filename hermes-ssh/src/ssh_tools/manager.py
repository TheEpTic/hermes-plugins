"""Core SSH manager — single class owning all state and operations.

Consolidates machine registry, session tracking, and SSH execution.
No module-level mutable state — everything lives on the instance.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from .config import DEFAULT_CONFIG, SSHConfig
from .models import Machine, Session
from .storage import EncryptedStore

logger = logging.getLogger(__name__)

_HOST_RE = re.compile(r"[A-Za-z0-9_.:-]{1,253}")
_USER_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_MAX_OUTPUT_RETURN_CHARS = 500_000
_AUDIT_MODES = frozenset({"redacted", "metadata", "off"})
_SENSITIVE_NAME = (
    r"(?:[a-z0-9]+[_-])*"
    r"(?:password|passwd|token|api[_-]?key|secret|authorization)"
    r"(?:[_-][a-z0-9]+)*"
)
_SECRET_VALUE = r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s;&|]+)"""
_AUDIT_ASSIGNMENT_RE = re.compile(rf"(?i)\b({_SENSITIVE_NAME})(\s*=\s*)({_SECRET_VALUE})")
_AUDIT_FLAG_RE = re.compile(rf"(?i)(--{_SENSITIVE_NAME}(?:=|\s+))({_SECRET_VALUE})")
_AUDIT_URL_RE = re.compile(r"(?i)(https?://[^:/\s]+:)([^@\s]+)(@)")
_AUDIT_HEADER_RE = re.compile(
    r"(?i)([\"']?(?:[a-z0-9]+[-_])*(?:authorization|api[-_]?key|token|secret)\s*:\s*)"
    r"(?:(?:bearer|basic)\s+)?([^\"'\s;&|]+)([\"']?)"
)


def _redact_command(command: str) -> str:
    """Remove common inline credentials before persisting command text."""
    redacted = _AUDIT_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>", command
    )
    redacted = _AUDIT_FLAG_RE.sub(lambda match: f"{match.group(1)}<redacted>", redacted)
    redacted = _AUDIT_URL_RE.sub(
        lambda match: f"{match.group(1)}<redacted>{match.group(3)}", redacted
    )
    redacted = _AUDIT_HEADER_RE.sub(
        lambda match: f"{match.group(1)}<redacted>{match.group(3)}", redacted
    )
    return redacted


# ---------------------------------------------------------------------------
# SSH Manager
# ---------------------------------------------------------------------------


class SSHManager:
    """Owns all SSH plugin state: machines, sessions, connections.

    Thread-safe. Designed for injection and testing with a custom config.
    """

    def __init__(self, config: SSHConfig | None = None) -> None:
        self._config = config or DEFAULT_CONFIG
        self._lock = threading.Lock()
        self._checker_thread: threading.Thread | None = None
        self._checker_event = threading.Event()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._background_outputs: dict[str, tuple[Path, Path, int]] = {}
        self._process_lock = threading.Lock()
        self._config.ensure_dirs()
        self._store = EncryptedStore(self._config.data_dir)
        # Auto-migrate plaintext machines.json to encrypted
        self._store.migrate_plaintext("machines.json")

    @property
    def config(self) -> SSHConfig:
        return self._config

    @property
    def _audit_log_path(self) -> Path:
        return self._config.data_dir / "command_log.jsonl"

    # ----- JSON persistence -----

    def _read_json(self, path: Path, default: Any) -> Any:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Corrupt data in %s, resetting: %s", path, exc)
                return default
        return default

    def _write_json(self, path: Path, data: Any) -> None:
        """Atomic write: write to temp file then os.replace()."""
        self._config.data_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._config.data_dir),
            suffix=".tmp",
            prefix=path.stem,
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(path))
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    # ----- Machine registry (encrypted) -----

    def _load_machines(self) -> dict[str, dict[str, Any]]:
        raw = self._store.read("machines.json", {"machines": {}})
        result = raw.get("machines", {})
        if not isinstance(result, dict):
            logger.warning("Corrupt machines.json structure, resetting")
            return {}
        return result

    def _save_machines(self, machines: dict[str, dict[str, Any]]) -> None:
        self._store.write("machines.json", {"machines": machines})

    def list_machines(self) -> dict[str, Machine]:
        raw = self._load_machines()
        return {name: Machine.from_dict(name, d) for name, d in raw.items()}

    def get_machine(self, name: str) -> Machine | None:
        machines = self._load_machines()
        # Direct match
        if name in machines:
            return Machine.from_dict(name, machines[name])
        # Alias match
        for mname, mdata in machines.items():
            if name in mdata.get("aliases", []):
                return Machine.from_dict(mname, mdata)
        return None

    def resolve_name(self, name: str) -> str | None:
        """Resolve a name or alias to canonical machine name.

        Shared lookup used by get_machine, remove_machine, and run_command.
        """
        machines = self._load_machines()
        if name in machines:
            return name
        for mname, mdata in machines.items():
            if name in mdata.get("aliases", []):
                return mname
        return None

    @staticmethod
    def _validate_machine_name(name: object) -> str | None:
        """Return an error message if the machine name is unsafe, else None."""
        if not isinstance(name, str):
            return "Machine name must be a string"
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", name):
            return (
                "Machine name must be 1-64 chars, alphanumeric, dots, hyphens, "
                "underscores — no slashes, spaces, or glob metacharacters"
            )
        return None

    @staticmethod
    def _validate_host(host: object) -> str | None:
        """Return an error message if an SSH host string is unsafe."""
        if not isinstance(host, str) or not host:
            return "Host must be a non-empty string"
        if host.startswith("-") or "@" in host or not _HOST_RE.fullmatch(host):
            return (
                "Host must be a hostname/IP with no spaces, @ signs, slashes, or shell characters"
            )
        return None

    @staticmethod
    def _validate_user(user: object) -> str | None:
        """Return an error message if an SSH username is unsafe."""
        if not isinstance(user, str) or not _USER_RE.fullmatch(user):
            return "User must be 1-64 chars: letters, numbers, dots, hyphens, underscores"
        return None

    @staticmethod
    def _coerce_port(port: object) -> int:
        """Validate and normalize an SSH port."""
        if isinstance(port, bool):
            raise ValueError("Port must be an integer from 1 to 65535")
        try:
            port_int = int(port) if isinstance(port, str) else port
        except ValueError as exc:
            raise ValueError("Port must be an integer from 1 to 65535") from exc
        if not isinstance(port_int, int) or not 1 <= port_int <= 65535:
            raise ValueError("Port must be an integer from 1 to 65535")
        return port_int

    @staticmethod
    def _validate_key_path(key: object) -> str | None:
        """Return an error message if an SSH key path is unsafe."""
        if not isinstance(key, str):
            return "Key path must be a string"
        if any(ch in key for ch in ("\x00", "\n", "\r")):
            return "Key path must not contain control characters"
        return None

    @classmethod
    def _validate_machine(cls, machine: Machine) -> Machine:
        """Validate and normalize a machine before it is persisted."""
        for error in (
            cls._validate_machine_name(machine.name),
            cls._validate_host(machine.host),
            cls._validate_user(machine.user),
            cls._validate_key_path(machine.key),
        ):
            if error:
                raise ValueError(error)
        port = cls._coerce_port(machine.port)
        aliases = machine.aliases or []
        tags = machine.tags or []
        if not isinstance(aliases, list) or any(cls._validate_machine_name(a) for a in aliases):
            raise ValueError("Aliases must be a list of safe machine names")
        if not isinstance(tags, list) or any(not isinstance(t, str) or len(t) > 64 for t in tags):
            raise ValueError("Tags must be a list of strings up to 64 chars")
        if not isinstance(machine.description, str):
            raise ValueError("Description must be a string")
        if port != machine.port or aliases is not machine.aliases or tags is not machine.tags:
            machine = replace(machine, port=port, aliases=aliases, tags=tags)
        return machine

    def add_machine(self, machine: Machine) -> Machine:
        """Add or update a machine. Returns the stored machine."""
        machine = self._validate_machine(machine)
        if not machine.added:
            machine = replace(machine, added=datetime.now(UTC).isoformat())
        with self._lock:
            machines = self._load_machines()
            machines[machine.name] = machine.to_dict()
            self._save_machines(machines)
        return machine

    def remove_machine(self, name: str) -> bool:
        with self._lock:
            machines = self._load_machines()
            canonical = (
                name
                if name in machines
                else next(
                    (mn for mn, md in machines.items() if name in md.get("aliases", [])),
                    "",
                )
            )
            if canonical and canonical in machines:
                del machines[canonical]
                self._save_machines(machines)
                return True
        return False

    def test_machine(self, name: str) -> dict[str, Any]:
        machine = self.get_machine(name)
        if not machine:
            return {"success": False, "error": f"Machine '{name}' not found"}

        cmd = self._build_ssh_args(machine, "echo ok", timeout=self._config.connect_timeout)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._config.connect_timeout + 5,
            )
            if result.returncode == 0 and "ok" in result.stdout:
                return {"success": True, "status": "connected", "host": machine.host}
            return {
                "success": False,
                "status": "unreachable",
                "host": machine.host,
                "error": result.stderr.strip() or f"exit code {result.returncode}",
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "status": "timeout",
                "host": machine.host,
                "error": "Connection timed out",
            }
        except Exception as e:
            return {"success": False, "status": "error", "host": machine.host, "error": str(e)}

    # ----- Session tracking -----

    def _load_sessions(self) -> dict[str, dict[str, Any]]:
        raw = self._read_json(self._config.sessions_file, {"sessions": {}})
        result = raw.get("sessions", {})
        if not isinstance(result, dict):
            logger.warning("Corrupt sessions.json structure, resetting")
            return {}
        return result

    def _save_sessions(self, sessions: dict[str, dict[str, Any]]) -> None:
        self._write_json(self._config.sessions_file, {"sessions": sessions})

    def list_sessions(self, status: str = "active") -> dict[str, Session]:
        raw = self._load_sessions()
        return {
            sid: Session.from_dict(sid, d)
            for sid, d in raw.items()
            if not status or d.get("status") == status
        }

    def get_session(self, session_id: str) -> Session | None:
        raw = self._load_sessions()
        if session_id in raw:
            return Session.from_dict(session_id, raw[session_id])
        return None

    def register_session(self, session: Session) -> None:
        now = datetime.now(UTC).isoformat()
        if not session.started:
            session = replace(
                session,
                started=now,
                last_active=now,
                command_count=0,
                status="active",
            )
        with self._lock:
            sessions = self._load_sessions()
            sessions[session.id] = session.to_dict()
            self._save_sessions(sessions)

    def touch_session(self, session_id: str) -> None:
        with self._lock:
            sessions = self._load_sessions()
            if session_id in sessions:
                sessions[session_id]["last_active"] = datetime.now(UTC).isoformat()
                sessions[session_id]["command_count"] = (
                    sessions[session_id].get("command_count", 0) + 1
                )
                self._save_sessions(sessions)

    def _cleanup_output_files(self, session_id: str) -> None:
        """Remove any saved output files for this session."""
        prefix = f"ssh_output_{session_id}_"
        try:
            for p in self._config.output_dir.iterdir():
                if p.name.startswith(prefix) and p.name.endswith(".txt"):
                    with contextlib.suppress(OSError):
                        p.unlink()
        except OSError:
            pass

    def close_session(self, session_id: str, *, cleanup_output_files: bool = True) -> None:
        if cleanup_output_files:
            self._cleanup_output_files(session_id)
        with self._process_lock:
            self._processes.pop(session_id, None)
            self._background_outputs.pop(session_id, None)
        with self._lock:
            sessions = self._load_sessions()
            if session_id in sessions:
                sessions[session_id]["status"] = "closed"
                self._save_sessions(sessions)

    def remove_session(self, session_id: str) -> None:
        self._cleanup_output_files(session_id)
        with self._lock:
            sessions = self._load_sessions()
            sessions.pop(session_id, None)
            self._save_sessions(sessions)

    def kill_session(self, session_id: str) -> dict[str, Any]:
        """Kill a background SSH process tracked by this manager instance.

        Persisted PIDs are never signalled: after a Hermes restart they may
        identify an unrelated recycled process. Shared ControlMaster sockets
        are connection state and are deliberately left alive.
        """
        session = self.get_session(session_id)
        if not session:
            return {"success": False, "error": f"Session '{session_id}' not found"}

        with self._process_lock:
            proc = self._processes.pop(session_id, None)
        if proc is None:
            with self._lock:
                sessions = self._load_sessions()
                if session_id in sessions:
                    sessions[session_id]["status"] = "orphaned"
                    self._save_sessions(sessions)
            return {
                "success": False,
                "error": "Session is not owned by this Hermes process; refusing to signal persisted PID",
                "status": "orphaned",
            }

        killed = proc.poll() is not None
        if not killed:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=2)
            except ProcessLookupError:
                pass
            killed = True

        self.close_session(session_id)
        return {"success": True, "pid_killed": killed, "socket_closed": False}

    def cleanup_idle(self, max_idle_minutes: int | None = None) -> dict[str, Any]:
        """Kill all sessions idle for more than max_idle_minutes."""
        threshold = (max_idle_minutes or self._config.idle_timeout_minutes) * 60
        active = self.list_sessions("active")
        # Collect IDs of sessions that need killing (single read pass)
        to_kill = []
        for sid, session in active.items():
            idle = session.idle_seconds
            if idle is not None and idle > threshold:
                to_kill.append(sid)

        killed: list[dict[str, Any]] = []
        for sid in to_kill:
            session = active[sid]
            result = self.kill_session(sid)
            killed.append({"session_id": sid, "machine": session.machine, **result})

        return {"killed": killed, "count": len(killed)}

    def _close_sessions_batch(self, session_ids: list[str]) -> None:
        """Mark multiple sessions as closed in a single file write."""
        if not session_ids:
            return
        for sid in session_ids:
            self._cleanup_output_files(sid)
        with self._lock:
            sessions = self._load_sessions()
            for sid in session_ids:
                if sid in sessions:
                    sessions[sid]["status"] = "closed"
            self._save_sessions(sessions)

    def prune_closed(self, max_age_hours: int | None = None) -> int:
        """Remove closed sessions older than max_age_hours."""
        hours = max_age_hours or self._config.closed_prune_hours
        with self._lock:
            raw = self._load_sessions()
            now = datetime.now(UTC)
            to_remove = []
            for sid, sdata in raw.items():
                if sdata.get("status") != "active":
                    try:
                        started = datetime.fromisoformat(sdata.get("started", ""))
                        if started.tzinfo is None:
                            started = started.replace(tzinfo=UTC)
                        if (now - started).total_seconds() > hours * 3600:
                            to_remove.append(sid)
                    except (ValueError, TypeError):
                        pass
            for sid in to_remove:
                del raw[sid]
            if to_remove:
                self._save_sessions(raw)
        return len(to_remove)

    # ----- SSH execution -----

    def _build_ssh_args(
        self,
        machine: Machine,
        command: str,
        control_path: str = "",
        timeout: int = 30,
    ) -> list[str]:
        cmd = [
            "ssh",
            "-o",
            f"ConnectTimeout={min(timeout, 10)}",
            "-o",
            f"StrictHostKeyChecking={self._config.strict_host_key_checking}",
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "RequestTTY=no",
            "-p",
            str(machine.port),
        ]
        if machine.key:
            cmd.extend(["-i", machine.key])
        if control_path:
            cmd.extend(
                [
                    "-o",
                    "ControlMaster=auto",
                    "-o",
                    f"ControlPath={control_path}",
                    "-o",
                    "ControlPersist=300",
                ]
            )
        cmd.append(f"{machine.user}@{machine.host}")
        # Wrap in bash -c to ensure bash features (pipefail, [[, etc.) work
        # regardless of the remote user's default shell.  set -o pipefail
        # makes pipeline exit codes predictable (non-zero if any component
        # fails, not just the last command).
        wrapped = f"set -o pipefail; {command}"
        cmd.extend(["bash", "-c", shlex.quote(wrapped)])
        return cmd

    def _normalize_timeout(self, timeout: object | None) -> int:
        """Normalize a caller-supplied timeout, falling back to the configured default."""
        if timeout is None:
            return self._config.command_timeout
        if isinstance(timeout, bool):
            raise ValueError("timeout must be a positive integer")
        try:
            timeout_int = int(timeout) if isinstance(timeout, str) else timeout
        except ValueError as exc:
            raise ValueError("timeout must be a positive integer") from exc
        if not isinstance(timeout_int, int):
            raise ValueError("timeout must be a positive integer")
        return timeout_int if timeout_int > 0 else self._config.command_timeout

    def _normalize_max_output_chars(self, max_output_chars: object) -> int:
        """Clamp caller-supplied output limits to a safe server-side maximum."""
        if isinstance(max_output_chars, bool):
            raise ValueError("max_output_chars must be a positive integer")
        try:
            limit = int(max_output_chars) if isinstance(max_output_chars, str) else max_output_chars
        except ValueError as exc:
            raise ValueError("max_output_chars must be a positive integer") from exc
        if not isinstance(limit, int):
            raise ValueError("max_output_chars must be a positive integer")
        if limit <= 0:
            return self._config.max_output_chars
        return min(limit, _MAX_OUTPUT_RETURN_CHARS)

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
        if not isinstance(machine_name, str) or not machine_name:
            return {"success": False, "error": "machine_name must be a non-empty string"}
        if not isinstance(command, str) or not command.strip():
            return {"success": False, "error": "command must be a non-empty string"}
        try:
            timeout = self._normalize_timeout(timeout)
            max_output_chars = self._normalize_max_output_chars(max_output_chars)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "exit_code": -1}

        machine = self.get_machine(machine_name)
        if not machine:
            return {"success": False, "error": f"Machine '{machine_name}' not found."}

        canonical = machine.name

        session_id = f"ssh_{canonical}_{uuid.uuid4().hex[:8]}"
        control_path = "" if new_session else str(self._config.socket_dir / f"{canonical}.sock")
        ssh_args = self._build_ssh_args(machine, command, control_path, timeout)

        start_time = time.monotonic()

        # ---- background path ----
        if background:
            stdout_path: Path | None = None
            stderr_path: Path | None = None
            stdout_handle: BinaryIO | None = None
            stderr_handle: BinaryIO | None = None
            proc: subprocess.Popen[bytes] | None = None
            try:
                stdout_path, stdout_handle = self._open_background_output(session_id, "stdout")
                stderr_path, stderr_handle = self._open_background_output(session_id, "stderr")
                proc = subprocess.Popen(
                    ssh_args,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    start_new_session=True,
                )
                stdout_handle.close()
                stderr_handle.close()
                stdout_handle = None
                stderr_handle = None

                self.register_session(
                    Session(
                        id=session_id, machine=canonical, pid=proc.pid, control_path=control_path
                    )
                )
                with self._process_lock:
                    self._processes[session_id] = proc
                    self._background_outputs[session_id] = (
                        stdout_path,
                        stderr_path,
                        max_output_chars,
                    )
                elapsed = round(time.monotonic() - start_time, 2)
                self._log_command(
                    canonical, command, exit_code=None, elapsed=elapsed, session_id=session_id
                )
                return {
                    "success": True,
                    "background": True,
                    "pid": proc.pid,
                    "machine": canonical,
                    "session_id": session_id,
                }
            except Exception as e:
                if stdout_handle is not None:
                    stdout_handle.close()
                if stderr_handle is not None:
                    stderr_handle.close()
                if proc is not None and proc.poll() is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                for path in (stdout_path, stderr_path):
                    if path is not None:
                        with contextlib.suppress(OSError):
                            path.unlink()
                logger.debug("run_command (bg) failed for %s: %s", canonical, e, exc_info=True)
                return {"success": False, "error": str(e), "exit_code": -1, "machine": canonical}

        # ---- synchronous path ----
        try:
            result = subprocess.run(
                ssh_args,
                capture_output=True,
                text=True,
                timeout=timeout + 5,
            )
            elapsed = round(time.monotonic() - start_time, 2)

            stdout, stdout_file = self._maybe_save_output(
                result.stdout, max_output_chars, session_id, "stdout"
            )
            stderr, stderr_file = self._maybe_save_output(
                result.stderr, max_output_chars, session_id, "stderr"
            )

            self._log_command(canonical, command, result.returncode, elapsed, session_id)
            resp: dict[str, Any] = {
                "success": result.returncode == 0,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": result.returncode,
                "elapsed_secs": elapsed,
                "machine": canonical,
                "session_id": session_id,
            }
            if stdout_file:
                resp["stdout_file"] = stdout_file
            if stderr_file:
                resp["stderr_file"] = stderr_file
            return resp

        except subprocess.TimeoutExpired:
            elapsed = round(time.monotonic() - start_time, 2)
            self._log_command(
                canonical, command, exit_code=-1, elapsed=elapsed, session_id=session_id
            )
            return {
                "success": False,
                "error": f"Command timed out after {timeout}s",
                "exit_code": -1,
                "elapsed_secs": elapsed,
                "machine": canonical,
                "session_id": session_id,
            }
        except Exception as e:
            logger.debug("run_command failed for %s: %s", canonical, e, exc_info=True)
            elapsed = round(time.monotonic() - start_time, 2)
            self._log_command(
                canonical, command, exit_code=-1, elapsed=elapsed, session_id=session_id
            )
            return {"success": False, "error": str(e), "exit_code": -1, "machine": canonical}

    # ----- Output helpers -----

    def _maybe_save_output(
        self, text: str, max_chars: int, session_id: str, stream: str
    ) -> tuple[str, str | None]:
        """Return *(text_or_summary, file_path_or_None).

        If *text* fits within *max_chars* it is returned unchanged.
        Otherwise the full output is written to the plugin's restricted
        output directory and a short summary is returned — the caller should include the file
        path in its response so the LLM can ``read_file`` the rest.
        """
        if len(text) <= max_chars:
            return text, None
        path = self._config.output_dir / f"ssh_output_{session_id}_{stream}.txt"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(path), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        summary = (
            f"[output saved to {path} — {len(text):,} chars total, "
            f"first {max_chars:,} shown below]\n"
            f"{text[:max_chars]}"
        )
        return summary, str(path)

    def _open_background_output(self, session_id: str, stream: str) -> tuple[Path, BinaryIO]:
        """Open a restricted spool file so background processes cannot fill a pipe."""
        path = self._config.output_dir / f"ssh_output_{session_id}_{stream}.txt"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(path), flags, 0o600)
        return path, os.fdopen(fd, "wb")

    def _collect_background_output(
        self, path: Path, fallback_stream: Any, max_chars: int
    ) -> tuple[str, str | None]:
        """Read a completed spool, deleting short output and retaining large output."""
        try:
            raw = path.read_bytes()
        except OSError:
            raw = b""

        if not raw and fallback_stream is not None and hasattr(fallback_stream, "read"):
            with contextlib.suppress(Exception):
                fallback = fallback_stream.read()
                if isinstance(fallback, str):
                    raw = fallback.encode()
                elif isinstance(fallback, bytes):
                    raw = fallback

        text = raw.decode("utf-8", errors="replace")
        if len(text) <= max_chars:
            with contextlib.suppress(OSError):
                path.unlink()
            return text, None

        summary = (
            f"[output saved to {path} — {len(text):,} chars total, "
            f"first {max_chars:,} shown below]\n{text[:max_chars]}"
        )
        return summary, str(path)

    def _take_finished_background_process(
        self, session_id: str
    ) -> tuple[subprocess.Popen[bytes], Path, Path, int] | None:
        """Atomically detach a finished process and its output spools."""
        with self._process_lock:
            proc = self._processes.get(session_id)
            if proc is None or proc.poll() is None:
                return None
            outputs = self._background_outputs.get(session_id)
            if outputs is None:
                outputs = (
                    self._config.output_dir / f"ssh_output_{session_id}_stdout.txt",
                    self._config.output_dir / f"ssh_output_{session_id}_stderr.txt",
                    self._config.max_output_chars,
                )
            self._processes.pop(session_id, None)
            self._background_outputs.pop(session_id, None)
        return proc, *outputs

    def _finish_background_process(
        self,
        session_id: str,
        proc: subprocess.Popen[bytes],
        stdout_path: Path,
        stderr_path: Path,
        max_chars: int,
    ) -> dict[str, Any]:
        stdout, stdout_file = self._collect_background_output(stdout_path, proc.stdout, max_chars)
        stderr, stderr_file = self._collect_background_output(stderr_path, proc.stderr, max_chars)
        self.close_session(session_id, cleanup_output_files=False)
        exit_code = proc.returncode if proc.returncode is not None else proc.poll()
        response: dict[str, Any] = {
            "success": exit_code == 0,
            "session_id": session_id,
            "running": False,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
        }
        if stdout_file:
            response["stdout_file"] = stdout_file
        if stderr_file:
            response["stderr_file"] = stderr_file
        return response

    # ----- Background process helpers -----

    def poll_session(self, session_id: str) -> dict[str, Any]:
        """Check whether a background process is running and collect it when complete."""
        with self._process_lock:
            proc = self._processes.get(session_id)
            if proc is None:
                return {
                    "success": False,
                    "error": f"No background process for session '{session_id}'",
                }
            if proc.poll() is None:
                return {"success": True, "session_id": session_id, "running": True}

        finished = self._take_finished_background_process(session_id)
        if finished is None:
            return {"success": True, "session_id": session_id, "running": True}
        proc, stdout_path, stderr_path, max_chars = finished
        return self._finish_background_process(
            session_id, proc, stdout_path, stderr_path, max_chars
        )

    def read_output(self, session_id: str) -> dict[str, Any]:
        """Read output from a completed background process."""
        with self._process_lock:
            proc = self._processes.get(session_id)
            if proc is None:
                return {
                    "success": False,
                    "error": f"No background process for session '{session_id}'",
                }
            if proc.poll() is None:
                return {
                    "success": False,
                    "error": f"Process for session '{session_id}' is still running",
                }

        finished = self._take_finished_background_process(session_id)
        if finished is None:
            return {
                "success": False,
                "error": f"No background process for session '{session_id}'",
            }
        proc, stdout_path, stderr_path, max_chars = finished
        result = self._finish_background_process(
            session_id, proc, stdout_path, stderr_path, max_chars
        )
        result.pop("running", None)
        return result

    # ----- Audit log -----

    def _log_command(
        self,
        machine: str,
        command: str,
        exit_code: int | None,
        elapsed: float,
        session_id: str,
    ) -> None:
        """Append a redacted or metadata-only JSONL audit entry."""
        mode = self._config.audit_log_mode.strip().lower()
        if mode not in _AUDIT_MODES:
            logger.warning("Unknown audit_log_mode %r; using redacted", mode)
            mode = "redacted"
        if mode == "off":
            return

        redacted_command = _redact_command(command)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "machine": machine,
            "command_sha256": hashlib.sha256(redacted_command.encode()).hexdigest(),
            "command_length": len(redacted_command),
            "exit_code": exit_code,
            "elapsed_secs": elapsed,
            "session_id": session_id,
        }
        if mode == "redacted":
            entry["command"] = redacted_command
        try:
            self._config.data_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(
                str(self._audit_log_path),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            logger.debug("Failed to append to audit log %s", self._audit_log_path, exc_info=True)

    def list_command_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the last *limit* entries from the command audit log."""
        if not self._audit_log_path.exists():
            return []
        with self._audit_log_path.open("rb") as f:
            f.seek(0, 2)  # end
            size = f.tell()
            read_size = min(size, 64 * 1024)  # read last 64KB max
            f.seek(max(0, size - read_size))
            tail = f.read().decode("utf-8", errors="replace")
        lines = tail.splitlines()[-limit:]
        entries: list[dict[str, Any]] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        return entries

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
                time.sleep(self._config.idle_check_interval)

        self._checker_event.clear()
        self._checker_thread = threading.Thread(target=_loop, daemon=True)
        self._checker_thread.start()

    def stop_idle_checker(self) -> None:
        self._checker_event.set()
