"""Pruner parity tests (vectors from fast-jev-compaction.test.ts, adapted to openai rows).

Traceability to test.ts blocks: state fitting ('sends the whole history',
'defaults the goal', 'truncates inputs', 'shrinks old calls', 'abridges /
collapses', 'throws when impossible'), question batching (1 / split / no-room),
decisions (matrix incl. pinned, drop+truncate, head-chars incl. zero head).
Where the openai row shape forces adaptation (no Message.toolUses), the test
names the TS block it mirrors and asserts the same observable (stage names,
marker text, reason strings).
"""

from __future__ import annotations

import json

from dataclasses import replace

import pytest

from hermes_jev_compact.adapter import collect_candidates, to_internal
from hermes_jev_compact.protocol import JevCallAnswer, JevOptions, JevToolCall
from hermes_jev_compact.pruner import (
    _compact_call,
    apply_decisions_openai,
    batch_calls,
    decide_call,
    fit_state,
    questions_for,
    truncated_result_text,
)
from hermes_jev_compact.shaping import estimate_tokens
from tests.conftest import make_tool_transcript

OPTS = JevOptions()


def _calls(n=3, chars=9000):
    messages = make_tool_transcript(n_calls=n, result_chars=chars)
    return messages, collect_candidates(messages, 99, 1)


def test_state_sends_history_with_results_omitted():
    messages, calls = _calls()
    assert len(calls) == 3
    fitted = fit_state(to_internal(messages), calls, OPTS, goal="fix the test")
    assert fitted["stage"] == "full"
    raw = json.dumps(fitted["state"])
    assert "x" * 100 not in raw  # result bodies omitted
    assert "never touch src/generated" in raw
    assert "go ahead" in raw
    first_call = fitted["state"]["history"][1]["tool_calls"][0]
    assert first_call["id"] == "t1"
    assert first_call["result"] == f"ok, {9000} chars (omitted)"


def test_goal_defaults_to_latest_user_prompts():
    messages, _ = _calls()
    fitted = fit_state(to_internal(messages), [], OPTS)
    assert "failing test" in fitted["state"]["goal"]
    assert "go ahead" in fitted["state"]["goal"]


def test_truncates_inputs_before_text():
    """Inputs shrink before texts: openai fixture has one extra short text row."""
    messages = make_tool_transcript(n_calls=1, result_chars=10)
    big_args = {"path": "x.ts", "content": "x" * 5000}
    messages[2]["tool_calls"][0]["function"]["arguments"] = json.dumps(big_args)
    calls = collect_candidates(messages, 99, 1)
    # Budget 320: +20 over the TS-ported 300 for the longer reworded context
    # (125 vs 108 estimated tokens); the test pins ladder ORDER (inputs
    # before texts), not the absolute budget.
    fitted = fit_state(to_internal(messages), calls, JevOptions(max_state_tokens=320), goal="g")
    assert fitted["stage"] == "inputs<=60"
    assert fitted["tokens"] <= 320
    assert fitted["state"]["history"][0]["text"].startswith("fix the failing")


def test_compact_call_vector():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "start"}]
    for i in range(40):
        cid = f"c{i}"
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "arguments": json.dumps({"file_path": f"/repo/src/module-{i}.ts"}),
                        },
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": cid, "content": "x"})
    messages.append({"role": "assistant", "content": "done"})
    calls = collect_candidates(messages, 10_000, 1)
    full = fit_state(to_internal(messages), calls, OPTS, preserve_recent=1)
    compacted = fit_state(
        to_internal(messages),
        calls,
        JevOptions(max_state_tokens=int(full["tokens"] * 0.8)),
        preserve_recent=1,
    )
    assert compacted["stage"] == "old calls compacted"
    # TS parity (state.ts:110-120): key=value per entry, raw strings, the
    # JOINED line truncated to 60 — not whole-object JSON.
    assert compacted["state"]["history"][1]["tool_calls"][0] == (
        "t1 Read file_path=/repo/src/module-0.ts → ok 1ch"
    )


def test_batching_splits_and_throws():
    _, calls = _calls(n=10, chars=100)
    assert len(batch_calls(calls, 1000, 30000)) == 1
    batches = batch_calls(calls, 29600, 30000)
    assert len(batches) > 1
    assert [c.id for b in batches for c in b] == [c.id for c in calls]
    with pytest.raises(ValueError, match="no room"):
        batch_calls(calls, 29990, 30000)


@pytest.mark.parametrize(
    ("answer", "threshold", "action"),
    [
        (JevCallAnswer(0.9, 0.7), 0.5, "keep"),
        (JevCallAnswer(0.9, 0.2), 0.5, "drop_result"),
        (JevCallAnswer(0.1, 0.2), 0.5, "drop_call"),
    ],
)
def test_decide_call_matrix(answer, threshold, action):
    _, calls = _calls(n=1)
    assert decide_call(calls[0], answer, threshold).action == action


