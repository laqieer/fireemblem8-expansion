"""Graph-domain planning over the shared native Make observer.

The lexical census classifies reference positions, never Make's target or
recipe behavior. Every recorded state comes from the caller's ProbeSession.
"""

from __future__ import annotations

import re
import copy
from collections import Counter
from dataclasses import dataclass, field
from itertools import chain
from typing import NamedTuple
from pathlib import PurePosixPath

from .authority import encoded, relative_path
from .budget import MakeProbeError, ProbeBudget
from .graph_commands import MakeCommands


IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
DEFAULT = re.compile(
    rf"(?<![A-Za-z0-9_])(?P<modifiers>(?:(?:export|private|override)\s+)*)"
    rf"(?P<name>{IDENTIFIER})\s*\?="
)
REFERENCE = re.compile(
    rf"(?<!\$)\$(?:\((?P<paren>{IDENTIFIER})(?=[:)])"
    rf"|\{{(?P<brace>{IDENTIFIER})(?=[:}}])|(?P<short>[A-Za-z]))"
)
SCOPED = re.compile(r"(?<!\$)\$(?:\(([@%*+<?^|](?:D|F)?|[0-9])\)|\{([@%*+<?^|](?:D|F)?|[0-9])\}|([@%*+<?^|0-9]))")
INTROSPECTION = re.compile(rf"\$[({{](?:flavor|origin|value)\s+({IDENTIFIER})[)}}]")
INTROSPECTION_CALL = re.compile(r"(?<!\$)\$[({](?:flavor|origin|value)[ \t]+([^)}]*)")
CONDITIONAL = re.compile(rf"^\s*(?:ifdef|ifndef)\s+({IDENTIFIER})")
CONDITIONAL_NAME = re.compile(r"^\s*(?:ifdef|ifndef)\s+(.+)$")
CALL = re.compile(rf"(?<!\$)\$[({{]call\s+({IDENTIFIER})[ \t]*(?=[,)}}])")
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
        rf"(?P<name>{IDENTIFIER})", rf"(?P<name>(?:{IDENTIFIER}|\.[A-Za-z_][A-Za-z0-9_]*))",
    ))
    for pattern in (ASSIGNMENT, TARGET_ASSIGNMENT)
)
DEFINE = re.compile(rf"^\s*(?:(?:export|override|private)\s+)*define\s+({IDENTIFIER})")
SECONDARY = re.compile(rf"\$\$(?:\(({IDENTIFIER})|\{{({IDENTIFIER})\}})")
NAME_PART = re.compile(rf"\$\(({IDENTIFIER})\)|\$\{{({IDENTIFIER})\}}")
MAKE_SPACE = " \t\r\n\v\f"
# GNU Make 4.3's intrinsic function namespace; candidate load is not admitted.
MAKE_FUNCTIONS = frozenset((
    "abspath", "addprefix", "addsuffix", "and", "basename", "call", "dir", "eq", "error",
    "eval", "file", "filter", "filter-out", "findstring", "firstword", "flavor", "foreach",
    "guile", "if", "info", "join", "lastword", "not", "notdir", "or", "origin", "patsubst",
    "realpath", "shell", "sort", "strip", "subst", "suffix", "value", "warning", "wildcard",
    "word", "wordlist", "words",
))
PURE_VALUE_FUNCTIONS = MAKE_FUNCTIONS - {
    "call", "eval", "file", "shell", "guile", "info", "error", "warning",
}


class MakeSourceUnit(NamedTuple):
    text: str
    body: str | None = None
    native_literal_header: bool = False


@dataclass
class _MakeSourceMode:
    posix: bool | None = False
    definitions: dict = field(default_factory=dict)
    forced: frozenset = frozenset()
    budget: ProbeBudget | None = None

    def checkpoint(self):
        if self.budget is not None:
            self.budget.remaining()

    def uncertain(self):
        if self.posix is not True:
            self.posix = None

    def collapse(self, value):
        self.checkpoint()
        if self.posix is not None:
            return _collapse_make_continuations(value, posix=self.posix)
        ordinary = _collapse_make_continuations(value)
        if ordinary != _collapse_make_continuations(value, posix=True):
            raise MakeProbeError("unproven GNU Make parsing-mode context changes continuation data")
        return ordinary

    def effectful(self, expression):
        if self.posix is True:
            return False
        pending, active, complete = [(None, expression)], set(), set()
        while pending:
            self.checkpoint()
            name, value = pending.pop()
            if value is None:
                active.remove(name)
                complete.add(name)
                continue
            if name is not None:
                if name in complete:
                    continue
                if name in active:
                    return True
                active.add(name)
                pending.append((name, None))
            for body in make_expressions(value):
                operation = re.match(r"([^ \t\r\n\v\f]+)[ \t\r\n\v\f]+", body)
                if operation and operation[1] not in MAKE_FUNCTIONS - {"call", "eval", "guile"}:
                    return True
            value = _without_literal_metadata(value)
            if next(computed_selectors(value), None) is not None:
                return True
            for dependency in references(value):
                if dependency not in self.definitions:
                    return True
                pending.append((dependency, self.definitions[dependency]))
        return False

    def assign(self, name, operator, value, *, override=False, active=True):
        if active is False or name in self.forced and not override:
            return
        if operator == "?=" and name in self.definitions:
            return
        if active is None:
            self.definitions.pop(name, None)
            if operator in {":=", "::=", "!=", "+="} and self.effectful(value):
                self.uncertain()
            return
        if operator in {":=", "::=", "!="}:
            if self.effectful(value):
                self.uncertain()
            self.definitions[name] = ""
        elif operator == "+=":
            # The prior flavor is not reconstructed here. Either evaluation
            # time must be safe before it can leave the mode proven.
            if self.effectful(value):
                self.uncertain()
            self.definitions[name] = self.definitions.get(name, "") + " " + value
        else:
            self.definitions[name] = value


def _literal_condition(keyword, arguments):
    if keyword not in {"ifeq", "ifneq"} or "$" in arguments:
        return None
    if arguments.startswith("("):
        match = re.fullmatch(r"\(([^(),]*),([^()]*)\)", arguments)
        if not match:
            return None
        left, right = match[1].rstrip(" \t"), match[2].lstrip(MAKE_SPACE)
    else:
        words = []
        while arguments and arguments[0] in "'\"" and len(words) < 2:
            stop = arguments.find(arguments[0], 1)
            if stop < 0:
                return None
            words.append(arguments[1:stop])
            arguments = arguments[stop + 1:].lstrip(MAKE_SPACE)
        if len(words) != 2 or arguments:
            return None
        left, right = words
    return (left == right) == (keyword == "ifeq")


def _mode_and(left, right):
    return False if left is False or right is False else True if left is True and right is True else None


def _mode_not(value):
    return None if value is None else not value


def _include_names(header):
    include = re.fullmatch(r"(?:-?include|sinclude)[ \t\r\n\v\f]+(.*)", header)
    if include is None:
        return False
    if any(character in include[1] for character in "$*?[]~\\"):
        return None
    return re.split(r"[ \t\r\n\v\f]+", include[1].strip(MAKE_SPACE)) if include[1].strip(MAKE_SPACE) else []


def _make_logical_chunks(text):
    pending = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        has_lf = index < len(lines) - 1
        if has_lf and line.endswith("\r"):
            line = line[:-1]
        pending.append(line)
        slashes = len(line) - len(line.rstrip("\\"))
        if has_lf and slashes % 2:
            continue
        yield "\n".join(pending)
        pending = []
    if pending:
        yield "\n".join(pending)


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


