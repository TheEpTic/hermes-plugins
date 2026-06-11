# Security Policy

## Scope

hermes-ssh executes commands on remote machines via SSH. This carries inherent risk — the plugin is designed for trusted environments where the operator controls both the local agent and the remote hosts.

## What hermes-ssh does

- Stores machine credentials (host, user, SSH key path) in plaintext JSON at `data/machines.json`
- Executes arbitrary commands on remote hosts via `ssh`
- Runs commands through `bash -c` with `pipefail` enabled
- Uses `ControlMaster` for connection reuse (5-minute persist)
- Defaults to `StrictHostKeyChecking=no` for convenience
- Logs every command to `data/command_log.jsonl` with timestamps and exit codes

## Security considerations

**`StrictHostKeyChecking=no` (default)**
The plugin disables SSH host key verification by default. This prevents connection failures on first use but makes the connection vulnerable to MITM attacks. If you're connecting to hosts over untrusted networks, set `StrictHostKeyChecking=yes` in your SSH config or pass `-o StrictHostKeyChecking=yes` via your machine's SSH configuration.

**Credential storage**
Machine configs are stored in plaintext JSON. The data directory (`data/`) is created with 0o700 permissions (owner-only access). Ensure appropriate filesystem permissions if you store SSH key paths in the registry.

**Command execution**
The `ssh_terminal` tool runs arbitrary commands on remote hosts. Anyone with access to the Hermes agent can execute commands on registered machines. Ensure your Hermes instance is appropriately access-controlled.

**Output files**
When command output exceeds the truncation threshold, it is saved to `/tmp/ssh_output_{session_id}_{stream}.txt` with 0o600 permissions (owner-only read/write). These files are automatically cleaned up when the session is closed or killed.

**ControlMaster sockets**
Persistent SSH connections are stored as Unix sockets in `data/sockets/`. These are local-only and not exposed over the network. Socket files are removed when sessions are killed.

## Hardening applied

- **Machine name validation** — names must match `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`. Slashes, spaces, glob characters, and other unsafe characters are rejected. This prevents path traversal in socket paths and output file paths.
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
5. Review `data/machines.json` periodically to remove stale entries
6. Set `command_timeout` appropriately — very long timeouts can tie up resources
7. Monitor `data/command_log.jsonl` for unexpected command patterns
