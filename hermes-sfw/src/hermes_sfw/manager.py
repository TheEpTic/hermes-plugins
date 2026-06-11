"""SFWManager — wraps the sfw CLI for safe dependency installation."""

from __future__ import annotations

import errno
import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (Fix 5: moved from _parse_output)
# ---------------------------------------------------------------------------

_BLOCKED_KEYWORDS = frozenset({"blocked", "🚫", "🔴"})
_INSTALLED_KEYWORDS = frozenset({"installed", "🟢", "added"})
_ALL_KEYWORDS = _BLOCKED_KEYWORDS | _INSTALLED_KEYWORDS

# ---------------------------------------------------------------------------
# Command prefix allowlist (Fix 1)
# ---------------------------------------------------------------------------

_ALLOWED_PREFIXES = frozenset(
    {
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "pip",
        "pip3",
        "uv",
        "cargo",
        "rustup",
    }
)

# ---------------------------------------------------------------------------
# OSError errno → generic message mapping (Fix 7)
# ---------------------------------------------------------------------------

_ERRNO_MESSAGES: dict[int, str] = {
    errno.EACCES: "Permission denied",
    errno.ENOENT: "No such file or directory",
    errno.EISDIR: "Is a directory",
    errno.ENOTDIR: "Not a directory",
    errno.ENAMETOOLONG: "File name too long",
    errno.ELOOP: "Too many levels of symbolic links",
}


@dataclass(frozen=True)
class SFWConfig:
    """Configuration for SFWManager."""

    sfw_bin: str = "sfw"
    timeout: int = 300  # 5 minutes max for installs


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

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "success": self.success,
            "command": self.command,
            "exit_code": self.exit_code,
        }
        if self.stdout:
            d["stdout"] = self.stdout
        if self.stderr:
            d["stderr"] = self.stderr
        if self.blocked:
            d["blocked"] = self.blocked
        if self.installed:
            d["installed"] = self.installed
        return d


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _validate_command(command: str) -> str | None:
    """Check that the command starts with an allowed prefix.

    Returns an error message if the command is disallowed, or None if OK.
    Also handles shlex.split() ValueError (Fix 2).
    """
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return f"Command parse error: {exc}"

    if not parts:
        return "Command is empty"

    program = Path(parts[0]).name
    if program not in _ALLOWED_PREFIXES:
        return (
            f"Command prefix '{program}' is not allowed. "
            f"Allowed: {', '.join(sorted(_ALLOWED_PREFIXES))}"
        )

    return None


def _validate_workdir(workdir: str | None) -> str | None:
    """Resolve and validate the working directory (Fix 3).

    Returns the resolved real path if valid, or None if workdir is not set.
    Raises ValueError with a message if the path is invalid.
    """
    if workdir is None:
        return None

    resolved = str(Path(workdir).expanduser().resolve())
    if not Path(resolved).exists():
        raise ValueError(f"Working directory does not exist: {workdir}")
    if not Path(resolved).is_dir():
        raise ValueError(f"Working directory is not a directory: {workdir}")
    return resolved


