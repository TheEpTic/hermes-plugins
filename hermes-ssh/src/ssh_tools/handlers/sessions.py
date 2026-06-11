"""Handler for the ssh_sessions tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..utils import err, ok, require

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager


def handle_ssh_sessions(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create a handler for ssh_sessions that captures manager via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        action = params.get("action", "list")

        if action == "list":
            active = manager.list_sessions("active")
            enriched = {}
            for sid, session in active.items():
                enriched[sid] = {
                    **session.to_dict(),
                    "idle_secs": session.idle_seconds,
                    "idle_human": session.idle_human,
                }
            return ok(sessions=enriched, count=len(enriched))

        if action == "kill":
            error = require(params, "session_id")
            if error:
                return err(error)
            return ok(**manager.kill_session(params["session_id"]))

        if action == "cleanup":
            max_idle = params.get("max_idle_minutes")
            result = manager.cleanup_idle(max_idle)
            return ok(cleaned=result["count"], details=result["killed"])

        if action == "prune":
            count = manager.prune_closed()
            return ok(pruned=count, message=f"Removed {count} closed session(s)")

        if action == "poll":
            error = require(params, "session_id")
            if error:
                return err(error)
            result = manager.poll_session(params["session_id"])
            return ok(**result)

        if action == "read_output":
            error = require(params, "session_id")
            if error:
                return err(error)
            result = manager.read_output(params["session_id"])
            return ok(**result)

        return err(f"Unknown action: {action}")

    return _handle
