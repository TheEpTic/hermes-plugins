"""SFW result and configuration models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SFWConfig:
    """Configuration for SFWManager."""

    sfw_bin: str = "sfw"
    timeout: int = 300

    def __post_init__(self) -> None:
        bad_bin = not isinstance(self.sfw_bin, str) or not self.sfw_bin.strip()
        if bad_bin:
            raise ValueError("sfw_bin must be a non-empty string")
        bad_timeout = isinstance(self.timeout, bool) or not isinstance(self.timeout, int)
        if bad_timeout or self.timeout <= 0:
            raise ValueError(f"timeout must be a positive integer (seconds), got {self.timeout!r}")


@dataclass
class SFWResult:
    """Result of an sfw command execution."""

    success: bool
    command: str
    stdout: str
    stderr: str
    exit_code: int
    blocked: list[str] = field(default_factory=list)
    installed: list[str] = field(default_factory=list)

    @classmethod
    def error(cls, command: str, stderr: str, exit_code: int = 1) -> "SFWResult":
        return cls(success=False, command=command, stdout="", stderr=stderr, exit_code=exit_code)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "success": self.success,
            "command": self.command,
            "exit_code": self.exit_code,
        }
        if self.stdout:
            data["stdout"] = self.stdout
        if self.stderr:
            data["stderr"] = self.stderr
        if self.blocked:
            data["blocked"] = self.blocked
        if self.installed:
            data["installed"] = self.installed
        return data


@dataclass(frozen=True)
class SFWBinaryInfo:
    binary: str | None
    binary_kind: str | None
    target: str | None


@dataclass(frozen=True)
class SFWDiagnosis:
    healthy: bool
    binary: str | None
    binary_kind: str | None
    version: str | None
    why: str
    target: str | None = None
    checked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "binary": self.binary,
            "binary_kind": self.binary_kind,
            "version": self.version,
            "why": self.why,
            "target": self.target,
            "checked": self.checked,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class SFWCacheFault:
    binary: str
    reason: str
    cache_dir: str
    cached_asset: str | None = None
    repair: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "binary": self.binary,
            "reason": self.reason,
            "cache_dir": self.cache_dir,
            "cached_asset": self.cached_asset,
            "repair": self.repair,
        }

    def note(self) -> str:
        asset = f" (cached firewall binary: {self.cached_asset})" if self.cached_asset else ""
        return (
            f"hermes-sfw: the sfw launcher at {self.binary} cannot prepare its firewall binary — "
            f"{self.reason}{asset}. This is a local sfw install fault, not a dependency block: "
            "the command never reached a package manager, and hermes-sfw will not run it unfiltered. "
            f"Repair: {self.repair}"
        )