def make_source_units(text, *, mode=None, include=None, known_context=True):
    """GNU logical lines and whole define bodies; recipes keep their escapes."""
    if "\0" in text:
        raise MakeProbeError("Make source contains an unsupported NUL byte")
    text = text.removeprefix("\ufeff")
    chunks = iter(_make_logical_chunks(text))
    mode = _MakeSourceMode() if mode is None else mode
    pending_posix = False
    conditions, active = [], True

    def record_pending():
        nonlocal pending_posix
        if pending_posix is not False and active is not False:
            if pending_posix is True and active is True:
                mode.posix = True
            else:
                mode.uncertain()
            if active is True:
                pending_posix = False

    for raw in chunks:
        mode.checkpoint()
        line = raw if raw.startswith("\t") else mode.collapse(raw)
        header = strip_comment(line).strip(MAKE_SPACE)
        assignment = None if raw.startswith("\t") else MODE_ASSIGNMENT.fullmatch(header)
        if not raw.startswith("\t") and not assignment and re.match(r"^(?:(?:export|override|private)[ \t]+)*define(?:[ \t]|$)", header) and not re.fullmatch(
            rf"(?:(?:export|override|private)[ \t]+)*define[ \t]+{IDENTIFIER}[ \t]*(?:(?:\?=|::=|:=|\+=|!=|=)[ \t]*)?",
            header,
        ):
            raise MakeProbeError("Make source has an unproven dynamic define name")
        if not raw.startswith("\t") and re.match(r"^(?:(?:export|override|private)[ \t]+)*\.RECIPEPREFIX\b", header):
            raise MakeProbeError("Make source requires an unproven non-default recipe-prefix context")
        conditional = None if raw.startswith("\t") or assignment else re.fullmatch(
            r"(ifeq|ifneq|ifdef|ifndef|else|endif)(?:[ \t\r\n\v\f]+(.*))?", header,
        )
        if conditional:
            keyword, arguments = conditional[1], conditional[2] or ""
            if keyword == "endif":
                if not conditions or arguments:
                    raise MakeProbeError("Make parsing-mode context has an unmatched conditional")
                active = conditions.pop()[0]
            else:
                if keyword == "else":
                    if not conditions or conditions[-1][2]:
                        raise MakeProbeError("Make parsing-mode context has an unmatched else")
                    parent, seen, _ = conditions[-1]
                    eligible = _mode_and(parent, _mode_not(seen))
                    nested = re.fullmatch(r"(ifeq|ifneq|ifdef|ifndef)[ \t\r\n\v\f]+(.*)", arguments)
                    if arguments and nested is None:
                        raise MakeProbeError("Make parsing-mode context has an unproven else")
                    if nested:
                        keyword, arguments = nested[1], nested[2]
                        choice = _literal_condition(keyword, arguments)
                    else:
                        choice = True
                        conditions[-1][2] = True
                    conditions[-1][1] = _mode_not(_mode_and(_mode_not(seen), _mode_not(choice)))
                    active = _mode_and(eligible, choice)
                else:
                    eligible = active
                    choice = _literal_condition(keyword, arguments)
                    conditions.append([active, choice, False])
                    active = _mode_and(active, choice)
                if eligible is not False and mode.effectful(arguments):
                    mode.uncertain()
            yield MakeSourceUnit(line)
            continue
        if header and not raw.startswith("\t"):
            # GNU collapses this line before recording the preceding rule.
            record_pending()
        definition = DEFINE.match(header) if not raw.startswith("\t") and not assignment else None
        if definition:
            body, depth = [], 1
            for raw_body in chunks:
                part = mode.collapse(raw_body)
                directive = strip_comment(part).strip(MAKE_SPACE)
                if not part.startswith("\t"):
                    if re.match(r"^define(?:[ \t]|$)", directive):
                        depth += 1
                    elif re.match(r"^endef(?:[ \t]|$)", directive):
                        depth -= 1
                        if not depth:
                            break
                body.append(part)
            else:
                raise MakeProbeError("Make source has an unterminated define body")
            body = "\n".join(body)
            mode.assign(
                definition[1], header[definition.end():].strip(MAKE_SPACE) or "=", body,
                override="override" in header[:definition.start(1)].split(), active=active,
            )
            yield MakeSourceUnit(line, body)
        else:
            if not raw.startswith("\t") and active is not False:
                if assignment:
                    mode.assign(
                        assignment["name"], assignment["operator"], assignment["value"],
                        override="override" in header[:assignment.start("name")].split(), active=active,
                    )
                elif MODE_TARGET_ASSIGNMENT.fullmatch(header):
                    target_assignment = MODE_TARGET_ASSIGNMENT.fullmatch(header)
                    if target_assignment["operator"] in {":=", "::=", "!=", "+="} and mode.effectful(target_assignment["value"]):
                        mode.uncertain()
                else:
                    if mode.effectful(header):
                        mode.uncertain()
                    included = _include_names(header)
                    if included is not False:
                        if include is None:
                            mode.uncertain()
                        else:
                            include(included, active, mode)
                    else:
                        separators = _rule_separators(header)
                        left = header[:separators[0]] if separators else header
                        if any(character in left for character in "$*?[%\\"):
                            pending_posix = None
                        elif separators and ".POSIX" in re.split(r"[ \t\r\n\v\f]+", left.strip(MAKE_SPACE)):
                            if mode.posix is not True and (active is None or not known_context):
                                raise MakeProbeError("unproven conditional/include .POSIX activation")
                            pending_posix = True
            yield MakeSourceUnit(line)
    if conditions:
        raise MakeProbeError("Make parsing-mode context has an unterminated conditional")
    record_pending()


def _source_units(sources, *, assignments=(), budget=None):
    decoded = {}
    for path, data in sources.items():
        if budget is not None:
            budget.remaining()
        try:
            decoded[path] = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MakeProbeError(f"Make census source is not UTF-8: {path}") from error
    mode = _MakeSourceMode(
        definitions={name: value for _, name, value in assignments},
        forced=frozenset(name for origin, name, _ in assignments if origin == "command-line"),
        budget=budget,
    )
    units, reading, unresolved_modes = {}, set(), []

    def visit(path, *, known=True):
        mode.checkpoint()
        if path in reading:
            raise MakeProbeError("Make include parsing-mode context is recursive")
        previous = units.get(path)
        if previous is None:
            units[path] = []
        reading.add(path)

        def included(names, active, current_mode):
            if names is None:
                unresolved_modes.append(current_mode.posix)
                current_mode.uncertain()
                return
            if active is None:
                current_mode.uncertain()
            for name in names:
                if name in decoded:
                    visit(name, known=known and active is True)

        count = 0
        # Revisit the original inputs; reuse only the identical source units,
        # never the effects of a previous include invocation.
        for unit in make_source_units(decoded[path], mode=mode, include=included, known_context=known):
            if previous is None:
                units[path].append(unit)
            elif count >= len(previous) or unit != previous[count]:
                raise MakeProbeError("Make include source changed across original read contexts")
            count += 1
        if previous is not None and count != len(previous):
            raise MakeProbeError("Make include source changed across original read contexts")
        reading.remove(path)

    if decoded:
        visit(next(iter(decoded)))
    for path in decoded:
        if path not in units:
            mode.posix = True if unresolved_modes and all(value is True for value in unresolved_modes) else None
            visit(path, known=False)
    return units


def _ordered_source_units(units):
    ordered, visited, known_positions = [], set(), set()

    def visit(path, *, known=False):
        if path in visited:
            return
        visited.add(path)
        conditional_depth = 0
        for index, unit in enumerate(units[path]):
            if known:
                known_positions.add(len(ordered))
            ordered.append((path, index, unit))
            header = strip_comment(unit.text)
            if unit.body is not None or unit.text.startswith("\t"):
                continue
            if re.match(r"^[ \t]*(?:ifeq|ifneq|ifdef|ifndef)(?:[ \t]|$)", header):
                conditional_depth += 1
            elif re.match(r"^[ \t]*endif(?:[ \t]|$)", header):
                conditional_depth -= 1
            include = re.fullmatch(r"[ \t]*-?include[ \t]+([^$#]+)", header)
            if include:
                for name in re.split(r"[ \t]+", include[1].strip(" \t")):
                    if name in units:
                        visit(name, known=known and conditional_depth == 0)

    if units:
        visit(next(iter(units)), known=True)
    for path in units:
        visit(path)
    return ordered, known_positions


class _UnresolvedName(ValueError):
    pass


def _make_expression_spans(line, *, staged=False, require_complete=False):
    stack = []
    index = 0
    while index < len(line):
        if line[index:index + 2] == "$$":
            index += 1 if staged else 2
            continue
        if line[index:index + 2] in {"$(", "${"}:
            stack.append((index + 2, ")" if line[index + 1] == "(" else "}"))
            index += 2
            continue
        if stack and line[index] == stack[-1][1]:
            start, _ = stack.pop()
            if start is not None:
                yield start - 2, index + 1, line[start:index]
        elif stack and line[index] == ("(" if stack[-1][1] == ")" else "{"):
            stack.append((None, stack[-1][1]))
        elif staged and line[index] == "$":
            token = line[index:index + 2]
            if not REFERENCE.fullmatch(token) and not SCOPED.fullmatch(token):
                raise _UnresolvedName("incomplete or unsupported dollar token")
            index += 1
        index += 1
    if (staged or require_complete) and stack:
        raise _UnresolvedName("incomplete dollar-bearing Make expression")


def make_expressions(line):
    for _, _, body in _make_expression_spans(line):
        yield body


