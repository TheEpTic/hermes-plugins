"""SSH tool schemas — what the LLM sees."""

from __future__ import annotations

from typing import Any

_S = {"type": "string"}
_BOOL = {"type": "boolean"}


def _timeout(default: int, maximum: int, verb: str) -> dict[str, Any]:
    return {
        "type": "integer",
        "description": f"Seconds before {verb} (default: {default})",
        "default": default,
        "minimum": 1,
        "maximum": maximum,
    }


def _flag(description: str) -> dict[str, Any]:
    return {**_BOOL, "description": description, "default": False}


def _int(description: str, default: int, maximum: int | None = None) -> dict[str, Any]:
    field: dict[str, Any] = {"type": "integer", "description": description, "default": default}
    if maximum is not None:
        field["minimum"] = 1
        field["maximum"] = maximum
    return field


def _tool(
    name: str, description: str, props: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": props, "required": required},
    }


SSH_TERMINAL_SCHEMA = _tool(
    "ssh_terminal",
    "Run a command on a remote machine via SSH. Uses the machine registry — add machines first with ssh_machines. Background commands return a session_id; poll or read output with ssh_sessions.",
    {
        "machine": {**_S, "description": "Machine name or alias (e.g. 'myserver', 'web1')"},
        "command": {**_S, "description": "Command to run on the remote machine"},
        "timeout": _timeout(30, 600, "killing the command"),
        "new_session": _flag("Force a new connection instead of reusing existing (default: false)"),
        "background": _flag(
            "Run in background and return immediately. Use ssh_sessions to poll/read output."
        ),
        "max_output_chars": _int(
            "Max output characters to return. Truncated if exceeded (default: 50000, max: 500000).",
            50000,
            500000,
        ),
    },
    ["machine", "command"],
)

SSH_TRANSFER_SCHEMA = _tool(
    "ssh_transfer",
    (
        "Upload or download a file or directory using a registered SSH machine and OpenSSH "
        "SFTP. Transfers default to no overwrite. Credential paths and symbolic links are blocked."
    ),
    {
        "action": {
            "type": "string",
            "enum": ["upload", "download"],
            "description": "Transfer direction from the Hermes host's perspective",
        },
        "machine": {**_S, "description": "Machine name or alias"},
        "source": {
            **_S,
            "description": (
                "Upload: local source path. Download: absolute remote source path or a path "
                "starting with '~/'."
            ),
        },
        "destination": {
            **_S,
            "description": (
                "Upload: absolute remote destination path or a path starting with '~/'. "
                "Download: local destination path."
            ),
        },
        "recursive": _flag("Required for directory transfers (default: false)"),
        "preserve": _flag("Preserve file times and modes where supported (default: false)"),
        "overwrite": _flag("Allow replacing an existing regular file (default: false)"),
        "timeout": _timeout(300, 3600, "cancelling the transfer"),
    },
    ["action", "machine", "source", "destination"],
)

SSH_MACHINES_SCHEMA = _tool(
    "ssh_machines",
    "Manage the SSH machine registry. Add, remove, list, test, or inspect machines.",
    {
        "action": {
            "type": "string",
            "enum": ["list", "add", "remove", "inspect", "test"],
            "description": "Action to perform",
        },
        "name": {**_S, "description": "Machine name (required for add/remove/inspect/test)"},
        "host": {**_S, "description": "IP or hostname (required for add)"},
        "user": {
            **_S,
            "description": "SSH username (defaults to the current local user when omitted)",
        },
        "port": _int("SSH port (default: 22)", 22),
        "key": {
            **_S,
            "description": "Path to SSH key (e.g. '~/.ssh/id_ed25519')",
            "default": "",
        },
        "aliases": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short aliases for this machine",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tags for organization",
        },
        "description": {**_S, "description": "Human-readable description", "default": ""},
    },
    ["action"],
)

SSH_SESSIONS_SCHEMA = _tool(
    "ssh_sessions",
    "Manage active SSH sessions. List, kill, cleanup idle, poll, or read output from background commands.",
    {
        "action": {
            "type": "string",
            "enum": ["list", "kill", "cleanup", "poll", "read_output"],
            "description": "Action to perform",
        },
        "session_id": {
            **_S,
            "description": "Session ID (required for kill, poll, read_output)",
        },
        "max_idle_minutes": _int(
            "Max idle minutes before auto-kill (for cleanup, default: 30)", 30
        ),
    },
    ["action"],
)
