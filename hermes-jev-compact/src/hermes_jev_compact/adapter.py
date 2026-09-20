"""OpenAI-format adapter: hermes messages → internal view → jev candidates."""

from __future__ import annotations

import json
from typing import Any

from .protocol import JevInternalMessage, JevToolCall
from .shaping import truncate


def _redact_excerpt(text: str) -> str:
    """Redact an excerpt crossing the jev egress boundary.

    Mirrors the host's compaction rule (context_compressor
    _redact_compaction_text: force=True + URL credentials): summaries persist
    and re-enter every later prompt, and jev excerpts likewise leave the
    host — so redaction must not depend on the operator's redact_secrets
    toggle. Import is lazy + guarded: without the host (bare unit env) the
    excerpt passes through unredacted.
    """
    try:
        from agent.redact import redact_sensitive_text

        out = redact_sensitive_text(text, force=True, redact_url_credentials=True)
        return out if isinstance(out, str) else text
    except Exception:
        return text


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
    fn = tc.get("function")
    return (
        isinstance(tc.get("id"), str)
        and bool(tc["id"])
        and tc.get("type") == REQUIRED_TOOL_CALL_TYPE
        and isinstance(fn, dict)
        and isinstance(fn.get("name"), str)
        and bool(fn["name"])
        and isinstance(fn.get("arguments"), (str, dict))
    )


def _flatten_text(content: Any) -> str | None:
    """Best-effort text view. Mirrors host _content_text_for_contains + _part_text
    (context_compressor.py:1191-1204): str passes through, lists join their
    parts' text (image/file parts contribute nothing), None → \"\".

    Returns None ONLY for shapes with no text channel at all (bytes, numbers,
    dicts without text) — those rows are excluded from candidates but still
    appear in state as \"\"."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = (
                item
                if isinstance(item, str)
                else item.get("text") if isinstance(item, dict) else None
            )
            if isinstance(text, str) and text:
                parts.append(text)
        return "\n".join(parts)
    return None


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
        if not isinstance(raw_args, str) or not raw_args.strip():
            return name, {}
        # Bound the parse: pathological arg blobs must not blow up fitting.
        if len(raw_args) > _MAX_ARGS_CHARS:
            return name, {"_raw": raw_args[:_MAX_ARGS_CHARS]}
        try:
            parsed = json.loads(raw_args)
        except (ValueError, TypeError, RecursionError):
            return name, {"_raw": raw_args}
        return name, parsed if isinstance(parsed, dict) else {"_raw": raw_args}
    return "unknown", {}


# Largest raw arguments string we will json-parse; beyond this the candidate
# keeps a truncated _raw form (shaping still budgets it honestly).
_MAX_ARGS_CHARS = 100_000


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
    keep = max(0, min(preserve_recent, total))
    return index == 0 or index >= total - keep


def _assistant_tool_calls(messages: list[dict[str, Any]]) -> list[tuple[int, list[Any]]]:
    """(row index, tool_calls list) for every assistant row carrying calls."""
    rows = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            rows.append((idx, tool_calls))
    return rows


def collect_candidates(
    messages: list[dict[str, Any]],
    prune_boundary: int,
    min_result_chars: int,
    preserve_recent: int = 0,
    result_excerpt_chars: int = 0,
) -> list[JevToolCall]:
    """Pair assistant tool_calls with their tool results before the prune boundary.

    Skips: system rows, index 0, tail (>= boundary), unpaired calls, unusable
    results (bytes/numbers/dicts-without-text), short results below the floor,
    malformed call rows, out-of-order pairs (result at/before its call).
    Duplicate assistant ids AND duplicate result ids are SKIPPED (all
    occurrences): the host itself uniquifies these pre-API
    (message_sanitization.uniquify_tool_call_ids), so scoring a pair we
    cannot address unambiguously would risk pruning the wrong occurrence.
    Survivors carry the TS pinned bit (state.ts:86-88) so decide_call can
    short-circuit; all boundary-excluded rows are pinned-equivalent by
    construction.

    ``result_excerpt_chars`` caps the truncated result head stored on each
    candidate (UTF-16 units, TS parity). 0 (default) keeps the TS-shaped
    size-only note.
    """
    prune_boundary = max(0, min(int(prune_boundary), len(messages)))
    min_result_chars = max(0, int(min_result_chars))
    # result id -> list of (row, text): duplicates are skipped below, never
    # collapsed — collapsing would score one occurrence and prune all of them.
    results: dict[str, list[tuple[int, str]]] = {}
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        cid = msg.get("tool_call_id")
        text = _flatten_text(msg.get("content"))
        if isinstance(cid, str) and cid and text is not None:
            results.setdefault(cid, []).append((idx, text))
    total = len(messages)
    # Pass 1: count well-formed assistant ids across the transcript so
    # duplicates can be skipped in pass 2 (ambiguous address, never scored).
    rows = _assistant_tool_calls(messages)
    id_counts: dict[str, int] = {}
    for _, tool_calls in rows:
        for tc in tool_calls:
            if is_well_formed_tool_call(tc):
                cid = tc["id"]
                id_counts[cid] = id_counts.get(cid, 0) + 1
    calls: list[JevToolCall] = []
    for call_idx, tool_calls in rows:
        for tc in tool_calls:
            if not is_well_formed_tool_call(tc):
                continue
            cid = tc["id"]
            occurrences = results.get(cid)
            if id_counts.get(cid, 0) != 1 or not occurrences or len(occurrences) != 1:
                continue
            result_idx, text = occurrences[0]
            # Ordering: the result must come AFTER its call (result_idx 0 with
            # call_idx > 0 is malformed/out-of-order — never a candidate).
            if call_idx == 0 or result_idx <= call_idx:
                continue
            if call_idx >= prune_boundary or result_idx >= prune_boundary:
                continue
            if len(text) < min_result_chars:
                continue
            name, args = _tool_name_and_args(tool_calls, cid)
            excerpt_chars = max(0, int(result_excerpt_chars))
            excerpt = _redact_excerpt(truncate(text, excerpt_chars)) if excerpt_chars else ""
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
                    result_excerpt=excerpt,
                )
            )
    return calls
