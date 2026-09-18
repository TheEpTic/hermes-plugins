"""Handler for the ssh_terminal tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..approval import approval_error, check_approval
from ..helpers import err, ok, param_bool, param_str

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager


def handle_ssh_terminal(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create a handler for ssh_terminal that captures manager via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        machine, error = param_str(params, "machine")
        if error:
            return err(error)
        assert machine is not None
        command, error = param_str(params, "command")
        if error:
            return err(error)
        assert command is not None
        background, error = param_bool(params, "background")
        if error:
            return err(error)
        assert background is not None
        new_session, error = param_bool(params, "new_session")
        if error:
            return err(error)
        assert new_session is not None

        # Check command against Hermes approval system.
        denied = approval_error(check_approval(command), "Command blocked by approval system")
        if denied:
            return denied

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
