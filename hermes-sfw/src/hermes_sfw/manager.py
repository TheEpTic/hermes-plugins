"""SFWManager — thin facade over resolve, diagnose, run, validate, and detect.

Public surface kept stable: construction from SFWConfig, sfw_path,
is_installed, get_version/get_version_info, diagnose, wrapper_cache_fault,
bootstrap_failure_note, run_command. Logic lives in the focused modules.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from .diagnose import diagnose as _diagnose
from .models import SFWBinaryInfo, SFWCacheFault, SFWConfig, SFWDiagnosis, SFWResult
from .output import parse_output as _parse_output
from .output import _MAX_LIST_ENTRIES
from .output import run_sfw
from .resolve import (
    cache_fault,
    classify_binary,
    find_sfw,
    is_bootstrap_failure,
    known_candidates,
    query_version,
)
from .validate import validate_command, validate_workdir

__all__ = [
    "SFWBinaryInfo",
    "SFWCacheFault",
    "SFWConfig",
    "SFWDiagnosis",
    "SFWManager",
    "SFWResult",
]


class SFWManager:
    """Manages sfw CLI execution."""

    def __init__(self, config: SFWConfig | None = None) -> None:
        self._config = config or SFWConfig()

    def _known_candidates(self) -> list[Path]:
        return known_candidates()

    def wrapper_cache_fault(self, binary: str | None = None) -> SFWCacheFault | None:
        """Report a launcher whose firewall-binary cache cannot start it."""
        target = binary if binary is not None else self.sfw_path
        return cache_fault(target) if target else None

    def bootstrap_failure_note(self, *streams: str) -> str | None:
        """Note to append when an sfw invocation failed to prepare its binary."""
        if not is_bootstrap_failure(*streams):
            return None
        fault = self.wrapper_cache_fault()
        if fault is not None:
            return fault.note()
        return (
            "hermes-sfw: sfw could not prepare its firewall binary on this host. "
            "No firewall binary is cached and the release could not be fetched "
            "(the GitHub releases API is commonly rate-limited or blocked for "
            "hosted IPs). Run the sfw tool with action=status for the resolved "
            "binary, or restore a cached release while the network can reach "
            "api.github.com."
        )

    def is_installed(self) -> bool:
        """Check if sfw is available."""
        return find_sfw(self._config.sfw_bin) is not None

    @property
    def sfw_path(self) -> str | None:
        """Return the current path to the sfw binary."""
        return find_sfw(self._config.sfw_bin)

    def get_version(self) -> str | None:
        """Get the sfw binary version string, or None if unavailable."""
        version = self.get_version_info()["version"]
        assert version is None or isinstance(version, str)
        return version

    def get_version_info(self) -> dict[str, Any]:
        """Report the binary version together with the layer it came from."""
        sfw_path = self.sfw_path
        if not sfw_path:
            return {"version": None, "binary": None, "binary_kind": None, "target": None}
        info = classify_binary(sfw_path)
        return {
            "version": query_version(sfw_path),
            "binary": info.binary,
            "binary_kind": info.binary_kind,
            "target": info.target,
        }

    def diagnose(self) -> SFWDiagnosis:
        """Self-diagnose the sfw install: PATH, known locations, override."""
        return _diagnose(self._config.sfw_bin, self._known_candidates(), query_version)

    def run_command(
        self,
        command: str,
        workdir: str | None = None,
        verbose: bool = False,
    ) -> SFWResult:
        """Execute a package manager command through sfw."""
        sfw_path = self.sfw_path
        if not sfw_path:
            return SFWResult.error(command, "sfw is not installed. Install with: npm i -g sfw")
        cmd_err = validate_command(command)
        if cmd_err is not None:
            return SFWResult.error(command, cmd_err)
        try:
            resolved_workdir = validate_workdir(workdir)
        except ValueError as exc:
            return SFWResult.error(command, str(exc))
        args = [sfw_path] + (["--verbose"] if verbose else []) + shlex.split(command)
        result = run_sfw(args, command, self._config.timeout, resolved_workdir)
        note = (
            self.bootstrap_failure_note(result.stdout, result.stderr)
            if result.exit_code != 0
            else None
        )
        if note:
            result.stderr = f"{result.stderr}\n\n{note}".strip()
        return result

    _parse_output = staticmethod(_parse_output)
    _MAX_LIST_ENTRIES = _MAX_LIST_ENTRIES
