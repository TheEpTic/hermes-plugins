"""JevContextCompressor: ContextCompressor subclass with a Jev-scored phase 1.

Host-seam contract (see agent/context_compressor.py):
- prune_tool_results_only (host :2987) is DOCUMENTED deterministic/no-LLM and is
  called on the hot post-tool path (turn_preflight.py:364). This engine KEEPS
  that contract: prune_tool_results_only is overridden to call the base
  implementation directly, bypassing Jev entirely (no network, no secret read).
- _prune_old_tool_results is the full-compression phase-1 seam (host :4784).
  Jev scoring REPLACES the base prune there: on success exactly one of the two
  runs (never jev-then-base), so base passes never re-munge jev output and
  counts never double-report. On any jev failure the base prune runs instead
  with identical args.
"""

from __future__ import annotations

import contextlib
import copy
import inspect
import logging
import math
import os
from typing import Any

from .adapter import collect_candidates, is_well_formed_tool_call, to_internal
from .asker import JevAsker
from .protocol import JevCallAnswer, JevCallDecision, JevError, JevOptions, JevToolCall
from .pruner import (
    apply_decisions_openai,
    batch_calls,
    decide_call,
    fit_state,
    questions_for_batch,
)
from .request import noul_answer

logger = logging.getLogger(__name__)

try:  # pragma: no cover - host import; CI uses the fallback below
    from agent.context_compressor import ContextCompressor
except Exception:  # pragma: no cover

    class ContextCompressor:  # type: ignore[no-redef]
        """Minimal stand-in so the override logic is testable without hermes installed."""

        def __init__(self, model: str, **kwargs: Any) -> None:
            self.model = model
            self.quiet_mode = bool(kwargs.get("quiet_mode", False))
            self.protect_last_n = int(kwargs.get("protect_last_n", 20))

        def _prune_boundary(
            self,
            result: list[dict[str, Any]],
            protect_tail_count: int,
            protect_tail_tokens: int | None,
        ) -> int:
            return len(result) - protect_tail_count

        def _prune_old_tool_results(
            self,
            messages: list[dict[str, Any]],
            protect_tail_count: int,
            protect_tail_tokens: int | None = None,
            min_prune_chars: int = 200,
        ) -> tuple[list[dict[str, Any]], int]:
            return messages, 0


# jev-only knobs: (attribute, setting key, default). Immutable policy, deepcopy-safe.
# Any /v1/systemone-compatible endpoint works here — TypeSafe's API
# (https://api.typesafe.ai/v1) is the reference; self-hosted routers that relay
# the same shape are fine too.
_JEV_KNOBS: tuple[tuple[str, str, Any], ...] = (
    ("jev_base_url", "base_url", "https://api.typesafe.ai/v1"),
    ("jev_api_key_env", "api_key_env", "TYPESAFE_API_KEY"),
    ("jev_model", "jev_model", "jev-latest"),
    ("jev_keep_threshold", "keep_threshold", 0.5),
    ("jev_max_state_tokens", "max_state_tokens", 25000),
    ("jev_max_request_tokens", "max_request_tokens", 30000),
    ("jev_truncate_head_chars", "truncate_head_chars", 300),
    ("jev_request_timeout_s", "request_timeout_s", 30.0),
    ("jev_min_result_chars", "min_result_chars", 8000),
)
_JEV_DEFAULTS = {attr: default for attr, _, default in _JEV_KNOBS}


def _resolve_secret(key_env: str) -> str:
    """Call-time secret read: agent.secret_scope when importable, else os.environ."""
    try:
        from agent.secret_scope import get_secret

        value = get_secret(key_env)
    except Exception:
        value = os.environ.get(key_env)
    # BaseException (KeyboardInterrupt/SystemExit) intentionally propagates —
    # swallowing process-control signals to attempt a prune would be wrong.
    return value if isinstance(value, str) else ""


