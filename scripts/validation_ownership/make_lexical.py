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


def _reserve(size, budget, charge):
    if budget is not None:
        budget.charge("cache", size)
    charge(size)


def _make_expression_spans(
    line, *, staged=False, require_complete=False, strict_dollars=False, short=False, budget=None,
    bodies=True, checkpoint=lambda: None, charge=lambda size: None,
):
    stack = []
    index, next_checkpoint = 0, 0
    while index < len(line):
        if index >= next_checkpoint:
            checkpoint()
            next_checkpoint = index + 4096
        if budget is not None:
            budget.remaining()
        if line[index:index + 2] == "$$":
            index += 1 if staged else 2
            continue
        if line[index:index + 2] in {"$(", "${"}:
            if len(stack) >= 512:
                if budget is not None:
                    budget.reject("Make expression exceeds the existing reference depth bound")
                raise MakeProbeError("Make expression exceeds the existing reference depth bound")
            _reserve(64, budget, charge)
            stack.append((index + 2, ")" if line[index + 1] == "(" else "}"))
            index += 2
            continue
        if stack and line[index] == stack[-1][1]:
            start, _ = stack.pop()
            if start is not None:
                _reserve(64 + (12 * (index - start) if bodies else 0), budget, charge)
                yield start - 2, index + 1, line[start:index] if bodies else None
        elif stack and line[index] == ("(" if stack[-1][1] == ")" else "{"):
            if len(stack) >= 512:
                if budget is not None:
                    budget.reject("Make expression exceeds the existing reference depth bound")
                raise MakeProbeError("Make expression exceeds the existing reference depth bound")
            _reserve(64, budget, charge)
            stack.append((None, stack[-1][1]))
        elif line[index] == "$":
            token = line[index:index + 2]
            if REFERENCE.fullmatch(token) or SCOPED.fullmatch(token):
                if short:
                    _reserve(76 if bodies else 64, budget, charge)
                    yield index, index + 2, token[1:] if bodies else None
                index += 1
            elif staged or strict_dollars and len(token) == 2:
                raise _UnresolvedName("incomplete or unsupported dollar token")
        index += 1
    if (staged or require_complete) and stack:
        raise _UnresolvedName("incomplete dollar-bearing Make expression")
    checkpoint()


def _literal_metadata(expression, **limits):
    for start, stop, body in _make_expression_spans(expression, **limits):
        match = re.fullmatch(r"(?:origin|flavor|value)[ \t\r\n\v\f]+(" + LITERAL_NAME + ")", body)
        if match:
            yield start, stop, match[1]


def _make_function(expression, **limits):
    text = expression.strip(MAKE_SPACE)
    outer = [
        (start, stop) for start, stop, _ in _make_expression_spans(text, bodies=False, **limits)
        if start == 0 and stop == len(text)
    ]
    if len(outer) != 1:
        return None
    _reserve(64 + 12 * len(text), limits.get("budget"), limits.get("charge", lambda size: None))
    match = re.fullmatch(r"([A-Za-z_-]+)[ \t]+(.*)", text[2:-1], re.S)
    if match is None:
        return None
    value = match[2]
    opening, closing = text[1], text[-1]
    arguments, start, depth = [], 0, 0
    for index, character in enumerate(value):
        if index % 4096 == 0:
            limits.get("checkpoint", lambda: None)()
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


