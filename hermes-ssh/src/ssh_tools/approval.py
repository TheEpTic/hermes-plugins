"""Hermes dangerous-command approval integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .helpers import err

logger = logging.getLogger(__name__)

ApprovalCheck = Callable[..., dict[str, Any]]
ApprovalMode = Callable[[], str]


def _approval_functions() -> tuple[ApprovalCheck | None, ApprovalMode | None]:
    """Load approval functions after plugin discovery has finished.

    Hermes imports enabled plugins in sequence. Importing one plugin can still be
    inside another plugin's package initializer, so binding these functions at
    module import time creates an order-dependent circular-import failure.
    """
    try:
        from tools.approval import check_dangerous_command
    except ImportError:
        return None, None
    try:
        from tools.approval_context import _get_approval_mode
    except ImportError:
        try:
            # compatibility with Hermes releases that still re-exported this helper.
            from tools.approval import _get_approval_mode
        except ImportError:
            return None, None
    return check_dangerous_command, _get_approval_mode


def check_approval(command: str) -> dict[str, Any] | None:
    """Return a denial result, or None when the command is approved/unchecked."""
    check_dangerous, get_approval_mode = _approval_functions()
    if check_dangerous is None or get_approval_mode is None:
        logger.warning("Hermes approval system not available — SSH commands will fail closed")
        return {
            "approved": False,
            "message": "SSH command blocked: Hermes approval system is unavailable",
        }
    if get_approval_mode() == "off":
        return None
    result: dict[str, Any] = check_dangerous(command, env_type="ssh")
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


def denial_message(approval: dict[str, Any] | None, fallback: str) -> str | None:
    """Plain-text denial message, or None when the approval result passes."""
    if approval is None or approval.get("approved", True):
        return None
    return str(approval.get("message", fallback))


def approval_error(approval: dict[str, Any] | None, fallback: str) -> str | None:
    """JSON error response when an approval result is a denial, else None."""
    denied = denial_message(approval, fallback)
    return err(denied) if denied is not None else None
