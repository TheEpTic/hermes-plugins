"""Shared utilities for hermes-ssh handlers."""

from __future__ import annotations

import json
from typing import Any


def ok(**data: Any) -> str:
    """Return a success JSON response."""
    return json.dumps({"success": True, **data})


def err(msg: str) -> str:
    """Return an error JSON response."""
    return json.dumps({"success": False, "error": msg})


def require(params: dict[str, Any], *fields: str) -> str | None:
    """Check that required fields are present and non-None.

    Returns error message string if any field is missing, None if all ok.
    """
    for field in fields:
        if field not in params or params[field] is None:
            labels = {
                "machine": "machine",
                "command": "command",
                "name": "name",
                "host": "host",
                "session_id": "session_id",
                "action": "action",
            }
            label = labels.get(field, field)
            return f"{label} is required"
    return None
