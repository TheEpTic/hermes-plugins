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
    "handle_ssh_machines",
    "handle_ssh_sessions",
    "handle_ssh_terminal",
    "handle_ssh_transfer",
    "register",
]

logger = logging.getLogger(__name__)

# (tool name, schema, handler factory, description)
_TOOLS = (
    (
        "ssh_terminal",
        SSH_TERMINAL_SCHEMA,
        handle_ssh_terminal,
        "Run a command on a remote machine via SSH.",
    ),
    (
        "ssh_transfer",
        SSH_TRANSFER_SCHEMA,
        handle_ssh_transfer,
        "Upload or download files using a registered SSH machine.",
    ),
    (
        "ssh_machines",
        SSH_MACHINES_SCHEMA,
        handle_ssh_machines,
        "Manage the SSH machine registry.",
    ),
    (
        "ssh_sessions",
        SSH_SESSIONS_SCHEMA,
        handle_ssh_sessions,
        "Manage active SSH sessions.",
    ),
)

# Module-level manager — initialized in register()
_manager: SSHManager | None = None


def _get_manager() -> SSHManager:
    global _manager
    if _manager is None:
        raise RuntimeError("hermes-ssh plugin not registered. Call register() first.")
    return _manager


def register(ctx: Any) -> None:
    """Register SSH tools with Hermes."""
    global _manager
    if _manager is not None:
        logger.debug("hermes-ssh: already registered, skipping")
        return
    _manager = SSHManager()

    for name, schema, handler_factory, description in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="ssh_tools",
            schema=schema,
            handler=handler_factory(_manager),
            description=description,
        )

    ctx.register_command(
        "ssh",
        handler=create_slash_handler(_get_manager),
        description="SSH session management — machines, sessions, idle alerts.",
    )

    _manager.start_idle_checker()

    logger.info("hermes-ssh plugin loaded")
