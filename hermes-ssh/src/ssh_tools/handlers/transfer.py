"""Handler for the ssh_transfer tool."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from ..approval import check_approval
from ..transfers import execute_transfer
from ..utils import err, ok, require

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager


def handle_ssh_transfer(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create an ssh_transfer handler bound to an SSHManager."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        del kwargs
        error = require(params, "action", "machine", "source", "destination")
        if error:
            return err(error)

        action = params["action"]
        machine = params["machine"]
        source = params["source"]
        destination = params["destination"]
        if action not in {"upload", "download"}:
            return err("action must be 'upload' or 'download'")
        if not isinstance(machine, str) or not machine:
            return err("machine must be a non-empty string")
        if not isinstance(source, str) or not source:
            return err("source must be a non-empty string")
        if not isinstance(destination, str) or not destination:
            return err("destination must be a non-empty string")
        if any(ord(char) < 32 or ord(char) == 127 for char in source + destination):
            return err("source and destination must not contain control characters")

        recursive = params.get("recursive", False)
        preserve = params.get("preserve", False)
        overwrite = params.get("overwrite", False)
        for name, value in (
            ("recursive", recursive),
            ("preserve", preserve),
            ("overwrite", overwrite),
        ):
            if not isinstance(value, bool):
                return err(f"{name} must be a boolean, got {type(value).__name__}")

        # Use a synthetic copy command so Hermes's existing sensitive write-target
        # approval patterns also cover transfer destinations such as /etc.
        approval_command = f"cp -- {shlex.quote(source)} {shlex.quote(destination)}"
        approval = check_approval(approval_command)
        if approval is not None and not approval.get("approved", True):
            return err(str(approval.get("message", "Transfer blocked by approval system")))

        result = execute_transfer(
            manager,
            action=action,
            machine_name=machine,
            source=source,
            destination=destination,
            recursive=recursive,
            preserve=preserve,
            overwrite=overwrite,
            timeout=params.get("timeout"),
        )
        return ok(**result)

    return _handle
