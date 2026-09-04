"""Hermes dangerous-command approval integration."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from tools.approval import (
        check_dangerous_command as _check_dangerous,
    )  # pyright: ignore[reportMissingImports]
except ImportError:
    _check_dangerous = None

try:
    from tools.approval_context import _get_approval_mode  # pyright: ignore[reportMissingImports]
except ImportError:
    try:
        # compatibility with Hermes releases that still re-exported this helper.
        from tools.approval import _get_approval_mode
    except ImportError:
        _get_approval_mode = None

if _check_dangerous is None or _get_approval_mode is None:
    logger.warning("Hermes approval system not available — SSH commands will fail closed")


def check_approval(command: str) -> dict[str, Any] | None:
    """Return a denial result, or None when the command is approved/unchecked."""
    if _check_dangerous is None:
        return {
            "approved": False,
            "message": "SSH command blocked: Hermes approval system is unavailable",
        }
    if _get_approval_mode is not None and _get_approval_mode() == "off":
        return None
    result: dict[str, Any] = _check_dangerous(command, env_type="ssh")
    if result.get("status") == "approval_required":
        description = str(result.get("description") or "command flagged")
        result = {
            **result,
            "message": (
                f"approval required: {description}. the user must reply with /approve or /deny.\n\n"
                f"command:\n```\n{command}\n```"
            ),
        }
    return result
