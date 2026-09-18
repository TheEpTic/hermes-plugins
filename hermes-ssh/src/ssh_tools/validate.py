"""Machine-name/host/user/key/port validation and normalization."""

from __future__ import annotations

import re
from dataclasses import replace

from .helpers import coerce_int
from .models import Machine

_HOST_RE = re.compile(r"[A-Za-z0-9_.:-]{1,253}")
_USER_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_NAME_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}")


def validate_machine_name(name: object) -> str | None:
    """Return an error message if the machine name is unsafe, else None."""
    if not isinstance(name, str):
        return "Machine name must be a string"
    if not _NAME_RE.fullmatch(name):
        return (
            "Machine name must be 1-64 chars, alphanumeric, dots, hyphens, "
            "underscores — no slashes, spaces, or glob metacharacters"
        )
    return None


def validate_host(host: object) -> str | None:
    """Return an error message if an SSH host string is unsafe."""
    if not isinstance(host, str) or not host:
        return "Host must be a non-empty string"
    if host.startswith("-") or "@" in host or not _HOST_RE.fullmatch(host):
        return "Host must be a hostname/IP with no spaces, @ signs, slashes, or shell characters"
    return None


def validate_user(user: object) -> str | None:
    """Return an error message if an SSH username is unsafe."""
    if not isinstance(user, str) or not _USER_RE.fullmatch(user):
        return "User must be 1-64 chars: letters, numbers, dots, hyphens, underscores"
    return None


def validate_key_path(key: object) -> str | None:
    """Return an error message if an SSH key path is unsafe."""
    if not isinstance(key, str):
        return "Key path must be a string"
    if any(ch in key for ch in ("\x00", "\n", "\r")):
        return "Key path must not contain control characters"
    return None


def validate_machine(machine: Machine) -> Machine:
    """Validate and normalize a machine before it is persisted."""
    for error in (
        validate_machine_name(machine.name),
        validate_host(machine.host),
        validate_user(machine.user),
        validate_key_path(machine.key),
    ):
        if error:
            raise ValueError(error)
    try:
        port = coerce_int(machine.port, "Port", minimum=1, maximum=65535)
    except ValueError as exc:
        raise ValueError("Port must be an integer from 1 to 65535") from exc
    aliases = machine.aliases or []
    tags = machine.tags or []
    if not isinstance(aliases, list) or any(validate_machine_name(a) for a in aliases):
        raise ValueError("Aliases must be a list of safe machine names")
    if not isinstance(tags, list) or any(not isinstance(t, str) or len(t) > 64 for t in tags):
        raise ValueError("Tags must be a list of strings up to 64 chars")
    if not isinstance(machine.description, str):
        raise ValueError("Description must be a string")
    if port != machine.port or aliases is not machine.aliases or tags is not machine.tags:
        machine = replace(machine, port=port, aliases=aliases, tags=tags)
    return machine
