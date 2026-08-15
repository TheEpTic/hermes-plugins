"""SFWManager — wraps the sfw CLI for safe dependency installation."""

from __future__ import annotations

import errno
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_BLOCKED_KEYWORDS = frozenset({"blocked", "🚫", "🔴"})
_INSTALLED_KEYWORDS = frozenset({"installed", "🟢", "added"})
_ALL_KEYWORDS = _BLOCKED_KEYWORDS | _INSTALLED_KEYWORDS
_MAX_LIST_ENTRIES = 50

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Dependency-operation grammar
# ---------------------------------------------------------------------------
# The command is passed to sfw as an argv list, never through a shell. Still,
# allowing a package-manager binary alone is not enough: several managers have
# subcommands that execute arbitrary programs. Require the command verb in its
# canonical position, before any options. This deliberately rejects convenient
# global-option forms such as ``npm --prefix x run`` rather than trying to
# parse every manager's option grammar and missing a runner behind an argument.
_ALLOWED_COMMAND_PREFIXES: dict[str, frozenset[tuple[str, ...]]] = {
    "npm": frozenset({("install",), ("uninstall",), ("update",), ("ci",), ("dedupe",)}),
    "yarn": frozenset({("add",), ("remove",), ("install",), ("upgrade",), ("up",)}),
    "pnpm": frozenset({("add",), ("remove",), ("install",), ("update",), ("up",)}),
    "pip": frozenset({("install",), ("uninstall",), ("download",)}),
    "pip3": frozenset({("install",), ("uninstall",), ("download",)}),
    "uv": frozenset(
        {
            ("add",),
            ("remove",),
            ("sync",),
            ("lock",),
            ("export",),
            ("pip", "install"),
            ("pip", "uninstall"),
            ("pip", "compile"),
            ("pip", "sync"),
        }
    ),
    "cargo": frozenset(
        {
            ("add",),
            ("remove",),
            ("fetch",),
            ("update",),
            ("install",),
            ("uninstall",),
            ("vendor",),
        }
    ),
}

# ---------------------------------------------------------------------------
# Command maxLength (server-side enforcement)
# ---------------------------------------------------------------------------

_MAX_COMMAND_LENGTH = 1024

# ---------------------------------------------------------------------------
# Binary discovery (SFW-2 / SFW-5)
# ---------------------------------------------------------------------------
# pnpm-style wrapper shims exec a real JS entry point (a cmd-shim or shell
# shim). The shim layer must be reported separately from the binary version.
_IS_NPM_SHIM_RE = re.compile(r"(?:^|/)(?:pnpm|npm-global)/")
# npm's cmd-shim writes the real target into the shim in two forms:
#   exec node "$basedir/../global/.../node_modules/sfw/dist/sfw.mjs" "$@"
#   # cmd-shim-target=/absolute/path/to/sfw.mjs
# The cmd-shim-target marker is the canonical absolute form and is preferred.
_CMD_SHIM_TARGET_RE = re.compile(r"cmd-shim-target=(\S+)")
_SHIM_EXEC_TARGET_RE = re.compile(r'exec\s+(?:\S+\s+)?["\']?([^\s"\']+?\.mjs)["\']?\s+"\$@"')

# Common npm/pnpm shim and binary locations, walked in order. A shim is only
# usable if its real target resolves; broken shims are skipped and reported.
_KNOWN_BINARY_CANDIDATES = (
    ".local/share/pnpm/sfw",
    ".local/share/pnpm/bin/sfw",
    ".local/bin/sfw",
    ".npm-global/bin/sfw",
    ".cargo/bin/sfw",
    "/usr/local/bin/sfw",
)

# ---------------------------------------------------------------------------
# OSError errno → generic message mapping
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


@dataclass(frozen=True)
class SFWBinaryInfo:
    """Where the sfw binary was found and which layer it lives in.

    ``binary_kind`` distinguishes npm-installed wrapper shims (``npm-shim``,
    a tiny launcher that execs a real JS entry point) from genuine binaries,
    so an npm-package/binary version mismatch is not confusing. ``target`` is
    the resolved real entry point for shims and None for real binaries.
    """

    binary: str | None
    binary_kind: str | None
    target: str | None