def _without_literal_metadata(expression):
    spans = [
        (start, stop) for start, stop, body in _make_expression_spans(expression)
        if re.fullmatch(r"(?:origin|flavor|value)[ \t\r\n\v\f]+" + IDENTIFIER, body)
    ]
    for start, stop in sorted(spans, reverse=True):
        expression = expression[:start] + expression[stop:]
    return expression


def _make_function(expression):
    text = expression.strip(MAKE_SPACE)
    outer = [body for start, stop, body in _make_expression_spans(text) if start == 0 and stop == len(text)]
    if len(outer) != 1:
        return None
    match = re.fullmatch(r"([A-Za-z_-]+)[ \t]+(.*)", outer[0], re.S)
    if match is None:
        return None
    value = match[2]
    arguments, start, stack = [], 0, []
    for index, character in enumerate(value):
        if character in "({":
            stack.append(")" if character == "(" else "}")
        elif stack and character == stack[-1]:
            stack.pop()
        elif character == "," and not stack:
            arguments.append(value[start:index])
            start = index + 1
    if stack:
        raise MakeProbeError("incomplete Make function argument")
    arguments.append(value[start:])
    return match[1], arguments


def _template_reference_names(text, *, recipe=False):
    """Validate one reference-preserving template region without evaluating it."""
    names, index = set(), 0
    while index < len(text):
        if text[index] != "$":
            index += 1
            continue
        if text[index:index + 2] == "$$":
            if not recipe:
                raise MakeProbeError("deferred graph syntax is outside the rule-template contract")
            deferred = text[index + 1:]
            spans = [(stop, body) for start, stop, body in _make_expression_spans(deferred) if start == 0]
            if spans:
                stop, body = spans[-1]
                if (
                    not re.fullmatch(IDENTIFIER, body)
                    and not SCOPED.fullmatch(deferred[:stop])
                    and not re.fullmatch(r"[@%*+<?^|](?:D|F)?:[^$\s=]*=[^$\s=]*", body)
                ):
                    raise MakeProbeError("rule-template recipe has an unproven deferred operation")
            elif len(deferred) > 1 and (SCOPED.fullmatch(deferred[:2]) or re.fullmatch(r"\$[A-Za-z]", deferred[:2])):
                stop = 2
            else:
                raise MakeProbeError("rule-template recipe has an unproven deferred reference")
            index += stop + 1
            continue
        spans = [(stop, body) for start, stop, body in _make_expression_spans(text[index:]) if start == 0]
        if not spans:
            raise MakeProbeError("rule template has an incomplete or unbound reference")
        stop, body = spans[-1]
        if re.fullmatch(IDENTIFIER, body):
            names.add(body)
        else:
            function = _make_function(text[index:index + stop])
            if recipe or function is None or function[0] != "wildcard" or len(function[1]) != 1:
                raise MakeProbeError("rule template has an unproven transformation")
            names.update(_template_reference_names(function[1][0]))
        index += stop
    return names


def _rule_template_parts(body):
    lines = list(make_source_units(body))
    headers = [unit.text for unit in lines if not unit.text.startswith("\t") and strip_comment(unit.text).strip(MAKE_SPACE)]
    recipes = [unit.text for unit in lines if unit.text.startswith("\t")]
    if len(headers) != 1 or any(unit.body is not None for unit in lines):
        raise MakeProbeError("rule template must contain one header and ordinary recipes")
    if next(unit for unit in lines if strip_comment(unit.text).strip(MAKE_SPACE)).text.startswith("\t"):
        raise MakeProbeError("rule-template recipe precedes its header")
    header, inline = split_inline_recipe(strip_comment(headers[0]))
    if inline or ASSIGNMENT.match(header) or TARGET_ASSIGNMENT.match(header):
        raise MakeProbeError("rule template cannot emit assignments or inline control")
    separators = _rule_separators(header)
    if len(separators) != 1:
        raise MakeProbeError("rule template lacks a single literal rule separator")
    left, right = header[:separators[0]], header[separators[0] + 1:]
    if not re.search(r"\$\([1-9]\)|\$\{[1-9]\}", left):
        raise MakeProbeError("rule template target does not bind its parameter")
    return left, right, recipes


def _rule_separators(header):
    spans = [(start, stop) for start, stop, _ in _make_expression_spans(header)]
    return [index for index, char in enumerate(header)
            if char == ":" and not any(start <= index < stop for start, stop in spans)]


def _unproven_assignment_destination(header):
    if re.match(r"^[ \t]*(?:ifeq|ifneq|ifdef|ifndef|else|endif|include|-include)(?:[ \t]|$)", header):
        return False
    spans = [(start, stop) for start, stop, _ in _make_expression_spans(header)]
    equals = next((index for index, char in enumerate(header)
                   if char == "=" and not any(start <= index < stop for start, stop in spans)), None)
    return (
        equals is not None and "$" in header[:equals] and ASSIGNMENT.match(header) is None
        and TARGET_ASSIGNMENT.match(header) is None
    ) or (
        re.match(r"^[ \t]*(?:override[ \t]+)?undefine[ \t]+", header)
        and not re.fullmatch(r"[ \t]*(?:override[ \t]+)?undefine[ \t]+" + IDENTIFIER + r"[ \t]*", header)
    )


