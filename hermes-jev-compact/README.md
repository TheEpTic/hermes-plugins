# hermes-jev-compact

Jev-powered smart tool-prune context engine for Hermes Agent.

`JevContextCompressor` subclasses the built-in `ContextCompressor` and overrides
only `_prune_old_tool_results`: stale tool call/result units are scored by
TypeSafe Jev through conduit2 `/v1/systemone` (keep / truncate / drop by
probability). Any Jev failure — transport, validation, timeout, cancel,
missing key — falls back to the inherited deterministic prune. Worst case is
exactly built-in behavior.

Requires conduit2 with the `typesafe:jev-latest` systemone route (key in
`~/.hermes/.env` as `CONDUIT_NEXUS_API_KEY`).

## install

```bash
/path/to/hermes-python -m pip install hermes-jev-compact
hermes plugins enable hermes-jev-compact --no-allow-tool-override
```

Then opt in per profile (`context.engine: jev`) and `/reset`:

```yaml
context:
  engine: jev
plugins:
  entries:
    hermes-jev-compact:
      settings:
        conduit_base_url: http://127.0.0.1:8765/v1
        conduit_api_key_env: CONDUIT_NEXUS_API_KEY
        jev_model: typesafe:jev-latest
        keep_threshold: 0.5
        max_state_tokens: 25000
        max_request_tokens: 30000
        truncate_head_chars: 300
        request_timeout_s: 30
        min_result_chars: 8000
```

`compressor` (default) bypasses plugins entirely; `jev` only activates when named.

## how it works

1. Both prune paths (proactive `prune_tool_results_only` and full `compress`
   phase 1) call `_prune_old_tool_results` — the single overridden seam.
2. Candidates: paired tool call+result before the prune boundary, above the
   char floor. System rows, index 0, the protected tail, unpaired calls, and
   non-string (multimodal) results are never candidates.
3. The transcript (results replaced by `ok, N chars (omitted)`) is fitted into
   `max_state_tokens`, two `noul` questions per call are batched into
   `max_request_tokens`, and Jev answers keep/drop.
4. Dropped results keep a 300-char head + note; dropped calls remove the result
   rows and strip the call (payload-empty assistant rows go too, mirroring the
   host repair). Output is validity-checked; on any violation the built-in
   prune runs instead.
5. After a successful Jev pass the deterministic demote still runs on the
   output, so dedup/arg-truncation/image-retire/stub passes are preserved.

State shaping is a port of
[tamara/fast-jev-compaction](https://github.com/tamara/fast-jev-compaction)
(MIT) — see THIRD_PARTY_NOTICES.md.

## development

```bash
uv sync --extra dev --locked
uv run pytest
uv run black --check src tests
uv run mypy src
```
