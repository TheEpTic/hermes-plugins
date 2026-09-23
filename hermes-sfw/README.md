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

**Supported package managers:** npm, yarn, and pnpm for JavaScript/TypeScript; pip, pip3, and uv for Python; cargo for Rust. The `sfw` tool accepts dependency operations only; `npx` and runner subcommands are rejected there. Direct terminal enforcement is broader: it routes every reachable manager command — including `cargo test`, `pnpm test`, `npm run build`, version checks, and runners such as `npx`/`pnpx`/`uvx` — through sfw, and blocks the forms it cannot rewrite faithfully (see below).

**Blocked packages:** When sfw detects a malicious package, the install is blocked and the package name is returned in the response. Blocked and installed indicators are parsed from sfw output and returned as `blocked` and `installed` lists in the result, alongside `success`, `command`, `exit_code`, `stdout`, and `stderr`:

```text
🔴 blocked malicious-pkg
blocked: evil-trojan
🟢 installed express
added 5 packages
```

Non-package-manager commands (like `cat`, `rm`, `curl`) are rejected by the prefix allowlist, and commands longer than 1,024 characters are rejected outright.

**Output truncation:** Output exceeding 10,000 characters is intentionally truncated with a size note. The discarded suffix is not returned in another field.

### automatic terminal enforcement

When enabled, the plugin watches Hermes `terminal` calls. Reachable package-manager commands — including dev commands such as `cargo test`, `cargo clippy`, `pnpm test`, and `npm run build` — are rewritten before execution to invoke the resolved `sfw` binary while preserving the original shell syntax:

```text
terminal command: npm install express
executed command: /home/user/.local/share/pnpm/bin/sfw npm install express
```

The model does not need to notice a block or issue a second tool call. A manager is found wherever the shell would run it: after `NAME=value` assignments and redirections, behind transparent wrappers (`env`, `timeout`, `nice`, `ionice`, `stdbuf`, `nohup`, `setsid`, `time`, `exec`, `command`), as `python -m pip` (any CPython option form: `-mpip`, `-Im pip`, `-W ignore -m pip`) or `python -c` code naming a manager, or a versioned `pip3.12`, with quoted or escaped names (`'pip'`, `\pip`), and inside `bash -c '...'`/`env -S '...'` payloads, which are rewritten in place.

Forms the hook cannot rewrite faithfully are blocked before raw execution when they name a manager: opaque wrappers (`sudo`, `doas`, `xargs`, `eval`, `find -exec`, `taskset`, ...), aliases naming a manager, path-qualified managers, dynamic command words (`$PM install`), backtick or double-quoted `$(...)` substitutions, heredocs and here-strings, shells reading commands from stdin (`... | bash`), payloads that are not one plain quoted string, and malformed commands. Commands that never name a manager are untouched, so a `python - <<'EOF'` script or a backtick inside a quoted commit message runs normally.

The hook reads the command text only. A manager invoked from inside a file (`./install.sh`, `source setup.sh`, `cat install.sh | sh`, a Makefile target, an npm script) is not visible to it and runs unwrapped; blocking every script or `source` would also block `source .venv/bin/activate`.

A routed command that fails because the sfw launcher cannot start its own engine is annotated with the local cause and its repair, so a blocked `cargo build` is not just sfw's one-line error — see [troubleshooting](#troubleshooting).

The hook only runs when Hermes exposes `pre_tool_call` hooks. Set `HERMES_SFW_ENFORCE_DIRECT=off` before starting Hermes only when you deliberately want to bypass automatic terminal enforcement. The default is on.

### `sfw status` — check installation

Verify sfw is installed and get the version.

```text
sfw action=status
```

Returns: `installed` (bool), `version` (string), `binary` (path). `version` is the sfw binary's own `--version` output, which can differ from the npm package version you installed — see [troubleshooting](#troubleshooting). When the resolved binary is a launcher whose downloaded firewall binary is unreachable, the response also carries `usable: false` plus a `cache_fault` object (`reason`, `cached_asset`, `repair`) — `installed: true` alone does not mean the launcher can run.

## how it works

