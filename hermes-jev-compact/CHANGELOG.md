# Changelog

## [Unreleased]

### Changed
- Generic System One endpoint: settings are now `base_url` /
  `api_key_env` (defaults `https://api.typesafe.ai/v1` / `TYPESAFE_API_KEY`)
  and `jev_model` defaults to `jev-latest`. Works with TypeSafe's API or any
  router relaying the same `{model, state, questions}` shape. The old
  `conduit_*` keys and `CONDUIT_NEXUS_API_KEY` default are gone (pre-release
  rename — no migration).
- Transport hardening: redirects refused, response bodies capped at 1 MiB,
  upstream error bodies no longer echoed into logs.

## [0.1.0] - 2026-09-18

### Added
- Initial release: `JevContextCompressor(ContextCompressor)` engine `jev`.
- Single-seam override of `_prune_old_tool_results`: Jev keep/drop scoring via
  conduit2 `/v1/systemone`, validity-checked, with built-in fallback on every
  failure mode (transport, validation, timeout, cancel, missing key).
- Port of fast-jev-compaction state shaping (estimator, fit stages, questions,
  decisions, batching) with parity tests.
- Deterministic demote still runs after a Jev pass (dedup/args/images/stubs).
