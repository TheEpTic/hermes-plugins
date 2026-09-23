"""Tests for hermes-sfw plugin."""

from __future__ import annotations

import json
import subprocess as _sp
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hermes_sfw.handlers import handle_sfw
from hermes_sfw.manager import SFWConfig, SFWManager, SFWResult, _MAX_LIST_ENTRIES


def _call(manager: SFWManager, params: dict[str, Any]) -> dict[str, Any]:
    return json.loads(handle_sfw(manager)(params))


def _script(path: Path, body: str = "#!/bin/bash\nexit 0\n") -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _mgr(bin_path: Path | str, **kw: Any) -> SFWManager:
    return SFWManager(SFWConfig(sfw_bin=str(bin_path), **kw))


# ---------------------------------------------------------------------------
# status / run
# ---------------------------------------------------------------------------


def test_status_installed_and_missing(manager: SFWManager, tmp_path: Path) -> None:
    ok = _call(manager, {"action": "status"})
    assert ok["success"] is True and ok["installed"] is True and ok["version"] is not None
    mgr = _mgr(tmp_path / "nonexistent" / "sfw")
    missing = _call(mgr, {"action": "status"})
    assert missing["installed"] is False and missing["version"] is None


def test_run_echo_verbose_workdir(manager: SFWManager, mock_popen, tmp_path: Path) -> None:
    mock_popen.return_value.communicate.return_value = (b"hello\n", b"")
    result = _call(manager, {"action": "run", "command": "npm install express"})
    assert result["success"] is True and "hello" in result.get("stdout", "")
    _call(
        manager,
        {"action": "run", "command": "npm install express", "verbose": True},
    )
    assert "--verbose" in mock_popen.call_args[0][0]
    _call(
        manager,
        {"action": "run", "command": "npm install express", "workdir": str(tmp_path)},
    )
    assert mock_popen.call_args[1]["cwd"] == str(tmp_path)


def test_run_not_installed(tmp_path: Path) -> None:
    result = _call(_mgr(tmp_path / "nonexistent" / "sfw"), {"action": "run", "command": "echo hi"})
    assert result["success"] is False and "not installed" in result.get("stderr", "").lower()


@pytest.mark.parametrize(
    "params,fragment,where",
    [
        ({"action": "run"}, "command", "error"),
        ({"action": "run", "command": 'echo "unclosed'}, "parse", "stderr"),
        ({"action": "run", "command": "cat /etc/passwd"}, None, "stderr"),
        ({"action": "run", "command": 123}, "string", "error"),
        ({"action": "run", "command": True}, None, "error"),
        ({}, "action", "error"),
        ({"action": "bogus"}, "Unknown action", "error"),
        ({"action": "run", "command": "/usr/bin/pip install foo"}, "path separator", "stderr"),
        ({"action": "run", "command": "../pip install foo"}, None, "stderr"),
        ({"action": "run", "command": "npm install " + "x" * 1100}, "too long", "stderr"),
        ({"action": "run", "command": ""}, "empty", "stderr"),
        ({"action": "run", "command": "npx cowsay hello"}, "not allowed", "stderr"),
        ({"action": "run", "command": "npm install\x00evil"}, None, "stderr"),
    ],
)
def test_run_validation(
    manager: SFWManager, params: dict, fragment: str | None, where: str
) -> None:
    result = _call(manager, params)
    assert result["success"] is False
    if fragment:
        assert fragment.lower() in result.get(where, "").lower()


@pytest.mark.parametrize("command", ["npm install express", "uv pip install flask", "cargo fetch"])
def test_accept_documented_ops(manager: SFWManager, mock_popen, command: str) -> None:
    _call(manager, {"action": "run", "command": command})
    assert mock_popen.called


