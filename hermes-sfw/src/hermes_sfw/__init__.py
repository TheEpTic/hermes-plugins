"""hermes-sfw — Socket Firewall Free wrapper for Hermes."""

from __future__ import annotations

import logging
import os
from importlib.metadata import PackageNotFoundError, version as distribution_version
from typing import Any

from .guard import plan_terminal_guard
from .handlers import handle_sfw
from .manager import SFWManager
from .schemas import SFW_TOOL_SCHEMA

try:
    __version__ = distribution_version("hermes-sfw")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
__all__ = ["register"]

logger = logging.getLogger(__name__)

_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

# The manager the terminal hooks consult. ``register`` replaces it on every
# call, so a host that unloads and re-registers plugins (``discover(force=True)``)
# gets fresh registrations instead of a stale one-shot latch. SFWManager holds
# only immutable config; binary discovery runs on demand.
_manager: SFWManager | None = None


def _current_manager() -> SFWManager:
    global _manager
    if _manager is None:
        _manager = SFWManager()
    return _manager


def _direct_terminal_block_message(
    command: str,
    sfw_path: str | None,
    reason: str,
) -> str:
    """Explain why a raw package-manager terminal call was refused."""
    resolved = sfw_path or "sfw (not installed)"
    return (
        "direct package-manager operation blocked by hermes-sfw; it was not "
        f"executed raw ({reason}). Use the sfw tool with command={command!r}, "
        f"or call the resolved binary directly ({resolved})."
    )


def _guard_direct_dependency_operation(
    tool_name: str, args: dict[str, Any], **kwargs: Any
) -> dict[str, Any] | None:
    """Force reachable terminal package-manager commands through sfw."""
    enabled = os.getenv("HERMES_SFW_ENFORCE_DIRECT", "1").strip().lower()
    if enabled in _FALSE_VALUES or tool_name != "terminal":
        return None

    command = args.get("command")
    if not isinstance(command, str):
        return None

    sfw_path = _current_manager().sfw_path
    plan = plan_terminal_guard(command, sfw_path)
    if plan.action == "modify":
        return {"action": "modify", "args": {"command": plan.command}}
    if plan.action == "block":
        reason = plan.reason or "the command could not be routed safely"
        return {
            "action": "block",
            "message": _direct_terminal_block_message(command, sfw_path, reason),
        }
    return None


def _annotate_sfw_bootstrap_failure(
    tool_name: str = "",
    command: str = "",
    output: Any = None,
    **kwargs: Any,
) -> str | None:
    """Attach the sfw launcher diagnosis to a terminal result that shows one.

    The pre-tool guard rewrites reachable package-manager commands to run
    through sfw. When the launcher cannot prepare its firewall binary, the
    routed command fails with sfw's own one-line error — no cause, no repair,
    and no hint that an unrelated dev command was stopped by the sfw layer.
    The ``transform_terminal_output`` hook adds both; any other output is
    returned untouched.
    """
    if not isinstance(output, str):
        return None
    note = _current_manager().bootstrap_failure_note(output)
    if note is None:
        return None
    return f"{output}\n\n{note}"


def register(ctx: Any) -> None:
    """Register the sfw tool and terminal hooks with Hermes."""
    global _manager
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
        register_hook("transform_terminal_output", _annotate_sfw_bootstrap_failure)
    else:
        logger.warning(
            "Hermes pre_tool_call hooks unavailable; direct terminal installs are not enforced"
        )

    logger.info("hermes-sfw loaded")
