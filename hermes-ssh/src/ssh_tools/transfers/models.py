"""Data models for SSH file transfers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

TransferAction = Literal["upload", "download"]
RemoteKind = Literal["file", "directory", "symlink", "special", "missing"]


class TransferValidationError(ValueError):
    """A transfer request crossed an input or path safety boundary."""


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
