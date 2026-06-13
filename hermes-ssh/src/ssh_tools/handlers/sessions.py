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
            session_id = params["session_id"]
            if not isinstance(session_id, str) or not session_id:
                return err("session_id must be a non-empty string")
            return ok(**manager.kill_session(session_id))

        if action == "cleanup":
            max_idle = params.get("max_idle_minutes")
            if isinstance(max_idle, bool):
                return err("max_idle_minutes must be an integer")
            if isinstance(max_idle, str):
                try:
                    max_idle = int(max_idle)
                except ValueError:
                    return err("max_idle_minutes must be an integer")
            if max_idle is not None and not isinstance(max_idle, int):
                return err("max_idle_minutes must be an integer")
            result = manager.cleanup_idle(max_idle)
            return ok(cleaned=result["count"], details=result["killed"])

        if action == "prune":
            count = manager.prune_closed()
            return ok(pruned=count, message=f"Removed {count} closed session(s)")

        if action == "poll":
            error = require(params, "session_id")
            if error:
                return err(error)
            session_id = params["session_id"]
            if not isinstance(session_id, str) or not session_id:
                return err("session_id must be a non-empty string")
            result = manager.poll_session(session_id)
            return ok(**result)

        if action == "read_output":
            error = require(params, "session_id")
            if error:
                return err(error)
            session_id = params["session_id"]
            if not isinstance(session_id, str) or not session_id:
                return err("session_id must be a non-empty string")
            result = manager.read_output(session_id)
            return ok(**result)

        return err(f"Unknown action: {action}")

    return _handle
