# Changelog

## [0.1.0] - 2026-09-18

### Added
- Initial release: `JevContextCompressor(ContextCompressor)` engine `jev`.
- Single-seam override of `_prune_old_tool_results`: Jev keep/drop scoring via
  conduit2 `/v1/systemone`, validity-checked, with built-in fallback on every
  failure mode (transport, validation, timeout, cancel, missing key).
- Port of fast-jev-compaction state shaping (estimator, fit stages, questions,
  decisions, batching) with parity tests.
- Deterministic demote still runs after a Jev pass (dedup/args/images/stubs).
