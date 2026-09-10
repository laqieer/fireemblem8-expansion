"""Small shared shell-token parser for trusted single-command adapters."""

from __future__ import annotations

import shlex


def bash_line_state(line, state):
    index = 0
    word_start = state == "normal"
    while index < len(line):
        character = line[index]
        if state == "normal":
            if character in " \t":
                word_start = True
            elif character == "#" and word_start:
                break
            elif character == "'":
                state = "single"
                word_start = False
            elif character == '"':
                state = "double"
                word_start = False
            elif character == "\\":
                if index == len(line) - 1:
                    return state, True
                index += 2
                word_start = False
                continue
            elif character in "&|;":
                if character in "&|" and index + 1 < len(line) and line[index + 1] == character:
                    index += 1
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
                    return state, True
                if line[index + 1] in '$`"\\':
                    index += 2
                    continue
        index += 1
    return state, False


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
