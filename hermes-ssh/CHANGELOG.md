# Changelog

## [0.4.12] - 2026-09-23

### Fixed
- Runtime dependency is now `cryptography>=50.0.0,<51` instead of an exact pin. 0.4.11 required `==50.0.1`, which conflicts with Hermes Agent's own `cryptography==50.0.0` pin and left installs with a broken `pip check`. The floor still excludes the vulnerable pre-50 releases.

## [0.4.11] - 2026-09-23

### Security
- Audit redaction now covers prefixed credential variables (`PGPASSWORD=`, `MYSQL_PWD=`, `AWS_SECRET_ACCESS_KEY=`), `curl -u user:pass` / `--user`, `sshpass -p`, `mysql -pSECRET`, and `user:pass@` in any URL scheme (`postgresql://`, `redis://`, ...), including passwords that contain `@`. Previously these were logged in clear text. Redaction remains pattern-based; README/SECURITY document the positional-secret limit.
- Host validation rejects `host:alias` / `host:port` forms: a host containing `:` must now be an IPv6 literal. Bracketed IPv6 is normalised to the bare form OpenSSH expects. Absolute FQDNs with a trailing dot (`example.com.`) remain valid.

### Fixed
- Transfers no longer blanket-block every path containing `.hermes`: inside a Hermes home (`~/.hermes`, `profiles/<name>/`, `$HERMES_HOME`) the non-secret working trees `cache/` (incl. the agent's `cache/scratch` TMPDIR), `images/`, `audio_cache/`, and `browser_screenshots/` are allowed; everything else there stays blocked. The test suite now passes when run under a Hermes-managed TMPDIR.
- `register()` no longer latches after the first call. A host force-reload of an entry-point (pip) install previously re-imported nothing and skipped registration, so every `ssh_*` tool and `/ssh` vanished; re-registration now reuses the live manager and its sessions.
- Plugin manifest version matches the package (was stuck at 0.4.9); a test pins them together.

### Changed
- README architecture tree reflects the real module layout (`audit`, `exec`, `helpers`, `registry`, `sessions`, `validate`; no `utils.py`).
- Dev dependencies: mypy 2.3.1, pytest 9.1.1; runtime `cryptography==50.0.1`. Dropped dead `tests.*` mypy override and paramiko/asyncssh warning filters (neither library is a dependency).

## [0.4.10] - 2026-09-18

### Changed
- Internal refactor: split the manager into focused execution, registry, session, audit, and validation modules with no behaviour change; dropped dead one-shot migrate script.

### Fixed
- Remote system-credential denylist compares by path segment, also refusing `/etc//shadow` and `/etc/shadow/` which resolve to `/etc/shadow` remotely (plugin-catalog admission fix).
- Plugin manifest declares the `cryptography==50.0.0` runtime dependency so directory installs get it.

## [0.4.9] - 2026-09-12

### Fixed
- Enforce `ssh_terminal` command timeouts for background sessions with process-group termination and deterministic timeout results.
- Register background process ownership before publishing persisted sessions, and record final background exit codes in the audit log.
- Make idle-checker shutdown/restart interruptible and bound SFTP subprocesses to the requested timeout instead of adding an extra five seconds.

## [0.4.8] - 2026-09-05

### Fixed
- **Order-independent approval loading:** `check_approval` now resolves the Hermes approval functions on each call instead of at plugin import time. This removes an order-dependent circular-import failure when plugins are imported in sequence, and re-validates availability on every check.
- **SSH denial completeness:** an `approval_required` result now always carries `approved: False`, matching the SFW plugin's contract.

### Added
- Regression tests for lazy approval loading: fail-closed when Hermes approval is unavailable, per-call re-resolution, `approvals.mode=off` bypass, and denial shape (both `hermes-ssh` and `hermes-sfw`).

## [0.4.7] - 2026-09-05

### Security
- Pin the development test dependency to pytest 9.0.3, the first release fixing CVE-2025-71176 insecure temporary-directory handling.
- Record the security dependency refresh in the release notes.

## [0.4.6] - 2026-09-04

### Fixed
- Restore compatibility with Hermes Agent 0.21.0 approval internals by importing `_get_approval_mode` from `tools.approval_context`, while retaining fallback support for older Hermes releases.
- Keep SSH commands fail-closed when either approval component is unavailable.

## [0.4.5] - 2026-08-24

### Fixed
- **Schema deduplication:** `poll` and `read_output` are now exposed only on `ssh_sessions` (the canonical session surface), removed from `ssh_terminal`. The `prune` action was removed from `ssh_sessions` (the idle checker auto-prunes every 10 cycles). `machine` and `command` are now always required by `ssh_terminal`.
- **Sync-session contract:** synchronous `run_command` no longer returns a phantom `session_id` that was never registered as a session. Background commands continue to return a real `session_id`.
- **Migration permissions:** `command_log.jsonl` copied during `migrate.py` now uses `0o600` instead of inheriting the default umask.

### Changed
- `ssh_terminal` description now directs agents to `ssh_sessions` for poll/read.
- `ssh_sessions` description clarified ("cleanup idle").

## [0.4.4] - 2026-08-18

- Pin cryptography to `50.0.0`, matching Hermes Agent 0.20.4 and excluding the vulnerable pre-50 release range reported by GitHub (CVE-2026-69248 and related advisories).

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
