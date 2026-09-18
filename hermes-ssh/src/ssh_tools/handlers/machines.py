"""Handler for the ssh_machines tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..helpers import dispatch, err, ok, param_str
from ..models import Machine

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager


def _registry_guard_warning(
    manager: SSHManager, name: str, host: str, user: str
) -> tuple[str, str] | None:
    """Return (warning, existing_names) when host+user already exist elsewhere.

    Discourages throwaway aliases: adding a machine whose host and user are
    already registered under a different name returns a warning instead of
    silently creating a duplicate entry. The exact name being re-added is an
    update, not a duplicate, so it never warns.
    """
    matches = [
        mname
        for mname, machine in manager.list_machines().items()
        if mname != name and machine.host == host and machine.user == user
    ]
    if not matches:
        return None
    existing = ", ".join(matches)
    noun = "name" if len(matches) == 1 else "names"
    return f"host {host} with user {user} already registered as {noun} {existing}", existing


def _handle_list(manager: SSHManager) -> str:
    machines = manager.list_machines()
    return ok(
        machines={
            name: {
                "host": machine.host,
                "user": machine.user,
                "port": machine.port,
                "aliases": machine.aliases or [],
                "tags": machine.tags or [],
                "description": machine.description,
            }
            for name, machine in machines.items()
        },
        count=len(machines),
    )


def _take_name(params: dict[str, Any]) -> tuple[str | None, str | None]:
    """Machine name or (None, error); empty passes through to registry errors."""
    return param_str(params, "name", allow_empty=True)


def _named(manager: SSHManager, params: dict[str, Any]) -> tuple[str | None, str | None]:
    """Validated machine name for remove/inspect/test, or (None, error)."""
    del manager
    return _take_name(params)


def _handle_add(manager: SSHManager, params: dict[str, Any]) -> str:
    name, error = _take_name(params)
    host, host_error = param_str(params, "host", allow_empty=True)
    if error or name is None:
        return err(error or "unreachable")
    if host_error or host is None:
        return err(host_error or "unreachable")
    try:
        machine = manager.add_machine(
            Machine(
                name=name,
                host=host,
                user=params.get("user") or manager.config.default_user,
                port=params.get("port", 22),
                key=params.get("key", ""),
                aliases=params.get("aliases", []),
                tags=params.get("tags", []),
                description=params.get("description", ""),
            )
        )
    except ValueError as exc:
        return err(str(exc))

    response: dict[str, Any] = {"machine": machine.to_dict()}
    guard = _registry_guard_warning(manager, machine.name, machine.host, machine.user)
    if guard is None:
        return ok(**response)
    warning, existing = guard
    response["warning"] = warning
    response["hint"] = (
        "If you meant to reuse that host, use the existing "
        f"registration ({existing}) instead of a new alias: "
        "ssh_machines action=inspect name=<existing> or "
        "ssh_machines action=list"
    )
    return ok(**response)


def _handle_remove(manager: SSHManager, params: dict[str, Any]) -> str:
    name, error = _named(manager, params)
    if error or name is None:
        return err(error or "unreachable")
    removed = manager.remove_machine(name)
    return ok(
        success=removed,
        message=f"Removed '{name}'" if removed else f"'{name}' not found",
    )


def _handle_inspect(manager: SSHManager, params: dict[str, Any]) -> str:
    name, error = _named(manager, params)
    if error or name is None:
        return err(error or "unreachable")
    inspected = manager.get_machine(name)
    if not inspected:
        return err(f"Machine '{name}' not found")
    canonical = manager.resolve_name(name)
    return ok(name=canonical, machine=inspected.to_dict())


def _handle_test(manager: SSHManager, params: dict[str, Any]) -> str:
    name, error = _named(manager, params)
    if error or name is None:
        return err(error or "unreachable")
    return ok(**manager.test_machine(name))


_ACTIONS = {
    "list": lambda manager, params: _handle_list(manager),
    "add": _handle_add,
    "remove": _handle_remove,
    "inspect": _handle_inspect,
    "test": _handle_test,
}


def handle_ssh_machines(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create a handler for ssh_machines that captures manager via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        return dispatch(params, _ACTIONS, manager)

    return _handle
