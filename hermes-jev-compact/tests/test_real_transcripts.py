"""Real-transcript shape tests (issue #30) + proactive-path no-network contract.

Real Hermes transcripts carry rows jev never touches: bookkeeping roles
(``session_meta``), rewind/retry replays that reuse one tool_call_id, and
orphaned replay artifacts. The commit gate must judge only the damage jev
itself could do; and removals must never leave adjacent assistant rows.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from tests.conftest import HERMES_IMPORTABLE, _default_hermes_agent_dir  # noqa: E402

pytestmark = pytest.mark.skipif(not HERMES_IMPORTABLE, reason="hermes host not importable")

import hermes_jev_compact.engine as eng_mod  # noqa: E402
from hermes_jev_compact.engine import _commit_valid  # noqa: E402
from tests.conftest import _valid_openai_sequence  # noqa: E402
from hermes_jev_compact.pruner import apply_decisions_openai  # noqa: E402
from hermes_jev_compact.protocol import JevCallDecision, JevToolCall  # noqa: E402

if HERMES_IMPORTABLE:
    _HOST_DIR = str(_default_hermes_agent_dir())
    sys.path.insert(0, _HOST_DIR)
    try:
        import importlib

        import agent.context_compressor as host_mod

        importlib.reload(eng_mod)
        ContextCompressor = host_mod.ContextCompressor
        JevContextCompressor = eng_mod.JevContextCompressor
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(_HOST_DIR)


def _tc(cid: str, name: str = "read_file") -> dict[str, Any]:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}


def _call(cid: str, content: str = "") -> dict[str, Any]:
    return {"role": "assistant", "content": content, "tool_calls": [_tc(cid)]}


def _result(cid: str, chars: int = 9000) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": cid, "content": "x" * chars}


def _irregular_transcript() -> list[dict[str, Any]]:
    """Scoreable pairs surrounded by the irregularities issue #30 reports."""
    return [
        {"role": "system", "content": "sys"},
        {"role": "session_meta", "content": "{}"},
        {"role": "user", "content": "go"},
        _call("a"),
        _result("a"),
        # rewind replay: the same call id issued twice, answered twice
        _call("dup"),
        _result("dup", 50),
        _call("dup"),
        _result("dup", 50),
        _call("b"),
        _result("b"),
        # orphan result from a superseded replay
        {"role": "tool", "tool_call_id": "gone", "content": "stale"},
        _call("c"),
        _result("c"),
        {"role": "user", "content": "tail"},
    ]


def _engine(**kw: Any) -> Any:
    params: dict[str, Any] = {"model": "test-model", "quiet_mode": True}
    params.update(kw)
    return JevContextCompressor(**params)