@pytest.mark.parametrize(
    "command",
    [
        # runners that execute code
        "npm exec sh",
        "npm run postinstall",
        "pnpm dlx cowsay hi",
        "yarn run build",
        "uv run python evil.py",
        "cargo run",
        "rustup run stable sh",
        "rustup toolchain run stable sh",
        # option-argument forms hiding the verb
        "npm --prefix /tmp exec sh",
        "npm --prefix=/tmp run-script x",
        "uv --project /tmp run sh",
        "uv --project=/tmp run sh",
        "cargo --manifest-path /tmp/Cargo.toml run",
        # local/git sources bypass the registry
        "cargo install --path /tmp/evil",
        "cargo install --path=./evil",
        "cargo install --git https://github.com/evil/thing",
        "pip install .",
        "pip install ./local-pkg",
        "pip install ../local-pkg",
        "uv pip install .",
        "npm install ./local-dep",
        "yarn add ../dep",
        "pnpm add ./dep",
        "pip install git+https://github.com/evil/pkg.git",
        "npm install git+https://github.com/evil/pkg.git",
        "npm install file:./local.tgz",
    ],
)
def test_reject_runners_options_and_sources(manager: SFWManager, command: str) -> None:
    result = _call(manager, {"action": "run", "command": command})
    stderr = result.get("stderr", "").lower()
    assert result["success"] is False
    assert "not allowed" in stderr or "registry" in stderr


def test_accept_manifest_values_and_index_urls(manager: SFWManager, mock_popen) -> None:
    _call(manager, {"action": "run", "command": "pip install -r ./requirements.txt"})
    _call(
        manager,
        {"action": "run", "command": "pip install --index-url https://pypi.org/simple requests"},
    )
    assert mock_popen.called


def test_non_string_workdir_rejected(manager: SFWManager) -> None:
    result = json.loads(
        handle_sfw(manager)({"action": "run", "command": "npm install x", "workdir": [1, 2]})
    )
    assert result["success"] is False and "string" in result["error"].lower()


@pytest.mark.parametrize(
    "approval",
    [
        {"approved": False, "message": "BLOCKED: approval required"},
        {"status": "approval_required", "message": "awaiting approval"},
    ],
)
def test_approval_blocks_execution(manager: SFWManager, approval: dict) -> None:
    with (
        patch("hermes_sfw.handlers.sfw.check_approval", return_value=approval),
        patch("hermes_sfw.output.subprocess.Popen") as popen,
    ):
        result = _call(manager, {"action": "run", "command": "npm install left-pad"})
    assert result["success"] is False and approval["message"] in result["error"]
    popen.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,blocked,installed",
    [
        ("", [], []),
        ("🔴 blocked malicious-pkg", ["malicious-pkg"], []),
        ("blocked: evil-trojan", ["evil-trojan"], []),
        ("🚫 forbidden-pkg", ["forbidden-pkg"], []),
        ("🟢 installed express", [], ["express"]),
        ("installed: safe-pkg", [], ["safe-pkg"]),
        ("🔴 blocked evil-pkg\n🟢 installed safe-pkg", ["evil-pkg"], ["safe-pkg"]),
        ("🔴 blocked pkg-a\n🔴 blocked pkg-b", ["pkg-a", "pkg-b"], []),
        ("npm WARN deprecated foo@1.0.0", [], []),
        ("\n\n\n", [], []),
        # prose / counts are not package names
        ("Something is blocked", [], []),
        ("the package was blocked", [], []),
        ("added 1 package", [], []),
        ("added 5 packages", [], []),
        ("the request was blocked by the firewall", [], []),
        ("Installing blocked-utils successfully", [], []),
        ("Removed added-package from cache", [], []),
        # ansi-wrapped forms
        ("\x1b[31m🔴 blocked malicious-pkg\x1b[0m", ["malicious-pkg"], []),
        ("\x1b[32m🟢 installed express\x1b[0m", [], ["express"]),
        ("\x1b[1mblocked\x1b[0m evil-pkg", ["evil-pkg"], []),
    ],
)
def test_parse_output(line: str, blocked: list, installed: list) -> None:
    got_blocked, got_installed = SFWManager._parse_output(line)
    assert got_blocked == blocked and got_installed == installed