def _prune_and(
    expression, resolve=None, budget=None, *, lazy=False,
    checkpoint=lambda: None, charge=lambda size: None,
):
    """Reference-analysis form only; immutable source text is retained separately."""
    limits = {"budget": budget, "checkpoint": checkpoint, "charge": charge}
    spans = sorted(
        _make_expression_spans(expression, bodies=False, **limits), key=lambda item: (item[0], -item[1]),
    )
    result, previous = [], 0
    changed = False
    for start, stop, _ in spans:
        checkpoint()
        if start < previous:
            continue
        _reserve(64 + 12 * (stop - start), budget, charge)
        part = expression[start:stop]
        function = _make_function(part, **limits)
        replacement = part
        if function is not None and function[0] in ({"and", "or"} if lazy else {"and"}):
            kept = []
            known = True
            for argument in function[1]:
                argument = argument.strip(MAKE_SPACE)
                kept.append(_prune_and(
                    argument, resolve if known else None, lazy=lazy, **limits,
                ))
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
                kept = [_prune_and(condition, resolve, lazy=True, **limits), "", ""]
                selected = 1 if value else 2
                if selected < len(arguments):
                    kept[selected] = _prune_and(arguments[selected], resolve, lazy=True, **limits)
                replacement = part[:2] + "if " + ",".join(kept) + part[-1]
        elif function is not None and function[0] not in {"origin", "flavor", "value"}:
            if lazy and function[0] not in {"foreach", "call", "eval", "guile"}:
                interior = function[0] + " " + ",".join(
                    _prune_and(argument, resolve, lazy=True, **limits) for argument in function[1]
                )
            else:
                interior = _prune_and(part[2:-1], None, lazy=lazy, **limits)
            replacement = part[:2] + interior + part[-1]
        result.extend((expression[previous:start], replacement))
        changed |= replacement != part
        previous = stop
    if not changed:
        return expression
    result.append(expression[previous:])
    return _join_make_text(result, budget, checkpoint=checkpoint, charge=charge)


def _join_make_text(parts, budget, *, checkpoint=lambda: None, charge=lambda size: None):
    result = []
    for part in parts:
        checkpoint()
        if part is None:
            return None
        _reserve(len(encoded(part)) + 1, budget, charge)
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


def _statement_syntax(line, **limits):
    spans = {}
    for start, stop, _ in _make_expression_spans(line, short=True, bodies=False, **limits):
        spans[start] = max(spans.get(start, stop), stop)
    index = 0
    while index < len(line):
        if index % 4096 == 0:
            limits.get("checkpoint", lambda: None)()
        if index in spans:
            index = spans[index]
            continue
        yield index
        index += 1


def _statement_boundary(line, **limits):
    for index in _statement_syntax(line, **limits):
        if line[index] not in "#:=?+!;":
            continue
        first = index
        while first and line[first - 1] == "\\":
            first -= 1
        if (index - first) % 2:
            continue
        if line[index] == "#":
            break
        if any(line.startswith(token, index) for token in ("::=", ":=", "?=", "+=", "!=", "=")):
            return "assignment", index
        if line[index] in ":;":
            return ("rule" if line[index] == ":" else "semicolon"), index
    return None, None


