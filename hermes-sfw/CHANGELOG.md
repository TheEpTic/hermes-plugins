# Changelog

## [0.2.10] - 2026-09-04

### Fixed
- Restore compatibility with Hermes Agent 0.21.0 approval internals by importing `_get_approval_mode` from `tools.approval_context`, while retaining fallback support for older Hermes releases.
- Keep SFW commands fail-closed when either approval component is unavailable.

## [0.2.9] - 2026-08-24

### Fixed
- **Timeout orphan regression:** `os.killpg` now uses the process's own PID as the process-group ID directly (set by `start_new_session=True`) instead of calling `os.getpgid`, which can raise `ProcessLookupError` when the leader has already exited. After the SIGTERM grace period, the entire group is unconditionally SIGKILLed regardless of leader state, preventing orphaned child processes from surviving a timeout.
- **Protected workdir prefixes:** `_validate_workdir` now rejects system directories (`/`, `/boot`, `/dev`, `/etc`, `/proc`, `/sys`, `/usr`) as install working directories.
- **Local and git source rejection:** `_validate_command` now refuses install commands whose arguments are local paths (`.`, `./`, `../`, absolute paths), `--path` / `--git` cargo flags, or `git+` / `file:` URLs. These forms bypass the dependency network that sfw inspects and execute arbitrary build/lifecycle scripts directly. Requirements-file flags (`-r ./requirements.txt`, `--requirement=...`) are explicitly exempted.
- **Parser false positives:** `_parse_output` now skips pure-digit tokens (counts like "added 1 package") and common English prose fillers ("by", "from", "for", etc.) after keywords, eliminating the "blocked by firewall" → `blocked=['by']` and "added 1 package" → `installed=['1']` false positives.

### Added
- Regression tests for timeout orphan kill (detached child cleanup).
- Grammar tests for local-path, git/file-source, and cargo `--path`/`--git` rejection, plus requirements-file and registry-URL exemption tests.
- Workdir protection tests for all system prefixes and root `/`.
- Parser noise tests for digit counts and prose stopwords.

## [0.2.8] - 2026-08-18

- Refresh the package and lock metadata for the coordinated release after hermes-ssh pinned cryptography to `50.0.0`, removing stale pre-50 records from the combined dependency graph.

## [0.2.7] - 2026-08-18

- Refresh release metadata and the lockfile so GitHub's dependency graph records the patched `pytest 9.1.1` development dependency instead of the stale vulnerable `9.0.2` snapshot (CVE-2025-71176 / GHSA-6w46-j5rx-g56g).
- Flatten nested conditionals across command parsing, dependency-operation handling, and plugin registration, with AST regression coverage.

## [0.2.6] - 2026-08-18

- Force supported dependency operations sent through Hermes `terminal` to execute via the resolved `sfw` binary using Hermes pre-tool argument rewriting.
- Block unsupported, shell-prefixed, malformed, and path-qualified package-manager commands instead of allowing raw terminal bypasses.
- Add regression coverage for transparent routing, quoting, wrapper/path detection, fail-closed binary discovery, and compound commands.
- Document deferred `sfw` invocation and resolved-binary guidance for foreground and background shells.

## [0.2.5] - 2026-08-15

- Add `SFWManager.diagnose()` returning structured health: which binary was found, which layer it lives in (npm shim vs real binary), the resolved target, and exactly why an install is unhealthy (missing binary, broken shim, version query failure).
- Label version reporting by layer so the npm-package/binary version mismatch is explicit.
- Make the direct-terminal guard message self-explanatory: exact `sfw action=run` invocation, deferred-tool discovery via tool_search/tool_describe, and the resolved binary path for PATH-independent use.
- Include invocation guidance (tool, tool shape, binary path, background-shell shape) in the `status` action response.
- Public-friendly docs parity with hermes-ssh: expanded README (installation, usage, binary discovery, troubleshooting, security framing), CONTRIBUTING, LICENSE, SECURITY cleanup.

## [0.2.4] - 2026-08-15

- Re-resolve the sfw binary on demand instead of caching the path at manager construction, so installs after registration are detected.
- Add the `~/.local/share/pnpm/sfw` shim location to binary discovery.
- Refresh transitive development dependencies (packaging, platformdirs).

## [0.2.3] - 2026-07-26

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
