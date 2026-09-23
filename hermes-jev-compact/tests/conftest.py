"""Test fixtures for hermes-jev-compact."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest  # noqa: E402


def _default_hermes_agent_dir() -> Path:
    """Hermes checkout: $HERMES_AGENT_DIR, else ~/.hermes/hermes-agent."""
    override = os.environ.get("HERMES_AGENT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes" / "hermes-agent"


_HERMES_AGENT_DIR = _default_hermes_agent_dir()
_HERMES_VENV_SITE = _HERMES_AGENT_DIR / "venv" / "lib"
_HERMES_DEPS: list[str] = []  # importable third-party deps hermes pulls (yaml, ...)


def _hermes_importable() -> bool:
    """True when the hermes source tree + its deps import in THIS interpreter."""
    global _HERMES_DEPS
    if not (_HERMES_AGENT_DIR / "agent" / "context_compressor.py").is_file():
        return False
    try:
        import yaml  # noqa: F401
    except ImportError:
        for lib in sorted(_HERMES_VENV_SITE.glob("python*/site-packages")):
            if (lib / "yaml").is_dir():
                _HERMES_DEPS.append(str(lib))
                sys.path.insert(0, str(lib))
                break
        try:
            import yaml  # noqa: F401
        except ImportError:
            return False
    try:
        sys.path.insert(0, str(_HERMES_AGENT_DIR))
        import agent.context_compressor  # noqa: F401

        return True
    except Exception:
        return False
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(_HERMES_AGENT_DIR))


HERMES_IMPORTABLE = _hermes_importable()

needs_hermes = pytest.mark.skipif(
    not HERMES_IMPORTABLE,
    reason="hermes source tree (+yaml) not importable in this interpreter",
)


def make_tool_transcript(n_calls: int = 3, result_chars: int = 9000) -> List[Dict[str, Any]]:
    """OpenAI-format transcript: system + user + n assistant(tool_calls)/tool pairs + tail user."""
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": "you are a test assistant"},
        {"role": "user", "content": "fix the failing test, never touch src/generated"},
    ]
    for i in range(n_calls):
        cid = f"call_{i + 1}"
        messages.append(
            {
                "role": "assistant",
                "content": f"checking file {i}",
                "tool_calls": [
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": f'{{"path": "src/f{i}.ts"}}',
                        },
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": cid, "content": "x" * result_chars})
    messages.append({"role": "user", "content": "go ahead"})
    return messages


# Strict structural oracle for tests: the full OpenAI sequence invariant.
# The engine gates on _commit_valid (only what Jev changed); tests use this
# to assert Jev output on clean input is fully well-formed.
from hermes_jev_compact.adapter import is_well_formed_tool_call  # noqa: E402


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