def strip_comment(line, *, recipe_context=False, **limits):
    if recipe_context and line.startswith("\t"):
        return line
    kind, boundary = _statement_boundary(line, **limits)
    syntax = iter(_statement_syntax(line, **limits))
    position = next(syntax, None)
    result, index, backslashes = [], 0, 0
    while index < len(line):
        if index % 4096 == 0:
            limits.get("checkpoint", lambda: None)()
        in_syntax = position == index
        if in_syntax:
            position = next(syntax, None)
        character = line[index]
        if character == "#" and in_syntax:
            count = backslashes
            if count:
                del result[-count:]
                result.extend("\\" * (count // 2))
            if not count % 2:
                break
        if recipe_context and kind == "rule" and index > boundary and in_syntax:
            escaped = backslashes % 2
            if not escaped:
                if character == ";":
                    return "".join(result) + line[index:]
        result.append(character)
        backslashes = backslashes + 1 if character == "\\" else 0
        index += 1
    return "".join(result)


def completion_declaration(statement, **limits):
    """Classify literal global sites before admitting non-site source context."""
    assignment = MODE_ASSIGNMENT.fullmatch(statement)
    if assignment is not None:
        if "private" in statement[:assignment.start("name")].split():
            raise MakeProbeError("unsupported completion private global")
        if assignment["name"] == ".RECIPEPREFIX":
            raise MakeProbeError("unsupported completion recipe prefix")
        return assignment, None, False
    header = statement.strip(MAKE_SPACE)
    if re.match(r"^(?:(?:export|override|private)[ \t]+)*define(?:[ \t]|$)", header):
        macro = DEFINE.fullmatch(header)
        if macro is None:
            raise MakeProbeError("unsupported completion define name")
        if "private" in header[:macro.start(1)].split():
            raise MakeProbeError("unsupported completion private global")
        if macro[1] == ".RECIPEPREFIX":
            raise MakeProbeError("unsupported completion recipe prefix")
        return None, macro, False
    if re.match(
        r"^(?:ifeq|ifneq|ifdef|ifndef|else|endif|include|-include|sinclude|undefine|unexport)(?:[ \t]|$)",
        header,
    ):
        return None, None, False
    kind, index = _statement_boundary(statement, **limits)
    if kind == "assignment":
        raise MakeProbeError("unsupported completion assignment name")
    if kind == "rule":
        targets = statement[:index]
        if any(character in targets for character in "$*?["):
            raise MakeProbeError("unsupported completion constructed target")
        names = {re.sub(r"^(?:\./)+", "", name) for name in targets.split()}
        if ".SECONDEXPANSION" in names:
            raise MakeProbeError("unsupported completion secondary expansion")
        if ".EXPORT_ALL_VARIABLES" in names:
            raise MakeProbeError("unsupported completion blanket export")
        return None, None, True
    if kind == "semicolon":
        return None, None, False
    if "$" in header:
        function = _make_function(header, **limits)
        if function is None or function[0] not in {"info", "warning", "error"}:
            raise MakeProbeError("unsupported completion expansion-generated declaration")
    return None, None, False


def references(
    line, *, reference_base=_make_reference_base, directives=True,
    checkpoint=lambda: None, charge=lambda size: None,
):
    limits = {"checkpoint": checkpoint, "charge": charge}
    line = _prune_and(line, **limits)
    names = set()

    def spans():
        try:
            yield from _make_expression_spans(
                line, short=True, strict_dollars=True, require_complete=True, **limits,
            )
        except _UnresolvedName as error:
            raise MakeProbeError("unsupported completion dollar reference") from error

    for start, stop, body in spans():
        name = reference_base(body)
        if re.fullmatch(LITERAL_NAME, name) or SCOPED.fullmatch("$(" + name + ")"):
            names.add(name)
        else:
            function = _make_function(line[start:stop], **limits)
            if function is None and "$" in name:
                raise MakeProbeError("computed Make supplier is not bound to native completion")
            if function is None and body:
                raise MakeProbeError("unsupported Make supplier is not bound to native completion")
            if function is not None and function[0] not in BUILTIN_FUNCTIONS:
                raise MakeProbeError("unsupported Make supplier is not bound to native completion")
            if function is not None and function[0] == "eval":
                raise MakeProbeError("unsupported completion evaluated source")
            if function is not None and function[0] in {"call", "origin", "flavor", "value"}:
                name = (function[1][0] if function[0] == "call" else ",".join(function[1])).strip(MAKE_SPACE)
                if "$" in name:
                    raise MakeProbeError("computed Make supplier is not bound to native completion")
                if name and not re.fullmatch(LITERAL_NAME, name):
                    raise MakeProbeError("unsupported Make supplier is not bound to native completion")
                if function[0] == "call" and name == "eval":
                    raise MakeProbeError("unsupported completion evaluated source")
                if function[0] == "call" and name in {"value", "origin", "flavor", "call"}:
                    raise MakeProbeError("unsupported completion forwarded name-taking builtin")
                if name:
                    names.add(name)
    names.update(name for _, _, name in _literal_metadata(line, **limits))
    conditional = CONDITIONAL.match(line) if directives else None
    if conditional:
        name = conditional[1].strip(MAKE_SPACE)
        if "$" in name:
            raise MakeProbeError("computed Make supplier is not bound to native completion")
        if not re.fullmatch(LITERAL_NAME, name):
            raise MakeProbeError("unsupported Make supplier is not bound to native completion")
        names.add(name)
    export = re.match(r"^[ \t]*(?:(?:override|private)[ \t]+)*export(?:[ \t]+(.*))?$", line) if directives else None
    if export is not None:
        consumers = (export[1] or "").split()
        if not consumers or any(not re.fullmatch(LITERAL_NAME, name) for name in consumers):
            raise MakeProbeError("unsupported completion export consumer")
        names.update(consumers)
    return names