hermes-sfw is a thin wrapper around the [sfw CLI](https://github.com/SocketDev/sfw-free). It:

1. Validates commands against the strict package-manager operation grammar
2. Resolves and validates the working directory (if specified)
3. Executes the command through `sfw` with timeout protection
4. Parses stdout/stderr for blocked and installed package indicators
5. Returns structured JSON with success status, output, and parsed results

The explicit `sfw` tool executes the manager as an argument vector — never through a shell — so quoting and special characters cannot reach a shell interpreter. Automatic terminal enforcement uses Hermes's `modify` hook to build a shell-quoted command for the resolved `sfw` binary, preserving the same manager arguments while preventing the raw package manager from running. Both paths pass through Hermes's dangerous-command approval system and fail closed when that system is unavailable.

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

All settings live in `src/hermes_sfw/models.py` as an `SFWConfig` dataclass. Invalid values (an empty `sfw_bin`, a non-positive or non-integer `timeout`) raise `ValueError` at construction:

| Setting | Default | Description |
|---------|---------|-------------|
| `sfw_bin` | `sfw` | Path to the sfw binary (a concrete path bypasses PATH and shim discovery; it must be an executable file) |
| `timeout` | 300s | Max seconds per command |

## architecture

```
src/hermes_sfw/
├── __init__.py          # Plugin registration + Hermes hooks
├── guard.py             # Terminal guard: route / block / pass per command
├── manager.py           # SFWManager — thin facade over the modules below
├── validate.py          # sfw tool command + workdir validation
├── resolve.py           # Binary discovery, launcher cache faults, version query
├── diagnose.py          # action=status self-diagnosis
├── output.py            # Subprocess execution + output parsing
├── approval.py          # Hermes dangerous-command approval bridge
├── models.py            # SFWConfig, SFWResult, diagnosis models
├── schemas.py           # Tool schema (what the LLM sees)
├── utils.py             # ok(), err(), require() helpers
├── plugin.yaml          # Hermes plugin manifest
├── py.typed             # PEP 561 marker
└── handlers/
    ├── __init__.py
    └── sfw.py           # sfw tool handler
```

**Key design decisions:**

- `SFWManager` holds immutable config only; binary discovery runs on demand. The terminal hooks read one module-level manager that `register()` replaces on every call, so a host unload + re-register gets fresh registrations.
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

**`[sfw] Failed to prepare firewall binary: Unable to fetch latest release and no valid cached release found.`**

The shim and its `sfw.mjs` target are fine, but the launcher cannot start its engine. `sfw.mjs` keeps the downloaded firewall binary in `<package root>/.sfw-cache/<release>/` and points `.sfw-cache/latest` at it; when that link does not resolve it falls back to fetching the release from the GitHub API, which is rate-limited (60 requests/hour for anonymous calls, shared per IP) and commonly blocked on hosted or cloud IPs. The launcher then exits before your package manager runs, so a routed command like `cargo build --release` is blocked even though it never touched a registry.

`sfw action=status` reports this as `usable: false` with a `cache_fault` object, and since 0.2.14 the same diagnosis is appended to the failing terminal output. Inspect and repair:

```bash
sfw action=status                    # binary + cache_fault.repair
ls -l <package root>/.sfw-cache/     # the latest link and the cached release
ln -sfn <package root>/.sfw-cache/<release>/sfw-free-linux-x86_64 <package root>/.sfw-cache/latest
```

The cached release directory already holds a working 140 MB `sfw-free-*` binary; relinking is offline and immediate. If no release directory exists at all, the install never downloaded one: reinstall while the network can reach `api.github.com`, or copy a `.sfw-cache` tree from a machine where sfw works.

Discovery prefers a usable install, so a second working sfw (for example one under `~/.hermes/node/bin`) is chosen over a broken one. When the only install is broken it is still reported rather than hidden as "not installed".

**Command rejected with "not allowed"**

Only the documented dependency operations are allowed. Runner-style commands, unsupported subcommands, shell-prefixed calls, malformed commands, and manager paths are intentionally blocked so they cannot bypass SFW. Use a documented `sfw` operation, or set `HERMES_SFW_ENFORCE_DIRECT=off` only when you deliberately accept raw terminal dependency execution.

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