def test_decide_call_pinned_short_circuits_like_ts():
    # TS test.ts 'decisions' block: pinned + (0,0) -> keep/pinned.
    _, calls = _calls(n=1)
    decision = decide_call(replace(calls[0], pinned=True), JevCallAnswer(0.0, 0.0), 0.5)
    assert (decision.action, decision.reason) == ("keep", "pinned")


def test_decide_call_error_threshold_keeps_errors_on_a_lower_bar():
    # Deliberate drift from TS decide.ts: hermes sessions re-run tools at
    # real cost (time, API spend, side effects, flaky output), so error
    # results keep unless jev is confident they are stale. Non-errors keep
    # the plain threshold.
    _, calls = _calls(n=1)
    error = replace(calls[0], is_error=True)
    plain = replace(calls[0], is_error=False)
    answer = JevCallAnswer(0.3, 0.3)
    assert decide_call(error, answer, 0.5, error_keep_threshold=0.25).action == "keep"
    assert decide_call(plain, answer, 0.5, error_keep_threshold=0.25).action == "drop_call"
    assert (
        decide_call(error, JevCallAnswer(0.3, 0.1), 0.5, error_keep_threshold=0.25).action
        == "drop_result"
    )
    pinned = replace(error, pinned=True)
    assert decide_call(pinned, JevCallAnswer(0.0, 0.0), 0.5, 0.25).reason == "pinned"


def test_decide_call_defaults_to_plain_threshold_for_errors():
    # Omitted error threshold = TS behavior: one bar for every unit.
    _, calls = _calls(n=1)
    error = replace(calls[0], is_error=True)
    assert decide_call(error, JevCallAnswer(0.3, 0.3), 0.5).action == "drop_call"


def test_state_context_does_not_promise_free_reruns():
    from hermes_jev_compact.shaping import STATE_CONTEXT

    assert "always re-run" not in STATE_CONTEXT


def test_result_question_does_not_require_irreproducibility():
    _, calls = _calls(n=1)
    text = questions_for(calls[0])[f"result_{calls[0].id}"]["instructions"]
    assert "re-running the tool would not do" not in text
    assert str(calls[0].result_chars) in text  # size context retained


def test_default_candidate_floor_is_2000_chars():
    assert JevOptions().min_result_chars == 2000


def test_abridge_and_collapse_stages_like_ts():
    # TS test.ts 'abridges long texts oldest-first and collapses old messages
    # last': pin index 0 first, recent last; collapse is a one-line note.
    # Entry 0 is SHORT here (pinned text is never the shrink target); the
    # three long middle entries drive abridge-then-collapse.
    from hermes_jev_compact.protocol import JevInternalMessage

    long = [JevInternalMessage(index=0, role="user", text="pin this first message")]
    long += [
        JevInternalMessage(
            index=i,
            role="assistant" if i % 2 == 1 else "user",
            text=f"{i} " + "lorem ipsum " * 300,
        )
        for i in range(1, 4)
    ]
    long.append(JevInternalMessage(index=4, role="user", text="latest"))
    abridged = fit_state(long, [], JevOptions(max_state_tokens=1800), preserve_recent=1)
    assert abridged["stage"] == "texts abridged"
    assert abridged["tokens"] <= 1800
    assert "chars omitted" in abridged["state"]["history"][1]["text"]
    assert abridged["state"]["history"][0]["text"] == "pin this first message"
    assert abridged["state"]["history"][4]["text"] == "latest"

    collapsed = fit_state(long, [], JevOptions(max_state_tokens=420), preserve_recent=1)
    assert collapsed["stage"] == "old messages collapsed"
    assert collapsed["tokens"] <= 420
    import re

    assert re.fullmatch(r"\[… \d+ chars omitted …\]", collapsed["state"]["history"][1]["text"])
    assert collapsed["state"]["history"][0]["text"] == "pin this first message"
    assert collapsed["state"]["history"][4]["text"] == "latest"


def test_apply_openai_drops_and_truncates():
    messages = make_tool_transcript(n_calls=3, result_chars=2000)
    calls = collect_candidates(messages, 99, 1)
    decisions = [
        decide_call(calls[0], JevCallAnswer(0.1, 0.1), 0.5),
        decide_call(calls[1], JevCallAnswer(0.9, 0.1), 0.5),
        decide_call(calls[2], JevCallAnswer(0.9, 0.9), 0.5),
    ]
    kept = apply_decisions_openai(messages, decisions, calls, 300)
    # dropped call_1 pair gone entirely; call_2 result truncated; call_3 intact
    roles = [(m.get("role"), m.get("tool_call_id") or "") for m in kept]
    assert ("tool", "call_1") not in roles
    assert all(
        not any(tc.get("id") == "call_1" for tc in (m.get("tool_calls") or []))
        for m in kept
        if m.get("role") == "assistant"
    )
    truncated = next(m for m in kept if m.get("tool_call_id") == "call_2")
    assert truncated["content"].startswith("x" * 300)
    # TS reference marker verbatim (compact.ts:135-140 / test.ts:297-300).
    assert "fast-jev-compaction truncated 1700 chars" in truncated["content"]
    intact = next(m for m in kept if m.get("tool_call_id") == "call_3")
    assert intact["content"] == "x" * 2000
    assert kept[0] is messages[0]  # untouched identity


