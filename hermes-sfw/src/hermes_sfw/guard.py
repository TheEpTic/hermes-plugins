"""Terminal-command guard for the automatic sfw enforcement hook.

Decides, per terminal command: route the package-manager invocation through
sfw, block fail-closed, or leave the command alone.

Routing inserts the resolved sfw binary as a literal prefix in front of every
reachable package-manager invocation. Insertions preserve every other byte of
the command: rebuilding via ``shlex.join()`` re-quoted shell operators into
literal arguments (``npm ci 2>&1 | tail`` became an argv list containing
``|``), silently changing semantics. Operators, quotes, redirections,
pipelines, and newline-separated statements are kept exactly as written.

Dev-loop commands (``cargo test``, ``npm test``, ``pnpm run build``, version
checks) are routed like dependency operations: sfw-free transparently proxies
any package-manager command, so routing preserves behavior while extending
filtering coverage to build-time and script-time downloads.

Only structures this guard can rewrite faithfully are routed: plain commands,
``;``/``&&``/``||``/``|`` chains, redirections, transparent prefixes
(``env``/``timeout``/``nice``/...), and quoted ``bash -c``-style payloads.
Everything else is blocked fail-closed rather than risk rewriting code whose
execution semantics differ: path-qualified managers, managers behind opaque
wrappers (``sudo``/``doas``/``xargs``), command substitutions (``$(...)`` or
backticks), heredocs, and text the scanner cannot parse. Lookup builtins
(``command -v``, ``which``, ``type``) never execute what they name and pass
through, and no text inside quotes is ever rewritten.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .manager import contains_package_manager_command

_MANAGERS = frozenset({"cargo", "npm", "npx", "pip", "pip3", "pnpm", "uv", "uvx", "yarn"})
_TRANSPARENT = frozenset({"command", "env", "exec", "nice", "nohup", "setsid", "time", "timeout"})
_OPAQUE = frozenset({"at", "doas", "script", "sudo", "watch", "xargs"})
_SHELLS = frozenset({"bash", "dash", "fish", "ksh", "sh", "zsh"})
_KEYWORDS = frozenset(
    {"case", "do", "done", "elif", "else", "esac", "fi", "for", "then", "until", "while"}
)
_OPTION_TAKES_VALUE = frozenset(
    {"-C", "-k", "-n", "-s", "-u", "--adjustment", "--chdir", "--kill-after", "--signal", "--unset"}
)
_HINT = re.compile(r"(?<![\w.-])(?:cargo|npm|npx|pip3?|pnpm|uvx?|yarn|python\d*)(?![\w.-])")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON = re.compile(r"python\d*(?:\.\d+)?")
_OPERATORS = ";|&(){}!"
_PASS, _MODIFY, _BLOCK = "pass", "modify", "block"


@dataclass(frozen=True)
class GuardPlan:
    """The terminal hook's decision and, for modify, its exact replacement."""

    action: str
    command: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _Token:
    value: str
    start: int
    end: int
    quoted: bool
    operator: bool = False


def _tokenize(text: str) -> list[_Token]:
    """Tokenize shell words/operators while retaining raw word offsets.

    Newlines are statement separators like ``;``. Quoted spans are consumed
    whole so their contents are never treated as shell syntax.
    """
    tokens: list[_Token] = []
    i = 0
    while i < len(text):
        if text[i] == "\n":
            tokens.append(_Token("\n", i, i + 1, False, True))
            i += 1
            continue
        if text[i].isspace():
            i += 1
            continue
        if text[i] in _OPERATORS:
            end = i + 2 if text[i : i + 2] in {"&&", "||"} else i + 1
            tokens.append(_Token(text[i:end], i, end, False, True))
            i = end
            continue
        start, quoted = i, False
        value: list[str] = []
        while i < len(text) and not text[i].isspace() and text[i] not in _OPERATORS:
            ch = text[i]
            if ch in "'\"":
                quoted = True
                i, value = _consume_quoted(text, i, ch, value)
                continue
            if ch == "\\":
                i, value = _consume_escaped(text, i, value)
                continue
            value.append(ch)
            i += 1
        tokens.append(_Token("".join(value), start, i, quoted))
    return tokens


