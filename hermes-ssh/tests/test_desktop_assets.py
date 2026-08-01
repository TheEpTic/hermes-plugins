from __future__ import annotations

import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1]


def test_manifest_and_runtime_asset_contract() -> None:
    manifest_path = PACKAGE_ROOT / "src" / "ssh_tools" / "dashboard" / "manifest.json"
    plugin_path = PACKAGE_ROOT / "desktop-plugins" / "hermes-ssh" / "plugin.js"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = plugin_path.read_text(encoding="utf-8")

    assert manifest["name"] == "hermes-ssh"
    assert manifest["api"] == "plugin_api.py"
    assert "defaultEnabled: false" in source
    assert "ctx.rest" in source
    assert "host.request" not in source
    assert "window.confirm" in source
    assert "ssh-operations" in source
