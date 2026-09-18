"""Port-parity tests for shaping (vectors from fast-jev-compaction.test.ts).

Traceability: test_estimate_tokens_vectors == test.ts 'token estimate' block
verbatim; test_estimate_tokens_exact_mixed_vectors is computed independently
from state.ts:28-37 (pieces/regex + class rule + ceil); test_truncate_matches_ts
/ test_abridge_matches_ts pin the exact TS truncate/abridge semantics
(state.ts:40-47); the astral tests pin UTF-16-unit parity (surrogate halves
count as TS counts them); the tail=0 test pins the one DOCUMENTED drift
(see shaping.py).
"""

from __future__ import annotations

from hermes_jev_compact.shaping import abridge, estimate_tokens, truncate


def test_estimate_tokens_vectors():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") == 2
    assert estimate_tokens("internationalization") == 4
    assert estimate_tokens("12345678") == 4


def test_estimate_tokens_exact_mixed_vectors():
    # Exact parity vectors computed INDEPENDENTLY from TS state.ts:28-37
    # (pieces: [A-Za-z]+ | \d+ | single non-space char; alpha: 1+floor((n-1)/6);
    # digits: n/2; symbols incl. non-ASCII alphanumerics: 0.9; ceil the sum).
    # "ab12!?": "ab"->1, "12"->1.0, "!"->0.9, "?"->0.9 = 3.8 -> 4.
    assert estimate_tokens("ab12!?") == 4
    # "hello, world! 12345": hello->1, ","->0.9, world->1, "!"->0.9,
    # "12345"->2.5 = 6.3 -> 7.
    assert estimate_tokens("hello, world! 12345") == 7
    # Digit + symbol runs: "12345678"->4.0 (existing vector), "a1b2c3":
    # a/b/c->1 each, 1/2/3->0.5 each = 4.5 -> 5.
    assert estimate_tokens("a1b2c3") == 5
    # Non-ASCII alphanumerics are SYMBOLS per charCodeAt: "é"*6 -> 5.4 -> 6.
    assert estimate_tokens("éééééé") == 6
    # JSON punctuation: '{"a":1}' -> {,",a,",:,1,} =
    # 0.9+0.9+1+0.9+0.9+0.5+0.9 = 6.0 -> 6.
    assert estimate_tokens('{"a":1}') == 6


def test_truncate_matches_ts():
    assert truncate("abc", 5) == "abc"
    assert truncate("abcdef", 5) == "abcd…"


def test_abridge_matches_ts():
    text = "x" * 1000
    out = abridge(text, 400, 150)
    assert out.startswith("x" * 400)
    assert out.endswith("x" * 150)
    assert "[… 450 chars omitted …]" in out
    short = "y" * 100
    assert abridge(short, 400, 150) == short


def test_estimate_tokens_ascii_only_like_ts_charcodeat():
    # TS state.ts:30-36 classifies by charCodeAt: non-ASCII alphanumerics are
    # symbols (0.9), NOT letters/digits. 'é'.isalpha() is True in Python, so
    # a naive port would charge 1.0; the faithful port charges ceil(0.9)=1
    # for one char but diverges on runs: 6 'é' = ceil(5.4)=6 vs naive 1.
    assert estimate_tokens("é" * 6) == 6
    # Sanity: the equivalent ASCII run costs 1 token per the 1/6 rule.
    assert estimate_tokens("e" * 6) == 1


def test_abridge_zero_tail_documented_drift():
    # DELIBERATE drift from TS: JS slice(-0) is the full string (state.ts:47),
    # which would duplicate the body. Python returns an empty tail instead.
    text = "x" * 1000
    out = abridge(text, 400, 0)
    assert out.startswith("x" * 400)
    assert out.endswith("…]\n")
    assert "[… 600 chars omitted …]" in out


def test_astral_counts_as_two_utf16_units_like_ts():
    # TS .length/regex count UTF-16 code units: one emoji = 2 surrogate
    # chars = 2 symbol matches @0.9 = 1.8 tokens. Python len() would say 1.
    assert estimate_tokens("😀" * 100) == 180
    assert estimate_tokens("😀") == 2  # ceil(1.8)
    # Truncation widths are units too, and never split a surrogate pair.
    assert truncate("😀" * 100, 10) == "😀" * 4 + "…"
    assert truncate("ab😀cd", 5) == "ab😀…"
    # Abridge counts + slices in units.
    out = abridge("😀" * 400, 400, 150)
    assert "[… 250 chars omitted …]" in out
    assert out.startswith("😀" * 200)
    assert out.endswith("😀" * 75)
