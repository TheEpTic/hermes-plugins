"""Encrypted machine registry: canonical-name + alias resolution, persistence."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from .config import SSHConfig
from .models import Machine
from .storage import EncryptedStore
from .validate import validate_machine

logger = logging.getLogger(__name__)


class MachineRegistry:
    """Encrypted store of machines; resolves canonical names and aliases."""

    def __init__(self, config: SSHConfig, store: EncryptedStore, lock: threading.Lock) -> None:
        self._config = config
        self._store = store
        self._lock = lock

    def _load(self) -> dict[str, dict[str, Any]]:
        raw = self._store.read("machines.json", {"machines": {}})
        result = raw.get("machines", {})
        if not isinstance(result, dict):
            logger.warning("Corrupt machines.json structure, resetting")
            return {}
        return result

    def _save(self, machines: dict[str, dict[str, Any]]) -> None:
        self._store.write("machines.json", {"machines": machines})

    @staticmethod
    def _alias_of(machines: dict[str, dict[str, Any]], name: str) -> str:
        """Canonical name for an alias, or '' when no machine carries it."""
        return next(
            (mname for mname, mdata in machines.items() if name in mdata.get("aliases", [])),
            "",
        )

    def resolve_name(self, name: str) -> str | None:
        """Resolve a name or alias to canonical machine name."""
        machines = self._load()
        if name in machines:
            return name
        return self._alias_of(machines, name) or None

    def get(self, name: str) -> Machine | None:
        canonical = self.resolve_name(name)
        if canonical is None:
            return None
        return Machine.from_dict(canonical, self._load()[canonical])

    def list_machines(self) -> dict[str, Machine]:
        return {name: Machine.from_dict(name, d) for name, d in self._load().items()}

    def add(self, machine: Machine) -> Machine:
        """Add or update a machine. Returns the stored machine."""
        from dataclasses import replace

        machine = validate_machine(machine)
        if not machine.added:
            machine = replace(machine, added=datetime.now(UTC).isoformat())
        with self._lock:
            machines = self._load()
            machines[machine.name] = machine.to_dict()
            self._save(machines)
        return machine

    def remove(self, name: str) -> bool:
        with self._lock:
            machines = self._load()
            canonical = name if name in machines else self._alias_of(machines, name)
            if canonical and canonical in machines:
                del machines[canonical]
                self._save(machines)
                return True
        return False

    def remember_key(self, machine: Machine) -> None:
        """Persist the key that just authenticated as this machine's key."""
        if not machine.key:
            return
        with self._lock:
            machines = self._load()
            if machine.name not in machines:
                return
            if machines[machine.name].get("key") == machine.key:
                return
            machines[machine.name]["key"] = machine.key
            self._save(machines)
