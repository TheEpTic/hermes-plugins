"""Centralized configuration for ssh-tools plugin."""

from __future__ import annotations

import contextlib
import getpass
import os
from dataclasses import dataclass, field
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent
DEFAULT_DATA_DIR = Path.home() / ".hermes" / "ssh-tools"


@dataclass(frozen=True)
class SSHConfig:
    """Immutable plugin configuration."""

    data_dir: Path = field(default=DEFAULT_DATA_DIR)

    # SSH defaults
    default_port: int = 22
    default_user: str = field(default_factory=getpass.getuser)
    connect_timeout: int = 5
    command_timeout: int = 30
    strict_host_key_checking: str = "accept-new"

    # Output
    max_output_chars: int = 50_000  # save to output_dir if exceeded
    audit_log_mode: str = "redacted"  # redacted, metadata, or off

    # Session management
    idle_check_interval: int = 60  # seconds between idle checks
    idle_timeout_minutes: int = 30  # auto-kill after this
    closed_prune_hours: int = 24  # remove closed sessions after this

    # Paths (derived from data_dir)
    @property
    def machines_file(self) -> Path:
        return self.data_dir / "machines.json"

    @property
    def sessions_file(self) -> Path:
        return self.data_dir / "sessions.json"

    @property
    def socket_dir(self) -> Path:
        return self.data_dir / "sockets"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "outputs"

    def ensure_dirs(self) -> None:
        """Create all required directories with restricted permissions."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Clean orphaned temp files from previous crashes
        for tmp in self.data_dir.glob("*.tmp"):
            with contextlib.suppress(OSError):
                tmp.unlink()
        # Restrict permissions — data dir contains machine credentials
        for d in (self.data_dir, self.socket_dir, self.output_dir):
            with contextlib.suppress(OSError):
                os.chmod(d, 0o700)


# Module-level default — importable, overridable for tests
DEFAULT_CONFIG = SSHConfig()