def _prepare_rule_templates(
    session, target, state, commands, observation, sources, *, observe_dispatch=False, external_names=(),
):
    units = _source_units(sources, assignments=state, budget=session.budget)
    ordered, known_positions = _ordered_source_units(units)
    positions = {(path, index): offset for offset, (path, index, _) in enumerate(ordered)}
    macros, assignments, initializers, callers = {}, {}, {}, []
    for path, index, unit in ordered:
        header = strip_comment(unit.text)
        declaration = ASSIGNMENT.match(header) if unit.body is None else None
        if declaration:
            assignments.setdefault(declaration["name"], []).append(positions[path, index])
            initializers.setdefault(declaration["name"], []).append((declaration["operator"], declaration["value"]))
        if unit.body is None and not header.startswith("\t"):
            undefined = re.fullmatch(r"[ \t]*(?:override[ \t]+)?undefine[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*", header)
            if undefined:
                assignments.setdefault(undefined[1], []).append(positions[path, index])
                initializers.setdefault(undefined[1], []).append(("undefine", ""))
        if unit.body is not None:
            match = DEFINE.match(header)
            if match:
                macros.setdefault(match[1], []).append((path, index, unit))
        function = _make_function(header) if unit.body is None and not header.startswith("\t") else None
        if function is None or function[0] != "foreach" or len(function[1]) != 3:
            continue
        variable, values, action = function[1]
        variable = variable.strip(MAKE_SPACE)
        evaluated = _make_function(action)
        invoked = _make_function(evaluated[1][0]) if evaluated and evaluated[0] == "eval" and len(evaluated[1]) == 1 else None
        if invoked is None or invoked[0] != "call":
            continue
        arguments = [argument.strip(MAKE_SPACE) for argument in invoked[1]]
        if (
            not re.fullmatch(IDENTIFIER, variable) or len(arguments) != 2
            or arguments[1] not in {"$(" + variable + ")", "${" + variable + "}"}
            or not re.fullmatch(IDENTIFIER, arguments[0]) or arguments[0] in MAKE_FUNCTIONS
        ):
            raise MakeProbeError("unproven parameterized rule-template invocation")
        candidates = macros.get(arguments[0], ())
        if len(candidates) != 1 or positions[candidates[0][0], candidates[0][1]] >= positions[path, index]:
            raise MakeProbeError("rule template lacks one original prior definition")
        callers.append((path, index, variable, values.strip(MAKE_SPACE), candidates[0]))
    if not callers:
        return None, set(), set()
    loaded_names = observation.semantics["domains"]["MAKEFILE_LIST"]["value"].split()
    if len(loaded_names) != len(set(loaded_names)):
        raise MakeProbeError("rule-template source order contains repeated includes")
    session.budget.charge("cache", len(encoded({
        path: [(unit.text, unit.body) for unit in items] for path, items in units.items()
    })))
    for _, _, unit in ordered:
        if unit.body is not None or unit.text.startswith("\t"):
            continue
        header, _ = split_inline_recipe(strip_comment(unit.text))
        if _unproven_assignment_destination(header):
            raise MakeProbeError("rule-template context has an unproven assignment destination")
    for _, _, _, _, (path, index, macro) in callers:
        name = DEFINE.match(strip_comment(macro.text))[1]
        header = strip_comment(macro.text)
        if len(macros[name]) != 1 or header[DEFINE.match(header).end():].strip(MAKE_SPACE) not in {"", "="}:
            raise MakeProbeError("rule template has an ambiguous or non-recursive definition history")
        if name in external_names or name in observation.semantics["domains"]:
            raise MakeProbeError("rule-template macro identity is not a closed source definition")
    validated_callers = {(path, index) for path, index, *_ in callers}
    for path, index, unit in ordered:
        if (path, index) in validated_callers:
            continue
        for body in make_expressions(unit.body if unit.body is not None else unit.text):
            if not body.startswith(("eval ", "eval\t")):
                continue
            assignment = ASSIGNMENT.fullmatch(body[5:].lstrip(MAKE_SPACE))
            if assignment is None:
                raise MakeProbeError("rule-template context contains an unproven eval effect")
            # Recipe/definition effects are not ordered before a top-level call.
            position = positions[path, index] if unit.body is None and not unit.text.startswith("\t") else len(ordered)
            assignments.setdefault(assignment["name"], []).append(position)
            initializers.setdefault(assignment["name"], []).append((assignment["operator"], assignment["value"]))
    globals_seen = {}
    pure = set()

    def require_pure_initializer(name, active=()):
        if name in pure:
            return
        if name in active:
            raise MakeProbeError("rule-template input has a cyclic initializer")
        if name not in initializers:
            supplied = [value for _, key, value in state if key == name]
            if len(supplied) != 1 or any(character in supplied[0] for character in "$\r\n"):
                raise MakeProbeError("rule-template initializer depends on an unproven input")
            pure.add(name)
            return
        for operator, value in initializers.get(name, ()):
            if operator not in {"=", "?=", ":=", "::=", "+="}:
                raise MakeProbeError("rule-template input has an effectful initializer")
            for body in make_expressions(value):
                operation = re.match(r"([^ \t]+)[ \t]+", body)
                if operation and operation[1] not in PURE_VALUE_FUNCTIONS:
                    raise MakeProbeError("rule-template input has an unproven initializer operation")
            for dependency in references(value):
                require_pure_initializer(dependency, (*active, name))
        pure.add(name)

    def read_globals(names):
        pending = sorted(set(names) - set(globals_seen))
        for offset in range(0, len(pending), 512):
            actual = session.make(target, definitions=tuple(pending[offset:offset + 512]), assignments=state,
                                  commands=commands, observe_recipe_dispatch=observe_dispatch)
            _stable_native_context(observation.semantics, actual.semantics)
            records = actual.semantics["definitions"]["global"]
            session.budget.charge("cache", len(encoded(records)))
            globals_seen.update(records)

    def stable_value(name, position):
        history = assignments.get(name, ())
        if not history or any(place >= position or place not in known_positions for place in history):
            raise MakeProbeError("rule-template input lacks original global assignment context")
        require_pure_initializer(name)
        read_globals((name,))
        record = globals_seen[name]
        value = record["value"]
        if record["origin"] == "undefined":
            return ""
        if record["flavor"] not in {"simple", "recursive"} or any(char in value for char in "$\r\n"):
            raise MakeProbeError("rule-template input is not a stable literal native value")
        return value

    def substitute_literals(text, position, *, header):
        result = text
        for match in reversed(list(NAME_PART.finditer(text))):
            if match.start() and text[match.start() - 1] == "$":
                continue
            value = stable_value(match[1] or match[2], position)
            if header and any(character in value for character in "#;:|=\\"):
                raise MakeProbeError("rule-template input can change its parsed header")
            result = result[:match.start()] + value + result[match.end():]
        return result

    replacements, omitted, graph_inputs, scoped = {}, set(), set(), set()
    for path, index, variable, values, (macro_path, macro_index, macro) in callers:
        position = positions[path, index]
        if position not in known_positions:
            raise MakeProbeError("rule-template caller lacks proven original source ordering")
        macro_name = DEFINE.match(strip_comment(macro.text))[1]
        if assignments.get(macro_name):
            raise MakeProbeError("rule-template macro has an additional assignment history")
        read_globals((macro_name,))
        actual_macro = globals_seen[macro_name]
        if (
            actual_macro["origin"] not in {"file", "override"} or actual_macro["flavor"] != "recursive"
            or actual_macro["value"] != macro.body
        ):
            raise MakeProbeError("rule-template native definition differs from its original source")
        value_name = NAME_PART.fullmatch(values)
        if value_name:
            name = value_name[1] or value_name[2]
            graph_inputs.add(name)
            values = stable_value(name, position)
        words = re.split(r"[ \t\r\n\v\f]+", values.strip(MAKE_SPACE)) if values.strip(MAKE_SPACE) else []
        if any(not re.fullmatch(IDENTIFIER, word) for word in words):
            raise MakeProbeError("rule-template parameters are not bounded identifier words")
        left, right, recipes = _rule_template_parts(macro.body)
        instantiated = []
        for word in words:
            # Admit the reference IR before expanding the parameter into copies
            # of the template. Identifiers are ASCII, so JSON growth is exact.
            parameter_count = macro.body.count("$(1)") + macro.body.count("${1}")
            session.budget.charge(
                "cache", len(encoded(macro.body)) + parameter_count * (len(word) - 4) + 10 * (len(recipes) + 1),
            )
            replace_parameter = lambda value: value.replace("$(1)", word).replace("${1}", word)
            target_text, prerequisite_text = replace_parameter(left), replace_parameter(right)
            names = _template_reference_names(target_text) | _template_reference_names(prerequisite_text)
            recipe_texts = [replace_parameter(recipe) for recipe in recipes]
            for recipe in recipe_texts:
                names.update(_template_reference_names(recipe, recipe=True))
            if variable in names:
                raise MakeProbeError("rule template has an unbound loop-scope reference")
            read_globals(names)
            actual_target = substitute_literals(target_text, position, header=True).strip(MAKE_SPACE)
            relative_path(actual_target)
            if any(character in actual_target for character in " \t%*?[]"):
                raise MakeProbeError("rule-template target is not one confined literal path")
            substitute_literals(prerequisite_text, position, header=True)
            for text in recipe_texts:
                substitute_literals(text, position, header=False)
            for body in make_expressions(prerequisite_text):
                wildcard = re.fullmatch(r"wildcard[ \t]+(.*)", body, re.S)
                if wildcard:
                    pattern = substitute_literals(wildcard[1], position, header=True)
                    relative_path(pattern)
                    parent = PurePosixPath(pattern).parent
                    if any(char in pattern for char in MAKE_SPACE + "[]~") or any(char in str(parent) for char in "*?"):
                        raise MakeProbeError("rule-template wildcard requires one literal directory pattern")
                    # A conservative directory superset avoids reimplementing
                    # GNU glob matching; only Make determines the actual list.
                    for name in chain(session.loader.entries, (item.path for item in observation.generated)):
                        if PurePosixPath(name).parent == parent and any(char in name for char in " \t\r\n\v\f$#;:|=\\"):
                            raise MakeProbeError("rule-template wildcard lacks a reference-preserving namespace")
            # This is the proved reference IR, never a Makefile executed by the
            # probe. GNU Make already supplied the actual graph/recipe result.
            instantiated.append(MakeSourceUnit(target_text + ":" + prerequisite_text, native_literal_header=True))
            instantiated.extend(MakeSourceUnit(recipe.replace("$$", "$")) for recipe in recipe_texts)
            scoped.add("1")
        replacements[path, index] = instantiated
        omitted.add((macro_path, macro_index))
        scoped.add(variable)
    prepared = {
        path: [replacement for index, unit in enumerate(items)
               for replacement in ([] if (path, index) in omitted else replacements.get((path, index), [unit]))]
        for path, items in units.items()
    }
    return prepared, graph_inputs, scoped


def dollar_fragment(value):
    """Classify incomplete staged syntax, not a list of known fragment values."""
    try:
        for _ in _make_expression_spans(value, staged=True):
            pass
    except _UnresolvedName:
        return True
    return False


def _outside_eval_references(expression):
    spans = sorted(
        (start, stop) for start, stop, body in _make_expression_spans(expression)
        if body.startswith(("eval ", "eval\t"))
    )
    pieces, previous = [], 0
    for start, stop in spans:
        if start >= previous:
            pieces.append(expression[previous:start])
        previous = max(previous, stop)
    pieces.append(expression[previous:])
    return references("".join(pieces))


