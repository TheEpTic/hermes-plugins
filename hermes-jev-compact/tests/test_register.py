"""register() wiring tests with a fake plugin context."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest  # noqa: E402

from tests.conftest import needs_hermes  # noqa: E402

pytestmark = needs_hermes

import hermes_jev_compact  # noqa: E402
from hermes_jev_compact.engine import JevContextCompressor  # noqa: E402


class _FakeManager:
    """Models the host's context-engine slot: ``_context_engine`` is the same
    field ``hermes_cli.plugins.PluginManager`` reads in register_context_engine
    and get_plugin_context_engine. An ``unload`` clears it."""

    def __init__(self) -> None:
        self._context_engine: Any = None


class FakeCtx:
    def __init__(self, settings: Dict[str, Any] | None = None) -> None:
        self.settings = settings or {}
        self._manager = _FakeManager()
        self.engine: Any = None

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def register_context_engine(self, engine: Any) -> None:
        self.engine = engine
        self._manager._context_engine = engine

    def unload(self) -> None:
        """Simulate the host clearing the slot during a plugin reload."""
        self._manager._context_engine = None


def test_register_installs_jev_engine_with_defaults():
    ctx = FakeCtx()
    hermes_jev_compact.register(ctx)
    assert isinstance(ctx.engine, JevContextCompressor)
    assert ctx.engine.name == "jev"
    assert ctx.engine.jev_api_key_env == "TYPESAFE_API_KEY"
    assert ctx.engine.jev_base_url == "https://api.typesafe.ai/v1"
    assert ctx.engine.jev_endpoint_path == "/systemone"


def test_register_honors_settings_and_is_idempotent():
    ctx = FakeCtx({"keep_threshold": 0.7, "jev_model": "jev-test"})
    hermes_jev_compact.register(ctx)
    assert ctx.engine.jev_keep_threshold == 0.7
    assert ctx.engine.jev_model == "jev-test"
    first = ctx.engine
    hermes_jev_compact.register(ctx)
    # Host still holds our engine -> skip, no second registration.
    assert ctx.engine is first


def test_register_honors_endpoint_path_setting():
    ctx = FakeCtx(
        {
            "base_url": "https://openrouter.ai",
            "endpoint_path": "/api/alpha/decisions",
            "api_key_env": "OPENROUTER_API_KEY",
            "jev_model": "typesafe/jev-1.13",
        }
    )
    hermes_jev_compact.register(ctx)
    assert ctx.engine.jev_base_url == "https://openrouter.ai"
    assert ctx.engine.jev_endpoint_path == "/api/alpha/decisions"
    assert ctx.engine.jev_api_key_env == "OPENROUTER_API_KEY"
    assert ctx.engine.jev_model == "typesafe/jev-1.13"


def test_register_rejects_garbage_settings():
    ctx = FakeCtx(
        {
            "keep_threshold": "junk",
            "max_state_tokens": "junk",
            "jev_model": "",
            "error_keep_threshold": "junk",
        }
    )
    hermes_jev_compact.register(ctx)
    assert ctx.engine.jev_keep_threshold == 0.5
    assert ctx.engine.jev_max_state_tokens == 25000
    assert ctx.engine.jev_model == "jev-latest"
    assert ctx.engine.jev_error_keep_threshold == 0.25


def test_register_honors_error_threshold_and_floor_settings():
    ctx = FakeCtx({"error_keep_threshold": 0.3, "min_result_chars": 500})
    hermes_jev_compact.register(ctx)
    assert ctx.engine.jev_error_keep_threshold == 0.3
    assert ctx.engine.jev_min_result_chars == 500


def test_register_defaults_floor_and_error_threshold():
    ctx = FakeCtx()
    hermes_jev_compact.register(ctx)
    assert ctx.engine.jev_min_result_chars == 2000
    assert ctx.engine.jev_error_keep_threshold == 0.25


def test_register_reregisters_after_host_slot_cleared():
    # Regression for the plugin-reload gap: the host calls register() again after
    # unload() clears its context-engine slot. A module-global latch that survives
    # the clear used to skip re-registration, leaving context.engine: jev empty.
    ctx = FakeCtx()
    hermes_jev_compact.register(ctx)
    first = ctx.engine
    ctx.unload()  # host dropped the engine
    hermes_jev_compact.register(ctx)
    assert isinstance(ctx.engine, JevContextCompressor)
    assert ctx.engine.name == "jev"
    assert ctx.engine is not first
    # And the freshly installed engine is back in the host slot.
    assert ctx._manager._context_engine is ctx.engine
