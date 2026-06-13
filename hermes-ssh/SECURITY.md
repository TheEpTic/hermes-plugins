# Security Policy

## Scope

hermes-ssh executes commands on remote machines via SSH. This carries inherent risk — the plugin is designed for trusted environments where the operator controls both the local agent and the remote hosts.

## What hermes-ssh does

- Stores machine credentials (host, user, SSH key path) encrypted at rest in `~/.hermes/ssh-tools/machines.json`
- Executes arbitrary commands on remote hosts via `ssh`
- Runs commands through `bash -c` with `pipefail` enabled
- Uses `ControlMaster` for connection reuse (5-minute persist)
- Defaults to `StrictHostKeyChecking=accept-new`
- Logs every command to `~/.hermes/ssh-tools/command_log.jsonl` with timestamps and exit codes

## Security considerations

**`StrictHostKeyChecking=accept-new` (default)**
The plugin accepts first-seen SSH host keys but rejects changed host keys. For high-trust production hosts, set `StrictHostKeyChecking=yes` in your SSH config.

**Credential storage**
Machine configs are encrypted at rest with Fernet in `~/.hermes/ssh-tools/machines.json`. The data directory is created with 0o700 permissions (owner-only access), and plaintext legacy files are migrated automatically on startup.

**Command execution**
The `ssh_terminal` tool runs arbitrary commands on remote hosts. Anyone with access to the Hermes agent can execute commands on registered machines. Ensure your Hermes instance is appropriately access-controlled.

**Output files**
When command output exceeds the truncation threshold, it is saved under `~/.hermes/ssh-tools/outputs/` with 0o600 permissions (owner-only read/write). These files are automatically cleaned up when the session is closed or killed.

**ControlMaster sockets**
Persistent SSH connections are stored as Unix sockets in `~/.hermes/ssh-tools/sockets/`. These are local-only and not exposed over the network. Socket files are removed when sessions are killed.

## Hardening applied

- **Machine field validation** — names must match `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`; hosts, users, ports, aliases, tags, and key paths are also validated before persistence. Slashes, spaces, glob characters, and other unsafe characters are rejected where they can affect local paths or SSH argument parsing.
- **Restricted file permissions** — data directory (0o700), audit log (0o600), output files (0o600).
- **Atomic JSON writes** — writes go through a temp file + `os.replace()` with `fsync` to prevent corruption on crash.
- **Glob injection prevention** — output file cleanup uses `iterdir()` + prefix matching instead of `Path.glob()` with user-controlled input.
- **Startup cleanup** — orphaned `.tmp` files from previous crashes are cleaned on plugin initialization.

## Reporting vulnerabilities

If you discover a security issue, please open a private security advisory on GitHub or email nexus@eptic.me. Do not open a public issue for security vulnerabilities.

## Recommendations

1. Use SSH key authentication (not passwords) for remote hosts
2. Restrict which machines can be registered via your Hermes access controls
3. Consider `StrictHostKeyChecking=yes` for production hosts
4. Run the Hermes agent as a non-root user where possible
5. Review `~/.hermes/ssh-tools/machines.json` periodically to remove stale entries
6. Set `command_timeout` appropriately — very long timeouts can tie up resources
7. Monitor `~/.hermes/ssh-tools/command_log.jsonl` for unexpected command patterns