@pytest.mark.parametrize("keyword", ["blocked", "installed"])
def test_parse_output_list_capped(keyword: str) -> None:
    emoji = "🔴" if keyword == "blocked" else "🟢"
    output = "\n".join(f"{emoji} {keyword} pkg-{i}" for i in range(100))
    got = SFWManager._parse_output(output)[0 if keyword == "blocked" else 1]
    assert len(got) == _MAX_LIST_ENTRIES + 1
    assert "and 50 more" in got[-1]
    short = SFWManager._parse_output("\n".join(f"🔴 blocked pkg-{i}" for i in range(10)))[0]
    assert len(short) == 10


# ---------------------------------------------------------------------------
# timeout / oserror
# ---------------------------------------------------------------------------


def test_timeout_and_process_group_kill(tmp_path: Path) -> None:
    sfw_bin = _script(tmp_path / "sfw", "#!/bin/bash\nsleep 100\n")
    result = _mgr(sfw_bin, timeout=1).run_command("npm install express")
    assert result.success is False and result.exit_code == -1
    assert "timed out" in result.stderr.lower()
    # child surviving the leader must still be reaped
    rogue = _script(
        tmp_path / "rogue",
        "#!/bin/bash\nsleep 100 &\nchild=$!\ntrap '' TERM\nsleep 0.2\nexit 0\n",
    )
    assert _mgr(rogue, timeout=1).run_command("npm install express").exit_code == -1
    leftover = _sp.run(["pgrep", "-f", "sleep 100"], capture_output=True, text=True, timeout=5)
    assert leftover.returncode != 0 or "sleep 100" not in leftover.stdout


def test_directory_or_non_executable_override_is_not_installed(tmp_path: Path) -> None:
    as_dir = tmp_path / "sfw"
    as_dir.mkdir()
    not_exec = tmp_path / "sfw-noexec"
    not_exec.write_text("#!/bin/sh\n", encoding="utf-8")
    for override in (as_dir, not_exec):
        mgr = _mgr(override)
        assert mgr.sfw_path is None and mgr.is_installed() is False
        result = mgr.run_command("npm install express")
        assert result.success is False and "not installed" in result.stderr


def test_oserror_from_exec_is_sanitized(tmp_path: Path) -> None:
    from hermes_sfw.output import run_sfw

    result = run_sfw([str(tmp_path)], "npm install express", 5, None)
    assert result.success is False and result.exit_code == -1


# ---------------------------------------------------------------------------
# SFWResult.to_dict
# ---------------------------------------------------------------------------


def test_result_to_dict_omits_empty() -> None:
    full = SFWResult(
        success=True,
        command="npm install",
        stdout="ok",
        stderr="",
        exit_code=0,
        blocked=["evil"],
        installed=["safe"],
    ).to_dict()
    assert full["blocked"] == ["evil"] and full["installed"] == ["safe"]
    assert "stderr" not in full
    bare = SFWResult(success=True, command="echo hi", stdout="hi", stderr="", exit_code=0).to_dict()
    assert "blocked" not in bare and "installed" not in bare


# ---------------------------------------------------------------------------
# binary discovery / version / workdir
# ---------------------------------------------------------------------------


def _shim_home(tmp_path: Path, rel: str = ".local/share/pnpm/sfw") -> Path:
    sfw_bin = tmp_path / rel
    sfw_bin.parent.mkdir(parents=True, exist_ok=True)
    return _script(sfw_bin)


