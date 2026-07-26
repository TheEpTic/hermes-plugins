"""hermes-sfw — Socket Firewall Free wrapper for Hermes."""

from __future__ import annotations

import logging
import os
from importlib.metadata import PackageNotFoundError, version as distribution_version
from typing import Any

from .handlers import handle_sfw
from .manager import SFWManager, is_dependency_operation
from .schemas import SFW_TOOL_SCHEMA

try:
    __version__ = distribution_version("hermes-sfw")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
__all__ = ["register"]

logger = logging.getLogger(__name__)

_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _guard_direct_dependency_operation(
    tool_name: str, args: dict[str, Any], **kwargs: Any
) -> dict[str, str] | None:
    """Route supported dependency operations through sfw instead of raw terminal."""
    enabled = os.getenv("HERMES_SFW_ENFORCE_DIRECT", "1").strip().lower()
    if enabled in _FALSE_VALUES or tool_name != "terminal":
        return None
    command = args.get("command")
    if not isinstance(command, str) or not is_dependency_operation(command):
        return None
    return {
        "action": "block",
        "message": (
            "dependency operation blocked by hermes-sfw. "
            "run it with the sfw tool instead: "
            f"sfw action=run command={command!r}"
        ),
    }


_manager: SFWManager | None = None


def register(ctx: Any) -> None:
    """Register tools with Hermes."""
    global _manager
    if _manager is not None:
        logger.debug("hermes-sfw: already registered, skipping")
        return
    _manager = SFWManager()

    ctx.register_tool(
        name="sfw",
        toolset="sfw",
        schema=SFW_TOOL_SCHEMA,
        handler=handle_sfw(_manager),
    )

    register_hook = getattr(ctx, "register_hook", None)
    if callable(register_hook):
        register_hook("pre_tool_call", _guard_direct_dependency_operation)
    else:
        logger.warning(
            "Hermes pre_tool_call hooks unavailable; direct terminal installs are not enforced"
        )

    logger.info("hermes-sfw loaded")
