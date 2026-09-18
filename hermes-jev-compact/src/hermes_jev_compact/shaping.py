"""Token estimator + text shaping (port of fast-jev-compaction state.ts)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

STATE_CONTEXT = (
    "A coding assistant conversation is being compacted to free context. `history` is the whole "
    "conversation so far, oldest first; tool outputs are replaced by a short `result` note and long "
    "texts may be abridged. Each question asks whether one tool call, or the full output of that call, "
    "still needs to stay in the history verbatim. Whatever is not kept is deleted permanently, but the "
    "assistant can always re-run a tool or re-read a file."
)

INPUT_CHARS: Sequence[int] = (1000, 200, 60)
TEXT_HEAD = 400
TEXT_TAIL = 150

_TOKEN_PIECES = re.compile(r"[A-Za-z]+|\d+|[^\sA-Za-z\d]")


def _piece_class(first: str) -> str:
    # TS parity (state.ts:30-36): classification is ASCII-only via charCodeAt.
    # Non-ASCII alphanumerics are SYMBOLS (0.9 each), not letters/digits.
    code = ord(first)
    if 48 <= code <= 57:
        return "digit"
    if 65 <= code <= 90 or 97 <= code <= 122:
        return "alpha"
    return "symbol"


def estimate_tokens(text: str) -> int:
    """Port of TS estimateTokens: words ~1/6 letters, digits 0.5, symbols 0.9."""
    tokens = 0.0
    for piece in _TOKEN_PIECES.findall(text):
        kind = _piece_class(piece[0])
        if kind == "digit":
            tokens += len(piece) / 2
        elif kind == "alpha":
            tokens += 1 + (len(piece) - 1) // 6
        else:
            tokens += 0.9
    return math.ceil(tokens)


def truncate(text: str, limit: int) -> str:
    """Port of TS truncate."""
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def abridge(text: str, head: int, tail: int) -> str:
    """Port of TS abridge (module-private there; exported here for tests).

    NOTE: deliberate drift from TS at tail=0 — JS slice(-0) returns the FULL
    string, which would duplicate the body after the omitted-note. Python
    returns an empty tail instead. No caller passes tail=0 (TEXT_TAIL=150).
    """
    if len(text) <= head + tail + 40:
        return text
    omitted = len(text) - head - tail
    tail_text = text[-tail:] if tail else ""
    return f"{text[:head]}\n[… {omitted} chars omitted …]\n{tail_text}"
