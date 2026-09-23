"""Terminal-command guard for the automatic sfw enforcement hook.

Decides, per terminal command: route the package-manager invocation through
sfw, block fail-closed, or leave the command alone.

Routing inserts the resolved sfw binary as a literal prefix in front of every
reachable package-manager invocation. Insertions preserve every other byte of
the command: operators, quotes, redirections, pipelines, comments and
newline-separated statements are kept exactly as written.

Dev-loop commands (``cargo test``, ``npm test``, ``pnpm run build``, version
checks) are routed like dependency operations: sfw-free transparently proxies
any command, so routing preserves behavior while extending filtering coverage
to build-time and script-time downloads. Runners (``npx``, ``pnpx``, ``uvx``)
are routed the same way.

A manager is found wherever the shell would execute it:

- the command word of every ``;``/``&&``/``||``/``|``/``&``/newline/subshell
  statement, after leading ``NAME=value`` assignments and shell keywords;
- behind transparent wrappers (``env``, ``timeout``, ``nice``, ``ionice``,
  ``stdbuf``, ``nohup``, ``setsid``, ``time``, ``exec``, ``command``), with
  their options consumed;
- ``python -m pip`` and versioned ``pip3.12``-style names;
- inside the quoted payload of ``bash -c``/``sh -lc``/``env -S``, rewritten
  in place when the payload is a single plain quoted string;
- regardless of quoting or escaping in the command word (``'pip'``, ``\\pip``).

Everything that cannot be rewritten faithfully is blocked fail-closed when a
manager name appears in the command: path-qualified managers, opaque wrappers
(``sudo``, ``xargs``, ``eval``, ``find -exec`` ...), dynamic command words
(``$PM install``), backtick or double-quoted ``$(...)`` substitutions,
heredocs/here-strings, shells reading commands from stdin, payloads the
scanner cannot map, and text it cannot parse. Commands without a manager name
pass untouched, so heredocs and substitutions elsewhere are unaffected.
Lookup builtins (``command -v``, ``which``, ``type``) never execute what they
name and pass through, and no text inside quotes is rewritten except a mapped
shell payload.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

# Command names routed through sfw (sfw-free proxies any command).
_MANAGER = re.compile(r"(?:cargo|npm|npx|pnpm|pnpx|uv|uvx|yarn|pip(?:\d+(?:\.\d+)*)?)")
# Cheap pre-filter: a manager name somewhere in the text, standalone or glued
# to a python short-flag cluster (``-mpip``, ``-Impip``).
_HINT = re.compile(
    r"(?:(?<![\w.-])|(?<=\s-[A-Za-z]m)|(?<=\s-m)|(?<=\s-[A-Za-z]{2}m))"
    r"(?:cargo|npm|npx|pnpm|pnpx|uvx?|yarn|pip(?:\d+(?:\.\d+)*)?)(?![\w-])"
)
# CPython, the Windows/Unix ``py`` launcher, and PyPy all take ``-m pip``.
_PYTHON = re.compile(r"(?:python|pypy)\d*(?:\.\d+)?|py")
_PYTHON_VALUE_FLAGS = frozenset("WXmc")  # CPython short options that take a value
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[^]]*\])?\+?=")
_OPERATORS = ";|&(){}!"
_KEYWORDS = frozenset(
    {
        "case",
        "do",
        "done",
        "elif",
        "else",
        "esac",
        "fi",
        "for",
        "if",
        "in",
        "then",
        "until",
        "while",
    }
)
_SHELLS = frozenset({"ash", "bash", "dash", "fish", "ksh", "mksh", "sh", "zsh"})
_SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
_OPAQUE = frozenset(
    {
        "at",
        "batch",
        "busybox",
        "chroot",
        "chrt",
        "cpulimit",
        "doas",
        "eval",
        "fakeroot",
        "find",
        "firejail",
        "flock",
        "nsenter",
        "numactl",
        "parallel",
        "prlimit",
        "proxychains",
        "proxychains4",
        "runuser",
        "script",
        "setpriv",
        "strace",
        "su",
        "sudo",
        "systemd-run",
        "taskset",
        "torsocks",
        "unbuffer",
        "unshare",
        "watch",
        "xargs",
    }
)
# Transparent wrappers: they exec the next non-option word with its argv.
# Values: options that consume the following word.
_TRANSPARENT: dict[str, frozenset[str]] = {
    "command": frozenset(),
    "env": frozenset({"-u", "--unset", "-C", "--chdir"}),
    "exec": frozenset({"-a"}),
    "ionice": frozenset({"-c", "--class", "-n", "--classdata"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "nohup": frozenset(),
    "setsid": frozenset(),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
}
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
# Redirection words (``>f``, ``2>>f``, ``&>f``, ``2>&1``); a bare operator
# form (``>``, ``2>``) takes the next word as its target.
_REDIRECT = re.compile(r"\d*(?:&>>?|[<>]&|<>|>>|>\||[<>])")
_SAFE_NESTED_PATH = re.compile(r"[A-Za-z0-9_./:+-]+")
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
    quoted: bool = False
    operator: bool = False
    dynamic: bool = False  # unquoted or double-quoted ``$``
    substitution: str | None = None  # block reason for an unroutable substitution
    heredoc: bool = False


@dataclass
class _Scan:
    """Mutable scan state shared across nested payloads."""

    sfw_path: str | None
    inserts: list[tuple[int, str]] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)


class _Word:
    """Accumulates one shell word while tracking the syntax that matters."""

    def __init__(self, text: str, start: int) -> None:
        self.text = text
        self.start = start
        self.value: list[str] = []
        self.quoted = False
        self.dynamic = False
        self.substitution: str | None = None
        self.heredoc = False

    def note_live(self, i: int, in_double: bool) -> None:
        """Record ``$``/backtick/``$(`` at ``i`` outside single quotes."""
        ch = self.text[i]
        self.dynamic = self.dynamic or ch == "$"
        backtick = ch == "`"
        quoted_subst = in_double and self.text.startswith("$(", i)
        reason = "command substitutions (backticks or quoted $(...)) cannot be routed"
        self.substitution = self.substitution or (reason if backtick or quoted_subst else None)

    def token(self, end: int) -> _Token:
        return _Token(
            "".join(self.value),
            self.start,
            end,
            quoted=self.quoted,
            dynamic=self.dynamic,
            substitution=self.substitution,
            heredoc=self.heredoc,
        )


def _consume_single(text: str, i: int, word: _Word) -> int:
    end = text.find("'", i + 1)
    if end < 0:
        raise ValueError("no closing ' quote")
    word.quoted = True
    word.value.append(text[i + 1 : end])
    return end + 1


def _consume_double(text: str, i: int, word: _Word) -> int:
    word.quoted = True
    i += 1
    while i < len(text) and text[i] != '"':
        escaped = text[i] == "\\" and i + 1 < len(text) and text[i + 1] in '$`"\\\n'
        word.note_live(i, in_double=True)
        word.value.append(text[i + 1] if escaped else text[i])
        i += 2 if escaped else 1
    if i >= len(text):
        raise ValueError('no closing " quote')
    return i + 1


def _consume_escape(text: str, i: int, word: _Word) -> int:
    if i + 1 >= len(text):
        raise ValueError("trailing backslash")
    word.value.append("" if text[i + 1] == "\n" else text[i + 1])
    return i + 2


def _consume_braced(text: str, i: int, word: _Word) -> int:
    """``${...}`` parameter expansion: one word, never operators/comments."""
    end = text.find("}", i + 2)
    if end < 0:
        raise ValueError("no closing } in ${...}")
    word.dynamic = True
    word.value.append(text[i : end + 1])
    return end + 1


def _redirect_ampersand(text: str, i: int) -> bool:
    """``&`` that belongs to a redirection (``2>&1``, ``&>f``), not ``&``/``&&``."""
    after_arrow = i > 0 and text[i - 1] in "<>"
    before_arrow = text.startswith("&>", i)
    return after_arrow or before_arrow


def _word_char(text: str, i: int) -> bool:
    ch = text[i]
    return not ch.isspace() and (ch not in _OPERATORS or ch == "&" and _redirect_ampersand(text, i))


def _read_word(text: str, i: int) -> tuple[_Token, int]:
    word = _Word(text, i)
    while i < len(text) and _word_char(text, i):
        ch = text[i]
        if text.startswith("${", i):
            i = _consume_braced(text, i, word)
            continue
        if ch == "'":
            i = _consume_single(text, i, word)
            continue
        if ch == '"':
            i = _consume_double(text, i, word)
            continue
        if ch == "\\":
            i = _consume_escape(text, i, word)
            continue
        word.heredoc = word.heredoc or text.startswith("<<", i)
        word.note_live(i, in_double=False)
        word.value.append(ch)
        i += 1
    return word.token(i), i


def _tokenize(text: str) -> list[_Token]:
    """Tokenize shell words/operators while retaining raw word offsets.

    Newlines are statement separators like ``;``. Quoted spans are consumed
    whole so their contents are never treated as shell syntax; ``#`` at the
    start of a word begins a comment that runs to the end of the line.
    """
    tokens: list[_Token] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            tokens.append(_Token("\n", i, i + 1, operator=True))
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        if ch == "#":
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
            continue
        if ch in _OPERATORS and not _word_char(text, i):
            end = i + 2 if text[i : i + 2] in {"&&", "||", ";;"} else i + 1
            tokens.append(_Token(text[i:end], i, end, operator=True))
            i = end
            continue
        token, i = _read_word(text, i)
        tokens.append(token)
    return tokens


def _statements(tokens: list[_Token]) -> list[list[_Token]]:
    statements: list[list[_Token]] = [[]]
    for token in tokens:
        statements.append([]) if token.operator else statements[-1].append(token)
    return [statement for statement in statements if statement]


def _is_manager(name: str) -> bool:
    return _MANAGER.fullmatch(Path(name).name) is not None


def _has_hint(text: str) -> bool:
    """A manager name in ``text``, also when split by quotes/escapes (``p''ip``)."""
    return _HINT.search(re.sub(r"['\"\\]", "", text)) is not None


def contains_package_manager_command(command: str) -> bool:
    """Return True when the guard would route or block ``command``.

    Single source of truth with :func:`plan_terminal_guard`: a command that
    the guard leaves untouched (``git status``, ``command -v pip``) is False.
    """
    return plan_terminal_guard(command, "sfw").action != _PASS


class _Scanner:
    """Walks statements of one text level; nested payloads recurse."""

    def __init__(self, scan: _Scan, text: str, base: int, nested: bool) -> None:
        self.scan = scan
        self.text = text
        self.base = base
        self.nested = nested

    def raw(self, token: _Token) -> str:
        return self.text[token.start : token.end]

    def block(self, reason: str) -> None:
        self.scan.blocks.append(reason)

    def route(self, token: _Token) -> None:
        path = self.scan.sfw_path
        if path is None:
            self.block("sfw binary is unavailable")
            return
        unsafe_nested = self.nested and _SAFE_NESTED_PATH.fullmatch(path) is None
        if unsafe_nested:
            self.block("sfw path cannot be inserted in this command context")
            return
        prefix = path if self.nested else shlex.quote(path)
        self.scan.inserts.append((self.base + token.start, prefix + " "))

    def run(self, tokens: list[_Token]) -> None:
        for statement in _statements(tokens):
            self.command(statement)

    def command(self, words: list[_Token]) -> None:
        i = self.skip_prefix(words, 0)
        while i < len(words):
            step = self.command_word(words, i)
            if step is None:
                return
            i = step

    def skip_prefix(self, words: list[_Token], i: int) -> int:
        """Skip leading keywords, ``NAME=value`` assignments and redirections."""
        while i < len(words):
            raw = self.raw(words[i])
            keyword = not words[i].quoted and raw in _KEYWORDS
            redirect = _REDIRECT.match(raw)
            if redirect is not None:
                i += 1 if redirect.end() < len(raw) else 2
                continue
            if not (keyword or _ASSIGNMENT.match(raw)):
                return i
            i += 1
        return i

    def command_word(self, words: list[_Token], i: int) -> int | None:
        """Handle the command word at ``i``; return the next command index."""
        word, name = words[i], words[i].value
        rest = words[i + 1 :]
        if word.dynamic:
            self.dynamic_command(words)
            return None
        if "/" in name and _is_manager(name):
            self.block("path-qualified package-manager command is not routable")
            return None
        if _is_manager(name):
            self.route(word)
            return None
        if _PYTHON.fullmatch(Path(name).name) and self.python_installs(rest):
            self.route(word)
            return None
        if name == "alias" and any(_has_hint(word.value) for word in rest):
            self.block("an alias naming a package manager cannot be routed through sfw")
            return None
        if name == "command" and rest and rest[0].value in {"-v", "-V"}:
            return None
        if name == "find" and not any(word.value in _FIND_EXEC for word in rest):
            return None
        if name in _OPAQUE:
            self.opaque(rest)
            return None
        if name in _SHELLS:
            self.shell(rest)
            return None
        if name in _TRANSPARENT:
            return self.transparent(name, words, i + 1)
        return None

    def dynamic_command(self, words: list[_Token]) -> None:
        span = " ".join(self.raw(word) for word in words)
        if _has_hint(span) or _has_hint(self.text):
            self.block("package manager may be named by a dynamic command word ($VAR/$(...))")

    @staticmethod
    def python_installs(rest: list[_Token]) -> bool:
        """True for ``python [opts] -m pip`` and ``python -c CODE`` naming a manager.

        Mirrors CPython's option parsing: short flags cluster (``-Im pip``,
        ``-mpip``), ``-W``/``-X`` consume a value, and the first non-option
        argument is the script, after which nothing is an interpreter option.
        """
        values = [word.value for word in rest]
        i = 0
        while i < len(values):
            value = values[i]
            if value == "--" or not value.startswith("-") or value == "-":
                return False
            for at, flag in enumerate(value[1:], start=2):
                if flag not in _PYTHON_VALUE_FLAGS:
                    continue
                argument = value[at:] or (values[i + 1] if i + 1 < len(values) else "")
                if flag == "m":
                    return _MANAGER.fullmatch(argument) is not None
                if flag == "c":
                    return _has_hint(argument)
                i += 0 if value[at:] else 1
                break
            i += 1
        return False

    def opaque(self, rest: list[_Token]) -> None:
        hidden = any(_has_hint(word.value) for word in rest)
        if hidden:
            self.block("package manager is hidden behind an opaque wrapper (sudo/xargs/eval/...)")

    def transparent(self, wrapper: str, words: list[_Token], i: int) -> int | None:
        takes_value = _TRANSPARENT[wrapper]
        duration_needed = wrapper == "timeout"
        while i < len(words):
            value = words[i].value
            if value == "--":
                return i + 1
            if wrapper == "env" and value in {"-S", "--split-string"}:
                self.payload(words[i + 1] if i + 1 < len(words) else None)
                return None
            if wrapper == "env" and value.startswith("--split-string="):
                self.opaque(words[i:])
                return None
            if value in takes_value:
                i += 2
                continue
            if value.startswith("-") and value != "-" or (wrapper == "env" and value == "-"):
                i += 1
                continue
            if wrapper == "env" and _ASSIGNMENT.match(value):
                i += 1
                continue
            if duration_needed:
                duration_needed = False
                i += 1
                continue
            return i
        return None

    def shell(self, rest: list[_Token]) -> None:
        """``sh [-opts] -c PAYLOAD [$0 ...]`` or ``sh SCRIPT`` or stdin."""
        command_mode = False
        i = 0
        while i < len(rest):
            value = rest[i].value
            if value in _SHELL_VALUE_OPTIONS:
                i += 2
                continue
            is_option = value.startswith(("-", "+")) and value not in {"-", "--"}
            command_mode = command_mode or (
                is_option and not value.startswith("--") and "c" in value
            )
            if is_option:
                i += 1
                continue
            i += 1 if value == "--" else 0
            break
        payload = rest[i] if i < len(rest) else None
        if command_mode:
            self.payload(payload)
            return
        if payload is None:
            self.stdin_shell()
            return
        if _is_manager(payload.value):
            self.block("package manager is hidden inside an unquoted shell payload")

    def stdin_shell(self) -> None:
        if _has_hint(self.text):
            self.block("a shell reading commands from stdin cannot be routed through sfw")

    def payload(self, word: _Token | None) -> None:
        """Scan a ``-c``/``-S`` payload, rewriting in place when mappable."""
        if word is None:
            self.stdin_shell()
            return
        raw = self.raw(word)
        quote = raw[:1]
        inner = raw[1:-1]
        single = quote == "'" and len(raw) >= 2 and raw.endswith("'") and "'" not in inner
        plain_double = quote == '"' and len(raw) >= 2 and raw.endswith('"')
        double = plain_double and not any(ch in inner for ch in '"\\$`')
        if not (single or double):
            self.unmappable(word)
            return
        try:
            tokens = _tokenize(word.value)
        except ValueError as exc:
            self.block(f"command could not be parsed: {exc}")
            return
        unsafe = _unsafe_feature(tokens)
        if unsafe and _has_hint(word.value):
            self.block(unsafe)
            return
        _Scanner(self.scan, word.value, self.base + word.start + 1, True).run(tokens)

    def unmappable(self, word: _Token) -> None:
        """Payload that is not one plain quoted string: cannot insert in place."""
        if _has_hint(word.value):
            self.block("shell payload is not a single plain quoted string; sfw cannot be inserted")


def _unsafe_feature(tokens: list[_Token]) -> str | None:
    """Block reason for structures whose execution the scanner cannot follow."""
    for token in tokens:
        if token.heredoc:
            return "heredocs/here-strings cannot be routed through sfw"
        if token.substitution:
            return token.substitution
    return None


def _apply(command: str, inserts: list[tuple[int, str]]) -> str:
    result = command
    for offset, text in sorted(set(inserts), reverse=True):
        result = result[:offset] + text + result[offset:]
    return result


def plan_terminal_guard(command: str, sfw_path: str | None) -> GuardPlan:
    """Plan the guard action for one terminal command."""
    if not isinstance(command, str) or not command.strip() or not _has_hint(command):
        return GuardPlan(_PASS)
    try:
        tokens = _tokenize(command)
    except ValueError as exc:
        return GuardPlan(_BLOCK, reason=f"command could not be parsed: {exc}")
    unsafe = _unsafe_feature(tokens)
    if unsafe:
        return GuardPlan(_BLOCK, reason=unsafe)
    scan = _Scan(sfw_path)
    _Scanner(scan, command, 0, False).run(tokens)
    if scan.blocks:
        return GuardPlan(_BLOCK, reason=scan.blocks[0])
    if not scan.inserts:
        return GuardPlan(_PASS)
    return GuardPlan(_MODIFY, _apply(command, scan.inserts))
