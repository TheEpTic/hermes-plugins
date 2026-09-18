"""hermes-jev-compact — Jev-powered smart tool-prune context engine for Hermes.

A ContextCompressor subclass named ``jev`` that scores stale tool call/result
units with TypeSafe Jev (any OpenAI-style ``POST {base_url}/systemone``
endpoint — TypeSafe's own API at https://api.typesafe.ai/v1 is the reference)
and keeps/drops them by probability, falling back to the built-in
deterministic prune on any failure.
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
    # Host policy the built-in constructor would receive (agent_init.py:1856+):
    # the plugin singleton is built at register() time, so read the same
    # config roots directly. Without this the jev engine logs in quiet mode
    # and guards the wrong tail size on the fallback/proactive paths.
    base: dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config_readonly  # type: ignore[import-not-found]

        cfg = load_config_readonly() or {}
        agent_cfg = cfg.get("agent", {}) if isinstance(cfg, dict) else {}
        comp_cfg = cfg.get("compression", {}) if isinstance(cfg, dict) else {}
        if isinstance(agent_cfg, dict) and isinstance(agent_cfg.get("quiet_mode"), bool):
            base["quiet_mode"] = agent_cfg["quiet_mode"]
        if isinstance(comp_cfg, dict) and comp_cfg.get("protect_last_n") is not None:
            try:
                base["protect_last_n"] = max(0, int(comp_cfg["protect_last_n"]))
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    return JevContextCompressor(model="jev-latest", **base, **knobs)


def register(ctx: Any) -> None:
    """Register the ``jev`` context engine (config-only singleton, no secrets)."""
    global _registered
    if _registered:
        logger.debug("hermes-jev-compact: already registered, skipping")
        return
    ctx.register_context_engine(_build_engine(ctx))
    # Only latch AFTER success: a failed registration must not permanently
    # disable retries (a second plugin wins the single-engine slot instead —
    # the host rejects it with a warning, same as any double-register).
    _registered = True
    logger.info("hermes-jev-compact loaded (engine: jev)")
