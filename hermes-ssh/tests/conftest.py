"""Shared test fixtures for hermes-ssh tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ssh_tools.config import SSHConfig
from ssh_tools.manager import SSHManager

if TYPE_CHECKING:
    from pathlib import Path


def _make_manager(tmp_path: Path) -> SSHManager:
    """Create an SSHManager with an isolated temp directory."""
    config = SSHConfig(data_dir=tmp_path)
    return SSHManager(config)


@pytest.fixture(autouse=True)
def _approved_commands():
    """Unit tests exercise handlers independently of a Hermes runtime."""
    with patch("ssh_tools.handlers.terminal.check_approval", return_value=None):
        yield
