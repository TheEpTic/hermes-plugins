"""OpenSSH SFTP argument and batch construction."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import SSHConfig
from ..models import Machine
from .models import TransferAction, TransferRequest


def sftp_quote(path: str) -> str:
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def ssh_options(config: SSHConfig, timeout: int, control_path: str | None) -> list[str]:
    """Shared OpenSSH -o flags: connect timeout, host-key policy, batch mode, mux."""
    opts = [
        "-o",
        f"ConnectTimeout={min(timeout, 10)}",
        "-o",
        f"StrictHostKeyChecking={config.strict_host_key_checking}",
        "-o",
        "BatchMode=yes",
    ]
    if control_path:
        opts += [
            "-o",
            "ControlMaster=auto",
            "-o",
            f"ControlPath={control_path}",
            "-o",
            "ControlPersist=300",
        ]
    return opts


def sftp_target(machine: Machine) -> str:
    host = f"[{machine.host}]" if ":" in machine.host else machine.host
    return f"{machine.user}@{host}"


def sftp_args(machine: Machine, config: SSHConfig, timeout: int) -> list[str]:
    control_path = str(config.socket_dir / f"{machine.name}.sock")
    args = ["sftp", "-b", "-"]
    args += ssh_options(config, timeout, control_path)
    args += ["-P", str(machine.port)]
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


def run_sftp(
    machine: Machine,
    config: SSHConfig,
    request: TransferRequest,
    local_path: Path,
    remote_path: str,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded OpenSSH SFTP batch operation."""
    return subprocess.run(
        sftp_args(machine, config, request.timeout),
        input=sftp_batch(
            request.action,
            local_path,
            remote_path,
            request.recursive,
            request.preserve,
        ),
        capture_output=True,
        text=True,
        timeout=request.timeout,
    )
