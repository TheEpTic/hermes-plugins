"""Engine contract tests: seam override, fallback matrix, deepcopy, validity."""

from __future__ import annotations

import contextlib  # noqa: E402
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tests.conftest import (  # noqa: E402
    HERMES_IMPORTABLE,
    _default_hermes_agent_dir,
    make_tool_transcript,
    needs_hermes,
)

pytestmark = needs_hermes

from hermes_jev_compact.protocol import JevError  # noqa: E402
from hermes_jev_compact.engine import _valid_openai_sequence  # noqa: E402
import hermes_jev_compact.engine as eng_mod  # noqa: E402

if not HERMES_IMPORTABLE:  # pragma: no cover - hermes missing; tests skip via needs_hermes
    ContextCompressor = eng_mod.ContextCompressor
    JevContextCompressor = eng_mod.JevContextCompressor
else:
    _HOST_DIR = str(_default_hermes_agent_dir())
    sys.path.insert(0, _HOST_DIR)
    try:
        import importlib

        import agent.context_compressor as host_mod

        importlib.reload(eng_mod)
        host_cls = host_mod.ContextCompressor
        assert eng_mod.ContextCompressor is host_cls, "engine did not bind the host base"
        ContextCompressor = host_cls
        JevContextCompressor = eng_mod.JevContextCompressor
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(_HOST_DIR)


def _engine(**kw: Any) -> Any:
    params: Dict[str, Any] = {"model": "test-model", "quiet_mode": True}
    params.update(kw)
    return JevContextCompressor(**params)


