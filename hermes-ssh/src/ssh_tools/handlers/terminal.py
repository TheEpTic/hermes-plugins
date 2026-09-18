"""Handler for the ssh_terminal tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..approval import approval_error, check_approval
from ..helpers import ok, param_bool, param_str, take

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager

_FIELDS: tuple[tuple[str, Any], ...] = (
    ("machine", param_str),
    ("command", param_str),
    ("background", param_bool),
    ("new_session", param_bool),
)


def handle_ssh_terminal(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create a handler for ssh_terminal that captures manager via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        args, error = take(params, _FIELDS)
        if error is not None:
            return error

        # Check command against Hermes approval system.
        denied = approval_error(
            check_approval(args["command"]), "Command blocked by approval system"
        )
        if denied:
            return denied

        result = manager.run_command(
            machine_name=args["machine"],
            command=args["command"],
            timeout=params.get("timeout"),
            new_session=args["new_session"],
            background=args["background"],
            max_output_chars=params.get("max_output_chars", 50_000),
        )

        if args["background"] and isinstance(result, dict) and "session_id" in result:
            return ok(
                session_id=result["session_id"],
                pid=result.get("pid"),
                machine=result.get("machine", args["machine"]),
                status="running",
                message="Command started in background. Use poll or read_output to check status.",
            )

        return ok(**result)

    return _handle
