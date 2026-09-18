"""Handler for the ssh_transfer tool."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from ..approval import approval_error, check_approval
from ..helpers import err, ok, param_bool, param_str
from ..transfers import execute_transfer

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager

_FIELDS: tuple[tuple[str, Any], ...] = (
    ("machine", param_str),
    ("source", param_str),
    ("destination", param_str),
    ("recursive", param_bool),
    ("preserve", param_bool),
    ("overwrite", param_bool),
)


def handle_ssh_transfer(manager: SSHManager) -> Callable[[dict[str, Any]], str]:
    """Create an ssh_transfer handler bound to an SSHManager."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:
        del kwargs
        action = params.get("action")
        if action not in {"upload", "download"}:
            return err("action must be 'upload' or 'download'")
        args: dict[str, Any] = {}
        for field, take in _FIELDS:
            value, error = take(params, field)
            if error or value is None:
                return err(error or "unreachable")
            args[field] = value
        if any(ord(char) < 32 or ord(char) == 127 for char in args["source"] + args["destination"]):
            return err("source and destination must not contain control characters")

        # Use a synthetic copy command so Hermes's existing sensitive write-target
        # approval patterns also cover transfer destinations such as /etc.
        approval_command = (
            f"cp -- {shlex.quote(str(args['source']))} {shlex.quote(str(args['destination']))}"
        )
        denied = approval_error(
            check_approval(approval_command), "Transfer blocked by approval system"
        )
        if denied:
            return denied

        result = execute_transfer(
            manager,
            action=action,
            machine_name=args["machine"],
            source=args["source"],
            destination=args["destination"],
            recursive=args["recursive"],
            preserve=args["preserve"],
            overwrite=args["overwrite"],
            timeout=params.get("timeout"),
        )
        return ok(**result)

    return _handle
