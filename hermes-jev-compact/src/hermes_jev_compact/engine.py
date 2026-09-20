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
from .pruner import apply_decisions_openai, batch_calls, decide_call, fit_state, questions_for_batch
from .request import noul_answer

logger = logging.getLogger(__name__)
try:
    from agent.context_compressor import ContextCompressor
except Exception:

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
            return (messages, 0)


_JEV_KNOBS: tuple[tuple[str, str, Any], ...] = (
    ("jev_base_url", "base_url", "https://api.typesafe.ai/v1"),
    ("jev_endpoint_path", "endpoint_path", "/systemone"),
    ("jev_api_key_env", "api_key_env", "TYPESAFE_API_KEY"),
    ("jev_model", "jev_model", "jev-latest"),
    ("jev_keep_threshold", "keep_threshold", 0.5),
    ("jev_error_keep_threshold", "error_keep_threshold", 0.25),
    ("jev_result_excerpt_chars", "result_excerpt_chars", 500),
    ("jev_max_state_tokens", "max_state_tokens", 25000),
    ("jev_max_request_tokens", "max_request_tokens", 30000),
    ("jev_truncate_head_chars", "truncate_head_chars", 300),
    ("jev_request_timeout_s", "request_timeout_s", 30.0),
    ("jev_min_result_chars", "min_result_chars", 2000),
    ("jev_min_reduction_ratio", "min_reduction_ratio", 0.10),
)
_JEV_DEFAULTS = {attr: default for attr, _, default in _JEV_KNOBS}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _finite_float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _answers_for_batch(batch: list[JevToolCall], raw: dict[str, Any]) -> dict[str, JevCallAnswer]:
    return {
        call.id: JevCallAnswer(
            keep_call=noul_answer(raw, f"call_{call.id}"),
            keep_result=noul_answer(raw, f"result_{call.id}"),
        )
        for call in batch
    }


def _resolve_secret(key_env: str) -> str:
    """Call-time secret read: agent.secret_scope when importable, else os.environ."""
    try:
        from agent.secret_scope import get_secret

        value = get_secret(key_env)
    except Exception:
        value = os.environ.get(key_env)
    return value if isinstance(value, str) else ""


