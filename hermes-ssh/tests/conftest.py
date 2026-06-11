"""Shared test fixtures for hermes-ssh tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ssh_tools.config import SSHConfig
from ssh_tools.manager import SSHManager

if TYPE_CHECKING:
    from pathlib import Path


def _make_manager(tmp_path: Path) -> SSHManager:
    """Create an SSHManager with an isolated temp directory."""
    config = SSHConfig(data_dir=tmp_path)
    return SSHManager(config)
