"""SFW tool handler — runs package manager commands through sfw."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SFWManager

from ..approval import check_approval
from ..utils import err, ok, require


def _invocation_guidance(binary: str | None) -> dict[str, str | None]:
    """Describe exactly how to invoke sfw, including from background/pty shells.

    ``sfw`` is a deferred tool (discoverable via tool_search/tool_describe) and
    may not be on PATH in background/pty shells. The resolved binary path from
    ``manager.sfw_path`` is PATH-independent and can be called directly.
    """
    if binary is None:
        return {
            "tool": "sfw",
            "tool_shape": "sfw action=run command=<package manager command>",
            "binary_path": None,
            "bg_shell_shape": None,
        }
    return {
        "tool": "sfw",
        "tool_shape": "sfw action=run command=<package manager command>",
        "binary_path": binary,
        "bg_shell_shape": f"{binary} <package manager command>",
    }


def _handle_status(manager: SFWManager) -> str:
    installed = manager.is_installed()
    version = manager.get_version() if installed else None
    binary = manager.sfw_path
    return ok(
        installed=installed,
        version=version,
        binary=binary,
        invocation=_invocation_guidance(binary),
    )


def _handle_run(manager: SFWManager, params: dict[str, Any]) -> str:
    error = require(params, "command")
    if error:
        return err(error)

    command = params["command"]
    if not isinstance(command, str):
        return err(f"command must be a string, got {type(command).__name__}")

    workdir = params.get("workdir")
    if workdir is not None and not isinstance(workdir, str):
        return err(f"workdir must be a string, got {type(workdir).__name__}")

    verbose = params.get("verbose", False)
    if not isinstance(verbose, bool):
        return err(f"verbose must be a boolean, got {type(verbose).__name__}")

    approval = check_approval(command)
    if approval is not None and approval.get("approved") is not True:
        return err(str(approval.get("message", "Command blocked by approval system")))

    result = manager.run_command(command=command, workdir=workdir, verbose=verbose)
    data = result.to_dict()
    if result.success:
        return ok(**data)
    # failures: return raw JSON so success=False is never overridden
    import json as _json

    return _json.dumps(data)


def handle_sfw(manager: SFWManager) -> Callable[..., str]:
    """Return a handler with manager injected via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:  # **kwargs: Hermes framework compat
        error = require(params, "action")
        if error:
            return err(error)

        action = params["action"]
        if action == "status":
            return _handle_status(manager)
        if action == "run":
            return _handle_run(manager, params)
        return err(f"Unknown action: {action}")

    return _handle
