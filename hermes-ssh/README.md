# hermes-ssh

[![CI](https://github.com/TheEpTic/hermes-plugins/actions/workflows/ci.yml/badge.svg)](https://github.com/TheEpTic/hermes-plugins/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

SSH remote operations plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Run commands, transfer files, track background sessions, and reuse connections across a named machine registry.

```text
/ssh web1 uptime
ssh_transfer action=upload machine=web1 source="./dist/app.tar.gz" destination="/srv/releases/app.tar.gz"
ssh_machines action=add name=web1 host=192.168.1.50 user=deploy
```

## quick start

`hermes-ssh` is for **named multi-host operations**. Hermes's core SSH backend is useful for one configured remote terminal; this plugin adds a reusable machine inventory, aliases, per-command targeting, file transfer, background sessions, and audit history.

> Requires Python 3.11+, Hermes Agent, and OpenSSH clients named `ssh` and `sftp`.

Install the package into the same Python environment that runs Hermes:

```bash
python -m pip install hermes-ssh
hermes plugins enable hermes-ssh --no-allow-tool-override
```

Run `/reset` or restart Hermes, then verify:

```bash
python -m pip show hermes-ssh
hermes plugins list --enabled --plain
```

If Hermes cannot see the package, the `python` command above was not Hermes's Python. Follow the environment procedure in the repository [AGENTS.md](../AGENTS.md).

For source development:

```bash
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins/hermes-ssh
./deploy.sh
hermes plugins enable hermes-ssh --no-allow-tool-override
```

Run `/reset` or restart Hermes after changing the source tree.

## features

### `ssh_terminal` — run commands

Execute any command on a remote machine. Commands run through `bash -c` with `pipefail`, so pipelines work correctly.

```text
# synchronous
ssh_terminal machine=web1 command="df -h"

# background
ssh_terminal machine=web1 command="tail -f /var/log/syslog" background=true

# custom timeout
ssh_terminal machine=web1 command="make -j4" timeout=300
```

When output exceeds `max_output_chars` (default: 50,000), the full output is saved under the plugin's restricted output directory and a summary with the path is returned.

Long-running commands can spool stdout and stderr to restricted files in the background:

```text
ssh_terminal poll=<session_id>
ssh_terminal read_output=<session_id>

ssh_sessions action=poll session_id=<session_id>
ssh_sessions action=read_output session_id=<session_id>
```

### `ssh_transfer` — upload and download files

Transfer a regular file or directory between the Hermes host and a registered machine. The tool uses OpenSSH SFTP, reuses the same ControlMaster connection settings as `ssh_terminal`, and records transfer metadata in the existing audit log.

```text
# upload a release
ssh_transfer action=upload machine=web1 source="./dist/app.tar.gz" destination="/srv/releases/app.tar.gz"

# download a log
ssh_transfer action=download machine=web1 source="/var/log/app.log" destination="./app.log"

# upload a directory
ssh_transfer action=upload machine=web1 source="./public" destination="/srv/app/public" recursive=true

# explicitly replace an existing regular file
ssh_transfer action=upload machine=web1 source="./app.tar.gz" destination="/srv/app.tar.gz" overwrite=true
```

Transfer behaviour is intentionally conservative:

- Existing files are not replaced unless `overwrite=true`.
- Directories require `recursive=true`.
- Existing directories are never merged or replaced.
- Uploads and downloads stage through generated temporary paths before final rename.
- Local and remote credential paths are blocked.
- Symbolic links, special files, traversal segments, wildcard remote paths, and recursive trees containing links are rejected.
- Upload and download paths are explicit destinations, not shell expressions.

### `ssh_machines` — machine registry

Register servers once, then refer to them by name or alias.

```text
ssh_machines action=add name=web1 host=192.168.1.50 user=deploy key=~/.ssh/id_ed25519
ssh_machines action=add name=prod-web host=10.0.0.1 aliases=web1 tags=production,web
ssh_machines action=list
ssh_machines action=test name=web1
ssh_machines action=inspect name=web1
```

Machine names must be alphanumeric with dots, hyphens, or underscores (1-64 characters). Slashes, spaces, and glob characters are rejected.

### `ssh_sessions` — session tracking

Background commands are tracked as sessions with their process, machine, command count, and idle time.

```text
ssh_sessions action=list
ssh_sessions action=kill session_id=<session_id>
ssh_sessions action=cleanup
ssh_sessions action=prune
```

Idle sessions are automatically killed after 30 minutes. Closed sessions are pruned after 24 hours.

### `/ssh` slash command

Quick terminal access from chat:

```text
/ssh
/ssh web1
/ssh web1 uptime
/ssh web1 docker ps
/ssh test
/ssh cleanup
/ssh help
```

File transfers use the `ssh_transfer` tool rather than slash-command syntax so direction, paths, overwrite behaviour, and recursion remain explicit.

## configuration

Settings live in `src/ssh_tools/config.py` as an `SSHConfig` dataclass:

| setting | default | description |
|---|---:|---|
| `default_port` | 22 | SSH port for new machines |
| `default_user` | current local user | SSH user for new machines |
| `connect_timeout` | 5s | SSH handshake timeout |
| `command_timeout` | 30s | command execution timeout |
| `max_output_chars` | 50,000 | output truncation threshold |
| `audit_log_mode` | redacted | `redacted`, `metadata`, or `off` |
| `idle_check_interval` | 60s | seconds between idle checks |
| `idle_timeout_minutes` | 30m | auto-kill after this idle time |
| `closed_prune_hours` | 24h | remove closed sessions after this |
| `strict_host_key_checking` | accept-new | SSH host key verification |

`ssh_transfer` has a 300-second default timeout and a 3,600-second maximum supplied through its tool schema.

## architecture

```text
src/ssh_tools/
├── __init__.py          # plugin registration and Hermes hooks
├── approval.py          # Hermes dangerous-command approval bridge
├── config.py            # SSHConfig
├── manager.py           # machine, command, and session state
├── models.py            # Machine and Session dataclasses
├── schemas.py           # LLM-facing tool schemas
├── storage.py           # encrypted machine registry
├── transfers/           # SFTP policy, transport, staging, and audit
│   ├── __init__.py      # validated transfer entry point
│   ├── models.py        # transfer request and result models
│   ├── policy.py        # local and remote path safety
│   ├── service.py       # transfer orchestration and finalisation
│   └── transport.py     # OpenSSH SFTP argv and batch construction
├── utils.py             # handler response helpers
├── py.typed             # PEP 561 marker
└── handlers/
    ├── terminal.py      # ssh_terminal
    ├── transfer.py      # ssh_transfer
    ├── machines.py      # ssh_machines
    ├── sessions.py      # ssh_sessions
    └── slash.py         # /ssh
```

Key design decisions:

- `SSHManager` owns the machine registry, command execution, and session state.
- Tool handlers are thin closures that validate parameters and dispatch work.
- Transfers use argv plus SFTP batch input, never `shell=True` or interpolated local shell commands.
- Upload/download payloads are staged before their final rename.
- Machine records are encrypted at rest.
- JSON files use atomic writes for crash safety.
- Data directories use 0o700; audit and output files use 0o600.
- Machine names and transfer paths are validated before reaching OpenSSH.
- Connections are reused through ControlMaster with a five-minute persist window.

## security

See [SECURITY.md](SECURITY.md) for the full boundary.

Defaults and limitations worth knowing:

- `StrictHostKeyChecking=accept-new` accepts first-seen keys and rejects changed keys. Use `yes` for strict production hosts.
- Machine credentials are encrypted at rest under `~/.hermes/ssh-tools/`.
- Audit logs redact common inline command credentials by default. Metadata mode stores hashes and lengths instead of transfer paths.
- Commands and transfers run with the registered remote user's permissions.
- Transfer path blocks reduce accidental credential movement but are not a sandbox for untrusted prompts.
- Use dedicated non-root accounts and expose Hermes only to trusted operators.

## requirements

- Python 3.11+
- OpenSSH clients named `ssh` and `sftp`
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)

## troubleshooting

**Plugin installed but tools are absent**

Enable the plugin and reset Hermes:

```bash
hermes plugins enable hermes-ssh --no-allow-tool-override
hermes plugins list --enabled --plain
```

**Permission denied (publickey)**

The stored user, key path, or remote authorisation is wrong. Verify the same account with OpenSSH outside Hermes.

**Transfer says `sftp` is unavailable**

Install the OpenSSH client package in the environment or container that runs Hermes and verify `sftp` is on that process's `PATH`.

**Remote path rejected**

Remote transfer paths must be absolute or begin with `~/`, name an explicit file or directory, and contain no wildcard or traversal segments.

**Destination already exists**

Regular files require `overwrite=true` for replacement. Directory replacement and merging are intentionally unsupported.

**Command timeout**

Increase `timeout` or use `background=true` for terminal commands. Transfers accept timeouts from 1 to 3,600 seconds.

**Output looks truncated**

Large terminal output is retained under the restricted output directory. Use `read_output` or `read_file` on the returned path.

## development

```bash
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins/hermes-ssh
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

black --check src/ssh_tools/ tests/
mypy src/ssh_tools/
pytest
```

CI runs those gates on Python 3.11, 3.12, and 3.13.

## license

MIT — see [LICENSE](LICENSE).
