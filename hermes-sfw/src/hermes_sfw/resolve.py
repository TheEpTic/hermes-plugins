"""Binary discovery: PATH/known-location search, shim layers, launcher caches.

The npm/pnpm distribution ships a launcher, not an engine: ``dist/sfw.mjs``
downloads the platform firewall binary into
``<package root>/.sfw-cache/<release>/<asset>`` and points
``.sfw-cache/latest`` at it. A launcher whose ``latest`` link does not
resolve cannot start: it exits 1 with "Failed to prepare firewall binary"
before the wrapped command runs, so a routed command (``cargo build``,
``pnpm test``) fails with no visible cause. The state is detectable
offline and has an exact repair, so it is checked before a launcher is
handed to the terminal guard or to a tool call.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
from pathlib import Path

from .models import SFWBinaryInfo, SFWCacheFault

# pnpm-style wrapper shims exec a real JS entry point (a cmd-shim or shell
# shim). The shim layer must be reported separately from the binary version.
_IS_NPM_SHIM_RE = re.compile(r"(?:^|/)(?:pnpm|npm-global)/")
# npm's cmd-shim writes the real target into the shim in two forms:
#   exec node "$basedir/../global/.../node_modules/sfw/dist/sfw.mjs" "$@"
#   # cmd-shim-target=/absolute/path/to/sfw.mjs
# The cmd-shim-target marker is the canonical absolute form and is preferred.
_CMD_SHIM_TARGET_RE = re.compile(r"cmd-shim-target=(\S+)")
_SHIM_EXEC_TARGET_RE = re.compile(r"exec\s+(?:\S+\s+)?[\"\']?([^\s\"\']+?\.mjs)[\"\']?\s+\"\$@\"")

# Common npm/pnpm shim and binary locations, walked in order. A shim is only
# usable if its real target resolves; broken shims are skipped and reported.
_KNOWN_BINARY_CANDIDATES = (
    ".local/share/pnpm/sfw",
    ".local/share/pnpm/bin/sfw",
    ".local/bin/sfw",
    ".npm-global/bin/sfw",
    ".cargo/bin/sfw",
    "/usr/local/bin/sfw",
)

_SFW_CACHE_DIR = ".sfw-cache"
_SFW_LATEST_LINK = "latest"
_SFW_ASSET_PREFIX = "sfw-free-"
_SFW_BOOTSTRAP_FAILURE_RE = re.compile(r"Failed to prepare firewall binary", re.IGNORECASE)


def known_candidates() -> list[Path]:
    """Known shim/binary locations, newest home-aware first."""
    home = Path.home()
    return [home / rel for rel in _KNOWN_BINARY_CANDIDATES]


def is_bootstrap_failure(*streams: str) -> bool:
    return any(_SFW_BOOTSTRAP_FAILURE_RE.search(s or "") for s in streams)


def resolve_shim_target(binary: str) -> str | None:
    """Resolve the real target of an npm-style shim, or None if not one.

    Reads the shim script and extracts the real entry point it execs,
    preferring the canonical ``cmd-shim-target`` marker over the
    ``exec ... sfw.mjs`` line. Symlinks are resolved first so a link into
    a pnpm shim (e.g. ``/usr/local/bin/sfw`` -> pnpm shim) is still
    detected. Returns None for real binaries or shims whose target cannot
    be determined.
    """
    binary_path = Path(binary)
    try:
        resolved = str(binary_path.resolve())
    except (OSError, RuntimeError):
        resolved = binary
    if not _IS_NPM_SHIM_RE.search(resolved):
        return None
    try:
        content = binary_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    marker = _CMD_SHIM_TARGET_RE.search(content)
    if marker is not None:
        return str(Path(marker.group(1)).expanduser())
    exec_match = _SHIM_EXEC_TARGET_RE.search(content)
    if exec_match is None:
        return None
    raw = exec_match.group(1)
    # The exec form is written relative to the shim's own directory and
    # may reference $basedir (resolved by the shim at runtime).
    if raw.startswith("$basedir/"):
        raw = str(binary_path.parent / raw[len("$basedir/") :])
    target_path = Path(raw)
    if not target_path.is_absolute():
        target_path = binary_path.parent / target_path
    return str(target_path.expanduser())


def classify_binary(binary: str) -> SFWBinaryInfo:
    """Classify a resolved binary path by layer.

    Returns which layer the binary lives in (``npm-shim`` for pnpm/npm
    wrapper shims, ``binary`` for real executables) and, for shims, the
    resolved real target entry point.
    """
    target = resolve_shim_target(binary)
    if target is not None:
        return SFWBinaryInfo(binary=binary, binary_kind="npm-shim", target=target)
    return SFWBinaryInfo(binary=binary, binary_kind="binary", target=None)


def _resolves_to_file(path: Path) -> bool:
    """True when *path* exists and follows to a regular file."""
    try:
        return path.is_file()
    except OSError:
        return False


def wrapper_install_root(binary: str) -> Path | None:
    """Package root of an npm/pnpm sfw launcher, or None for a real binary.

    The launcher layer is recognised structurally — a JavaScript entry point
    under ``dist/`` — rather than by install path, so a package installed
    outside ``npm-global``/``pnpm`` (for example under Hermes's own node
    prefix) is identified too.
    """
    try:
        resolved = Path(binary).resolve()
    except (OSError, RuntimeError):
        resolved = Path(binary)
    shim_target = resolve_shim_target(binary)
    entry = Path(shim_target) if shim_target else resolved
    if entry.suffix != ".mjs":
        return None
    return entry.parent.parent


def _cached_release_asset(cache_dir: Path) -> Path | None:
    """Return the newest downloaded firewall binary in a launcher cache, if any."""
    try:
        releases = [p for p in cache_dir.iterdir() if p.is_dir()]
    except OSError:
        return None
    assets: list[Path] = []
    for release in releases:
        try:
            assets.extend(
                f for f in release.iterdir() if f.is_file() and f.name.startswith(_SFW_ASSET_PREFIX)
            )
        except OSError:
            continue
    if not assets:
        return None
    return max(assets, key=lambda f: f.stat().st_mtime)


def _latest_reason(latest: Path) -> str:
    if latest.is_symlink():
        pointed = ""
        with contextlib.suppress(OSError):
            pointed = f" (points at {os.readlink(latest)})"
        return f"{latest} is a dangling symlink{pointed}"
    if latest.exists():
        return f"{latest} exists but is not a usable firewall binary"
    return f"{latest} is missing"


def cache_fault(binary: str) -> SFWCacheFault | None:
    """Report a launcher whose firewall-binary cache cannot start it.

    Returns None for a real binary (no cache layer), for a launcher whose
    ``latest`` link resolves, and for a fresh install whose cache is still
    empty (the launcher downloads on first use).
    """
    root = wrapper_install_root(binary)
    if root is None:
        return None
    cache_dir = root / _SFW_CACHE_DIR
    latest = cache_dir / _SFW_LATEST_LINK
    if _resolves_to_file(latest):
        return None
    cached_asset = _cached_release_asset(cache_dir)
    if cached_asset is None:
        # Nothing downloaded yet: the launcher's normal first-run path.
        return None
    return SFWCacheFault(
        binary=binary,
        reason=_latest_reason(latest),
        cache_dir=str(cache_dir),
        cached_asset=str(cached_asset),
        repair=f"ln -sfn {cached_asset} {latest}",
    )


def _candidate_sfw(candidate: Path) -> str | None:
    """Return an executable, usable candidate, or None."""
    if not candidate.exists() or not os.access(candidate, os.X_OK):
        return None
    target = resolve_shim_target(str(candidate))
    return str(candidate) if target is None or Path(target).exists() else None


def find_sfw(sfw_bin: str) -> str | None:
    """Locate a sfw binary.

    Discovery is intentionally performed on demand instead of being cached
    during manager construction. The plugin manager can outlive changes to
    the process environment, and sfw may be installed after registration.
    """
    # If config points to a specific binary, use it directly.
    if sfw_bin != "sfw":
        return sfw_bin if Path(sfw_bin).exists() else None

    # Default: search PATH and common locations. A candidate that exists but
    # cannot run is skipped in favour of a working install — a shim whose
    # real target is missing, or a launcher whose downloaded firewall binary
    # is unreachable. When nothing usable exists the unusable candidate is
    # still returned, so the failure carries its own repair note instead of
    # hiding behind "sfw is not installed".
    fallback: str | None = None
    path = shutil.which(sfw_bin)
    candidate = _candidate_sfw(Path(path)) if path else None
    if candidate is not None and cache_fault(candidate) is None:
        return candidate
    fallback = candidate
    # Check common locations. A candidate that exists but is a wrapper
    # shim whose real target is missing is skipped, like the one that
    # broke a machine even though ``npm ci`` succeeded.
    for known in known_candidates():
        found = _candidate_sfw(known)
        if found is None:
            continue
        if cache_fault(found) is None:
            return found
        fallback = fallback or found
    return fallback
