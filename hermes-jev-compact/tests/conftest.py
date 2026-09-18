"""Test fixtures for hermes-jev-compact."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest  # noqa: E402

_HERMES_AGENT_DIR = Path("/home/agent/.hermes/hermes-agent")
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
