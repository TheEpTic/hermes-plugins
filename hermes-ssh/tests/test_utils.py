"""Tests for ssh_tools.utils — ok, err, require."""

from __future__ import annotations

import json

from ssh_tools.utils import err, ok, require


def test_ok_returns_success_true() -> None:
    result = json.loads(ok())
    assert result["success"] is True


def test_ok_merges_data() -> None:
    result = json.loads(ok(count=5, name="test"))
    assert result["success"] is True
    assert result["count"] == 5
    assert result["name"] == "test"


def test_err_returns_success_false() -> None:
    result = json.loads(err("something broke"))
    assert result["success"] is False
    assert result["error"] == "something broke"


def test_require_missing_field() -> None:
    result = require({"action": "list"}, "name")
    assert result is not None
    assert "name" in result


def test_require_none_value() -> None:
    result = require({"name": None}, "name")
    assert result is not None


def test_require_present_field() -> None:
    result = require({"name": "test"}, "name")
    assert result is None


def test_require_empty_string_passes() -> None:
    """Empty string is a valid value (not None, not missing)."""
    result = require({"name": ""}, "name")
    assert result is None


def test_require_zero_is_valid() -> None:
    result = require({"port": 0}, "port")
    assert result is None


def test_require_false_is_valid() -> None:
    result = require({"flag": False}, "flag")
    assert result is None


def test_require_multiple_fields() -> None:
    result = require({"name": "test"}, "name", "host")
    assert result is not None
    assert "host" in result