def test_find_sfw_custom_and_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = _script(tmp_path / "sfw")
    mgr = _mgr(real)
    assert mgr.is_installed() and mgr.sfw_path == str(real)
    missing = _mgr(tmp_path / "nope")
    assert not missing.is_installed() and missing.sfw_path is None
    with patch("hermes_sfw.resolve.shutil.which", return_value=str(real)):
        assert _mgr("sfw").is_installed()
    # pnpm root shim found via HOME
    shim = _shim_home(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    with patch("hermes_sfw.resolve.shutil.which", return_value=None):
        mgr2 = _mgr("sfw")
        assert mgr2.is_installed() and mgr2.sfw_path == str(shim)


def test_default_search_not_found() -> None:
    with patch("hermes_sfw.resolve.shutil.which", return_value=None):
        with patch("hermes_sfw.resolve.Path") as MockPath:
            instance = MockPath.return_value
            instance.__truediv__ = lambda self, x: instance
            instance.exists.return_value = False
            MockPath.home.return_value = instance
            MockPath.side_effect = lambda *a, **kw: instance
            assert not _mgr("sfw").is_installed()


def test_manager_sees_binary_installed_after_construction(tmp_path: Path) -> None:
    """All manager ops must see a binary installed after construction."""
    sfw_bin = tmp_path / "sfw"
    mgr = _mgr(sfw_bin, timeout=5)
    assert not mgr.is_installed()
    _script(
        sfw_bin,
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        '  printf "Socket Firewall Free, version 1.15.0\\n"\n'
        "else\n"
        '  printf "🟢 installed express\\n"\n'
        "fi\n",
    )
    assert mgr.is_installed()
    assert mgr.get_version() == "Socket Firewall Free, version 1.15.0"
    result = mgr.run_command("npm install express")
    assert result.success and result.installed == ["express"]


def test_get_version_edges(tmp_path: Path, manager: SFWManager) -> None:
    assert _mgr("/nonexistent/sfw").get_version() is None
    as_dir = tmp_path / "sfw_dir"
    as_dir.mkdir()
    assert _mgr(as_dir).get_version() is None
    assert isinstance(manager.get_version(), str)


def test_workdir_symlink_loop_rejected(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.symlink_to(b)
    b.symlink_to(a)
    result = _mgr(_script(tmp_path / "sfw")).run_command("npm install express", workdir=str(a))
    assert result.success is False
    assert "invalid" in result.stderr.lower() or "working directory" in result.stderr.lower()


def test_workdir_tilde_and_system_prefix(tmp_path: Path, manager: SFWManager, mock_popen) -> None:
    _call(manager, {"action": "run", "command": "npm install express", "workdir": "~"})
    assert mock_popen.called
    for workdir in ("/", "/etc", "/usr", "/usr/local", "/boot", "/proc", "/sys", "/dev"):
        result = _call(
            manager, {"action": "run", "command": "npm install express", "workdir": workdir}
        )
        assert result["success"] is False
        assert "system directory" in result.get("stderr", "").lower()


def test_binary_output_no_crash(tmp_path: Path) -> None:
    sfw_bin = tmp_path / "sfw"
    sfw_bin.write_bytes(b"#!/bin/bash\nprintf '\\x80\\x81\\x82\\xff\\xfe'\n")
    sfw_bin.chmod(0o755)
    assert isinstance(_mgr(sfw_bin).run_command("npm install express").stdout, str)


# ---------------------------------------------------------------------------
# direct terminal guard wiring
# ---------------------------------------------------------------------------


def _guard(command: str):
    from hermes_sfw import _guard_direct_dependency_operation

    return _guard_direct_dependency_operation("terminal", {"command": command})


def test_guard_rewrites_installs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hermes_sfw

    sfw_bin = _script(tmp_path / "sfw", "#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(hermes_sfw, "_manager", _mgr(sfw_bin))
    result = _guard("npm install express")
    assert result is not None and result["action"] == "modify"
    assert result["args"]["command"].endswith(" npm install express")
    assert str(sfw_bin) in result["args"]["command"]


def test_guard_routes_dev_commands_and_newlines(manager: SFWManager, monkeypatch) -> None:
    import hermes_sfw

    monkeypatch.setattr(hermes_sfw, "_manager", manager)
    build = _guard("npm run build")
    assert build is not None and build["action"] == "modify"
    assert build["args"]["command"].endswith(" npm run build")
    multi = _guard("cargo test\necho after")
    assert multi is not None and multi["args"]["command"].endswith(" cargo test\necho after")


def test_guard_ignores_blocks_and_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_sfw import _guard_direct_dependency_operation

    assert _guard("git status") is None
    assert _guard_direct_dependency_operation("read_file", {"command": "npm install x"}) is None
    for command in ("xargs npm test", "cat <<EOF\nnpm test\nEOF"):
        result = _guard(command)
        assert result is not None and result["action"] == "block"
    monkeypatch.setenv("HERMES_SFW_ENFORCE_DIRECT", "off")
    assert _guard("pip install requests") is None
