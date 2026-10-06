"""Shared pure Make lexical analysis; no graph or native planning dependencies."""

from __future__ import annotations

import re

if __package__:
    from .authority import encoded
    from .budget import MakeProbeError
else:
    from authority import encoded
    from budget import MakeProbeError


IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
LITERAL_NAME = r"\.?[A-Za-z_][A-Za-z0-9_.-]*"
SHORT_REFERENCE_CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_"
LITERAL_NAME_CHARACTERS = SHORT_REFERENCE_CHARACTERS + "0123456789.-"


REFERENCE = re.compile(
    rf"(?<!\$)\$(?:\((?P<paren>{LITERAL_NAME})(?=[:)])"
    rf"|\{{(?P<brace>{LITERAL_NAME})(?=[:}}])|(?P<short>[{SHORT_REFERENCE_CHARACTERS}]))"
)


SCOPED = re.compile(r"(?<!\$)\$(?:\(([@%*+<?^|](?:D|F)?|[0-9])\)|\{([@%*+<?^|](?:D|F)?|[0-9])\}|([@%*+<?^|0-9]))")


CONDITIONAL = re.compile(r"^[ \t]*(?:else[ \t]+)?(?:ifdef|ifndef)[ \t]+([^\r\n]*)")


BUILTIN_FUNCTIONS = frozenset((
    "subst", "patsubst", "strip", "findstring", "filter", "filter-out", "sort",
    "word", "wordlist", "words", "firstword", "lastword", "dir", "notdir", "suffix",
    "basename", "addsuffix", "addprefix", "join", "wildcard", "realpath", "abspath",
    "if", "or", "and", "foreach", "file", "call", "value", "eval",
    "origin", "flavor", "shell", "error", "warning", "info", "guile",
))


ASSIGNMENT = re.compile(
    rf"^\s*(?:(?:export|override|private)\s+)*(?P<name>{IDENTIFIER})\s*"
    r"(?P<operator>\?=|::=|:=|\+=|!=|=)(?P<value>.*)$"
)


TARGET_ASSIGNMENT = re.compile(
    rf"^(?P<target>.*?)\s*:\s*(?:(?:export|override|private)\s+)*"
    rf"(?P<name>{IDENTIFIER})\s*(?P<operator>\?=|::=|:=|\+=|!=|=)(?P<value>.*)$"
)


MODE_ASSIGNMENT, MODE_TARGET_ASSIGNMENT = (
    re.compile(pattern.pattern.replace(
        rf"(?P<name>{IDENTIFIER})", rf"(?P<name>{LITERAL_NAME})",
    ))
    for pattern in (ASSIGNMENT, TARGET_ASSIGNMENT)
)


DEFINE = re.compile(
    rf"^\s*(?:(?:export|override|private)\s+)*define\s+({LITERAL_NAME})"
    r"(?:[ \t]+(?:::?=|\?=|\+=|!=|=))?[ \t]*$"
)


MAKE_SPACE = " \t\r\n\v\f"