@dataclass(frozen=True)
class SFWDiagnosis:
    """Structured result of :meth:`SFWManager.diagnose`.

    ``healthy`` is True only when a binary was found and its version query
    succeeded. ``why`` is a human-readable explanation of exactly what is
    broken when the install is not healthy. ``checked`` lists every candidate
    location that was walked during discovery.
    """

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


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _validate_command(command: str) -> str | None:
    """Check that the command starts with an allowed prefix.

    Returns an error message if the command is disallowed, or None if OK.
    Also handles shlex.split() ValueError.
    """
    # Reject null bytes and control characters
    if "\x00" in command:
        return "Command contains null bytes"

    # Server-side maxLength enforcement
    if len(command) > _MAX_COMMAND_LENGTH:
        return f"Command too long ({len(command)} chars, max {_MAX_COMMAND_LENGTH})"

    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return f"Command parse error: {exc}"

    if not parts:
        return "Command is empty"

    program = Path(parts[0]).name

    # Reject path separators in the first token — prevents /tmp/evil/pip bypass
    if os.sep in parts[0] or (os.altsep and os.altsep in parts[0]):
        return (
            "Command must not contain path separators. "
            "Use bare command name (e.g. 'pip install foo', not '/usr/bin/pip install foo')"
        )

    if program not in _ALLOWED_COMMAND_PREFIXES:
        return (
            f"Command prefix '{program}' is not allowed. "
            f"Allowed: {', '.join(sorted(_ALLOWED_COMMAND_PREFIXES))}"
        )

    command_parts = parts[1:]
    allowed_prefixes = _ALLOWED_COMMAND_PREFIXES[program]
    if not any(tuple(command_parts[: len(prefix)]) == prefix for prefix in allowed_prefixes):
        allowed = ", ".join(" ".join(prefix) for prefix in sorted(allowed_prefixes))
        return (
            f"Command is not allowed for '{program}': it is not a dependency operation. "
            f"Allowed forms: {allowed}"
        )

    return None


def is_dependency_operation(command: str) -> bool:
    """Return True only for an operation the sfw tool itself would accept."""
    return isinstance(command, str) and _validate_command(command) is None


def _validate_workdir(workdir: str | None) -> str | None:
    """Resolve and validate the working directory.

    Returns the resolved real path if valid, or None if workdir is not set.
    Raises ValueError with a message if the path is invalid.
    """
    if workdir is None:
        return None

    try:
        resolved = str(Path(workdir).expanduser().resolve())
    except (ValueError, RuntimeError) as exc:
        raise ValueError(f"Invalid working directory: {workdir}") from exc

    if not Path(resolved).exists():
        raise ValueError(f"Working directory does not exist: {workdir}")
    if not Path(resolved).is_dir():
        raise ValueError(f"Working directory is not a directory: {workdir}")
    return resolved


