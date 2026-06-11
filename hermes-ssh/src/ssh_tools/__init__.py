"""hermes-ssh — SSH remote execution plugin for Hermes Agent.

Provides:
  - ssh_terminal: Run commands on remote machines via SSH
  - ssh_machines: Machine registry (add/list/remove/test/inspect)
  - ssh_sessions: Active session tracking (list/kill/cleanup)
  - /ssh slash command for quick access
"""

from __future__ import annotations

import logging
from typing import Any

from .handlers import handle_ssh_machines, handle_ssh_sessions, handle_ssh_terminal
from .handlers.slash import create_slash_handler
from .manager import SSHManager
from .schemas import SSH_MACHINES_SCHEMA, SSH_SESSIONS_SCHEMA, SSH_TERMINAL_SCHEMA

__version__ = "0.2.0"
__all__ = [
    "SSH_MACHINES_SCHEMA",
    "SSH_SESSIONS_SCHEMA",
    "SSH_TERMINAL_SCHEMA",
    "SSHManager",
    "handle_ssh_machines",
    "handle_ssh_sessions",
    "handle_ssh_terminal",
    "register",
]

logger = logging.getLogger(__name__)

# Module-level manager — initialized in register()
_manager: SSHManager | None = None


def _get_manager() -> SSHManager:
    global _manager
    if _manager is None:
        raise RuntimeError("hermes-ssh plugin not registered. Call register() first.")
    return _manager


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def register(ctx: Any) -> None:
    """Register SSH tools with Hermes."""
    global _manager
    if _manager is not None:
        logger.debug("hermes-ssh: already registered, skipping")
        return
    _manager = SSHManager()

    # Tools
    ctx.register_tool(
        name="ssh_terminal",
        toolset="ssh_tools",
        schema=SSH_TERMINAL_SCHEMA,
        handler=handle_ssh_terminal(_manager),
        description="Run a command on a remote machine via SSH.",
    )
    ctx.register_tool(
        name="ssh_machines",
        toolset="ssh_tools",
        schema=SSH_MACHINES_SCHEMA,
        handler=handle_ssh_machines(_manager),
        description="Manage the SSH machine registry.",
    )
    ctx.register_tool(
        name="ssh_sessions",
        toolset="ssh_tools",
        schema=SSH_SESSIONS_SCHEMA,
        handler=handle_ssh_sessions(_manager),
        description="Manage active SSH sessions.",
    )

    # Slash command
    slash_handler = create_slash_handler(_get_manager)
    ctx.register_command(
        "ssh",
        handler=slash_handler,
        description="SSH session management — machines, sessions, idle alerts.",
    )

    # Start background idle checker
    _manager.start_idle_checker()

    logger.info("hermes-ssh plugin loaded")
