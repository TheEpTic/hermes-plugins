# hermes-sfw

[![CI](https://github.com/TheEpTic/hermes-plugins/actions/workflows/ci.yml/badge.svg)](https://github.com/TheEpTic/hermes-plugins/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Socket Firewall Free plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Block known malicious dependencies during supported dependency operations. Route those operations through `sfw` for automatic protection — no API key, no config.

```text
sfw action=run command="npm install express"
sfw action=status
```

## quick start

> Requires Python 3.11+, Hermes Agent, and the Socket Firewall Free `sfw` CLI.

Install the prerequisite and the plugin:

```bash
npm i -g sfw
python -m pip install hermes-sfw
hermes plugins enable hermes-sfw --no-allow-tool-override
```

Run `/reset` or restart Hermes, then verify without installing a throwaway dependency:

```bash
sfw --version
python -m pip show hermes-sfw
hermes plugins list --enabled --plain
```

Inside Hermes:

```text
sfw action=status
```

If Hermes cannot see the package, install it with the Python environment that owns the `hermes` executable. See [AGENTS.md](../AGENTS.md).

For source development:

```bash
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins/hermes-sfw
./deploy.sh
hermes plugins enable hermes-sfw --no-allow-tool-override
```

Run `/reset` or restart Hermes after changing the source tree.

## Features

### `sfw run` — Execute Commands

Run supported dependency operations through sfw. Known malicious packages are blocked automatically.

```text
# Install a package
sfw action=run command="npm install express"

# Uninstall
sfw action=run command="npm uninstall lodash"

# Python packages
sfw action=run command="pip install flask"
sfw action=run command="uv pip install -r requirements.txt"

# Rust crates
sfw action=run command="cargo add serde"

# With verbose output
sfw action=run command="pnpm add -D vitest" verbose=true

# In a specific directory
sfw action=run command="npm install" workdir="/path/to/project"
```

**Supported package managers:** npm, yarn, and pnpm for JavaScript/TypeScript; pip, pip3, and uv for Python; cargo for Rust. `npx`, `rustup`, and runner-style subcommands are intentionally blocked because they can execute arbitrary programs.

**Blocked packages:** When sfw detects a malicious package, the install is blocked and the package name is returned in the response. Non-package-manager commands (like `cat`, `rm`, `curl`) are rejected by the prefix allowlist.

**Output truncation:** Output exceeding 10,000 characters is intentionally truncated with a size note. The discarded suffix is not returned in another field.

### automatic terminal guard

When enabled, the plugin watches Hermes `terminal` calls. A supported dependency operation such as `npm install`, `uv pip install`, or `cargo fetch` is blocked before raw execution and the agent is directed to use the `sfw` tool instead.

Set `HERMES_SFW_ENFORCE_DIRECT=off` before starting Hermes only when you deliberately want direct terminal dependency operations.

### `sfw status` — Check Installation

Verify sfw is installed and get the version.

```text
sfw action=status
```

Returns: `installed` (bool), `version` (string), `binary` (path).

## How It Works

hermes-sfw is a thin wrapper around the [sfw CLI](https://github.com/SocketDev/sfw-free). It:

1. Validates the command starts with an allowed package manager prefix
2. Resolves and validates the working directory (if specified)
3. Executes the command through `sfw` with timeout protection
4. Parses stdout/stderr for blocked and installed package indicators
5. Returns structured JSON with success status, output, and parsed results

## Configuration

All settings live in `src/hermes_sfw/manager.py` as an `SFWConfig` dataclass:

| Setting | Default | Description |
|---------|---------|-------------|
| `sfw_bin` | `sfw` | Path to the sfw binary |
| `timeout` | 300s | Max seconds per command |

## Architecture

```
src/hermes_sfw/
├── __init__.py          # Plugin registration + Hermes hooks
├── manager.py           # SFWManager — command execution + output parsing
├── schemas.py           # Tool schema (what the LLM sees)
├── utils.py             # ok(), err(), require() helpers
├── py.typed             # PEP 561 marker
└── handlers/
    ├── __init__.py
    └── sfw.py           # sfw tool handler
```

**Key design decisions:**

- `SFWManager` owns all state. No module-level mutable state.
- Command prefix allowlist prevents arbitrary command execution through sfw.
- `shlex.split()` parsing with error handling catches malformed commands early.
- Output sanitization truncates long outputs to prevent context overflow.
- `OSError` errno mapping provides clean error messages without leaking internals.

## Security

See [SECURITY.md](SECURITY.md) for the full picture.

**Defaults you should know about:**

- Only package manager commands are allowed (prefix allowlist: npm, yarn, pnpm, pip, cargo, etc.)
- Non-package-manager commands (`cat`, `rm`, `curl`, etc.) are rejected
- Commands run with the permissions of the Hermes agent process

**Hardening applied:**

- Command prefix validation via allowlist before execution
- Commands are passed as an argument vector without invoking a shell
- `shlex.split()` handles quoting and rejects malformed command strings early
- Working directories are expanded, resolved, and checked to be existing directories
- Output truncated at 10K chars to prevent context overflow
- Timeout protection prevents hanging installs

## Requirements

- Python 3.11+
- [sfw CLI](https://github.com/SocketDev/sfw-free) installed on PATH
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)

## Troubleshooting

**"sfw is not installed"**
Install sfw globally: `npm i -g sfw`. The plugin searches PATH and common locations (`~/.local/share/pnpm/bin/`, `/usr/local/bin/`, `~/.npm-global/bin/`).

**Command rejected with "not allowed"**
Only the documented dependency operations are allowed. Runner-style commands and unsupported subcommands are intentionally rejected; use the regular Hermes terminal only when you deliberately do not want SFW protection.

**Command timeout**
Default timeout is 5 minutes (300s). For very large installs, this may not be enough. Override via `SFWConfig(timeout=...)` when creating the manager.

**Output looks truncated**
This is intentional. Outputs over 10K characters are truncated to protect context, and the discarded suffix is not retained by the plugin.

## Development

```bash
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins/hermes-sfw
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Run checks
black --check src/hermes_sfw/ tests/
mypy src/hermes_sfw/
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT — see [LICENSE](LICENSE).
