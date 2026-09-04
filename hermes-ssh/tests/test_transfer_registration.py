from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import ssh_tools
from ssh_tools.schemas import SSH_TRANSFER_SCHEMA


class Context:
    def __init__(self) -> None:
        self.tools: dict[str, dict[str, Any]] = {}
        self.commands: list[str] = []

    def register_tool(self, *, name: str, **kwargs: Any) -> None:
        self.tools[name] = kwargs

    def register_command(self, name: str, **kwargs: Any) -> None:
        self.commands.append(name)


class FakeManager:
    def start_idle_checker(self) -> None:
        pass


def test_transfer_schema_and_registration() -> None:
    context = Context()
    with patch.object(ssh_tools, "SSHManager", FakeManager):
        ssh_tools._manager = None
        ssh_tools.register(context)
        ssh_tools._manager = None
    assert "ssh_transfer" in context.tools
    assert context.tools["ssh_transfer"]["schema"] is SSH_TRANSFER_SCHEMA
    assert set(context.tools) == {
        "ssh_terminal",
        "ssh_transfer",
        "ssh_machines",
        "ssh_sessions",
    }
    assert context.commands == ["ssh"]


def test_manifest_declares_transfer_tool() -> None:
    manifest = Path("src/ssh_tools/plugin.yaml").read_text(encoding="utf-8")
    assert "  - ssh_transfer\n" in manifest
