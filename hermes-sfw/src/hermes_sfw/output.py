"""sfw output parsing + output/error sanitation."""

from __future__ import annotations

import errno
import re

_MAX_LIST_ENTRIES = 50

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_BLOCKED_KEYWORDS = frozenset({"blocked", "🚫", "🔴"})
_INSTALLED_KEYWORDS = frozenset({"installed", "🟢", "added"})
_ALL_KEYWORDS = _BLOCKED_KEYWORDS | _INSTALLED_KEYWORDS
# Free-text prose commonly wraps sfw keywords ("blocked by firewall",
# "added 5 packages"). The token directly after a keyword is only treated as a
# package name when it is not a count or a prose filler word.
_NON_PACKAGE_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "at",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "package",
        "packages",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)

_ERRNO_MESSAGES: dict[int, str] = {
    errno.EACCES: "Permission denied",
    errno.ENOENT: "No such file or directory",
    errno.EISDIR: "Is a directory",
    errno.ENOTDIR: "Not a directory",
    errno.ENAMETOOLONG: "File name too long",
    errno.ELOOP: "Too many levels of symbolic links",
}


def sanitize_output(text: str, max_len: int = 10_000) -> str:
    """Truncate long output and add a note about total size."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [output truncated, total {len(text)} chars]"


def sanitize_oserror(exc: OSError) -> str:
    """Map common errno values to generic messages."""
    errnum = getattr(exc, "errno", None)
    if errnum is not None and errnum in _ERRNO_MESSAGES:
        return _ERRNO_MESSAGES[errnum]
    return "An internal error occurred"


def truncate_list(items: list[str], limit: int = _MAX_LIST_ENTRIES) -> list[str]:
    """Cap a list to prevent context flooding."""
    if len(items) <= limit:
        return items
    return items[:limit] + [f"... and {len(items) - limit} more"]


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_ESCAPE_RE.sub("", text)


def _package_after_keyword(parts: list[str], i: int) -> str | None:
    """Package name after keyword token i, or None when prose/count/end."""
    # Walk past any additional keyword tokens (e.g. 🔴 blocked)
    j = i + 1
    while j < len(parts) and parts[j].lower().strip(",:;") in _ALL_KEYWORDS:
        j += 1
    if j >= len(parts):
        return None
    candidate = parts[j].strip(",:;")
    # Skip counts ("added 5 packages") and prose fillers
    # ("blocked by firewall") that are not package names.
    if candidate.isdigit() or candidate.lower() in _NON_PACKAGE_TOKENS:
        return None
    return candidate


def parse_output(output: str) -> tuple[list[str], list[str]]:
    """Parse sfw output for blocked and installed packages.

    Uses exact word-boundary matching to avoid false positives from
    substrings like 'blocked' inside package names.

    Handles formats like:
        🔴 blocked malicious-pkg
        blocked: evil-trojan
        🟢 installed express
        added 5 packages

    Returns:
        Tuple of (blocked_packages, installed_packages).
    """
    blocked: list[str] = []
    installed: list[str] = []

    for line in output.splitlines():
        # Strip ANSI escape sequences before parsing
        parts = strip_ansi(line).split()
        if not parts:
            continue

        # Find the keyword token, then take the first non-keyword token after it
        for i, part in enumerate(parts):
            token = part.lower().strip(",:;")
            if token not in _ALL_KEYWORDS or i + 1 >= len(parts):
                continue
            candidate = _package_after_keyword(parts, i)
            if candidate is None:
                break
            if token in _BLOCKED_KEYWORDS:
                blocked.append(candidate)
            else:
                installed.append(candidate)
            break

    # Deduplicate results while preserving order, then cap list size
    blocked = truncate_list(list(dict.fromkeys(blocked)))
    installed = truncate_list(list(dict.fromkeys(installed)))

    return blocked, installed
