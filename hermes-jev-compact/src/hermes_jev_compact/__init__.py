"""hermes-jev-compact — Jev-powered smart tool-prune context engine for Hermes.

A ContextCompressor subclass named ``jev`` that scores stale tool call/result
units with TypeSafe Jev (via conduit2 /v1/systemone) and keeps/drops them by
probability, falling back to the built-in deterministic prune on any failure.
"""

from __future__ import annotations

import logging
import math
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from typing import Any

try:
    __version__ = distribution_version("hermes-jev-compact")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
__all__ = ["__version__", "register"]

logger = logging.getLogger(__name__)

_registered: bool = False


def _setting(ctx: Any, key: str, default: Any) -> Any:
    """One typed coercion for ctx settings; garbage in → default out."""
    try:
        value = ctx.get_config(key, default)
    except Exception:
        return default
    if isinstance(value, bool):
        # bool coerces to everything (int(True) == 1); a bool setting is
        # never what a str/int/float knob wants, so reject it outright.
        return default
    if isinstance(default, str):
        return value.strip() if isinstance(value, str) and value.strip() else default
    try:
        out = type(default)(value)
    except (TypeError, ValueError):
        return default
    if isinstance(out, float) and not math.isfinite(out):
        return default
    return out


def _build_engine(ctx: Any) -> Any:
    from .engine import _JEV_KNOBS, JevContextCompressor

    knobs = {attr: _setting(ctx, key, default) for attr, key, default in _JEV_KNOBS}
    return JevContextCompressor(model="typesafe:jev-latest", **knobs)


def register(ctx: Any) -> None:
    """Register the ``jev`` context engine (config-only singleton, no secrets)."""
    global _registered
    if _registered:
        logger.debug("hermes-jev-compact: already registered, skipping")
        return
    _registered = True
    ctx.register_context_engine(_build_engine(ctx))
    logger.info("hermes-jev-compact loaded (engine: jev)")
