"""Handler for the ssh_terminal tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..approval import check_approval
from ..utils import err, ok, require

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager


def handle_ssh_terminal(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create a handler for ssh_terminal that captures manager via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        # Handle poll/read_output first — these don't need machine/command.
        if "poll" in params and params["poll"] is not None:
            session_id = params["poll"]
            if not isinstance(session_id, str) or not session_id:
                return err("poll must be a non-empty string")
            return ok(**manager.poll_session(session_id))
        if "read_output" in params and params["read_output"] is not None:
            session_id = params["read_output"]
            if not isinstance(session_id, str) or not session_id:
                return err("read_output must be a non-empty string")
            return ok(**manager.read_output(session_id))

        error = require(params, "machine", "command")
        if error:
            return err(error)

        machine = params["machine"]
        command = params["command"]
        if not isinstance(machine, str) or not machine:
            return err("machine must be a non-empty string")
        if not isinstance(command, str) or not command.strip():
            return err("command must be a non-empty string")

        background = params.get("background", False)
        if not isinstance(background, bool):
            return err(f"background must be a boolean, got {type(background).__name__}")
        new_session = params.get("new_session", False)
        if not isinstance(new_session, bool):
            return err(f"new_session must be a boolean, got {type(new_session).__name__}")

        # Check command against Hermes approval system.
        approval = check_approval(command)
        if approval is not None and not approval.get("approved", True):
            return err(str(approval.get("message", "Command blocked by approval system")))

        result = manager.run_command(
            machine_name=machine,
            command=command,
            timeout=params.get("timeout"),
            new_session=new_session,
            background=background,
            max_output_chars=params.get("max_output_chars", 50_000),
        )

        if background and isinstance(result, dict) and "session_id" in result:
            return ok(
                session_id=result["session_id"],
                pid=result.get("pid"),
                machine=result.get("machine", machine),
                status="running",
                message="Command started in background. Use poll or read_output to check status.",
            )

        return ok(**result)

    return _handle
