"""OpenAI-format adapter: hermes messages → internal view → jev candidates."""

from __future__ import annotations

import json
from typing import Any

from .protocol import JevInternalMessage, JevToolCall

_ERROR_MARKERS = ("error", "failed", "failure", "traceback", "exception")

# Host-canonical tool-call shape (chat_completion_helpers._assistant_tool_call_dict
# :1517-1540): every live call carries str id, str type, and a function mapping
# with str name + arguments. The jev gate rejects anything below this bar so a
# malformed input (or malformed jev output) fails closed to the built-in prune —
# never silently dropped or passed to scoring.
REQUIRED_TOOL_CALL_TYPE = "function"


def is_well_formed_tool_call(tc: Any) -> bool:
    """Host-shape check for one assistant tool_calls entry."""
    if not isinstance(tc, dict):
        return False
    if not isinstance(tc.get("id"), str) or not tc["id"]:
        return False
    if tc.get("type") != REQUIRED_TOOL_CALL_TYPE:
        return False
    fn = tc.get("function")
    if not isinstance(fn, dict):
        return False
    if not isinstance(fn.get("name"), str) or not fn["name"]:
        return False
    return isinstance(fn.get("arguments"), (str, dict))


def _flatten_text(content: Any) -> str | None:
    """Return str content, else None (multimodal/list/dict → excluded from candidates)."""
    return content if isinstance(content, str) else None


def _tool_name_and_args(tool_calls: list[Any], call_id: str) -> tuple[str, dict[str, Any]]:
    for tc in tool_calls:
        if not isinstance(tc, dict) or tc.get("id") != call_id:
            continue
        fn = tc.get("function")
        fn = fn if isinstance(fn, dict) else {}
        name = fn.get("name")
        name = name if isinstance(name, str) and name else "unknown"
        raw_args = fn.get("arguments", "")
        if isinstance(raw_args, dict):
            return name, raw_args
        if isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = json.loads(raw_args)
            except (ValueError, TypeError):
                return name, {"_raw": raw_args}
            return name, parsed if isinstance(parsed, dict) else {"_raw": raw_args}
        return name, {}
    return "unknown", {}


def _looks_error(text: str) -> bool:
    lowered = text[:2000].lower()
    return any(m in lowered for m in _ERROR_MARKERS)


def to_internal(messages: list[dict[str, Any]]) -> list[JevInternalMessage]:
    """Project openai rows to the minimal internal view. System rows excluded."""
    internal: list[JevInternalMessage] = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("tool", "assistant", "user"):
            continue
        internal.append(
            JevInternalMessage(index=i, role=role, text=_flatten_text(msg.get("content")) or "")
        )
    return internal


def is_pinned(index: int, total: int, preserve_recent: int) -> bool:
    """TS parity (state.ts:50-56): index 0 and the last N messages are pinned."""
    return index == 0 or index >= total - preserve_recent


def collect_candidates(
    messages: list[dict[str, Any]],
    prune_boundary: int,
    min_result_chars: int,
    preserve_recent: int = 0,
) -> list[JevToolCall]:
    """Pair assistant tool_calls with their tool results before the prune boundary.

    Skips: system rows, index 0, tail (>= boundary), unpaired calls, non-str
    results (multimodal), short results below the floor, malformed call rows.
    Duplicate assistant ids are SKIPPED (both occurrences): the host itself
    uniquifies these pre-API (message_sanitization.uniquify_tool_call_ids),
    so scoring a pair we cannot address unambiguously would risk pruning the
    wrong occurrence. Survivors carry the
    TS pinned bit (state.ts:86-88) so decide_call can short-circuit; all
    boundary-excluded rows are pinned-equivalent by construction.
    """
    results: dict[str, tuple[int, str]] = {}
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        cid = msg.get("tool_call_id")
        text = _flatten_text(msg.get("content"))
        if isinstance(cid, str) and cid and text is not None:
            results[cid] = (idx, text)
    total = len(messages)
    # Pass 1: count well-formed assistant ids across the transcript so
    # duplicates can be skipped in pass 2 (ambiguous address, never scored).
    id_counts: dict[str, int] = {}
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if is_well_formed_tool_call(tc):
                cid = tc["id"]
                id_counts[cid] = id_counts.get(cid, 0) + 1
    calls: list[JevToolCall] = []
    for call_idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if not is_well_formed_tool_call(tc):
                continue
            cid = tc["id"]
            if id_counts.get(cid, 0) != 1 or cid not in results:
                continue
            result_idx, text = results[cid]
            if call_idx == 0 or call_idx >= prune_boundary or result_idx >= prune_boundary:
                continue
            if len(text) < min_result_chars:
                continue
            name, args = _tool_name_and_args(tool_calls, cid)
            calls.append(
                JevToolCall(
                    id=f"t{len(calls) + 1}",
                    tool_call_id=cid,
                    tool=name,
                    input=args,
                    call_index=call_idx,
                    result_index=result_idx,
                    result_chars=len(text),
                    is_error=_looks_error(text),
                    pinned=is_pinned(call_idx, total, preserve_recent)
                    or is_pinned(result_idx, total, preserve_recent),
                )
            )
    return calls
