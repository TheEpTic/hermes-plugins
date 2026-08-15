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

## features

### `sfw run` — execute commands

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

**Supported package managers:** npm, yarn, and pnpm for JavaScript/TypeScript; pip, pip3, and uv for Python; cargo for Rust. Each manager is restricted to dependency operations — for example `npm install`, `npm ci`, `npm uninstall`, and `npm update` are accepted, but runner-style subcommands like `npm run` are not. `npx`, `rustup`, and runner-style subcommands are intentionally blocked because they can execute arbitrary programs.

**Blocked packages:** When sfw detects a malicious package, the install is blocked and the package name is returned in the response. Blocked and installed indicators are parsed from sfw output and returned as `blocked` and `installed` lists in the result, alongside `success`, `command`, `exit_code`, `stdout`, and `stderr`:

```text
🔴 blocked malicious-pkg
blocked: evil-trojan
🟢 installed express
added 5 packages
```

Non-package-manager commands (like `cat`, `rm`, `curl`) are rejected by the prefix allowlist, and commands longer than 1,024 characters are rejected outright.

**Output truncation:** Output exceeding 10,000 characters is intentionally truncated with a size note. The discarded suffix is not returned in another field.

### automatic terminal guard

When enabled, the plugin watches Hermes `terminal` calls. A supported dependency operation such as `npm install`, `uv pip install`, or `cargo fetch` is blocked before raw execution and the agent is directed to use the `sfw` tool instead:

```text
dependency operation blocked by hermes-sfw. run it with the sfw tool instead:
sfw action=run command='npm install express'
```

The guard only intercepts operations the `sfw` tool itself would accept, and it only runs when Hermes exposes `pre_tool_call` hooks (if hooks are unavailable, a warning is logged and direct terminal installs are not enforced). Set `HERMES_SFW_ENFORCE_DIRECT=off` before starting Hermes only when you deliberately want direct terminal dependency operations.

### `sfw status` — check installation

Verify sfw is installed and get the version.

```text
sfw action=status
```

Returns: `installed` (bool), `version` (string), `binary` (path). `version` is the sfw binary's own `--version` output, which can differ from the npm package version you installed — see [troubleshooting](#troubleshooting).

## how it works

hermes-sfw is a thin wrapper around the [sfw CLI](https://github.com/SocketDev/sfw-free). It:

1. Validates the command starts with an allowed package manager prefix
2. Resolves and validates the working directory (if specified)
3. Executes the command through `sfw` with timeout protection
4. Parses stdout/stderr for blocked and installed package indicators
5. Returns structured JSON with success status, output, and parsed results

Commands are executed as an argument vector — never through a shell — so quoting and special characters cannot reach a shell interpreter. Commands also pass through Hermes's dangerous-command approval system first and fail closed when that system is unavailable.

### binary discovery

The sfw binary is located on demand for every call rather than cached, so an install that happens after the plugin is registered is picked up immediately. Discovery order:

1. An explicit `SFWConfig(sfw_bin=...)` path, if configured
2. `sfw` on `PATH` (`shutil.which`)
3. Known shim and install locations:
   - `~/.local/share/pnpm/sfw`
   - `~/.local/share/pnpm/bin/sfw`
   - `~/.local/bin/sfw`
   - `~/.npm-global/bin/sfw`
   - `~/.cargo/bin/sfw`
   - `/usr/local/bin/sfw`

## configuration

All settings live in `src/hermes_sfw/manager.py` as an `SFWConfig` dataclass:

| Setting | Default | Description |
|---------|---------|-------------|
| `sfw_bin` | `sfw` | Path to the sfw binary (a concrete path bypasses PATH and shim discovery) |
| `timeout` | 300s | Max seconds per command |

## architecture

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

## security

See [SECURITY.md](SECURITY.md) for the full boundary.

**Defaults you should know about:**

- Only package manager commands are allowed (prefix allowlist: npm, yarn, pnpm, pip, cargo, etc.)
- Non-package-manager commands (`cat`, `rm`, `curl`, etc.) are rejected
- Commands run with the permissions of the Hermes agent process
- Commands pass through Hermes dangerous-command approval checks and fail closed if the approval system is unavailable

**hermes-sfw is a dependency guard, not a sandbox.** It blocks packages sfw knows are malicious, but package lifecycle scripts (`postinstall`, etc.) and build backends still run with the permissions of the Hermes process. Use it to reduce known-bad dependencies, not to contain untrusted code.

**Hardening applied:**

- Command prefix validation via allowlist before execution
- Commands are passed as an argument vector without invoking a shell
- `shlex.split()` handles quoting and rejects malformed command strings early
- Working directories are expanded, resolved, and checked to be existing directories
- Output truncated at 10K chars to prevent context overflow
- Timeout protection prevents hanging installs

## requirements

- Python 3.11+
- [sfw CLI](https://github.com/SocketDev/sfw-free) installed on PATH
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)

## troubleshooting

**Plugin installed but tools are absent**

Enable the plugin and reset Hermes:

```bash
hermes plugins enable hermes-sfw --no-allow-tool-override
hermes plugins list --enabled --plain
```

**`sfw action=status` reports `installed: false`**

The binary was not found on `PATH` or in any known shim location at the moment of the call. Possible causes:

- sfw was never installed — run `npm i -g sfw`.
- sfw was installed into a different environment or user than the one running Hermes. A shell finding `sfw` does not prove the Hermes process can find it; background shells, systemd services, and containers often have a different `PATH`.
- The install happened after Hermes started. Binary discovery is on-demand since 0.2.4, so no restart is required — but if you are on an older version, restart Hermes after installing sfw.

**Version looks wrong (`status` reports a version that differs from the npm package)**

`sfw action=status` reports the version of the sfw *binary* (`sfw --version`). The npm package version and the binary's own version are separate layers and can legitimately differ. Check which layer you are looking at before reporting a bug.

**Broken shim: `sfw` exists but every run fails**

pnpm-style installs create a wrapper script at the shim path that points at the real `sfw.mjs`. If that target file is missing or stale, even `npm ci` can fail and `sfw --version` may error. Verify the resolved binary from `sfw action=status` (the `binary` field), inspect that path, and repair with `npm i -g sfw` (or your package manager's equivalent) so the shim is regenerated. As a workaround, point `SFWConfig(sfw_bin=...)` at a known-good binary.

**Command rejected with "not allowed"**

Only the documented dependency operations are allowed. Runner-style commands and unsupported subcommands are intentionally rejected; use the regular Hermes terminal only when you deliberately do not want SFW protection.

**Command timeout**

Default timeout is 5 minutes (300s). For very large installs, this may not be enough. Override via `SFWConfig(timeout=...)` when creating the manager.

**Output looks truncated**

This is intentional. Outputs over 10K characters are truncated to protect context, and the discarded suffix is not retained by the plugin.

## development

```bash
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins/hermes-sfw
uv sync --extra dev --locked

# Run the gates
uv run pytest
uv run black --check src tests
uv run mypy src
```

CI runs those gates on Python 3.11, 3.12, and 3.13.

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## license

MIT — see [LICENSE](LICENSE).