class _DropAll:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    def ask(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        return {name: {"noul": 0.05} for name in questions}


def test_irregular_input_is_invalid_under_the_strict_checker():
    # Precondition for the regression: the old whole-output gate rejects the
    # PRISTINE input, so it discarded every scored pass on such transcripts.
    assert _valid_openai_sequence(_irregular_transcript()) is False


def test_jev_commits_on_irregular_real_transcript(monkeypatch):
    eng = _engine(jev_min_result_chars=1000, jev_min_reduction_ratio=0.0)
    messages = _irregular_transcript()
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with patch("hermes_jev_compact.engine.JevAsker", _DropAll):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    assert eng.jev_fallbacks == 0, "jev work was discarded on irregular input"
    assert count >= 3 and eng.jev_calls >= 1
    # jev-owned pairs are gone; irregular rows survive untouched, by identity
    ids = [m.get("tool_call_id") for m in out if m.get("role") == "tool"]
    assert "a" not in ids and "b" not in ids and "c" not in ids
    assert ids.count("dup") == 2 and "gone" in ids
    assert any(m is messages[1] for m in out), "session_meta row must keep identity"
    assert _commit_valid(messages, out)


def test_apply_merges_assistants_left_adjacent_by_removals():
    messages = [
        {"role": "user", "content": "go"},
        {**_call("a", "calling a"), "_db_persisted": True},
        _result("a"),
        {
            "role": "assistant",
            "content": "done",
            "reasoning_content": "r2",
            "api_content": "old",
        },
        {"role": "user", "content": "tail"},
    ]
    call = JevToolCall(
        id="t1",
        tool_call_id="a",
        tool="read_file",
        input={},
        call_index=1,
        result_index=2,
        result_chars=9000,
        is_error=False,
    )
    decision = JevCallDecision(id="t1", tool="read_file", action="drop_call", reason="call_dropped")
    out = apply_decisions_openai(messages, [decision], [call], 300)
    roles = [m["role"] for m in out]
    assert roles == ["user", "assistant", "user"], roles
    merged = out[1]
    assert merged["content"] == "calling a\ndone"
    assert "tool_calls" not in merged
    assert merged["reasoning_content"] == "r2"
    assert "_db_persisted" not in merged and "api_content" not in merged
    # source rows are never mutated in place
    assert messages[1]["tool_calls"] and messages[1]["_db_persisted"] is True
    assert messages[3]["content"] == "done"


def test_apply_leaves_preexisting_adjacent_assistants_alone():
    first = {"role": "assistant", "content": "one"}
    second = {"role": "assistant", "content": "two"}
    messages = [{"role": "user", "content": "go"}, first, second, _call("a"), _result("a")]
    call = JevToolCall("t1", "a", "read_file", {}, 3, 4, 9000, False)
    decision = JevCallDecision(id="t1", tool="read_file", action="drop_call", reason="call_dropped")
    out = apply_decisions_openai(messages, [decision], [call], 300)
    assert out[1] is first and out[2] is second


def test_apply_never_merges_codex_interim_rows():
    interim = {"role": "assistant", "content": "", "codex_reasoning_items": [{"x": 1}]}
    messages = [{"role": "user", "content": "go"}, interim, _call("a"), _result("a")]
    messages.append({"role": "assistant", "content": "after"})
    call = JevToolCall("t1", "a", "read_file", {}, 2, 3, 9000, False)
    decision = JevCallDecision(id="t1", tool="read_file", action="drop_call", reason="call_dropped")
    out = apply_decisions_openai(messages, [decision], [call], 300)
    assert out[1] is interim and len(out) == 3


@pytest.mark.parametrize(
    "mutate",
    [
        "drop_session_meta",
        "orphan_new_result",
        "drop_irregular_result",
        "split_pair",
        "new_adjacent_assistants",
        "invent_id",
    ],
)
def test_commit_gate_still_rejects_damage(mutate: str):
    before = _irregular_transcript()
    after = list(before)
    if mutate == "drop_session_meta":
        after.pop(1)
    elif mutate == "orphan_new_result":
        after.pop(3)  # call "a" gone, result "a" stays
    elif mutate == "drop_irregular_result":
        after.pop(6)  # one of the "dup" results
    elif mutate == "split_pair":
        after[3], after[4] = after[4], after[3]  # result before call
    elif mutate == "new_adjacent_assistants":
        after.insert(4, {"role": "assistant", "content": "x"})
        after.pop(5)  # drop result "a" → orphaned call AND adjacency
    elif mutate == "invent_id":
        after.append(_call("zzz"))
    assert _commit_valid(before, after) is False


def _pair(cid: str) -> list[dict[str, Any]]:
    return [
        _call(cid, content=cid),
        {"role": "tool", "tool_call_id": cid, "content": "x"},
    ]


def test_commit_gate_rejects_reordered_pairs():
    user = {"role": "user", "content": "go"}
    before = [user, *_pair("a"), *_pair("b")]
    assert _commit_valid(before, [user, *_pair("b"), *_pair("a")]) is False
    # a pair moved across a non-tool row is also a reorder
    before = [*_pair("a"), user, *_pair("b")]
    assert _commit_valid(before, [*_pair("a"), *_pair("b"), user]) is False
    assert _commit_valid(before, [user, *_pair("a"), *_pair("b")]) is False


def test_commit_gate_accepts_clean_drop_and_truncate():
    before = _irregular_transcript()
    after = [m for m in before if m.get("tool_call_id") != "a" and m is not before[3]]
    after = [({**m, "content": "short"} if m.get("tool_call_id") == "b" else m) for m in after]
    assert _commit_valid(before, after) is True


def test_proactive_prune_never_reaches_jev(monkeypatch):
    # The host's prune_tool_results_only calls self._prune_old_tool_results —
    # the jev seam. The proactive path is documented no-LLM/no-network.
    eng = _engine(jev_min_result_chars=100, protect_last_n=1)
    eng.proactive_prune_tokens = 1
    eng.proactive_prune_min_reclaim_tokens = 0
    eng.proactive_prune_min_result_chars = 200
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
    for i in range(6):
        messages += [_call(f"p{i}", f"step {i}"), _result(f"p{i}")]
    messages.append({"role": "user", "content": "tail"})
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with (
        patch("hermes_jev_compact.engine._resolve_secret") as secret,
        patch("hermes_jev_compact.engine.JevAsker") as asker_cls,
    ):
        out, count = eng.prune_tool_results_only(messages, current_tokens=10**6)
    secret.assert_not_called()
    asker_cls.assert_not_called()
    assert count > 0 and out is not messages, "fixture must actually prune"
    assert eng.jev_fallbacks == 0 and eng.jev_calls == 0
    assert getattr(eng, "_jev_bypass_depth", 0) == 0
    # a later full compression still takes the jev seam
    with patch("hermes_jev_compact.engine.JevAsker", _DropAll):
        eng._prune_old_tool_results(messages, 1, None, 200)
    assert eng.jev_calls >= 1
