"""Sync Jev asker over conduit2 systemone (stdlib urllib, per-call transport)."""

from __future__ import annotations

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


def _default_transport(
    url: str, body: bytes, headers: dict[str, str], timeout_s: float
) -> tuple[int, str]:
    # Fail closed on scheme: https anywhere, http only for loopback
    # (conduit2 defaults to http://127.0.0.1:8765). Anything else (file:,
    # gopher:, remote http:) is a config error, not a request.
    parts = urlsplit(url)
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    if scheme not in {"http", "https"}:
        raise JevError(f"refusing non-http conduit url: {scheme or '(none)'}")
    if scheme == "http" and host not in {
        "localhost",
        "127.0.0.1",
        "::1",
        "0:0:0:0:0:0:0:1",
    }:
        raise JevError(f"refusing cleartext conduit url for non-loopback host: {host}")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    ctx = ssl.create_default_context() if scheme == "https" else None
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            return (int(resp.status), resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
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
            raise JevError("conduit api key is not configured")
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
            raise JevError(f"Jev transport failed: {exc}") from exc
        answers = parse_jev_response(status, 200 <= status < 300, text).get("answers")
        if not isinstance(answers, dict):
            raise JevError("Jev response is missing answers")
        return answers
