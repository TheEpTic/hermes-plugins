"""Bounded, redacted REST adapter for the hermes-sfw desktop plugin.

The surface is a dependency guard, not a sandbox. Accepted package-manager
operations can still run lifecycle scripts and build backends, so every run
requires explicit confirmation and the existing approval/allowlist checks.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from hermes_sfw import get_manager
from hermes_sfw.approval import check_approval
from hermes_sfw.manager import is_dependency_operation

router = APIRouter()
app = FastAPI()
app.include_router(router)

_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_MAX_OUTPUT_CHARS = 10_000
_MAX_LIST_ITEMS = 100
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


class RunRequest(BaseModel):
    command: str = Field(min_length=1, max_length=20_000)
    workdir: str | None = Field(default=None, max_length=4_096)
    verbose: bool = False
    confirm: bool = False


def _redact_text(value: object, *, limit: int = _MAX_OUTPUT_CHARS) -> str:
    text = str(value or "")
    text = _redact_private_key_blocks(text)
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", text)
    text = _SECRET_VALUE.sub(r"\1[REDACTED]", text)
    if len(text) > limit:
        return f"{text[:limit]}\n... [truncated]"
    return text


def _require_confirmation(payload: RunRequest) -> None:
    if not payload.confirm:
        raise HTTPException(
            status_code=400, detail="confirm must be true for dependency operations"
        )


def _require_approval(command: str) -> None:
    approval = check_approval(command)
    if approval is not None and not approval.get("approved", True):
        raise HTTPException(
            status_code=403,
            detail=_redact_text(approval.get("message", "Command blocked by approval system")),
        )


def _result_view(result: Any) -> dict[str, Any]:
    """Expose structured result fields without echoing command/workdir values."""
    raw = result.to_dict()
    output: dict[str, Any] = {
        "success": bool(raw.get("success")),
        "exit_code": raw.get("exit_code"),
    }
    for key in ("stdout", "stderr"):
        if key in raw:
            output[key] = _redact_text(raw[key])
    for key in ("blocked", "installed"):
        if key in raw and isinstance(raw[key], list):
            output[key] = [_redact_text(item, limit=256) for item in raw[key][:_MAX_LIST_ITEMS]]
    return output


@router.get("/status")
def status() -> dict[str, Any]:
    manager = get_manager()
    binary = Path(manager.sfw_path).name if manager.sfw_path else None
    enforce = os.getenv("HERMES_SFW_ENFORCE_DIRECT", "1").strip().lower() not in _FALSE_VALUES
    installed = manager.is_installed()
    return {
        "installed": installed,
        "version": _redact_text(manager.get_version(), limit=128) if installed else None,
        "binary": _redact_text(binary, limit=64) if binary else None,
        "direct_terminal_enforced": enforce,
    }


@router.post("/run")
def run(payload: RunRequest) -> dict[str, Any]:
    _require_confirmation(payload)
    if not is_dependency_operation(payload.command):
        raise HTTPException(
            status_code=400,
            detail="command is not an allowed dependency operation",
        )
    _require_approval(payload.command)
    result = get_manager().run_command(
        command=payload.command,
        workdir=payload.workdir,
        verbose=payload.verbose,
    )
    return _result_view(result)
