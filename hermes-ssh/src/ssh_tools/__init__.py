"""hermes-ssh — SSH remote operations plugin for Hermes Agent.

Provides:
  - ssh_terminal: Run commands on remote machines via SSH
  - ssh_transfer: Upload or download files via SFTP
  - ssh_machines: Machine registry (add/list/remove/test/inspect)
  - ssh_sessions: Active session tracking (list/kill/cleanup)
  - /ssh slash command for quick access
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version as distribution_version
from typing import Any

from .handlers import (
    handle_ssh_machines,
    handle_ssh_sessions,
    handle_ssh_terminal,
    handle_ssh_transfer,
)
from .handlers.slash import create_slash_handler
from .manager import SSHManager
from .schemas import (
    SSH_MACHINES_SCHEMA,
    SSH_SESSIONS_SCHEMA,
    SSH_TERMINAL_SCHEMA,
    SSH_TRANSFER_SCHEMA,
)

try:
    __version__ = distribution_version("hermes-ssh")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
__all__ = [
    "SSH_MACHINES_SCHEMA",
    "SSH_SESSIONS_SCHEMA",
    "SSH_TERMINAL_SCHEMA",
    "SSH_TRANSFER_SCHEMA",
    "SSHManager",
    "get_manager",
    "handle_ssh_machines",
    "handle_ssh_sessions",
    "handle_ssh_terminal",
    "handle_ssh_transfer",
    "register",
]

logger = logging.getLogger(__name__)

# Module-level manager — initialized lazily for dashboard access and fully started in register()
_manager: SSHManager | None = None
_registered = False


def get_manager() -> SSHManager:
    """Return the shared manager, creating it for dashboard discovery if needed."""
    global _manager
    if _manager is None:
        _manager = SSHManager()
    return _manager


def _get_manager() -> SSHManager:
    """Return the registration-owned manager for slash-command access."""
    if _manager is None:
        raise RuntimeError("hermes-ssh plugin not registered. Call register() first.")
    return _manager


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def register(ctx: Any) -> None:
    """Register SSH tools with Hermes."""
    global _registered
    if _registered:
        logger.debug("hermes-ssh: already registered, skipping")
        return
    manager = get_manager()

    # Tools
    ctx.register_tool(
        name="ssh_terminal",
        toolset="ssh_tools",
        schema=SSH_TERMINAL_SCHEMA,
        handler=handle_ssh_terminal(manager),
        description="Run a command on a remote machine via SSH.",
    )
    ctx.register_tool(
        name="ssh_transfer",
        toolset="ssh_tools",
        schema=SSH_TRANSFER_SCHEMA,
        handler=handle_ssh_transfer(manager),
        description="Upload or download files using a registered SSH machine.",
    )
    ctx.register_tool(
        name="ssh_machines",
        toolset="ssh_tools",
        schema=SSH_MACHINES_SCHEMA,
        handler=handle_ssh_machines(manager),
        description="Manage the SSH machine registry.",
    )
    ctx.register_tool(
        name="ssh_sessions",
        toolset="ssh_tools",
        schema=SSH_SESSIONS_SCHEMA,
        handler=handle_ssh_sessions(manager),
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
    manager.start_idle_checker()
    _registered = True

    logger.info("hermes-ssh plugin loaded")
