"""Safe SFTP file transfers for hermes-ssh."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .manager import SSHManager
    from .models import Machine

TransferAction = Literal["upload", "download"]
RemoteKind = Literal["file", "directory", "symlink", "special", "missing"]

_MAX_TIMEOUT = 3600
_MAX_SCAN_ENTRIES = 100_000
_GLOB_RE = re.compile(r"[*?\[\]{}]")
_SENSITIVE_PARTS = frozenset({".ssh", ".gnupg", ".aws", ".kube", ".docker", ".hermes"})
_SENSITIVE_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".pgpass",
        "credentials",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
_REMOTE_SECRET_PATHS = frozenset(
    {
        "/etc/shadow",
        "/etc/gshadow",
        "/etc/sudoers",
        "/etc/ssh/ssh_host_rsa_key",
        "/etc/ssh/ssh_host_ecdsa_key",
        "/etc/ssh/ssh_host_ed25519_key",
    }
)
_WRITE_DENIED_PREFIXES = tuple(Path(path) for path in ("/boot", "/dev", "/etc", "/proc", "/sys", "/usr"))
_AUDIT_MODES = frozenset({"redacted", "metadata", "off"})


class TransferValidationError(ValueError):
    """A transfer request crossed a path or input safety boundary."""


@dataclass(frozen=True)
class TransferRequest:
    action: TransferAction
    machine_name: str
    source: str
    destination: str
    recursive: bool
    preserve: bool
    overwrite: bool
    timeout: int


@dataclass(frozen=True)
class LocalSource:
    path: Path
    is_directory: bool
    size: int


def _normalise_timeout(value: object | None) -> int:
    if value is None:
        return 300
    if isinstance(value, bool):
        raise TransferValidationError("timeout must be an integer from 1 to 3600")
    try:
        timeout = int(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise TransferValidationError("timeout must be an integer from 1 to 3600") from exc
    if not isinstance(timeout, int) or not 1 <= timeout <= _MAX_TIMEOUT:
        raise TransferValidationError("timeout must be an integer from 1 to 3600")
    return timeout


def _path_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TransferValidationError(f"{label} must be a non-empty string")
    if len(value) > 4096:
        raise TransferValidationError(f"{label} is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise TransferValidationError(f"{label} must not contain control characters")
    return value


def _remote_path(value: object, label: str) -> str:
    path = _path_text(value, label)
    if not (path.startswith("/") or path.startswith("~/")):
        raise TransferValidationError(f"{label} must be absolute or start with '~/'.")
    if path in {"/", "~/"} or path.endswith("/"):
        raise TransferValidationError(f"{label} must name an explicit file or directory")
    if "\\" in path or _GLOB_RE.search(path):
        raise TransferValidationError(f"{label} must not contain wildcards or backslashes")
    parts = PurePosixPath(path[2:] if path.startswith("~/") else path).parts
    if any(part in {".", ".."} for part in parts):
        raise TransferValidationError(f"{label} must not contain '.' or '..' segments")
    return path


def _is_env_file(name: str) -> bool:
    lowered = name.casefold()
    return lowered == ".env" or lowered.startswith(".env.")


def _sensitive_parts(parts: tuple[str, ...], name: str) -> str | None:
    if {part.casefold() for part in parts}.intersection(_SENSITIVE_PARTS):
        return "credential directory"
    lowered = name.casefold()
    if lowered in _SENSITIVE_NAMES or _is_env_file(lowered):
        return "credential file"
    return None


def _local_sensitive_reason(path: Path) -> str | None:
    return _sensitive_parts(path.parts, path.name)


def _remote_sensitive_reason(path: str) -> str | None:
    lowered = path.casefold()
    comparable = lowered[2:] if lowered.startswith("~/") else lowered
    reason = _sensitive_parts(tuple(part for part in comparable.split("/") if part), comparable.rsplit("/", 1)[-1])
    if reason:
        return reason
    if lowered in _REMOTE_SECRET_PATHS or lowered.startswith("/etc/sudoers.d/"):
        return "system credential file"
    return None


def _hermes_write_denied(path: Path) -> bool:
    try:
        module = importlib.import_module("agent.file_safety")
        checker = getattr(module, "is_write_denied", None)
        return bool(checker(str(path))) if callable(checker) else False
    except ImportError:
        return False
    except Exception:
        return True


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _download_denied_reason(path: Path) -> str | None:
    reason = _local_sensitive_reason(path)
    if reason:
        return reason
    if _hermes_write_denied(path):
        return "Hermes write-protected path"
    if any(_within(path, prefix) for prefix in _WRITE_DENIED_PREFIXES):
        return "system path"
    return None


def _prepare_upload_source(value: str, recursive: bool) -> LocalSource:
    source = Path(value).expanduser()
    if source.is_symlink():
        raise TransferValidationError("upload source must not be a symbolic link")
    try:
        source = source.resolve(strict=True)
    except FileNotFoundError as exc:
        raise TransferValidationError(f"upload source does not exist: {value}") from exc
    if _GLOB_RE.search(str(source)):
        raise TransferValidationError("upload source must not contain wildcard characters")
    reason = _local_sensitive_reason(source)
    if reason:
        raise TransferValidationError(f"upload source is blocked because it is a {reason}")

    mode = source.stat().st_mode
    if stat.S_ISREG(mode):
        return LocalSource(source, False, source.stat().st_size)
    if not stat.S_ISDIR(mode):
        raise TransferValidationError("upload source must be a regular file or directory")
    if not recursive:
        raise TransferValidationError("recursive=true is required to upload a directory")

    size = 0
    entries = 0
    for root, directories, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        for name in [*directories, *files]:
            entries += 1
            if entries > _MAX_SCAN_ENTRIES:
                raise TransferValidationError(
                    f"recursive upload exceeds the {_MAX_SCAN_ENTRIES:,}-entry safety limit"
                )
            child = root_path / name
            relative = child.relative_to(source)
            if child.is_symlink():
                raise TransferValidationError(f"recursive upload contains a symbolic link: {relative}")
            reason = _local_sensitive_reason(child)
            if reason:
                raise TransferValidationError(f"recursive upload contains a blocked {reason}: {relative}")
            if child.is_file():
                size += child.stat().st_size
            elif not child.is_dir():
                raise TransferValidationError(f"recursive upload contains a special file: {relative}")
    return LocalSource(source, True, size)


def _prepare_download_destination(value: str) -> Path:
    destination = Path(value).expanduser()
    if destination.is_symlink():
        raise TransferValidationError("download destination must not be a symbolic link")
    destination = destination.resolve(strict=False)
    if _GLOB_RE.search(str(destination)):
        raise TransferValidationError("download destination must not contain wildcard characters")
    reason = _download_denied_reason(destination)
    if reason:
        raise TransferValidationError(f"download destination is blocked because it is a {reason}")
    return destination


def _remote_shell_path(path: str) -> str:
    if path.startswith("~/"):
        return f'"$HOME"/{shlex.quote(path[2:])}'
    return shlex.quote(path)


def _sftp_quote(path: str) -> str:
    return f'"{path.replace("\\", "\\\\").replace(chr(34), "\\\"")}"'


def _sftp_target(machine: Machine) -> str:
    host = f"[{machine.host}]" if ":" in machine.host else machine.host
    return f"{machine.user}@{host}"


def _sftp_args(machine: Machine, manager: SSHManager, timeout: int) -> list[str]:
    config = manager.config
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
        f"ControlPath={config.socket_dir / f'{machine.name}.sock'}",
        "-o",
        "ControlPersist=300",
        "-P",
        str(machine.port),
    ]
    if machine.key:
        args.extend(["-i", machine.key])
    args.append(_sftp_target(machine))
    return args


def _sftp_batch(
    action: TransferAction,
    local_path: Path,
    remote_path: str,
    recursive: bool,
    preserve: bool,
) -> str:
    flags = ("p" if preserve else "") + ("r" if recursive else "")
    option = f" -{flags}" if flags else ""
    local = _sftp_quote(str(local_path))
    remote = _sftp_quote(remote_path)
    command = "put" if action == "upload" else "get"
    first, second = (local, remote) if action == "upload" else (remote, local)
    return f"{command}{option} {first} {second}\n"


def _remote_temp(destination: str) -> str:
    suffix = uuid.uuid4().hex[:12]
    if destination.startswith("~/"):
        path = PurePosixPath(destination[2:])
        return f"~/{path.parent / f'.{path.name}.hermes-upload-{suffix}'}"
    path = PurePosixPath(destination)
    return str(path.parent / f".{path.name}.hermes-upload-{suffix}")


def _local_temp(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.hermes-download-{uuid.uuid4().hex[:12]}"


def _cleanup_local(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        with contextlib.suppress(OSError):
            path.unlink()
    elif path.is_dir():
        with contextlib.suppress(OSError):
            shutil.rmtree(path)


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(
        child.stat().st_size
        for root, _, files in os.walk(path, followlinks=False)
        for child in (Path(root) / name for name in files)
        if child.is_file() and not child.is_symlink()
    )


class TransferService:
    """Coordinates path policy, SFTP transport, staging, and transfer audit events."""

    def __init__(self, manager: SSHManager) -> None:
        self.manager = manager

    def execute(self, request: TransferRequest) -> dict[str, Any]:
        machine = self.manager.get_machine(request.machine_name)
        if machine is None:
            return {"success": False, "error": f"Machine '{request.machine_name}' not found."}
        if shutil.which("sftp") is None:
            return {
                "success": False,
                "error": "OpenSSH sftp client is not installed or available on PATH",
                "machine": machine.name,
            }
        return self._upload(request, machine) if request.action == "upload" else self._download(request, machine)

    def _probe(self, machine: str, path: str, timeout: int) -> tuple[RemoteKind | None, str | None]:
        target = _remote_shell_path(path)
        command = (
            f"if [ -L {target} ]; then exit 4; "
            f"elif [ -f {target} ]; then exit 0; "
            f"elif [ -d {target} ]; then exit 3; "
            f"elif [ -e {target} ]; then exit 5; else exit 6; fi"
        )
        result = self.manager.run_command(
            machine, command, timeout=min(timeout, 30), max_output_chars=2_000
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
        return None, str(result.get("error") or result.get("stderr") or "remote probe failed")

    def _tree_has_symlink(self, machine: str, path: str, timeout: int) -> tuple[bool | None, str | None]:
        target = _remote_shell_path(path)
        command = (
            f"link=$(find {target} -type l -print -quit 2>/dev/null); status=$?; "
            "if [ $status -ne 0 ]; then exit 9; "
            'elif [ -n "$link" ]; then printf "%s" "$link"; exit 7; else exit 0; fi'
        )
        result = self.manager.run_command(
            machine, command, timeout=min(timeout, 60), max_output_chars=2_000
        )
        if result.get("exit_code") == 0:
            return False, None
        if result.get("exit_code") == 7:
            return True, str(result.get("stdout") or "symbolic link")
        return None, str(result.get("error") or result.get("stderr") or "remote scan failed")

    def _run_sftp(
        self, machine: Machine, request: TransferRequest, local: Path, remote: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            _sftp_args(machine, self.manager, request.timeout),
            input=_sftp_batch(
                request.action, local, remote, request.recursive, request.preserve
            ),
            capture_output=True,
            text=True,
            timeout=request.timeout + 5,
        )

    def _cleanup_remote(self, machine: str, path: str, timeout: int) -> None:
        self.manager.run_command(
            machine,
            f"rm -rf -- {_remote_shell_path(path)}",
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
        temporary_arg = _remote_shell_path(temporary)
        destination_arg = _remote_shell_path(request.destination)
        if request.overwrite and not is_directory:
            command = f"mv -f -- {temporary_arg} {destination_arg}"
        else:
            command = (
                f"if [ -e {destination_arg} ] || [ -L {destination_arg} ]; then exit 3; fi; "
                f"mv -- {temporary_arg} {destination_arg}"
            )
        return self.manager.run_command(
            machine, command, timeout=min(request.timeout, 60), max_output_chars=2_000
        )

    def _upload(self, request: TransferRequest, machine: Machine) -> dict[str, Any]:
        try:
            local = _prepare_upload_source(request.source, request.recursive)
            destination = _remote_path(request.destination, "destination")
            reason = _remote_sensitive_reason(destination)
            if reason:
                raise TransferValidationError(f"upload destination is blocked because it is a {reason}")
        except TransferValidationError as exc:
            return self._validation_error(machine.name, exc)

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
        temporary = _remote_temp(destination)
        try:
            result = self._run_sftp(machine, request, local.path, temporary)
        except subprocess.TimeoutExpired:
            self._cleanup_remote(machine.name, temporary, request.timeout)
            return self._audited_error(request, machine.name, local.path, destination, started, -1, "Transfer timed out")
        if result.returncode:
            self._cleanup_remote(machine.name, temporary, request.timeout)
            message = result.stderr.strip() or f"sftp exited with code {result.returncode}"
            return self._audited_error(
                request, machine.name, local.path, destination, started, result.returncode, message
            )

        finalise = self._finalise_upload(request, machine.name, temporary, local.is_directory)
        if not finalise.get("success"):
            self._cleanup_remote(machine.name, temporary, request.timeout)
            raw_code = finalise.get("exit_code")
            code = raw_code if isinstance(raw_code, int) else -1
            message = (
                "remote destination appeared during transfer; refusing to overwrite it"
                if code == 3 and not request.overwrite
                else str(finalise.get("error") or finalise.get("stderr") or "remote rename failed")
            )
            return self._audited_error(request, machine.name, local.path, destination, started, code, message)

        elapsed = round(time.monotonic() - started, 2)
        self._audit(request, machine.name, str(local.path), destination, True, 0, elapsed, local.size)
        return self._success(request, machine.name, str(local.path), destination, elapsed, local.size)

    def _download(self, request: TransferRequest, machine: Machine) -> dict[str, Any]:
        try:
            source = _remote_path(request.source, "source")
            reason = _remote_sensitive_reason(source)
            if reason:
                raise TransferValidationError(f"download source is blocked because it is a {reason}")
            destination = _prepare_download_destination(request.destination)
        except TransferValidationError as exc:
            return self._validation_error(machine.name, exc)

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
            return self._error(machine.name, "recursive=true is required to download a directory")
        if is_directory:
            has_symlink, scan_error = self._tree_has_symlink(machine.name, source, request.timeout)
            if scan_error:
                return self._error(machine.name, f"Could not safely scan remote directory: {scan_error}")
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
            return self._error(machine.name, f"could not create destination directory: {exc}")

        started = time.monotonic()
        temporary = _local_temp(destination)
        _cleanup_local(temporary)
        try:
            result = self._run_sftp(machine, request, temporary, source)
        except subprocess.TimeoutExpired:
            _cleanup_local(temporary)
            return self._audited_error(request, machine.name, source, destination, started, -1, "Transfer timed out")
        if result.returncode:
            _cleanup_local(temporary)
            message = result.stderr.strip() or f"sftp exited with code {result.returncode}"
            return self._audited_error(
                request, machine.name, source, destination, started, result.returncode, message
            )
        try:
            os.replace(temporary, destination)
        except OSError as exc:
            _cleanup_local(temporary)
            return self._audited_error(
                request,
                machine.name,
                source,
                destination,
                started,
                -1,
                f"could not finalise local download: {exc}",
            )

        size = _path_size(destination)
        elapsed = round(time.monotonic() - started, 2)
        self._audit(request, machine.name, source, str(destination), True, 0, elapsed, size)
        result_data = self._success(
            request, machine.name, source, str(destination), elapsed, size
        )
        result_data["dirs_created"] = dirs_created
        return result_data

    @staticmethod
    def _error(machine: str, message: str) -> dict[str, Any]:
        return {"success": False, "error": message, "machine": machine}

    def _validation_error(self, machine: str, error: Exception) -> dict[str, Any]:
        return self._error(machine, str(error))

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
            entry["source"] = self._redact_local(source) if request.action == "upload" else source
            entry["destination"] = (
                destination if request.action == "upload" else self._redact_local(destination)
            )
        try:
            self.manager.config.data_dir.mkdir(parents=True, exist_ok=True)
            path = self.manager.config.data_dir / "command_log.jsonl"
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError:
            return

    @staticmethod
    def _redact_local(path: str) -> str:
        home = str(Path.home())
        if path == home:
            return "~"
        return "~" + path[len(home) :] if path.startswith(home + os.sep) else path


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
        if not all(isinstance(flag, bool) for flag in (recursive, preserve, overwrite)):
            raise TransferValidationError("recursive, preserve, and overwrite must be booleans")
        request = TransferRequest(
            action=action_value,
            machine_name=machine_name,
            source=_path_text(source, "source"),
            destination=_path_text(destination, "destination"),
            recursive=recursive,
            preserve=preserve,
            overwrite=overwrite,
            timeout=_normalise_timeout(timeout),
        )
    except TransferValidationError as exc:
        return {"success": False, "error": str(exc)}
    return TransferService(manager).execute(request)
