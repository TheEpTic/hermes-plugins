"""SSH execution: sync commands, background processes, output spooling."""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from .audit import AuditLog
from .config import SSHConfig
from .helpers import coerce_int
from .models import Machine, Session
from .registry import MachineRegistry
from .sessions import SessionStore
from .transfers.transport import ssh_options

logger = logging.getLogger(__name__)

_MAX_OUTPUT_RETURN_CHARS = 500_000
_HOST_KEY_VERIFICATION_MARKERS = (
    "host key verification failed",
    "no hostkey alg",
    "remote host identification has changed",
    "host key mismatch",
)
_DEFAULT_IDENTITY_PATHS = ("~/.ssh/id_ed25519", "~/.ssh/id_rsa")


def build_ssh_args(
    config: SSHConfig,
    machine: Machine,
    command: str,
    control_path: str = "",
    timeout: int = 30,
) -> list[str]:
    cmd = ["ssh"]
    cmd += ssh_options(config, timeout, control_path or None)
    cmd += [
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "RequestTTY=no",
        "-p",
        str(machine.port),
    ]
    if machine.key:
        cmd.extend(["-i", machine.key])
    cmd.append(f"{machine.user}@{machine.host}")
    # Wrap in bash -c so pipefail/[[ work regardless of the remote login shell.
    cmd.extend(["bash", "-c", shlex.quote(f"set -o pipefail; {command}")])
    return cmd


def _is_host_key_failure(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in _HOST_KEY_VERIFICATION_MARKERS)


def _attempted_keys(machine: Machine) -> list[str]:
    """Keys OpenSSH tried, in order: -i key, else the ssh default identities."""
    if machine.key:
        return [machine.key]
    return list(_DEFAULT_IDENTITY_PATHS)


def _host_key_remediation(machine: Machine) -> str:
    return (
        f"Host key verification failed for {machine.user}@{machine.host}:"
        f" the host key is not trusted yet. Seed it first, e.g.\n"
        f"  ssh-keyscan -p {machine.port} {machine.host} >> ~/.ssh/known_hosts\n"
        f"or accept it interactively once (ssh -o StrictHostKeyChecking=accept-new "
        f"{machine.user}@{machine.host}) and retry this command."
    )


def _failure_context(machine: Machine, exit_code: int | None, stderr: str) -> dict[str, Any] | None:
    """SSH-1 remediation / SSH-2 attempted-keys for connection-level (255) failures."""
    if exit_code != 255:
        return None
    if _is_host_key_failure(stderr):
        return {"error": _host_key_remediation(machine)}
    if "permission denied" in stderr.lower():
        return {"keys_attempted": _attempted_keys(machine)}
    return None


