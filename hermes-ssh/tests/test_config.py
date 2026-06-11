"""Tests for ssh_tools.config."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ssh_tools.config import SSHConfig


def test_default_config_paths(tmp_path: Path) -> None:
    config = SSHConfig(data_dir=tmp_path)
    assert config.machines_file == tmp_path / "machines.json"
    assert config.sessions_file == tmp_path / "sessions.json"
    assert config.socket_dir == tmp_path / "sockets"


def test_ensure_dirs_creates_directories(tmp_path: Path) -> None:
    config = SSHConfig(data_dir=tmp_path / "deep" / "nested")
    config.ensure_dirs()
    assert (tmp_path / "deep" / "nested").is_dir()
    assert (tmp_path / "deep" / "nested" / "sockets").is_dir()


def test_config_defaults() -> None:
    config = SSHConfig()
    assert config.default_port == 22
    assert config.default_user == "root"
    assert config.connect_timeout == 5
    assert config.command_timeout == 30
    assert config.idle_check_interval == 60
    assert config.idle_timeout_minutes == 30
    assert config.closed_prune_hours == 24


def test_config_is_frozen() -> None:
    config = SSHConfig()
    try:
        config.default_port = 2222  # type: ignore[misc]
        raise AssertionError("Should have raised FrozenInstanceError")
    except AttributeError:
        pass


def test_config_custom_values() -> None:
    config = SSHConfig(
        default_port=2222,
        default_user="admin",
        connect_timeout=10,
        command_timeout=60,
        idle_timeout_minutes=15,
    )
    assert config.default_port == 2222
    assert config.default_user == "admin"
    assert config.connect_timeout == 10
    assert config.command_timeout == 60
    assert config.idle_timeout_minutes == 15
