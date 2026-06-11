# Changelog

## 0.1.0 — Initial release

- `sfw` tool — run package manager commands through Socket Firewall Free
- `sfw status` — check installation and version
- Command prefix allowlist (npm, yarn, pnpm, pip, cargo, etc.)
- Output parsing for blocked/installed package indicators
- Working directory validation with path traversal prevention
- Output truncation at 10K chars with size notes
- Timeout protection (default 5 minutes)
- `OSError` errno mapping for clean error messages
- `shlex.split()` command parsing with error handling
- Tests covering status, run, validation, parsing, timeout, and edge cases
