"""Command audit log: redacted JSONL entries plus tail reader."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .helpers import append_jsonl

logger = logging.getLogger(__name__)

_AUDIT_MODES = frozenset({"redacted", "metadata", "off"})
# A credential-bearing name: the keyword may be glued to a prefix
# (PGPASSWORD, MYSQL_PWD, AWS_SECRET_ACCESS_KEY, DB_PASSWORD).
_SENSITIVE_NAME = (
    r"[a-z0-9_-]*"
    r"(?:password|passwd|passphrase|pwd|token|api[_-]?key|secret|authorization"
    r"|private[_-]?key|access[_-]?key|credentials?)"
    r"(?:[_-][a-z0-9]+)*"
)
_SECRET_VALUE = r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s;&|]+)"""
# ``user:secret`` pairs passed to -u/--user (curl, wget, httpie) and
# password flags whose value follows the flag (sshpass -p, mysql -pSECRET).
_USERPASS_FLAG = r"(?:-u|--user|--proxy-user|--http-user|--auth)"
_AUDIT_PATTERNS = (
    (re.compile(rf"(?i)(?<![\w-])({_SENSITIVE_NAME})(\s*=\s*)({_SECRET_VALUE})"), 3),
    (re.compile(rf"(?i)(--{_SENSITIVE_NAME}(?:=|\s+))({_SECRET_VALUE})"), 2),
    # userinfo runs to the LAST "@" before the host: passwords may contain "@"
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/@\s'\"]*:)([^\s'\"/]*)(@[^@/\s'\"]*)"), 2),
    (re.compile(rf"(?<![\w-])({_USERPASS_FLAG}(?:=|\s+)['\"]?[^:\s'\"]+:)([^\s'\"]+)"), 2),
    (re.compile(r"(\bsshpass\s+-p\s*)(" + _SECRET_VALUE + ")"), 2),
    (re.compile(r"(\b(?:mysql|mysqldump|mariadb|mysqladmin)\b[^;&|]*?\s-p)([^\s;&|]+)"), 2),
    (
        re.compile(
            r"(?i)([\"']?(?:[a-z0-9]+[-_])*(?:authorization|api[-_]?key|token|secret)\s*:\s*)"
            r"(?:(?:bearer|basic)\s+)?([^\"'\s;&|]+)([\"']?)"
        ),
        2,
    ),
)


def redact_command(command: str) -> str:
    """Remove common inline credentials before persisting command text."""
    for pattern, secret_group in _AUDIT_PATTERNS:
        command = pattern.sub(
            lambda m: "".join(
                "<redacted>" if i == secret_group else (m.group(i) or "")
                for i in range(1, pattern.groups + 1)
            ),
            command,
        )
    return command


def effective_mode(audit_log_mode: str) -> str | None:
    """Effective audit mode, or None when auditing is off. Unknown modes warn + redact."""
    mode = audit_log_mode.strip().lower()
    if mode not in _AUDIT_MODES:
        logger.warning("Unknown audit_log_mode %r; using redacted", mode)
        mode = "redacted"
    return None if mode == "off" else mode


def redact_local_path(path: str) -> str:
    """Shorten a local audit path: home dir becomes ~."""
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


class AuditLog:
    """Append-only JSONL audit log with redacted/metadata/off modes."""

    def __init__(self, data_dir: Path, audit_log_mode: str) -> None:
        self._data_dir = data_dir
        self._audit_log_mode = audit_log_mode

    @property
    def path(self) -> Path:
        return self._data_dir / "command_log.jsonl"

    def enabled(self) -> bool:
        return effective_mode(self._audit_log_mode) is not None

    def log_command(
        self,
        machine: str,
        command: str,
        exit_code: int | None,
        elapsed: float,
        session_id: str,
    ) -> None:
        """Append a redacted or metadata-only JSONL audit entry."""
        mode = effective_mode(self._audit_log_mode)
        if mode is None:
            return
        redacted = redact_command(command)
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "machine": machine,
            "command_sha256": hashlib.sha256(redacted.encode()).hexdigest(),
            "command_length": len(redacted),
            "exit_code": exit_code,
            "elapsed_secs": elapsed,
            "session_id": session_id,
        }
        if mode == "redacted":
            entry["command"] = redacted
        append_jsonl(self.path, entry)

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the last *limit* entries from the command audit log."""
        if not self.path.exists():
            return []
        with self.path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            tail = f.read().decode("utf-8", errors="replace")
        entries: list[dict[str, Any]] = []
        for line in tail.splitlines()[-limit:]:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        return entries
