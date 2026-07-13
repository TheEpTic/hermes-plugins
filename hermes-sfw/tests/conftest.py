"""Test fixtures for hermes-sfw."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# NOTE: pyproject.toml sets pythonpath=["src"], so the sys.path hack below
# is technically unnecessary.  Keeping it for backward-compat with editors
# or test runners that don't read pyproject.toml.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hermes_sfw.manager import SFWConfig, SFWManager


@pytest.fixture(autouse=True)
def _approved_commands():
    """Unit tests exercise handlers independently of a Hermes runtime."""
    with patch("hermes_sfw.handlers.sfw.check_approval", return_value=None):
        yield


@pytest.fixture
def manager(tmp_path: Path) -> SFWManager:
    """Create an SFWManager with a fake sfw binary for testing."""
    # Create a fake sfw script that passes through to the real command
    sfw_bin = tmp_path / "sfw"
    sfw_bin.write_text(
        "#!/bin/bash\n"
        "# Fake sfw: strip --verbose flag and pass rest to real command\n"
        'args=("$@")\n'
        "real_args=()\n"
        'for arg in "${args[@]}"; do\n'
        '  if [[ "$arg" != "--verbose" ]]; then\n'
        '    real_args+=("$arg")\n'
        "  fi\n"
        "done\n"
        'exec "${real_args[@]}"\n',
        encoding="utf-8",
    )
    sfw_bin.chmod(0o755)

    config = SFWConfig(sfw_bin=str(sfw_bin))
    mgr = SFWManager(config)
    return mgr


@pytest.fixture
def mock_popen():
    """Patch subprocess.Popen so no real processes are launched."""
    with patch("hermes_sfw.manager.subprocess.Popen") as m:
        proc = MagicMock()
        proc.communicate.return_value = (b"", b"")
        proc.returncode = 0
        m.return_value = proc
        yield m


@pytest.fixture
def not_installed_manager(tmp_path: Path) -> SFWManager:
    """Return an SFWManager pointing at a nonexistent binary."""
    config = SFWConfig(sfw_bin=str(tmp_path / "nonexistent" / "sfw"))
    return SFWManager(config)
