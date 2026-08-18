# Changelog

## [0.4.4] - 2026-08-18

- Raise the cryptography runtime floor to `50.0.0`, matching Hermes Agent 0.20.4 and excluding the vulnerable pre-50 release range reported by GitHub (CVE-2026-69248 and related advisories).

## [0.4.3] - 2026-08-18

- Refresh release metadata and the lockfile so GitHub's dependency graph records the patched `pytest 9.1.1` development dependency instead of the stale vulnerable `9.0.2` snapshot (CVE-2025-71176 / GHSA-6w46-j5rx-g56g).
- Flatten nested conditionals across SSH handlers, session and transfer lifecycle code, and migration paths, with AST regression coverage.

## [0.4.2] - 2026-08-15

- Surface actionable remediation when host-key verification fails on first connect (ssh-keyscan seeding or accept-new) without changing the strict verification policy.
- Remember the working key per host after successful authentication and report which keys were attempted on failure, so agents stop brute-forcing default identities.
- Warn when `ssh_machines add` registers a host+user that already exists under a different name (non-blocking, with a hint pointing at the existing registration).
- Document `ssh_transfer` as the audited replacement for raw `scp`/`ssh -i`, and the shared-inventory boundary (`~/.hermes/ssh-tools` global across profiles).

## [0.4.1] - 2026-08-15

- Refresh development and transitive dependencies (cryptography 50.0.0, librt, packaging, platformdirs).

## Unreleased

## [0.4.0] - 2026-07-29

- Default to strict SSH host-key verification so a first connection cannot silently trust a network attacker.
- Report failed background commands as failures when they finish instead of returning a false success result.
- Align the machine-registration schema with runtime behavior: omitted users default to the current local user, never `root`.
- Add `ssh_transfer` for audited uploads and downloads over OpenSSH SFTP, with staged finalisation, no-overwrite defaults, recursive directory support, and credential/symlink protections.
- Relax the `cryptography` lower bound to support Hermes Agent 0.19.0's pinned 46.0.7 runtime dependency.
- Spool background stdout and stderr to restricted files so verbose commands cannot deadlock.
- Keep returned large-output files available after completed sessions are closed.
- Redact common inline secrets from audit logs, with metadata-only and disabled modes.
- Default new machine registrations to the current local user instead of root.
- Resolve `__version__` from installed package metadata.

## [0.3.3] - 2026-07-24

- Update development dependencies to mypy 2.3.0 and pytest 9.1.1.

## [0.3.2] - 2026-07-13

- Align the packaged Hermes manifest with the release version.

## [0.3.1] - 2026-07-13

- Fix the release artifact path so the tagged build can reach PyPI.

## [0.3.0] - 2026-07-13

- Fail closed when Hermes command approvals are unavailable.
- Never signal persisted PIDs after restart; only tracked process groups can be killed.
- Preserve shared SSH ControlMaster sockets when killing individual commands.
- Make `cryptography` a required dependency and add packaged-plugin discovery metadata.
- Declare provided tools in the Hermes plugin manifest.

## 0.2.0 — Bug hunt, security hardening, documentation

### New features

- **Background commands** — run long commands with `background=true`, poll status, read output when done
- **Output truncation** — outputs exceeding `max_output_chars` (50K) saved under the restricted plugin output directory; LLM can `read_file` the full output
- **Command audit log** — every command logged with timestamps, machine, exit code, and session ID (`~/.hermes/ssh-tools/command_log.jsonl`)
- **Poll/read_output on ssh_terminal** — check background command status directly from the terminal tool
- **Machine name validation** — names must match `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`; prevents path traversal and glob injection

### Bug fixes

- `ssh_terminal` poll/read_output no longer requires machine/command parameters
- Background process dict uses atomic `pop()` to prevent output loss on concurrent polls
- Output files written with 0o600 permissions in the restricted plugin output directory
- Batch session cleanup now removes orphaned saved output files
- `_write_json` calls `fsync` before `os.replace` to prevent data loss on crash
- Orphaned SSH control socket files removed after session kill
- `slash.py` no longer uses `assert` in production code (stripped with `python -O`)
- Stale help text fixed: `max_output_lines` → `max_output_chars`
- `list_command_log` reads file tail instead of entire file (unbounded memory)
- `_log_command` uses single `os.open` instead of double open TOCTOU
- `prune_closed` handles sessions with naive (non-timezone) timestamps
- `_load_machines`/`_load_sessions` validate JSON structure (dict check)
- Startup cleans orphaned `.tmp` files from the data directory
- Background sessions registered in JSON before process reference stored
- `timeout` parameter coerced to int (string input no longer crashes)
- Tool schemas updated: poll/read_output descriptions mention session_id
- `require()` docstring corrected (non-empty → non-None)

### Security

- Data directory created with 0o700 permissions
- Audit log created with 0o600 permissions
- Output files written with 0o600 permissions
- Machine names validated against `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`
- `_cleanup_output_files` uses `iterdir()` + prefix matching instead of glob (prevents glob injection)

### Documentation

- `llms.txt` added — installation and usage guide for LLMs
- README rewritten with full feature documentation
- CHANGELOG updated

## 0.1.0 — Initial release

- `ssh_terminal` — run commands on remote machines via SSH
- `ssh_machines` — machine registry with aliases, tags, and connectivity tests
- `ssh_sessions` — session tracking with idle detection and cleanup
- `ControlMaster` — persistent SSH connections with 5-minute reuse window
- `bash -c` wrapping with `pipefail` for reliable pipeline exit codes
- `/ssh` slash command for quick machine inspection and command execution
- Background idle checker with configurable timeout
- Atomic JSON writes with temp files + `os.replace`
- Thread-safe operations via locks
- 77 tests covering config, manager, tool handlers, and edge cases
- CI with black, mypy, and pytest across Python 3.11–3.13
