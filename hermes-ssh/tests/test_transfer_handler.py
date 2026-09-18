"""Tests for the ssh_transfer handler: validation, approval, dispatch."""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import patch

import pytest

from ssh_tools.handlers.transfer import handle_ssh_transfer


class StubManager:
    pass


def _run(params: dict[str, Any], **patches: Any) -> dict[str, Any]:
    handler = handle_ssh_transfer(cast(Any, StubManager()))
    with (
        patch(
            "ssh_tools.handlers.transfer.check_approval",
            return_value=patches.get("approval"),
        ) as approval,
        patch(
            "ssh_tools.handlers.transfer.execute_transfer",
            return_value={"success": True, "action": "download", "bytes": 5},
        ) as execute,
    ):
        result = json.loads(handler(params))
    result["_approval_calls"] = approval.call_count
    result["_execute_calls"] = execute.call_count
    result["_approval_arg"] = approval.call_args.args[0] if approval.call_args else None
    return result


_BASE = {
    "action": "upload",
    "machine": "web1",
    "source": "./release",
    "destination": "/srv/release",
}


@pytest.mark.parametrize(
    "params,fragment",
    [
        ({**_BASE, "recursive": "yes"}, "recursive must be a boolean"),
        ({**_BASE, "source": "./release\nnext"}, "control characters"),
    ],
)
def test_handler_rejects_bad_params_before_approval(params: dict, fragment: str) -> None:
    result = _run(params)
    assert result["success"] is False and fragment in result["error"]
    assert result["_approval_calls"] == 0 and result["_execute_calls"] == 0


def test_handler_honours_approval_denial() -> None:
    result = _run(_BASE, approval={"approved": False, "message": "approval required"})
    assert result["success"] is False and result["error"] == "approval required"
    assert result["_execute_calls"] == 0


def test_handler_dispatches_transfer() -> None:
    result = _run({**_BASE, "action": "download", "overwrite": True})
    assert result["success"] is True
    assert result["_approval_calls"] == 1 and result["_execute_calls"] == 1


def test_handler_uses_copy_shape_for_sensitive_destination_approval() -> None:
    result = _run(
        {**_BASE, "source": "./sshd_config", "destination": "/etc/ssh/sshd_config"},
        approval={"approved": False, "message": "approval required"},
    )
    assert result == {
        "success": False,
        "error": "approval required",
        "_approval_calls": 1,
        "_execute_calls": 0,
        "_approval_arg": "cp -- ./sshd_config /etc/ssh/sshd_config",
    }
