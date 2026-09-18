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


class FakeCtx:
    def __init__(self, settings: Dict[str, Any] | None = None) -> None:
        self.settings = settings or {}
        self.engine: Any = None

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def register_context_engine(self, engine: Any) -> None:
        self.engine = engine


def test_register_installs_jev_engine_with_defaults():
    hermes_jev_compact._registered = False
    ctx = FakeCtx()
    hermes_jev_compact.register(ctx)
    assert isinstance(ctx.engine, JevContextCompressor)
    assert ctx.engine.name == "jev"
    assert ctx.engine.jev_api_key_env == "CONDUIT_NEXUS_API_KEY"
    assert ctx.engine.jev_conduit_base_url == "http://127.0.0.1:8765/v1"


def test_register_honors_settings_and_is_idempotent():
    hermes_jev_compact._registered = False
    ctx = FakeCtx({"keep_threshold": 0.7, "jev_model": "typesafe:jev-test"})
    hermes_jev_compact.register(ctx)
    assert ctx.engine.jev_keep_threshold == 0.7
    assert ctx.engine.jev_model == "typesafe:jev-test"
    first = ctx.engine
    hermes_jev_compact.register(ctx)
    assert ctx.engine is first


def test_register_rejects_garbage_settings():
    hermes_jev_compact._registered = False
    ctx = FakeCtx({"keep_threshold": "junk", "max_state_tokens": "junk", "jev_model": ""})
    hermes_jev_compact.register(ctx)
    assert ctx.engine.jev_keep_threshold == 0.5
    assert ctx.engine.jev_max_state_tokens == 25000
    assert ctx.engine.jev_model == "typesafe:jev-latest"
