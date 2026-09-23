"""Pruner: state fitting, batching, questions, decisions, apply. Port of compact.ts."""

from __future__ import annotations
import json
import re
from collections.abc import Sequence
from typing import Any
from .protocol import (
    JevCallAnswer,
    JevCallDecision,
    JevError,
    JevInternalMessage,
    JevOptions,
    JevToolCall,
)
from .shaping import (
    INPUT_CHARS,
    STATE_CONTEXT,
    TEXT_HEAD,
    TEXT_TAIL,
    abridge,
    estimate_tokens,
    truncate,
)

REQUEST_OVERHEAD_TOKENS = 20
TRUNCATION_TAG = "fast-jev-compaction"


def _input_text(input: dict[str, Any], limit: int) -> str:
    try:
        text = json.dumps(input, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        text = "[unserializable input]"
    return truncate(text, limit)


def _result_note(call: JevToolCall) -> str:
    status = "error" if call.is_error else "ok"
    # Deliberate drift from TS buildHistoryEntries: the truncated head rides
    # along so jev scores content, not size alone. An empty excerpt keeps the
    # TS shape exactly ("ok, N chars (omitted)").
    if not call.result_excerpt:
        return f"{status}, {call.result_chars} chars (omitted)"
    return f"{status}, {call.result_chars} chars, head: {call.result_excerpt} (truncated)"


_WS_RUN = re.compile("\\s+")


def _compact_call(call: JevToolCall) -> str:
    parts = []
    for key, value in call.input.items():
        text = value if isinstance(value, str) else _input_text({key: value}, 200)
        parts.append(f"{key}={_WS_RUN.sub(' ', text)}")
    text = truncate(" ".join(parts), INPUT_CHARS[2])
    status = "error" if call.is_error else "ok"
    return f"{call.id} {call.tool} {text} → {status} {call.result_chars}ch"


def questions_for(call: JevToolCall) -> dict[str, dict[str, str]]:
    return {
        f"call_{call.id}": {
            "type": "noul",
            "instructions": f"Tool call {call.id} ({call.tool}) should stay in the history: knowing this call was made, with its input, still matters for what the assistant does next",
        },
        f"result_{call.id}": {
            "type": "noul",
            # Deliberate drift from TS buildQuestions: TS requires
            # "re-running the tool would not do". Hermes sessions re-run at
            # real cost (time, API spend, side effects, flaky output), so the
            # question asks for likely-future-need instead of
            # irreproducibility.
            "instructions": f"The full output of tool call {call.id} ({call.tool}, {call.result_chars} chars) should stay in the history verbatim: the assistant is likely to need its exact contents again and dropping them would lose information",
        },
    }


def batch_calls(
    calls: Sequence[JevToolCall], state_tokens: int, max_request_tokens: int
) -> list[list[JevToolCall]]:
    budget = max_request_tokens - state_tokens - REQUEST_OVERHEAD_TOKENS
    batches: list[list[JevToolCall]] = []
    current: list[JevToolCall] = []
    current_tokens = 0
    for call in calls:
        tokens = _question_tokens(call)
        if current and current_tokens + tokens > budget:
            batches.append(current)
            current = []
            current_tokens = 0
        if not current and tokens > budget:
            raise ValueError(
                f"state leaves no room for questions (~{state_tokens} of {max_request_tokens} tokens)"
            )
        current.append(call)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


def questions_for_batch(batch: Sequence[JevToolCall]) -> dict[str, Any]:
    """Prebuilt questions for one batch — same objects batch_calls measured."""
    questions: dict[str, Any] = {}
    for call in batch:
        questions.update(questions_for(call))
    return questions


_question_token_cache: dict[tuple[str, str, int, bool], int] = {}


def _question_tokens(call: JevToolCall) -> int:
    key = (call.id, call.tool, call.result_chars, call.is_error)
    tokens = _question_token_cache.get(key)
    if tokens is None:
        tokens = estimate_tokens(json.dumps(questions_for(call), separators=(",", ":")))
        if len(_question_token_cache) >= 4096:
            _question_token_cache.clear()
        _question_token_cache[key] = tokens
    return tokens


def decide_call(
    call: JevToolCall,
    answer: JevCallAnswer,
    keep_threshold: float,
    error_keep_threshold: float | None = None,
) -> JevCallDecision:
    # Deliberate drift from TS decide.ts: errors keep on a lower bar
    # (default None = plain TS single-threshold behavior).
    bar = keep_threshold
    if call.is_error and error_keep_threshold is not None:
        bar = error_keep_threshold
    base = {
        "id": call.id,
        "tool": call.tool,
        "keep_call": answer.keep_call,
        "keep_result": answer.keep_result,
    }  # type: dict[str, Any]
    if call.pinned:
        return JevCallDecision(action="keep", reason="pinned", **base)
    if answer.keep_result >= bar:
        action, reason = ("keep", "kept")
    elif answer.keep_call >= bar:
        action, reason = ("drop_result", "result_dropped")
    else:
        action, reason = ("drop_call", "call_dropped")
    return JevCallDecision(action=action, reason=reason, **base)


def _calls_by_index(calls: Sequence[JevToolCall]) -> dict[int, list[JevToolCall]]:
    by_index: dict[int, list[JevToolCall]] = {}
    for call in calls:
        by_index.setdefault(call.call_index, []).append(call)
    return by_index


def _history_entries(
    messages: Sequence[JevInternalMessage], calls: Sequence[JevToolCall], input_chars: int
) -> list[dict[str, Any]]:
    by_message = _calls_by_index(calls)
    entries: list[dict[str, Any]] = []
    for m in messages:
        tool_calls = [
            {
                "id": c.id,
                "tool": c.tool,
                "input": _input_text(c.input, input_chars),
                "result": _result_note(c),
            }
            for c in by_message.get(m.index, [])
        ]
        text = m.text
        if m.role == "tool":
            text = ""
        if not text.strip() and (not tool_calls):
            continue
        entry: dict[str, Any] = {"i": m.index, "role": m.role, "text": text}
        if tool_calls:
            entry["tool_calls"] = tool_calls
        entries.append(entry)
    return entries


def goal_from_messages(messages: Sequence[JevInternalMessage]) -> str:
    texts = [m.text for m in messages if m.role == "user" and m.text.strip()]
    return "\n".join((truncate(t, 500) for t in texts[-3:]))


def _entry_tokens(entry: dict[str, Any]) -> int:
    return estimate_tokens(json.dumps(entry, separators=(",", ":"))) + 1


class _StateFitter:
    """Greedy shrink ladder: cheapest fidelity loss first, pinned entries last.

    Stages, in order: cap call inputs (1000→200→60) → abridge long texts →
    collapse old texts → compact old calls to one line → drop text-only old
    entries → merge adjacent call-run entries. Raises when nothing fits.
    """

    def __init__(
        self,
        messages: Sequence[JevInternalMessage],
        calls: Sequence[JevToolCall],
        options: JevOptions,
        goal: str,
        preserve_recent: int,
    ) -> None:
        self._messages = messages
        self._calls = calls
        self._options = options
        self._goal = goal or goal_from_messages(messages)
        self._pinned_idx = {0} | set(range(len(messages) - preserve_recent, len(messages)))
        self._base = estimate_tokens(json.dumps(self._state_of([]), separators=(",", ":")))
        self.history: list[dict[str, Any]] = []
        self.per_entry: list[int] = []
        self.tokens = 0

    def _state_of(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        return {"context": STATE_CONTEXT, "goal": self._goal, "history": history}

    def _pinned(self, entry: dict[str, Any]) -> bool:
        return int(entry.get("i", 0)) in self._pinned_idx

    def _rebuild(self, input_chars: int) -> None:
        self.history = _history_entries(self._messages, self._calls, input_chars)
        self.per_entry = [_entry_tokens(e) for e in self.history]
        self.tokens = self._base + sum(self.per_entry)

    def _fits(self) -> bool:
        return self.tokens <= self._options.max_state_tokens

    def _shrink(self, index: int, change: Any) -> None:
        entry = self.history[index]
        change(entry)
        now = _entry_tokens(entry)
        self.tokens += now - self.per_entry[index]
        self.per_entry[index] = now

    def _order(self) -> list[int]:
        unpinned = [i for i, e in enumerate(self.history) if not self._pinned(e)]
        pinned = set(unpinned)
        return unpinned + [i for i in range(len(self.history)) if i not in pinned]

    def done(self, stage: str) -> dict[str, Any]:
        return {"state": self._state_of(self.history), "tokens": self.tokens, "stage": stage}

    def _rewrite_entries(self, kind: str) -> dict[str, Any] | None:
        original_len = {m.index: len(m.text) for m in self._messages} if kind == "collapse" else {}
        by_message = _calls_by_index(self._calls) if kind == "compact" else {}
        stages = ("texts abridged", "old messages collapsed", "old calls compacted")
        value: str | list[str]
        for index in self._order():
            entry = self.history[index]
            if kind == "abridge":
                if len(entry.get("text", "")) <= TEXT_HEAD + TEXT_TAIL + 40:
                    continue
                value = abridge(str(entry.get("text", "")), TEXT_HEAD, TEXT_TAIL)
            elif kind == "collapse":
                if self._pinned(entry) or not entry.get("text"):
                    continue
                n = original_len.get(int(entry.get("i", -1)), len(str(entry.get("text", ""))))
                value = f"[… {n} chars omitted …]"
            else:
                own = by_message.get(int(entry.get("i", -1))) or []
                if self._pinned(entry) or not own:
                    continue
                value = [_compact_call(c) for c in own]
            self._shrink(
                index,
                lambda e, v=value, k=kind: (
                    e.update(text=v) if k != "compact" else e.update(tool_calls=v)
                ),
            )
            if self._fits():
                return self.done(stages[("abridge", "collapse", "compact").index(kind)])
        return None

    def _drop_text_only(self) -> tuple[dict[str, Any] | None, set[int]]:
        left: set[int] = set()
        for index in self._order():
            entry = self.history[index]
            if self._pinned(entry) or entry.get("tool_calls"):
                continue
            left.add(index)
            self.tokens -= self.per_entry[index]
            if self._fits():
                kept = [e for i, e in enumerate(self.history) if i not in left]
                return (self.done("old messages left out") | {"state": self._state_of(kept)}, left)
        return (None, left)

    def _merged(self, skip: set[int]) -> dict[str, Any] | None:

        def foldable(e: dict[str, Any]) -> bool:
            tcs = e.get("tool_calls")
            return (
                not self._pinned(e)
                and e.get("text") == ""
                and isinstance(tcs, list)
                and bool(tcs)
                and all((isinstance(x, str) for x in tcs))
            )

        merged: list[dict[str, Any]] = []
        for entry in (e for i, e in enumerate(self.history) if i not in skip):
            prev = merged[-1] if merged else None
            if (
                prev is not None
                and foldable(prev)
                and foldable(entry)
                and (prev.get("role") == entry.get("role"))
            ):
                prev_tcs = prev.get("tool_calls")
                entry_tcs = entry.get("tool_calls")
                assert isinstance(prev_tcs, list) and isinstance(entry_tcs, list)
                prev_tcs.extend(entry_tcs)
                continue
            merged.append(dict(entry))
        self.history = merged
        self.per_entry = [_entry_tokens(e) for e in self.history]
        self.tokens = self._base + sum(self.per_entry)
        return self.done("old calls merged") if self._fits() else None

    def run(self) -> dict[str, Any]:
        self._rebuild(INPUT_CHARS[0])
        if self._fits():
            return self.done("full")
        for limit in INPUT_CHARS[1:]:
            self._rebuild(limit)
            if self._fits():
                return self.done(f"inputs<={limit}")
        for kind in ("abridge", "collapse", "compact"):
            if (result := self._rewrite_entries(kind)) is not None:
                return result
        result, left = self._drop_text_only()
        if result is not None:
            return result
        if (result := self._merged(left)) is not None:
            return result
        raise ValueError(
            f"history too large for Jev (~{self.tokens} tokens after truncation, limit {self._options.max_state_tokens})"
        )


def fit_state(
    messages: Sequence[JevInternalMessage],
    calls: Sequence[JevToolCall],
    options: JevOptions,
    goal: str = "",
    preserve_recent: int = 0,
) -> dict[str, Any]:
    """Fit the whole history into max_state_tokens; returns {state, tokens, stage}."""
    return _StateFitter(messages, calls, options, goal, preserve_recent).run()


def truncated_result_text(text: str, is_error: bool, head_chars: int) -> str:
    head_chars = max(0, int(head_chars))
    if len(text) <= head_chars + 120:
        return text
    head = f"{text[:head_chars]}\n" if head_chars > 0 else ""
    return f"{head}[{TRUNCATION_TAG} truncated {len(text) - head_chars} chars of this tool result{(' (error)' if is_error else '')}; re-run the tool if needed]"


def _drop_tool_call(msg: dict[str, Any], actions: dict[str, str]) -> dict[str, Any] | None:
    """Assistant row minus dropped calls; None when the row is payload-empty."""
    tcs = msg.get("tool_calls")
    if not isinstance(tcs, list):
        return msg
    kept = [
        tc
        for tc in tcs
        if not (
            isinstance(tc, dict)
            and isinstance(tc.get("id"), str)
            and (actions.get(tc["id"]) == "drop_call")
        )
    ]
    if len(kept) == len(tcs):
        return msg
    if kept:
        return {**msg, "tool_calls": kept}
    rest = {k: v for k, v in msg.items() if k != "tool_calls"}
    content = rest.get("content")
    if isinstance(content, str):
        return rest if content.strip() else None
    return rest if content else None


def _tool_row(
    msg: dict[str, Any], actions: dict[str, str], error_by_call: dict[str, bool], head_chars: int
) -> dict[str, Any] | None:
    """One tool row after apply: None when its call was dropped."""
    cid = msg.get("tool_call_id")
    action = actions.get(cid) if isinstance(cid, str) else None
    if action == "drop_call":
        return None
    if action == "drop_result" and isinstance(msg.get("content"), str):
        is_error = bool(error_by_call.get(cid)) if isinstance(cid, str) else False
        new_text = truncated_result_text(msg["content"], is_error, head_chars)
        return msg if new_text == msg["content"] else {**msg, "content": new_text}
    return msg


# Assistant rows the host replays verbatim or supersedes instead of merging
# (agent_runtime_helpers._is_codex_interim / _merge_consecutive_assistants).
_UNMERGEABLE_FINISH = frozenset({"incomplete", "verification_required", "verify_hook_continue"})
_PERSISTED_MARKER = "_db_persisted"


def _mergeable_assistant(msg: dict[str, Any]) -> bool:
    return not (
        msg.get("codex_reasoning_items")
        or msg.get("codex_message_items")
        or msg.get("finish_reason") in _UNMERGEABLE_FINISH
    )


def _merged_content(prev: Any, new: Any) -> tuple[bool, Any]:
    """(ok, content) for folding ``new`` into ``prev``; not ok = leave both rows."""
    if isinstance(prev, str) and isinstance(new, str):
        return True, "\n".join(p for p in (prev.strip(), new.strip()) if p)
    if not prev:
        return True, new
    if not new:
        return True, prev
    return False, None  # two non-empty multimodal/mixed bodies: never guess a join


def _merge_assistant_pair(prev: dict[str, Any], msg: dict[str, Any]) -> dict[str, Any] | None:
    """Host-shaped fold of ``msg`` into a COPY of ``prev`` (union tool_calls,
    join text). None when the pair must stay as-is (host repair owns it)."""
    if not (_mergeable_assistant(prev) and _mergeable_assistant(msg)):
        return None
    ok, content = _merged_content(prev.get("content"), msg.get("content"))
    if not ok:
        return None
    merged = dict(prev)
    calls = list(prev.get("tool_calls") or []) + list(msg.get("tool_calls") or [])
    if calls:
        merged["tool_calls"] = calls
    else:
        merged.pop("tool_calls", None)
    if content != prev.get("content"):
        merged["content"] = content
        # A stale api_content sidecar would replay the pre-merge bytes.
        merged.pop("api_content", None)
    if not merged.get("reasoning_content") and msg.get("reasoning_content"):
        merged["reasoning_content"] = msg["reasoning_content"]
    # The merged row is not the persisted row; let the host re-flush it.
    merged.pop(_PERSISTED_MARKER, None)
    return merged


def _merge_created_assistant_runs(rows: list[tuple[int, Any]]) -> list[Any]:
    """Merge assistant rows made adjacent by jev removals.

    ``rows`` pairs each surviving row with its source index. Two assistant rows
    whose source indices are consecutive were already adjacent in the input —
    that is pre-existing shape the host's own repair owns, so it is left alone.
    Only adjacency jev CREATED (removed rows between them) is folded, keeping
    the strict role-alternation invariant the host expects from a compressor.
    """
    out: list[Any] = []
    last_src: list[int] = []
    for src, row in rows:
        prev = out[-1] if out else None
        if (
            isinstance(prev, dict)
            and isinstance(row, dict)
            and prev.get("role") == "assistant"
            and row.get("role") == "assistant"
            and src != last_src[-1] + 1
        ):
            merged = _merge_assistant_pair(prev, row)
            if merged is not None:
                out[-1] = merged
                last_src[-1] = src
                continue
        out.append(row)
        last_src.append(src)
    return out


def apply_decisions_openai(
    messages: list[dict[str, Any]],
    decisions: Sequence[JevCallDecision],
    calls: Sequence[JevToolCall],
    head_chars: int,
) -> list[dict[str, Any]]:
    """Apply keep/drop decisions to openai rows. Untouched rows keep identity.

    Fail-closed on ambiguous input: duplicate short ids, duplicate decisions
    for one id, or two decisions colliding on one tool_call_id raise JevError
    (the engine treats that as a failed jev attempt → built-in prune).

    Assistant rows that removals leave adjacent are merged (host
    ``_merge_assistant_into`` semantics) so the output keeps strict role
    alternation; adjacency already present in the input is left untouched.
    """
    by_short_id: dict[str, JevToolCall] = {}
    for c in calls:
        if c.id in by_short_id:
            raise JevError(f"duplicate jev call id: {c.id}")
        by_short_id[c.id] = c
    seen_decisions: set[str] = set()
    actions: dict[str, str] = {}
    for d in decisions:
        if d.id in seen_decisions:
            raise JevError(f"duplicate jev decision id: {d.id}")
        seen_decisions.add(d.id)
        call = by_short_id.get(d.id)
        if call is None or d.action == "keep":
            continue
        if call.tool_call_id in actions:
            raise JevError(f"conflicting jev decisions for {call.tool_call_id}")
        actions[call.tool_call_id] = d.action
    if not actions:
        return list(messages)
    error_by_call = {c.tool_call_id: c.is_error for c in calls}
    out: list[tuple[int, Any]] = []
    for src, msg in enumerate(messages):
        row: Any = msg
        if isinstance(msg, dict) and msg.get("role") == "tool":
            row = _tool_row(msg, actions, error_by_call, head_chars)
        elif isinstance(msg, dict) and msg.get("role") == "assistant":
            row = _drop_tool_call(msg, actions)
        if row is not None:
            out.append((src, row))
    return _merge_created_assistant_runs(out)
