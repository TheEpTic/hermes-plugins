"""Jev request/response shaping (port of fast-jev-compaction request.ts)."""

from __future__ import annotations

import json
import math
from typing import Any

from .protocol import JevError

SYSTEMONE_PATH = "/systemone"
DEFAULT_MODEL = "jev-latest"


def build_jev_body(model: str, state: Any, questions: dict[str, Any]) -> str:
    """JSON body for one systemone call. No `stream` key — routers 400 on it."""
    try:
        # allow_nan=False: NaN/Infinity are not valid JSON — a state carrying
        # them is corrupt input, not a request (strict endpoints 400 it).
        return json.dumps({"model": model, "state": state, "questions": questions}, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise JevError(f"jev request is not JSON-serializable: {type(exc).__name__}") from exc


def parse_jev_response(status: int, text: str, ok: bool | None = None) -> dict[str, Any]:
    """Validate a systemone response body; JevError on anything but an answers object.

    `ok` is deprecated (kept for backward compat): the status code is the
    authority — a caller passing ok=True with a 500 must not succeed.
    """
    if ok is None:
        ok = 200 <= status < 300
    elif ok != (200 <= status < 300):
        raise JevError(f"jev response status/ok mismatch (http {status})")
    if not ok:
        # Upstream error bodies are attacker-influenced: log a category, not
        # the body — no newlines/control bytes, no echoed creds in the log.
        raise JevError(f"jev request failed (http {status})")
    try:
        parsed = json.loads(text)
    except ValueError:
        raise JevError("Jev returned malformed JSON") from None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("answers"), dict):
        raise JevError("Jev response is missing answers")
    return parsed


def noul_answer(answers: dict[str, Any], name: str) -> float:
    """The noul probability of one answer; JevError when it is not there."""
    answer = answers.get(name)
    prob = answer.get("noul") if isinstance(answer, dict) else None
    if isinstance(prob, bool) or not isinstance(prob, (int, float)) or not math.isfinite(prob):
        raise JevError(f"Invalid Jev answer for {name}")
    if not 0.0 <= float(prob) <= 1.0:
        raise JevError(f"Invalid Jev answer for {name}: not a probability")
    return float(prob)
