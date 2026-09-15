"""Small shared shell-token parser for trusted single-command adapters."""

from __future__ import annotations

import shlex
import re
from typing import NamedTuple


class BashToken(NamedTuple):
    value: str
    operator: bool
    raw: str = ""
    start: int = -1
    end: int = -1
    assignment: bool = False
    io_number: bool = False


_OPERATORS = (
    ";;&", "<<-", "<<<", "&>>", "&&", "||", ";;", ";&", "<<", ">>",
    "<&", ">&", "<>", ">|", "&>", "|&", ";", "&", "|", "<", ">", "(", ")", "\n",
)
_REDIRECTIONS = frozenset(("<", ">", "<<", "<<-", "<<<", ">>", "<&", ">&", "<>", ">|"))


def _scan_bash_line(line, state, *, operators=None, words=None, word_start=None):
    index = 0
    word_begin = None

    def finish_word(end):
        nonlocal word_begin
        if words is not None and word_begin is not None:
            words.append((word_begin, end))
        word_begin = None

    if word_start is None:
        word_start = state == "normal"
    while index < len(line):
        character = line[index]
        if state == "normal":
            if character in " \t":
                finish_word(index)
                word_start = True
            elif character == "#" and word_start:
                finish_word(index)
                return state, False, index, word_start
            elif character in ";&|<>()\n":
                finish_word(index)
                start = index
                operator = next(value for value in _OPERATORS if line.startswith(value, index))
                index += len(operator)
                if operators is not None:
                    operators.append((start, index))
                word_start = True
                continue
            else:
                if word_begin is None:
                    word_begin = index
                if character == "\\" and index == len(line) - 1:
                    return state, True, len(line), word_start
                word_start = False
                if character == "'":
                    state = "single"
                elif character == '"':
                    state = "double"
                elif character == "\\":
                    index += 2
                    continue
        elif state == "single":
            if character == "'":
                state = "normal"
        else:
            if character == '"':
                state = "normal"
            elif character == "\\":
                if index == len(line) - 1:
                    return state, True, len(line), word_start
                if line[index + 1] in '$`"\\':
                    index += 2
                    continue
        index += 1
    finish_word(len(line))
    return state, False, len(line), word_start


def bash_line_state(line, state):
    return _scan_bash_line(line, state)[:2]


def strip_bash_command_comment(command):
    """Remove a real shell comment after logical-line continuation folding."""
    return command[:_scan_bash_line(command, "normal")[2]]


def _split_bash_words(command):
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace = " \t\n"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return tuple(lexer)


def tokenize_bash_command(command):
    """Retain lexical roles and adjacency while shlex only decodes words."""
    if "\0" in command:
        raise ValueError("shell command contains an invalid NUL byte")
    operators, words = [], []
    state, continued, end, _ = _scan_bash_line(command, "normal", operators=operators, words=words)
    if state != "normal" or continued:
        raise ValueError("shell command is not a complete logical line")
    tokens = []
    for start, stop, operator in sorted(
        [(start, stop, True) for start, stop in operators]
        + [(start, stop, False) for start, stop in words]
    ):
        raw = command[start:stop]
        if operator:
            tokens.append(BashToken(raw, True, raw, start, stop))
        else:
            decoded = _split_bash_words(raw)
            if len(decoded) != 1:
                raise ValueError("shell word has an unsupported lexical shape")
            assignment = re.match(r"[A-Za-z_][A-Za-z0-9_]*=", raw) is not None
            tokens.append(BashToken(decoded[0], False, raw, start, stop, assignment))
    for index, token in enumerate(tokens[:-1]):
        following = tokens[index + 1]
        if (
            not token.operator and token.raw.isascii() and token.raw.isdigit()
            and token.end == following.start and following.operator and following.value in _REDIRECTIONS
        ):
            tokens[index] = token._replace(io_number=True)
    return tuple(tokens)


def normalize_bash_script_commands(script, label):
    state = "normal"
    word_start = True
    current = ""
    parsed = []
    lines = script.split("\n")
    for index, line in enumerate(lines):
        has_lf = index < len(lines) - 1
        state, continued, end, word_start = _scan_bash_line(line, state, word_start=word_start)
        current += line[:end]
        if continued:
            if not has_lf:
                raise ValueError(f"{label} has an unsupported bare backslash at EOF")
            current = current[:-1]
            continue
        if state != "normal":
            if not has_lf:
                raise ValueError(f"{label} has unterminated quoting")
            current += "\n"
            continue
        if current.strip(" \t") and not current.lstrip(" \t").startswith("#"):
            parsed.append(current)
        current = ""
        word_start = True
    if current:
        raise ValueError(f"{label} has unterminated quoting or continuation")
    if not parsed:
        raise ValueError(f"{label} run command is empty")
    return tuple(parsed)


def parse_bash_script_commands(script, label):
    """Legacy word projection; syntax consumers must use typed tokens."""
    parsed = []
    for command in normalize_bash_script_commands(script, label):
        words = _split_bash_words(command)
        if not words:
            raise ValueError(f"{label} run command is empty")
        parsed.append(words)
    return tuple(parsed)
