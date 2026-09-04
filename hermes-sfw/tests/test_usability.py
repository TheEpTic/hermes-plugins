"""Regression tests for SFW-3 and SFW-4 (usability lane).

SFW-3: the ``sfw`` binary is not on PATH in background/pty shells. The status
result must expose the resolved binary path plus explicit invocation guidance.
SFW-4: the terminal block message must say exactly how to invoke the deferred
``sfw`` tool instead of a vague "run it with the sfw tool".
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any

import hermes_sfw
import pytest
from hermes_sfw import SFW_TOOL_SCHEMA, _guard_direct_dependency_operation
from hermes_sfw.handlers import handle_sfw
from hermes_sfw.manager import SFWConfig, SFWManager


def _call(manager: SFWManager, params: dict[str, Any]) -> dict[str, Any]:
    """Helper to call the sfw handler and parse its JSON response."""
    raw: str = handle_sfw(manager)(params)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# forced terminal routing
# ---------------------------------------------------------------------------


class TestForcedTerminalRouting:
    """Supported package-manager calls must execute through the sfw binary."""

    @staticmethod
    def _manager_for(tmp_path: Path) -> tuple[SFWManager, Path]:
        sfw_bin = tmp_path / "sfw"
        sfw_bin.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        sfw_bin.chmod(0o755)
        return SFWManager(SFWConfig(sfw_bin=str(sfw_bin))), sfw_bin

    def test_valid_dependency_operation_is_rewritten_to_sfw(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        manager, sfw_bin = self._manager_for(tmp_path)
        monkeypatch.setattr(hermes_sfw, "_manager", manager)

        result = _guard_direct_dependency_operation("terminal", {"command": "npm install express"})

        assert result == {
            "action": "modify",
            "args": {
                "command": shlex.join([str(sfw_bin), "npm", "install", "express"]),
            },
        }

    def test_rewritten_command_preserves_quoted_arguments(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        manager, sfw_bin = self._manager_for(tmp_path)
        monkeypatch.setattr(hermes_sfw, "_manager", manager)

        result = _guard_direct_dependency_operation(
            "terminal", {"command": 'npm install "@scope/package" --save-dev'}
        )

        assert result is not None
        assert result["action"] == "modify"
        assert result["args"]["command"] == shlex.join(
            [str(sfw_bin), "npm", "install", "@scope/package", "--save-dev"]
        )

    def test_unsupported_package_manager_operation_is_blocked(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        manager, sfw_bin = self._manager_for(tmp_path)
        monkeypatch.setattr(hermes_sfw, "_manager", manager)

        result = _guard_direct_dependency_operation("terminal", {"command": "npm run build"})

        assert result is not None
        assert result["action"] == "block"
        assert "npm run build" in result["message"]
        assert "sfw" in result["message"]
        assert str(sfw_bin) in result["message"]

    def test_compound_package_manager_operation_is_blocked(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        manager, _ = self._manager_for(tmp_path)
        monkeypatch.setattr(hermes_sfw, "_manager", manager)

        result = _guard_direct_dependency_operation(
            "terminal", {"command": "cd project && npm install express"}
        )

        assert result is not None
        assert result["action"] == "block"
        assert "sfw" in result["message"]

    @pytest.mark.parametrize(
        "command",
        [
            "sudo npm install express",
            "env NODE_ENV=test npm install express",
            "sudo bash -lc 'npm install express'",
            "bash -c 'npm install express'",
            "(npm install express)",
            "cd project\nnpm install express",
            "/usr/bin/npm install express",
            'npm install "unterminated',
        ],
    )
    def test_wrapped_path_or_malformed_package_command_is_blocked(
        self, tmp_path: Path, monkeypatch: Any, command: str
    ) -> None:
        manager, _ = self._manager_for(tmp_path)
        monkeypatch.setattr(hermes_sfw, "_manager", manager)

        result = _guard_direct_dependency_operation("terminal", {"command": command})

        assert result is not None
        assert result["action"] == "block"
        assert "sfw" in result["message"]

    def test_valid_dependency_operation_fails_closed_without_sfw(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        manager = SFWManager(SFWConfig(sfw_bin=str(tmp_path / "missing" / "sfw")))
        monkeypatch.setattr(hermes_sfw, "_manager", manager)

        result = _guard_direct_dependency_operation("terminal", {"command": "npm install express"})

        assert result is not None
        assert result["action"] == "block"
        assert "unavailable" in result["message"]

    def test_non_package_manager_terminal_command_is_ignored(self) -> None:
        assert _guard_direct_dependency_operation("terminal", {"command": "git status"}) is None

    def test_other_tools_are_ignored(self) -> None:
        assert (
            _guard_direct_dependency_operation("read_file", {"command": "npm install express"})
            is None
        )


# ---------------------------------------------------------------------------
# SFW-3: status exposes the resolved binary path + invocation guidance
# ---------------------------------------------------------------------------


class TestStatusInvocationGuidance:
    """The status result must be usable from background/pty shells."""

    def test_status_includes_resolved_binary_path(self, manager: SFWManager) -> None:
        result = _call(manager, {"action": "status"})
        assert result["success"] is True
        binary = result["binary"]
        assert binary == manager.sfw_path
        assert binary is not None
        assert Path(binary).is_absolute()
        assert os.access(binary, os.X_OK)

    def test_status_invocation_field_is_self_explanatory(self, manager: SFWManager) -> None:
        result = _call(manager, {"action": "status"})
        invocation = result["invocation"]
        assert invocation["tool"] == "sfw"
        assert invocation["tool_shape"].startswith("sfw action=run")
        assert invocation["binary_path"] == manager.sfw_path
        bg_shell = invocation["bg_shell_shape"]
        assert bg_shell is not None
        assert bg_shell.startswith(f"{manager.sfw_path} ")

    def test_status_not_installed_invocation_still_explains_tool(self, tmp_path: Path) -> None:
        mgr = SFWManager(SFWConfig(sfw_bin=str(tmp_path / "nonexistent" / "sfw")))
        result = _call(mgr, {"action": "status"})
        assert result["success"] is True
        assert result["installed"] is False
        assert result["binary"] is None
        invocation = result["invocation"]
        assert invocation["tool"] == "sfw"
        assert invocation["tool_shape"].startswith("sfw action=run")
        assert invocation["binary_path"] is None
        assert invocation["bg_shell_shape"] is None


# ---------------------------------------------------------------------------
# Schema documents the invocation fields
# ---------------------------------------------------------------------------


class TestSchemaInvocationDocumentation:
    """The tool schema must document PATH-friendly invocation."""

    def test_schema_documents_deferred_tool_and_background_shells(self) -> None:
        description = SFW_TOOL_SCHEMA["description"].lower()
        assert "deferred" in description
        assert "tool_search" in description
        assert "background" in description
        assert "path" in description

    def test_schema_documents_status_invocation_field(self) -> None:
        status_desc = SFW_TOOL_SCHEMA["parameters"]["properties"]["action"]["description"].lower()
        assert "invocation" in status_desc
        assert "binary" in status_desc