def _fake_factory(probs: Dict[str, float]):
    def factory(base_url: str, key: str, model: str, options: Any):
        calls_made: List[Dict[str, Any]] = []

        class Fake:
            def ask(self, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
                calls_made.append(questions)
                return {name: {"noul": probs.get(name, 0.9)} for name in questions}

        fake = Fake()
        fake.calls_made = calls_made  # type: ignore[attr-defined]
        return fake

    return factory


def test_name_and_subclass():
    eng = _engine()
    assert eng.name == "jev"
    assert isinstance(eng, ContextCompressor)


def test_init_forwards_base_params_and_sets_jev_defaults():
    eng = _engine(threshold_percent=0.9, protect_last_n=5)
    assert eng.threshold_percent == 0.9
    assert eng.protect_last_n == 5
    assert eng.jev_keep_threshold == 0.5
    assert eng.jev_model == "jev-latest"
    assert (eng.jev_calls, eng.jev_pruned_units, eng.jev_fallbacks) == (0, 0, 0)
    # Host contract: update_model exists so the host can re-supply the chat key
    # per agent (summary LLM path); our instances keep api_key stripped.
    assert callable(getattr(eng, "update_model", None))
    assert getattr(eng, "api_key", "") == ""
    # ...and update_model is the ONLY way a key lands: it forwards to the host
    # (host :2308-2314 stores api_key there), which is exactly the per-agent
    # path the host itself uses at agent_init.py:1849-1852.
    eng.update_model("test-model", 200_000, api_key="«redacted:sk-…»")
    assert eng.api_key == "«redacted:sk-…»"


def test_proactive_path_never_touches_jev(monkeypatch):
    # Host contract (host :2987 + turn_preflight.py:364): prune_tool_results_only
    # is deterministic/no-LLM and fires on the hot post-tool path. The engine
    # must bypass Jev entirely — no secret read, no network — and behave
    # exactly like the base implementation.
    eng = _engine(jev_min_result_chars=1)
    messages = make_tool_transcript(n_calls=2, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with (
        patch("hermes_jev_compact.engine._resolve_secret") as secret,
        patch("hermes_jev_compact.engine.JevAsker") as asker_cls,
    ):
        out, count = eng.prune_tool_results_only(messages)
    secret.assert_not_called()
    asker_cls.assert_not_called()
    expected, expected_count = ContextCompressor.prune_tool_results_only(eng, messages)  # type: ignore[attr-defined]
    assert (out, count) == (expected, expected_count)
    assert eng.jev_calls == 0 and eng.jev_fallbacks == 0


def test_jev_success_replaces_base_without_double_count(monkeypatch):
    # Jev REPLACES the base prune (never jev-then-base): base must not run on
    # jev's output, and the count must be exactly the jev drop/truncate count.
    eng = _engine(jev_min_result_chars=100)
    messages = make_tool_transcript(n_calls=3, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    probs = {}
    for i in range(1, 4):
        probs[f"call_t{i}"] = 0.1
        probs[f"result_t{i}"] = 0.1
    calls: list[Any] = []

    class SpyAsker:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def ask(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
            calls.append(questions)
            return {name: {"noul": probs[name]} for name in questions}

    orig = ContextCompressor._prune_old_tool_results
    base_calls: list[Any] = []

    def spy_base(self: Any, *a: Any, **k: Any) -> Any:
        base_calls.append((a, k))
        return orig(self, *a, **k)

    with (
        patch("hermes_jev_compact.engine.JevAsker", SpyAsker),
        patch.object(ContextCompressor, "_prune_old_tool_results", spy_base),
    ):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    assert base_calls == []  # base never ran on jev's output
    assert count == 3  # exactly the 3 jev drop_call units, no extras
    assert out is not messages
    assert eng.jev_calls >= 1
    assert eng.jev_pruned_units == 3 and eng.jev_fallbacks == 0
    assert _valid_openai_sequence(out)


def test_jev_path_prunes_and_counts(monkeypatch):
    eng = _engine(jev_min_result_chars=100)
    messages = make_tool_transcript(n_calls=3, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    probs = {}
    for i in range(1, 4):
        probs[f"call_t{i}"] = 0.1
        probs[f"result_t{i}"] = 0.1
    with patch(
        "hermes_jev_compact.engine.JevAsker",
        side_effect=lambda *a, **k: _fake_factory(probs)(*a, **k),
    ):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    assert count == 3  # exactly the jev units; base never re-runs on success
    assert out is not messages
    assert eng.jev_calls >= 1
    assert _valid_openai_sequence(out)


def test_no_candidates_falls_back_to_super():
    eng = _engine()
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)
    assert eng.jev_fallbacks == 1


def test_missing_key_falls_back(monkeypatch):
    eng = _engine(jev_min_result_chars=100)
    messages = make_tool_transcript(n_calls=2, result_chars=9000)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with patch("hermes_jev_compact.engine._resolve_secret", return_value=""):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)


def test_jev_error_falls_back(monkeypatch):
    eng = _engine(jev_min_result_chars=100)
    messages = make_tool_transcript(n_calls=2, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")

    class Boom:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def ask(self, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
            raise JevError("down")

    with patch("hermes_jev_compact.engine.JevAsker", Boom):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)


def test_cancel_consult_falls_back(monkeypatch):
    eng = _engine(jev_min_result_chars=100)
    eng._compression_cancelled_check = lambda: True  # type: ignore[attr-defined]
    messages = make_tool_transcript(n_calls=2, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with patch("hermes_jev_compact.engine.JevAsker") as asker_cls:
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    asker_cls.assert_not_called()
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)


def test_keep_everything_falls_back(monkeypatch):
    eng = _engine(jev_min_result_chars=100)
    messages = make_tool_transcript(n_calls=2, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with patch(
        "hermes_jev_compact.engine.JevAsker",
        side_effect=lambda *a, **k: _fake_factory({})(*a, **k),
    ):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    # jev kept all → super() result (deterministic demote may still fire)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)


def test_secret_never_cached_on_instance(monkeypatch):
    eng = _engine()
    monkeypatch.setenv("TYPESAFE_API_KEY", "k1")
    messages = make_tool_transcript(n_calls=1, result_chars=9000)
    with patch(
        "hermes_jev_compact.engine.JevAsker",
        side_effect=lambda *a, **k: _fake_factory({})(*a, **k),
    ):
        eng._prune_old_tool_results(messages, 1, None, 200)
    blob = json.dumps({k: v for k, v in eng.__dict__.items() if "cancel" not in k}, default=str)
    assert "k1" not in blob
    # register() never passes a real chat key, and __init__ strips any it gets.
    keyed = _engine(api_key="sk-live-should-not-stick")
    assert getattr(keyed, "api_key", "") == ""


def test_deepcopy_safe():
    eng = _engine()
    clone = copy.deepcopy(eng)
    assert clone.name == "jev"
    assert clone is not eng
    assert clone.jev_base_url == eng.jev_base_url


def test_deepcopy_survives_host_runtime_state():
    # The host installs uncopyable runtime state on the shared singleton
    # (_session_db handle, cancellation callback bound to the parent agent).
    # The allowlist copy must succeed, drop the callback + db handle, strip
    # api_key, and keep policy + counters.
    import threading

    eng = _engine(jev_keep_threshold=0.7)
    eng._session_db = object()  # handle stand-in dropped by the allowlist
    eng._compression_cancelled_check = lambda: False  # noqa: E731
    eng._lock = threading.Lock()  # genuinely uncopyable
    eng.api_key = "sk-live"
    eng.jev_calls, eng.jev_pruned_units, eng.jev_fallbacks = 3, 5, 1
    clone = copy.deepcopy(eng)
    assert clone.name == "jev"
    assert clone.jev_keep_threshold == 0.7
    assert (clone.jev_calls, clone.jev_pruned_units, clone.jev_fallbacks) == (3, 5, 1)
    assert getattr(clone, "api_key", "") == ""
    assert getattr(clone, "_compression_cancelled_check", None) is None
    assert getattr(clone, "_session_db", None) is None
    assert getattr(clone, "_lock", None) is None
    assert getattr(clone, "_session_id", "") == ""


def test_sequential_batches_cancel_between_asks(monkeypatch):
    # DELIBERATE drift pin (engine._ask_batches): batches run SEQUENTIALLY
    # (TS uses Promise.all, compact.ts:275-278) so cancellation stops between
    # asks. Budget the questions for exactly two asks: ~125 tokens per call,
    # so max_request_tokens = state + 20 (overhead) + 2*125 + slack fits two
    # calls per batch max; with n=4 calls that is two batches. Cancelling
    # after the first ask must prevent the second and fall back clean.
    eng = _engine(jev_min_result_chars=100)
    n = 4
    messages = make_tool_transcript(n_calls=n, result_chars=5000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    from hermes_jev_compact.protocol import JevOptions as _Opts

    from hermes_jev_compact.adapter import collect_candidates as _cc
    from hermes_jev_compact.adapter import to_internal as _ti
    from hermes_jev_compact.pruner import batch_calls as _bc
    from hermes_jev_compact.pruner import fit_state as _fs

    # protect_tail_count=0 so the host boundary leaves all 4 pairs eligible;
    # verify with the REAL boundary the engine will use.
    boundary = eng._prune_boundary(messages, 0, None)
    cands = _cc(messages, boundary, 200)
    assert len(cands) == 4, f"fixture must yield 4 candidates, got {len(cands)}"
    state_tokens = int(_fs(_ti(messages), cands, _Opts())["tokens"])
    budget = state_tokens + 20 + 2 * 125 + 50
    assert len(_bc(cands, state_tokens, budget)) == 2, "fixture must yield 2 batches"
    eng.jev_max_request_tokens = budget
    ask_order: list[list[str]] = []
    state = {"cancel": False}
    eng._compression_cancelled_check = lambda: state["cancel"]  # noqa: E731

    class OrderAsker:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def ask(self, st: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
            ask_order.append(sorted(questions))
            state["cancel"] = True  # cancel lands after ask 1 returns
            return {name: {"noul": 0.1} for name in questions}

    with patch("hermes_jev_compact.engine.JevAsker", OrderAsker):
        out, count = eng._prune_old_tool_results(messages, 0, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 0, None, 200
    )
    # Cancel-after-ask raised JevError -> deterministic fallback, ask 2 never ran.
    assert len(ask_order) == 1
    assert (out, count) == (expected, expected_count)
    assert (eng.jev_calls, eng.jev_pruned_units) == (0, 0)
    assert eng.jev_fallbacks == 1


def test_cancel_after_ask_falls_back_and_commits_nothing(monkeypatch):
    # Cancellation landing DURING the jev round-trip must discard the answers:
    # no counters, no jev output — straight to the deterministic prune.
    eng = _engine(jev_min_result_chars=100)
    messages = make_tool_transcript(n_calls=2, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    state = {"cancel": False}

    class SlowCancelAsker:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def ask(self, st: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
            state["cancel"] = True
            return {name: {"noul": 0.1} for name in questions}

    eng._compression_cancelled_check = lambda: state["cancel"]  # noqa: E731
    with patch("hermes_jev_compact.engine.JevAsker", SlowCancelAsker):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)
    assert (eng.jev_calls, eng.jev_pruned_units) == (0, 0)
    assert eng.jev_fallbacks == 1


def test_validity_checker():
    ok = make_tool_transcript(1, 100)
    assert _valid_openai_sequence(ok) is True
    bad = [m for m in ok if not (m.get("role") == "assistant" and m.get("tool_calls"))]
    assert _valid_openai_sequence(bad) is False


def test_validity_rejects_orphan_assistant_call():
    # A tool RESULT dropped while its assistant tool_calls entry survives is
    # an invalid openai sequence (orphan call) — must fail closed to super().
    ok = make_tool_transcript(1, 100)
    orphan = [m for m in ok if m.get("role") != "tool"]
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in orphan)
    assert _valid_openai_sequence(orphan) is False


def test_validity_rejects_duplicate_and_malformed_tool_rows():
    ok = make_tool_transcript(1, 100)
    tool_row = next(m for m in ok if m.get("role") == "tool")
    duped = [*ok, dict(tool_row)]
    assert _valid_openai_sequence(duped) is False
    malformed = [dict(m) if m is not tool_row else {**m, "tool_call_id": ""} for m in ok]
    assert _valid_openai_sequence(malformed) is False
    missing = [dict(m) if m is not tool_row else {**m, "tool_call_id": "nope"} for m in ok]
    assert _valid_openai_sequence(missing) is False


def _assistant_row_with(calls: Any) -> Dict[str, Any]:
    return {"role": "assistant", "content": "", "tool_calls": calls}


def test_validity_rejects_malformed_and_duplicate_assistant_calls():
    # The fail-closed gate validates the ASSISTANT side too (host shape:
    # id/type/function.name/arguments) and rejects duplicate call ids —
    # jev output below this bar must never commit.
    def good(cid: str) -> Dict[str, Any]:
        return {
            "id": cid,
            "type": "function",
            "function": {"name": "t", "arguments": "{}"},
        }

    def tool_row(cid: str) -> Dict[str, Any]:
        return {"role": "tool", "tool_call_id": cid, "content": "ok"}

    base = [_assistant_row_with([good("x")]), tool_row("x")]
    assert _valid_openai_sequence(base) is True
    # Missing type / function map / name / arguments — even WITH a matching
    # result row, each must fail.
    for bad_tc in (
        {"id": "x"},
        {"id": "x", "type": "function"},
        {"id": "x", "type": "function", "function": {}},
        {"id": "x", "type": "function", "function": {"name": "", "arguments": "{}"}},
        {"id": "x", "type": "function", "function": {"name": "t"}},
        {"id": "x", "type": "wat", "function": {"name": "t", "arguments": "{}"}},
        {"id": "", "type": "function", "function": {"name": "t", "arguments": "{}"}},
        "not-a-dict",
    ):
        assert _valid_openai_sequence([_assistant_row_with([bad_tc]), tool_row("x")]) is False
    # Duplicate assistant ids collapse ambiguity — reject even fully paired.
    duped = [
        _assistant_row_with([good("x")]),
        _assistant_row_with([good("x")]),
        tool_row("x"),
        {"role": "tool", "tool_call_id": "x", "content": "ok2"},
    ]
    assert _valid_openai_sequence(duped) is False
    # ...and duplicate assistant ids WITHOUT a matching second result also fail.
    duped_orphan = [
        _assistant_row_with([good("x")]),
        _assistant_row_with([good("x")]),
        tool_row("x"),
    ]
    assert _valid_openai_sequence(duped_orphan) is False
    # ...and a PRESENT-but-non-list tool_calls container fails closed:
    # {} / "bad" / 42 / True must not slip through as "no tool calls".
    # (None is deliberately allowed: host treats it as absent, see engine.)
    for bad_container in ({}, "bad", 42, True):
        bad = [
            {"role": "assistant", "content": "", "tool_calls": bad_container},
            tool_row("x"),
        ]
        assert _valid_openai_sequence(bad) is False
    # Absent tool_calls key (plain assistant text) is still fine.
    assert (
        _valid_openai_sequence([{"role": "assistant", "content": "hi"}, tool_row("x")])
        is False  # orphan tool row, no call — fails for the RIGHT reason
    )
    assert _valid_openai_sequence([{"role": "assistant", "content": "hi"}]) is True
    # Malformed top-level rows fail closed (never committed as jev output).
    assert _valid_openai_sequence(["junk"]) is False
    assert _valid_openai_sequence([{"content": "no role"}]) is False
    assert _valid_openai_sequence([{"role": "developer", "content": "x"}]) is False
    # ...as does a result ordered BEFORE its call.
    ooo = [tool_row("x"), _assistant_row_with([good("x")])]
    assert _valid_openai_sequence(ooo) is False


def test_duplicate_assistant_ids_never_scored(monkeypatch):
    # End-to-end through _jev_prune: a transcript whose only pairs use a
    # duplicated assistant id must NOT reach jev at all (ambiguous address) —
    # straight to the deterministic fallback with zero jev counters.
    eng = _engine(jev_min_result_chars=1)
    call = {
        "id": "dup",
        "type": "function",
        "function": {"name": "t", "arguments": "{}"},
    }
    messages = [
        {"role": "user", "content": "go"},
        dict(_assistant_row_with([dict(call)])),
        dict(_assistant_row_with([dict(call)])),
        {"role": "tool", "tool_call_id": "dup", "content": "D" * 9000},
        {"role": "user", "content": "tail"},
    ]
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with patch("hermes_jev_compact.engine.JevAsker") as asker_cls:
        out, count = eng._prune_old_tool_results(messages, 1, None, 1)
    asker_cls.assert_not_called()
    expected, expected_count = ContextCompressor._prune_old_tool_results(eng, messages, 1, None, 1)
    assert (out, count) == (expected, expected_count)
    assert (eng.jev_calls, eng.jev_pruned_units) == (0, 0)


def test_invalid_jev_output_falls_back(monkeypatch):
    # Even if jev says drop_call for everything reachable, a malformed apply
    # (simulated by forcing the checker to fail) must fall back to super().
    eng = _engine(jev_min_result_chars=100)
    messages = make_tool_transcript(n_calls=2, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    probs = {}
    for i in range(1, 3):
        probs[f"call_t{i}"] = 0.1
        probs[f"result_t{i}"] = 0.1
    with (
        patch(
            "hermes_jev_compact.engine.JevAsker",
            side_effect=lambda *a, **k: _fake_factory(probs)(*a, **k),
        ),
        patch("hermes_jev_compact.engine._valid_openai_sequence", return_value=False),
    ):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)
    assert eng.jev_fallbacks == 1
    assert eng.jev_calls == 0


def test_low_reduction_falls_back_to_super(monkeypatch):
    # TS README parity: reductionRatio < 0.25 → "not worth it". A jev pass
    # that drops one small unit out of a big transcript must NOT commit —
    # the deterministic prune runs instead (and gets the fallback credit).
    eng = _engine(jev_min_result_chars=1)
    messages = make_tool_transcript(n_calls=3, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    # Keep 2 units, drop_result on 1: ~8.6k of ~27k chars ≈ 32%... so make
    # the drop smaller: truncate head 300 of ONE 9000-char result while the
    # other two stay — then pad with a big user row to sink under 25%.
    messages.insert(1, {"role": "user", "content": "pad " * 20000})
    probs = {}
    for i in range(1, 4):
        probs[f"call_t{i}"] = 0.9
        probs[f"result_t{i}"] = 0.9
    probs["result_t1"] = 0.1  # one drop_result ≈ 8.7k chars of ~117k total
    with patch(
        "hermes_jev_compact.engine.JevAsker",
        side_effect=lambda *a, **k: _fake_factory(probs)(*a, **k),
    ):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)
    assert eng.jev_fallbacks == 1
    assert eng.jev_calls == 0  # nothing committed, no jev credit


def test_high_reduction_commits_jev_output(monkeypatch):
    # Control for the above: drop everything → >25% → jev commits.
    eng = _engine(jev_min_result_chars=100)
    messages = make_tool_transcript(n_calls=3, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    probs = {}
    for i in range(1, 4):
        probs[f"call_t{i}"] = 0.1
        probs[f"result_t{i}"] = 0.1
    with patch(
        "hermes_jev_compact.engine.JevAsker",
        side_effect=lambda *a, **k: _fake_factory(probs)(*a, **k),
    ):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    assert count == 3
    assert eng.jev_fallbacks == 0
    assert eng.jev_calls >= 1
    assert _valid_openai_sequence(out)
