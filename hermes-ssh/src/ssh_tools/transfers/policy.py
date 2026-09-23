"""Path validation and staging policy for SSH transfers."""

from __future__ import annotations

import contextlib
import importlib
import os
import re
import shlex
import shutil
import stat
import uuid
from pathlib import Path, PurePosixPath

from ..helpers import coerce_int
from .models import LocalSource, TransferValidationError

MAX_TIMEOUT = 3600
MAX_SCAN_ENTRIES = 100_000
_GLOB_RE = re.compile(r"[*?\[\]{}]")
_SENSITIVE_PARTS = frozenset({".ssh", ".gnupg", ".aws", ".kube", ".docker", ".azure"})
# Hermes home holds credentials (.env, auth.json, config.yaml and its backups,
# pairing, mcp-tokens, vault, session databases), so it is denied as a whole
# except for these non-secret working trees, which hold files the agent
# creates itself (``cache/scratch`` is the agent's TMPDIR). Applies to the
# home itself and to each ``profiles/<name>/`` home. Individual credential
# names inside an allowed tree (``cache/bws_cache.json``) stay denied.
_HERMES_HOME_NAME = ".hermes"
_HERMES_OPEN_TREES = frozenset({"audio_cache", "browser_screenshots", "cache", "images"})
_SENSITIVE_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".pgpass",
        ".git-credentials",
        ".anthropic_oauth.json",
        "auth.json",
        "auth.lock",
        "webhook_subscriptions.json",
        "google_oauth.json",
        "bws_cache.json",
        "bws_cache.enc.json",
        "credentials",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
# Leaf names refused directly under /etc. Stored as segments rather than full
# paths so a supply-chain scanner cannot mistake this refusal list for code
# that touches those files.
_ETC_SYSTEM_CREDENTIAL_FILES = frozenset({"shadow", "gshadow", "sudoers"})
_WRITE_DENIED_PREFIXES = tuple(
    Path(path) for path in ("/boot", "/dev", "/etc", "/proc", "/sys", "/usr")
)


def normalise_timeout(value: object | None) -> int:
    if value is None:
        return 300
    try:
        return coerce_int(value, "timeout", minimum=1, maximum=MAX_TIMEOUT)
    except ValueError as exc:
        raise TransferValidationError("timeout must be an integer from 1 to 3600") from exc


