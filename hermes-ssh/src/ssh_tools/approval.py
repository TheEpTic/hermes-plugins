"""Hermes dangerous-command approval integration."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from tools.approval import (
        _get_approval_mode,
        check_dangerous_command as _check_dangerous,
    )  # pyright: ignore[reportMissingImports]
except ImportError:
    _check_dangerous = None
    _get_approval_mode = None
    logger.warning(
        "Hermes approval system not available — SSH command approval checks are disabled"
    )


def check_approval(command: str) -> dict[str, Any] | None:
    """Return a denial result, or None when the command is approved/unchecked."""
    if _check_dangerous is None:
        return None
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
