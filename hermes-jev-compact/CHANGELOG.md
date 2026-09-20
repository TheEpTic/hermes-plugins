# Changelog

## [Unreleased]

### Added

- Result head excerpts in the Jev state: each candidate's note now carries
  the first 500 chars of its result (`ok, N chars, head: ... (truncated)`)
  so Jev scores content, not size alone. New `result_excerpt_chars` setting
  (default 500; 0 restores size-only notes). Trust-boundary note in
  SECURITY.md.
- Error-aware keep: `error_keep_threshold` (default 0.25) gives error
  results their own lower keep bar — tracebacks survive unless Jev is
  confident they are stale.
- Reduction-gate knob: `min_reduction_ratio` (default 0.10, was a hardcoded
  0.25). The hermes summary always runs, so the gate only picks the phase-1
  author — selective-but-small Jev passes now commit instead of falling back.
- Per-decision telemetry: one `jev decision:` log line per scored unit
  (tool, result size, error flag, both scores, action) plus split counters
  `jev_kept_units` / `jev_truncate_units` / `jev_drop_units`; the gate
  fallback logs its achieved ratio.
- Non-demotion host hygiene after a Jev pass: dedup, tool-call arg
  truncation, and image retire now run on Jev output (each guarded — host
  drift keeps Jev output, never fails the prune).

### Changed

- Candidate floor `min_result_chars` default 8000 → 2000: short-but-critical
  results (test failures, errors) now reach Jev instead of bypassing it.
- Prompt reword (deliberate TS divergence): the state context no longer
  claims tools can "always" be re-run, and the keep question asks for
  likely-future-need instead of irreproducibility. Hermes re-runs cost
  time/API spend and may have side effects.

### Fixed

- README no longer claims the deterministic demote passes run after a Jev
  pass — they don't (replacement, not augmentation); only the non-demotion
  hygiene passes do now.
- Review findings (2 independent falsify-mode reviewers):
  - Excerpts redacted with the host compaction rule before leaving the host.
  - Post-Jev hygiene recomputes its boundary on the post-Jev list (a stale
    pre-Jev boundary could reach into the protected tail after removals).
  - Cancellation consulted after hygiene, before commit.
  - New `jev_hygiene_units` counter: return count is
    `jev_pruned_units + jev_hygiene_units`.
  - Stale Unreleased notes below (25% rule, demote-after-Jev) marked as
    superseded history — see the entries above.

## [0.1.3] - 2026-09-18

### Added

- `endpoint_path` setting (default `/systemone`): the request path appended
  to `base_url`. Lets the plugin talk to any router speaking the same
  `{model, state, questions}` → `{answers}` shape on a different path —
  e.g. OpenRouter's Decisions API (`base_url: https://openrouter.ai`,
  `endpoint_path: /api/alpha/decisions`, `jev_model: typesafe/jev-1.13`).
  Only plain absolute paths are accepted; anything else fails closed to
  the default. Existing configs are unaffected.

## [0.1.2] - 2026-09-18

### Fixed

- Context engine survived plugin hot-reloads. The host clears its engine
  slot during a plugin reload (`unload`/`discover_and_load(force=True)`) then
  re-invokes each plugin's `register()`. The module-global `_registered` latch
  survived that clear, so Jev skipped re-registering and the slot stayed empty —
  `context.engine: jev` resolved to nothing and silently fell back to the
  built-in compressor. Registration is now keyed to the host's live slot
  (read via `ctx._manager`), so a cleared slot is re-filled on the next
  `register()` call.

## [0.1.1] - 2026-09-18

### Fixed

- Cleartext HTTP allowed for private-LAN hosts: RFC1918, Tailscale CGNAT
  (100.64/10), ULA/link-local IPv6, plus loopback. The 0.1.0 loopback-only
  guard failed closed against every LAN self-hosted router (conduit2 over
  `http://192.168.x.x`), silently disabling Jev everywhere but localhost.
  Public IPs, DNS names, link-local 169.254/16 (cloud metadata), and
  unspecified addresses still require HTTPS.

## [Unreleased]

### Fixed

- Bug-hunt wave (5 review agents, all findings verified before fixing):
  - UTF-16-unit parity in the estimator/truncate/abridge — emoji-heavy text
    was under-counted ~2x vs the TS reference.
  - TS `_compact_call` port: `key=value` per entry with raw strings (was
    whole-object JSON).
  - ~~25% minimum-reduction rule enforced in-engine (TS `reductionRatio`):
    low-value Jev passes fall back to the deterministic prune.~~
    SUPERSEDED — now the `min_reduction_ratio` knob, default 0.10; see the
    current Unreleased section above.
  - Duplicate tool-result ids skip scoring (was: last-row-wins collapse
    while pruning every row sharing the id).
  - Out-of-order pairs (result before its call) are never candidates; the
    validity gate rejects them plus malformed top-level rows.
  - Multimodal text parts are flattened into state (host `_part_text`
    parity); image/file parts contribute nothing instead of voiding the row.
  - Multimodal assistant rows survive call-stripping (were dropped as
    "payload-empty").
  - `apply_decisions_openai` fails closed on duplicate/conflicting decision
    input instead of last-wins.
  - Transport: base URLs with query strings join correctly, fragments
    refused, empty userinfo refused, malformed URLs raise JevError,
    timeout must be finite + positive, NaN/Infinity bodies rejected,
    `noul` answers range-checked to [0,1], status code is the authority
    over the legacy `ok` flag.
  - Engine inherits host `quiet_mode` / `protect_last_n`; `keep_threshold`
    clamped to [0,1]; question-token cache bounded (4096 entries, tuple
    keys); `register()` latches only after success.

### Changed

- Generic System One endpoint: settings are now `base_url` / `api_key_env`
  (defaults `https://api.typesafe.ai/v1` / `TYPESAFE_API_KEY`) and `jev_model`
  defaults to `jev-latest`. Works with TypeSafe's API or any router relaying
  the same `{model, state, questions}` shape. The old `conduit_*` keys and
  `CONDUIT_NEXUS_API_KEY` default are gone (pre-release rename — no
  migration).
- Transport hardening: redirects refused, response bodies capped at 1 MiB,
  upstream error bodies no longer echoed into logs.

## [0.1.0] - 2026-09-18

### Added

- Initial release: `JevContextCompressor(ContextCompressor)` engine `jev`.
- Single-seam override of `_prune_old_tool_results`: Jev keep/drop scoring
  via `/v1/systemone`, validity-checked, with built-in fallback on every
  failure mode (transport, validation, timeout, cancel, missing key).
- Port of fast-jev-compaction state shaping (estimator, fit stages,
  questions, decisions, batching) with parity tests.
- ~~Deterministic demote still runs after a Jev pass
  (dedup/args/images/stubs).~~
  SUPERSEDED — only the NON-demotion passes (dedup, arg truncation, image
  retire) run after a Jev pass; demote/pressure would munge Jev's keeps.
  See the current Unreleased section above.
