"""hermes-sfw — Socket Firewall Free wrapper for Hermes."""

from __future__ import annotations

import logging
import os
import shlex
from importlib.metadata import PackageNotFoundError, version as distribution_version
from typing import Any

from .handlers import handle_sfw
from .manager import (
    SFWManager,
    contains_package_manager_command,
    is_dependency_operation,
)
from .schemas import SFW_TOOL_SCHEMA

try:
    __version__ = distribution_version("hermes-sfw")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
__all__ = ["register"]

logger = logging.getLogger(__name__)

_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


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
    """Force supported terminal dependency operations through the sfw binary.

    Hermes pre-tool hooks support ``modify`` directives. Returning one rewrites
    the terminal command before its backend executes it, so the model does not
    need to notice a block and issue a second tool call. Package-manager forms
    outside the plugin's strict grammar are blocked rather than allowed to raw
    execute.
    """
    enabled = os.getenv("HERMES_SFW_ENFORCE_DIRECT", "1").strip().lower()
    if enabled in _FALSE_VALUES or tool_name != "terminal":
        return None

    command = args.get("command")
    if not isinstance(command, str):
        return None

    if is_dependency_operation(command):
        sfw_path = _resolved_sfw_path()
        if sfw_path is None:
            return {
                "action": "block",
                "message": _direct_terminal_block_message(
                    command,
                    sfw_path,
                    "the sfw binary is unavailable",
                ),
            }
        try:
            tokens = shlex.split(command)
        except ValueError as exc:  # defensive: validation already parses it
            return {
                "action": "block",
                "message": _direct_terminal_block_message(
                    command,
                    sfw_path,
                    f"the command could not be parsed: {exc}",
                ),
            }
        return {
            "action": "modify",
            "args": {"command": shlex.join([sfw_path, *tokens])},
        }

    if contains_package_manager_command(command):
        return {
            "action": "block",
            "message": _direct_terminal_block_message(
                command,
                _resolved_sfw_path(),
                "the command is outside sfw's supported dependency grammar",
            ),
        }

    return None


def _resolved_sfw_path() -> str | None:
    """Return the resolved sfw binary path for PATH-independent invocation."""
    global _manager
    if _manager is None:
        _manager = SFWManager()
    return _manager.sfw_path


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