def _require_staged_reference_contract(expressions, *, allow_eval=False, original=True):
    """Only transparent references have an emitted-name contract here."""
    for expression in expressions:
        for staged in (False, True):
            try:
                for _, _, body in _make_expression_spans(
                    expression, staged=staged, require_complete=original and not staged,
                ):
                    operation = re.fullmatch(r"([^ \t\r\n\v\f]+)[ \t\r\n\v\f]+(.*)", body, re.S)
                    if operation:
                        name, argument = operation.groups()
                        if allow_eval and name == "eval":
                            continue
                        if name == "call":
                            argument = argument.strip(" \t\r\n\v\f")
                            if re.fullmatch(IDENTIFIER, argument) or NAME_PART.fullmatch(argument):
                                continue
                        raise MakeProbeError("unproven emitted-reference transformation in staged Make input")
                    if ":" in body or any(character.isspace() for character in body):
                        raise MakeProbeError("unproven emitted-reference substitution in staged Make input")
            except _UnresolvedName:
                if original and not staged:
                    raise MakeProbeError("incomplete original Make expression cannot prove staged references")
                # Incomplete syntax still needs the separate native fragment/
                # eval proof; it is not transparent output evidence.
                continue


def computed_selectors(line):
    for body in make_expressions(line):
        call = re.match(r"call[ \t]+", body)
        if call:
            body = body[call.end():]
        depth, end = 0, len(body)
        for position, character in enumerate(body):
            if character in "({":
                depth += 1
            elif character in ")}":
                depth -= 1
            elif not depth and (character == "," and call or character.isspace() or character == ":"):
                end = position if call or character == ":" else 0
                break
        head = body[:end]
        if "$" in head:
            yield head


def selected_names(expressions, definitions, observed_values, *, unresolved=None):
    def native_constant(declarations):
        if len(set(declarations)) != 1:
            return False
        value = declarations[0]
        spans = list(_make_expression_spans(value))
        if len(spans) != 1 or spans[0][:2] != (0, len(value)):
            return False
        operation = re.fullmatch(r"(?:subst|patsubst)[ \t]+([^$]*)", spans[0][2], re.S)
        return operation is not None

    def expand(template, active):
        matches = list(NAME_PART.finditer(template))
        values, offset = {""}, 0
        for match in matches:
            literal = template[offset:match.start()]
            if "$" in literal:
                raise _UnresolvedName("computed selector contains an unsupported name expression")
            name = match[1] or match[2]
            if name in active:
                raise _UnresolvedName("computed selector has a cyclic name definition")
            declarations = definitions.get(name)
            choices = set(observed_values.get(name, ()))
            if declarations:
                if any(value is None for value in declarations):
                    raise _UnresolvedName("computed selector has an unresolved source definition")
                try:
                    for value in declarations:
                        choices.update(expand(value, active | {name}))
                except _UnresolvedName:
                    if not choices or not native_constant(declarations):
                        raise
            elif not choices:
                raise _UnresolvedName("computed selector lacks a closed literal or finite name definition")
            combined = set()
            for prefix in values:
                for choice in choices:
                    combined.add(prefix + literal + choice)
                    if len(combined) > 512:
                        raise _UnresolvedName("computed selector exceeds the existing bounded context plan")
            values, offset = combined, match.end()
        if "$" in template[offset:]:
            raise _UnresolvedName("computed selector contains an unsupported name expression")
        return {value + template[offset:] for value in values}

    selected = set()
    for expression in expressions:
        for selector in computed_selectors(expression):
            try:
                values = expand(selector, set())
                if not values or any(
                    not re.fullmatch(IDENTIFIER, name) and not SCOPED.fullmatch("$(" + name + ")")
                    for name in values
                ):
                    raise _UnresolvedName("computed selector is not a closed set of variable identifiers")
            except _UnresolvedName as error:
                if unresolved is None:
                    raise
                unresolved.append(error)
                continue
            selected.update(values)
    return selected


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


def computed_introspection(line):
    return any(
        not re.fullmatch(IDENTIFIER, match[1].strip())
        for match in INTROSPECTION_CALL.finditer(line)
    )


def split_inline_recipe(line):
    if ASSIGNMENT.match(line) or TARGET_ASSIGNMENT.match(line):
        return line, ""
    stack = []
    escaped = False
    rule = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if stack:
            if character == stack[-1]:
                stack.pop()
            elif character in "({" and (character == "(" and stack[-1] == ")" or
                                       character == "{" and stack[-1] == "}" or
                                       index and line[index - 1] == "$"):
                stack.append(")" if character == "(" else "}")
            continue
        if character in "({" and index and line[index - 1] == "$":
            stack.append(")" if character == "(" else "}")
        elif character == "#":
            break
        elif character == ":":
            rule = True
        elif character == ";" and rule:
            return line[:index], line[index + 1:]
    return line, ""


def references(line):
    names = {next(value for value in match.groups() if value is not None)
             for pattern in (REFERENCE, SCOPED) for match in pattern.finditer(line)}
    names.update(INTROSPECTION.findall(line))
    names.update(CALL.findall(line))
    conditional = CONDITIONAL.match(line)
    if conditional:
        names.add(conditional.group(1))
    return names


def closure(names, dependencies):
    pending, result = set(names), set()
    while pending:
        name = pending.pop()
        if name not in result:
            result.add(name)
            pending.update(dependencies.get(name, ()))
    return result


