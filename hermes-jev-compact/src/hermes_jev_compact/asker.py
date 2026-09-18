"""Sync Jev asker over any OpenAI-style /v1/systemone endpoint (stdlib urllib).

Threat posture: base_url + key come from OPERATOR config + operator .env, so
a malicious URL is a self-own, not remote input. The guard below exists to
catch config typos/redirects (file:, gopher:, cleartext-off-loopback) before
the bearer key is sent anywhere surprising — fail closed, fall back to the
built-in prune.
"""

from __future__ import annotations

import ipaddress
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from .protocol import JevError
from .request import SYSTEMONE_PATH, build_jev_body, parse_jev_response

Transport = Callable[[str, bytes, dict[str, str], float], tuple[int, str]]

# Largest systemone body we will buffer: jev answers are small probabilities
# (~hundreds of bytes/question). Anything past this is a broken/compromised
# endpoint — fail closed rather than buffering unbounded bytes into prune.
MAX_RESPONSE_BYTES = 1_000_000


def _is_loopback_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_url(url: str) -> tuple[str, str]:
    """Fail-closed URL policy: https anywhere, http loopback-only, no userinfo.

    Returns (scheme, host) for the caller. Raises JevError on anything else —
    the caller treats that as a failed jev attempt (built-in prune).
    """
    parts = urlsplit(url)
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    if scheme not in {"http", "https"}:
        raise JevError(f"refusing non-http jev url: {scheme or '(none)'}")
    if parts.username or parts.password:
        raise JevError("refusing jev url with embedded credentials")
    if scheme == "http" and not _is_loopback_host(host):
        raise JevError(f"refusing cleartext jev url for non-loopback host: {host or '(none)'}")
    return scheme, host


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib follows 3xx automatically — a redirect would bypass _check_url.

    Raise instead: jev endpoints answer POST in place; a redirect means the
    base_url is wrong, not that we should chase it with the bearer key.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise JevError(f"refusing redirect to {urlsplit(newurl).scheme or '(none)'}://…")


def _read_capped(resp: Any) -> str:
    raw: bytes = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise JevError(f"jev response over {MAX_RESPONSE_BYTES} byte cap")
    return raw.decode("utf-8", "replace")


def _default_transport(
    url: str, body: bytes, headers: dict[str, str], timeout_s: float
) -> tuple[int, str]:
    scheme, _ = _check_url(url)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    handlers: list[Any] = [_NoRedirect()]
    if scheme == "https":
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return (int(resp.status), _read_capped(resp))
    except urllib.error.HTTPError as exc:
        try:
            text = _read_capped(exc)
        except Exception:
            text = ""
        return (int(exc.code or 0), text)


class JevAsker:
    """One-shot asker: created per prune call, holds no connection state."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 30.0,
        transport: Transport | None = None,
    ) -> None:
        if not api_key:
            raise JevError("jev api key is not configured")
        self._url = base_url.rstrip("/") + SYSTEMONE_PATH
        self._api_key = api_key
        self._model = model
        self._timeout_s = max(1.0, timeout_s)
        self._transport = transport or _default_transport
        self._deadline = time.monotonic() + self._timeout_s

    def ask(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise JevError("Jev budget exhausted before request")
        body = build_jev_body(self._model, state, questions).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            status, text = self._transport(
                self._url, body, headers, min(self._timeout_s, remaining)
            )
        except JevError:
            raise
        except Exception as exc:
            # Transport exceptions can echo the request URL (and its query);
            # _check_url already rejects userinfo, but never log a raw URL.
            raise JevError(f"jev transport failed: {type(exc).__name__}") from exc
        answers = parse_jev_response(status, 200 <= status < 300, text).get("answers")
        if not isinstance(answers, dict):
            raise JevError("Jev response is missing answers")
        return answers
