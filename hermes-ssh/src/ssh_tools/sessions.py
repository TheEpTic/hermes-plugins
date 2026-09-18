"""Session tracking: registration, lifecycle, idle cleanup, pruning."""

from __future__ import annotations

import contextlib
import logging
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import SSHConfig
from .helpers import read_json, write_json_atomic
from .models import Session

logger = logging.getLogger(__name__)


def _session_is_old(sdata: dict[str, Any], now: datetime, hours: int) -> bool:
    """Return whether a persisted non-active session is older than the limit."""
    if sdata.get("status") == "active":
        return False
    try:
        started = datetime.fromisoformat(sdata.get("started", ""))
    except (ValueError, TypeError):
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return (now - started).total_seconds() > hours * 3600


class SessionStore:
    """JSON-backed session registry with output-file cleanup."""

    def __init__(self, config: SSHConfig, lock: threading.Lock) -> None:
        self._config = config
        self._lock = lock

    def _load(self) -> dict[str, dict[str, Any]]:
        raw = read_json(self._config.sessions_file, {"sessions": {}})
        result = raw.get("sessions", {})
        if not isinstance(result, dict):
            logger.warning("Corrupt sessions.json structure, resetting")
            return {}
        return result

    def _save(self, sessions: dict[str, dict[str, Any]]) -> None:
        write_json_atomic(self._config.sessions_file, {"sessions": sessions})

    def list_sessions(self, status: str = "active") -> dict[str, Session]:
        return {
            sid: Session.from_dict(sid, d)
            for sid, d in self._load().items()
            if not status or d.get("status") == status
        }

    def get(self, session_id: str) -> Session | None:
        raw = self._load()
        if session_id in raw:
            return Session.from_dict(session_id, raw[session_id])
        return None

    def register(self, session: Session) -> None:
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
            sessions = self._load()
            sessions[session.id] = session.to_dict()
            self._save(sessions)

    def touch(self, session_id: str) -> None:
        with self._lock:
            sessions = self._load()
            if session_id in sessions:
                sessions[session_id]["last_active"] = datetime.now(UTC).isoformat()
                sessions[session_id]["command_count"] = (
                    sessions[session_id].get("command_count", 0) + 1
                )
                self._save(sessions)

    def cleanup_output_files(self, session_id: str) -> None:
        """Remove any saved output files for this session."""
        prefix = f"ssh_output_{session_id}_"
        try:
            for p in self._config.output_dir.iterdir():
                if p.name.startswith(prefix) and p.name.endswith(".txt"):
                    with contextlib.suppress(OSError):
                        p.unlink()
        except OSError:
            pass

    def mark(self, session_id: str, status: str) -> None:
        with self._lock:
            sessions = self._load()
            if session_id in sessions:
                sessions[session_id]["status"] = status
                self._save(sessions)

    def remove(self, session_id: str) -> None:
        self.cleanup_output_files(session_id)
        with self._lock:
            sessions = self._load()
            sessions.pop(session_id, None)
            self._save(sessions)

    def prune_closed(self, max_age_hours: int | None, default_hours: int) -> int:
        """Remove closed sessions older than max_age_hours. Returns count removed."""
        hours = max_age_hours or default_hours
        with self._lock:
            raw = self._load()
            now = datetime.now(UTC)
            to_remove = [sid for sid, sdata in raw.items() if _session_is_old(sdata, now, hours)]
            for sid in to_remove:
                del raw[sid]
            if to_remove:
                self._save(raw)
        return len(to_remove)

    def output_path(self, session_id: str, stream: str) -> Path:
        """Path for a saved output file (stdout/stderr spill + bg spools)."""
        return self._config.output_dir / f"ssh_output_{session_id}_{stream}.txt"
