"""Small shared shell-token parser for trusted single-command adapters."""

from __future__ import annotations

import shlex
from typing import NamedTuple


class BashToken(NamedTuple):
    value: str
    operator: bool


def _scan_bash_line(line, state, *, operators=None):
    index = 0
    word_start = state == "normal"
    while index < len(line):
        character = line[index]
        if state == "normal":
            if character in " \t":
                word_start = True
            elif character == "#" and word_start:
                return state, False, index
            elif character == "'":
                state = "single"
                word_start = False
            elif character == '"':
                state = "double"
                word_start = False
            elif character == "\\":
                if index == len(line) - 1:
                    return state, True, len(line)
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
                    return state, True, len(line)
                if line[index + 1] in '$`"\\':
                    index += 2
                    continue
        index += 1
    return state, False, len(line)


def bash_line_state(line, state):
    return _scan_bash_line(line, state)[:2]


def strip_bash_command_comment(command):
    """Remove a real shell comment after logical-line continuation folding."""
    return command[:_scan_bash_line(command, "normal")[2]]


def tokenize_bash_command(command):
    """Keep operators distinct while shlex decodes the intervening words."""
    if "\0" in command:
        raise ValueError("shell command contains an invalid NUL byte")
    operators = []
    state, continued, end = _scan_bash_line(command, "normal", operators=operators)
    if state != "normal" or continued:
        raise ValueError("shell command is not a complete logical line")
    tokens = []
    previous = 0
    for start, stop in operators:
        tokens.extend(BashToken(word, False) for word in shlex.split(command[previous:start]))
        tokens.append(BashToken(command[start:stop], True))
        previous = stop
    tokens.extend(BashToken(word, False) for word in shlex.split(command[previous:end]))
    return tuple(tokens)


def normalize_bash_script_commands(script, label):
    state = "normal"
    current = ""
    parsed = []
    for line in script.splitlines():
        current += line
        state, continued = bash_line_state(line, state)
        if continued:
            current = current[:-1]
            continue
        if state != "normal":
            current += "\n"
            continue
        if current.strip() and not current.lstrip().startswith("#"):
            parsed.append(current)
        current = ""
    if current:
        raise ValueError(f"{label} has unterminated quoting or continuation")
    if not parsed:
        raise ValueError(f"{label} run command is empty")
    return tuple(parsed)


def parse_bash_script_commands(script, label):
    parsed = []
    for command in normalize_bash_script_commands(script, label):
        words = tuple(shlex.split(command))
        if not words:
            raise ValueError(f"{label} run command is empty")
        parsed.append(words)
    return tuple(parsed)
