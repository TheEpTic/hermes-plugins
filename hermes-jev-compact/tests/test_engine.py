"""Engine contract tests: seam override, fallback matrix, deepcopy, validity."""

from __future__ import annotations
import contextlib
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from tests.conftest import (
    HERMES_IMPORTABLE,
    _default_hermes_agent_dir,
    make_tool_transcript,
    needs_hermes,
)

pytestmark = needs_hermes
from hermes_jev_compact.protocol import JevError
from hermes_jev_compact.engine import _valid_openai_sequence
import hermes_jev_compact.engine as eng_mod

if not HERMES_IMPORTABLE:
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

    def factory(base_url: str, key: str, model: str, options: Any, **kw: Any):
        calls_made: List[Dict[str, Any]] = []

        class Fake:

            def ask(self, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
                calls_made.append(questions)
                return {name: {"noul": probs.get(name, 0.9)} for name in questions}

        fake = Fake()
        fake.calls_made = calls_made
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
    assert eng.jev_endpoint_path == "/systemone"
    assert (eng.jev_calls, eng.jev_pruned_units, eng.jev_fallbacks) == (0, 0, 0)
    assert callable(getattr(eng, "update_model", None))
    assert getattr(eng, "api_key", "") == ""
    eng.update_model("test-model", 200000, api_key="«redacted:sk-…»")
    assert eng.api_key == "«redacted:sk-…»"


def test_proactive_path_never_touches_jev(monkeypatch):
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
    expected, expected_count = ContextCompressor.prune_tool_results_only(eng, messages)
    assert (out, count) == (expected, expected_count)
    assert eng.jev_calls == 0 and eng.jev_fallbacks == 0


def test_jev_success_replaces_base_without_double_count(monkeypatch):
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
    assert base_calls == []
    assert count == 3
    assert out is not messages
    assert eng.jev_calls >= 1
    assert eng.jev_pruned_units == 3 and eng.jev_fallbacks == 0
    assert _valid_openai_sequence(out)


@pytest.mark.parametrize(
    "scenario, n_calls, min_chars, probs, invalid_output, low_reduction",
    [
        ("path", 3, 100, 0.1, False, False),
        ("invalid-output", 2, 100, 0.1, True, False),
        ("low-reduction", 3, 1, 0.9, False, True),
        ("high-reduction", 3, 100, 0.1, False, False),
    ],
    ids=lambda row: row[0] if isinstance(row, tuple) else str(row),
)
def test_jev_pruning_scenarios(
    monkeypatch,
    scenario: str,
    n_calls: int,
    min_chars: int,
    probs: float,
    invalid_output: bool,
    low_reduction: bool,
):
    eng = _engine(jev_min_result_chars=min_chars)
    messages = make_tool_transcript(n_calls=n_calls, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    if low_reduction:
        messages.insert(1, {"role": "user", "content": "pad " * 20000})
    probability = {
        f"{kind}_t{i}": probs for i in range(1, n_calls + 1) for kind in ("call", "result")
    }
    if low_reduction:
        probability["result_t1"] = 0.1
    patches = [
        patch(
            "hermes_jev_compact.engine.JevAsker",
            side_effect=lambda *a, **k: _fake_factory(probability)(*a, **k),
        )
    ]
    if invalid_output:
        patches.append(
            patch("hermes_jev_compact.engine._valid_openai_sequence", return_value=False)
        )
    with patches[0], patches[1] if invalid_output else contextlib.nullcontext():
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    if invalid_output or low_reduction:
        expected, expected_count = ContextCompressor._prune_old_tool_results(
            eng, messages, 1, None, 200
        )
        assert (out, count) == (expected, expected_count)
        assert eng.jev_fallbacks == 1
        assert eng.jev_calls == 0
    else:
        assert count == 3
        assert eng.jev_fallbacks == 0
        assert eng.jev_calls >= 1
        assert _valid_openai_sequence(out)
        if scenario == "path":
            assert out is not messages


def test_no_candidates_falls_back_to_super():
    eng = _engine()
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)
    assert eng.jev_fallbacks == 1


def test_missing_key_falls_back(monkeypatch, caplog):
    eng = _engine(jev_min_result_chars=100)
    messages = make_tool_transcript(n_calls=2, result_chars=9000)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with patch("hermes_jev_compact.engine._resolve_secret", return_value=""):
        with caplog.at_level("DEBUG", logger="hermes_jev_compact.engine"):
            out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)
    assert "TYPESAFE_API_KEY" not in caplog.text


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
    eng._compression_cancelled_check = lambda: True
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
        "hermes_jev_compact.engine.JevAsker", side_effect=lambda *a, **k: _fake_factory({})(*a, **k)
    ):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 1, None, 200
    )
    assert (out, count) == (expected, expected_count)


