"""OpenSSH SFTP argument and batch construction."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .models import TransferAction, TransferRequest

if TYPE_CHECKING:
    from ..manager import SSHManager
    from ..models import Machine


def sftp_quote(path: str) -> str:
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def sftp_target(machine: Machine) -> str:
    host = f"[{machine.host}]" if ":" in machine.host else machine.host
    return f"{machine.user}@{host}"


def sftp_args(machine: Machine, manager: SSHManager, timeout: int) -> list[str]:
    config = manager.config
    control_path = config.socket_dir / f"{machine.name}.sock"
    args = [
        "sftp",
        "-b",
        "-",
        "-o",
        f"ConnectTimeout={min(timeout, 10)}",
        "-o",
        f"StrictHostKeyChecking={config.strict_host_key_checking}",
        "-o",
        "BatchMode=yes",
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={control_path}",
        "-o",
        "ControlPersist=300",
        "-P",
        str(machine.port),
    ]
    if machine.key:
        args.extend(["-i", machine.key])
    args.append(sftp_target(machine))
    return args


def sftp_batch(
    action: TransferAction,
    local_path: Path,
    remote_path: str,
    recursive: bool,
    preserve: bool,
) -> str:
    flags = ("p" if preserve else "") + ("r" if recursive else "")
    option = f" -{flags}" if flags else ""
    local = sftp_quote(str(local_path))
    remote = sftp_quote(remote_path)
    command = "put" if action == "upload" else "get"
    first, second = (local, remote) if action == "upload" else (remote, local)
    return f"{command}{option} {first} {second}\n"


class SFTPTransport:
    """Runs one bounded OpenSSH SFTP batch operation."""

    def __init__(self, manager: SSHManager) -> None:
        self.manager = manager

    def run(
        self,
        machine: Machine,
        request: TransferRequest,
        local_path: Path,
        remote_path: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            sftp_args(machine, self.manager, request.timeout),
            input=sftp_batch(
                request.action,
                local_path,
                remote_path,
                request.recursive,
                request.preserve,
            ),
            capture_output=True,
            text=True,
            timeout=request.timeout + 5,
        )