def source_census(
    sources, *, observed_values=None, reference_units=None, template_graph_inputs=(), template_scoped=(),
    source_assignments=(), budget=None,
):
    all_names, graph, recipe, introspection, defaults = set(), set(), set(), set(), set()
    dependencies = {}
    definitions, expressions, graph_expressions = {}, {}, []
    eval_requests, deferred_evals, consumed_expressions = [], set(), []
    eval_raw_expressions = {}
    retained_declarations = set()
    ambiguous_assignment = False
    observed_values = {} if observed_values is None else observed_values
    stage_sinks, stage_roots = [], set()
    graph.update(template_graph_inputs)
    all_names.update(template_graph_inputs)
    all_names.update(template_scoped)

    def extend_known(destination, values):
        added = set(values) - destination
        if added and budget is not None:
            budget.charge("cache", len(encoded(sorted(added))))
        destination.update(added)

    def retain_once(kind, value):
        key = (kind, value)
        if key in retained_declarations:
            return False
        if budget is not None:
            budget.charge("cache", len(encoded(key)))
        retained_declarations.add(key)
        return True

    def retain_defaults(statement):
        assignment = ASSIGNMENT.fullmatch(statement) or TARGET_ASSIGNMENT.fullmatch(statement)
        if assignment:
            if assignment["operator"] == "?=":
                found = DEFAULT.finditer(statement[:assignment.start("value")])
                defaults.update(match["name"] for match in found
                                if "override" not in match["modifiers"].split())
            return
        spans = [(start, stop) for start, stop, _ in _make_expression_spans(statement)]
        if any(not any(start <= match.start() < stop for start, stop in spans)
               for match in re.finditer(r"\?=", statement)):
            raise MakeProbeError("Make external-default declaration has a dynamic name")

    def retain_define_default(line, definition):
        prefix = " ".join(word for word in line[:definition.start(1)].split() if word != "define")
        retain_defaults(prefix + " " + line[definition.start(1):])

    def retain_assignment(assignment, *, expanded_input=False):
        if expanded_input and not retain_once("assignment", assignment[0]):
            return
        name, value = assignment["name"], assignment["value"]
        retain_defaults(assignment[0])
        dependencies.setdefault(name, set())
        if not expanded_input or "$" not in value:
            dependencies[name].update(references(value))
        definitions.setdefault(name, []).append(
            value.lstrip(MAKE_SPACE)
            if assignment["operator"] in {"=", ":=", "::=", "?="} and not (expanded_input and "$" in value)
            else None
        )
        if not expanded_input or "$" not in value:
            expressions.setdefault(name, []).append(value)

    if reference_units is None:
        reference_units = _source_units(sources, assignments=source_assignments, budget=budget)
    ordered, known_positions = _ordered_source_units(reference_units)
    secondary_directive = lambda unit: (
        unit.body is None and not unit.text.startswith("\t")
        and re.match(r"^[ \t]*\.SECONDEXPANSION[ \t]*:", strip_comment(unit.text))
    )
    secondary_expansion = any(
        secondary_directive(unit) for position, (_, _, unit) in enumerate(ordered) if position not in known_positions
    )
    for path, _, unit in ordered:
        raw = unit.text
        statement, inline_recipe = (raw, "") if raw.startswith("\t") else split_inline_recipe(raw)
        line = statement if raw.startswith("\t") else strip_comment(statement)
        if not raw.startswith("\t"):
            ambiguous_assignment |= bool(_unproven_assignment_destination(line))
            undefined = re.fullmatch(r"[ \t]*(?:override[ \t]+)?undefine[ \t]+(" + IDENTIFIER + r")[ \t]*", line)
            if undefined:
                definitions.setdefault(undefined[1], []).append(None)
        if secondary_directive(unit):
            secondary_expansion = True
        if not raw.startswith("\t") and computed_introspection(line.replace("$$", "$")):
            raise MakeProbeError(f"computed Make introspection lacks a sealed literal selector: {path}")
        if inline_recipe:
            inline_names = references(inline_recipe)
            all_names.update(inline_names)
            consumed_expressions.append(inline_recipe)
            eval_requests.append((None, inline_recipe))
            if "$(eval" in inline_recipe or "${eval" in inline_recipe:
                graph.update(inline_names)
                graph_expressions.append(inline_recipe)
                stage_sinks.append(inline_recipe)
            else:
                recipe.update(inline_names)
        start = DEFINE.match(line)
        if start and unit.body is not None:
            defining = start.group(1)
            retain_define_default(line, start)
            dependencies.setdefault(defining, set())
            expressions.setdefault(defining, [])
            operator = line[start.end():].strip(MAKE_SPACE)
            definitions.setdefault(defining, []).append(
                unit.body if operator in {"", "=", ":=", "::=", "?="} else None
            )
            names = references(unit.body) | references(unit.body.replace("$$", "$"))
            all_names.update(names)
            dependencies[defining].update(names)
            expressions[defining].append(unit.body)
            introspection.update(INTROSPECTION.findall(unit.body))
            if "$(eval" in unit.body or "${eval" in unit.body:
                deferred_evals.add(defining)
                eval_requests.append((defining, unit.body))
            continue
        names = references(line)
        all_names.update(names)
        introspection.update(INTROSPECTION.findall(line))
        assignment = None if raw.startswith("\t") else ASSIGNMENT.match(line)
        target_assignment = None if raw.startswith("\t") else TARGET_ASSIGNMENT.match(line)
        if assignment:
            retain_assignment(assignment)
            immediate = assignment["operator"] in {":=", "::=", "!="}
            eval_requests.append((None if immediate else assignment["name"], assignment["value"]))
            eval_raw_expressions.setdefault(assignment["value"], set()).add(assignment["value"].lstrip(MAKE_SPACE))
            if immediate:
                consumed_expressions.append(assignment["value"])
            if line.lstrip(MAKE_SPACE).startswith("export "):
                recipe.update(names)
                consumed_expressions.append("$(" + assignment["name"] + ")")
            if "$(eval" in assignment["value"] or "${eval" in assignment["value"]:
                if immediate:
                    graph.update(names)
                    graph_expressions.append(assignment["value"])
                    stage_sinks.append(assignment["value"])
                else:
                    deferred_evals.add(assignment["name"])
        elif target_assignment:
            retain_assignment(target_assignment)
            immediate = target_assignment["operator"] in {":=", "::=", "!="}
            eval_requests.append((None if immediate else target_assignment["name"], target_assignment["value"]))
            eval_raw_expressions.setdefault(target_assignment["value"], set()).add(target_assignment["value"].lstrip(MAKE_SPACE))
            if immediate:
                consumed_expressions.append(target_assignment["value"])
            graph.update(references(target_assignment["target"]))
            graph_expressions.append(target_assignment["target"])
            consumed_expressions.append(target_assignment["target"])
            if "$(eval" in target_assignment["value"] or "${eval" in target_assignment["value"]:
                if immediate:
                    graph.update(names)
                    graph_expressions.append(target_assignment["value"])
                    stage_sinks.append(target_assignment["value"])
                else:
                    deferred_evals.add(target_assignment["name"])
        elif raw.startswith("\t"):
            consumed_expressions.append(line)
            eval_requests.append((None, line))
            (graph if "$(eval" in line or "${eval" in line else recipe).update(names)
            if "$(eval" in line or "${eval" in line:
                graph_expressions.append(line)
                stage_sinks.append(line)
        else:
            consumed_expressions.append(line)
            eval_requests.append((None, line))
            graph.update(names)
            graph_expressions.append(line.replace("$$", "$"))
            if "$(eval" in line or "${eval" in line:
                stage_sinks.append(line.replace("$$", "$"))
            separators = _rule_separators(line)
            if (
                secondary_expansion and separators and not unit.native_literal_header
                and "$(eval" not in line and "${eval" not in line
            ):
                stage_sinks.append(line[separators[0] + 1:].replace("$$", "$"))
            conditional = CONDITIONAL_NAME.match(line)
            if conditional and "$" in conditional[1]:
                graph_expressions.append("$(" + conditional[1] + ")")
            secondary = {left or right for left, right in SECONDARY.findall(line)}
            graph.update(secondary)
            all_names.update(secondary)
        if not raw.startswith("\t"):
            retain_defaults(line)

    def retain_eval_history(body, active=()):
        if budget is not None:
            budget.remaining()
        forwarded = NAME_PART.fullmatch(body.strip(MAKE_SPACE))
        function = _make_function(body)
        if function and function[0] == "eval" and len(function[1]) == 1:
            return retain_eval_history(function[1][0], active)
        if forwarded or function and function[0] == "call" and len(function[1]) == 1:
            name = (forwarded[1] or forwarded[2]) if forwarded else function[1][0].strip(MAKE_SPACE)
            if not re.fullmatch(IDENTIFIER, name):
                try:
                    names = selected_names(["$(" + name + ")"], definitions, observed_values)
                except _UnresolvedName:
                    return False
            else:
                names = {name}
            for name in names:
                values = tuple(definitions.get(name, ()))
                if not values or name in active or len(active) >= 512:
                    return False
                if any(value is None or not retain_eval_history(value, (*active, name)) for value in values):
                    return False
            return True
        for unit in make_source_units(body, mode=_MakeSourceMode(budget=budget)):
            if unit.text.startswith("\t"):
                continue
            line = strip_comment(unit.text).strip(MAKE_SPACE)
            assignment = ASSIGNMENT.fullmatch(line) or TARGET_ASSIGNMENT.fullmatch(line)
            definition = DEFINE.match(line) if unit.body is not None else None
            if assignment:
                retain_assignment(assignment, expanded_input=True)
            elif definition:
                if retain_once("define", (line, unit.body)):
                    retain_define_default(line, definition)
                    definitions.setdefault(definition[1], []).append(unit.body if "$" not in unit.body else None)
                    dependencies.setdefault(definition[1], set()).update(references(unit.body))
            elif NAME_PART.fullmatch(line) or _make_function(line):
                nested = _make_function(line)
                if (nested and (nested[0] not in {"call", "eval"} or len(nested[1]) != 1)) or not retain_eval_history(line, active):
                    return False
            elif line and not _rule_separators(line):
                return False
        return True

    consumed, execution_roots, execution_dependencies = set(), set(), {}

    def fact_count():
        maps = (definitions, expressions, dependencies, execution_dependencies)
        return (
            sum(len(values) + sum(len(items) for items in values.values()) for values in maps)
            + len(consumed) + len(execution_roots) + len(graph) + len(stage_roots)
            + len(defaults) + len(retained_declarations)
        )

    # New selector edges can consume an eval whose assignments resolve another
    # selector. Only append-only facts determine completion.
    while True:
        if budget is not None:
            budget.remaining()
        before = fact_count()
        unresolved, unresolved_execution = set(), set()
        for name, values in list(expressions.items()):
            executing = [_without_literal_metadata(value) for value in values]
            actual_dependencies = execution_dependencies.setdefault(name, set())
            extend_known(actual_dependencies, set().union(*(references(value) for value in executing)))
            errors = []
            extend_known(dependencies[name], selected_names(values, definitions, observed_values, unresolved=errors))
            if errors:
                unresolved.add(name)
            errors = []
            extend_known(actual_dependencies, selected_names(executing, definitions, observed_values, unresolved=errors))
            if errors:
                unresolved_execution.add(name)
        executing = [_without_literal_metadata(value) for value in consumed_expressions]
        extend_known(execution_roots, set().union(*(references(value) for value in executing)))
        root_errors = []
        for destination, values in ((graph, graph_expressions), (stage_roots, stage_sinks), (execution_roots, executing)):
            extend_known(destination, selected_names(values, definitions, observed_values, unresolved=root_errors))
        extend_known(consumed, closure(execution_roots, execution_dependencies))
        extend_known(graph, deferred_evals & consumed)
        extend_known(stage_roots, deferred_evals & consumed)

        ambiguous_history, proven_eval_expressions = False, set()
        for owner, expression in eval_requests:
            if owner is not None and owner not in consumed:
                continue
            bodies = [body[5:].lstrip(MAKE_SPACE) for body in make_expressions(expression.replace("$$", "$"))
                      if body.startswith(("eval ", "eval\t"))]
            proven = True
            for body in bodies:
                if not retain_eval_history(body):
                    ambiguous_history, proven = True, False
            if bodies and proven:
                proven_eval_expressions.add(expression)
                proven_eval_expressions.update(eval_raw_expressions.get(expression, ()))
        if fact_count() == before:
            break

    if root_errors:
        raise MakeProbeError(str(root_errors[0])) from root_errors[0]
    if consumed & unresolved_execution:
        raise MakeProbeError("consumed Make body has an unresolved computed invocation")
    if ambiguous_history:
        raise MakeProbeError("unproven emitted-reference invocation in original assignment history")
    if budget is not None and proven_eval_expressions:
        budget.charge("cache", len(encoded(sorted(proven_eval_expressions))))
    expanded_graph = closure(graph, dependencies)
    if ambiguous_assignment and any(
        next(computed_selectors(value), None) is not None
        for value in chain(graph_expressions, (
            expression for name in expanded_graph for expression in expressions.get(name, ())
        ))
    ):
        raise MakeProbeError("computed selector has an unproven original assignment history")
    if expanded_graph & unresolved:
        raise MakeProbeError("graph dependency has an unresolved computed selector")
    expanded_recipe = closure(recipe, dependencies)
    stage_graph = closure(stage_roots | set().union(*(references(value) for value in stage_sinks)), dependencies)
    return {
        "all": closure(all_names | graph | recipe, dependencies),
        "graph": expanded_graph,
        "recipe": expanded_recipe,
        "recipe_only": expanded_recipe - expanded_graph,
        "introspection": closure(introspection, dependencies),
        "defaults": defaults,
        "defined": set(dependencies),
        "dependencies": dependencies,
        "definitions": definitions,
        "observed_values": observed_values,
        "unresolved": unresolved,
        "source_expressions": expressions,
        "graph_expressions": graph_expressions,
        "stage_graph": stage_graph,
        "stage_sinks": stage_sinks,
        "stage_expressions": stage_sinks + [
            value for name in stage_graph for value in expressions.get(name, ())
        ],
        "secondary_expansion": secondary_expansion,
        "proven_eval_expressions": proven_eval_expressions,
    }


