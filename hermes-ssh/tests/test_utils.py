"""Tests for ssh_tools.helpers — ok, err, param_str, param_bool, coerce_int."""

from __future__ import annotations

import json

import pytest

from ssh_tools.helpers import coerce_int, err, ok, param_bool, param_str


def test_ok_err_envelopes() -> None:
    assert json.loads(ok()) == {"success": True}
    assert json.loads(ok(count=5)) == {"success": True, "count": 5}
    assert json.loads(err("boom")) == {"success": False, "error": "boom"}


def test_param_str_missing_and_wrong_type() -> None:
    assert param_str({}, "name") == (None, "name is required")
    value, error = param_str({"name": 5}, "name")
    assert value is None and error is not None and "name" in error
    assert param_str({"name": ""}, "name") == (None, "name must be a non-empty string")
    assert param_str({"name": "x"}, "name") == ("x", None)


def test_param_bool_defaults_and_wrong_type() -> None:
    assert param_bool({}, "background") == (False, None)
    assert param_bool({"background": True}, "background") == (True, None)
    value, error = param_bool({"background": "yes"}, "background")
    assert value is None and error is not None and "background" in error


def test_coerce_int_rejects_bools_and_garbage() -> None:
    assert coerce_int("300", "timeout", minimum=1) == 300
    for bad in (True, "abc", None, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            coerce_int(bad, "timeout")
