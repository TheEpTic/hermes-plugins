"""Detection of package-manager invocations inside shell commands.

The terminal hook must not let a package-manager call escape through a
shell prefix (for example ``cd app && npm install``, ``sudo npm install``,
or ``bash -c 'npm install'``). This is deliberately a conservative
detector: false positives fail closed, while commands that do not invoke a
package manager are left alone.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from .validate import _ALLOWED_COMMAND_PREFIXES

_PACKAGE_MANAGER_WRAPPERS = frozenset(
    {"command", "env", "exec", "nice", "nohup", "sudo", "timeout"}
)
_SHELL_WRAPPERS = frozenset({"bash", "dash", "fish", "ksh", "sh", "zsh"})
_SHELL_COMMAND_BOUNDARIES = frozenset({";", "&&", "||", "|", "&", "(", ")"})


def _shell_command_tokens(command: str) -> list[str]:
    """Tokenize shell syntax enough to identify nested command segments."""
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    return list(lexer)


def _segment_start_state(name: str) -> tuple[bool, str | None]:
    if name in _ALLOWED_COMMAND_PREFIXES:
        return True, None
    wrapper_mode = "shell" if name in _SHELL_WRAPPERS else None
    return False, "generic" if name in _PACKAGE_MANAGER_WRAPPERS else wrapper_mode


def _wrapped_command_state(name: str, wrapper_mode: str | None) -> tuple[bool, str | None]:
    if wrapper_mode != "generic":
        return wrapper_mode == "shell" and contains_package_manager_command(name), wrapper_mode
    if name in _ALLOWED_COMMAND_PREFIXES:
        return True, wrapper_mode
    return False, "shell" if name in _SHELL_WRAPPERS else wrapper_mode


def contains_package_manager_command(command: str) -> bool:
    """Return True when a shell command segment invokes a supported manager."""
    if not isinstance(command, str) or not command.strip():
        return False

    try:
        parts = _shell_command_tokens(command)
    except ValueError:
        # A malformed command beginning with a known manager still needs to be
        # stopped before the terminal backend gets a chance to interpret it.
        return bool(
            re.search(
                r"(?<![\w.-])(?:npm|yarn|pnpm|pip3?|uv|cargo)(?=\s|$)",
                command,
            )
        )

    segment_start = True
    wrapper_mode: str | None = None
    for token in parts:
        if token in _SHELL_COMMAND_BOUNDARIES:
            segment_start = True
            wrapper_mode = None
            continue

        name = Path(token).name
        if segment_start:
            matched, wrapper_mode = _segment_start_state(name)
            segment_start = False
        else:
            matched, wrapper_mode = _wrapped_command_state(name, wrapper_mode)
        if matched:
            return True

    return False
