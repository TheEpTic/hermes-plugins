"""Jev request/response shaping (port of fast-jev-compaction request.ts)."""

from __future__ import annotations

import json
import math
from typing import Any

from .protocol import JevError

SYSTEMONE_PATH = "/systemone"
DEFAULT_MODEL = "typesafe:jev-latest"


def build_jev_body(model: str, state: Any, questions: dict[str, Any]) -> str:
    """JSON body for one systemone call. No `stream` key — conduit2 400s on it."""
    return json.dumps({"model": model, "state": state, "questions": questions})


def parse_jev_response(status: int, ok: bool, text: str) -> dict[str, Any]:
    """Validate a systemone response body; JevError on anything but an answers object."""
    if not ok:
        raise JevError(f"Jev request failed ({status}): {text[:200]}")
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
    return float(prob)
