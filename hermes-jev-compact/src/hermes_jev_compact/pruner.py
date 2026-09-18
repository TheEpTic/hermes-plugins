"""Pruner: state fitting, batching, questions, decisions, apply. Port of compact.ts."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from .protocol import JevCallAnswer, JevCallDecision, JevInternalMessage, JevOptions, JevToolCall
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
# TS reference marker verbatim (compact.ts:135-140): downstream tooling and the
# TS vectors match on this string; do NOT rebrand it.
TRUNCATION_TAG = "fast-jev-compaction"


def _input_text(input: dict[str, Any], limit: int) -> str:
    try:
        text = json.dumps(input, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        text = "[unserializable input]"
    return truncate(text, limit)


def _result_note(call: JevToolCall) -> str:
    return f"{'error' if call.is_error else 'ok'}, {call.result_chars} chars (omitted)"


_WS_RUN = re.compile(r"\s+")


def _compact_call(call: JevToolCall) -> str:
    # TS parity (state.ts:110-120): whole input JSON truncated as ONE string.
    text = _input_text(call.input, INPUT_CHARS[2])
    status = "error" if call.is_error else "ok"
    return f"{call.id} {call.tool} {_WS_RUN.sub(' ', text)} → {status} {call.result_chars}ch"


def questions_for(call: JevToolCall) -> dict[str, dict[str, str]]:
    return {
        f"call_{call.id}": {
            "type": "noul",
            "instructions": (
                f"Tool call {call.id} ({call.tool}) should stay in the history: knowing this call "
                "was made, with its input, still matters for what the assistant does next"
            ),
        },
        f"result_{call.id}": {
            "type": "noul",
            "instructions": (
                f"The full output of tool call {call.id} ({call.tool}, {call.result_chars} chars) "
                "should stay in the history verbatim: the assistant still needs its contents and "
                "re-running the tool would not do"
            ),
        },
    }


def batch_calls(
    calls: Sequence[JevToolCall], state_tokens: int, max_request_tokens: int
) -> list[list[JevToolCall]]:
    budget = max_request_tokens - state_tokens - REQUEST_OVERHEAD_TOKENS
    batches: list[list[JevToolCall]] = []
    current: list[JevToolCall] = []
    current_tokens = 0
    # One questions_for() per call: cache the built questions AND their token
    # cost so the asker path reuses them instead of rebuilding (engine passes
    # batches through _ask_batches, which looks them up here).
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


_question_token_cache: dict[str, int] = {}


def _question_tokens(call: JevToolCall) -> int:
    key = f"{call.id}\x00{call.tool}\x00{call.result_chars}\x00{call.is_error}"
    tokens = _question_token_cache.get(key)
    if tokens is None:
        tokens = estimate_tokens(json.dumps(questions_for(call), separators=(",", ":")))
        _question_token_cache[key] = tokens
    return tokens


def decide_call(call: JevToolCall, answer: JevCallAnswer, keep_threshold: float) -> JevCallDecision:
    base = {
        "id": call.id,
        "tool": call.tool,
        "keep_call": answer.keep_call,
        "keep_result": answer.keep_result,
    }
    # TS parity (compact.ts:107): pinned short-circuits before probabilities.
    if call.pinned:
        return JevCallDecision(action="keep", reason="pinned", **base)  # type: ignore[arg-type]
    if answer.keep_result >= keep_threshold:
        action, reason = "keep", "kept"
    elif answer.keep_call >= keep_threshold:
        action, reason = "drop_result", "result_dropped"
    else:
        action, reason = "drop_call", "call_dropped"
    return JevCallDecision(action=action, reason=reason, **base)  # type: ignore[arg-type]


def _calls_by_index(calls: Sequence[JevToolCall]) -> dict[int, list[JevToolCall]]:
    by_index: dict[int, list[JevToolCall]] = {}
    for call in calls:
        by_index.setdefault(call.call_index, []).append(call)
    return by_index


def _history_entries(
    messages: Sequence[JevInternalMessage], calls: Sequence[JevToolCall], input_chars: int
) -> list[dict[str, Any]]:
    by_message = _calls_by_index(calls)
    # TS has no tool role: tool results ride as tool_calls[].result on the CALL
    # message, and result messages are empty shells that get skipped. mirror it:
    # paired tool rows become empty text (bodies live only in _result_note),
    # EXCEPT unpaired tool rows (not a candidate) which collapse to a note.
    paired_result_idx = {c.result_index for c in calls}
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
            text = (
                ""
                if m.index in paired_result_idx
                else f"[tool result, {len(m.text)} chars, omitted]"
            )
        if not text.strip() and not tool_calls:
            continue
        entry: dict[str, Any] = {"i": m.index, "role": m.role, "text": text}
        if tool_calls:
            entry["tool_calls"] = tool_calls
        entries.append(entry)
    return entries


def goal_from_messages(messages: Sequence[JevInternalMessage]) -> str:
    # TS parity (state.ts:174-185): last three NON-EMPTY user prompts. In TS,
    # user-role result carriers are excluded via toolResults; here tool rows
    # have their own role so the filter is just role+non-blank.
    texts = [m.text for m in messages if m.role == "user" and m.text.strip()]
    return "\n".join(truncate(t, 500) for t in texts[-3:])


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

    def _abridge_texts(self) -> dict[str, Any] | None:
        for index in self._order():
            entry = self.history[index]
            if len(entry.get("text", "")) <= TEXT_HEAD + TEXT_TAIL + 40:
                continue
            abridged = abridge(str(entry.get("text", "")), TEXT_HEAD, TEXT_TAIL)
            self._shrink(index, lambda e: e.update(text=abridged))
            if self._fits():
                return self.done("texts abridged")
        return None

    def _collapse_texts(self) -> dict[str, Any] | None:
        original_len = {m.index: len(m.text) for m in self._messages}
        for index in self._order():
            entry = self.history[index]
            if self._pinned(entry) or not entry.get("text"):
                continue
            n = original_len.get(int(entry.get("i", -1)), len(str(entry.get("text", ""))))
            note = f"[… {n} chars omitted …]"
            self._shrink(index, lambda e: e.update(text=note))
            if self._fits():
                return self.done("old messages collapsed")
        return None

    def _compact_calls(self) -> dict[str, Any] | None:
        by_message = _calls_by_index(self._calls)
        for index in self._order():
            entry = self.history[index]
            own = by_message.get(int(entry.get("i", -1))) or []
            if self._pinned(entry) or not own:
                continue
            compacted = [_compact_call(c) for c in own]
            self._shrink(index, lambda e: e.update(tool_calls=compacted))
            if self._fits():
                return self.done("old calls compacted")
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
                return self.done("old messages left out") | {"state": self._state_of(kept)}, left
        return None, left

    def _merged(self, skip: set[int]) -> dict[str, Any] | None:
        def foldable(e: dict[str, Any]) -> bool:
            tcs = e.get("tool_calls")
            return (
                not self._pinned(e)
                and e.get("text") == ""
                and isinstance(tcs, list)
                and bool(tcs)
                and all(isinstance(x, str) for x in tcs)
            )

        merged: list[dict[str, Any]] = []
        for entry in (e for i, e in enumerate(self.history) if i not in skip):
            prev = merged[-1] if merged else None
            if (
                prev is not None
                and foldable(prev)
                and foldable(entry)
                and prev.get("role") == entry.get("role")
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
        for stage in (self._abridge_texts, self._collapse_texts, self._compact_calls):
            if (result := stage()) is not None:
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
    if len(text) <= head_chars + 120:
        return text
    head = f"{text[:head_chars]}\n" if head_chars > 0 else ""
    return (
        f"{head}[{TRUNCATION_TAG} truncated {len(text) - head_chars} chars of this tool result"
        f"{' (error)' if is_error else ''}; re-run the tool if needed]"
    )


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
            and actions.get(tc["id"]) == "drop_call"
        )
    ]
    if len(kept) == len(tcs):
        return msg
    if kept:
        return {**msg, "tool_calls": kept}
    rest = {k: v for k, v in msg.items() if k != "tool_calls"}
    content = rest.get("content")
    # payload-empty assistant turn (only dropped calls): drop the row —
    # mirrors host pass 2, which prunes it anyway.
    return rest if isinstance(content, str) and content.strip() else None


def apply_decisions_openai(
    messages: list[dict[str, Any]],
    decisions: Sequence[JevCallDecision],
    calls: Sequence[JevToolCall],
    head_chars: int,
) -> list[dict[str, Any]]:
    """Apply keep/drop decisions to openai rows. Untouched rows keep identity."""
    by_short_id = {c.id: c for c in calls}
    # Occurrence-level identity: candidates carry occurrence (call,row) pairs
    # by construction (adapter skips duplicate assistant ids), and the host
    # never hands jev an ambiguous transcript — the gate above fails closed.
    # Belt-and-braces: if two decisions ever collide on one tool_call_id, the
    # LAST wins exactly as TS applies per-tool actions in order.
    actions = {
        call.tool_call_id: d.action
        for d in decisions
        if (call := by_short_id.get(d.id)) is not None and d.action != "keep"
    }
    if not actions:
        return list(messages)
    error_by_call = {c.tool_call_id: c.is_error for c in calls}
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        if msg.get("role") == "tool":
            cid = msg.get("tool_call_id")
            action = actions.get(cid) if isinstance(cid, str) else None
            if action == "drop_call":
                continue
            if action == "drop_result" and isinstance(msg.get("content"), str):
                content = msg["content"]
                # TS parity (compact.ts:173-177,192-199): the truncation marker
                # carries "(error)" when the original result was an error.
                is_error = bool(error_by_call.get(cid)) if isinstance(cid, str) else False
                new_text = truncated_result_text(content, is_error, head_chars)
                out.append(msg if new_text == content else {**msg, "content": new_text})
                continue
            out.append(msg)
        elif msg.get("role") == "assistant":
            dropped = _drop_tool_call(msg, actions)
            if dropped is not None:
                out.append(dropped)
        else:
            out.append(msg)
    return out