class JevContextCompressor(ContextCompressor):  # type: ignore[misc]
    """Built-in compressor with Jev keep/drop scoring inside the prune seam."""

    jev_base_url: str
    jev_endpoint_path: str
    jev_api_key_env: str
    jev_model: str
    jev_keep_threshold: float
    jev_error_keep_threshold: float
    jev_result_excerpt_chars: int
    jev_max_state_tokens: int
    jev_max_request_tokens: int
    jev_truncate_head_chars: int
    jev_request_timeout_s: float
    jev_min_result_chars: int
    jev_min_reduction_ratio: float
    jev_calls: int
    jev_pruned_units: int
    jev_fallbacks: int
    jev_kept_units: int
    jev_truncate_units: int
    jev_drop_units: int
    jev_hygiene_units: int

    def __init__(self, model: str, **kwargs: Any) -> None:
        base_params = set(inspect.signature(ContextCompressor.__init__).parameters) - {"self"}
        super().__init__(model, **{k: v for k, v in kwargs.items() if k in base_params})
        for attr, _, default in _JEV_KNOBS:
            setattr(self, attr, kwargs.get(attr, default))
        self.jev_calls = 0
        self.jev_pruned_units = 0
        self.jev_fallbacks = 0
        self.jev_kept_units = 0
        self.jev_truncate_units = 0
        self.jev_drop_units = 0
        self.jev_hygiene_units = 0
        with contextlib.suppress(Exception):
            self.api_key = ""

    def update_model(self, *args: Any, **kwargs: Any) -> None:
        super().update_model(*args, **kwargs)

    @property
    def name(self) -> str:
        return "jev"

    def __deepcopy__(self, memo: dict[int, Any]) -> JevContextCompressor:
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
        model = base_kwargs.pop("model", getattr(self, "model", ""))
        try:
            fresh.__init__(model, **base_kwargs)  # type: ignore[misc]
        except Exception:
            for key, value in self.__dict__.items():
                if key.startswith("_compression_cancelled") or key in {"_session_db", "api_key"}:
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
        for counter in (
            "jev_calls",
            "jev_pruned_units",
            "jev_fallbacks",
            "jev_kept_units",
            "jev_truncate_units",
            "jev_drop_units",
            "jev_hygiene_units",
        ):
            fresh.__dict__[counter] = int(getattr(self, counter, 0) or 0)
        fresh.__dict__["api_key"] = ""
        return fresh

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
        threshold = _finite_float(self.jev_keep_threshold, 0.5)
        error_threshold = _finite_float(getattr(self, "jev_error_keep_threshold", 0.25), 0.25)
        return JevOptions(
            keep_threshold=min(1.0, max(0.0, threshold)),
            error_keep_threshold=min(1.0, max(0.0, error_threshold)),
            result_excerpt_chars=max(0, _safe_int(self.jev_result_excerpt_chars, 500)),
            min_reduction_ratio=min(
                1.0, max(0.0, _finite_float(self.jev_min_reduction_ratio, 0.10))
            ),
            max_state_tokens=max(1, _safe_int(self.jev_max_state_tokens, 25000)),
            max_request_tokens=max(1, _safe_int(self.jev_max_request_tokens, 30000)),
            truncate_head_chars=max(0, _safe_int(self.jev_truncate_head_chars, 300)),
            request_timeout_s=max(1.0, _finite_float(self.jev_request_timeout_s, 30.0)),
            min_result_chars=max(0, _safe_int(self.jev_min_result_chars, 2000)),
        )

    def _ask_batches(
        self, asker: JevAsker, state: Any, batches: list[list[JevToolCall]], options: JevOptions
    ) -> list[JevCallDecision]:
        # Answers recorded per batch (sequential: cancellation can stop between
        # asks). Each asked call MUST have an entry here before decisions run.
        answers: dict[str, JevCallAnswer] = {}
        for batch in batches:
            if self._cancelled():
                raise JevError("compression cancelled before jev batch")
            questions = questions_for_batch(batch)
            raw = asker.ask(state, questions)
            if self._cancelled():
                raise JevError("compression cancelled during jev batch")
            answers.update(_answers_for_batch(batch, raw))
        decisions = []
        for call in (c for b in batches for c in b):
            answer = answers.get(call.id)
            if answer is None:
                raise JevError(f"missing jev answers for {call.id}")
            decisions.append(
                decide_call(
                    call,
                    answer,
                    options.keep_threshold,
                    options.error_keep_threshold,
                )
            )
        return decisions

    def _fallback(
        self,
        message: str | None = None,
        *args: Any,
        level: str = "info",
        **kwargs: Any,
    ) -> tuple[None, int]:
        if message is not None:
            getattr(logger, level)(message, *args, **kwargs)
        return (None, 0)

    def _nondemotion_hygiene(
        self,
        applied: list[dict[str, Any]],
        protect_tail_count: int,
        protect_tail_tokens: int | None,
    ) -> int:
        """Host passes that must not be lost on the jev path — dedup (lossless),
        tool-call arg truncation (oversized args 400 providers), and image
        retire (anti-thrash). Explicitly NOT the demote/pressure passes: those
        would munge jev's keeps. Each host call is guarded: on host drift the
        helper logs and jev's output still commits. Returns hygiene prune count.

        The arg-truncation boundary is recomputed on the POST-jev list: jev
        removals shift rows left, so the pre-jev boundary would reach into the
        protected tail.
        """
        extra = 0
        try:
            dedupe = getattr(self, "_dedupe_tool_results", None)
            if callable(dedupe):
                extra += _safe_int(dedupe(applied), 0)
        except Exception:
            logger.warning("jev post-hygiene dedupe unavailable; keeping jev output")
        try:
            boundary = self._prune_boundary(applied, protect_tail_count, protect_tail_tokens)
        except Exception:
            logger.warning("jev post-hygiene boundary unavailable; keeping jev output")
            boundary = 0
        try:
            trunc_at = getattr(self, "_truncate_tool_call_args_at", None)
            if callable(trunc_at):
                for i in range(max(0, boundary)):
                    try:
                        trunc_at(applied, i)
                    except Exception:
                        logger.warning("jev post-hygiene arg truncation failed at row %d", i)
                        break
        except Exception:
            logger.warning("jev post-hygiene arg pass unavailable; keeping jev output")
        try:
            from agent.context_compressor import _retire_stale_tool_result_images

            extra += _safe_int(_retire_stale_tool_result_images(applied), 0)
        except Exception:
            logger.warning("jev post-hygiene image retire unavailable; keeping jev output")
        return extra

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
            messages,
            boundary,
            max(min_prune_chars, options.min_result_chars),
            result_excerpt_chars=options.result_excerpt_chars,
        )
        if not candidates or self._cancelled():
            return self._fallback()
        key = _resolve_secret(self.jev_api_key_env)
        if not key:
            return self._fallback("jev: no api key configured; built-in prune", level="debug")
        try:
            fitted = fit_state(to_internal(messages), candidates, options)
            batches = batch_calls(candidates, int(fitted["tokens"]), options.max_request_tokens)
            asker = (
                asker_factory(self.jev_base_url, key, self.jev_model, options)
                if asker_factory is not None
                else JevAsker(
                    self.jev_base_url,
                    key,
                    self.jev_model,
                    options.request_timeout_s,
                    endpoint_path=self.jev_endpoint_path,
                )
            )
            decisions = self._ask_batches(asker, fitted["state"], batches, options)
        except JevError as exc:
            return self._fallback("jev prune failed (%s); built-in prune", exc)
        except (ValueError, TimeoutError) as exc:
            return self._fallback("jev prune skipped (%s); built-in prune", exc)
        except Exception:
            return self._fallback(
                "jev prune crashed; built-in prune", level="warning", exc_info=True
            )
        try:
            applied = apply_decisions_openai(
                messages, decisions, candidates, options.truncate_head_chars
            )
        except JevError as exc:
            return self._fallback("jev output ambiguous (%s); built-in prune", exc, level="warning")
        pruned_units = sum((1 for d in decisions if d.action != "keep"))
        kept_units = sum((1 for d in decisions if d.action == "keep"))
        truncate_units = sum((1 for d in decisions if d.action == "drop_result"))
        drop_units = sum((1 for d in decisions if d.action == "drop_call"))
        if not self.quiet_mode:
            by_id = {c.id: c for c in candidates}
            for d in decisions:
                call = by_id.get(d.id)
                logger.info(
                    "jev decision: %s %s result=%dch error=%s keep_call=%.2f "
                    "keep_result=%.2f action=%s",
                    d.id,
                    d.tool,
                    call.result_chars if call is not None else -1,
                    "yes" if call is not None and call.is_error else "no",
                    d.keep_call,
                    d.keep_result,
                    d.action,
                )
        if pruned_units == 0 or applied == messages or (not _valid_openai_sequence(applied)):
            if applied != messages and pruned_units:
                logger.warning("jev output failed validity; built-in prune")
            return self._fallback()
        chars_before = sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
        chars_after = sum(len(str(m.get("content", ""))) for m in applied if isinstance(m, dict))
        ratio = (chars_before - chars_after) / chars_before if chars_before > 0 else 0.0
        gate = options.min_reduction_ratio
        if ratio < gate:
            return self._fallback(
                "jev reduction under %.0f%% (got %.1f%%); built-in prune",
                gate * 100.0,
                ratio * 100.0,
            )
        if self._cancelled():
            return self._fallback("jev prune cancelled before commit; built-in prune")
        hygiene = self._nondemotion_hygiene(applied, protect_tail_count, protect_tail_tokens)
        if self._cancelled():
            return self._fallback("jev prune cancelled during hygiene; built-in prune")
        if not _valid_openai_sequence(applied):
            logger.warning("jev post-hygiene output failed validity; built-in prune")
            return self._fallback()
        self.jev_calls += len(batches)
        self.jev_pruned_units += pruned_units
        self.jev_kept_units += kept_units
        self.jev_truncate_units += truncate_units
        self.jev_drop_units += drop_units
        self.jev_hygiene_units += hygiene
        # Return count = jev decisions + host hygiene rewrites (dedup/image
        # retire). The split counters below stay jev-only; jev_hygiene_units
        # carries the hygiene share so count == pruned + hygiene reconciles.
        total_units = pruned_units + hygiene
        if not self.quiet_mode:
            logger.info(
                "jev prune: %d call(s) scored in %d request(s), %d dropped/truncated (%s)",
                len(candidates),
                len(batches),
                pruned_units,
                fitted["stage"],
            )
        return (applied, total_units)

    def prune_tool_results_only(
        self, messages: list[dict[str, Any]], current_tokens: int | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        out, n = super().prune_tool_results_only(messages, current_tokens=current_tokens)
        return (list(out), int(n))

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
            applied, count = (None, 0)
        if applied is not None and count > 0:
            return (applied, count)
        self.jev_fallbacks += 1
        out, n = super()._prune_old_tool_results(
            messages, protect_tail_count, protect_tail_tokens, min_prune_chars
        )
        return (list(out), int(n))


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
        if not isinstance(msg, dict):
            return False
        role = msg.get("role")
        if not isinstance(role, str) or role not in {"system", "user", "assistant", "tool"}:
            return False
        if role != "assistant":
            continue
        tcs = msg.get("tool_calls")
        if tcs is None:
            continue
        if not isinstance(tcs, list):
            return False
        for tc in tcs:
            if not is_well_formed_tool_call(tc):
                return False
            if tc["id"] in call_rows:
                return False
            call_rows[tc["id"]] = index
    seen_results: set[str] = set()
    for index, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        cid = msg.get("tool_call_id")
        if not isinstance(cid, str) or not cid or cid not in call_rows:
            return False
        if cid in seen_results:
            return False
        if index <= call_rows[cid]:
            return False
        seen_results.add(cid)
    return set(call_rows) <= seen_results