def _loaded_sources(session, observation):
    values = observation.semantics["domains"]["MAKEFILE_LIST"]["value"].split()
    generated = {item.path: item.data for item in observation.generated}
    for resolved, spelling in observation.file_open_attempts:
        try:
            name = relative_path(resolved.removeprefix("/repo/"))
            relative_path(spelling.removeprefix("/repo/"))
        except MakeProbeError as error:
            raise MakeProbeError(f"unadmitted Make file-open spelling: {spelling!r}") from error
        if name not in session.snapshot.files and name not in generated:
            raise MakeProbeError(f"unadmitted Make file-open: {spelling!r}")
    result = {}
    for name in values:
        name = name.removeprefix("/repo/")
        relative_path(name)
        if name in session.snapshot.files:
            result[name] = session.snapshot.files[name]
        elif name in generated:
            result[name] = generated[name]
        else:
            raise MakeProbeError(f"GNU Make loaded an unadmitted include: {name}")
    return result


def _semantic(semantics):
    result = copy.deepcopy(semantics)
    result.pop("owner_inputs", None)
    result["domains"] = {name: value for name, value in result["domains"].items()
                         if name not in {"MAKEFILE_LIST", "MAKE_RESTARTS"}}
    result["files"] = [
        {**entry, "variables": {name: value for name, value in entry["variables"].items()
                        if name not in {"MAKEFILE_LIST", "MAKE_RESTARTS"}}}
        for entry in result["files"]
    ]
    return result


def _stable_native_context(expected, actual):
    structure = lambda value: [
        {key: item[key] for key in ("target", "source", "recipe", "prerequisites")}
        for item in value["files"]
    ]
    if structure(expected) != structure(actual):
        raise MakeProbeError("Make graph changed across variable observations")
    if expected["dynamic_commands"] != actual["dynamic_commands"]:
        raise MakeProbeError("Make command provenance changed across variable observations")
    recipes = lambda value: [
        {key: field for key, field in item.items() if key != "sequence"}
        for item in value["native_dispatches"] if item["kind"] == "recipe"
    ]
    if recipes(expected) != recipes(actual):
        raise MakeProbeError("native recipe context changed across variable observations")


def _graph_definitions(session, target, state, commands, observation, usage, *, observe_dispatch=False):
    evals = [
        body[5:].lstrip() for expression in usage["stage_expressions"]
        for body in make_expressions(expression) if body.startswith(("eval ", "eval\t"))
    ]
    if not usage["stage_sinks"] and not evals:
        return
    session.budget.charge("cache", len(encoded([
        usage["stage_sinks"], usage["source_expressions"],
    ])))
    _require_staged_reference_contract(
        usage["stage_sinks"], allow_eval=True,
    )
    if any("$" in dependency["name"] for item in observation.semantics["files"] for dependency in item["prerequisites"]):
        raise MakeProbeError("staged Make graph retains unresolved dollar-bearing prerequisites")
    required = set(usage["stage_graph"])
    measured, records = set(), {}
    while True:
        for name in required:
            for expression in usage["source_expressions"].get(name, ()):
                _require_staged_reference_contract(
                    (expression,), allow_eval=expression in usage["proven_eval_expressions"],
                )
        pending = sorted(name for name in required - measured if re.fullmatch(IDENTIFIER, name))
        for offset in range(0, len(pending), 512):
            names = tuple(pending[offset:offset + 512])
            actual = session.make(
                target, definitions=names, assignments=state, commands=commands,
                observe_recipe_dispatch=observe_dispatch,
            )
            _stable_native_context(observation.semantics, actual.semantics)
            metadata = actual.semantics["definitions"]
            session.budget.charge("cache", len(encoded(metadata)))
            scopes = [metadata["global"], *(item["variables"] for item in metadata["files"])]
            for name in names:
                records[name] = [scope[name] for scope in scopes if scope[name]["origin"] != "undefined"]
                if any(value["origin"] in {"file", "override"} for value in records[name]):
                    usage["defined"].add(name)
            measured.update(names)
        forms, literals = [], copy.deepcopy(usage["observed_values"])
        definitions = copy.deepcopy(usage["definitions"])
        fragments = set()
        for name, values in records.items():
            for value in values:
                raw, flavor = value["value"], value["flavor"]
                definitions.setdefault(name, []).append(raw)
                if flavor == "simple" or "$" not in raw:
                    literals.setdefault(name, set()).add(raw)
                if dollar_fragment(raw):
                    fragments.add(name)
                if flavor == "recursive" or usage["secondary_expansion"] or evals:
                    forms.append(raw)
                if flavor == "recursive" and "$$" in raw and (usage["secondary_expansion"] or evals):
                    forms.append(raw.replace("$$", "$"))
        for form in forms:
            _require_staged_reference_contract(
                (form,), original=False, allow_eval=form in usage["proven_eval_expressions"],
            )
        found = closure(set().union(*(references(form) for form in forms)), usage["dependencies"])
        if found - required:
            required.update(found)
            continue
        try:
            found.update(selected_names(forms, definitions, literals))
        except _UnresolvedName as error:
            raise MakeProbeError("unresolved staged Make selector: " + str(error)) from error
        if found - required:
            required.update(found)
            continue
        affected_assignments = []
        for body in evals:
            if closure(references(body), usage["dependencies"]) & fragments:
                assignment = ASSIGNMENT.fullmatch(body)
                if assignment is None or assignment["operator"] != "=" or "\n" in body:
                    raise MakeProbeError("unresolved dollar-generated Make eval")
                affected_assignments.append(assignment["name"])
        outside_eval = set().union(*(
            _outside_eval_references(expression) for expression in usage["stage_expressions"]
        ))
        if usage["secondary_expansion"] and fragments & closure(outside_eval, usage["dependencies"]):
            raise MakeProbeError("unresolved dollar-generated secondary expansion")
        assignment_counts = Counter(
            assignment["name"] for body in evals
            if (assignment := ASSIGNMENT.fullmatch(body)) is not None
        )
        if any(assignment_counts[name] != 1 for name in affected_assignments):
            raise MakeProbeError("staged Make eval overwrites its observed definition")
        if set(affected_assignments) - required:
            required.update(affected_assignments)
            continue
        if any(not records.get(name) or any(value["flavor"] != "recursive" for value in records[name])
               for name in affected_assignments):
            raise MakeProbeError("staged Make eval lacks its resulting recursive definition")
        break
    usage["graph"].update(required)
    usage["all"].update(required)
    usage["recipe_only"].difference_update(required)


