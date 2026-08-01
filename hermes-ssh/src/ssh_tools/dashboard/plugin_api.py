"""Bounded, redacted REST adapter for the hermes-ssh desktop plugin.

The gateway mounts ``router`` under ``/api/plugins/hermes-ssh``.  The
adapter intentionally exposes projections instead of ``Machine.to_dict()`` or
``Session.to_dict()`` so encrypted configuration fields and local socket
paths cannot cross the dashboard boundary.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from ssh_tools import get_manager
from ssh_tools.approval import check_approval
from ssh_tools.models import Machine

router = APIRouter()
app = FastAPI()
app.include_router(router)

_MAX_ITEMS = 100
_MAX_OUTPUT_CHARS = 32_768
_MAX_TEXT_CHARS = 2_000
_SECRET_VALUE = re.compile(
    r"(?i)(\b(?:api[_-]?key|authorization|password|passwd|private[_-]?key|secret|token)\b\s*[:=]\s*)([^\s,;]+)"
)
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)([^/@\s]+)@")


def _redact_private_key_blocks(text: str) -> str:
    """Redact PEM private-key blocks with a linear marker scan."""
    begin_marker = "-----BEGIN "
    key_marker = " PRIVATE KEY-----"
    end_marker = "-----END "
    cursor = 0
    pieces: list[str] = []
    while True:
        start = text.find(begin_marker, cursor)
        if start < 0:
            pieces.append(text[cursor:])
            break
        header_end = text.find(key_marker, start + len(begin_marker))
        if header_end < 0:
            pieces.append(text[cursor:start])
            pieces.append("[REDACTED PRIVATE KEY]")
            break
        end_start = text.find(end_marker, header_end + len(key_marker))
        if end_start < 0:
            pieces.append(text[cursor:start])
            pieces.append("[REDACTED PRIVATE KEY]")
            break
        end_end = text.find(key_marker, end_start + len(end_marker))
        if end_end < 0:
            pieces.append(text[cursor:start])
            pieces.append("[REDACTED PRIVATE KEY]")
            break
        pieces.append(text[cursor:start])
        pieces.append("[REDACTED PRIVATE KEY]")
        cursor = end_end + len(key_marker)
    return "".join(pieces)


class ConfirmedAction(BaseModel):
    confirm: bool = False


class CleanupRequest(ConfirmedAction):
    max_idle_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)


class MachineRequest(ConfirmedAction):
    name: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    user: str = Field(min_length=1, max_length=64)
    port: int = Field(default=22, ge=1, le=65_535)
    key: str = Field(default="", max_length=4_096)
    aliases: list[str] = Field(default_factory=list, max_length=16)
    tags: list[str] = Field(default_factory=list, max_length=16)
    description: str = Field(default="", max_length=500)


class TerminalRequest(ConfirmedAction):
    machine: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=20_000)
    timeout: int | None = Field(default=None, ge=1, le=3_600)
    new_session: bool = False
    background: bool = False
    max_output_chars: int = Field(default=_MAX_OUTPUT_CHARS, ge=1, le=_MAX_OUTPUT_CHARS)


def _redact_text(value: object, *, limit: int = _MAX_TEXT_CHARS) -> str:
    """Redact common secret-shaped values and cap text crossing the UI boundary."""
    text = str(value or "")
    text = _redact_private_key_blocks(text)
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", text)
    text = _SECRET_VALUE.sub(r"\1[REDACTED]", text)
    if len(text) > limit:
        return f"{text[:limit]}\n... [truncated]"
    return text


def _require_confirmation(payload: ConfirmedAction) -> None:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true for this action")


def _require_approval(command: str) -> None:
    approval = check_approval(command)
    if approval is not None and not approval.get("approved", True):
        raise HTTPException(
            status_code=403,
            detail=_redact_text(approval.get("message", "Command blocked by approval system")),
        )


def _machine_view(name: str, machine: Any) -> dict[str, Any]:
    """Return only non-secret machine inventory fields."""
    return {
        "name": _redact_text(name, limit=64),
        "host": _redact_text(getattr(machine, "host", ""), limit=255),
        "port": int(getattr(machine, "port", 22)),
        "user": _redact_text(getattr(machine, "user", ""), limit=64),
        "aliases": [
            _redact_text(item, limit=64)
            for item in list(getattr(machine, "aliases", None) or [])[:16]
        ],
        "tags": [
            _redact_text(item, limit=64) for item in list(getattr(machine, "tags", None) or [])[:16]
        ],
        "added": _redact_text(getattr(machine, "added", ""), limit=64),
    }


def _session_view(session_id: str, session: Any) -> dict[str, Any]:
    """Return session metadata without local control/socket paths."""
    idle_seconds = getattr(session, "idle_seconds", None)
    return {
        "id": _redact_text(session_id, limit=128),
        "machine": _redact_text(getattr(session, "machine", ""), limit=64),
        "pid": int(getattr(session, "pid", 0) or 0),
        "started": _redact_text(getattr(session, "started", ""), limit=64),
        "last_active": _redact_text(getattr(session, "last_active", ""), limit=64),
        "command_count": int(getattr(session, "command_count", 0) or 0),
        "status": _redact_text(getattr(session, "status", ""), limit=32),
        "idle_seconds": int(idle_seconds) if isinstance(idle_seconds, int) else None,
    }


def _audit_view(entry: dict[str, Any]) -> dict[str, Any]:
    """Keep audit metadata while deliberately dropping command text."""
    return {
        "timestamp": _redact_text(entry.get("timestamp", ""), limit=64),
        "machine": _redact_text(entry.get("machine", ""), limit=64),
        "command_sha256": _redact_text(entry.get("command_sha256", ""), limit=128),
        "command_length": int(entry.get("command_length", 0) or 0),
        "exit_code": entry.get("exit_code"),
        "elapsed_secs": entry.get("elapsed_secs"),
        "session_id": _redact_text(entry.get("session_id", ""), limit=128),
    }


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    """Project manager results without local output-file paths."""
    allowed = {
        "success",
        "background",
        "running",
        "pid",
        "machine",
        "session_id",
        "stdout",
        "stderr",
        "exit_code",
        "elapsed_secs",
        "status",
        "pid_killed",
        "socket_closed",
        "count",
        "killed",
    }
    output: dict[str, Any] = {}
    for key in allowed:
        if key not in result:
            continue
        value = result[key]
        if key in {"stdout", "stderr", "error", "status"}:
            output[key] = _redact_text(value, limit=_MAX_OUTPUT_CHARS)
        elif key in {"machine", "session_id"}:
            output[key] = _redact_text(value, limit=128)
        elif key == "killed" and isinstance(value, list):
            output[key] = [
                {
                    "session_id": _redact_text(item.get("session_id", ""), limit=128),
                    "machine": _redact_text(item.get("machine", ""), limit=64),
                    "success": item.get("success"),
                    "status": _redact_text(item.get("status", ""), limit=32),
                }
                for item in value[:_MAX_ITEMS]
                if isinstance(item, dict)
            ]
        else:
            output[key] = value
    if "error" in result:
        output["error"] = _redact_text(result["error"])
    return output


def _session_collection(status: str = "") -> list[dict[str, Any]]:
    sessions = get_manager().list_sessions(status)
    return [
        _session_view(session_id, session)
        for session_id, session in list(sessions.items())[:_MAX_ITEMS]
    ]


@router.get("/status")
def status() -> dict[str, Any]:
    """Return a bounded shared-inventory/session snapshot for the status chip."""
    manager = get_manager()
    machines = manager.list_machines()
    sessions = manager.list_sessions("")
    session_views = [
        _session_view(session_id, session)
        for session_id, session in list(sessions.items())[:_MAX_ITEMS]
    ]
    counts: dict[str, int] = {}
    for session in sessions.values():
        state = str(getattr(session, "status", "unknown"))
        counts[state] = counts.get(state, 0) + 1
    idle_timeout_minutes = max(
        1,
        int(
            getattr(
                getattr(manager, "_config", None),
                "idle_timeout_minutes",
                30,
            )
        ),
    )
    idle_session_count = sum(
        1
        for session in sessions.values()
        if str(getattr(session, "status", "")) == "active"
        and int(getattr(session, "idle_seconds", 0) or 0) >= idle_timeout_minutes * 60
    )
    return {
        "scope": "shared",
        "machines": [
            _machine_view(name, machine) for name, machine in list(machines.items())[:_MAX_ITEMS]
        ],
        "sessions": session_views,
        "audit": [
            _audit_view(entry)
            for entry in manager.list_command_log(limit=50)[-50:]
            if isinstance(entry, dict)
        ],
        "machine_count": len(machines),
        "session_counts": counts,
        "active_session_count": counts.get("active", 0),
        "idle_session_count": idle_session_count,
    }


@router.get("/machines")
def list_machines() -> dict[str, Any]:
    machines = get_manager().list_machines()
    return {
        "machines": [
            _machine_view(name, machine) for name, machine in list(machines.items())[:_MAX_ITEMS]
        ],
        "scope": "shared",
    }


@router.post("/machines")
def add_machine(payload: MachineRequest) -> dict[str, Any]:
    _require_confirmation(payload)
    machine = Machine(
        name=payload.name,
        host=payload.host,
        user=payload.user,
        port=payload.port,
        key=payload.key,
        aliases=payload.aliases,
        tags=payload.tags,
        description=payload.description,
    )
    try:
        stored = get_manager().add_machine(machine)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="machine validation failed") from exc
    return {"machine": _machine_view(stored.name, stored)}


@router.delete("/machines/{name}")
def remove_machine(name: str, payload: ConfirmedAction) -> dict[str, Any]:
    _require_confirmation(payload)
    return {"removed": bool(get_manager().remove_machine(name))}


@router.post("/machines/{name}/test")
def test_machine(name: str) -> dict[str, Any]:
    result = get_manager().test_machine(name)
    return {
        "success": bool(result.get("success")),
        "status": _redact_text(result.get("status", "unknown"), limit=32),
        **({"error": _redact_text(result["error"])} if result.get("error") else {}),
    }


@router.get("/sessions")
def list_sessions(status: str = Query("active", max_length=32)) -> dict[str, Any]:
    if status not in {"", "active", "closed", "orphaned"}:
        raise HTTPException(
            status_code=400, detail="status must be active, closed, orphaned, or empty"
        )
    return {"sessions": _session_collection(status)}


@router.post("/sessions/{session_id}/poll")
def poll_session(session_id: str) -> dict[str, Any]:
    return _safe_result(get_manager().poll_session(session_id))


@router.post("/sessions/{session_id}/kill")
def kill_session(session_id: str, payload: ConfirmedAction) -> dict[str, Any]:
    _require_confirmation(payload)
    return _safe_result(get_manager().kill_session(session_id))


@router.post("/sessions/cleanup")
def cleanup_sessions(payload: CleanupRequest) -> dict[str, Any]:
    _require_confirmation(payload)
    return _safe_result(get_manager().cleanup_idle(payload.max_idle_minutes))


@router.get("/audit")
def audit(limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    return {
        "entries": [
            _audit_view(entry)
            for entry in get_manager().list_command_log(limit=limit)
            if isinstance(entry, dict)
        ]
    }


@router.post("/terminal")
def terminal(payload: TerminalRequest) -> dict[str, Any]:
    _require_confirmation(payload)
    _require_approval(payload.command)
    result = get_manager().run_command(
        machine_name=payload.machine,
        command=payload.command,
        timeout=payload.timeout,
        new_session=payload.new_session,
        background=payload.background,
        max_output_chars=payload.max_output_chars,
    )
    return _safe_result(result)
