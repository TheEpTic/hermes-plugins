"""Adapter tests: pairing, pins, exclusions (hermes openai format)."""

from __future__ import annotations

from hermes_jev_compact.adapter import collect_candidates, to_internal
from tests.conftest import make_tool_transcript


def test_pairs_calls_with_results_and_pins_tail():
    messages = make_tool_transcript(n_calls=3, result_chars=9000)
    # boundary at 7 → last pair (idx 6,7) + tail user (8) protected
    calls = collect_candidates(messages, prune_boundary=7, min_result_chars=8000)
    assert [(c.id, c.tool, c.call_index, c.result_index) for c in calls] == [
        ("t1", "read_file", 2, 3),
        ("t2", "read_file", 4, 5),
    ]
    assert calls[0].result_chars == 9000
    assert calls[0].input == {"path": "src/f0.ts"}


def test_ignores_unpaired_and_short_and_unusable():
    messages = make_tool_transcript(n_calls=1, result_chars=100)
    assert collect_candidates(messages, 99, 8000) == []
    # Multimodal text parts ARE flattened now (host _part_text parity) — a
    # 9000-char text part is a real candidate, not an exclusion.
    messages[3]["content"] = [{"type": "text", "text": "x" * 9000}]
    assert [c.tool_call_id for c in collect_candidates(messages, 99, 8000)] == ["call_1"]
    # Shapes with no text channel (bytes/numbers) are still excluded.
    messages[3]["content"] = b"\x00\x01" * 5000
    assert collect_candidates(messages, 99, 8000) == []
    # unpaired call: no result row
    lonely = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "zzz",
                    "type": "function",
                    "function": {"name": "t", "arguments": "{}"},
                }
            ],
        },
    ]
    assert collect_candidates(lonely, 99, 1) == []


def _well_formed_call(cid: str, name: str = "t") -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}


def test_skips_malformed_and_duplicate_assistant_ids():
    # Below-host-shape rows (missing type/function) are never scored, and
    # duplicate assistant ids are skipped in BOTH occurrences — the decisions
    # can never address them unambiguously (fail closed, built-in prune keeps
    # them). Only the well-formed unique pair survives.
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "bad"},  # malformed: no type/function
                {"id": "ok", "type": "function"},  # malformed: no function map
                _well_formed_call("dup"),
                _well_formed_call("solo"),
            ],
        },
        {"role": "assistant", "content": "", "tool_calls": [_well_formed_call("dup")]},
        {"role": "tool", "tool_call_id": "bad", "content": "B" * 9000},
        {"role": "tool", "tool_call_id": "ok", "content": "O" * 9000},
        {"role": "tool", "tool_call_id": "dup", "content": "D" * 9000},
        {"role": "tool", "tool_call_id": "solo", "content": "S" * 9000},
        {"role": "user", "content": "tail"},
    ]
    calls = collect_candidates(messages, 99, 1)
    assert [c.tool_call_id for c in calls] == ["solo"]


def test_skips_index_zero_call():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c0", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c0", "content": "y" * 9000},
        {"role": "user", "content": "tail"},
    ]
    assert collect_candidates(messages, 99, 1) == []


def test_multi_call_row_and_duplicate_ids():
    # Two calls in ONE assistant row pair independently; a duplicate tool row
    # for one call id SKIPS that id entirely (all occurrences) — collapsing
    # would score one occurrence while pruning every row sharing the id.
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "A" * 9000},
        {"role": "tool", "tool_call_id": "b", "content": "B" * 9000},
        {"role": "tool", "tool_call_id": "a", "content": "A2" * 4500},
        {"role": "user", "content": "tail"},
    ]
    calls = collect_candidates(messages, 99, 1)
    assert [(c.tool_call_id, c.call_index) for c in calls] == [("b", 1)]


def test_pinned_bit_marks_index_zero_and_recent():
    from hermes_jev_compact.adapter import is_pinned

    messages = make_tool_transcript(n_calls=2, result_chars=9000)
    # preserve_recent=1 → last message (tail user) is pinned; with the default
    # 0 only index 0 is pinned and no candidate is near it.
    assert is_pinned(0, len(messages), 0) is True
    assert is_pinned(3, len(messages), 0) is False
    assert is_pinned(len(messages) - 1, len(messages), 1) is True
    assert all(c.pinned is False for c in collect_candidates(messages, 99, 8000))
    pinned = collect_candidates(messages, 99, 8000, preserve_recent=len(messages))
    assert pinned and all(c.pinned is True for c in pinned)


def test_to_internal_excludes_system():
    internal = to_internal(make_tool_transcript(1))
    assert all(m.role != "system" for m in internal)
    assert [m.role for m in internal] == ["user", "assistant", "tool", "user"]


def test_skips_duplicate_result_ids_and_out_of_order():
    # Duplicate RESULT ids skip (like duplicate assistant ids): scoring one
    # occurrence while pruning all rows sharing the id would corrupt output.
    dup = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_well_formed_call("a")],
        },
        {"role": "tool", "tool_call_id": "a", "content": "A" * 9000},
        {"role": "tool", "tool_call_id": "a", "content": "A2" * 4500},
        {"role": "user", "content": "tail"},
    ]
    assert collect_candidates(dup, 99, 1) == []
    # Result BEFORE its call is malformed — never a candidate.
    ooo = [
        {"role": "tool", "tool_call_id": "a", "content": "A" * 9000},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_well_formed_call("a")],
        },
        {"role": "user", "content": "tail"},
    ]
    assert collect_candidates(ooo, 99, 1) == []


def test_flattens_multimodal_parts_like_host():
    # Host parity (context_compressor _part_text): text parts join, image/file
    # parts contribute nothing, and the row keeps its text for state.
    from hermes_jev_compact.adapter import _flatten_text

    assert _flatten_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"
    assert _flatten_text([{"type": "image_url", "image_url": {"url": "x"}}]) == ""
    assert _flatten_text([{"type": "text", "text": "keep"}, {"type": "image_url"}]) == "keep"
    assert _flatten_text(None) == ""
    assert _flatten_text(b"\x00") is None
    assert _flatten_text(42) is None


def test_normalizes_boundary_and_preserve_recent():
    from hermes_jev_compact.adapter import is_pinned

    messages = make_tool_transcript(n_calls=1, result_chars=9000)
    # Oversized boundary clamps to len (all eligible); negative clamps to 0.
    assert len(collect_candidates(messages, 10**9, 1)) == 1
    assert collect_candidates(messages, -5, 1) == []
    assert is_pinned(3, 9, -1) is False
    assert is_pinned(3, 9, 10**9) is True
