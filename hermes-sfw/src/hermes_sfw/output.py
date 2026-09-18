"""sfw output parsing, sanitation, and subprocess execution."""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import signal
import subprocess
from typing import Any

from .models import SFWResult

_MAX_LIST_ENTRIES = 50

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_BLOCKED_KEYWORDS = frozenset({"blocked", "🚫", "🔴"})
_INSTALLED_KEYWORDS = frozenset({"installed", "🟢", "added"})
_ALL_KEYWORDS = _BLOCKED_KEYWORDS | _INSTALLED_KEYWORDS
_NON_PACKAGE_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "at",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "package",
        "packages",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)

_ERRNO_MESSAGES: dict[int, str] = {
    errno.EACCES: "Permission denied",
    errno.ENOENT: "No such file or directory",
    errno.EISDIR: "Is a directory",
    errno.ENOTDIR: "Not a directory",
    errno.ENAMETOOLONG: "File name too long",
    errno.ELOOP: "Too many levels of symbolic links",
}


def sanitize_output(text: str, max_len: int = 10_000) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [output truncated, total {len(text)} chars]"


def sanitize_oserror(exc: OSError) -> str:
    errnum = getattr(exc, "errno", None)
    if errnum is not None and errnum in _ERRNO_MESSAGES:
        return _ERRNO_MESSAGES[errnum]
    return "An internal error occurred"


def truncate_list(items: list[str], limit: int = _MAX_LIST_ENTRIES) -> list[str]:
    """Deduplicate (order-preserving) and cap a list to prevent context flooding."""
    deduped = list(dict.fromkeys(items))
    if len(deduped) <= limit:
        return deduped
    return deduped[:limit] + [f"... and {len(deduped) - limit} more"]


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _package_after_keyword(parts: list[str], i: int) -> str | None:
    """Package name after keyword token i, or None when prose/count/end."""
    # Walk past any additional keyword tokens (e.g. 🔴 blocked)
    j = i + 1
    while j < len(parts) and parts[j].lower().strip(",:;") in _ALL_KEYWORDS:
        j += 1
    if j >= len(parts):
        return None
    candidate = parts[j].strip(",:;")
    # Skip counts ("added 5 packages") and prose fillers
    # ("blocked by firewall") that are not package names.
    if candidate.isdigit() or candidate.lower() in _NON_PACKAGE_TOKENS:
        return None
    return candidate


def parse_output(output: str) -> tuple[list[str], list[str]]:
    blocked: list[str] = []
    installed: list[str] = []

    for line in output.splitlines():
        # Strip ANSI escape sequences before parsing
        parts = strip_ansi(line).split()
        if not parts:
            continue

        # Find the keyword token, then take the first non-keyword token after it
        for i, part in enumerate(parts):
            token = part.lower().strip(",:;")
            if token not in _ALL_KEYWORDS or i + 1 >= len(parts):
                continue
            candidate = _package_after_keyword(parts, i)
            if candidate is None:
                break
            if token in _BLOCKED_KEYWORDS:
                blocked.append(candidate)
            else:
                installed.append(candidate)
            break

    return truncate_list(blocked), truncate_list(installed)


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
