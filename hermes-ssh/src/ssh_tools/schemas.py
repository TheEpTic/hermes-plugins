"""SSH tool schemas — what the LLM sees."""

SSH_TERMINAL_SCHEMA = {
    "name": "ssh_terminal",
    "description": "Run a command on a remote machine via SSH. Uses the machine registry — add machines first with ssh_machines.",
    "parameters": {
        "type": "object",
        "properties": {
            "machine": {
                "type": "string",
                "description": "Machine name or alias (e.g. 'myserver', 'web1')",
            },
            "command": {
                "type": "string",
                "description": "Command to run on the remote machine",
            },
            "timeout": {
                "type": "integer",
                "description": "Seconds before killing the command (default: 30)",
                "default": 30,
            },
            "new_session": {
                "type": "boolean",
                "description": "Force a new connection instead of reusing existing (default: false)",
                "default": False,
            },
            "background": {
                "type": "boolean",
                "description": "Run in background and return immediately. Use ssh_sessions to poll/read output.",
                "default": False,
            },
            "max_output_chars": {
                "type": "integer",
                "description": "Max output characters to return. Truncated if exceeded (default: 50000).",
                "default": 50000,
            },
            "poll": {
                "type": "string",
                "description": "Session ID of a background command to poll for status. Returns running (bool), stdout, stderr, exit_code.",
            },
            "read_output": {
                "type": "string",
                "description": "Session ID of a completed background command to read full stdout/stderr from.",
            },
        },
        "required": ["machine", "command"],
    },
}

SSH_MACHINES_SCHEMA = {
    "name": "ssh_machines",
    "description": "Manage the SSH machine registry. Add, remove, list, test, or inspect machines.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "remove", "inspect", "test"],
                "description": "Action to perform",
            },
            "name": {
                "type": "string",
                "description": "Machine name (required for add/remove/inspect/test)",
            },
            "host": {
                "type": "string",
                "description": "IP or hostname (required for add)",
            },
            "user": {
                "type": "string",
                "description": "SSH username (default: root)",
                "default": "root",
            },
            "port": {
                "type": "integer",
                "description": "SSH port (default: 22)",
                "default": 22,
            },
            "key": {
                "type": "string",
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
            "description": {
                "type": "string",
                "description": "Human-readable description",
                "default": "",
            },
        },
        "required": ["action"],
    },
}

SSH_SESSIONS_SCHEMA = {
    "name": "ssh_sessions",
    "description": "Manage active SSH sessions. List, kill, cleanup, poll, or read output.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "kill", "cleanup", "prune", "poll", "read_output"],
                "description": "Action to perform",
            },
            "session_id": {
                "type": "string",
                "description": "Session ID (required for kill, poll, read_output)",
            },
            "max_idle_minutes": {
                "type": "integer",
                "description": "Max idle minutes before auto-kill (for cleanup, default: 30)",
                "default": 30,
            },
        },
        "required": ["action"],
    },
}
