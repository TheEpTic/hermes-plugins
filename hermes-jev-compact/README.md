# hermes-jev-compact

Smarter context compression for Hermes Agent: stale tool calls are scored by
[TypeSafe Jev](https://docs.typesafe.ai/) — a fast decision model built for
exactly this kind of keep-or-drop judgment — instead of being pruned by age
alone. What Jev says still matters stays; the dead weight goes.

Opt-in per profile (`context.engine: jev`). Worst case is exactly the built-in
behavior: any Jev failure — transport, validation, timeout, cancel, missing
key — falls back to the inherited deterministic prune.

## why

The built-in compressor prunes old tool results blindly: beyond the protected
tail, everything is truncated by position. That is safe, but it throws away
results the conversation still depends on (a test failure three turns back, the
file listing that motivated the current edit) while keeping verbose output
nobody will ever reference again.

Jev fixes the targeting. For every stale tool call/result unit it answers two
calibrated questions — *does the call still matter? does its full output still
matter?* — against the whole conversation as state. The result:

- **fewer broken continuations** — results the next step actually needs survive
  compression instead of being truncated by age.
- **smaller contexts** — high-confidence dead weight (passing test logs,
  superseded listings, retried commands) is dropped entirely, not kept as
  stubs.
- **cheap judgments, not LLM summaries** — Jev returns probabilities in
  ~70–500 ms at a fraction of a cent per prune; no generative model is
  consulted during the prune path.

## how it works

`JevContextCompressor` subclasses the built-in `ContextCompressor` and
overrides exactly one seam — `_prune_old_tool_results` (full-compression
phase 1). The hot proactive path (`prune_tool_results_only`, documented
deterministic/no-LLM) is untouched: it bypasses Jev entirely.

One prune, end to end:

1. **Candidates.** Paired tool call + result before the prune boundary, above
   the char floor. Never candidates: system rows, index 0, the protected
   tail, unpaired calls, unusable result shapes (bytes/numbers), duplicate or
   out-of-order pairs (ambiguous address — fail closed).
2. **State.** The whole transcript with result bodies replaced by short notes
   (`ok, 9000 chars (omitted)`) is fitted into `max_state_tokens` through a
   shrink ladder: cap call inputs → abridge long texts → collapse old texts →
   compact old calls → drop text-only rows → merge call runs. Pinned rows
   (index 0 + recent tail) shrink last.
3. **Questions.** Two `noul` (yes/no probability) questions per call — *keep
   the call? keep its full result?* — batched into `max_request_tokens` and
   asked sequentially (so cancellation stops between asks).
4. **Decisions.** `keep` / `drop_result` (truncate to a head + marker) /
   `drop_call` (remove result rows, strip the call). Pinned calls always keep.
5. **Commit gates.** Output is validity-checked (no orphans either way, no
   duplicates, no out-of-order pairs, clean row shapes) and must shrink the
   transcript by ≥25% (TS `reductionRatio` rule) — otherwise the deterministic
   prune runs instead. The deterministic demote passes (dedup, arg truncation,
   image retire, stubs) still run after a Jev pass, so nothing the built-in
   compressor did is lost.

State shaping is a port of
[tamara/fast-jev-compaction](https://github.com/tamara/fast-jev-compaction)
(MIT) — see THIRD_PARTY_NOTICES.md. Deliberate divergences from upstream:
sequential batches (cancellation), OpenAI row adaptation, and the 25% rule
enforced in-engine rather than by the caller.

## install

Works with any System One-compatible endpoint. TypeSafe's own API is the
reference: get a key at
[console.typesafe.ai](https://console.typesafe.ai/settings/keys), put it in
`~/.hermes/.env` as `TYPESAFE_API_KEY`. Self-hosted routers relaying the same
`{model, state, questions}` shape work too — just point `base_url` at them.

```bash
/path/to/hermes-python -m pip install hermes-jev-compact
hermes plugins enable hermes-jev-compact --no-allow-tool-override
```

Then opt in per profile and `/reset`:

```yaml
context:
  engine: jev
plugins:
  entries:
    hermes-jev-compact:
      settings:
        base_url: https://api.typesafe.ai/v1   # any Decisions-shaped endpoint
        endpoint_path: /systemone              # path appended to base_url
        api_key_env: TYPESAFE_API_KEY              # env var holding the key
        jev_model: jev-latest
        keep_threshold: 0.5        # noul >= this keeps the unit
        max_state_tokens: 25000    # transcript budget per request
        max_request_tokens: 30000  # state + questions budget
        truncate_head_chars: 300   # kept head of a dropped result
        request_timeout_s: 30
        min_result_chars: 8000     # results below this never become candidates
```

`endpoint_path` lets the plugin talk to any router speaking the
`{model, state, questions}` → `{answers}` shape even when its path differs
from TypeSafe's `/systemone`. Only plain absolute paths are accepted
(anything else fails closed to the default), so this knob can never turn
the request into a different host, add credentials, or smuggle a query.

OpenRouter example (Jev via their Decisions API — same input/output
shape, same $0.042/MTok input / $0 output pricing):

```yaml
plugins:
  entries:
    hermes-jev-compact:
      settings:
        base_url: https://openrouter.ai
        endpoint_path: /api/alpha/decisions
        api_key_env: OPENROUTER_API_KEY
        jev_model: typesafe/jev-1.13   # OpenRouter route id, not jev-latest
```

`compressor` (default) bypasses plugins entirely; `jev` only activates when
named. To disable: `hermes config set context.engine compressor` (and
optionally `hermes plugins disable hermes-jev-compact`), then `/reset`.

## observability

Per-agent counters live on the compressor: `jev_calls` (requests made),
`jev_pruned_units` (units dropped/truncated), `jev_fallbacks` (times the
built-in prune ran instead). A successful pass logs its score/drop counts and
fit stage; every fallback logs its reason.

## development

```bash
uv sync --extra dev --locked
uv run pytest
uv run black --check src tests
uv run mypy src
```
