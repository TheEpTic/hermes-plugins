"""Grammar + validation for sfw-bound dependency commands and workdirs."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

_MAX_COMMAND_LENGTH = 1024

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

# Flags whose value is a local *manifest* (requirements file, find-links dir)
# rather than an install source. The value itself is not executed; the
# dependency manager reads it to resolve registry packages.
_PATH_VALUE_FLAGS = frozenset({"-r", "--requirement", "-f", "--find-links"})

# System directories a dependency install must never run in. Installing into
# /etc, /usr, or / is almost certainly a mistake or an attack; the resolved
# workdir is checked against these before any process starts.
_WORKDIR_DENIED_PREFIXES = tuple(
    Path(path) for path in ("/boot", "/dev", "/etc", "/proc", "/sys", "/usr")
)


def _reject_local_or_git_source(parts: list[str]) -> str | None:
    """Reject installs whose source is local code or a git/file URL.

    ``cargo install --path``, ``pip install .``, ``npm install ./local`` and
    ``pip install git+https://...`` all execute build/lifecycle scripts from a
    source sfw never sees on the dependency network — the local filesystem or
    a git clone. Those forms are refused; registry installs are unaffected.
    """
    for i, token in enumerate(parts[1:], start=1):
        lowered = token.lower()
        if (
            lowered == "--path"
            or lowered.startswith("--path=")
            or lowered == "--git"
            or lowered.startswith("--git=")
        ):
            return (
                "local/git install sources are not allowed: "
                f"{token!r}. Install from a registry instead."
            )
        if token == ".":
            return (
                "installing the current directory ('.') runs arbitrary local "
                "build scripts; install from a registry instead."
            )
        previous = parts[i - 1].lower() if i > 0 else ""
        is_manifest_value = previous in _PATH_VALUE_FLAGS
        is_relative_source = token.startswith("./") or token.startswith("../")
        if is_relative_source and not is_manifest_value:
            return (
                "local path install sources are not allowed: "
                f"{token!r}. Install from a registry instead."
            )
        if "git+" in lowered or lowered.startswith("file:"):
            return (
                "git/file install sources are not allowed: "
                f"{token!r}. Install from a registry instead."
            )
    return None


def validate_command(command: str) -> str | None:
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

    return _reject_local_or_git_source(parts)


def is_dependency_operation(command: str) -> bool:
    """Return True only for an operation the sfw tool itself would accept."""
    return isinstance(command, str) and validate_command(command) is None


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def validate_workdir(workdir: str | None) -> str | None:
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
    resolved_path = Path(resolved)
    if resolved_path == Path("/") or any(
        _within(resolved_path, prefix) for prefix in _WORKDIR_DENIED_PREFIXES
    ):
        raise ValueError(
            "Working directory must not be a system directory "
            "(/, /boot, /dev, /etc, /proc, /sys, /usr)"
        )
    return resolved
