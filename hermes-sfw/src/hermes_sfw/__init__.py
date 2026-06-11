"""hermes-sfw — Socket Firewall Free wrapper for Hermes."""

from __future__ import annotations

import logging
from typing import Any

from .handlers import handle_sfw
from .manager import SFWManager
from .schemas import SFW_TOOL_SCHEMA

__version__ = "0.1.0"
__all__ = ["register"]

logger = logging.getLogger(__name__)

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

    logger.info("hermes-sfw loaded")
