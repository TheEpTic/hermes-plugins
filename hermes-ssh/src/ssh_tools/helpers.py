"""Shared helpers: json responses, param validation, json persistence."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def ok(**data: Any) -> str:
    """Return a success JSON response."""
    return json.dumps({"success": True, **data})


def err(msg: str) -> str:
    """Return an error JSON response."""
    return json.dumps({"success": False, "error": msg})


def require(params: dict[str, Any], *fields: str) -> str | None:
    """Check that required fields are present and non-None."""
    for field in fields:
        if field not in params or params[field] is None:
            return f"{field} is required"
    return None


def dispatch(
    params: dict[str, Any], actions: dict[str, Any], manager: Any, default: str = "list"
) -> str:
    """Route a tool call to its action handler, or an unknown-action error."""
    action = params.get("action", default)
    handler = actions.get(action)
    return handler(manager, params) if handler else err(f"Unknown action: {action}")


def param_str(
    params: dict[str, Any], field: str, *, allow_empty: bool = False
) -> tuple[str | None, str | None]:
    """Take a validated string param: (value, None), or (None, error)."""
    value = params.get(field)
    if value is None:
        return None, f"{field} is required"
    if not isinstance(value, str):
        return None, f"{field} must be a string, got {type(value).__name__}"
    if not allow_empty and not value.strip():
        return None, f"{field} must be a non-empty string"
    return value, None


def param_bool(
    params: dict[str, Any], field: str, *, default: bool = False
) -> tuple[bool | None, str | None]:
    """Take a validated boolean param: (value, None), or (None, error)."""
    value = params.get(field, default)
    if not isinstance(value, bool):
        return None, f"{field} must be a boolean, got {type(value).__name__}"
    return value, None


def coerce_int(value: object, field: str, minimum: int = 0, maximum: int | None = None) -> int:
    """Coerce str/int to int, rejecting bools and range violations."""
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        out = int(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if not isinstance(out, int):
        raise ValueError(f"{field} must be a positive integer")
    if out < minimum or (maximum is not None and out > maximum):
        raise ValueError(f"{field} must be a positive integer")
    return out


def read_json(path: Path, default: Any) -> Any:
    """Read JSON with corrupt-file fallback; warns so resets stay visible."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Corrupt data in %s, resetting: %s", path, exc)
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    """Atomic JSON write: temp file + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=path.stem)
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


def append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    """Append one JSONL entry with 0600 perms; debug-logs on failure."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        logger.debug("Failed to append to %s", path, exc_info=True)