def _consume_quoted(text: str, i: int, quote: str, value: list[str]) -> tuple[int, list[str]]:
    i += 1
    while i < len(text) and text[i] != quote:
        escaped = text[i] == "\\" and i + 1 < len(text)
        value.append(text[i + 1] if escaped else text[i])
        i += 2 if escaped else 1
    if i == len(text):
        raise ValueError(f"no closing {quote} quote")
    return i + 1, value


def _consume_escaped(text: str, i: int, value: list[str]) -> tuple[int, list[str]]:
    if i + 1 == len(text):
        raise ValueError("trailing backslash")
    value.append(text[i + 1])
    return i + 2, value


def _manager(value: str) -> bool:
    return value in _MANAGERS or Path(value).name in _MANAGERS


def _path_manager(value: str) -> bool:
    return "/" in value and Path(value).name in _MANAGERS


def _prefix(path: str | None, nested: bool) -> str | None:
    if path is None:
        return None
    if nested and re.fullmatch(r"[A-Za-z0-9_./:+-]+", path) is None:
        return None
    return (path if nested else shlex.quote(path)) + " "


def _apply(command: str, inserts: list[tuple[int, str]]) -> str:
    result = command
    for offset, text in sorted(inserts, reverse=True):
        result = result[:offset] + text + result[offset:]
    return result


def _route(
    token: _Token,
    path: str | None,
    nested: bool,
    base: int,
    inserts: list[tuple[int, str]],
    blocks: list[str],
) -> None:
    if path is None:
        blocks.append("sfw binary is unavailable")
        return
    prefix = _prefix(path, nested)
    if prefix is None:
        blocks.append("sfw path cannot be inserted in this command context")
        return
    inserts.append((base + token.start, prefix))


def _wrapper_consume(
    wrapper: str, value: str, duration_seen: bool, skip_value: bool
) -> tuple[bool, bool, bool]:
    """Return (consumed, duration_seen, skip_next_value) for wrapper arguments."""
    if skip_value:
        return True, duration_seen, False
    takes_value = value in _OPTION_TAKES_VALUE
    is_option = value.startswith("-")
    is_assignment = wrapper == "env" and (_ASSIGNMENT.fullmatch(value) is not None)
    is_duration = wrapper == "timeout" and not duration_seen and not is_option
    consumed = takes_value or is_option or is_assignment or is_duration
    return consumed, duration_seen or is_duration, takes_value


def _payload_offset(token: _Token, text: str) -> int | None:
    """Offset of the first char inside a quoted token's span, or None."""
    if not token.quoted:
        return None
    first = text[token.start : token.start + 1]
    return token.start if first in {"'", '"'} else None


def _unsafe_feature(tokens: list[_Token], text: str) -> str | None:
    """Return a block reason when the command uses structures we cannot rewrite."""
    for token in tokens:
        if token.operator and token.value == "(" and False:
            continue
        if token.operator:
            continue
        if text[token.start : token.end].startswith("<<"):
            return "heredocs cannot be routed through sfw"
        if "$(" in token.value or "`" in token.value:
            return "command substitutions ($(...) / backticks) cannot be routed through sfw"
        if token.value.startswith("<<"):
            return "heredocs cannot be routed through sfw"
    return None