class JevContextCompressor(ContextCompressor):  # type: ignore[misc]
    """Built-in compressor with Jev keep/drop scoring inside the prune seam."""

    # jev-only knobs (declared here so type-checkers see them; set in __init__).
    jev_base_url: str
    jev_api_key_env: str
    jev_model: str
    jev_keep_threshold: float
    jev_max_state_tokens: int
    jev_max_request_tokens: int
    jev_truncate_head_chars: int
    jev_request_timeout_s: float
    jev_min_result_chars: int
    jev_calls: int
    jev_pruned_units: int
    jev_fallbacks: int

    def __init__(self, model: str, **kwargs: Any) -> None:
        base_params = set(inspect.signature(ContextCompressor.__init__).parameters) - {"self"}
        super().__init__(model, **{k: v for k, v in kwargs.items() if k in base_params})
        for attr, _, default in _JEV_KNOBS:
            setattr(self, attr, kwargs.get(attr, default))
        self.jev_calls = 0
        self.jev_pruned_units = 0
        self.jev_fallbacks = 0
        # The summary LLM only ever runs inside the host's compress(), which we
        # inherit — but the shared plugin singleton must never RETAIN the chat
        # api key: the deepcopy at agent_init.py:1785-1801 would carry it into
        # every child, and register() builds us with model=jev-latest anyway.
        # Strip it; the host re-supplies it via update_model() per agent.
        with contextlib.suppress(Exception):
            self.api_key = ""

    def update_model(self, *args: Any, **kwargs: Any) -> None:
        # Host calls this per agent with the REAL chat key (agent_init
        # :1849-1852: summary + context-length resolution need it). The base
        # stores it on self.api_key and the summary path reads it back at
        # call time (:3357), so stripping here would BREAK the summary LLM.
        # Secret posture instead: the deepcopy below never copies api_key,
        # and register() never passes a real key (model=jev-latest, key="").
        # The key lives only on the per-agent deepcopies, exactly like the
        # built-in compressor — never on the shared plugin singleton.
        super().update_model(*args, **kwargs)

    @property
    def name(self) -> str:
        return "jev"

    def __deepcopy__(self, memo: dict[int, Any]) -> JevContextCompressor:
        # Allowlist copy: host installs uncopyable runtime state on the shared
        # singleton (_session_db handle, _compression_cancelled_check callback
        # bound to the parent agent, possibly locks). Copying the callback
        # would also pin the child to the PARENT's cancellation generation.
        # So: fresh instance via the real __init__ (correct base invariants),
        # then copy only jev policy + safe scalar/counter state.
        cls = self.__class__
        fresh = cls.__new__(cls)
        memo[id(self)] = fresh
        try:
            params = set(inspect.signature(ContextCompressor.__init__).parameters) - {"self"}
        except (TypeError, ValueError):
            params = set()
        base_kwargs = {
            k: copy.deepcopy(v, memo)
            for k, v in self.__dict__.items()
            if k in params and k not in {"api_key"}
        }
        # model is required positional on the base; fall back to current value.
        model = base_kwargs.pop("model", getattr(self, "model", ""))
        try:
            fresh.__init__(model, **base_kwargs)  # type: ignore[misc]
        except Exception:
            # Last resort: shallow-copy scalars only, never callbacks/handles.
            for key, value in self.__dict__.items():
                if key.startswith("_compression_cancelled") or key in {
                    "_session_db",
                    "api_key",
                }:
                    continue
                try:
                    fresh.__dict__[key] = copy.deepcopy(value, memo)
                except Exception:
                    continue
            fresh.__dict__.setdefault("_session_db", None)
            fresh.__dict__.setdefault("_session_id", "")
            fresh.__dict__["api_key"] = ""
            return fresh
        for attr, _, default in _JEV_KNOBS:
            try:
                fresh.__dict__[attr] = copy.deepcopy(getattr(self, attr, default), memo)
            except Exception:
                fresh.__dict__[attr] = default
        for counter in ("jev_calls", "jev_pruned_units", "jev_fallbacks"):
            fresh.__dict__[counter] = int(getattr(self, counter, 0) or 0)
        fresh.__dict__["api_key"] = ""
        return fresh

    # -- seam ---------------------------------------------------------------

    def _cancelled(self) -> bool:
        check = getattr(self, "_compression_cancelled_check", None)
        if not callable(check):
            return False
        try:
            return bool(check())
        except Exception:
            logger.debug("jev cancellation consult failed", exc_info=True)
            return False

    def _jev_options(self) -> JevOptions:
        threshold = self.jev_keep_threshold
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            threshold = 0.5
        if not math.isfinite(threshold):
            threshold = 0.5
        return JevOptions(
            keep_threshold=min(1.0, max(0.0, threshold)),
            max_state_tokens=max(1, int(self.jev_max_state_tokens)),
            max_request_tokens=max(1, int(self.jev_max_request_tokens)),
            truncate_head_chars=max(0, int(self.jev_truncate_head_chars)),
            request_timeout_s=max(1.0, float(self.jev_request_timeout_s)),
            min_result_chars=max(0, int(self.jev_min_result_chars)),
        )

    def _ask_batches(
        self,
        asker: JevAsker,
        state: Any,
        batches: list[list[JevToolCall]],
        options: JevOptions,
    ) -> list[JevCallDecision]:
        # NOTE: deliberate drift from TS (compact.ts:275-278 Promise.all):
        # batches run SEQUENTIALLY so cancellation can stop between asks and
        # the shared JevAsker deadline applies in order. Same questions, same
        # state per batch; only latency/parallelism differs.
        answers: dict[str, JevCallAnswer] = {}
        for batch in batches:
            if self._cancelled():
                raise JevError("compression cancelled before jev batch")
            questions = questions_for_batch(batch)
            raw = asker.ask(state, questions)
            # Cancellation may have landed mid-flight: never apply answers the
            # host no longer wants — fall back to the deterministic prune.
            if self._cancelled():
                raise JevError("compression cancelled during jev batch")
            for call in batch:
                answers[call.id] = JevCallAnswer(
                    keep_call=noul_answer(raw, f"call_{call.id}"),
                    keep_result=noul_answer(raw, f"result_{call.id}"),
                )
        decisions = []
        for call in (c for b in batches for c in b):
            if call.id not in answers:
                raise JevError(f"missing jev answers for {call.id}")
            decisions.append(decide_call(call, answers[call.id], options.keep_threshold))
        return decisions

    def _jev_prune(
        self,
        messages: list[dict[str, Any]],
        protect_tail_count: int,
        protect_tail_tokens: int | None,
        min_prune_chars: int,
        asker_factory: Any = None,
    ) -> tuple[list[dict[str, Any]] | None, int]:
        """Attempt jev scoring. Returns (None, 0) when the caller must use super()."""
        options = self._jev_options()
        boundary = self._prune_boundary(messages, protect_tail_count, protect_tail_tokens)
        candidates = collect_candidates(
            messages, boundary, max(min_prune_chars, options.min_result_chars)
        )
        if not candidates or self._cancelled():
            return None, 0
        key = _resolve_secret(self.jev_api_key_env)
        if not key:
            logger.debug("jev: no api key (%s); built-in prune", self.jev_api_key_env)
            return None, 0
        try:
            fitted = fit_state(to_internal(messages), candidates, options)
            batches = batch_calls(candidates, int(fitted["tokens"]), options.max_request_tokens)
            asker = (
                asker_factory(self.jev_base_url, key, self.jev_model, options)
                if asker_factory is not None
                else JevAsker(self.jev_base_url, key, self.jev_model, options.request_timeout_s)
            )
            decisions = self._ask_batches(asker, fitted["state"], batches, options)
        except JevError as exc:
            logger.info("jev prune failed (%s); built-in prune", exc)
            return None, 0
        except (ValueError, TimeoutError) as exc:
            logger.info("jev prune skipped (%s); built-in prune", exc)
            return None, 0
        except Exception:
            logger.warning("jev prune crashed; built-in prune", exc_info=True)
            return None, 0
        try:
            applied = apply_decisions_openai(
                messages, decisions, candidates, options.truncate_head_chars
            )
        except JevError as exc:
            logger.warning("jev output ambiguous (%s); built-in prune", exc)
            return None, 0
        pruned_units = sum(1 for d in decisions if d.action != "keep")
        if pruned_units == 0 or applied == messages or not _valid_openai_sequence(applied):
            if applied != messages and pruned_units:
                logger.warning("jev output failed validity; built-in prune")
            return None, 0
        # TS README parity (reductionRatio < 0.25 → "not worth it"): a jev pass
        # that barely shrinks the transcript is worse than the deterministic
        # prune — it spent a network round-trip to keep everything. Measure in
        # chars (same unit TS uses) and fall back under 25% reduction.
        chars_before = sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
        chars_after = sum(len(str(m.get("content", ""))) for m in applied if isinstance(m, dict))
        if chars_before > 0 and (chars_before - chars_after) / chars_before < 0.25:
            logger.info("jev reduction under 25%%; built-in prune")
            return None, 0
        # Final cancellation consult before committing counters/output: an ask
        # that finished just as the host cancelled must not mutate state.
        if self._cancelled():
            logger.info("jev prune cancelled before commit; built-in prune")
            return None, 0
        self.jev_calls += len(batches)
        self.jev_pruned_units += pruned_units
        if not self.quiet_mode:
            logger.info(
                "jev prune: %d call(s) scored in %d request(s), %d dropped/truncated (%s)",
                len(candidates),
                len(batches),
                pruned_units,
                fitted["stage"],
            )
        return applied, pruned_units

    def prune_tool_results_only(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        # KEEP the host's deterministic/no-LLM contract (host :2987, hot path
        # turn_preflight.py:364): bypass Jev entirely — straight to base.
        # Keyword form: survives a host reorder; name is the stable contract.
        out, n = super().prune_tool_results_only(messages, current_tokens=current_tokens)
        return list(out), int(n)

    def _prune_old_tool_results(
        self,
        messages: list[dict[str, Any]],
        protect_tail_count: int,
        protect_tail_tokens: int | None = None,
        min_prune_chars: int = 200,
    ) -> tuple[list[dict[str, Any]], int]:
        try:
            applied, count = self._jev_prune(
                messages, protect_tail_count, protect_tail_tokens, min_prune_chars
            )
        except Exception:
            logger.warning("jev prune path failed; built-in prune", exc_info=True)
            applied, count = None, 0
        if applied is not None and count > 0:
            # Jev REPLACES the base prune (never jev-then-base): exactly one
            # pass owns the output, so counts stay single-report and base
            # passes can't re-munge jev-truncated rows.
            return applied, count
        self.jev_fallbacks += 1
        out, n = super()._prune_old_tool_results(
            messages, protect_tail_count, protect_tail_tokens, min_prune_chars
        )
        return list(out), int(n)


def _valid_openai_sequence(messages: list[dict[str, Any]]) -> bool:
    """Bidirectional structural check: every tool row has its call id present
    AND every assistant tool call keeps its result row (no orphans either way).
    Also rejects duplicate tool rows for one call id, malformed ids, malformed
    assistant tool-call rows (host shape: id/type/function.name/arguments),
    duplicate assistant call ids (ambiguous address — jev output must never
    create them), out-of-order pairs (tool row before its call), and malformed
    top-level rows (non-dict, missing/unknown role)."""
    call_rows: dict[str, int] = {}
    for index, msg in enumerate(messages):
        # Fail closed on malformed rows: jev output must be a clean transcript.
        if not isinstance(msg, dict):
            return False
        role = msg.get("role")
        if not isinstance(role, str) or role not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            return False
        if role != "assistant":
            continue
        tcs = msg.get("tool_calls")
        if tcs is None:
            # None == absent: host treats tool_calls=None as "no calls"
            # (chat_completion_helpers.py:1567 getattr default, :1644 truthiness
            # gate), and orphans still fail below (cid not in call_rows).
            continue
        # Fail closed: a present-but-non-list container is malformed output.
        if not isinstance(tcs, list):
            return False
        for tc in tcs:
            # Fail closed on anything below the host-canonical call shape, and
            # on duplicate call ids (ambiguous pairing address).
            if not is_well_formed_tool_call(tc):
                return False
            if tc["id"] in call_rows:
                return False
            call_rows[tc["id"]] = index
    seen_results: set[str] = set()
    for index, msg in enumerate(messages):
        # (dict + role already validated above; re-check cheaply for mypy.)
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        cid = msg.get("tool_call_id")
        if not isinstance(cid, str) or not cid or cid not in call_rows:
            return False
        if cid in seen_results:
            return False
        # Ordering: a result before its call is malformed output.
        if index <= call_rows[cid]:
            return False
        seen_results.add(cid)
    # Every assistant tool call must keep exactly its result row.
    return set(call_rows) <= seen_results
