"""Terminal guard coverage: every reachable manager is routed or blocked.

Each case is also executed under real bash with fake binaries: a fake
``sfw`` prints ``SFW`` before exec'ing its argv, fake managers print ``RAW``.
A routed command must reach the manager only through sfw, which proves the
rewrite is both applied and semantically faithful.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import hermes_sfw
from hermes_sfw import _guard_direct_dependency_operation, register
from hermes_sfw.guard import contains_package_manager_command, plan_terminal_guard
from hermes_sfw.manager import SFWConfig, SFWManager

SFW = "/opt/sfw"

ROUTED = [
    "pip install x",
    "FOO=1 pip install x",
    "A=1 B='x y' npm ci",
    "env FOO=1 pip install x",
    "env -i PATH=/bin npm ci",
    "env -- pip install x",
    "stdbuf -oL pip install x",
    "ionice -c3 pip install x",
    "ionice -c 3 nice -n 5 timeout 30 pip install x",
    "exec -a foo pip install x",
    "time pip install x",
    "command pip install x",
    "X=1 Y=2 env Z=3 nohup pip install x",
    "'pip' install x",
    '"pip" install x',
    "p''ip install x",
    "\\pip install x",
    "echo 'a\\' ; pip install x #'",
    "pip3.13 install x",
    "python -I -m pip install x",
    "sh -c 'pip install x'",
    'bash -c "pip install x"',
    "sh -c 'pip install x' extra0",
    "bash -lc 'cd /tmp && pip install x'",
    "bash -o pipefail -c 'npm ci | cat'",
    "env -S 'pip install x'",
    "npx cowsay hi",
    "echo $(pip install x)",
    "echo ${#HOME}; pip install x",
    "</dev/null pip install x",
    "2>/dev/null pip install x",
    "pip install x 2>&1 | cat",
    "pip install x &>/dev/stdout",
    "(cd /tmp && npm ci)",
    "{ pip install x; }",
    "if true; then npm ci; fi",
    "pip install x\nnpm install y",
    "true && pip install x || false",
    "! pip install x",
    "a=(1 2); pip install x",
    "python3 -mpip install x",
    "python -Impip install x",
    "python -W ignore -m pip install x",
    "python3 -X dev -m pip install x",
    'python -c \'import runpy; runpy.run_module("pip", run_name="__main__")\' install x',
    "source /dev/null && pip install x",
    "python3 -c 'import pip'",
]

BLOCKED = [
    "eval 'pip install x'",
    'bash -c "echo \\"q\\" && pip install x"',
    'echo "$(pip install x)"',
    "echo `pip install x`",
    "PM=npm; $PM install",
    "${PM} install x && echo npm",
    "echo 'npm install' | bash",
    "bash <<< 'pip install x'",
    "sudo npm install",
    "sudo bash -lc 'npm install express'",
    "xargs npm test",
    "find . -name '*.js' -exec npm test \\;",
    "taskset -c 0 pip install x",
    "busybox sh -c 'pip install x'",
    "/usr/bin/npm install express",
    "cat <<EOF\nnpm test\nEOF",
    "for p in npm pip; do $p install; done",
    'npm install "unterminated',
    "shopt -s expand_aliases; alias p=pip; p install x",
    "alias p='pip install'; p x",
    "echo 'pip install x' | sh",
    "sh <<'EOF'\npip install x\nEOF",
]

PASSED = [
    "git status",
    "python - <<'EOF'\nprint('data')\nEOF",
    "cat <<EOF > requirements.txt\nrequests\nEOF",
    "printf 'x `y` z'",
    'printf "see `date`"',
    "command -v pip",
    "which npm",
    "type pip",
    "grep npm package.json",
    "git commit -m 'bump npm deps'",
    "apt install python3-pip",
    "echo hi # uses npm",
    "find . -name npm",
    "cat node_modules/.bin/npm",
    "echo $(date) > out",
    "bash script.sh",
    "python3 -m pytest -q",
    "python3 -mpytest",
    "python3 -m pip_audit",
    "python3 script.py -m pip",
    "python3 -c 'print(1)'",
    "source .venv/bin/activate && pytest -q",
    "alias ll='ls -la'",
]


@pytest.mark.parametrize("command", ROUTED)
def test_reachable_manager_is_routed(command: str) -> None:
    plan = plan_terminal_guard(command, SFW)
    assert plan.action == "modify", plan
    assert plan.command is not None and SFW in plan.command
    assert contains_package_manager_command(command) is True


@pytest.mark.parametrize("command", BLOCKED)
def test_unroutable_manager_is_blocked(command: str) -> None:
    plan = plan_terminal_guard(command, SFW)
    assert plan.action == "block", plan
    assert plan.reason


@pytest.mark.parametrize("command", PASSED)
def test_command_without_reachable_manager_passes(command: str) -> None:
    assert plan_terminal_guard(command, SFW).action == "pass"
    assert contains_package_manager_command(command) is False


def test_shell_payload_is_rewritten_in_place() -> None:
    plan = plan_terminal_guard("bash -lc 'cd /tmp && pip install x' extra0", SFW)
    assert plan.command == f"bash -lc 'cd /tmp && {SFW} pip install x' extra0"


def test_rewrite_preserves_every_other_byte() -> None:
    command = "FOO='a b' npm ci 2>&1 | tail -5 # npm"
    plan = plan_terminal_guard(command, SFW)
    assert plan.command == f"FOO='a b' {SFW} npm ci 2>&1 | tail -5 # npm"


def test_unquotable_sfw_path_blocks_nested_payload() -> None:
    plan = plan_terminal_guard("sh -c 'pip install x'", "/opt/my sfw/sfw")
    assert plan.action == "block"
    top = plan_terminal_guard("pip install x", "/opt/my sfw/sfw")
    assert top.command == "'/opt/my sfw/sfw' pip install x"


def test_missing_sfw_blocks() -> None:
    plan = plan_terminal_guard("pip install x", None)
    assert plan.action == "block" and "unavailable" in (plan.reason or "")


def _fake_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("command", ROUTED)
def test_routed_command_executes_manager_only_through_sfw(tmp_path: Path, command: str) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    _fake_bin(fake, "sfw", '#!/bin/sh\necho "SFW $*"\nexec "$@"\n')
    for name in ("pip", "pip3.13", "npm", "npx", "python"):
        _fake_bin(fake, name, f'#!/bin/sh\necho "RAW {name} $*"\n')
    plan = plan_terminal_guard(command, str(fake / "sfw"))
    assert plan.command is not None
    env = dict(os.environ, PATH=f"{fake}:/usr/bin:/bin")
    out = subprocess.run(
        ["bash", "-c", plan.command],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=tmp_path,
    ).stdout
    marks = [line.split()[0] for line in out.splitlines() if line.startswith(("SFW", "RAW"))]
    assert marks and marks[0] == "SFW", out


# ---------------------------------------------------------------------------
# hook wiring + lifecycle
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self) -> None:
        self.tools: list[str] = []
        self.hooks: list[str] = []

    def register_tool(self, name: str, **kwargs: Any) -> None:
        self.tools.append(name)

    def register_hook(self, name: str, callback: Any) -> None:
        self.hooks.append(name)


def test_register_is_repeatable_after_host_unload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hermes_sfw, "_manager", None)
    first, second = _Ctx(), _Ctx()
    register(first)
    register(second)
    assert first.tools == second.tools == ["sfw"]
    assert first.hooks == second.hooks == ["pre_tool_call", "transform_terminal_output"]


def test_hook_uses_the_registered_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sfw_bin = tmp_path / "sfw"
    _fake_bin(tmp_path, "sfw", "#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(hermes_sfw, "_manager", SFWManager(SFWConfig(sfw_bin=str(sfw_bin))))
    result = _guard_direct_dependency_operation("terminal", {"command": "FOO=1 pip install x"})
    assert result == {
        "action": "modify",
        "args": {"command": f"FOO=1 {sfw_bin} pip install x"},
    }
