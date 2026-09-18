"""Port-parity tests for request shaping + asker transport."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from hermes_jev_compact.asker import JevAsker, _default_transport, _normalize_endpoint_path
from hermes_jev_compact.protocol import JevError
from hermes_jev_compact.request import build_jev_body, noul_answer, parse_jev_response


def test_build_jev_body_has_no_stream_key():
    body = json.loads(build_jev_body("jev-latest", {"a": 1}, {"q": {"type": "noul"}}))
    assert body == {
        "model": "jev-latest",
        "state": {"a": 1},
        "questions": {"q": {"type": "noul"}},
    }
    assert "stream" not in body
    # NaN/Infinity are not valid JSON — fail closed, don't ship "NaN" to routers.
    with pytest.raises(JevError, match="not JSON-serializable"):
        build_jev_body("m", {"x": float("nan")}, {})


def test_parse_jev_response_rejects():
    with pytest.raises(JevError, match="500"):
        parse_jev_response(500, "boom")
    with pytest.raises(JevError, match="malformed"):
        parse_jev_response(200, "not json")
    with pytest.raises(JevError, match="missing answers"):
        parse_jev_response(200, "{}")
    assert parse_jev_response(200, '{"answers":{}}') == {"answers": {}}
    # status is the authority: a stale ok=True with a 500 must not succeed.
    with pytest.raises(JevError, match="mismatch"):
        parse_jev_response(500, '{"answers":{}}', ok=True)


def test_noul_answer_validates():
    assert noul_answer({"q": {"noul": 0.4}}, "q") == 0.4
    assert noul_answer({"q": {"noul": 1}}, "q") == 1.0  # ints are fine
    for bad in [
        {},
        {"q": {}},
        {"q": {"noul": "x"}},
        {"q": {"noul": float("nan")}},
        {"q": {"noul": True}},
        {"q": {"noul": -0.1}},  # finite but not a probability
        {"q": {"noul": 1.5}},
    ]:
        with pytest.raises(JevError, match="Invalid Jev answer"):
            noul_answer(bad, "q")


def test_asker_uses_transport_and_refuses_without_key():
    seen = {}

    def fake_transport(url, body, headers, timeout):
        seen["url"] = url
        seen["body"] = body
        seen["auth"] = headers["Authorization"]
        return (200, json.dumps({"answers": {"q": {"noul": 0.4}}}))

    asker = JevAsker("http://x:8765/v1", "k", "jev-latest", transport=fake_transport)
    answers = asker.ask("state", {"q": {"type": "noul", "instructions": "x"}})
    assert answers["q"] == {"noul": 0.4}
    assert seen["url"] == "http://x:8765/v1/systemone"
    assert json.loads(seen["body"])["model"] == "jev-latest"
    assert seen["auth"] == "Bearer k"

    with pytest.raises(JevError, match="not configured"):
        JevAsker("http://x:8765/v1", "", "jev-latest")
    # Query survives the join (path before ?); fragments are refused.
    assert (
        JevAsker("https://x/v1/?tenant=t", "k", "m", transport=fake_transport)._url
        == "https://x/v1/systemone?tenant=t"
    )
    with pytest.raises(JevError, match="fragment"):
        JevAsker("https://x/v1#frag", "k", "m")
    # Timeout must be finite + positive (inf/nan/zero all fail closed).
    for bad_timeout in (float("inf"), float("nan"), 0, -1):
        with pytest.raises(JevError, match="invalid jev timeout"):
            JevAsker("http://x:8765/v1", "k", "m", timeout_s=bad_timeout)


def test_asker_endpoint_path_override():
    seen = {}

    def fake_transport(url, body, headers, timeout):
        seen["url"] = url
        return (200, json.dumps({"answers": {"q": {"noul": 0.4}}}))

    # OpenRouter Decisions API shape: absolute base + alpha path.
    asker = JevAsker(
        "https://openrouter.ai",
        "k",
        "typesafe/jev-1.13",
        transport=fake_transport,
        endpoint_path="/api/alpha/decisions",
    )
    assert asker.ask("state", {"q": {}})["q"] == {"noul": 0.4}
    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
    # Default unchanged: existing configs keep hitting /systemone.
    assert (
        JevAsker("https://x/v1", "k", "m", transport=fake_transport)._url
        == "https://x/v1/systemone"
    )
    # Trailing slash on the path is tolerated, not doubled.
    assert (
        JevAsker(
            "https://x/v1/", "k", "m", transport=fake_transport, endpoint_path="/systemone/"
        )._url
        == "https://x/v1/systemone"
    )
    # Garbage / hostile paths fail closed to the default — never a surprising URL.
    for bad_path in (
        "",
        "systemone",
        "https://evil.example/pwn",
        "//evil.example/pwn",
        "/systemone?tenant=t",
        "/systemone#frag",
        "/sys temone",
        "/x@evil.example",
        "/",
    ):
        assert (
            JevAsker(
                "https://x/v1", "k", "m", transport=fake_transport, endpoint_path=bad_path
            )._url
            == "https://x/v1/systemone"
        ), bad_path

    def failing(url, body, headers, timeout):
        raise TimeoutError("slow")

    with pytest.raises(JevError, match="transport"):
        JevAsker("http://x:8765/v1", "k", "m", transport=failing).ask({}, {})


@pytest.fixture
def mocked_opener():
    with patch("hermes_jev_compact.asker.urllib.request.build_opener") as mk_opener:
        resp = mk_opener.return_value.open.return_value.__enter__.return_value
        resp.status = 200
        resp.read.return_value = b"{}"
        yield mk_opener


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("file:///etc/passwd", False),
        ("gopher://x:70/1", False),
        ("ftp://x/f", False),
        ("http://jev.internal:8765/v1/systemone", False),  # DNS names must use https
        ("http://8.8.8.8:8765/v1/systemone", False),  # cleartext to public ip
        ("http://169.254.169.254/latest/meta-data/", False),  # cloud metadata endpoint
        ("http://0.0.0.0:8765/v1/systemone", False),  # unspecified
        ("http://2130706433:8765/v1/systemone", False),  # decimal-encoded loopback
        ("http://0177.0.0.1:8765/v1/systemone", False),  # octal-encoded loopback
        ("http://0x7f000001:8765/v1/systemone", False),  # hex-encoded loopback
        ("http://127.1:8765/v1/systemone", False),  # short-form loopback
        ("http://127.0.0.1.:8765/v1/systemone", False),  # trailing-dot loopback
        ("http://[::ffff:127.0.0.1]:8765/v1/systemone", False),  # v4-mapped loopback
        ("http://[ff02::1]:8765/v1/systemone", False),  # multicast
        ("http://[2001:db8::1]:8765/v1/systemone", False),  # public v6 (docs range)
        ("https://user:pass@api.typesafe.ai/v1/systemone", False),  # embedded creds
        ("http://@127.0.0.1:8765/v1/systemone", False),  # empty userinfo still refused
        ("http://[::1/x", False),  # malformed IPv6 literal
        ("http://127.0.0.1:8765/v1/systemone", True),
        ("http://localhost:8765/v1/systemone", True),
        ("http://127.0.0.2:8765/v1/systemone", True),  # full 127/8 loopback range
        ("http://192.168.0.25:8765/v1/systemone", True),  # RFC1918 LAN (conduit2)
        ("http://10.99.0.1:8765/v1/systemone", True),  # RFC1918 10/8
        ("http://172.16.5.4:8765/v1/systemone", True),  # RFC1918 172.16/12
        ("http://100.103.204.82:8765/v1/systemone", True),  # Tailscale CGNAT
        ("http://[::1]:8765/v1/systemone", True),  # IPv6 loopback
        ("http://[fc00::1]:8765/v1/systemone", True),  # IPv6 ULA
        ("http://[fe80::1]:8765/v1/systemone", True),  # IPv6 link-local
        ("https://api.typesafe.ai/v1/systemone", True),
        ("https://api.typesafe.ai/v1/systemone?key=secret", True),  # query w/o userinfo: allowed
    ],
)
def test_default_transport_url_policy(mocked_opener, url: str, allowed: bool):
    if allowed:
        status, _ = _default_transport(url, b"{}", {}, 5.0)
        assert status == 200
        mocked_opener.return_value.open.assert_called_once()
    else:
        with pytest.raises(JevError, match="refusing|invalid jev url"):
            _default_transport(url, b"{}", {}, 5.0)
        mocked_opener.assert_not_called()


def test_default_transport_refuses_redirect():
    from hermes_jev_compact.asker import _NoRedirect

    handler = _NoRedirect()
    with pytest.raises(JevError, match="refusing redirect"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/x")  # type: ignore[arg-type]


def test_default_transport_rejects_oversize_body():
    with patch("hermes_jev_compact.asker.urllib.request.build_opener") as mk_opener:
        resp = mk_opener.return_value.open.return_value.__enter__.return_value
        resp.status = 200
        resp.read.return_value = b"x" * (1_000_000 + 1)
        with pytest.raises(JevError, match="over .* byte cap"):
            _default_transport("https://api.typesafe.ai/v1/systemone", b"{}", {}, 5.0)


def test_normalize_endpoint_path():
    assert _normalize_endpoint_path("/api/alpha/decisions") == "/api/alpha/decisions"
    assert _normalize_endpoint_path("  /systemone  ") == "/systemone"
    for bad in ("", "relative", "https://x/y", "//x/y", "/a?b=c", "/a#f", "/a b", "/@x", "/"):
        assert _normalize_endpoint_path(bad) == "/systemone", bad


def test_parse_jev_response_hides_upstream_error_body():
    with pytest.raises(JevError, match=r"http 500"):
        parse_jev_response(500, False, "secret-body\ninjected: yes")
    try:
        parse_jev_response(500, False, "secret-body\ninjected: yes")
    except JevError as exc:
        assert "secret-body" not in str(exc)
        assert "\n" not in str(exc)