def _collapse_make_continuations(text, *, posix=False):
    pieces, cursor = [], 0
    while True:
        end = text.find("\n", cursor)
        if end < 0:
            pieces.append(text[cursor:])
            return "".join(pieces)
        prefix = text[cursor:end]
        count = len(prefix) - len(prefix.rstrip("\\"))
        pieces.append(prefix[:-count] + "\\" * (count // 2) if count else prefix)
        cursor = end + 1
        if count % 2:
            while cursor < len(text) and text[cursor] in " \t":
                cursor += 1
            if not posix:
                while pieces and not pieces[-1].rstrip(" \t"):
                    pieces.pop()
                if pieces:
                    pieces[-1] = pieces[-1].rstrip(" \t")
            pieces.append(" ")
        else:
            pieces.append("\n")


class _UnresolvedName(ValueError):
    pass


def _make_expression_spans(
    line, *, staged=False, require_complete=False, strict_dollars=False, short=False, budget=None,
):
    stack = []
    index = 0
    while index < len(line):
        if budget is not None:
            budget.remaining()
        if line[index:index + 2] == "$$":
            index += 1 if staged else 2
            continue
        if line[index:index + 2] in {"$(", "${"}:
            if budget is not None:
                if len(stack) >= 512:
                    budget.reject("Make expression exceeds the existing reference depth bound")
                budget.charge("cache", 64)
            stack.append((index + 2, ")" if line[index + 1] == "(" else "}"))
            index += 2
            continue
        if stack and line[index] == stack[-1][1]:
            start, _ = stack.pop()
            if start is not None:
                if budget is not None:
                    budget.charge("cache", 64 + 12 * (index - start))
                yield start - 2, index + 1, line[start:index]
        elif stack and line[index] == ("(" if stack[-1][1] == ")" else "{"):
            if budget is not None:
                if len(stack) >= 512:
                    budget.reject("Make expression exceeds the existing reference depth bound")
                budget.charge("cache", 64)
            stack.append((None, stack[-1][1]))
        elif line[index] == "$":
            token = line[index:index + 2]
            if REFERENCE.fullmatch(token) or SCOPED.fullmatch(token):
                if short:
                    yield index, index + 2, token[1:]
                index += 1
            elif staged or strict_dollars and len(token) == 2:
                raise _UnresolvedName("incomplete or unsupported dollar token")
        index += 1
    if (staged or require_complete) and stack:
        raise _UnresolvedName("incomplete dollar-bearing Make expression")


def _literal_metadata(expression):
    for start, stop, body in _make_expression_spans(expression):
        match = re.fullmatch(r"(?:origin|flavor|value)[ \t\r\n\v\f]+(" + LITERAL_NAME + ")", body)
        if match:
            yield start, stop, match[1]


def _make_function(expression):
    text = expression.strip(MAKE_SPACE)
    outer = [body for start, stop, body in _make_expression_spans(text) if start == 0 and stop == len(text)]
    if len(outer) != 1:
        return None
    match = re.fullmatch(r"([A-Za-z_-]+)[ \t]+(.*)", outer[0], re.S)
    if match is None:
        return None
    value = match[2]
    opening, closing = text[1], text[-1]
    arguments, start, depth = [], 0, 0
    for index, character in enumerate(value):
        if character == opening:
            depth += 1
        elif depth and character == closing:
            depth -= 1
        elif character == "," and not depth:
            arguments.append(value[start:index])
            start = index + 1
    if depth:
        raise MakeProbeError("incomplete Make function argument")
    arguments.append(value[start:])
    return match[1], arguments


def _prune_and(expression, resolve=None, budget=None, *, lazy=False):
    """Reference-analysis form only; immutable source text is retained separately."""
    spans = sorted(_make_expression_spans(expression), key=lambda item: (item[0], -item[1]))
    result, previous = [], 0
    changed = False
    for start, stop, body in spans:
        if start < previous:
            continue
        part = expression[start:stop]
        function = _make_function(part)
        replacement = part
        if function is not None and function[0] in ({"and", "or"} if lazy else {"and"}):
            kept = []
            known = True
            for argument in function[1]:
                argument = argument.strip(MAKE_SPACE)
                kept.append(_prune_and(argument, resolve if known else None, budget, lazy=lazy))
                value = (
                    resolve(argument) if resolve is not None and known
                    else argument if "$" not in argument else None
                )
                if value is None:
                    known = False
                if known and ((function[0] == "and" and value == "") or (function[0] == "or" and value != "")):
                    break
            replacement = part[:2] + function[0] + " " + ",".join(kept) + part[-1]
        elif lazy and function is not None and function[0] == "if" and len(function[1]) in {2, 3}:
            arguments = function[1]
            condition = arguments[0].strip(MAKE_SPACE)
            value = resolve(condition) if resolve is not None else condition if "$" not in condition else None
            if value is not None:
                kept = [_prune_and(condition, resolve, budget, lazy=True), "", ""]
                selected = 1 if value else 2
                if selected < len(arguments):
                    kept[selected] = _prune_and(arguments[selected], resolve, budget, lazy=True)
                replacement = part[:2] + "if " + ",".join(kept) + part[-1]
        elif function is not None and function[0] not in {"origin", "flavor", "value"}:
            if lazy and function[0] not in {"foreach", "call", "eval", "guile"}:
                interior = function[0] + " " + ",".join(
                    _prune_and(argument, resolve, budget, lazy=True) for argument in function[1]
                )
            else:
                interior = _prune_and(body, None, budget, lazy=lazy)
            replacement = part[:2] + interior + part[-1]
        result.extend((expression[previous:start], replacement))
        changed |= replacement != part
        previous = stop
    if not changed:
        return expression
    result.append(expression[previous:])
    return _join_make_text(result, budget)


def _join_make_text(parts, budget):
    result = []
    for part in parts:
        if part is None:
            return None
        if budget is not None:
            budget.charge("cache", len(encoded(part)) + 1)
        result.append(part)
    return "".join(result)


def _make_reference_base(body, *, call=False):
    depth, end = 0, len(body)
    for position, character in enumerate(body):
        if character in "({":
            depth += 1
        elif character in ")}":
            depth -= 1
        elif not depth and (character == "," and call or character.isspace() or character == ":"):
            end = position if call or character == ":" else 0
            break
    return body[:end]


def strip_comment(line):
    result, index = "", 0
    while index < len(line):
        character = line[index]
        if character == "$" and index + 1 < len(line):
            end = index + 2
            opening = line[index + 1]
            if opening in "({":
                closing, depth = (")" if opening == "(" else "}"), 1
                while end < len(line) and depth:
                    if line[end] == opening:
                        depth += 1
                    elif line[end] == closing:
                        depth -= 1
                    end += 1
            result += line[index:end]
            index = end
            continue
        if character == "#":
            count = len(result) - len(result.rstrip("\\"))
            if count:
                result = result[:-count] + "\\" * (count // 2)
            if not count % 2:
                break
        result += character
        index += 1
    return result


def completion_declaration(statement):
    """Classify literal global sites before admitting non-site source context."""
    assignment = MODE_ASSIGNMENT.fullmatch(statement)
    if assignment is not None:
        return assignment, None
    header = statement.strip(MAKE_SPACE)
    if re.match(r"^\.SECONDEXPANSION[ \t]*:", header):
        raise MakeProbeError("unsupported completion secondary expansion")
    if re.match(r"^(?:(?:export|override|private)[ \t]+)*define(?:[ \t]|$)", header):
        macro = DEFINE.fullmatch(header)
        if macro is None:
            raise MakeProbeError("unsupported completion define name")
        return None, macro
    if re.match(
        r"^(?:ifeq|ifneq|ifdef|ifndef|else|endif|include|-include|sinclude|undefine|unexport)(?:[ \t]|$)",
        header,
    ):
        return None, None
    spans = {}
    for start, stop, _ in _make_expression_spans(statement, short=True):
        spans[start] = max(spans.get(start, stop), stop)
    index = 0
    while index < len(statement):
        if index in spans:
            index = spans[index]
            continue
        operator = next((token for token in ("::=", ":=", "?=", "+=", "!=", "=")
                         if statement.startswith(token, index)), None)
        if operator is not None:
            raise MakeProbeError("unsupported completion assignment name")
        if statement[index] in ":;":
            return None, None
        index += 1
    return None, None


def references(line, *, reference_base=_make_reference_base):
    line = _prune_and(line)
    names = set()
    try:
        spans = list(_make_expression_spans(line, short=True, strict_dollars=True, require_complete=True))
    except _UnresolvedName as error:
        raise MakeProbeError("unsupported completion dollar reference") from error
    for start, stop, body in spans:
        name = reference_base(body)
        if re.fullmatch(LITERAL_NAME, name) or SCOPED.fullmatch("$(" + name + ")"):
            names.add(name)
        else:
            function = _make_function(line[start:stop])
            if function is None and "$" in name:
                raise MakeProbeError("computed Make supplier is not bound to native completion")
            if function is None and body:
                raise MakeProbeError("unsupported Make supplier is not bound to native completion")
            if function is not None and function[0] not in BUILTIN_FUNCTIONS:
                raise MakeProbeError("unsupported Make supplier is not bound to native completion")
            if function is not None and function[0] in {"call", "origin", "flavor", "value"}:
                name = (function[1][0] if function[0] == "call" else ",".join(function[1])).strip(MAKE_SPACE)
                if "$" in name:
                    raise MakeProbeError("computed Make supplier is not bound to native completion")
                if name and not re.fullmatch(LITERAL_NAME, name):
                    raise MakeProbeError("unsupported Make supplier is not bound to native completion")
                if function[0] == "call" and name in {"value", "origin", "flavor", "call"}:
                    raise MakeProbeError("unsupported completion forwarded name-taking builtin")
                if name:
                    names.add(name)
    names.update(name for _, _, name in _literal_metadata(line))
    conditional = CONDITIONAL.match(line)
    if conditional:
        name = conditional[1].strip(MAKE_SPACE)
        if "$" in name:
            raise MakeProbeError("computed Make supplier is not bound to native completion")
        if not re.fullmatch(LITERAL_NAME, name):
            raise MakeProbeError("unsupported Make supplier is not bound to native completion")
        names.add(name)
    return names
