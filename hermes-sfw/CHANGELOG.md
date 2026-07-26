# Changelog

## Unreleased

- Block supported dependency operations sent directly through the terminal and route agents to `sfw`.
- Allow the direct-terminal guard to be disabled with `HERMES_SFW_ENFORCE_DIRECT=off`.
- Resolve `__version__` from installed package metadata.

## [0.2.3] - 2026-07-24

- Update development dependencies to mypy 2.3.0 and pytest 9.1.1.

## [0.2.2] - 2026-07-13

- Align the packaged Hermes manifest with the release version.

## [0.2.1] - 2026-07-13

- Fix the release artifact path so the tagged build can reach PyPI.

## [0.2.0] - 2026-07-13

- Route package-manager commands through Hermes dangerous-command approval checks.
- Fail closed when the approval system is unavailable.
- Block package-manager subcommands that directly execute arbitrary commands.
- Validate `verbose` as a strict boolean.
- Add packaged-plugin discovery metadata and manifest tool declarations.

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
