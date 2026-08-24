"""Helpers for inspecting GNU Make's resolved database output in tests."""

import re


def _join_continuations(lines, start):
    fragments = [lines[start]]
    index = start + 1
    while (
        fragments[-1].rstrip().endswith("\\")
        and index < len(lines)
        and lines[index].startswith((" ", "\t"))
    ):
        fragments.append(lines[index])
        index += 1
    return " ".join(fragment.strip().removesuffix("\\").strip() for fragment in fragments)


def _rule_start(lines, target):
    prefix = f"{target}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            return index
    return None


def make_database_rule_header(database, target):
    lines = database.splitlines()
    index = _rule_start(lines, target)
    if index is None:
        return None
    return _join_continuations(lines, index)


def make_database_rule(database, target):
    lines = database.splitlines()
    start = _rule_start(lines, target)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and lines[end]:
        end += 1
    return "\n".join(lines[start:end])


def make_database_variable(database, name):
    pattern = re.compile(rf"^{re.escape(name)}\s*(?::=|\?=|\+=|=)\s*(.*)$")
    lines = database.splitlines()
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            lines[index] = match.group(1)
            return _join_continuations(lines, index)
    return None