def test_secret_never_cached_on_instance(monkeypatch):
    eng = _engine()
    monkeypatch.setenv("TYPESAFE_API_KEY", "k1")
    messages = make_tool_transcript(n_calls=1, result_chars=9000)
    with patch(
        "hermes_jev_compact.engine.JevAsker", side_effect=lambda *a, **k: _fake_factory({})(*a, **k)
    ):
        eng._prune_old_tool_results(messages, 1, None, 200)
    blob = json.dumps({k: v for k, v in eng.__dict__.items() if "cancel" not in k}, default=str)
    assert "k1" not in blob
    keyed = _engine(api_key="sk-live-should-not-stick")
    assert getattr(keyed, "api_key", "") == ""


def test_deepcopy_safe():
    eng = _engine()
    clone = copy.deepcopy(eng)
    assert clone.name == "jev"
    assert clone is not eng
    assert clone.jev_base_url == eng.jev_base_url
    assert clone.jev_endpoint_path == eng.jev_endpoint_path


def test_engine_passes_endpoint_path_to_asker(monkeypatch):
    # The knob must reach the wire: an OpenRouter-shaped config builds the
    # Decisions URL, not /systemone.
    eng = _engine(
        jev_min_result_chars=100,
        jev_base_url="https://openrouter.ai",
        jev_endpoint_path="/api/alpha/decisions",
        jev_model="typesafe/jev-1.13",
    )
    messages = make_tool_transcript(n_calls=1, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    seen: dict[str, Any] = {}

    class CaptureAsker:
        def __init__(self, base_url: str, key: str, model: str, *a: Any, **k: Any) -> None:
            from hermes_jev_compact.asker import JevAsker as _Real

            real = _Real(base_url, key, model, *a, **k)
            seen["url"] = real._url
            seen["model"] = model

        def ask(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
            return {name: {"noul": 0.1} for name in questions}

    with patch("hermes_jev_compact.engine.JevAsker", CaptureAsker):
        eng._prune_old_tool_results(messages, 1, None, 200)
    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert seen["model"] == "typesafe/jev-1.13"


def test_deepcopy_survives_host_runtime_state():
    import threading

    eng = _engine(jev_keep_threshold=0.7)
    eng._session_db = object()
    eng._compression_cancelled_check = lambda: False
    eng._lock = threading.Lock()
    eng.api_key = "sk-live"
    eng.jev_calls, eng.jev_pruned_units, eng.jev_fallbacks = (3, 5, 1)
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
    eng = _engine(jev_min_result_chars=100)
    n = 4
    messages = make_tool_transcript(n_calls=n, result_chars=5000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    from hermes_jev_compact.protocol import JevOptions as _Opts
    from hermes_jev_compact.adapter import collect_candidates as _cc
    from hermes_jev_compact.adapter import to_internal as _ti
    from hermes_jev_compact.pruner import batch_calls as _bc
    from hermes_jev_compact.pruner import fit_state as _fs

    boundary = eng._prune_boundary(messages, 0, None)
    cands = _cc(messages, boundary, 200)
    assert len(cands) == 4, f"fixture must yield 4 candidates, got {len(cands)}"
    # Excerpt-free candidates: the batching math below is excerpt-agnostic,
    # and the engine runs with excerpts disabled so both agree on the budget.
    eng.jev_result_excerpt_chars = 0
    state_tokens = int(_fs(_ti(messages), cands, _Opts(result_excerpt_chars=0))["tokens"])
    budget = state_tokens + 20 + 2 * 125 + 50
    assert len(_bc(cands, state_tokens, budget)) == 2, "fixture must yield 2 batches"
    eng.jev_max_request_tokens = budget
    ask_order: list[list[str]] = []
    state = {"cancel": False}
    eng._compression_cancelled_check = lambda: state["cancel"]

    class OrderAsker:

        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def ask(self, st: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
            ask_order.append(sorted(questions))
            state["cancel"] = True
            return {name: {"noul": 0.1} for name in questions}

    with patch("hermes_jev_compact.engine.JevAsker", OrderAsker):
        out, count = eng._prune_old_tool_results(messages, 0, None, 200)
    expected, expected_count = ContextCompressor._prune_old_tool_results(
        eng, messages, 0, None, 200
    )
    assert len(ask_order) == 1
    assert (out, count) == (expected, expected_count)
    assert (eng.jev_calls, eng.jev_pruned_units) == (0, 0)
    assert eng.jev_fallbacks == 1


def test_cancel_after_ask_falls_back_and_commits_nothing(monkeypatch):
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

    eng._compression_cancelled_check = lambda: state["cancel"]
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
    ok = make_tool_transcript(1, 100)
    orphan = [m for m in ok if m.get("role") != "tool"]
    assert any((m.get("role") == "assistant" and m.get("tool_calls") for m in orphan))
    assert _valid_openai_sequence(orphan) is False


def test_validity_rejects_duplicate_and_malformed_tool_rows():
    ok = make_tool_transcript(1, 100)
    tool_row = next((m for m in ok if m.get("role") == "tool"))
    duped = [*ok, dict(tool_row)]
    assert _valid_openai_sequence(duped) is False
    malformed = [dict(m) if m is not tool_row else {**m, "tool_call_id": ""} for m in ok]
    assert _valid_openai_sequence(malformed) is False
    missing = [dict(m) if m is not tool_row else {**m, "tool_call_id": "nope"} for m in ok]
    assert _valid_openai_sequence(missing) is False


def _assistant_row_with(calls: Any) -> Dict[str, Any]:
    return {"role": "assistant", "content": "", "tool_calls": calls}


def test_validity_rejects_malformed_and_duplicate_assistant_calls():

    def good(cid: str) -> Dict[str, Any]:
        return {"id": cid, "type": "function", "function": {"name": "t", "arguments": "{}"}}

    def tool_row(cid: str) -> Dict[str, Any]:
        return {"role": "tool", "tool_call_id": cid, "content": "ok"}

    base = [_assistant_row_with([good("x")]), tool_row("x")]
    assert _valid_openai_sequence(base) is True
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
    duped = [
        _assistant_row_with([good("x")]),
        _assistant_row_with([good("x")]),
        tool_row("x"),
        {"role": "tool", "tool_call_id": "x", "content": "ok2"},
    ]
    assert _valid_openai_sequence(duped) is False
    duped_orphan = [
        _assistant_row_with([good("x")]),
        _assistant_row_with([good("x")]),
        tool_row("x"),
    ]
    assert _valid_openai_sequence(duped_orphan) is False
    for bad_container in ({}, "bad", 42, True):
        bad = [{"role": "assistant", "content": "", "tool_calls": bad_container}, tool_row("x")]
        assert _valid_openai_sequence(bad) is False
    assert _valid_openai_sequence([{"role": "assistant", "content": "hi"}, tool_row("x")]) is False
    assert _valid_openai_sequence([{"role": "assistant", "content": "hi"}]) is True
    assert _valid_openai_sequence(["junk"]) is False
    assert _valid_openai_sequence([{"content": "no role"}]) is False
    assert _valid_openai_sequence([{"role": "developer", "content": "x"}]) is False
    ooo = [tool_row("x"), _assistant_row_with([good("x")])]
    assert _valid_openai_sequence(ooo) is False


def test_duplicate_assistant_ids_never_scored(monkeypatch):
    eng = _engine(jev_min_result_chars=1)
    call = {"id": "dup", "type": "function", "function": {"name": "t", "arguments": "{}"}}
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


def _mixed_probs(n_calls: int) -> Dict[str, float]:
    # t1 keep (both high), t2 drop_result (call high, result low), t3+ drop_call (both low).
    probs: Dict[str, float] = {}
    for i in range(1, n_calls + 1):
        if i == 1:
            probs[f"call_t{i}"], probs[f"result_t{i}"] = 0.9, 0.9
        elif i == 2:
            probs[f"call_t{i}"], probs[f"result_t{i}"] = 0.9, 0.1
        else:
            probs[f"call_t{i}"], probs[f"result_t{i}"] = 0.1, 0.1
    return probs


def test_jev_logs_per_decision_details(monkeypatch, caplog):
    eng = _engine(jev_min_result_chars=100, quiet_mode=False)
    messages = make_tool_transcript(n_calls=3, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    probs = _mixed_probs(3)
    with patch(
        "hermes_jev_compact.engine.JevAsker",
        side_effect=lambda *a, **k: _fake_factory(probs)(*a, **k),
    ):
        with caplog.at_level("INFO", logger="hermes_jev_compact.engine"):
            eng._prune_old_tool_results(messages, 1, None, 200)
    lines = [r.getMessage() for r in caplog.records if r.name == "hermes_jev_compact.engine"]
    per_call = [line for line in lines if line.startswith("jev decision:")]
    assert len(per_call) == 3
    assert "read_file" in per_call[0] and "keep" in per_call[0]
    assert "0.90" in per_call[0] and "9000" in per_call[0]
    assert "drop_result" in per_call[1] and "drop_call" in per_call[2]
    # No result content and no secret material in the log lines.
    blob = "\n".join(per_call)
    assert "x" * 20 not in blob
    assert "TYPESAFE_API_KEY" not in blob and "test-key" not in blob


def test_jev_per_decision_log_respects_quiet_mode(monkeypatch, caplog):
    eng = _engine(jev_min_result_chars=100, quiet_mode=True)
    messages = make_tool_transcript(n_calls=2, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    probs = {f"{kind}_t{i}": 0.1 for i in (1, 2) for kind in ("call", "result")}
    with patch(
        "hermes_jev_compact.engine.JevAsker",
        side_effect=lambda *a, **k: _fake_factory(probs)(*a, **k),
    ):
        with caplog.at_level("INFO", logger="hermes_jev_compact.engine"):
            eng._prune_old_tool_results(messages, 1, None, 200)
    assert "jev decision:" not in caplog.text


def test_jev_counters_split_keep_truncate_drop(monkeypatch):
    eng = _engine(jev_min_result_chars=100)
    messages = make_tool_transcript(n_calls=3, result_chars=9000)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    probs = _mixed_probs(3)
    with patch(
        "hermes_jev_compact.engine.JevAsker",
        side_effect=lambda *a, **k: _fake_factory(probs)(*a, **k),
    ):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    assert count == 2
    assert eng.jev_pruned_units == 2  # truncate + drop, kept unit excluded
    assert eng.jev_kept_units == 1
    assert eng.jev_truncate_units == 1
    assert eng.jev_drop_units == 1
    assert _valid_openai_sequence(out)


def test_jev_gate_fallback_logs_achieved_ratio(monkeypatch, caplog):
    eng = _engine(jev_min_result_chars=1)
    messages = make_tool_transcript(n_calls=3, result_chars=9000)
    messages.insert(1, {"role": "user", "content": "pad " * 20000})
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    probs = {f"{kind}_t{i}": 0.9 for i in (1, 2, 3) for kind in ("call", "result")}
    probs["result_t1"] = 0.1
    with patch(
        "hermes_jev_compact.engine.JevAsker",
        side_effect=lambda *a, **k: _fake_factory(probs)(*a, **k),
    ):
        with caplog.at_level("INFO", logger="hermes_jev_compact.engine"):
            eng._prune_old_tool_results(messages, 1, None, 200)
    gate = [r.getMessage() for r in caplog.records if "reduction under" in r.getMessage()]
    assert len(gate) == 1
    assert "25%" in gate[0]
    assert "got " in gate[0] and "%" in gate[0].split("got ", 1)[1][:8]


def test_init_sets_tuning_defaults_and_deepcopy_carries_them():
    eng = _engine(jev_error_keep_threshold=0.1, jev_min_result_chars=1234)
    assert eng.jev_error_keep_threshold == 0.1
    assert eng.jev_min_result_chars == 1234
    clone = copy.deepcopy(eng)
    assert clone.jev_error_keep_threshold == 0.1
    assert clone.jev_min_result_chars == 1234
    assert _engine().jev_error_keep_threshold == 0.25
    assert _engine().jev_min_result_chars == 2000


def test_jev_keeps_error_unit_where_plain_unit_drops(monkeypatch):
    eng = _engine(jev_min_result_chars=100)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "fix it"},
        {
            "role": "assistant",
            "content": "run a",
            "tool_calls": [
                {
                    "id": "e1",
                    "type": "function",
                    "function": {"name": "run_tests", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "e1",
            "content": "Traceback FAILED: assertion error " + "E" * 9000,
        },
        {
            "role": "assistant",
            "content": "run b",
            "tool_calls": [
                {
                    "id": "n1",
                    "type": "function",
                    "function": {"name": "list_files", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "n1", "content": "ok listing " + "L" * 9000},
        {"role": "user", "content": "tail"},
    ]
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    probs = {"call_t1": 0.3, "result_t1": 0.3, "call_t2": 0.3, "result_t2": 0.3}
    with patch(
        "hermes_jev_compact.engine.JevAsker",
        side_effect=lambda *a, **k: _fake_factory(probs)(*a, **k),
    ):
        out, count = eng._prune_old_tool_results(messages, 1, None, 200)
    assert count == 1
    rows = [m for m in out if isinstance(m, dict)]
    assert any(
        m.get("tool_call_id") == "e1" and "Traceback" in str(m.get("content", "")) for m in rows
    )
    assert not any(m.get("tool_call_id") == "n1" for m in rows)
    assert eng.jev_kept_units == 1 and eng.jev_drop_units == 1
