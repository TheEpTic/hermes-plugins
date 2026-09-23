"""Self-diagnosis of the sfw install: walks PATH + known locations + override."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

from .models import SFWDiagnosis
from .resolve import classify_binary, resolve_shim_target


def _build_diagnosis(
    *,
    healthy: bool,
    binary: str | None,
    binary_kind: str | None,
    version: str | None,
    why: str,
    checked: list[str],
    errors: list[str],
    target: str | None = None,
) -> SFWDiagnosis:
    return SFWDiagnosis(
        healthy=healthy,
        binary=binary,
        binary_kind=binary_kind,
        version=version,
        why=why,
        target=target,
        checked=checked,
        errors=errors,
    )


def diagnose_override(
    sfw_bin: str,
    checked: list[str],
    errors: list[str],
    binary_at: Callable[[str, str], SFWDiagnosis],
) -> SFWDiagnosis:
    override = Path(sfw_bin)
    checked.append(str(override))
    if not override.exists():
        return _build_diagnosis(
            healthy=False,
            binary=None,
            binary_kind=None,
            version=None,
            why=(
                f"configured sfw_bin override does not exist: {override}. "
                "Reinstall with: npm i -g sfw"
            ),
            checked=checked,
            errors=errors,
        )
    return binary_at(str(override), "the binary exists but its --version query failed")


def diagnose_candidate(
    candidate: Path,
    checked: list[str],
    errors: list[str],
    binary_at: Callable[[str, str], SFWDiagnosis],
) -> SFWDiagnosis | None:
    if not candidate.exists():
        return None
    if not os.access(candidate, os.X_OK):
        errors.append(f"{candidate} exists but is not executable")
        return None
    target = resolve_shim_target(str(candidate))
    if target is not None and not Path(target).exists():
        return _build_diagnosis(
            healthy=False,
            binary=str(candidate),
            binary_kind="npm-shim",
            version=None,
            why=(
                f"shim {candidate} points at missing target {target}. "
                "Reinstall with: npm i -g sfw"
            ),
            target=target,
            checked=checked,
            errors=errors,
        )
    return binary_at(str(candidate), f"binary {candidate} exists but its --version query failed")


def diagnose(
    sfw_bin: str,
    candidates: list[Path],
    version_of: Callable[[str], str | None],
) -> SFWDiagnosis:
    """Self-diagnose the sfw install.

    Walks every known shim/cache location, the PATH lookup and the
    configured ``sfw_bin`` override and reports exactly what is broken:
    no binary found anywhere, a shim that exists but points at a missing
    target, or a binary whose version query fails.

    Returns:
        An :class:`SFWDiagnosis` with ``healthy``, the resolved
        ``binary``/``binary_kind``/``target``, the queried ``version``, a
        human-readable ``why``, the list of ``checked`` locations and any
        discovery ``errors``.
    """
    checked: list[str] = []
    errors: list[str] = []

    def binary_at(binary: str, failure_reason: str) -> SFWDiagnosis:
        version = version_of(binary)
        info = classify_binary(binary)
        return _build_diagnosis(
            healthy=version is not None,
            binary=binary,
            binary_kind=info.binary_kind,
            version=version,
            why="ok" if version is not None else failure_reason,
            target=info.target,
            checked=checked,
            errors=errors,
        )

    if sfw_bin != "sfw":
        return diagnose_override(sfw_bin, checked, errors, binary_at)

    path = shutil.which(sfw_bin)
    if path:
        checked.append(path)
        return binary_at(path, "the binary was found on PATH but its --version query failed")

    for candidate in candidates:
        checked.append(str(candidate))
        candidate_diagnosis = diagnose_candidate(candidate, checked, errors, binary_at)
        if candidate_diagnosis is not None:
            return candidate_diagnosis

    return _build_diagnosis(
        healthy=False,
        binary=None,
        binary_kind=None,
        version=None,
        why=(
            "sfw binary not found: checked PATH and "
            + ", ".join(checked)
            + ". Install with: npm i -g sfw"
        ),
        checked=checked,
        errors=errors,
    )
