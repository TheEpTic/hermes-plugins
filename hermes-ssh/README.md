# hermes-ssh

[![CI](https://github.com/TheEpTic/hermes-ssh/actions/workflows/ci.yml/badge.svg)](https://github.com/TheEpTic/hermes-ssh/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

SSH remote execution plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Run commands on remote servers, track sessions, reuse connections — all from inside Hermes.

```
/ssh web1 uptime
ssh_machines add name=web1 host=192.168.1.50
ssh_sessions list
```

## Quick Start

> **Requires Python 3.11+** and an OpenSSH client (`ssh`) on the host system.

### Option 1: Deploy script (recommended)

```bash
git clone https://github.com/TheEpTic/hermes-ssh.git
cd hermes-ssh
./deploy.sh
```

Then restart Hermes with `/reset`.

### Option 2: Manual symlink

```bash
git clone https://github.com/TheEpTic/hermes-ssh.git
ln -s "$(pwd)/hermes-ssh/src/ssh_tools" ~/.hermes/plugins/hermes-ssh
```

Then `/reset` in Hermes. Changes to the source take effect immediately through the symlink — no restart needed.

### Option 3: As a Python package

```bash
pip install git+https://github.com/TheEpTic/hermes-ssh.git
```

Then add to your Hermes config:

```yaml
plugins:
  - name: hermes-ssh
    module: ssh_tools
```

## Features

### `ssh_terminal` — Run Commands

Execute any command on a remote machine. Commands run through `bash -c` with `pipefail`, so pipelines work correctly.

```bash
# Synchronous (waits for completion)
ssh_terminal machine=web1 command="df -h"

# Background (returns immediately)
ssh_terminal machine=web1 command="tail -f /var/log/syslog" background=true

# With timeout
ssh_terminal machine=web1 command="make -j4" timeout=300
```

**Output truncation:** When output exceeds `max_output_chars` (default: 50,000), the full output is saved to a `/tmp/` file and a summary with the file path is returned. The LLM can then use `read_file` to access the complete output.

**Background commands:** Long-running commands can be backgrounded. The plugin tracks the process and lets you poll for status or retrieve output later.

```bash
# Check if still running
ssh_terminal poll=<session_id>

# Read output from completed command
ssh_terminal read_output=<session_id>

# Or via ssh_sessions
ssh_sessions action=poll session_id=<session_id>
ssh_sessions action=read_output session_id=<session_id>
```

### `ssh_machines` — Machine Registry

Register servers once, refer to them by name or alias.

```bash
# Add a server
ssh_machines action=add name=web1 host=192.168.1.50 user=deploy key=~/.ssh/id_ed25519

# Add with aliases and tags
ssh_machines action=add name=prod-web host=10.0.0.1 aliases=web1,tags=production,web

# List all machines
ssh_machines action=list

# Test connectivity
ssh_machines action=test name=web1

# Get full details
ssh_machines action=inspect name=web1
```

Machine names must be alphanumeric with dots, hyphens, or underscores (1-64 chars). Slashes, spaces, and glob characters are rejected.

### `ssh_sessions` — Session Tracking

Every command creates a session. Sessions track the PID, machine, command count, and idle time.

```bash
# List active sessions
ssh_sessions action=list

# Kill a session (terminates the SSH process)
ssh_sessions action=kill session_id=<session_id>

# Clean up all idle sessions (>30 min)
ssh_sessions action=cleanup

# Remove old closed sessions (>24 hours)
ssh_sessions action=prune
```

Idle sessions are automatically killed by a background checker after 30 minutes. Closed sessions are pruned after 24 hours.

### `/ssh` Slash Command

Quick access from chat without remembering tool names:

```
/ssh                     # List machines and sessions
/ssh web1                # Inspect a machine
/ssh web1 uptime         # Run a command
/ssh web1 docker ps      # Run a command
/ssh test                # Test connectivity to all machines
/ssh cleanup             # Kill all idle sessions
/ssh help                # Show help
```

## Configuration

All settings live in `src/ssh_tools/config.py` as an `SSHConfig` dataclass:

| Setting | Default | Description |
|---------|---------|-------------|
| `default_port` | 22 | SSH port for new machines |
| `default_user` | root | SSH user for new machines |
| `connect_timeout` | 5s | SSH handshake timeout |
| `command_timeout` | 30s | Command execution timeout |
| `max_output_chars` | 50,000 | Output truncation threshold |
| `idle_check_interval` | 60s | Seconds between idle checks |
| `idle_timeout_minutes` | 30m | Auto-kill after this idle time |
| `closed_prune_hours` | 24h | Remove closed sessions after this |
| `strict_host_key_checking` | no | SSH host key verification |

## Architecture

```
src/ssh_tools/
├── __init__.py          # Plugin registration + Hermes hooks
├── config.py            # SSHConfig (immutable dataclass)
├── manager.py           # SSHManager — all state and operations
├── models.py            # Machine, Session dataclasses
├── schemas.py           # Tool schemas (LLM-facing)
├── utils.py             # ok(), err(), require() helpers
├── py.typed             # PEP 561 marker
└── handlers/
    ├── terminal.py      # ssh_terminal (execute, poll, read_output)
    ├── machines.py      # ssh_machines (add/list/remove/test/inspect)
    ├── sessions.py      # ssh_sessions (list/kill/cleanup/prune/poll/read_output)
    └── slash.py         # /ssh slash command
```

**Key design decisions:**

- `SSHManager` owns all state. Thread-safe. No module-level mutable state.
- Handlers are thin closures — validate params, dispatch to manager, return JSON.
- JSON files use atomic writes (temp file + `os.replace`) for crash safety.
- Data directory has restricted permissions (0o700). Audit log and output files use 0o600.
- Machine names are validated to prevent path traversal and glob injection.
- Connection reuse via `ControlMaster` with 5-minute persist window.

## Security

See [SECURITY.md](SECURITY.md) for the full picture.

**Defaults you should know about:**

- `StrictHostKeyChecking=no` — convenient but vulnerable to MITM. Set to `yes` for production.
- Credentials stored in plaintext JSON at `data/machines.json`. Data directory is 0o700.
- All commands execute with the permissions of the Hermes agent process.

**Hardening applied:**

- Machine names validated against `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`
- Output files written with 0o600 permissions
- Audit log created with 0o600 permissions
- Atomic JSON writes prevent corruption on crash
- Orphaned temp files cleaned on startup

## Requirements

- Python 3.11+
- OpenSSH client (`ssh`)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)

## Troubleshooting

**Connection refused**
The remote host may not be listening on the expected port, or a firewall is blocking the connection. Verify with `ssh -v user@host` outside of Hermes.

**Permission denied (publickey)**
The SSH key path stored in the machine registry may be incorrect, or the remote host doesn't have the corresponding public key in `~/.ssh/authorized_keys`. Verify with `ssh -i /path/to/key user@host`.

**Command timeout**
Commands exceeding `command_timeout` (default 30s) are killed. Increase the timeout or use `background=true` for long-running work.

**Output looks truncated**
This is intentional — large outputs are saved to `/tmp/` and a summary is returned. Use `read_output` or `read_file` on the returned path for the full output.

**Session stuck as "active" after process died**
If the agent restarted, background process references are lost. Use `ssh_sessions action=cleanup` to kill stale sessions, or `ssh_sessions action=prune` to remove old closed ones.

## Development

```bash
git clone https://github.com/TheEpTic/hermes-ssh.git
cd hermes-ssh
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Run checks
ruff check src/ssh_tools/ tests/
ruff format --check src/ssh_tools/ tests/
mypy src/ssh_tools/
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT — see [LICENSE](LICENSE).
