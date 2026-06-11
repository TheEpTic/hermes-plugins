"""Handler for the ssh_machines tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..models import Machine
from ..utils import err, ok, require

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager


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
            machine = manager.add_machine(
                Machine(
                    name=params["name"],
                    host=params["host"],
                    user=params.get("user", "root"),
                    port=params.get("port", 22),
                    key=params.get("key", ""),
                    aliases=params.get("aliases", []),
                    tags=params.get("tags", []),
                    description=params.get("description", ""),
                )
            )
            return ok(machine=machine.to_dict())

        if action == "remove":
            error = require(params, "name")
            if error:
                return err(error)
            name = params["name"]
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
            inspected = manager.get_machine(name)
            if not inspected:
                return err(f"Machine '{name}' not found")
            canonical = manager.resolve_name(name)
            return ok(name=canonical, machine=inspected.to_dict())

        if action == "test":
            error = require(params, "name")
            if error:
                return err(error)
            return ok(**manager.test_machine(params["name"]))

        return err(f"Unknown action: {action}")

    return _handle
