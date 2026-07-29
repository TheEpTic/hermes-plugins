from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import patch

from ssh_tools.handlers.transfer import handle_ssh_transfer


class StubManager:
    pass


def test_handler_rejects_non_boolean_flags() -> None:
    handler = handle_ssh_transfer(cast(Any, StubManager()))
    result = json.loads(
        handler(
            {
                "action": "upload",
                "machine": "web1",
                "source": "./release",
                "destination": "/srv/release",
                "recursive": "yes",
            }
        )
    )
    assert result["success"] is False
    assert "recursive must be a boolean" in result["error"]


def test_handler_honours_approval_denial() -> None:
    handler = handle_ssh_transfer(cast(Any, StubManager()))
    with patch(
        "ssh_tools.handlers.transfer.check_approval",
        return_value={"approved": False, "message": "approval required"},
    ):
        result = json.loads(
            handler(
                {
                    "action": "upload",
                    "machine": "web1",
                    "source": "./release",
                    "destination": "/srv/release",
                }
            )
        )
    assert result == {"success": False, "error": "approval required"}


def test_handler_dispatches_transfer() -> None:
    handler = handle_ssh_transfer(cast(Any, StubManager()))
    with (
        patch("ssh_tools.handlers.transfer.check_approval", return_value=None),
        patch(
            "ssh_tools.handlers.transfer.execute_transfer",
            return_value={"success": True, "action": "download", "bytes": 5},
        ) as execute,
    ):
        result = json.loads(
            handler(
                {
                    "action": "download",
                    "machine": "web1",
                    "source": "/var/log/app.log",
                    "destination": "./app.log",
                    "overwrite": True,
                }
            )
        )
    assert result["success"] is True
    execute.assert_called_once()
    assert execute.call_args.kwargs["overwrite"] is True
