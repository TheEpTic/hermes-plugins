from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from hermes_sfw.dashboard import plugin_api


@dataclass
class FakeResult:
    success: bool = True
    command: str = "npm install example"
    stdout: str = "token=super-secret\ninstalled example"
    stderr: str = ""
    exit_code: int = 0
    blocked: list[str] | None = None
    installed: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "blocked": self.blocked or [],
            "installed": self.installed or ["example"],
        }


class FakeSFWManager:
    sfw_path = "/home/runner/.local/share/pnpm/bin/sfw"

    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    def is_installed(self) -> bool:
        return True

    def get_version(self) -> str:
        return "sfw 1.2.3"

    def run_command(self, **kwargs: Any) -> FakeResult:
        self.run_calls.append(kwargs)
        return FakeResult()


def test_status_exposes_health_without_local_binary_path(monkeypatch: Any) -> None:
    manager = FakeSFWManager()
    monkeypatch.setattr(plugin_api, "get_manager", lambda: manager)
    monkeypatch.setenv("HERMES_SFW_ENFORCE_DIRECT", "1")

    response = TestClient(plugin_api.app).get("/status")

    assert response.status_code == 200
    assert response.json() == {
        "installed": True,
        "version": "sfw 1.2.3",
        "binary": "sfw",
        "direct_terminal_enforced": True,
    }


def test_run_requires_explicit_confirmation(monkeypatch: Any) -> None:
    manager = FakeSFWManager()
    monkeypatch.setattr(plugin_api, "get_manager", lambda: manager)

    response = TestClient(plugin_api.app).post(
        "/run",
        json={"command": "npm install example"},
    )

    assert response.status_code == 400
    assert "confirm" in response.json()["detail"]
    assert manager.run_calls == []


def test_run_rejects_non_dependency_commands(monkeypatch: Any) -> None:
    manager = FakeSFWManager()
    monkeypatch.setattr(plugin_api, "get_manager", lambda: manager)

    response = TestClient(plugin_api.app).post(
        "/run",
        json={"command": "rm -rf /", "confirm": True},
    )

    assert response.status_code == 400
    assert "dependency operation" in response.json()["detail"]
    assert manager.run_calls == []


def test_run_returns_redacted_bounded_result(monkeypatch: Any) -> None:
    manager = FakeSFWManager()
    monkeypatch.setattr(plugin_api, "get_manager", lambda: manager)
    monkeypatch.setattr(plugin_api, "check_approval", lambda command: None)

    response = TestClient(plugin_api.app).post(
        "/run",
        json={
            "command": "npm install example --registry=https://token=super-secret@example.test",
            "confirm": True,
            "verbose": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "command" not in body
    assert body["stdout"] == "token=[REDACTED]\ninstalled example"
    assert body["installed"] == ["example"]
    assert manager.run_calls == [
        {
            "command": "npm install example --registry=https://token=super-secret@example.test",
            "workdir": None,
            "verbose": True,
        }
    ]
