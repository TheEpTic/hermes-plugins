"""Shared types for hermes-jev-compact: options, transcript view, jev answers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class JevError(RuntimeError):
    """Jev scoring failed (transport, validation, budget) — caller falls back."""


@dataclass(frozen=True)
class JevOptions:
    """Resolved, validated tunables."""

    keep_threshold: float = 0.5
    # Deliberate drift from TS (single keepThreshold): error results get
    # their own lower bar — a stale-looking traceback is usually the unit
    # the next turn debugs from. None/omitted falls back to keep_threshold.
    error_keep_threshold: float | None = None
    max_state_tokens: int = 25000
    max_request_tokens: int = 30000
    truncate_head_chars: int = 300
    request_timeout_s: float = 30.0
    min_result_chars: int = 2000


@dataclass(frozen=True)
class JevToolCall:
    """One paired tool call + result, addressed by hermes tool_call_id."""

    id: str  # short jev id: t1, t2, ...
    tool_call_id: str  # hermes pairing key
    tool: str
    input: dict[str, Any]
    call_index: int
    result_index: int
    result_chars: int
    is_error: bool
    pinned: bool = False  # TS parity: pinned calls are never candidates


@dataclass(frozen=True)
class JevCallAnswer:
    keep_call: float
    keep_result: float


@dataclass(frozen=True)
class JevCallDecision:
    id: str
    tool: str
    action: str  # "keep" | "drop_result" | "drop_call"
    reason: str  # "kept" | "pinned" | "result_dropped" | "call_dropped"
    keep_call: float = 1.0
    keep_result: float = 1.0


@dataclass
class JevInternalMessage:
    """Minimal transcript view for state shaping (system rows excluded upstream)."""

    index: int
    role: str  # "user" | "assistant" | "tool"
    text: str
    tool_calls: list[JevToolCall] = field(default_factory=list)
