"""Regression tests for the ssh_machines registry guard (SSH-4).

When a machine is added whose host+user already exist under a different
name, the add response must carry a non-blocking warning so agents stop
creating throwaway aliases (a web host registered under both a project name and a generic name).
The warning never blocks the add; only re-adding the exact same name
takes the existing overwrite/update path.
"""

from __future__ import annotations

import getpass
import json
from typing import TYPE_CHECKING, Any

import pytest

from ssh_tools.handlers import handle_ssh_machines
from ssh_tools.models import Machine

from .conftest import _make_manager

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

HOST = "10.0.0.1"
USER = "admin"


def _add(
    handler: Callable[[dict[str, Any]], str],
    name: str,
    host: str = HOST,
    user: str | None = USER,
) -> dict[str, Any]:
    params: dict[str, Any] = {"action": "add", "name": name, "host": host}
    if user is not None:
        params["user"] = user
    return json.loads(handler(params))


def _make_handler(tmp_path: Path) -> Callable[[dict[str, Any]], str]:
    return handle_ssh_machines(_make_manager(tmp_path))


@pytest.mark.parametrize(
    "first,second,expect_warning",
    [
        ("host1", "host2", True),  # same host+user, different name -> warn
        ("host1", "host1", False),  # exact same name -> update, no warning
    ],
)
def test_add_duplicate_warning(
    tmp_path: Path, first: str, second: str, expect_warning: bool
) -> None:
    handler = _make_handler(tmp_path)
    assert _add(handler, first)["success"] is True

    result = _add(handler, second)
    assert result["success"] is True
    assert ("warning" in result) is expect_warning
    if expect_warning:
        assert result["warning"] == f"host {HOST} with user {USER} already registered as name host1"


def test_add_duplicate_warning_does_not_block_add(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)
    _add(handler, "host1")

    result = _add(handler, "host2")
    assert result["success"] is True
    assert result["machine"]["host"] == HOST
    assert "warning" in result

    listed = json.loads(handler({"action": "list"}))
    assert listed["count"] == 2
    assert "host1" in listed["machines"]
    assert "host2" in listed["machines"]


@pytest.mark.parametrize(
    "setup,second,kwargs",
    [
        (("host1", {"host": HOST}), "host1", {"host": "2.2.2.2"}),  # same name, other host
        (("host1", {"user": USER}), "host2", {"user": "deploy"}),  # same host, other user
    ],
)
def test_add_no_warning_for_updates_and_other_users(
    tmp_path: Path, setup: tuple, second: str, kwargs: dict
) -> None:
    handler = _make_handler(tmp_path)
    _add(handler, setup[0], **setup[1])

    result = _add(handler, second, **kwargs)
    assert result["success"] is True
    assert "warning" not in result


def test_add_duplicate_warning_carries_diagnose_hint(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)
    _add(handler, "host1")

    result = _add(handler, "host2")
    assert "warning" in result
    assert result["hint"].startswith("If you meant to reuse that host")
    for fragment in ("host1", "inspect", "list"):
        assert fragment in result["hint"]


def test_add_duplicate_multiple_matches_lists_all(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)
    _add(handler, "host1")
    _add(handler, "host2")

    result = _add(handler, "host3")
    assert result["success"] is True
    assert result["warning"] == (
        f"host {HOST} with user {USER} already registered as names host1, host2"
    )


def test_add_duplicate_check_uses_effective_default_user(tmp_path: Path) -> None:
    """An omitted user resolves to the local user before the guard runs."""
    handler = _make_handler(tmp_path)
    first = _add(handler, "host1", user=None)
    assert first["success"] is True
    default_user = first["machine"]["user"]
    assert default_user == getpass.getuser()

    result = _add(handler, "host2", user=None)
    assert result["success"] is True
    assert result["warning"] == (
        f"host {HOST} with user {default_user} already registered as name host1"
    )


def test_registry_guard_ignores_machines_with_other_hosts(tmp_path: Path) -> None:
    """Machines on different hosts never trigger the guard."""
    mgr = _make_manager(tmp_path)
    mgr.add_machine(Machine(name="web1", host="192.168.1.50", user=USER))
    handler = handle_ssh_machines(mgr)

    result = _add(handler, "web2")
    assert result["success"] is True
    assert "warning" not in result
