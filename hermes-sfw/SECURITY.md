# Security Policy

## Scope

hermes-sfw wraps package manager commands with Socket Firewall Free (sfw) to block malicious dependencies. The plugin executes package manager commands as subprocesses on the host system.

## What hermes-sfw does

- Validates commands against a prefix allowlist before execution
- Executes package manager commands through the `sfw` CLI
- Parses stdout/stderr for blocked and installed package indicators
- Runs commands with the permissions of the Hermes agent process

## Security considerations

**Command execution**
The `sfw run` tool executes package manager commands on the host. Anyone with access to the Hermes agent can run `npm install`, `pip install`, etc. on registered working directories. Ensure your Hermes instance is appropriately access-controlled.

**Prefix allowlist**
Only commands starting with allowed prefixes (npm, yarn, pnpm, pip, pip3, uv, cargo) are accepted, and each manager is restricted to dependency operations (install/add/remove/update/ci/sync and the like). Non-package-manager commands are rejected. `npx` and runner-style subcommands are intentionally blocked because they can execute arbitrary package code even when the package is not known-malicious yet. This prevents misuse of the tool for arbitrary command execution, but the allowlist is not a security boundary — package managers themselves can execute arbitrary code (postinstall scripts, etc.).

**Direct terminal enforcement**
When Hermes exposes `pre_tool_call` hooks, supported dependency operations sent directly through the Hermes `terminal` tool are rewritten to the resolved `sfw` binary before execution. Unsupported package-manager forms, shell prefixes, malformed commands, and manager paths are blocked instead of falling through to raw execution. Set `HERMES_SFW_ENFORCE_DIRECT=off` in the Hermes process environment only when deliberately disabling this enforcement. If the hooks are unavailable, direct terminal dependency enforcement cannot be provided and the runtime should be upgraded before relying on this control.

**Working directory**
When a `workdir` is specified, it is resolved with `os.path.realpath()` to prevent symlink-based path traversal. The directory must exist and be a directory.

**Output handling**
Command output is truncated at 10,000 characters to prevent context overflow. Output is not persisted to disk — it lives only in the JSON response.

**Timeout protection**
Commands are killed after 300 seconds (5 minutes) by default. This prevents runaway installs from consuming resources indefinitely.

## Hardening applied

- **Command prefix allowlist** — only recognized package managers are accepted
- **`shlex.split()` parsing** — prevents shell injection via malformed commands
- **Path traversal prevention** — workdir resolved with `os.path.realpath()`
- **Output truncation** — prevents context overflow from large install output
- **Timeout protection** — prevents hanging processes
- **`OSError` sanitization** — internal error details not leaked to the LLM

## Reporting vulnerabilities

If you discover a security issue, please open a private security advisory on GitHub or email nexus@eptic.me. Do not open a public issue for security vulnerabilities.

## Recommendations

1. Run the Hermes agent as a non-root user where possible
2. Use sfw's built-in threat database for maximum protection
3. Review sfw's blocked package list periodically
4. Consider restricting which working directories the agent can access
5. Monitor sfw output for unexpected blocked packages
