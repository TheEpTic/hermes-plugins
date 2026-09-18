"""Handler for the ssh_sessions tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..helpers import coerce_int, dispatch, err, ok, param_str

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


def _take_session_id(params: dict[str, Any]) -> tuple[str | None, str | None]:
    """Session id or (None, error); every op here needs one."""
    return param_str(params, "session_id")


def _handle_kill(manager: SSHManager, params: dict[str, Any]) -> str:
    session_id, error = _take_session_id(params)
    if error or session_id is None:
        return err(error or "unreachable")
    return ok(**manager.kill_session(session_id))


def _handle_cleanup(manager: SSHManager, params: dict[str, Any]) -> str:
    try:
        value = params.get("max_idle_minutes")
        max_idle = None if value is None else coerce_int(value, "max_idle_minutes")
    except ValueError as exc:
        return err(str(exc))
    result = manager.cleanup_idle(max_idle)
    return ok(cleaned=result["count"], details=result["killed"])


def _handle_poll(manager: SSHManager, params: dict[str, Any]) -> str:
    session_id, error = _take_session_id(params)
    if error or session_id is None:
        return err(error or "unreachable")
    return ok(**manager.poll_session(session_id))


def _handle_read_output(manager: SSHManager, params: dict[str, Any]) -> str:
    session_id, error = _take_session_id(params)
    if error or session_id is None:
        return err(error or "unreachable")
    return ok(**manager.read_output(session_id))


_ACTIONS = {
    "list": lambda manager, params: _handle_list(manager),
    "kill": _handle_kill,
    "cleanup": _handle_cleanup,
    "poll": _handle_poll,
    "read_output": _handle_read_output,
}


def handle_ssh_sessions(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create a handler for ssh_sessions that captures manager via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        return dispatch(params, _ACTIONS, manager)

    return _handle