def _sanitize_output(text: str, max_len: int = 10_000) -> str:
    """Truncate long output and add a note about total size (Fix 4)."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [output truncated, total {len(text)} chars]"


def _sanitize_oserror(exc: OSError) -> str:
    """Map common errno values to generic messages (Fix 7)."""
    errnum = getattr(exc, "errno", None)
    if errnum is not None and errnum in _ERRNO_MESSAGES:
        return _ERRNO_MESSAGES[errnum]
    return "An internal error occurred"


class SFWManager:
    """Manages sfw CLI execution."""

    def __init__(self, config: SFWConfig | None = None) -> None:
        self._config = config or SFWConfig()
        self._sfw_path = self._find_sfw()

    def _find_sfw(self) -> str | None:
        """Locate the sfw binary."""
        # If config points to a specific binary, use it directly
        if self._config.sfw_bin != "sfw":
            if Path(self._config.sfw_bin).exists():
                return self._config.sfw_bin
            return None

        # Default: search PATH and common locations
        path = shutil.which(self._config.sfw_bin)
        if path:
            return path
        # Check common locations (Fix 8: added os.access check)
        for candidate in [
            Path.home() / ".local" / "share" / "pnpm" / "bin" / "sfw",
            Path("/usr/local/bin/sfw"),
            Path.home() / ".npm-global" / "bin" / "sfw",
        ]:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    def is_installed(self) -> bool:
        """Check if sfw is available."""
        return self._sfw_path is not None

    @property
    def sfw_path(self) -> str | None:
        """Return the path to the sfw binary."""
        return self._sfw_path

    def get_version(self) -> str | None:
        """Get sfw version string."""
        if not self._sfw_path:
            return None
        try:
            result = subprocess.run(
                [self._sfw_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() or result.stderr.strip()
        except (subprocess.TimeoutExpired, OSError):
            return None

    def run_command(
        self,
        command: str,
        workdir: str | None = None,
        verbose: bool = False,
    ) -> SFWResult:
        """Execute a package manager command through sfw.

        Args:
            command: The package manager command (e.g., 'npm install express').
            workdir: Working directory for the command.
            verbose: Enable verbose output.

        Returns:
            SFWResult with stdout, stderr, exit_code, and parsed blocked/installed.
        """
        if not self._sfw_path:
            return SFWResult(
                success=False,
                command=command,
                stdout="",
                stderr="sfw is not installed. Install with: npm i -g sfw",
                exit_code=1,
            )

        # Fix 1 + Fix 2: validate command prefix allowlist and handle parse errors
        cmd_err = _validate_command(command)
        if cmd_err is not None:
            return SFWResult(
                success=False,
                command=command,
                stdout="",
                stderr=cmd_err,
                exit_code=1,
            )

        # Command passed validation, so shlex.split should succeed here too
        cmd_parts = shlex.split(command)

        # Fix 3: validate workdir
        try:
            resolved_workdir = _validate_workdir(workdir)
        except ValueError as exc:
            return SFWResult(
                success=False,
                command=command,
                stdout="",
                stderr=str(exc),
                exit_code=1,
            )

        args = [self._sfw_path]
        if verbose:
            args.append("--verbose")
        args.extend(cmd_parts)

        logger.debug("sfw run: %s", " ".join(args))  # Fix 9: logger.debug

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self._config.timeout,
                cwd=resolved_workdir,
            )
            stdout = _sanitize_output(proc.stdout)  # Fix 4
            stderr = _sanitize_output(proc.stderr)  # Fix 4
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            return SFWResult(
                success=False,
                command=command,
                stdout="",
                stderr=f"Command timed out after {self._config.timeout}s",
                exit_code=-1,
            )
        except OSError as exc:
            return SFWResult(
                success=False,
                command=command,
                stdout="",
                stderr=_sanitize_oserror(exc),  # Fix 7
                exit_code=-1,
            )

        # Parse output for blocked/installed packages
        blocked, installed = self._parse_output(stdout + stderr)

        return SFWResult(
            success=exit_code == 0,
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            blocked=blocked,
            installed=installed,
        )

    @staticmethod
    def _parse_output(output: str) -> tuple[list[str], list[str]]:
        """Parse sfw output for blocked and installed packages.

        Uses exact word-boundary matching to avoid false positives from
        substrings like 'blocked' inside package names.

        Handles formats like:
            🔴 blocked malicious-pkg
            blocked: evil-trojan
            🟢 installed express
            added 5 packages

        Returns:
            Tuple of (blocked_packages, installed_packages).
        """
        blocked: list[str] = []
        installed: list[str] = []

        for line in output.splitlines():
            parts = line.split()
            if not parts:
                continue

            # Find the keyword token, then take the first non-keyword token after it
            for i, part in enumerate(parts):
                token = part.lower().strip(",:;")
                if token in _ALL_KEYWORDS and i + 1 < len(parts):
                    # Walk past any additional keyword tokens (e.g. 🔴 blocked)
                    j = i + 1
                    while j < len(parts) and parts[j].lower().strip(",:;") in _ALL_KEYWORDS:
                        j += 1
                    if j < len(parts):
                        if token in _BLOCKED_KEYWORDS:
                            blocked.append(parts[j])
                        else:
                            installed.append(parts[j])
                    break

        # Fix 6: deduplicate results while preserving order
        blocked = list(dict.fromkeys(blocked))
        installed = list(dict.fromkeys(installed))

        return blocked, installed
