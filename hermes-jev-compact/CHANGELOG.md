# Changelog

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
  - 25% minimum-reduction rule enforced in-engine (TS `reductionRatio`):
    low-value Jev passes fall back to the deterministic prune.
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
- Deterministic demote still runs after a Jev pass
  (dedup/args/images/stubs).