def _recipe_domains(session, target, state, commands, observation, usage, observed_names, *, observe_dispatch=False):
    """Measure referenced recipe values through bounded native variable pages."""
    if any(computed_introspection(entry["recipe"]) for entry in observation.semantics["files"]):
        raise MakeProbeError("computed Make introspection in a consumed recipe lacks a sealed literal selector")
    names = closure(
        {name for entry in observation.semantics["files"] for name in references(entry["recipe"])},
        usage["dependencies"],
    )
    try:
        names.update(closure(selected_names(
            (entry["recipe"] for entry in observation.semantics["files"]),
            usage["definitions"], usage["observed_values"],
        ), usage["dependencies"]))
    except _UnresolvedName as error:
        raise MakeProbeError(str(error)) from error
    if names & usage["unresolved"]:
        raise MakeProbeError("consumed recipe has an unresolved computed selector")
    exports = {
        name for context in observation.semantics["native_dispatches"]
        for name in context["environment"] if name in usage["defined"]
    }
    usage["recipe"].update(closure(names | exports, usage["dependencies"]))
    usage["all"].update(usage["recipe"])
    usage["recipe_only"] = usage["recipe"] - usage["graph"]
    pending = sorted(name for name in names - set(observed_names) if re.fullmatch(IDENTIFIER, name))
    combined = copy.deepcopy(observation.semantics)
    for index in range(0, len(pending), 512):
        chunk = tuple(pending[index:index + 512])
        measured = session.make(
            target, variables=chunk, assignments=state, commands=commands,
            observe_recipe_dispatch=observe_dispatch,
        )
        _stable_native_context(combined, measured.semantics)
        if measured.semantics.get("recipe_dispatches") != combined.get("recipe_dispatches"):
            raise MakeProbeError("native recipe dispatch changed across recipe observations")
        combined["domains"].update(measured.semantics["domains"])
        for previous, actual in zip(combined["files"], measured.semantics["files"]):
            previous["variables"].update(actual["variables"])
    return combined


def run_probe(
    loader, requested_targets, domains, dynamic_contracts, *, session,
    declared_external_names=(), environment_names=(), generated_path_names=(),
    symbolic_recipe_names=(), ambient_undefined_names=(), trusted_builtin_names=(),
    scoped_variable_names=(), escaped_literal_names=(), dispatch_targets=(), **unused,
):
    if session is None or session.loader is not loader or session.snapshot is None:
        raise MakeProbeError("graph Make planning requires the selected shared report session")
    if unused:
        raise MakeProbeError("obsolete Make planner arguments are not supported")
    if not requested_targets:
        return {}
    external = set(declared_external_names)
    environment = set(environment_names)
    symbolic = set(symbolic_recipe_names)
    trusted = set(trusted_builtin_names)
    scoped = set(scoped_variable_names)
    escaped = set(escaped_literal_names)
    undefined = set(ambient_undefined_names)
    variables = tuple(sorted({"MAKEFILE_LIST", "MAKE_RESTARTS", *domains}))
    if len(variables) > 512:
        raise MakeProbeError("graph domain observation exceeds the public variable bound")
    commands = MakeCommands(session, dynamic_contracts)
    results = {}
    for target in sorted(requested_targets):
        observe_dispatch = target in dispatch_targets
        pending = [()]
        visited, variants, source_union, usages = set(), [], {}, []
        planned = set()
        planned_domains = set()
        enumerated = set()
        while pending:
            session.budget.remaining()
            state = pending.pop(0)
            identity = tuple(sorted(state))
            planned.discard(identity)
            if identity in visited:
                continue
            visited.add(identity)
            observation = session.make(
                target, variables=variables, assignments=state, commands=commands,
                observe_recipe_dispatch=observe_dispatch,
            )
            loaded = _loaded_sources(session, observation)
            source_union.update(loaded)
            reference_units, template_inputs, template_scoped = _prepare_rule_templates(
                session, target, state, commands, observation, loaded, observe_dispatch=observe_dispatch,
                external_names=external | symbolic | environment,
            )
            scopes = [observation.semantics["domains"], *(
                entry["variables"] for entry in observation.semantics["files"]
            )]
            observed_values = {
                name: set(domain.get("values", ())) | {
                    scope[name]["value"] for scope in scopes if scope[name]["origin"] != "undefined"
                }
                for name, domain in domains.items()
            }
            usage = source_census(
                loaded, observed_values=observed_values, reference_units=reference_units,
                template_graph_inputs=template_inputs, template_scoped=template_scoped,
                source_assignments=state,
                budget=session.budget,
            )
            usages.append(usage)
            _graph_definitions(
                session, target, state, commands, observation, usage, observe_dispatch=observe_dispatch,
            )
            semantics = _recipe_domains(
                session, target, state, commands, observation, usage, variables, observe_dispatch=observe_dispatch,
            )
            unclassified = usage["defaults"] - external - set(domains)
            if unclassified:
                raise MakeProbeError(f"unsealed external defaults: {sorted(unclassified)}")
            wrong_symbolic = symbolic & usage["graph"]
            if wrong_symbolic:
                raise MakeProbeError(f"symbolic inputs influence the Make graph: {sorted(wrong_symbolic)}")
            values = semantics["domains"]
            actual_undefined = {name for name in usage["all"] if name in values
                                and values[name]["origin"] == "undefined"}
            unknown_undefined = actual_undefined - undefined - set(domains) - trusted - scoped - escaped
            unknown_undefined.update(usage["all"] - usage["defined"] - set(values)
                                     - trusted - scoped - escaped - undefined)
            if unknown_undefined:
                raise MakeProbeError(f"unsealed undefined Make inputs: {sorted(unknown_undefined)}")
            variants.append({"state": list(state), "record": _semantic(semantics)})
            for name in sorted(set(domains) & (usage["graph"] | usage["introspection"])):
                domain = domains[name]
                if domain["kind"] == "explicit":
                    choices = domain["values"]
                else:
                    if name not in values or values[name]["origin"] == "undefined":
                        raise MakeProbeError(f"tracked fallback has no actual Make value: {name}")
                    choices = [values[name]["value"]]
                if not choices or len(choices) > 32:
                    raise MakeProbeError(f"invalid finite Make domain: {name}")
                origins = ["command-line"]
                if name in environment and name in usage["defaults"]:
                    origins.append("environment")
                explicit_context = tuple(sorted(
                    item for item in state if item[1] != name
                    and domains[item[1]]["kind"] == "explicit"
                    and len(domains[item[1]]["values"]) > 1
                ))
                domain_context = (name, tuple(choices), tuple(origins), explicit_context)
                if domain_context in planned_domains:
                    continue
                planned_domains.add(domain_context)
                for origin in origins:
                    for value in choices:
                        if not isinstance(value, str):
                            raise MakeProbeError("Make domain values must be strings")
                        replacement = tuple(item for item in state if item[1] != name) + ((origin, name, value),)
                        key = tuple(sorted(replacement))
                        if key not in visited and key not in planned:
                            if len(visited) + len(planned) >= min(512, session.budget.limits.states):
                                raise MakeProbeError("graph domain fixed point exceeds its bounded context plan")
                            session.budget.admit_planned_state(len(encoded(replacement)))
                            pending.append(replacement)
                            planned.add(key)
                        enumerated.add(name)
        aggregate_defaults = set().union(*(usage["defaults"] for usage in usages))
        actual_names = set().union(*(usage["all"] for usage in usages))
        used_domains = set(domains) & actual_names
        generated = set(generated_path_names) & {
            dependency["name"]
            for variant in variants for entry in variant["record"]["files"]
            for dependency in entry["prerequisites"]
        }
        record = {
            "variants": sorted(variants, key=encoded),
            "includes": sorted(source_union),
            "symbolic_recipe_names": sorted(symbolic & set().union(*(usage["recipe_only"] for usage in usages))),
        }
        session.budget.charge("cache", len(encoded(record)))
        results[target] = {
            "record": record,
            "variable_census": {
                "defaults": sorted(aggregate_defaults & external),
                "ambient_undefined": sorted(actual_names & undefined),
                "trusted_builtins": sorted(actual_names & trusted),
                "scoped_variables": sorted(actual_names & scoped),
                "escaped_literals": sorted(actual_names & escaped),
            },
            "prerequisite_domain_census": {
                "used": sorted(used_domains), "enumerated": sorted(enumerated),
                "generated_paths": sorted(generated),
            },
            "dynamic_dependencies": sorted(dynamic_contracts.values(), key=lambda item: item["id"]),
        }
    return results