def _open_spool(path: Path, exclusive: bool) -> Any:
    """Open a restricted spool file (O_NOFOLLOW where available)."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    return os.fdopen(fd, "wb" if exclusive else "w", encoding=None if exclusive else "utf-8")


def _summarize(text: str, path: Path, max_chars: int) -> str:
    return (
        f"[output saved to {path} — {len(text):,} chars total, "
        f"first {max_chars:,} shown below]\n"
        f"{text[:max_chars]}"
    )


def _kill_process_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, sig)


def _read_stream(stream: Any) -> bytes:
    """Read a fallback stream, normalising supported values to bytes."""
    if stream is None or not hasattr(stream, "read"):
        return b""
    try:
        out = stream.read()
    except Exception:
        return b""
    if isinstance(out, str):
        return out.encode()
    if isinstance(out, bytes):
        return out
    return b""


def _collected_response(
    session_id: str, state: bool | None, still_running_error: bool
) -> dict[str, Any]:
    """poll/read_output answer when there is nothing to collect.

    state: None = no process tracked, True = still running, False = raced away.
    """
    if not still_running_error and state is True:
        return {"success": True, "session_id": session_id, "running": True}
    error = (
        f"Process for session '{session_id}' is still running"
        if state is True
        else f"No background process for session '{session_id}'"
    )
    return {"success": False, "error": error}


def _coerce_positive(value: object, field: str, fallback: int) -> int:
    """Positive-int-or-fallback for soft limits; invalid means config default."""
    try:
        out = coerce_int(value, field, minimum=0)
    except ValueError:
        raise ValueError(f"{field} must be a positive integer") from None
    return fallback if out <= 0 else out


class Executor:
    """Runs SSH commands: sync, background, and background lifecycle."""

    def __init__(
        self,
        config: SSHConfig,
        registry: MachineRegistry,
        sessions: SessionStore,
        audit: AuditLog,
    ) -> None:
        self._config = config
        self._registry = registry
        self._sessions = sessions
        self._audit = audit
        self._lock = threading.Lock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._outputs: dict[str, tuple[Path, Path, int]] = {}
        self._meta: dict[str, tuple[str, str, float, float]] = {}
        self._timeouts: set[str] = set()

    def _normalize_timeout(self, timeout: object | None) -> int:
        if timeout is None:
            return self._config.command_timeout
        return _coerce_positive(timeout, "timeout", self._config.command_timeout)

    def _normalize_max_output_chars(self, max_output_chars: object) -> int:
        limit = _coerce_positive(
            max_output_chars, "max_output_chars", self._config.max_output_chars
        )
        return min(limit, _MAX_OUTPUT_RETURN_CHARS)

    def _finish_response(
        self,
        resp: dict[str, Any],
        machine: Machine,
        exit_code: int | None,
        stderr: str,
        *,
        success: bool,
    ) -> dict[str, Any]:
        """On success remember the working key; on failure add SSH-1/SSH-2 context."""
        if success:
            self._registry.remember_key(machine)
            resp["key_used"] = machine.key
            return resp
        failure = _failure_context(machine, exit_code, stderr)
        if failure is not None:
            resp.update(failure)
        return resp

    def test_machine(self, name: str) -> dict[str, Any]:
        machine = self._registry.get(name)
        if not machine:
            return {"success": False, "error": f"Machine '{name}' not found"}

        cmd = build_ssh_args(self._config, machine, "echo ok", timeout=self._config.connect_timeout)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._config.connect_timeout + 5,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "status": "timeout",
                "host": machine.host,
                "error": "Connection timed out",
            }
        except OSError as e:
            return {"success": False, "status": "error", "host": machine.host, "error": str(e)}
        if result.returncode == 0 and "ok" in result.stdout:
            self._registry.remember_key(machine)
            return {"success": True, "status": "connected", "host": machine.host}
        failure = _failure_context(machine, result.returncode, result.stderr)
        error = (
            failure["error"]
            if failure is not None and "error" in failure
            else result.stderr.strip() or f"exit code {result.returncode}"
        )
        resp: dict[str, Any] = {
            "success": False,
            "status": "unreachable",
            "host": machine.host,
            "error": error,
        }
        if failure is not None:
            resp.update({k: v for k, v in failure.items() if k != "error"})
        return resp

    def run_command(
        self,
        machine_name: str,
        command: str,
        timeout: object | None = None,
        new_session: bool = False,
        background: bool = False,
        max_output_chars: object = 50_000,
    ) -> dict[str, Any]:
        """Run a command on a remote machine via SSH."""
        if not isinstance(machine_name, str) or not machine_name:
            return {"success": False, "error": "machine_name must be a non-empty string"}
        if not isinstance(command, str) or not command.strip():
            return {"success": False, "error": "command must be a non-empty string"}
        try:
            timeout_secs = self._normalize_timeout(timeout)
            max_chars = self._normalize_max_output_chars(max_output_chars)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "exit_code": -1}

        machine = self._registry.get(machine_name)
        if not machine:
            return {"success": False, "error": f"Machine '{machine_name}' not found."}
        canonical = machine.name
        session_id = f"ssh_{canonical}_{uuid.uuid4().hex[:8]}"
        control_path = "" if new_session else str(self._config.socket_dir / f"{canonical}.sock")
        ssh_args = build_ssh_args(self._config, machine, command, control_path, timeout_secs)
        start_time = time.monotonic()

        return (
            self._run_background(
                machine,
                canonical,
                command,
                ssh_args,
                session_id,
                control_path,
                start_time,
                timeout_secs,
                max_chars,
            )
            if background
            else self._run_sync(
                machine,
                canonical,
                command,
                ssh_args,
                session_id,
                start_time,
                timeout_secs,
                max_chars,
            )
        )

    def _run_sync(
        self,
        machine: Machine,
        canonical: str,
        command: str,
        ssh_args: list[str],
        session_id: str,
        start_time: float,
        timeout: int,
        max_chars: int,
    ) -> dict[str, Any]:
        try:
            result = subprocess.run(ssh_args, capture_output=True, text=True, timeout=timeout + 5)
        except subprocess.TimeoutExpired:
            elapsed = round(time.monotonic() - start_time, 2)
            self._audit.log_command(canonical, command, -1, elapsed, session_id)
            return {
                "success": False,
                "error": f"Command timed out after {timeout}s",
                "exit_code": -1,
                "elapsed_secs": elapsed,
                "machine": canonical,
            }
        except OSError as e:
            logger.debug("run_command failed for %s: %s", canonical, e, exc_info=True)
            elapsed = round(time.monotonic() - start_time, 2)
            self._audit.log_command(canonical, command, -1, elapsed, session_id)
            return {"success": False, "error": str(e), "exit_code": -1, "machine": canonical}

        elapsed = round(time.monotonic() - start_time, 2)
        stdout, stdout_file = self._maybe_save_output(
            result.stdout, max_chars, session_id, "stdout"
        )
        stderr, stderr_file = self._maybe_save_output(
            result.stderr, max_chars, session_id, "stderr"
        )
        self._audit.log_command(canonical, command, result.returncode, elapsed, session_id)
        resp: dict[str, Any] = {
            "success": result.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.returncode,
            "elapsed_secs": elapsed,
            "machine": canonical,
        }
        if stdout_file:
            resp["stdout_file"] = stdout_file
        if stderr_file:
            resp["stderr_file"] = stderr_file
        return self._finish_response(
            resp, machine, result.returncode, result.stderr, success=result.returncode == 0
        )

    def _run_background(
        self,
        machine: Machine,
        canonical: str,
        command: str,
        ssh_args: list[str],
        session_id: str,
        control_path: str,
        start_time: float,
        timeout: int,
        max_chars: int,
    ) -> dict[str, Any]:
        del machine  # identity not needed; canonical name carries through
        spool = self._sessions.output_path
        stdout_path, stderr_path = spool(session_id, "stdout"), spool(session_id, "stderr")
        stdout_handle: BinaryIO | None = None
        stderr_handle: BinaryIO | None = None
        proc: subprocess.Popen[bytes] | None = None
        try:
            stdout_handle = _open_spool(stdout_path, exclusive=True)
            stderr_handle = _open_spool(stderr_path, exclusive=True)
            proc = subprocess.Popen(
                ssh_args, stdout=stdout_handle, stderr=stderr_handle, start_new_session=True
            )
            stdout_handle.close()
            stderr_handle.close()
            stdout_handle = stderr_handle = None
        except OSError as e:
            self._cleanup_failed_background(
                proc, stdout_handle, stderr_handle, stdout_path, stderr_path
            )
            logger.debug("run_command (bg) failed for %s: %s", canonical, e, exc_info=True)
            return {"success": False, "error": str(e), "exit_code": -1, "machine": canonical}

        with self._lock:
            self._processes[session_id] = proc
            self._outputs[session_id] = (stdout_path, stderr_path, max_chars)
            self._meta[session_id] = (canonical, command, start_time, start_time + timeout)
        self._sessions.register(
            Session(id=session_id, machine=canonical, pid=proc.pid, control_path=control_path)
        )
        self._audit.log_command(
            canonical, command, None, round(time.monotonic() - start_time, 2), session_id
        )
        threading.Thread(target=self._watch_timeout, args=(session_id,), daemon=True).start()
        return {
            "success": True,
            "background": True,
            "pid": proc.pid,
            "machine": canonical,
            "session_id": session_id,
        }

    def _maybe_save_output(
        self, text: str, max_chars: int, session_id: str, stream: str
    ) -> tuple[str, str | None]:
        """Return (text_or_summary, file_path_or_None); oversize text spills to a file."""
        if len(text) <= max_chars:
            return text, None
        path = self._sessions.output_path(session_id, stream)
        with _open_spool(path, exclusive=False) as f:
            f.write(text)
        return _summarize(text, path, max_chars), str(path)

    @staticmethod
    def _cleanup_failed_background(
        proc: subprocess.Popen[bytes] | None,
        stdout_handle: BinaryIO | None,
        stderr_handle: BinaryIO | None,
        stdout_path: Path | None,
        stderr_path: Path | None,
    ) -> None:
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()
        if proc is not None and proc.poll() is None:
            _kill_process_group(proc, signal.SIGKILL)
        for path in (stdout_path, stderr_path):
            if path is not None:
                with contextlib.suppress(OSError):
                    path.unlink()

    def _collect_output(
        self, path: Path, fallback_stream: Any, max_chars: int
    ) -> tuple[str, str | None]:
        """Read a completed spool, deleting short output and retaining large output."""
        try:
            raw = path.read_bytes()
        except OSError:
            raw = b""
        if not raw and fallback_stream is not None and hasattr(fallback_stream, "read"):
            raw = _read_stream(fallback_stream)
        text = raw.decode("utf-8", errors="replace")
        if len(text) <= max_chars:
            with contextlib.suppress(OSError):
                path.unlink()
            return text, None
        return _summarize(text, path, max_chars), str(path)

    def _watch_timeout(self, session_id: str) -> None:
        """Enforce a background command deadline for this process-owned session."""
        with self._lock:
            metadata = self._meta.get(session_id)
        if metadata is None:
            return
        delay = max(0.0, metadata[3] - time.monotonic())
        if delay:
            time.sleep(delay)
        with self._lock:
            proc = self._processes.get(session_id)
            if proc is None or proc.poll() is not None:
                return
            self._timeouts.add(session_id)
            _kill_process_group(proc, signal.SIGKILL)

    def detach_process(self, session_id: str) -> None:
        """Forget process-side state (called when a session closes)."""
        with self._lock:
            self._processes.pop(session_id, None)
            self._outputs.pop(session_id, None)
            self._meta.pop(session_id, None)
            self._timeouts.discard(session_id)

    def _take_finished(
        self, session_id: str
    ) -> tuple[subprocess.Popen[bytes], Path, Path, int] | None:
        """Atomically detach a finished process and its output spools."""
        with self._lock:
            proc = self._processes.get(session_id)
            if proc is None or proc.poll() is None:
                return None
            outputs = self._outputs.get(session_id)
            if outputs is None:
                spool = self._sessions.output_path
                outputs = (
                    spool(session_id, "stdout"),
                    spool(session_id, "stderr"),
                    self._config.max_output_chars,
                )
            self._processes.pop(session_id, None)
            self._outputs.pop(session_id, None)
        return proc, *outputs

    def _finish_background(
        self,
        session_id: str,
        proc: subprocess.Popen[bytes],
        stdout_path: Path,
        stderr_path: Path,
        max_chars: int,
        close_session: Any,
    ) -> dict[str, Any]:
        stdout, stdout_file = self._collect_output(stdout_path, proc.stdout, max_chars)
        stderr, stderr_file = self._collect_output(stderr_path, proc.stderr, max_chars)
        with self._lock:
            metadata = self._meta.pop(session_id, None)
            timeout_hit = session_id in self._timeouts
            self._timeouts.discard(session_id)
        close_session(session_id, cleanup_output_files=False)
        exit_code = -1 if timeout_hit else proc.returncode
        if exit_code is None:
            exit_code = proc.poll()
        response: dict[str, Any] = {
            "success": exit_code == 0 and not timeout_hit,
            "session_id": session_id,
            "running": False,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
        }
        if timeout_hit:
            response.update({"status": "timeout", "timed_out": True, "error": "Command timed out"})
        if metadata is not None:
            self._audit.log_command(
                metadata[0],
                metadata[1],
                exit_code,
                round(time.monotonic() - metadata[2], 2),
                session_id,
            )
        if stdout_file:
            response["stdout_file"] = stdout_file
        if stderr_file:
            response["stderr_file"] = stderr_file
        session = self._sessions.get(session_id)
        machine = self._registry.get(session.machine) if session is not None else None
        if machine is not None:
            return self._finish_response(
                response, machine, exit_code, stderr, success=exit_code == 0
            )
        return response

    def _collect_finished(
        self, session_id: str, *, still_running_error: bool, close_session: Any
    ) -> dict[str, Any]:
        """Shared poll/read_output core: error unless the process just finished."""
        with self._lock:
            proc = self._processes.get(session_id)
            running = proc is not None and proc.poll() is None
        if proc is None:
            return _collected_response(session_id, None, still_running_error)
        if running:
            return _collected_response(session_id, True, still_running_error)
        finished = self._take_finished(session_id)
        if finished is None:
            return _collected_response(session_id, False, still_running_error)
        proc, stdout_path, stderr_path, max_chars = finished
        return self._finish_background(
            session_id, proc, stdout_path, stderr_path, max_chars, close_session
        )

    def poll(self, session_id: str, close_session: Any) -> dict[str, Any]:
        """Check whether a background process is running and collect it when complete."""
        return self._collect_finished(
            session_id, still_running_error=False, close_session=close_session
        )

    def read_output(self, session_id: str, close_session: Any) -> dict[str, Any]:
        """Read output from a completed background process."""
        result = self._collect_finished(
            session_id, still_running_error=True, close_session=close_session
        )
        result.pop("running", None)
        return result

    def kill(self, session_id: str) -> dict[str, Any]:
        """Kill a background SSH process tracked by this manager instance.

        Persisted PIDs are never signalled: after a Hermes restart they may
        identify an unrelated recycled process. Shared ControlMaster sockets
        are connection state and are deliberately left alive.
        """
        session = self._sessions.get(session_id)
        if not session:
            return {"success": False, "error": f"Session '{session_id}' not found"}
        with self._lock:
            proc = self._processes.pop(session_id, None)
        if proc is None:
            self._sessions.mark(session_id, "orphaned")
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
                _kill_process_group(proc, signal.SIGKILL)
                proc.wait(timeout=2)
            except ProcessLookupError:
                pass
            killed = True
        return {"success": True, "pid_killed": killed, "socket_closed": False}
