"""Data models for hermes-ssh — Machine and Session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class Machine:
    """A registered remote host."""

    name: str
    host: str
    user: str = "root"
    port: int = 22
    key: str = ""
    aliases: list[str] | None = None
    tags: list[str] | None = None
    description: str = ""
    added: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "user": self.user,
            "port": self.port,
            "key": self.key,
            "aliases": self.aliases or [],
            "tags": self.tags or [],
            "description": self.description,
            "added": self.added,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> Machine:
        return cls(
            name=name,
            host=data.get("host", ""),
            user=data.get("user", "root"),
            port=data.get("port", 22),
            key=data.get("key", ""),
            aliases=data.get("aliases", []),
            tags=data.get("tags", []),
            description=data.get("description", ""),
            added=data.get("added", ""),
        )


@dataclass
class Session:
    """An active or closed SSH session."""

    id: str
    machine: str
    pid: int = 0
    control_path: str = ""
    started: str = ""
    last_active: str = ""
    command_count: int = 0
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "machine": self.machine,
            "pid": self.pid,
            "control_path": self.control_path,
            "started": self.started,
            "last_active": self.last_active,
            "command_count": self.command_count,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, sid: str, data: dict[str, Any]) -> Session:
        return cls(
            id=sid,
            machine=data.get("machine", ""),
            pid=data.get("pid", 0),
            control_path=data.get("control_path", ""),
            started=data.get("started", ""),
            last_active=data.get("last_active", ""),
            command_count=data.get("command_count", 0),
            status=data.get("status", "active"),
        )

    @property
    def idle_seconds(self) -> int | None:
        if self.status != "active" or not self.last_active:
            return None
        try:
            last = datetime.fromisoformat(self.last_active)
            return int((datetime.now(UTC) - last).total_seconds())
        except (ValueError, TypeError):
            return None

    @property
    def idle_human(self) -> str:
        s = self.idle_seconds
        if s is None:
            return "unknown"
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m {s % 60}s"
        return f"{s // 3600}h {(s % 3600) // 60}m"
