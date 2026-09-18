from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from ssh_tools.transfers import (
    TransferRequest,
    TransferService,
    _prepare_upload_source,
    _remote_path,
    _sftp_args,
    _sftp_batch,
    execute_transfer,
)


class StubManager:
    def __init__(self, tmp_path: Path, *, audit_mode: str = "redacted") -> None:
        self.config = SimpleNamespace(
            data_dir=tmp_path,
            socket_dir=tmp_path / "sockets",
            strict_host_key_checking="accept-new",
            audit_log_mode=audit_mode,
        )
        self.config.socket_dir.mkdir(parents=True, exist_ok=True)
        self.machine = SimpleNamespace(
            name="web1",
            host="192.0.2.10",
            user="deploy",
            port=2222,
            key="/keys/id_ed25519",
        )
        self.commands: list[str] = []

    def get_machine(self, name: str):
        return self.machine if name in {"web1", "web"} else None

    def run_command(self, machine_name: str, command: str, **kwargs):
        self.commands.append(command)
        return {"success": True, "exit_code": 0, "stdout": "", "stderr": ""}


class LocalExecManager(StubManager):
    """Run transfer finalise/scan commands locally instead of over ssh."""

    def run_command(self, machine_name: str, command: str, **kwargs: Any) -> dict[str, Any]:
        del machine_name, kwargs
        self.commands.append(command)
        completed = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True, check=False
        )
        return {
            "success": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }


_WHICH = "ssh_tools.transfers.service.shutil.which"


def _ok() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["sftp"], 0, "", "")


def _upload_locally(result: str | None, payload: bytes = b"payload"):
    """fake _run_sftp that materialises the remote temp file locally."""

    def fake_sftp(machine: Any, request: Any, local_path: Path, remote_path: str):
        del machine, request, local_path
        temporary = Path(remote_path)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(payload)
        return subprocess.CompletedProcess(["sftp"], 0, "", "")

    return fake_sftp


def _writes_file(path_content: bytes | None, code: int = 0, err: str = ""):
    """fake _run_sftp that writes the staged local file (downloads)."""

    def fake_sftp(machine: Any, request: Any, local_path: Path, remote_path: str):
        del machine, request, remote_path
        if path_content is not None:
            local_path.write_bytes(path_content)
        return subprocess.CompletedProcess(["sftp"], code, "", err)

    return fake_sftp


def _fake_mv_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mv = fake_bin / "mv"
    fake_mv.write_text("#!/bin/sh\n" + script)
    fake_mv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")


def _up_source(tmp_path: Path, name: str = "release.tar.gz") -> Path:
    source = tmp_path / name
    source.write_bytes(b"payload")
    return source


