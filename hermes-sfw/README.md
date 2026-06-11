# hermes-sfw

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Socket Firewall Free plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Block malicious dependencies at install time. Wrap any package manager command with `sfw` to get automatic protection — no API key, no config.

```
sfw action=run command="npm install express"
sfw action=status
```

## Quick Start

> **Requires Python 3.11+** and the [sfw CLI](https://github.com/nicedoc/socket-firewall-free) installed on the host system.

### Option 1: Deploy script (recommended)

```bash
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins/hermes-sfw
./deploy.sh
```

Then restart Hermes with `/reset`.

### Option 2: Manual symlink

```bash
git clone https://github.com/TheEpTic/hermes-plugins.git
ln -s "$(pwd)/hermes-plugins/hermes-sfw/src/hermes_sfw" ~/.hermes/plugins/hermes-sfw
```

Then `/reset` in Hermes. Changes to the source take effect immediately through the symlink — no restart needed.

### Option 3: As a Python package

```bash
pip install git+https://github.com/TheEpTic/hermes-plugins.git#subdirectory=hermes-sfw
```

Then add to your Hermes config:

```yaml
plugins:
  - name: hermes-sfw
    module: hermes_sfw
```

## Features

### `sfw run` — Execute Commands

Run any package manager command through sfw. Malicious packages are blocked automatically.

```bash
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

**Supported package managers:** npm, npx, yarn, pnpm (JS/TS), pip, pip3, uv (Python), cargo, rustup (Rust).

**Blocked packages:** When sfw detects a malicious package, the install is blocked and the package name is returned in the response. Non-package-manager commands (like `cat`, `rm`, `curl`) are rejected by the prefix allowlist.

**Output truncation:** Output exceeding 10,000 characters is automatically truncated with a size note.

### `sfw status` — Check Installation

Verify sfw is installed and get the version.

```bash
sfw action=status
```

Returns: `installed` (bool), `version` (string), `binary` (path).

## How It Works

hermes-sfw is a thin wrapper around the [sfw CLI](https://github.com/nicedoc/socket-firewall-free). It:

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
- `shlex.split()` with error handling prevents shell injection
- Workdir resolved with `os.path.realpath()` to prevent path traversal
- Output truncated at 10K chars to prevent context overflow
- Timeout protection prevents hanging installs

## Requirements

- Python 3.11+
- [sfw CLI](https://github.com/nicedoc/socket-firewall-free) installed on PATH
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)

## Troubleshooting

**"sfw is not installed"**
Install sfw globally: `npm i -g sfw`. The plugin searches PATH and common locations (`~/.local/share/pnpm/bin/`, `/usr/local/bin/`, `~/.npm-global/bin/`).

**Command rejected with "not allowed"**
Only package manager commands are allowed. If you need to add a prefix, modify `_ALLOWED_PREFIXES` in `manager.py`.

**Command timeout**
Default timeout is 5 minutes (300s). For very large installs, this may not be enough. Override via `SFWConfig(timeout=...)` when creating the manager.

**Output looks truncated**
This is intentional — outputs over 10K chars are truncated with a size note. The full output is in the raw stdout/stderr fields.

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
