# Changelog

## 0.2.0 — Bug hunt, security hardening, documentation

### New features

- **Background commands** — run long commands with `background=true`, poll status, read output when done
- **Output truncation** — outputs exceeding `max_output_chars` (50K) saved to `/tmp/` files; LLM can `read_file` the full output
- **Command audit log** — every command logged with timestamps, machine, exit code, and session ID (`data/command_log.jsonl`)
- **Poll/read_output on ssh_terminal** — check background command status directly from the terminal tool
- **Machine name validation** — names must match `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`; prevents path traversal and glob injection

### Bug fixes

- `ssh_terminal` poll/read_output no longer requires machine/command parameters
- Background process dict uses atomic `pop()` to prevent output loss on concurrent polls
- `/tmp` output files written with 0o600 permissions (were 0o644, world-readable)
- Batch session cleanup now removes orphaned `/tmp` output files
- `_write_json` calls `fsync` before `os.replace` to prevent data loss on crash
- Orphaned SSH control socket files removed after session kill
- `slash.py` no longer uses `assert` in production code (stripped with `python -O`)
- Stale help text fixed: `max_output_lines` → `max_output_chars`
- `list_command_log` reads file tail instead of entire file (unbounded memory)
- `_log_command` uses single `os.open` instead of double open TOCTOU
- `prune_closed` handles sessions with naive (non-timezone) timestamps
- `_load_machines`/`_load_sessions` validate JSON structure (dict check)
- Startup cleans orphaned `.tmp` files from data directory
- Background sessions registered in JSON before process reference stored
- `timeout` parameter coerced to int (string input no longer crashes)
- Tool schemas updated: poll/read_output descriptions mention session_id
- `require()` docstring corrected (non-empty → non-None)

### Security

- Data directory created with 0o700 permissions
- Audit log created with 0o600 permissions
- `/tmp` output files written with 0o600 permissions
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
- Atomic JSON writes with temp files for crash safety
- Thread-safe operations via locks
- 77 tests covering config, manager, tool handlers, and edge cases
- CI with ruff, mypy, and pytest across Python 3.11–3.13
