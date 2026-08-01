from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from ssh_tools.dashboard import plugin_api


@dataclass
class FakeMachine:
    name: str = "build"
    host: str = "build.example"
    user: str = "runner"
    port: int = 22
    key: str = "/home/runner/.ssh/id_ed25519"
    aliases: list[str] | None = None
    tags: list[str] | None = None
    description: str = "private note"
    added: str = "2026-08-01T00:00:00+00:00"


@dataclass
class FakeSession:
    id: str = "ssh_build_ab12cd34"
    machine: str = "build"
    pid: int = 1234
    control_path: str = "/home/runner/.hermes/ssh-tools/sockets/build.sock"
    started: str = "2026-08-01T00:00:00+00:00"
    last_active: str = "2026-08-01T00:01:00+00:00"
    command_count: int = 2
    status: str = "active"
    idle_seconds: int = 4


class FakeSSHManager:
    def __init__(self) -> None:
        self.machine = FakeMachine(aliases=["ci"], tags=["build"])
        self.session = FakeSession()
        self.run_calls: list[dict[str, Any]] = []
        self.kill_calls: list[str] = []

    def list_machines(self) -> dict[str, FakeMachine]:
        return {self.machine.name: self.machine}

    def list_sessions(self, status: str = "active") -> dict[str, FakeSession]:
        if status and status != self.session.status:
            return {}
        return {self.session.id: self.session}

    def list_command_log(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            {
                "timestamp": "2026-08-01T00:01:00+00:00",
                "machine": "build",
                "command": "deploy --token [REDACTED]",
                "command_sha256": "a" * 64,
                "command_length": 24,
                "exit_code": 0,
                "elapsed_secs": 0.2,
                "session_id": self.session.id,
            }
        ][:limit]

    def test_machine(self, name: str) -> dict[str, Any]:
        return {"success": True, "status": "connected", "host": self.machine.host}

    def add_machine(self, machine: Any) -> Any:
        self.machine = machine
        return machine

    def remove_machine(self, name: str) -> bool:
        return name == self.machine.name

    def kill_session(self, session_id: str) -> dict[str, Any]:
        self.kill_calls.append(session_id)
        return {"success": True, "pid_killed": True, "socket_closed": False}

    def cleanup_idle(self, max_idle_minutes: int | None = None) -> dict[str, Any]:
        return {"killed": [], "count": 0}

    def poll_session(self, session_id: str) -> dict[str, Any]:
        return {"success": True, "session_id": session_id, "running": True}

    def run_command(self, **kwargs: Any) -> dict[str, Any]:
        self.run_calls.append(kwargs)
        return {
            "success": True,
            "stdout": "token=super-secret\nready",
            "stderr": "",
            "exit_code": 0,
            "machine": kwargs["machine_name"],
            "session_id": "ssh_build_deadbeef",
            "stdout_file": "/home/runner/.hermes/ssh-tools/outputs/private.txt",
        }


class FakeSFWManager:
    sfw_path = "/home/runner/.local/share/pnpm/bin/sfw"

    def is_installed(self) -> bool:
        return True

    def get_version(self) -> str:
        return "sfw 1.2.3"

    def run_command(self, **kwargs: Any) -> Any:
        raise AssertionError("not used by SSH dashboard tests")


def test_status_is_bounded_and_omits_machine_secrets(monkeypatch: Any) -> None:
    manager = FakeSSHManager()
    monkeypatch.setattr(plugin_api, "get_manager", lambda: manager)

    response = TestClient(plugin_api.app).get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["machines"][0] == {
        "name": "build",
        "host": "build.example",
        "port": 22,
        "user": "runner",
        "aliases": ["ci"],
        "tags": ["build"],
        "added": "2026-08-01T00:00:00+00:00",
    }
    assert "key" not in body["machines"][0]
    assert "description" not in body["machines"][0]
    assert "control_path" not in body["sessions"][0]
    assert "command" not in body["audit"][0]


def test_terminal_requires_explicit_confirmation(monkeypatch: Any) -> None:
    manager = FakeSSHManager()
    monkeypatch.setattr(plugin_api, "get_manager", lambda: manager)

    response = TestClient(plugin_api.app).post(
        "/terminal",
        json={"machine": "build", "command": "echo ok"},
    )

    assert response.status_code == 400
    assert "confirm" in response.json()["detail"]
    assert manager.run_calls == []


def test_terminal_redacts_output_and_drops_private_file_paths(monkeypatch: Any) -> None:
    manager = FakeSSHManager()
    monkeypatch.setattr(plugin_api, "get_manager", lambda: manager)
    monkeypatch.setattr(plugin_api, "check_approval", lambda command: None)

    response = TestClient(plugin_api.app).post(
        "/terminal",
        json={"machine": "build", "command": "echo token=super-secret", "confirm": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["stdout"] == "token=[REDACTED]\nready"
    assert "stdout_file" not in body
    assert "stderr_file" not in body
    assert manager.run_calls[0]["max_output_chars"] <= 32768


def test_session_kill_requires_confirmation(monkeypatch: Any) -> None:
    manager = FakeSSHManager()
    monkeypatch.setattr(plugin_api, "get_manager", lambda: manager)

    response = TestClient(plugin_api.app).post(
        "/sessions/ssh_build_ab12cd34/kill",
        json={"confirm": False},
    )

    assert response.status_code == 400
    assert manager.kill_calls == []


def test_machine_test_is_read_only_to_the_dashboard(monkeypatch: Any) -> None:
    manager = FakeSSHManager()
    monkeypatch.setattr(plugin_api, "get_manager", lambda: manager)

    response = TestClient(plugin_api.app).post("/machines/build/test")

    assert response.status_code == 200
    assert response.json() == {"success": True, "status": "connected"}


def test_machine_write_requires_confirmation_and_never_echoes_key(monkeypatch: Any) -> None:
    manager = FakeSSHManager()
    monkeypatch.setattr(plugin_api, "get_manager", lambda: manager)

    response = TestClient(plugin_api.app).post(
        "/machines",
        json={
            "name": "staging",
            "host": "staging.example",
            "user": "runner",
            "key": "/home/runner/.ssh/id_ed25519",
            "description": "private note",
            "confirm": True,
        },
    )

    assert response.status_code == 200
    body = response.json()["machine"]
    assert body["name"] == "staging"
    assert "key" not in body
    assert "description" not in body
