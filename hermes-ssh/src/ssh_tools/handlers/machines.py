"""Handler for the ssh_machines tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..models import Machine
from ..utils import err, ok, require

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
    if len(matches) == 1:
        warning = f"host {host} with user {user} already registered as name {existing}"
    else:
        warning = f"host {host} with user {user} already registered as names {existing}"
    return warning, existing


def handle_ssh_machines(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create a handler for ssh_machines that captures manager via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        action = params.get("action", "list")

        if action == "list":
            machines = manager.list_machines()
            return ok(
                machines={
                    name: {
                        "host": m.host,
                        "user": m.user,
                        "port": m.port,
                        "aliases": m.aliases or [],
                        "tags": m.tags or [],
                        "description": m.description,
                    }
                    for name, m in machines.items()
                },
                count=len(machines),
            )

        if action == "add":
            error = require(params, "name", "host")
            if error:
                return err(error)
            try:
                machine = manager.add_machine(
                    Machine(
                        name=params["name"],
                        host=params["host"],
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
            if guard is not None:
                warning, existing = guard
                response["warning"] = warning
                response["hint"] = (
                    "If you meant to reuse that host, use the existing "
                    f"registration ({existing}) instead of a new alias: "
                    "ssh_machines action=inspect name=<existing> or "
                    "ssh_machines action=list"
                )
            return ok(**response)

        if action == "remove":
            error = require(params, "name")
            if error:
                return err(error)
            name = params["name"]
            if not isinstance(name, str):
                return err("name must be a string")
            removed = manager.remove_machine(name)
            return ok(
                success=removed,
                message=f"Removed '{name}'" if removed else f"'{name}' not found",
            )

        if action == "inspect":
            error = require(params, "name")
            if error:
                return err(error)
            name = params["name"]
            if not isinstance(name, str):
                return err("name must be a string")
            inspected = manager.get_machine(name)
            if not inspected:
                return err(f"Machine '{name}' not found")
            canonical = manager.resolve_name(name)
            return ok(name=canonical, machine=inspected.to_dict())

        if action == "test":
            error = require(params, "name")
            if error:
                return err(error)
            name = params["name"]
            if not isinstance(name, str):
                return err("name must be a string")
            return ok(**manager.test_machine(name))

        return err(f"Unknown action: {action}")

    return _handle