def test_apply_openai_error_result_carries_ts_marker():
    # TS parity (compact.ts:173-177,192-199): error results get "(error)" in
    # the truncation marker; clean results don't. is_error rides on the
    # candidate, not the decision.
    from dataclasses import replace

    from tests.conftest import make_tool_transcript

    messages = make_tool_transcript(n_calls=2, result_chars=2000)
    messages[3]["content"] = "FAILED: traceback " + "e" * 2000
    calls = collect_candidates(messages, 99, 1)
    assert [c.is_error for c in calls] == [True, False]
    decisions = [
        decide_call(calls[0], JevCallAnswer(0.9, 0.1), 0.5),
        decide_call(calls[1], JevCallAnswer(0.9, 0.1), 0.5),
    ]
    assert [d.action for d in decisions] == ["drop_result", "drop_result"]
    kept = apply_decisions_openai(messages, decisions, calls, 300)
    err = next(m for m in kept if m.get("tool_call_id") == "call_1")
    clean = next(m for m in kept if m.get("tool_call_id") == "call_2")
    assert "(error)" in err["content"]
    assert "(error)" not in clean["content"]
    # Untouched-identity + explicit is_error passthrough (independent of the
    # keyword heuristic): a clean-body call flagged by the caller still lands.
    forced = [replace(calls[1], is_error=True)]
    forced_dec = [decide_call(forced[0], JevCallAnswer(0.9, 0.1), 0.5)]
    kept2 = apply_decisions_openai(messages, forced_dec, forced, 300)
    forced_row = next(m for m in kept2 if m.get("tool_call_id") == "call_2")
    assert "(error)" in forced_row["content"]


def test_truncated_result_text_short_passthrough_and_zero_head():
    assert truncated_result_text("y" * 100, False, 300) == "y" * 100
    out = truncated_result_text("z" * 500, False, 0)
    assert (
        out
        == "[fast-jev-compaction truncated 500 chars of this tool result; re-run the tool if needed]"
    )
    # Negative head clamps to 0 (marker-only) instead of corrupting the count.
    assert truncated_result_text("z" * 500, False, -1) == out


@pytest.mark.parametrize(
    ("call_id", "tool_call_id", "tool", "input_data", "result_chars", "is_error", "expected"),
    [
        (
            "t1",
            "a",
            "Read",
            {"file_path": "/repo/src/a.ts", "limit": 50},
            100,
            False,
            't1 Read file_path=/repo/src/a.ts limit={"limit":50} → ok 100ch',
        ),
        ("t2", "b", "Grep", {"pattern": "a\n  b"}, 5, True, "t2 Grep pattern=a b → error 5ch"),
    ],
)
def test_compact_call_multikey_ts_vector(
    call_id, tool_call_id, tool, input_data, result_chars, is_error, expected
):
    # TS state.ts:110-120 verbatim semantics: key=value per entry, string
    # values raw (no JSON quotes), non-strings via inputText({k: v}, 200),
    # whitespace flattened PER ENTRY, joined line truncated to 60.
    call = JevToolCall(call_id, tool_call_id, tool, input_data, 1, 2, result_chars, is_error)
    assert _compact_call(call) == expected


def test_apply_rejects_ambiguous_input():
    from hermes_jev_compact.protocol import JevCallDecision, JevError

    messages, calls = _calls(n=2, chars=2000)
    dup_calls = [calls[0], calls[0]]
    dec = [JevCallDecision(id=calls[0].id, tool="t", action="drop_result", reason="x")]
    with pytest.raises(JevError, match="duplicate jev call id"):
        apply_decisions_openai(messages, dec, dup_calls, 300)
    dup_dec = [
        JevCallDecision(id=calls[0].id, tool="t", action="drop_result", reason="x"),
        JevCallDecision(id=calls[0].id, tool="t", action="drop_call", reason="x"),
    ]
    with pytest.raises(JevError, match="duplicate jev decision id"):
        apply_decisions_openai(messages, dup_dec, calls, 300)
    # Unknown decision ids are ignored, not applied.
    unknown = [JevCallDecision(id="tX", tool="t", action="drop_call", reason="x")]
    assert apply_decisions_openai(messages, unknown, calls, 300) == messages


def test_fit_state_throws_when_impossible():
    from hermes_jev_compact.protocol import JevInternalMessage

    messages = [
        JevInternalMessage(index=0, role="user", text="a" * 2000),
        JevInternalMessage(index=1, role="assistant", text="b"),
    ]
    with pytest.raises(ValueError, match="too large"):
        fit_state(messages, [], JevOptions(max_state_tokens=50))
