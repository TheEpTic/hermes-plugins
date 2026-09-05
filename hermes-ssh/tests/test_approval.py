"""Regression tests for hermes-ssh approval lazy-loading.

SSH-approval: check_approval must resolve the Hermes approval functions on
each call rather than at module import time. Plugin discovery imports enabled
plugins in sequence; importing one plugin can still be inside another plugin's
package initializer, so binding ``tools.approval`` at module import time creates
an order-dependent circular-import failure.

Behavior pinned here:

- if Hermes approval is unavailable, check_approval returns a fail-closed denial
  (approved=False) instead of passing the command through;
- the approval functions are resolved lazily per call, so module import never
  imports ``tools.approval``;
- approvals.mode=off still bypasses the checks;
- a dangerous command (mode != off) is denied with the approval-required message.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from ssh_tools.approval import check_approval


def _deny(_command: str, **_kwargs: Any) -> dict[str, Any]:
    return {"status": "approval_required", "description": "recursive delete"}


def _ok(_command: str, **_kwargs: Any) -> dict[str, Any]:
    return {"status": "ok"}


def test_fail_closed_when_approval_unavailable() -> None:
    """No Hermes approval system -> commands are denied, not passed through."""
    with patch("ssh_tools.approval._approval_functions", return_value=(None, None)):
        result = check_approval("rm -rf /home")
    assert result is not None
    assert result["approved"] is False
    assert "approval system" in result["message"]


def test_approval_functions_loaded_lazily_per_call() -> None:
    """_approval_functions is invoked on each check, not at import time."""
    with patch("ssh_tools.approval._approval_functions", return_value=(None, None)) as mock_load:
        check_approval("rm -rf /home")
        mock_load.assert_called_once_with()


def test_mode_off_bypasses_checks() -> None:
    """approvals.mode=off -> check_approval returns None (not denied)."""
    with patch(
        "ssh_tools.approval._approval_functions",
        return_value=(_deny, lambda: "off"),
    ):
        result = check_approval("rm -rf /home")
    assert result is None


def test_dangerous_command_denied_when_mode_strict() -> None:
    """mode != off -> a dangerous command is denied with the flagged message."""
    with patch(
        "ssh_tools.approval._approval_functions",
        return_value=(_deny, lambda: "strict"),
    ):
        result = check_approval("rm -rf /home")
    assert result is not None
    assert result["approved"] is False
    assert "/approve" in result["message"]
    assert "/deny" in result["message"]


def test_non_flagged_command_passes_through() -> None:
    """A non-dangerous command returns the raw result (not denied)."""
    with patch(
        "ssh_tools.approval._approval_functions",
        return_value=(_ok, lambda: "strict"),
    ):
        result = check_approval("ls -la")
    assert result == {"status": "ok"}
