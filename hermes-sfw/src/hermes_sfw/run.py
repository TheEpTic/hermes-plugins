"""Subprocess execution: run one sfw command with timeout + process-group kill."""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
from typing import Any

from .models import SFWResult
from .output import parse_output, sanitize_oserror, sanitize_output

logger = logging.getLogger(__name__)


def _kill_group(pgid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)


def _timeout_result(command: str, timeout: int) -> SFWResult:
    return SFWResult(
        success=False,
        command=command,
        stdout="",
        stderr=f"Command timed out after {timeout}s",
        exit_code=-1,
    )


def _reap_after_timeout(proc: subprocess.Popen[bytes]) -> None:
    # Grace period for SIGTERM, then unconditionally SIGKILL the whole
    # group. The leader may exit quickly while children keep running, so
    # the SIGKILL must not be gated on the leader's state: gate on the
    # group's existence instead.
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=2)
    else:
        # Leader exited during the grace period; make sure no detached
        # children of the group are left behind.
        _kill_group(proc.pid, signal.SIGKILL)


def run_sfw(
    args: list[str],
    command: str,
    timeout: int,
    workdir: str | None,
    popen: Any = None,
) -> SFWResult:
    """Run one sfw argv through Popen with a bounded timeout."""
    logger.debug("sfw run: %s", " ".join(args))
    spawn = popen or subprocess.Popen
    try:
        proc = spawn(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workdir,
            start_new_session=True,
        )
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill entire process group (sfw + child package manager
            # processes). start_new_session=True means the child is its own
            # group leader, so its pid is the process-group id.
            _kill_group(proc.pid, signal.SIGTERM)
            _reap_after_timeout(proc)
            return _timeout_result(command, timeout)
    except OSError as exc:
        return SFWResult(
            success=False,
            command=command,
            stdout="",
            stderr=sanitize_oserror(exc),
            exit_code=-1,
        )

    stdout = sanitize_output(stdout_bytes.decode("utf-8", errors="replace"))
    stderr = sanitize_output(stderr_bytes.decode("utf-8", errors="replace"))
    blocked, installed = parse_output(stdout + stderr)
    return SFWResult(
        success=proc.returncode == 0,
        command=command,
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode,
        blocked=blocked,
        installed=installed,
    )
