"""Transfer orchestration, remote probes, staging, and audit events."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import RemoteKind, TransferRequest, TransferValidationError
from .policy import (
    cleanup_local,
    local_temp,
    path_size,
    prepare_download_destination,
    prepare_upload_source,
    remote_path,
    remote_sensitive_reason,
    remote_shell_path,
    remote_temp,
)
from .transport import SFTPTransport

if TYPE_CHECKING:
    from ..manager import SSHManager
    from ..models import Machine

_AUDIT_MODES = frozenset({"redacted", "metadata", "off"})


class TransferService:
    """Coordinates policy, OpenSSH transport, finalisation, and audit events."""

    def __init__(self, manager: SSHManager) -> None:
        self.manager = manager
        self.transport = SFTPTransport(manager)

    def execute(self, request: TransferRequest) -> dict[str, Any]:
        machine = self.manager.get_machine(request.machine_name)
        if machine is None:
            return {"success": False, "error": f"Machine '{request.machine_name}' not found."}
        if shutil.which("sftp") is None:
            return self._error(
                machine.name,
                "OpenSSH sftp client is not installed or available on PATH",
            )
        if request.action == "upload":
            return self._upload(request, machine)
        return self._download(request, machine)

    def _probe(
        self,
        machine: str,
        path: str,
        timeout: int,
    ) -> tuple[RemoteKind | None, str | None]:
        target = remote_shell_path(path)
        command = (
            f"if [ -L {target} ]; then exit 4; "
            f"elif [ -f {target} ]; then exit 0; "
            f"elif [ -d {target} ]; then exit 3; "
            f"elif [ -e {target} ]; then exit 5; else exit 6; fi"
        )
        result = self.manager.run_command(
            machine,
            command,
            timeout=min(timeout, 30),
            max_output_chars=2_000,
        )
        kinds: dict[int, RemoteKind] = {
            0: "file",
            3: "directory",
            4: "symlink",
            5: "special",
            6: "missing",
        }
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int) and exit_code in kinds:
            return kinds[exit_code], None
        error = result.get("error") or result.get("stderr") or "remote probe failed"
        return None, str(error)

    def _tree_has_symlink(
        self,
        machine: str,
        path: str,
        timeout: int,
    ) -> tuple[bool | None, str | None]:
        target = remote_shell_path(path)
        command = (
            f"link=$(find {target} -type l -print -quit 2>/dev/null); status=$?; "
            "if [ $status -ne 0 ]; then exit 9; "
            'elif [ -n "$link" ]; then printf "%s" "$link"; exit 7; else exit 0; fi'
        )
        result = self.manager.run_command(
            machine,
            command,
            timeout=min(timeout, 60),
            max_output_chars=2_000,
        )
        if result.get("exit_code") == 0:
            return False, None
        if result.get("exit_code") == 7:
            return True, str(result.get("stdout") or "symbolic link")
        error = result.get("error") or result.get("stderr") or "remote scan failed"
        return None, str(error)

    def _run_sftp(
        self,
        machine: Machine,
        request: TransferRequest,
        local_path: Path,
        remote_path_value: str,
    ) -> subprocess.CompletedProcess[str]:
        return self.transport.run(machine, request, local_path, remote_path_value)

    def _cleanup_remote(
        self,
        machine: str,
        path: str,
        timeout: int,
    ) -> None:
        self.manager.run_command(
            machine,
            f"rm -rf -- {remote_shell_path(path)}",
            timeout=min(timeout, 30),
            max_output_chars=1_000,
        )

    def _finalise_upload(
        self,
        request: TransferRequest,
        machine: str,
        temporary: str,
        is_directory: bool,
    ) -> dict[str, Any]:
        temporary_arg = remote_shell_path(temporary)
        destination_arg = remote_shell_path(request.destination)
        if request.overwrite and not is_directory:
            command = (
                f"if [ -L {destination_arg} ] || [ -d {destination_arg} ]; then exit 4; fi; "
                f"if [ -e {destination_arg} ] && [ ! -f {destination_arg} ]; then exit 4; fi; "
                f"mv -f -- {temporary_arg} {destination_arg}"
            )
        else:
            command = (
                f"if [ -e {destination_arg} ] || [ -L {destination_arg} ]; then exit 3; fi; "
                f"mv -- {temporary_arg} {destination_arg}"
            )
        return self.manager.run_command(
            machine,
            command,
            timeout=min(request.timeout, 60),
            max_output_chars=2_000,
        )

    def _upload(self, request: TransferRequest, machine: Machine) -> dict[str, Any]:
        try:
            local = prepare_upload_source(request.source, request.recursive)
            destination = remote_path(request.destination, "destination")
            reason = remote_sensitive_reason(destination)
            if reason:
                raise TransferValidationError(
                    f"upload destination is blocked because it is a {reason}"
                )
        except TransferValidationError as exc:
            return self._error(machine.name, str(exc))

        kind, error = self._probe(machine.name, destination, request.timeout)
        if error:
            return self._error(machine.name, f"Could not inspect remote destination: {error}")
        if kind in {"directory", "symlink", "special"}:
            return self._error(machine.name, f"remote destination already exists as a {kind}")
        if kind == "file" and (not request.overwrite or local.is_directory):
            message = (
                "recursive directory uploads cannot replace an existing destination"
                if local.is_directory
                else "remote destination already exists; set overwrite=true to replace it"
            )
            return self._error(machine.name, message)

        started = time.monotonic()
        temporary = remote_temp(destination)
        try:
            result = self._run_sftp(machine, request, local.path, temporary)
        except subprocess.TimeoutExpired:
            self._cleanup_remote(machine.name, temporary, request.timeout)
            return self._audited_error(
                request,
                machine.name,
                local.path,
                destination,
                started,
                -1,
                "Transfer timed out",
            )
        if result.returncode:
            self._cleanup_remote(machine.name, temporary, request.timeout)
            message = result.stderr.strip() or f"sftp exited with code {result.returncode}"
            return self._audited_error(
                request,
                machine.name,
                local.path,
                destination,
                started,
                result.returncode,
                message,
            )

        finalise = self._finalise_upload(
            request,
            machine.name,
            temporary,
            local.is_directory,
        )
        if not finalise.get("success"):
            self._cleanup_remote(machine.name, temporary, request.timeout)
            raw_code = finalise.get("exit_code")
            code = raw_code if isinstance(raw_code, int) else -1
            if code == 3 and not request.overwrite:
                message = "remote destination appeared during transfer; refusing to overwrite it"
            elif code == 4:
                message = "remote destination changed to an unsupported type during transfer"
            else:
                detail = finalise.get("error") or finalise.get("stderr")
                message = str(detail or "remote rename failed")
            return self._audited_error(
                request,
                machine.name,
                local.path,
                destination,
                started,
                code,
                message,
            )

        elapsed = round(time.monotonic() - started, 2)
        self._audit(
            request,
            machine.name,
            str(local.path),
            destination,
            True,
            0,
            elapsed,
            local.size,
        )
        return self._success(
            request,
            machine.name,
            str(local.path),
            destination,
            elapsed,
            local.size,
        )

    def _download(self, request: TransferRequest, machine: Machine) -> dict[str, Any]:
        try:
            source = remote_path(request.source, "source")
            reason = remote_sensitive_reason(source)
            if reason:
                raise TransferValidationError(
                    f"download source is blocked because it is a {reason}"
                )
            destination = prepare_download_destination(request.destination)
        except TransferValidationError as exc:
            return self._error(machine.name, str(exc))

        kind, error = self._probe(machine.name, source, request.timeout)
        if error:
            return self._error(machine.name, f"Could not inspect remote source: {error}")
        if kind == "missing":
            return self._error(machine.name, "remote source does not exist")
        if kind == "symlink":
            return self._error(machine.name, "remote source must not be a symbolic link")
        if kind == "special":
            return self._error(machine.name, "remote source must be a regular file or directory")

        is_directory = kind == "directory"
        if is_directory and not request.recursive:
            return self._error(
                machine.name,
                "recursive=true is required to download a directory",
            )
        if is_directory:
            has_symlink, scan_error = self._tree_has_symlink(
                machine.name,
                source,
                request.timeout,
            )
            if scan_error:
                return self._error(
                    machine.name,
                    f"Could not safely scan remote directory: {scan_error}",
                )
            if has_symlink:
                return self._error(
                    machine.name,
                    "remote directory contains a symbolic link; refusing recursive download",
                )

        if destination.exists():
            if destination.is_dir() or destination.is_symlink():
                return self._error(
                    machine.name,
                    "download destination already exists as a directory or symbolic link",
                )
            if is_directory:
                return self._error(
                    machine.name,
                    "recursive directory downloads cannot replace an existing destination",
                )
            if not request.overwrite:
                return self._error(
                    machine.name,
                    "download destination already exists; set overwrite=true to replace it",
                )

        dirs_created = not destination.parent.exists()
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return self._error(
                machine.name,
                f"could not create destination directory: {exc}",
            )

        started = time.monotonic()
        temporary = local_temp(destination)
        cleanup_local(temporary)
        try:
            result = self._run_sftp(machine, request, temporary, source)
        except subprocess.TimeoutExpired:
            cleanup_local(temporary)
            return self._audited_error(
                request,
                machine.name,
                source,
                destination,
                started,
                -1,
                "Transfer timed out",
            )
        if result.returncode:
            cleanup_local(temporary)
            message = result.stderr.strip() or f"sftp exited with code {result.returncode}"
            return self._audited_error(
                request,
                machine.name,
                source,
                destination,
                started,
                result.returncode,
                message,
            )
        try:
            os.replace(temporary, destination)
        except OSError as exc:
            cleanup_local(temporary)
            return self._audited_error(
                request,
                machine.name,
                source,
                destination,
                started,
                -1,
                f"could not finalise local download: {exc}",
            )

        size = path_size(destination)
        elapsed = round(time.monotonic() - started, 2)
        self._audit(
            request,
            machine.name,
            source,
            str(destination),
            True,
            0,
            elapsed,
            size,
        )
        response = self._success(
            request,
            machine.name,
            source,
            str(destination),
            elapsed,
            size,
        )
        response["dirs_created"] = dirs_created
        return response

    @staticmethod
    def _error(machine: str, message: str) -> dict[str, Any]:
        return {"success": False, "error": message, "machine": machine}

    def _audited_error(
        self,
        request: TransferRequest,
        machine: str,
        source: str | Path,
        destination: str | Path,
        started: float,
        exit_code: int,
        message: str,
    ) -> dict[str, Any]:
        elapsed = round(time.monotonic() - started, 2)
        self._audit(
            request,
            machine,
            str(source),
            str(destination),
            False,
            exit_code,
            elapsed,
            0,
        )
        return {
            "success": False,
            "error": message,
            "machine": machine,
            "exit_code": exit_code,
            "elapsed_secs": elapsed,
        }

    @staticmethod
    def _success(
        request: TransferRequest,
        machine: str,
        source: str,
        destination: str,
        elapsed: float,
        size: int,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "action": request.action,
            "machine": machine,
            "source": source,
            "destination": destination,
            "recursive": request.recursive,
            "preserve": request.preserve,
            "overwrite": request.overwrite,
            "bytes": size,
            "elapsed_secs": elapsed,
            "transport": "openssh-sftp",
        }

    def _audit(
        self,
        request: TransferRequest,
        machine: str,
        source: str,
        destination: str,
        success: bool,
        exit_code: int,
        elapsed: float,
        size: int,
    ) -> None:
        mode = str(self.manager.config.audit_log_mode).strip().lower()
        if mode not in _AUDIT_MODES:
            mode = "redacted"
        if mode == "off":
            return

        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": "transfer",
            "direction": request.action,
            "machine": machine,
            "recursive": request.recursive,
            "preserve": request.preserve,
            "overwrite": request.overwrite,
            "success": success,
            "exit_code": exit_code,
            "elapsed_secs": elapsed,
            "bytes": size,
        }
        if mode == "metadata":
            entry.update(
                {
                    "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "source_length": len(source),
                    "destination_sha256": hashlib.sha256(destination.encode()).hexdigest(),
                    "destination_length": len(destination),
                }
            )
        else:
            if request.action == "upload":
                entry["source"] = self._redact_local(source)
                entry["destination"] = destination
            else:
                entry["source"] = source
                entry["destination"] = self._redact_local(destination)

        try:
            self.manager.config.data_dir.mkdir(parents=True, exist_ok=True)
            path = self.manager.config.data_dir / "command_log.jsonl"
            fd = os.open(
                str(path),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError:
            return

    @staticmethod
    def _redact_local(path: str) -> str:
        home = str(Path.home())
        if path == home:
            return "~"
        if path.startswith(home + os.sep):
            return "~" + path[len(home) :]
        return path
