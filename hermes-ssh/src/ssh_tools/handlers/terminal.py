"""Handler for the ssh_terminal tool."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..utils import err, ok, require

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager

logger = logging.getLogger(__name__)

# Hermes dangerous command approval — optional integration.
# When running inside Hermes, this hooks into the native approval system
# (approvals.mode config, YOLO bypass, session/permanent allowlists).
# When running standalone, approval checks are silently skipped.
try:
    from tools.approval import (
        check_dangerous_command as _check_dangerous,
    )

    def _check_approval(command: str) -> dict[str, Any] | None:
        """Check command against Hermes approval system.

        Returns the approval result dict if the command needs approval
        or is blocked, None if approved (safe to proceed).
        """
        return _check_dangerous(command, env_type="ssh")  # type: ignore[no-any-return]

except ImportError:
    logger.debug("Hermes approval system not available — SSH commands will not be checked")

    def _check_approval(command: str) -> dict[str, Any] | None:
        return None


def handle_ssh_terminal(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create a handler for ssh_terminal that captures manager via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        # Handle poll/read_output first — these don't need machine/command
        if params.get("poll"):
            return ok(**manager.poll_session(params["poll"]))
        if params.get("read_output"):
            return ok(**manager.read_output(params["read_output"]))

        error = require(params, "machine", "command")
        if error:
            return err(error)

        # Check command against Hermes approval system
        approval = _check_approval(params["command"])
        if approval is not None and not approval.get("approved", True):
            return err(approval.get("message", "Command blocked by approval system"))

        background = params.get("background", False)
        result = manager.run_command(
            machine_name=params["machine"],
            command=params["command"],
            timeout=params.get("timeout"),
            new_session=params.get("new_session", False),
            background=background,
            max_output_chars=params.get("max_output_chars", 50_000),
        )

        if background and isinstance(result, dict) and "session_id" in result:
            return ok(
                session_id=result["session_id"],
                pid=result.get("pid"),
                machine=result.get("machine", params["machine"]),
                status="running",
                message="Command started in background. Use poll or read_output to check status.",
            )

        return ok(**result)

    return _handle
