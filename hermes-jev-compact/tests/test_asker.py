"""Port-parity tests for request shaping + asker transport."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from hermes_jev_compact.asker import JevAsker, _default_transport
from hermes_jev_compact.protocol import JevError
from hermes_jev_compact.request import build_jev_body, noul_answer, parse_jev_response


def test_build_jev_body_has_no_stream_key():
    body = json.loads(build_jev_body("typesafe:jev-latest", {"a": 1}, {"q": {"type": "noul"}}))
    assert body == {
        "model": "typesafe:jev-latest",
        "state": {"a": 1},
        "questions": {"q": {"type": "noul"}},
    }
    assert "stream" not in body


def test_parse_jev_response_rejects():
    with pytest.raises(JevError, match="500"):
        parse_jev_response(500, False, "boom")
    with pytest.raises(JevError, match="malformed"):
        parse_jev_response(200, True, "not json")
    with pytest.raises(JevError, match="missing answers"):
        parse_jev_response(200, True, "{}")
    assert parse_jev_response(200, True, '{"answers":{}}') == {"answers": {}}


def test_noul_answer_validates():
    assert noul_answer({"q": {"noul": 0.4}}, "q") == 0.4
    for bad in [{}, {"q": {}}, {"q": {"noul": "x"}}, {"q": {"noul": float("nan")}}]:
        with pytest.raises(JevError, match="Invalid Jev answer"):
            noul_answer(bad, "q")


def test_asker_uses_transport_and_refuses_without_key():
    seen = {}

    def fake_transport(url, body, headers, timeout):
        seen["url"] = url
        seen["body"] = body
        seen["auth"] = headers["Authorization"]
        return (200, json.dumps({"answers": {"q": {"noul": 0.4}}}))

    asker = JevAsker("http://x:8765/v1", "k", "typesafe:jev-latest", transport=fake_transport)
    answers = asker.ask("state", {"q": {"type": "noul", "instructions": "x"}})
    assert answers["q"] == {"noul": 0.4}
    assert seen["url"] == "http://x:8765/v1/v1/systemone".replace("/v1/v1/", "/v1/")
    assert json.loads(seen["body"])["model"] == "typesafe:jev-latest"
    assert seen["auth"] == "Bearer k"

    with pytest.raises(JevError, match="not configured"):
        JevAsker("http://x:8765/v1", "", "typesafe:jev-latest")

    def failing(url, body, headers, timeout):
        raise TimeoutError("slow")

    with pytest.raises(JevError, match="transport"):
        JevAsker("http://x:8765/v1", "k", "m", transport=failing).ask({}, {})


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://x:70/1",
        "ftp://x/f",
        "http://192.168.0.25:8765/v1/systemone",  # cleartext off loopback
        "http://conduit.lan:8765/v1/systemone",
    ],
)
def test_default_transport_refuses_ssrf_and_cleartext(url: str):
    with patch("hermes_jev_compact.asker.urllib.request.urlopen") as opened:
        with pytest.raises(JevError, match="refusing"):
            _default_transport(url, b"{}", {}, 5.0)
    opened.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/v1/systemone",
        "http://localhost:8765/v1/systemone",
        "https://conduit.example.com/v1/systemone",
    ],
)
def test_default_transport_allows_loopback_http_and_any_https(url: str):
    with patch("hermes_jev_compact.asker.urllib.request.urlopen") as opened:
        opened.return_value.__enter__.return_value.status = 200
        opened.return_value.__enter__.return_value.read.return_value = b"{}"
        status, _ = _default_transport(url, b"{}", {}, 5.0)
    assert status == 200
    opened.assert_called_once()