def _scan_segment(
    raw: str, tokens: list[_Token], path: str | None, nested: bool, base: int
) -> tuple[list[tuple[int, str]], list[str]]:
    inserts: list[tuple[int, str]] = []
    blocks: list[str] = []
    scan_next = False
    skip_value = False
    mode, wrapper, duration_seen = "start", "", False
    i = 0
    while i < len(tokens):
        token, value = tokens[i], tokens[i].value
        if token.operator:
            mode, wrapper, duration_seen, skip_value, scan_next = "start", "", False, False, False
            i += 1
            continue
        if scan_next:
            scan_next = False
            offset = _payload_offset(token, raw) or token.start
            _nested_scan(
                raw, value, offset - token.start, path, base + token.start, inserts, blocks
            )
            i += 1
            continue
        if mode == "lookup" or mode == "args":
            i += 1
            continue
        keyword = mode == "start" and value in _KEYWORDS
        if keyword:
            i += 1
            continue
        opaque_hit = mode == "opaque" and (
            _manager(value) or contains_package_manager_command(value)
        )
        if opaque_hit:
            blocks.append("package manager is hidden behind an opaque wrapper (sudo/xargs/...)")
            mode = "args"
            i += 1
            continue
        if mode == "opaque":
            i += 1
            continue
        split_string = mode == "wrapper" and wrapper == "env" and value in {"-S", "--split-string"}
        if split_string:
            scan_next = True
            i += 1
            continue
        env_manager = mode == "wrapper" and wrapper == "env" and _manager(value)
        if env_manager:
            _route(token, path, nested, base, inserts, blocks)
            mode, i = "args", i + 1
            continue
        wrapper_state = (
            _wrapper_consume(wrapper, value, duration_seen, skip_value)
            if mode == "wrapper"
            else (False, duration_seen, False)
        )
        if wrapper_state[0]:
            _, duration_seen, skip_value = wrapper_state
            i += 1
            continue
        payload_start = _payload_offset(token, raw) if mode == "shell" else None
        payload_adjacent = i > 0 and tokens[i - 1].value.startswith("-")
        shell_payload = payload_start is not None and (i == 1 or payload_adjacent)
        if shell_payload:
            assert payload_start is not None
            _nested_scan(
                raw, value, payload_start - token.start, path, base + token.start, inserts, blocks
            )
            i += 1
            continue
        shell_word = mode == "shell" and _manager(value)
        if shell_word:
            blocks.append("package manager is hidden inside an unquoted shell payload")
            i += 1
            continue
        if mode == "shell":
            i += 1
            continue
        if token.quoted:
            i += 1
            continue
        if _path_manager(value):
            blocks.append("path-qualified package-manager command is not routable")
            mode, i = "args", i + 1
            continue
        if _manager(value):
            _route(token, path, nested, base, inserts, blocks)
            mode, i = "args", i + 1
            continue
        python_pip = (
            _PYTHON.fullmatch(value)
            and i + 2 < len(tokens)
            and tokens[i + 1].value == "-m"
            and tokens[i + 2].value in {"pip", "pip3"}
        )
        if python_pip:
            _route(token, path, nested, base, inserts, blocks)
            mode, i = "args", i + 3
            continue
        lookup = value == "command" and i + 1 < len(tokens) and tokens[i + 1].value in {"-v", "-V"}
        if lookup:
            mode, i = "lookup", i + 1
            continue
        if value in _OPAQUE:
            mode, i = "opaque", i + 1
            continue
        if value in _SHELLS:
            mode, wrapper, i = "shell", value, i + 1
            continue
        if value in _TRANSPARENT:
            mode, wrapper, duration_seen, i = "wrapper", value, False, i + 1
            continue
        hidden = contains_package_manager_command(value)
        blocks.extend(
            ["package-manager invocation is hidden behind an unsupported wrapper"] if hidden else []
        )
        mode, i = "args", i + 1
    return inserts, blocks


def _nested_scan(
    raw: str,
    value: str,
    offset: int,
    path: str | None,
    base: int,
    inserts: list[tuple[int, str]],
    blocks: list[str],
) -> None:
    try:
        inner = _tokenize(value[offset:])
    except ValueError as exc:
        blocks.append(f"command could not be parsed: {exc}")
        return
    child, child_blocks = _scan_segment(raw, inner, path, True, base + offset)
    inserts.extend(child)
    blocks.extend(child_blocks)


def plan_terminal_guard(command: str, sfw_path: str | None) -> GuardPlan:
    """Plan the guard action for one terminal command."""
    if not isinstance(command, str) or not command.strip() or not _HINT.search(command):
        return GuardPlan(_PASS)
    try:
        tokens = _tokenize(command)
    except ValueError as exc:
        return GuardPlan(_BLOCK, reason=f"command could not be parsed: {exc}")
    unsafe = _unsafe_feature(tokens, command)
    if unsafe and any(
        not token.operator and _HINT.search(command[token.start : token.end]) for token in tokens
    ):
        return GuardPlan(_BLOCK, reason=unsafe)
    inserts, blocks = _scan_segment(command, tokens, sfw_path, False, 0)
    if blocks:
        return GuardPlan(_BLOCK, reason=blocks[0])
    if not inserts:
        return GuardPlan(_PASS)
    if sfw_path is None:
        return GuardPlan(_BLOCK, reason="sfw binary is unavailable")
    return GuardPlan(_MODIFY, _apply(command, inserts))
