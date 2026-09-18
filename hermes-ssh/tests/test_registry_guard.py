"""Regression tests for the ssh_machines registry guard (SSH-4).

Adding a machine whose host+user already exist under a different name
carries a non-blocking warning, so agents stop creating throwaway aliases.
"""

from __future__ import annotations

import getpass
import json
from typing import TYPE_CHECKING, Any

import pytest

from ssh_tools.handlers import handle_ssh_machines

from .conftest import _make_manager

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

HOST = "10.0.0.1"
USER = "admin"


def _make_handler(tmp_path: Path) -> Callable[[dict[str, Any]], str]:
    return handle_ssh_machines(_make_manager(tmp_path))


def _add(handler: Callable[[dict[str, Any]], str], name: str, **kw: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"action": "add", "name": name, "host": kw.pop("host", HOST)}
    if "user" in kw or True:
        user = kw.pop("user", USER)
        if user is not None:
            params["user"] = user
    params.update(kw)
    return json.loads(handler(params))


def test_warns_and_still_adds(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)
    assert _add(handler, "host1")["success"] is True
    result = _add(handler, "host2")
    assert result["success"] is True and result["machine"]["host"] == HOST
    assert result["warning"] == f"host {HOST} with user {USER} already registered as name host1"
    assert result["hint"].startswith("If you meant to reuse that host")
    for fragment in ("host1", "inspect", "list"):
        assert fragment in result["hint"]
    listed = json.loads(handler({"action": "list"}))
    assert listed["count"] == 2 and set(listed["machines"]) == {"host1", "host2"}


@pytest.mark.parametrize(
    "second,kwargs",
    [
        ("host1", {"host": "2.2.2.2"}),  # same name = update, no warning
        ("host2", {"user": "deploy"}),  # same host, other user
        ("web2", {"seed_host": "192.168.1.50"}),  # other host entirely
    ],
)
def test_no_warning(tmp_path: Path, second: str, kwargs: dict) -> None:
    handler = _make_handler(tmp_path)
    seed_host = kwargs.pop("seed_host", HOST)
    _add(handler, "host1", host=seed_host)
    result = _add(handler, second, **kwargs)
    assert result["success"] is True and "warning" not in result


def test_multiple_matches_lists_all(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)
    _add(handler, "host1")
    _add(handler, "host2")
    result = _add(handler, "host3")
    assert result["success"] is True
    assert result["warning"] == (
        f"host {HOST} with user {USER} already registered as names host1, host2"
    )


def test_uses_effective_default_user(tmp_path: Path) -> None:
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