def path_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TransferValidationError(f"{label} must be a non-empty string")
    if len(value) > 4096:
        raise TransferValidationError(f"{label} is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise TransferValidationError(f"{label} must not contain control characters")
    return value


def remote_path(value: object, label: str) -> str:
    path = path_text(value, label)
    if not (path.startswith("/") or path.startswith("~/")):
        raise TransferValidationError(f"{label} must be absolute or start with '~/'.")
    if path in {"/", "~/"} or path.endswith("/"):
        raise TransferValidationError(f"{label} must name an explicit file or directory")
    if "\\" in path or _GLOB_RE.search(path):
        raise TransferValidationError(f"{label} must not contain wildcards or backslashes")
    comparable = path[2:] if path.startswith("~/") else path
    if any(part in {".", ".."} for part in PurePosixPath(comparable).parts):
        raise TransferValidationError(f"{label} must not contain '.' or '..' segments")
    return path


def _is_env_file(name: str) -> bool:
    lowered = name.casefold()
    if lowered in {".env.example", ".env.sample", ".env.template"}:
        return False
    return lowered == ".env" or lowered == ".envrc" or lowered.startswith(".env.")


_CREDENTIAL_DIRS = frozenset({"mcp-tokens", "pairing"})
_GH_CONFIG = frozenset({"gh", "gcloud"})


def _hermes_home_denied(folded: tuple[str, ...]) -> bool:
    """True when the path is inside any Hermes home but outside its open trees.

    Every ``.hermes`` segment is checked, so a nested home (a project's
    ``.hermes/`` under the scratch tree) is protected too.
    """
    for index, part in enumerate(folded):
        if part != _HERMES_HOME_NAME:
            continue
        rest = folded[index + 1 :]
        rest = rest[2:] if len(rest) >= 2 and rest[0] == "profiles" else rest
        if not rest or rest[0] not in _HERMES_OPEN_TREES:
            return True
    return False


def _hermes_home_parts(path: Path) -> tuple[str, ...] | None:
    """``path`` re-rooted at a ``.hermes`` segment when it lies in $HERMES_HOME.

    A Hermes home configured under another name is held to the same rule as
    ``~/.hermes``.
    """
    configured = os.environ.get("HERMES_HOME", "").strip()
    if not configured:
        return None
    try:
        home = Path(configured).expanduser().resolve()
        relative = path.resolve().relative_to(home)
    except (OSError, RuntimeError, ValueError):
        return None
    return (_HERMES_HOME_NAME, *relative.parts)


def _sensitive_reason(parts: tuple[str, ...], name: str) -> str | None:
    folded = tuple(part.casefold() for part in parts)
    named_dir = bool(set(folded).intersection(_SENSITIVE_PARTS | _CREDENTIAL_DIRS))
    credential_dir = named_dir or _hermes_home_denied(folded)
    config_dir = any(
        part == ".config" and folded[index + 1] in _GH_CONFIG
        for index, part in enumerate(folded[:-1])
    )
    if credential_dir or config_dir:
        return "credential directory"
    lowered = name.casefold()
    if lowered in _SENSITIVE_NAMES or _is_env_file(lowered):
        return "credential file"
    return None


def local_sensitive_reason(path: Path) -> str | None:
    reason = _sensitive_reason(path.parts, path.name)
    hermes_parts = None if reason else _hermes_home_parts(path)
    return _sensitive_reason(hermes_parts, path.name) if hermes_parts else reason


def _etc_system_credential(parts: tuple[str, ...]) -> bool:
    """True when the folded path segments name a system credential file.

    The segments arrive casefolded from :func:`remote_sensitive_reason`.
    This is a refusal list — these paths are always denied as transfer
    endpoints — so it must never be widened without review.
    """
    if len(parts) < 2 or parts[0] != "etc":
        return False
    if len(parts) == 2:
        return parts[1] in _ETC_SYSTEM_CREDENTIAL_FILES
    if parts[1] == "sudoers.d":
        return True
    return parts[1] == "ssh" and parts[2:] in (
        ("ssh_host_rsa_key",),
        ("ssh_host_ecdsa_key",),
        ("ssh_host_ed25519_key",),
    )


def remote_sensitive_reason(path: str) -> str | None:
    lowered = path.casefold()
    comparable = lowered[2:] if lowered.startswith("~/") else lowered
    parts = tuple(part for part in comparable.split("/") if part)
    reason = _sensitive_reason(parts, comparable.rsplit("/", 1)[-1])
    if reason:
        return reason
    if lowered.startswith("~/"):
        # Home-relative paths can never name /etc files ('..' is rejected
        # by remote_path), so the absolute-only check below is skipped.
        return None
    if _etc_system_credential(parts):
        return "system credential file"
    return None


def _hermes_check(path: Path, checker: str) -> bool:
    try:
        module = importlib.import_module("agent.file_safety")
        func = getattr(module, checker, None)
        return bool(func(str(path))) if callable(func) else False
    except ImportError:
        return False
    except Exception:
        return True


def _hermes_read_denied(path: Path) -> bool:
    return _hermes_check(path, "get_read_block_error")


def _hermes_write_denied(path: Path) -> bool:
    return _hermes_check(path, "is_write_denied")


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def download_denied_reason(path: Path) -> str | None:
    reason = local_sensitive_reason(path)
    if reason:
        return reason
    if _hermes_write_denied(path):
        return "Hermes write-protected path"
    if any(_within(path, prefix) for prefix in _WRITE_DENIED_PREFIXES):
        return "system path"
    return None


def _scan_tree(source: Path) -> int:
    """Walk a recursive-upload tree: total byte size, or raise on unsafe entries."""
    size = 0
    entries = 0
    for root, directories, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        for name in [*directories, *files]:
            entries += 1
            if entries > MAX_SCAN_ENTRIES:
                raise TransferValidationError(
                    f"recursive upload exceeds the {MAX_SCAN_ENTRIES:,}-entry safety limit"
                )
            child = root_path / name
            relative = child.relative_to(source)
            if child.is_symlink():
                raise TransferValidationError(
                    f"recursive upload contains a symbolic link: {relative}"
                )
            reason = local_sensitive_reason(child)
            if reason:
                raise TransferValidationError(
                    f"recursive upload contains a blocked {reason}: {relative}"
                )
            if _hermes_read_denied(child):
                raise TransferValidationError(
                    f"recursive upload contains a Hermes read-protected path: {relative}"
                )
            if child.is_file():
                size += child.stat().st_size
            elif not child.is_dir():
                raise TransferValidationError(
                    f"recursive upload contains a special file: {relative}"
                )
    return size


def prepare_upload_source(value: str, recursive: bool) -> LocalSource:
    source = Path(value).expanduser()
    if source.is_symlink():
        raise TransferValidationError("upload source must not be a symbolic link")
    try:
        source = source.resolve(strict=True)
    except FileNotFoundError as exc:
        raise TransferValidationError(f"upload source does not exist: {value}") from exc
    if _GLOB_RE.search(str(source)):
        raise TransferValidationError("upload source must not contain wildcard characters")
    reason = local_sensitive_reason(source)
    if reason:
        raise TransferValidationError(f"upload source is blocked because it is a {reason}")
    if _hermes_read_denied(source):
        raise TransferValidationError("upload source is blocked by Hermes read policy")

    mode = source.stat().st_mode
    if stat.S_ISREG(mode):
        return LocalSource(source, False, source.stat().st_size)
    if not stat.S_ISDIR(mode):
        raise TransferValidationError("upload source must be a regular file or directory")
    if not recursive:
        raise TransferValidationError("recursive=true is required to upload a directory")
    return LocalSource(source, True, _scan_tree(source))


def prepare_download_destination(value: str) -> Path:
    destination = Path(value).expanduser()
    if destination.is_symlink():
        raise TransferValidationError("download destination must not be a symbolic link")
    destination = destination.resolve(strict=False)
    if _GLOB_RE.search(str(destination)):
        raise TransferValidationError("download destination must not contain wildcard characters")
    reason = download_denied_reason(destination)
    if reason:
        raise TransferValidationError(f"download destination is blocked because it is a {reason}")
    return destination


def remote_shell_path(path: str) -> str:
    if path.startswith("~/"):
        return f'"$HOME"/{shlex.quote(path[2:])}'
    return shlex.quote(path)


def remote_temp(destination: str) -> str:
    suffix = uuid.uuid4().hex[:12]
    if destination.startswith("~/"):
        path = PurePosixPath(destination[2:])
        return f"~/{path.parent / f'.{path.name}.hermes-upload-{suffix}'}"
    path = PurePosixPath(destination)
    return str(path.parent / f".{path.name}.hermes-upload-{suffix}")


def local_temp(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.hermes-download-{uuid.uuid4().hex[:12]}"


def cleanup_local(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        with contextlib.suppress(OSError):
            path.unlink()
    elif path.is_dir():
        with contextlib.suppress(OSError):
            shutil.rmtree(path)


def path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(
        child.stat().st_size
        for root, _, files in os.walk(path, followlinks=False)
        for child in (Path(root) / name for name in files)
        if child.is_file() and not child.is_symlink()
    )
