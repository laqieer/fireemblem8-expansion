"""Small shared shell-token parser for trusted single-command adapters."""

from __future__ import annotations

import shlex
from typing import NamedTuple


class BashToken(NamedTuple):
    value: str
    operator: bool


def _scan_bash_line(line, state, *, operators=None, word_start=None):
    index = 0
    if word_start is None:
        word_start = state == "normal"
    while index < len(line):
        character = line[index]
        if state == "normal":
            if character in " \t":
                word_start = True
            elif character == "#" and word_start:
                return state, False, index, word_start
            elif character == "'":
                state = "single"
                word_start = False
            elif character == '"':
                state = "double"
                word_start = False
            elif character == "\\":
                if index == len(line) - 1:
                    return state, True, len(line), word_start
                index += 2
                word_start = False
                continue
            elif character in ";&|<>()":
                start = index
                while index + 1 < len(line) and line[index + 1] in ";&|<>()":
                    index += 1
                if operators is not None:
                    operators.append((start, index + 1))
                word_start = True
            else:
                word_start = False
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
    """Keep operators distinct while shlex decodes the intervening words."""
    if "\0" in command:
        raise ValueError("shell command contains an invalid NUL byte")
    operators = []
    state, continued, end, _ = _scan_bash_line(command, "normal", operators=operators)
    if state != "normal" or continued:
        raise ValueError("shell command is not a complete logical line")
    tokens = []
    previous = 0
    for start, stop in operators:
        tokens.extend(BashToken(word, False) for word in _split_bash_words(command[previous:start]))
        tokens.append(BashToken(command[start:stop], True))
        previous = stop
    tokens.extend(BashToken(word, False) for word in _split_bash_words(command[previous:end]))
    return tuple(tokens)


def normalize_bash_script_commands(script, label):
    state = "normal"
    word_start = True
    current = ""
    parsed = []
    lines = script.split("\n")
    for index, line in enumerate(lines):
        has_lf = index < len(lines) - 1
        current += line
        state, continued, _, word_start = _scan_bash_line(line, state, word_start=word_start)
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
    parsed = []
    for command in normalize_bash_script_commands(script, label):
        words = _split_bash_words(command)
        if not words:
            raise ValueError(f"{label} run command is empty")
        parsed.append(words)
    return tuple(parsed)
