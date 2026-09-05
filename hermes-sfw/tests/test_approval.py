"""Regression tests for hermes-sfw approval lazy-loading.

SFW-approval: check_approval must resolve the Hermes approval functions on
each call rather than at module import time. Plugin discovery imports enabled
plugins in sequence; importing one plugin can still be inside another plugin's
package initializer, so binding ``tools.approval`` at module import time creates
an order-dependent circular-import failure.

Behavior pinned here:

- if Hermes approval is unavailable, check_approval returns a fail-closed denial
  (approved=False) instead of passing the command through;
- the approval functions are resolved lazily per call, so module import never
  imports ``tools.approval``;
- approvals.mode=off still bypasses the checks (the dedicated mode helper is
  consulted when present).
"""

from __future__ import annotations

from typing import Callable
from unittest.mock import patch

from hermes_sfw.approval import check_approval


def _deny(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {"status": "approval_required", "description": "dangerous command"}


def test_fail_closed_when_approval_unavailable() -> None:
    """No Hermes approval system -> commands are denied, not passed through."""
    with patch("hermes_sfw.approval._approval_functions", return_value=(None, None)):
        result = check_approval("pip install foo")
    assert result is not None
    assert result["approved"] is False
    assert "approval system is unavailable" in result["message"]


def test_approval_functions_loaded_lazily_per_call() -> None:
    """_approval_functions is invoked on each check, not at import time."""
    with patch("hermes_sfw.approval._approval_functions", return_value=(None, None)) as mock_load:
        check_approval("npm install")
        mock_load.assert_called_once_with()


def test_mode_off_bypasses_checks() -> None:
    """approvals.mode=off -> check_approval returns None (not denied)."""
    with patch(
        "hermes_sfw.approval._approval_functions",
        return_value=(_deny, lambda: "off"),
    ):
        result = check_approval("pnpm add foo")
    assert result is None


def test_dangerous_command_denied_when_mode_strict() -> None:
    """mode != off -> a dangerous command is denied with the flagged message."""
    with patch(
        "hermes_sfw.approval._approval_functions",
        return_value=(_deny, lambda: "strict"),
    ):
        result = check_approval("npm install foo")
    assert result is not None
    assert result["approved"] is False
    assert "dangerous command" in result["message"]


def test_approved_command_passes_through() -> None:
    """An approved (non-flagged) command returns the raw result (not denied)."""
    with patch(
        "hermes_sfw.approval._approval_functions",
        return_value=(lambda *_a, **_k: {"status": "ok"}, lambda: "strict"),
    ):
        result = check_approval("npm install foo")
    assert result == {"status": "ok"}
    # The raw (non-flagged) result is surfaced, not converted into a denial.
