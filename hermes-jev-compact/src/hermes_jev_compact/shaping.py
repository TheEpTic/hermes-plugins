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


def _utf16_units(text: str) -> int:
    """Length in UTF-16 code units — the unit JS .length/slice/regex use.

    Astral chars (emoji, some CJK-ext) are 2 units in TS but 1 Python code
    point; without this the estimator UNDER-counts emoji-heavy text by ~2x
    and truncation keeps more than the TS budget allows.
    """
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def _utf16_slice(text: str, start: int, stop: int | None = None) -> str:
    """Slice by UTF-16 units, never splitting a surrogate pair."""
    units = 0
    out: list[str] = []
    end = stop if stop is not None else _utf16_units(text)
    for ch in text:
        width = 2 if ord(ch) > 0xFFFF else 1
        if units >= start and units + width <= end:
            out.append(ch)
        units += width
        if units >= end:
            break
    return "".join(out)


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
    """Port of TS estimateTokens: words ~1/6 letters, digits 0.5, symbols 0.9.

    Piece runs + lengths are measured in UTF-16 units (see _utf16_units):
    one astral char is TWO symbol units (1.8 tokens), exactly as TS counts
    its two surrogates.
    """
    tokens = 0.0
    for piece in _TOKEN_PIECES.findall(text):
        kind = _piece_class(piece[0])
        units = _utf16_units(piece)
        if kind == "digit":
            tokens += units / 2
        elif kind == "alpha":
            tokens += 1 + (units - 1) // 6
        else:
            # TS: each char of a symbol run is its own match (+0.9 each), and
            # each surrogate half is a separate char. Python's regex merges a
            # symbol RUN into one match, so charge per unit — except the ASCII
            # single-char case, which is exactly 1 unit anyway.
            tokens += 0.9 * max(1, units)
    # Float dust (0.9*n): TS Math.ceil(180.00000000000028) = 181 too — but the
    # values that matter (budgets/stages) compare identically on both sides
    # since BOTH ceil. Round to 9dp first so exact-integer expectations hold.
    return math.ceil(round(tokens, 9))


def truncate(text: str, limit: int) -> str:
    """Port of TS truncate. Limits/widths are UTF-16 units (TS parity)."""
    if _utf16_units(text) <= limit:
        return text
    return f"{_utf16_slice(text, 0, max(0, limit - 1))}…"


def abridge(text: str, head: int, tail: int) -> str:
    """Port of TS abridge (module-private there; exported here for tests).

    NOTE: deliberate drift from TS at tail=0 — JS slice(-0) returns the FULL
    string, which would duplicate the body after the omitted-note. Python
    returns an empty tail instead. No caller passes tail=0 (TEXT_TAIL=150).
    Lengths are UTF-16 units (TS parity).
    """
    total = _utf16_units(text)
    if total <= head + tail + 40:
        return text
    omitted = total - head - tail
    tail_text = _utf16_slice(text, total - tail, total) if tail else ""
    return f"{_utf16_slice(text, 0, head)}\n[… {omitted} chars omitted …]\n{tail_text}"