def test_sftp_args_reuse_machine_connection(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    args = _sftp_args(manager.machine, manager.config, timeout=300)
    assert args[0] == "sftp" and args[1:3] == ["-b", "-"]
    for flag in ("-P", "2222", "-i", "/keys/id_ed25519", "ControlMaster=auto"):
        assert flag in args
    assert any(v.startswith("ControlPath=") for v in args)
    assert args[-1] == "deploy@192.0.2.10"
    manager.machine.host = "2001:db8::10"  # ipv6 brackets
    assert _sftp_args(manager.machine, manager.config, timeout=30)[-1] == "deploy@[2001:db8::10]"
    batch = _sftp_batch(
        action="upload",
        local_path=tmp_path / "release.tar.gz",
        remote_path="/srv/releases/release.tar.gz",
        recursive=False,
        preserve=True,
    )
    assert batch.startswith("put -p ") and "/srv/releases/release.tar.gz" in batch


@pytest.mark.parametrize(
    "path", ["relative/file", "/tmp/*.log", "/tmp/../etc/passwd", "/tmp/file\nnext"]
)
def test_remote_path_validation_fails_closed(path: str) -> None:
    with pytest.raises(ValueError):
        _remote_path(path, "source")


@pytest.mark.parametrize(
    "name,blocked", [(".env", True), (".env.example", False), (".git-credentials", True)]
)
def test_upload_credential_screening(tmp_path: Path, name: str, blocked: bool) -> None:
    source = tmp_path / name
    source.write_text("TOKEN=secret")
    if blocked:
        with pytest.raises(ValueError, match="credential file"):
            _prepare_upload_source(str(source), recursive=False)
    else:
        assert _prepare_upload_source(str(source), recursive=False).path == source.resolve()


def test_recursive_upload_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "release"
    source.mkdir()
    (source / "app.js").write_text("ok")
    (source / "linked").symlink_to(source / "app.js")
    with pytest.raises(ValueError, match="symbolic link"):
        _prepare_upload_source(str(source), recursive=True)


def _xfer(manager: Any, **kw: Any) -> dict:
    return execute_transfer(manager, machine_name=kw.pop("machine_name", "web1"), **kw)


def test_upload_temp_then_rename_and_refusals(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    source = _up_source(tmp_path)
    with (
        patch(_WHICH, return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("missing", None)),
        patch.object(TransferService, "_run_sftp", return_value=_ok()) as run_sftp,
    ):
        result = _xfer(
            manager,
            action="upload",
            machine_name="web",
            source=str(source),
            destination="/srv/releases/release.tar.gz",
        )
    assert result["success"] is True and result["machine"] == "web1"
    assert result["bytes"] == len(b"payload")
    assert ".release.tar.gz.hermes-upload-" in run_sftp.call_args.args[3]
    assert any("mv -n --" in c for c in manager.commands)
    # existing destination without overwrite
    with (
        patch(_WHICH, return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("file", None)),
        patch.object(TransferService, "_run_sftp") as run_sftp,
    ):
        refused = _xfer(
            manager, action="upload", source=str(source), destination="/srv/release.tar.gz"
        )
    assert refused["success"] is False and "overwrite=true" in refused["error"]
    run_sftp.assert_not_called()


_MV_MKDIR = (
    "for destination; do :; done\n" '/usr/bin/mkdir -- "$destination"\n' 'exec /usr/bin/mv "$@"\n'
)
_MV_CONCURRENT = (
    "for destination; do :; done\n"
    '/usr/bin/mkdir -p -- "$(/usr/bin/dirname -- "$destination")"\n'
    '/usr/bin/printf concurrent > "$destination"\n'
    '/usr/bin/mv "$@"\n'
    "exit 1\n"
)


@pytest.mark.parametrize(
    "script,fragment,check",
    [
        (_MV_MKDIR, "unsupported type", "isdir"),
        (_MV_CONCURRENT, "appeared during transfer", "concurrent"),
    ],
)
def test_upload_race_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str, fragment: str, check: str
) -> None:
    manager = cast(Any, LocalExecManager(tmp_path))
    source = _up_source(tmp_path)
    destination = tmp_path / "remote" / "release.tar.gz"
    _fake_mv_bin(tmp_path, monkeypatch, script)
    with (
        patch(_WHICH, return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("missing", None)),
        patch.object(TransferService, "_run_sftp", side_effect=_upload_locally(None)),
    ):
        result = _xfer(manager, action="upload", source=str(source), destination=str(destination))
    assert result["success"] is False and fragment in result["error"]
    if check == "isdir":
        assert destination.is_dir()
        assert not list(destination.glob(".release.tar.gz.hermes-upload-*"))
    else:
        assert destination.read_bytes() == b"concurrent"


def test_download_staged_atomic_replace(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    destination = tmp_path / "downloads" / "app.log"
    with (
        patch(_WHICH, return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("file", None)),
        patch.object(TransferService, "_run_sftp", side_effect=_writes_file(b"remote log")),
    ):
        result = _xfer(
            manager, action="download", source="/var/log/app.log", destination=str(destination)
        )
    assert result["success"] is True and destination.read_bytes() == b"remote log"
    assert result["dirs_created"] is True and result["bytes"] == len(b"remote log")
    assert not list(destination.parent.glob("*.hermes-download-*"))


def test_download_concurrent_and_failed_cleanup(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    destination = tmp_path / "downloads" / "app.log"

    def racing(machine: Any, request: Any, local_path: Path, remote_path: str):
        del machine, request, remote_path
        local_path.write_bytes(b"remote log")
        destination.write_bytes(b"concurrent writer")
        return _ok()

    with (
        patch(_WHICH, return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("file", None)),
        patch.object(TransferService, "_run_sftp", side_effect=racing),
    ):
        raced = _xfer(
            manager, action="download", source="/var/log/app.log", destination=str(destination)
        )
    assert raced["success"] is False and "appeared during transfer" in raced["error"]
    assert destination.read_bytes() == b"concurrent writer"
    assert not list(destination.parent.glob("*.hermes-download-*"))
    # failed sftp removes the partial temp
    partial = tmp_path / "app.log"
    with (
        patch(_WHICH, return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("file", None)),
        patch.object(
            TransferService, "_run_sftp", side_effect=_writes_file(b"partial", 1, "network failed")
        ),
    ):
        failed = _xfer(
            manager, action="download", source="/var/log/app.log", destination=str(partial)
        )
    assert failed["success"] is False and not partial.exists()
    assert not list(tmp_path.glob("*.hermes-download-*"))


def test_download_guardrails(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    with (
        patch(_WHICH, return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("directory", None)),
    ):
        no_recurse = _xfer(
            manager,
            action="download",
            source="/srv/export",
            destination=str(tmp_path / "export"),
        )
    assert no_recurse["success"] is False and "recursive=true" in no_recurse["error"]
    with patch(_WHICH, return_value="/usr/bin/sftp"):
        creds = _xfer(
            manager,
            action="download",
            source="~/.ssh/id_ed25519",
            destination=str(tmp_path / "key"),
        )
    assert creds["success"] is False and "credential" in creds["error"]
    with (
        patch(_WHICH, return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("directory", None)),
        patch.object(
            TransferService,
            "_tree_has_unsafe_entry",
            return_value=(True, "/srv/export/.ssh/id_ed25519"),
        ),
        patch.object(TransferService, "_run_sftp") as run_sftp,
    ):
        nested = _xfer(
            manager,
            action="download",
            source="/srv/export",
            destination=str(tmp_path / "export"),
            recursive=True,
        )
    assert nested["success"] is False and "credential path" in nested["error"]
    run_sftp.assert_not_called()
    # the scanner itself flags a real nested key dir
    scan_export = tmp_path / "scan_export"
    (scan_export / ".ssh").mkdir(parents=True)
    (scan_export / ".ssh" / "id_ed25519").write_text("private key")
    scanner = cast(Any, LocalExecManager(tmp_path))
    unsafe, detail = TransferService(scanner)._tree_has_unsafe_entry("web1", str(scan_export), 30)
    assert unsafe is True and detail is not None and ".ssh" in detail


def test_metadata_audit_omits_paths(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path, audit_mode="metadata"))
    request = TransferRequest(
        action="upload",
        machine_name="web1",
        source="/home/alice/project/release.tar.gz",
        destination="/srv/releases/release.tar.gz",
        recursive=False,
        preserve=False,
        overwrite=False,
        timeout=300,
    )
    TransferService(manager)._audit(
        request, "web1", request.source, request.destination, True, 0, 1.2, 123
    )
    entry = json.loads((tmp_path / "command_log.jsonl").read_text())
    assert "source" not in entry and "destination" not in entry
    assert entry["source_sha256"] and entry["destination_sha256"]
