"""SFW tool handler — runs package manager commands through sfw."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SFWManager

from ..utils import err, ok, require


def handle_sfw(manager: SFWManager) -> Callable[..., str]:
    """Return a handler with manager injected via closure."""

    def _handle(params: dict[str, Any], **kwargs: Any) -> str:  # **kwargs: Hermes framework compat
        error = require(params, "action")
        if error:
            return err(error)

        action = params["action"]

        if action == "status":
            installed = manager.is_installed()
            version = manager.get_version() if installed else None
            return ok(
                installed=installed,
                version=version,
                binary=manager.sfw_path,
            )

        if action == "run":
            error = require(params, "command")
            if error:
                return err(error)

            command = params["command"]
            workdir = params.get("workdir")
            verbose = params.get("verbose", False)

            result = manager.run_command(
                command=command,
                workdir=workdir,
                verbose=verbose,
            )
            d = result.to_dict()
            if result.success:
                return ok(**d)
            # failures: return raw JSON so success=False is never overridden
            import json as _json

            return _json.dumps(d)

        else:
            return err(f"Unknown action: {action}")

    return _handle
