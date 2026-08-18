"""Handler for the ssh_sessions tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..utils import err, ok, require

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager


def _handle_list(manager: SSHManager) -> str:
    active = manager.list_sessions("active")
    enriched = {}
    for sid, session in active.items():
        enriched[sid] = {
            **session.to_dict(),
            "idle_secs": session.idle_seconds,
            "idle_human": session.idle_human,
        }
    return ok(sessions=enriched, count=len(enriched))


def _handle_kill(manager: SSHManager, params: dict[str, Any]) -> str:
    error = require(params, "session_id")
    if error:
        return err(error)
    session_id = params["session_id"]
    if not isinstance(session_id, str) or not session_id:
        return err("session_id must be a non-empty string")
    return ok(**manager.kill_session(session_id))


def _parse_max_idle(value: Any) -> int | None:
    if isinstance(value, bool):
        raise ValueError("max_idle_minutes must be an integer")
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError as exc:
            raise ValueError("max_idle_minutes must be an integer") from exc
    if value is not None and not isinstance(value, int):
        raise ValueError("max_idle_minutes must be an integer")
    return value


def _handle_cleanup(manager: SSHManager, params: dict[str, Any]) -> str:
    try:
        max_idle = _parse_max_idle(params.get("max_idle_minutes"))
    except ValueError as exc:
        return err(str(exc))
    result = manager.cleanup_idle(max_idle)
    return ok(cleaned=result["count"], details=result["killed"])


def _handle_prune(manager: SSHManager) -> str:
    count = manager.prune_closed()
    return ok(pruned=count, message=f"Removed {count} closed session(s)")


def _handle_poll(manager: SSHManager, params: dict[str, Any]) -> str:
    error = require(params, "session_id")
    if error:
        return err(error)
    session_id = params["session_id"]
    if not isinstance(session_id, str) or not session_id:
        return err("session_id must be a non-empty string")
    return ok(**manager.poll_session(session_id))


def _handle_read_output(manager: SSHManager, params: dict[str, Any]) -> str:
    error = require(params, "session_id")
    if error:
        return err(error)
    session_id = params["session_id"]
    if not isinstance(session_id, str) or not session_id:
        return err("session_id must be a non-empty string")
    return ok(**manager.read_output(session_id))


def handle_ssh_sessions(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create a handler for ssh_sessions that captures manager via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        action = params.get("action", "list")
        if action == "list":
            return _handle_list(manager)
        if action == "kill":
            return _handle_kill(manager, params)
        if action == "cleanup":
            return _handle_cleanup(manager, params)
        if action == "prune":
            return _handle_prune(manager)
        if action == "poll":
            return _handle_poll(manager, params)
        if action == "read_output":
            return _handle_read_output(manager, params)
        return err(f"Unknown action: {action}")

    return _handle
