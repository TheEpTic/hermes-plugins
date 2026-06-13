"""Tool schemas — what the LLM sees."""

from __future__ import annotations

SFW_TOOL_SCHEMA = {
    "name": "sfw",
    "description": (
        "Run package manager commands through Socket Firewall Free (sfw). "
        "sfw wraps your package manager and blocks malicious packages at install time. "
        "No API key or config needed. "
        "Supports: npm, yarn, pnpm (JS/TS), pip, uv (Python), cargo (Rust). "
        "Works for install, uninstall, add, remove, update, and any other package manager command."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run", "status"],
                "description": (
                    "'run' — execute a package manager command through sfw. "
                    "'status' — check if sfw is installed and get version."
                ),
            },
            "command": {
                "type": "string",
                "maxLength": 1024,
                "description": (
                    "Package manager command to run through sfw. "
                    "Must start with: npm, yarn, pnpm, pip, pip3, uv, cargo, rustup. "
                    "Any command the underlying package manager supports works here. "
                    "Examples: "
                    "'npm install express', 'npm uninstall lodash', "
                    "'yarn add @types/node', 'yarn remove debug', "
                    "'pnpm add -D vitest', 'pnpm update', "
                    "'pip install requests', 'pip uninstall flask', "
                    "'uv pip install flask', 'uv pip install -r requirements.txt', "
                    "'cargo add serde', 'cargo fetch', 'cargo update'."
                ),
            },
            "workdir": {
                "type": "string",
                "description": "Working directory for the command (default: current directory). Must be a valid existing directory.",
            },
            "verbose": {
                "type": "boolean",
                "description": "Enable verbose output (default: false).",
            },
        },
        "required": ["action"],
    },
}
