"""Safe OpenSSH SFTP transfers for hermes-ssh."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import TransferAction, TransferRequest, TransferValidationError
from .policy import (
    normalise_timeout,
    path_text,
    prepare_upload_source,
    remote_path,
)
from .service import TransferService, shutil
from .transport import sftp_args, sftp_batch

if TYPE_CHECKING:
    from ..manager import SSHManager

# Private aliases retained for focused tests and internal callers.
_prepare_upload_source = prepare_upload_source
_remote_path = remote_path
_sftp_args = sftp_args
_sftp_batch = sftp_batch

__all__ = [
    "TransferAction",
    "TransferRequest",
    "TransferService",
    "TransferValidationError",
    "execute_transfer",
]


def execute_transfer(
    manager: SSHManager,
    *,
    action: object,
    machine_name: object,
    source: object,
    destination: object,
    recursive: bool = False,
    preserve: bool = False,
    overwrite: bool = False,
    timeout: object | None = None,
) -> dict[str, Any]:
    """Validate a tool request and execute it through a TransferService."""
    try:
        if action == "upload":
            action_value: TransferAction = "upload"
        elif action == "download":
            action_value = "download"
        else:
            raise TransferValidationError("action must be 'upload' or 'download'")
        if not isinstance(machine_name, str) or not machine_name:
            raise TransferValidationError("machine must be a non-empty string")
        if not all(
            isinstance(flag, bool)
            for flag in (recursive, preserve, overwrite)
        ):
            raise TransferValidationError(
                "recursive, preserve, and overwrite must be booleans"
            )
        request = TransferRequest(
            action=action_value,
            machine_name=machine_name,
            source=path_text(source, "source"),
            destination=path_text(destination, "destination"),
            recursive=recursive,
            preserve=preserve,
            overwrite=overwrite,
            timeout=normalise_timeout(timeout),
        )
    except TransferValidationError as exc:
        return {"success": False, "error": str(exc)}
    return TransferService(manager).execute(request)
