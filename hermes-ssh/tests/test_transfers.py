from __future__ import annotations

import json
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
        self.config.socket_dir.mkdir(parents=True)
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


def test_sftp_args_reuses_machine_connection(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    args = _sftp_args(manager.machine, manager, timeout=300)

    assert args[0] == "sftp"
    assert args[1:3] == ["-b", "-"]
    assert "-P" in args
    assert "2222" in args
    assert "-i" in args
    assert "/keys/id_ed25519" in args
    assert "ControlMaster=auto" in args
    assert any(value.startswith("ControlPath=") for value in args)
    assert args[-1] == "deploy@192.0.2.10"
    batch = _sftp_batch(
        action="upload",
        local_path=tmp_path / "release.tar.gz",
        remote_path="/srv/releases/release.tar.gz",
        recursive=False,
        preserve=True,
    )
    assert batch.startswith("put -p ")
    assert "/srv/releases/release.tar.gz" in batch


def test_sftp_args_brackets_ipv6_host(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    manager.machine.host = "2001:db8::10"
    args = _sftp_args(manager.machine, manager, timeout=30)
    assert args[-1] == "deploy@[2001:db8::10]"


@pytest.mark.parametrize(
    "path",
    ["relative/file", "/tmp/*.log", "/tmp/../etc/passwd", "/tmp/file\nnext"],
)
def test_remote_path_validation_fails_closed(path: str) -> None:
    with pytest.raises(ValueError):
        _remote_path(path, "source")


def test_sensitive_upload_source_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / ".env"
    source.write_text("TOKEN=secret")
    with pytest.raises(ValueError, match="credential file"):
        _prepare_upload_source(str(source), recursive=False)


def test_recursive_upload_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "release"
    source.mkdir()
    (source / "app.js").write_text("ok")
    (source / "linked").symlink_to(source / "app.js")
    with pytest.raises(ValueError, match="symbolic link"):
        _prepare_upload_source(str(source), recursive=True)


def test_upload_uses_temp_then_remote_rename(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    source = tmp_path / "release.tar.gz"
    source.write_bytes(b"payload")

    completed = subprocess.CompletedProcess(["sftp"], 0, "", "")
    with (
        patch("ssh_tools.transfers.service.shutil.which", return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("missing", None)),
        patch.object(TransferService, "_run_sftp", return_value=completed) as run_sftp,
    ):
        result = execute_transfer(
            manager,
            action="upload",
            machine_name="web",
            source=str(source),
            destination="/srv/releases/release.tar.gz",
        )

    assert result["success"] is True
    assert result["machine"] == "web1"
    assert result["bytes"] == len(b"payload")
    remote_temporary = run_sftp.call_args.args[3]
    assert ".release.tar.gz.hermes-upload-" in remote_temporary
    assert any("mv --" in command for command in manager.commands)


def test_upload_refuses_existing_destination_without_overwrite(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    source = tmp_path / "release.tar.gz"
    source.write_bytes(b"payload")

    with (
        patch("ssh_tools.transfers.service.shutil.which", return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("file", None)),
        patch.object(TransferService, "_run_sftp") as run_sftp,
    ):
        result = execute_transfer(
            manager,
            action="upload",
            machine_name="web1",
            source=str(source),
            destination="/srv/release.tar.gz",
        )

    assert result["success"] is False
    assert "overwrite=true" in result["error"]
    run_sftp.assert_not_called()


def test_download_is_staged_and_atomically_replaced(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    destination = tmp_path / "downloads" / "app.log"

    def fake_sftp(machine, request, local_path: Path, remote_path: str):
        local_path.write_bytes(b"remote log")
        return subprocess.CompletedProcess(["sftp"], 0, "", "")

    with (
        patch("ssh_tools.transfers.service.shutil.which", return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("file", None)),
        patch.object(TransferService, "_run_sftp", side_effect=fake_sftp),
    ):
        result = execute_transfer(
            manager,
            action="download",
            machine_name="web1",
            source="/var/log/app.log",
            destination=str(destination),
        )

    assert result["success"] is True
    assert destination.read_bytes() == b"remote log"
    assert result["bytes"] == len(b"remote log")
    assert result["dirs_created"] is True
    assert not list(destination.parent.glob("*.hermes-download-*"))


def test_download_directory_requires_recursive(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    with (
        patch("ssh_tools.transfers.service.shutil.which", return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("directory", None)),
    ):
        result = execute_transfer(
            manager,
            action="download",
            machine_name="web1",
            source="/srv/export",
            destination=str(tmp_path / "export"),
        )
    assert result["success"] is False
    assert "recursive=true" in result["error"]


def test_download_blocks_remote_credentials(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    with patch("ssh_tools.transfers.service.shutil.which", return_value="/usr/bin/sftp"):
        result = execute_transfer(
            manager,
            action="download",
            machine_name="web1",
            source="~/.ssh/id_ed25519",
            destination=str(tmp_path / "key"),
        )
    assert result["success"] is False
    assert "credential" in result["error"]


def test_failed_download_removes_partial_temp(tmp_path: Path) -> None:
    manager = cast(Any, StubManager(tmp_path))
    destination = tmp_path / "app.log"

    def fake_sftp(machine, request, local_path: Path, remote_path: str):
        local_path.write_bytes(b"partial")
        return subprocess.CompletedProcess(["sftp"], 1, "", "network failed")

    with (
        patch("ssh_tools.transfers.service.shutil.which", return_value="/usr/bin/sftp"),
        patch.object(TransferService, "_probe", return_value=("file", None)),
        patch.object(TransferService, "_run_sftp", side_effect=fake_sftp),
    ):
        result = execute_transfer(
            manager,
            action="download",
            machine_name="web1",
            source="/var/log/app.log",
            destination=str(destination),
        )

    assert result["success"] is False
    assert not destination.exists()
    assert not list(tmp_path.glob("*.hermes-download-*"))


def test_metadata_audit_does_not_store_paths(tmp_path: Path) -> None:
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
        request,
        "web1",
        request.source,
        request.destination,
        True,
        0,
        1.2,
        123,
    )
    entry = json.loads((tmp_path / "command_log.jsonl").read_text())
    assert "source" not in entry
    assert "destination" not in entry
    assert entry["source_sha256"]
    assert entry["destination_sha256"]


def test_env_example_upload_is_allowed(tmp_path: Path) -> None:
    source = tmp_path / ".env.example"
    source.write_text("TOKEN=")
    prepared = _prepare_upload_source(str(source), recursive=False)
    assert prepared.path == source.resolve()


def test_git_credentials_upload_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / ".git-credentials"
    source.write_text("https://token@example.test")
    with pytest.raises(ValueError, match="credential file"):
        _prepare_upload_source(str(source), recursive=False)
