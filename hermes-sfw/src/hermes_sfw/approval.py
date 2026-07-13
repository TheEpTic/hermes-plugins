"""Hermes dangerous-command approval integration."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from tools.approval import (  # pyright: ignore[reportMissingImports]
        _get_approval_mode,
        check_dangerous_command as _check_dangerous,
    )
except ImportError:
    _check_dangerous = None
    _get_approval_mode = None
    logger.warning("Hermes approval system unavailable — SFW commands will fail closed")


def check_approval(command: str) -> dict[str, Any] | None:
    """Return a denial result, or None when the command is approved."""
    if _check_dangerous is None:
        return {
            "approved": False,
            "message": "SFW command blocked: Hermes approval system is unavailable",
        }
    if _get_approval_mode is not None and _get_approval_mode() == "off":
        return None
    result: dict[str, Any] = _check_dangerous(command, env_type="local")
    if result.get("status") == "approval_required":
        description = str(result.get("description") or "command flagged")
        return {
            **result,
            "approved": False,
            "message": (
                f"approval required: {description}. the user must reply with /approve or /deny.\n\n"
                f"command:\n```\n{command}\n```"
            ),
        }
    return result