def _sanitize_output(text: str, max_len: int = 10_000) -> str:
    """Truncate long output and add a note about total size."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [output truncated, total {len(text)} chars]"


def _sanitize_oserror(exc: OSError) -> str:
    """Map common errno values to generic messages."""
    errnum = getattr(exc, "errno", None)
    if errnum is not None and errnum in _ERRNO_MESSAGES:
        return _ERRNO_MESSAGES[errnum]
    return "An internal error occurred"


def _truncate_list(items: list[str], limit: int = _MAX_LIST_ENTRIES) -> list[str]:
    """Cap a list to prevent context flooding."""
    if len(items) <= limit:
        return items
    return items[:limit] + [f"... and {len(items) - limit} more"]


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_ESCAPE_RE.sub("", text)


class SFWManager:
    """Manages sfw CLI execution."""

    def __init__(self, config: SFWConfig | None = None) -> None:
        self._config = config or SFWConfig()

    def _classify_binary(self, binary: str) -> SFWBinaryInfo:
        """Classify a resolved binary path by layer.

        Returns which layer the binary lives in (``npm-shim`` for pnpm/npm
        wrapper shims, ``binary`` for real executables) and, for shims, the
        resolved real target entry point.
        """
        target = self._resolve_shim_target(binary)
        if target is not None:
            return SFWBinaryInfo(binary=binary, binary_kind="npm-shim", target=target)
        return SFWBinaryInfo(binary=binary, binary_kind="binary", target=None)

    @staticmethod
    def _resolve_shim_target(binary: str) -> str | None:
        """Resolve the real target of an npm-style shim, or None if not one.

        Reads the shim script and extracts the real entry point it execs,
        preferring the canonical ``cmd-shim-target`` marker over the
        ``exec ... sfw.mjs`` line. Symlinks are resolved first so a link into
        a pnpm shim (e.g. ``/usr/local/bin/sfw`` -> pnpm shim) is still
        detected. Returns None for real binaries or shims whose target cannot
        be determined.
        """
        binary_path = Path(binary)
        try:
            resolved = str(binary_path.resolve())
        except (OSError, RuntimeError):
            resolved = binary
        if not _IS_NPM_SHIM_RE.search(resolved):
            return None
        try:
            content = binary_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        marker = _CMD_SHIM_TARGET_RE.search(content)
        if marker is not None:
            return str(Path(marker.group(1)).expanduser())
        exec_match = _SHIM_EXEC_TARGET_RE.search(content)
        if exec_match is None:
            return None
        raw = exec_match.group(1)
        # The exec form is written relative to the shim's own directory and
        # may reference $basedir (resolved by the shim at runtime).
        if raw.startswith("$basedir/"):
            raw = str(binary_path.parent / raw[len("$basedir/") :])
        target_path = Path(raw)
        if not target_path.is_absolute():
            target_path = binary_path.parent / target_path
        return str(target_path.expanduser())

    def _find_sfw(self) -> str | None:
        """Locate the sfw binary.

        Discovery is intentionally performed on demand instead of being cached
        during manager construction. The plugin manager can outlive changes to
        the process environment, and sfw may be installed after registration.
        """
        # If config points to a specific binary, use it directly.
        if self._config.sfw_bin != "sfw":
            if Path(self._config.sfw_bin).exists():
                return self._config.sfw_bin
            return None

        # Default: search PATH and common locations.
        path = shutil.which(self._config.sfw_bin)
        if path:
            return path
        # Check common locations. A candidate that exists but is a wrapper
        # shim whose real target is missing is skipped, like the one that
        # broke a machine even though ``npm ci`` succeeded.
        for candidate in self._known_candidates():
            if candidate.exists() and os.access(candidate, os.X_OK):
                target = self._resolve_shim_target(str(candidate))
                if target is None or Path(target).exists():
                    return str(candidate)
        return None

    def _known_candidates(self) -> list[Path]:
        """Known shim/binary locations, newest home-aware first."""
        home = Path.home()
        candidates = [home / rel for rel in _KNOWN_BINARY_CANDIDATES]
        return candidates

    def is_installed(self) -> bool:
        """Check if sfw is available."""
        return self._find_sfw() is not None

    @property
    def sfw_path(self) -> str | None:
        """Return the current path to the sfw binary."""
        return self._find_sfw()

    def get_version(self) -> str | None:
        """Get the sfw binary version string, or None if unavailable."""
        version = self.get_version_info()["version"]
        assert version is None or isinstance(version, str)
        return version

    def get_version_info(self) -> dict[str, Any]:
        """Report the binary version together with the layer it came from.

        The npm package version (e.g. 2.0.6) and the binary version (e.g.
        1.15.0) can differ, so the layer must be explicit: ``binary_kind``
        says whether the resolved path is an npm/pnpm wrapper shim or a real
        binary, and ``target`` names the real entry point for shims.

        Returns:
            Dict with keys ``version``, ``binary``, ``binary_kind`` and
            ``target`` (all None when sfw is not installed).
        """
        sfw_path = self.sfw_path
        if not sfw_path:
            return {"version": None, "binary": None, "binary_kind": None, "target": None}
        info = self._classify_binary(sfw_path)
        try:
            proc = subprocess.run(
                [sfw_path, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace").strip()
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            version = stdout or stderr or None
        except (subprocess.TimeoutExpired, OSError):
            version = None
        return {
            "version": version,
            "binary": info.binary,
            "binary_kind": info.binary_kind,
            "target": info.target,
        }

    def diagnose(self) -> SFWDiagnosis:
        """Self-diagnose the sfw install.

        Walks every known shim/cache location, the PATH lookup and the
        configured ``sfw_bin`` override and reports exactly what is broken:
        no binary found anywhere, a shim that exists but points at a missing
        target, or a binary whose version query fails.

        Returns:
            An :class:`SFWDiagnosis` with ``healthy``, the resolved
            ``binary``/``binary_kind``/``target``, the queried ``version``, a
            human-readable ``why``, the list of ``checked`` locations and any
            discovery ``errors``.
        """
        checked: list[str] = []
        errors: list[str] = []

        # 1. Configured override.
        if self._config.sfw_bin != "sfw":
            override = Path(self._config.sfw_bin)
            checked.append(str(override))
            if not override.exists():
                return SFWDiagnosis(
                    healthy=False,
                    binary=None,
                    binary_kind=None,
                    version=None,
                    why=(
                        f"configured sfw_bin override does not exist: {override}. "
                        "Reinstall with: npm i -g sfw"
                    ),
                    checked=checked,
                    errors=errors,
                )
            info = self._classify_binary(str(override))
            version = self.get_version()
            if version is None:
                return SFWDiagnosis(
                    healthy=False,
                    binary=str(override),
                    binary_kind=info.binary_kind,
                    version=None,
                    why="the binary exists but its --version query failed",
                    target=info.target,
                    checked=checked,
                    errors=errors,
                )
            return SFWDiagnosis(
                healthy=True,
                binary=str(override),
                binary_kind=info.binary_kind,
                version=version,
                why="ok",
                target=info.target,
                checked=checked,
                errors=errors,
            )

        # 2. PATH lookup.
        path = shutil.which(self._config.sfw_bin)
        if path:
            checked.append(path)
            info = self._classify_binary(path)
            version = self.get_version()
            if version is None:
                return SFWDiagnosis(
                    healthy=False,
                    binary=path,
                    binary_kind=info.binary_kind,
                    version=None,
                    why="the binary was found on PATH but its --version query failed",
                    target=info.target,
                    checked=checked,
                    errors=errors,
                )
            return SFWDiagnosis(
                healthy=True,
                binary=path,
                binary_kind=info.binary_kind,
                version=version,
                why="ok",
                target=info.target,
                checked=checked,
                errors=errors,
            )

        # 3. Known shim/cache locations.
        for candidate in self._known_candidates():
            checked.append(str(candidate))
            if not candidate.exists():
                continue
            if not os.access(candidate, os.X_OK):
                errors.append(f"{candidate} exists but is not executable")
                continue
            target = self._resolve_shim_target(str(candidate))
            if target is not None and not Path(target).exists():
                # Shim exists but its real target is missing -> broken.
                return SFWDiagnosis(
                    healthy=False,
                    binary=str(candidate),
                    binary_kind="npm-shim",
                    version=None,
                    why=(
                        f"shim {candidate} points at missing target {target}. "
                        "Reinstall with: npm i -g sfw"
                    ),
                    target=target,
                    checked=checked,
                    errors=errors,
                )
            # Candidate found; verify the version query works.
            info = self._classify_binary(str(candidate))
            version = self.get_version()
            if version is None:
                return SFWDiagnosis(
                    healthy=False,
                    binary=str(candidate),
                    binary_kind=info.binary_kind,
                    version=None,
                    why=f"binary {candidate} exists but its --version query failed",
                    target=info.target,
                    checked=checked,
                    errors=errors,
                )
            return SFWDiagnosis(
                healthy=True,
                binary=str(candidate),
                binary_kind=info.binary_kind,
                version=version,
                why="ok",
                target=info.target,
                checked=checked,
                errors=errors,
            )

        # 4. Nothing found anywhere.
        return SFWDiagnosis(
            healthy=False,
            binary=None,
            binary_kind=None,
            version=None,
            why=(
                "sfw binary not found: checked PATH and "
                + ", ".join(checked)
                + ". Install with: npm i -g sfw"
            ),
            checked=checked,
            errors=errors,
        )

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
        sfw_path = self.sfw_path
        if not sfw_path:
            return SFWResult(
                success=False,
                command=command,
                stdout="",
                stderr="sfw is not installed. Install with: npm i -g sfw",
                exit_code=1,
            )

        # Validate command prefix allowlist and handle parse errors.
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

        # Validate workdir.
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

        args = [sfw_path]
        if verbose:
            args.append("--verbose")
        args.extend(cmd_parts)

        logger.debug("sfw run: %s", " ".join(args))

        # Use Popen with start_new_session so we can kill the process group on timeout
        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=resolved_workdir,
                start_new_session=True,
            )
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=self._config.timeout)
                stdout = _sanitize_output(stdout_bytes.decode("utf-8", errors="replace"))
                stderr = _sanitize_output(stderr_bytes.decode("utf-8", errors="replace"))
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                # Kill entire process group (sfw + child package manager processes)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                # Grace period for SIGTERM, then force-kill with SIGKILL
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        proc.kill()
                    proc.wait()
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
                stderr=_sanitize_oserror(exc),
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
            # Strip ANSI escape sequences before parsing
            clean_line = _strip_ansi(line)
            parts = clean_line.split()
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

        # Deduplicate results while preserving order, then cap list size
        blocked = _truncate_list(list(dict.fromkeys(blocked)))
        installed = _truncate_list(list(dict.fromkeys(installed)))

        return blocked, installed
