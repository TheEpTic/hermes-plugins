"""Sync Jev asker over any Decisions-shaped endpoint (stdlib urllib).

Threat posture: base_url + key come from OPERATOR config + operator .env, so
a malicious URL is a self-own, not remote input. The guard below exists to
catch config typos/redirects (file:, gopher:, cleartext-off-LAN) before
the bearer key is sent anywhere surprising — fail closed, fall back to the
built-in prune.
"""

from __future__ import annotations

import ipaddress
import math
import re
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .protocol import JevError
from .request import DEFAULT_ENDPOINT_PATH, build_jev_body, parse_jev_response

Transport = Callable[[str, bytes, dict[str, str], float], tuple[int, str]]

# Largest systemone body we will buffer: jev answers are small probabilities
# (~hundreds of bytes/question). Anything past this is a broken/compromised
# endpoint — fail closed rather than buffering unbounded bytes into prune.
MAX_RESPONSE_BYTES = 1_000_000


def _is_loopback_host(host: str) -> bool:
    # localhost is the one permitted DNS alias (pre-existing behavior);
    # all other names must use https. Canonical v4/v6 loopbacks match via
    # is_loopback below — but NOT v4-mapped ::ffff:127.x (urllib may route
    # those as v4, so they take the explicit-network path instead).
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return False
    return ip.is_loopback


# Cleartext http is allowed exactly for loopback + these non-routed ranges:
# RFC1918 LANs (self-hosted routers like conduit2), Tailscale CGNAT
# (100.64/10 — NOT covered by is_private), ULA + link-local IPv6. The
# ips are checked against explicit networks, never is_private — that flag
# also matches 169.254/16 link-local, i.e. the cloud metadata endpoint.
_CLEAR_NETWORKS_V4 = tuple(
    ipaddress.ip_network(c)
    for c in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8")
)
_CLEAR_NETWORKS_V6 = tuple(
    ipaddress.ip_network(c) for c in ("::1/128", "fc00::/7", "fe80::/10", "fec0::/10")
)


def _allows_cleartext(host: str) -> bool:
    if _is_loopback_host(host):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # DNS names must use https — no resolution at prune time
    nets = _CLEAR_NETWORKS_V6 if ip.version == 6 else _CLEAR_NETWORKS_V4
    return any(ip in net for net in nets)


def _check_url(url: str) -> tuple[str, str]:
    """Fail-closed URL policy: https anywhere, http LAN-only, no userinfo.

    Returns (scheme, host) for the caller. Raises JevError on anything else —
    the caller treats that as a failed jev attempt (built-in prune).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        raise JevError("invalid jev url") from None
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    if scheme not in {"http", "https"}:
        raise JevError(f"refusing non-http jev url: {scheme or '(none)'}")
    # "@" anywhere in netloc means userinfo was present — even an empty
    # username (http://@host/) violates the no-userinfo policy and risks
    # parser/request-library disagreement downstream.
    if "@" in parts.netloc:
        raise JevError("refusing jev url with embedded credentials")
    if scheme == "http" and not _allows_cleartext(host):
        raise JevError(f"refusing cleartext jev url for non-LAN host: {host or '(none)'}")
    return scheme, host


# Conservative endpoint-path charset (review finding: percent-escapes like
# %2f/%3f and unicode look-alikes must not reach the URL — a proxy/router
# could reinterpret them as delimiters). Real Decisions paths (/systemone,
# /api/alpha/decisions) are all in this set.
_ENDPOINT_PATH_CHARS = re.compile(r"[A-Za-z0-9/._\-~]+")


def _normalize_endpoint_path(endpoint_path: str) -> str:
    """Validate an operator-supplied endpoint path (fail closed to default).

    The path is operator config, not remote input — but it flows into the
    request URL, so keep it to a plain absolute path of unreserved chars:
    ASCII letters, digits, and ``/._-~`` only. No scheme, host, query,
    fragment, credentials — and no percent-escapes (``%2f``/``%3f`` smuggle
    delimiters a proxy/router could reinterpret) or non-ASCII/control bytes.
    Anything else returns the default, so a typo degrades to
    TypeSafe-shaped behavior (key mismatch then fails closed at auth)
    instead of building a surprising URL.
    """
    candidate = (endpoint_path or "").strip()
    if not candidate.startswith("/") or " " in candidate:
        return DEFAULT_ENDPOINT_PATH
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return DEFAULT_ENDPOINT_PATH
    if parts.scheme or parts.netloc or parts.query or parts.fragment or "@" in candidate:
        return DEFAULT_ENDPOINT_PATH
    # Match against the raw candidate, not the parsed path: urlsplit strips
    # ASCII newlines/tabs before parsing, so validating parts.path would let
    # "/x\ny" through as "/xy" — a control byte that must fail closed.
    if "%" in candidate or _ENDPOINT_PATH_CHARS.fullmatch(candidate) is None:
        return DEFAULT_ENDPOINT_PATH
    # Drop a trailing slash so base "https://x/" + path stays clean.
    return candidate.rstrip("/") or DEFAULT_ENDPOINT_PATH


def _join_systemone(base_url: str, endpoint_path: str = DEFAULT_ENDPOINT_PATH) -> str:
    """base_url + endpoint path without mangling query/fragment.

    String concat would turn ?tenant=x into ?tenant=x/systemone (path becomes
    query). Fragments never belong on an API endpoint — reject them.
    """
    try:
        parts = urlsplit(base_url)
    except ValueError:
        raise JevError("invalid jev base url") from None
    if parts.fragment:
        raise JevError("refusing jev base url with fragment")
    path = parts.path.rstrip("/") + _normalize_endpoint_path(endpoint_path)
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


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
        endpoint_path: str = DEFAULT_ENDPOINT_PATH,
    ) -> None:
        if not api_key:
            raise JevError("jev api key is not configured")
        self._url = _join_systemone(base_url, endpoint_path)
        self._api_key = api_key
        self._model = model
        # Finite positive timeout only: inf would hand transports an unbounded
        # deadline, NaN clamps accidentally — fail closed at construction.
        try:
            timeout_f = float(timeout_s)
        except (TypeError, ValueError):
            raise JevError("invalid jev timeout") from None
        if not math.isfinite(timeout_f) or timeout_f <= 0:
            raise JevError("invalid jev timeout")
        self._timeout_s = timeout_f
        self._transport = transport or _default_transport
        self._deadline = time.monotonic() + self._timeout_s

    def ask(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise JevError("Jev budget exhausted before request")
        try:
            body = build_jev_body(self._model, state, questions).encode("utf-8")
        except JevError:
            raise
        except Exception as exc:
            raise JevError(f"jev request shaping failed: {type(exc).__name__}") from exc
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
        answers = parse_jev_response(status, text).get("answers")
        if not isinstance(answers, dict):
            raise JevError("Jev response is missing answers")
        return answers
