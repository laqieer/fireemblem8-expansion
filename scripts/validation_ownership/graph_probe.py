"""Graph-domain planning over the shared native Make observer.

The lexical census classifies reference positions, never Make's target or
recipe behavior. Every recorded state comes from the caller's ProbeSession.
"""

from __future__ import annotations

import re
import copy
import hashlib
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import chain
from typing import NamedTuple
from pathlib import PurePosixPath

from .authority import ENVIRONMENT, _event_command, encoded, relative_path
from .budget import Limits, MakeProbeError, ProbeBudget
from .graph_commands import MakeCommands
from .graph_commands import _normalized_shell_commands
from .make_probe import _NamespaceUnavailable


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
SECONDARY = re.compile(rf"\$\$(?:\(({IDENTIFIER})(?=[:)])|\{{({IDENTIFIER})(?=[:}}]))")
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
INVOCATION_CONTROL_READS = frozenset(("MAKECMDGOALS", "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEOVERRIDES"))
SOURCE_HISTORY_CONTROLS = frozenset(("MAKEFILE_LIST", "MAKE_RESTARTS"))
EMPTY_RESULT_FUNCTIONS = frozenset(("error", "warning", "info", "eval"))
SIMPLE_ASSIGNMENT_OPERATORS = frozenset((":=", "::="))
# GNU Make 4.3 default.c identifier-shaped implicit-rule inputs, plus engine
# controls. Dotted/automatic names are outside the literal binding grammar.
IMPLICIT_MAKE_INPUTS = frozenset((
    "AR", "ARFLAGS", "AS", "ASFLAGS", "CC", "CFLAGS", "CPP", "CPPFLAGS", "CXX", "CXXFLAGS",
    "OBJC", "OBJCFLAGS", "CO", "COFLAGS", "FC", "FFLAGS", "F77", "F77FLAGS", "GET", "GFLAGS",
    "LD", "LDFLAGS", "LDLIBS", "LOADLIBES", "LEX", "LFLAGS", "LINT", "LINTFLAGS", "M2C",
    "M2FLAGS", "DEFFLAGS", "MODFLAGS", "PC", "PFLAGS", "RFLAGS", "YACC", "YFLAGS",
    "MAKEINFO", "MAKEINFO_FLAGS", "TEX", "TEXI2DVI", "TEXI2DVI_FLAGS", "WEAVE", "CWEAVE",
    "TANGLE", "CTANGLE", "RM", "TARGET_ARCH", "TARGET_MACH", "OUTPUT_OPTION",
    "SCCS_OUTPUT_OPTION", "VPATH", "GPATH", "SHELL", "MAKE", "MAKEFILES", "MAKELEVEL",
    "MAKE_VERSION", "MAKE_HOST", "MAKE_TERMOUT", "MAKE_TERMERR", "SUFFIXES",
))
PROTECTED_BINDINGS = IMPLICIT_MAKE_INPUTS | INVOCATION_CONTROL_READS | SOURCE_HISTORY_CONTROLS | set(ENVIRONMENT)
MAKE_DIRECTIVE = re.compile(
    r"^(define|endef|undefine|ifdef|ifndef|ifeq|ifneq|else|endif|include|-include|sinclude|"
    r"override|export|unexport|private|vpath|load|-load)(?:[ \t\r\n\v\f]+|$)"
)


class _AssignmentEffect(NamedTuple):
    applies: bool | None
    immediate: bool | None
    emitted: tuple = ()
    read_value: str | None = None


class MakeSourceUnit(NamedTuple):
    text: str
    body: str | None = None
    native_literal_header: bool = False
    conditional_depth: int = 0
    active: bool | None = True
    assignment: _AssignmentEffect | None = None
    emitted: tuple = ()
    kind: str | None = None
    phase: tuple | None = None
    phase_target: str | None = None
    phase_command: str | None = None
    reads: tuple = ()
    created_bindings: tuple = ()
    source_rule: object = None
    recipe_ordinal: int | None = None
    site: object = None


class _BindingRead(NamedTuple):
    text: str
    context: str = "staged"
    header: bool = False


class _SourceUnitStream(NamedTuple):
    ordered: tuple
    known_positions: frozenset
    remade: bool = False
    read_sources: tuple = ()
    native_exports: tuple = ()
    literal_modules: tuple = ()
    phase_tests: frozenset = frozenset()
    mode_state: object = None
    failed_sources: tuple = ()


class _LiteralBindingModule(NamedTuple):
    path: str
    bindings: tuple
    commands: tuple


class _SourceSite(NamedTuple):
    path: str
    logical: int
    start: int
    end: int

    def label(self):
        lines = str(self.start) if self.start == self.end else f"{self.start}-{self.end}"
        return f"{self.path}:{lines} (logical {self.logical})"


class _LogicalChunk(NamedTuple):
    text: str
    logical: int
    start: int
    end: int


@dataclass(frozen=True)
class _ModeBinding:
    origin: str
    flavor: str
    value: str | None
    inherited: tuple = ()
    scope: str | None = None


class _ScopeContext(NamedTuple):
    target: str | None
    selector: str
    kind: str
    rule: object = None
    ordinal: int | None = None
    job: int | None = None


class _ScopeDeclaration(NamedTuple):
    selector: str
    name: str
    operator: str
    site: object
    version: int


class _SourceRule(NamedTuple):
    number: int
    site: object
    targets: tuple | None
    target_pattern: str | None
    prerequisites: tuple | None
    first_prerequisite: str | None


def _scope_pattern_stem(pattern, target):
    if "%" not in pattern:
        return "" if pattern == target else None
    prefix, suffix = pattern.split("%")
    if not target.startswith(prefix) or not target.endswith(suffix) or len(target) < len(prefix) + len(suffix):
        return None
    return target[len(prefix):len(target) - len(suffix) if suffix else len(target)]


def _scopes_overlap(left, right):
    if "%" not in left:
        return _scope_pattern_stem(right, left) is not None
    if "%" not in right:
        return _scope_pattern_stem(left, right) is not None
    left_prefix, left_suffix = left.split("%")
    right_prefix, right_suffix = right.split("%")
    return (
        (left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix))
        and (left_suffix.endswith(right_suffix) or right_suffix.endswith(left_suffix))
    )


def _scope_words(value, *, patterns=True):
    if value is None:
        return None
    words = tuple(re.findall(r"[^ \t\r\n\v\f]+", value))
    if any(
        re.fullmatch(r"[A-Za-z0-9_./+%\-]+", word) is None
        or word.count("%") > int(patterns) or ".." in word.split("/")
        for word in words
    ):
        return None
    return words


UNPROVEN_BINDING = _ModeBinding("unknown", "unknown", None)
UNDEFINED_BINDING = _ModeBinding("undefined", "undefined", "")


@dataclass
class _MakeSourceMode:
    posix: bool | None = False
    definitions: dict = field(default_factory=dict)
    forced: frozenset = frozenset()
    budget: ProbeBudget | None = None
    control_values: dict = field(default_factory=dict)
    control_metadata: dict = field(default_factory=dict)
    control_reads: set = field(default_factory=set)
    original_input: object = None
    original_namespace_valid: bool = True
    namespace: frozenset | None = None
    site: _SourceSite | None = None
    first_uncertainty: tuple | None = None
    last_effect_input: str | None = None
    target_definitions: dict = field(default_factory=dict)
    binding_versions: dict = field(default_factory=dict)
    version: int = 0
    reads: set = field(default_factory=set)
    template_values: dict = field(default_factory=dict)
    template_mode: object = None
    original_target_value: object = None
    original_wildcard: object = None
    original_include_value: object = None
    original_execution: object = None
    invocation_inputs: frozenset = frozenset()
    namespace_holds: set = field(default_factory=set)
    scope_declarations: list = field(default_factory=list)
    inherited_appends: set = field(default_factory=set)
    scope_context: _ScopeContext | None = None
    scope_lookup_guard: object = None
    source_rule_count: int = 0

    def __post_init__(self):
        self.definitions = {
            name: frozenset((_ModeBinding("command line" if name in self.forced else "environment", "recursive", value),))
            for name, value in self.definitions.items()
        }

    def binding(self, name, scope=None):
        if scope is None and self.scope_lookup_guard is not None:
            self.scope_lookup_guard(self, name)
        if scope is None and self.scope_context is not None:
            context = self.scope_context
            if context.target is None:
                if name in self.scoped_names():
                    raise MakeProbeError("unproven target-pattern RHS binding: " + name)
                return self.raw_binding(name)
            selectors = self.matching_scopes(context.target)
            if selectors:
                selected, = selectors
                if (selected, name) in self.declared_scopes() and name in self.target_definitions.get(selected, {}):
                    values = self.raw_binding(name, selected)
                    bases = (
                        self.raw_binding(name) if (selected, name) in self.inherited_appends else ()
                    )
                    bases = tuple(
                        _ModeBinding(base.origin, base.flavor, self.template_snapshot(name))
                        if base.flavor == "simple" and base.value is None else base
                        for base in bases
                    )
                    result = frozenset(
                        _ModeBinding(value.origin, value.flavor, value.value,
                                     () if base is None else (base,), selected)
                        for value in values for base in (bases or (None,))
                    )
                    if self.budget is not None:
                        self.budget.charge("cache", len(encoded((
                            selected, name, [(value.origin, value.flavor, value.value) for value in values],
                            [(value.origin, value.flavor, value.value) for value in bases],
                        ))))
                    return result
            return self.raw_binding(name)
        return self.raw_binding(name, scope)

    def raw_binding(self, name, scope=None):
        definitions = self.definitions if scope is None else self.target_definitions.get(scope, {})
        if name in definitions:
            if self.binding_versions.get((scope, name), 0) != self.version:
                return frozenset((UNPROVEN_BINDING,))
            return definitions[name]
        if scope is not None:
            # A new target-specific append does not inherit a global simple
            # flavor. Original command-line precedence is still applicable.
            if self.version:
                values = frozenset((UNPROVEN_BINDING,))
            elif name in self.forced:
                values = frozenset(
                    value if value.origin == "command line"
                    else UNPROVEN_BINDING if value.origin == "unknown" else UNDEFINED_BINDING
                    for value in self.raw_binding(name)
                )
            else:
                values = frozenset((UNDEFINED_BINDING,))
        else:
            record = (
                self.original_input(name)
                if self.original_namespace_valid and self.original_input is not None else None
            )
            if record is None:
                values = frozenset((UNPROVEN_BINDING,))
            else:
                values = frozenset((_ModeBinding(record["origin"], record["flavor"], record["value"]),))
        self.retain_binding(name, values, scope)
        return values

    def declared_scopes(self):
        return {(record.selector, record.name) for record in self.scope_declarations}

    def scoped_names(self):
        return {record.name for record in self.scope_declarations}

    def matching_scopes(self, target):
        selectors = set()
        for record in self.scope_declarations:
            self.checkpoint()
            if _scope_pattern_stem(record.selector, target) is not None:
                selectors.add(record.selector)
        if len(selectors) > 1:
            raise MakeProbeError("unproven overlapping target/pattern bindings: " + target)
        return tuple(selectors)

    @contextmanager
    def using_scope(self, context):
        if not isinstance(context, _ScopeContext):
            raise MakeProbeError("scoped source lookup requires its typed context")
        previous = self.scope_context
        self.scope_context = context
        try:
            yield
        finally:
            self.scope_context = previous

    def binding_parts(self, binding):
        return (*binding.inherited, _ModeBinding(binding.origin, binding.flavor, binding.value))

    def binding_values(self, binding, active):
        choices = {""}
        for index, part in enumerate(self.binding_parts(binding)):
            self.checkpoint()
            if part.flavor == "undefined":
                values = ("",)
            elif part.value is None or part.flavor == "unknown":
                return None
            elif part.flavor == "recursive":
                values = self.literal_values(part.value, active)
                if values is None:
                    return None
            else:
                values = (part.value,)
            combined = set()
            for before in choices:
                for value in values:
                    text = before + (" " if index and before else "") + value
                    if self.budget is not None:
                        self.budget.charge("cache", len(encoded(text)))
                    combined.add(text)
                    if len(combined) > 512:
                        raise MakeProbeError("literal Make context exceeds the existing bounded context plan")
            choices = combined
        return choices

    def source_rule(self, header, *, literal=False, site=None):
        header, _ = split_inline_recipe(strip_comment(header))
        separators = _rule_separators(header)
        if not separators or len(separators) > 2:
            return None
        left = header[:separators[0]].strip(MAKE_SPACE)
        if left.startswith(".") and "/" not in left:
            return None
        def resolve(value, target=False):
            if literal or "$" not in value:
                return value
            return self.exact_target_text(value) if target else self.exact_initializer_value(value)
        targets = _scope_words(resolve(left, True))
        if targets is not None and any(
            "%" in target and "/" not in target and not target.startswith("%") for target in targets
        ):
            targets = None
        pattern = None
        if len(separators) == 2:
            patterns = _scope_words(resolve(header[separators[0] + 1:separators[1]].strip(MAKE_SPACE)))
            if patterns is None or len(patterns) != 1 or patterns[0].count("%") != 1:
                targets = None
            else:
                pattern = patterns[0]
        right = resolve(header[separators[-1] + 1:].strip(MAKE_SPACE))
        prerequisites = _scope_words(
            right.replace("|", " ") if right is not None and right.count("|") <= 1 else None,
        )
        normal = _scope_words(right.split("|", 1)[0]) if right is not None else None
        first = None if normal is None else normal[0] if normal else ""
        self.source_rule_count += 1
        if self.budget is not None and self.source_rule_count > self.budget.limits.observation_count:
            raise MakeProbeError("scoped source rule count exceeds existing observation bound")
        rule = _SourceRule(self.source_rule_count, site or self.site, targets, pattern, prerequisites, first)
        if self.budget is not None:
            self.budget.charge("cache", len(encoded(rule)))
        return rule

    def retain_binding(self, name, values, scope=None):
        definitions = self.definitions if scope is None else self.target_definitions.setdefault(scope, {})
        values = frozenset(values)
        if self.budget is not None and (
            values != definitions.get(name) or self.binding_versions.get((scope, name), 0) != self.version
        ):
            self.budget.charge("cache", len(encoded((
                scope, name, self.version,
                sorted(((value.origin, value.flavor, value.value) for value in values), key=encoded),
            ))))
        definitions[name] = values
        self.binding_versions[scope, name] = self.version

    def literal_text(self, expression, active=()):
        values = self.literal_values(expression, active)
        return next(iter(values)) if values is not None and len(values) == 1 else None

    def retain_reads(self, names):
        added = set(names) - self.reads
        if added and self.budget is not None:
            self.budget.charge("cache", len(encoded(sorted(added))))
        self.reads.update(added)

    def reference_names(self, body, active=()):
        """Resolve names from this original binding context, never final values."""
        self.checkpoint()
        if not self.original_namespace_valid:
            return None
        if re.fullmatch(IDENTIFIER, body):
            return frozenset((body,))
        if "$" not in body:
            return None
        try:
            names = self.literal_values(body, active)
        except RecursionError:
            return None
        if names is None or not names or any(not re.fullmatch(IDENTIFIER, name) for name in names):
            return None
        return names

    def literal_values(self, expression, active=()):
        self.checkpoint()
        if not self.original_namespace_valid:
            return None

        def literal(text):
            return None if "$" in text.replace("$$", "") else text.replace("$$", "$")

        spans = list(_make_expression_spans(expression, short=True))
        values, previous = {""}, 0
        for start, stop, body in sorted(spans, key=lambda item: (item[0], -item[1])):
            if start < previous:
                continue
            prefix = literal(expression[previous:start])
            if prefix is None:
                return None
            metadata = None
            function = None if re.fullmatch(IDENTIFIER, body) else _make_function(expression[start:stop])
            if function is not None:
                if (function[0] not in {"origin", "flavor", "value"} or len(function[1]) != 1
                        or not re.fullmatch(IDENTIFIER, function[1][0])):
                    return None
                metadata, body = function[0], function[1][0]
            names = self.reference_names(body, active)
            if names is None or metadata is None and set(names) & set(active):
                return None
            self.retain_reads(names)
            choices = set()
            for name in names:
                for binding in self.binding(name):
                    self.checkpoint()
                    if metadata is not None:
                        value = getattr(binding, metadata)
                        if value is None or metadata != "value" and value == "unknown":
                            return None
                        choices.add(value)
                    else:
                        expanded = self.binding_values(binding, (*active, name))
                        if expanded is None:
                            return None
                        choices.update(expanded)
            if not choices:
                return None
            combined = set()
            for value in values:
                for choice in choices:
                    self.checkpoint()
                    combined.add(value + prefix + choice)
                    if len(combined) > 512:
                        raise MakeProbeError("literal Make context exceeds the existing bounded context plan")
            if self.budget is not None:
                self.budget.charge("cache", len(encoded(sorted(combined))))
            values = combined
            previous = stop
        suffix = literal(expression[previous:])
        if suffix is None:
            return None
        return frozenset(value + suffix for value in values)

    def target_posix(self, header):
        statement, _ = split_inline_recipe(header)
        separators = _rule_separators(statement)
        left = statement[:separators[0]] if separators else statement
        if separators and left.endswith("&"):
            if left.endswith("\\&"):
                return None
            left = left[:-1]
        if "$" not in left:
            values = (left,)
        else:
            try:
                values = self.literal_values(left)
            except RecursionError:
                return None
            if values is None:
                value = self.exact_target_text(left)
                if value is None:
                    return None
                values = (value,)
        if not separators:
            if "$" not in header and not any(character in header for character in "*?[%\\"):
                return False
            return False if all(not value.strip(" \t") for value in values) else None
        outcomes = set()
        for value in values:
            self.checkpoint()
            names = _make_target_words(value)
            if names is None:
                return None
            outcomes.add(".POSIX" in names)
        return next(iter(outcomes)) if len(outcomes) == 1 else None

    def exact_target_text(self, expression):
        self.checkpoint()
        if self.posix is None or not self.original_namespace_valid or self.original_target_value is None:
            return None
        return self.original_target_value(self, expression)

    def checkpoint(self):
        if self.budget is not None:
            self.budget.remaining()

    def uncertain(self, reason="unproven effect", *, bindings=True):
        if self.posix is not True and self.first_uncertainty is None:
            self.first_uncertainty = (self.site, reason, self.last_effect_input)
        self.last_effect_input = None
        if bindings:
            self.version += 1
        self.original_namespace_valid = False
        self.control_values.clear()
        self.control_metadata.clear()
        self.control_reads.clear()
        if self.posix is not True:
            self.posix = None

    def bind_invocation(self, target):
        relative_path(target)
        if any(character in target for character in MAKE_SPACE + "$"):
            raise MakeProbeError("Make mode context requires one literal invocation goal")
        supplied = set(self.definitions) | self.forced | self.invocation_inputs
        if supplied & INVOCATION_CONTROL_READS:
            self.uncertain()
            return
        # These are original GNU invocation data, not post-parse variable
        # values. Source writes or unproven effects invalidate the facts.
        self.control_reads.update(INVOCATION_CONTROL_READS - supplied)
        if "MAKECMDGOALS" not in supplied:
            self.control_values["MAKECMDGOALS"] = target
            self.control_metadata["MAKECMDGOALS"] = ("default", "simple")
            self.definitions["MAKECMDGOALS"] = frozenset((_ModeBinding("default", "simple", target),))
        if self.budget is not None:
            self.budget.charge("cache", len(encoded((
                sorted(self.control_reads), self.control_values, self.control_metadata,
            ))))

    def collapse(self, value, *, construct=False):
        self.checkpoint()
        if self.posix is not None:
            return _collapse_make_continuations(value, posix=self.posix)
        ordinary = _collapse_make_continuations(value)
        posix = _collapse_make_continuations(value, posix=True)
        if ordinary != posix and not (construct and _same_make_assignment(ordinary, posix)):
            message = "unproven GNU Make parsing-mode context changes continuation data"
            detail = "" if self.site is None else "; source " + self.site.label()
            if self.first_uncertainty is not None:
                site, reason, name = self.first_uncertainty
                detail += "; first uncertainty " + (site.label() if site is not None else "<unknown source>")
                detail += ": " + reason + (f" [{name}]" if name else "")
            raise MakeProbeError(message + detail) from MakeProbeError(message)
        return ordinary

    def control_text(self, expression):
        self.checkpoint()
        spans = sorted(_make_expression_spans(expression), key=lambda item: (item[0], -item[1]))
        pieces, previous = [], 0
        for start, stop, body in spans:
            if start < previous:
                continue
            literal = expression[previous:start]
            if "$" in literal:
                return None
            if re.fullmatch(IDENTIFIER, body):
                value = self.control_values.get(body)
            else:
                function = _make_function(expression[start:stop])
                if function is None:
                    return None
                name, arguments = function
                if name in {"origin", "flavor"} and len(arguments) == 1:
                    metadata = self.control_metadata.get(arguments[0])
                    value = None if metadata is None else metadata[0 if name == "origin" else 1]
                elif name == "strip" and len(arguments) == 1:
                    value = self.control_text(arguments[0])
                    if value is not None:
                        value = " ".join(re.findall(r"[^ \t\r\n\v\f]+", value))
                elif name in {"filter", "filter-out"} and len(arguments) == 2:
                    patterns, words = (self.control_text(argument) for argument in arguments)
                    if patterns is None or words is None or any(character in patterns for character in "%\\"):
                        return None
                    patterns = set(re.findall(r"[^ \t\r\n\v\f]+", patterns))
                    value = " ".join(word for word in re.findall(r"[^ \t\r\n\v\f]+", words)
                                     if (word in patterns) == (name == "filter"))
                else:
                    return None
            if value is None:
                return None
            pieces.extend((literal, value))
            previous = stop
        if "$" in expression[previous:]:
            return None
        return "".join((*pieces, expression[previous:]))

    def condition(self, keyword, arguments):
        if "$" not in arguments:
            if keyword in {"ifdef", "ifndef"} and arguments in self.control_values:
                return bool(self.control_values[arguments]) == (keyword == "ifdef")
            return _literal_condition(keyword, arguments)
        if keyword not in {"ifeq", "ifneq"}:
            return None
        operands = _condition_operands(arguments)
        if operands is None:
            return None
        try:
            left, right = (self.control_text(value) for value in operands)
        except RecursionError:
            return None
        if left is None or right is None:
            equal = self.literal_comparison(operands, (left, right))
        else:
            equal = left == right
        return None if equal is None else equal == (keyword == "ifeq")

    def literal_comparison(self, operands, controls):
        self.checkpoint()
        if not self.original_namespace_valid:
            return None
        try:
            left, right = (
                frozenset((control,)) if control is not None else self.literal_values(operand)
                for operand, control in zip(operands, controls)
            )
        except RecursionError:
            return None
        if left is None or right is None or not left or not right:
            values = []
            for operand, value in zip(operands, (left, right)):
                exact = self.exact_initializer_value(operand) if value is None else None
                values.append(frozenset((exact,)) if exact is not None else value)
            left, right = values
            if left is None or right is None or not left or not right:
                return None
        # Source assignments cannot refresh invalidated engine/history facts.
        if self.reads & (SOURCE_HISTORY_CONTROLS | (INVOCATION_CONTROL_READS - self.control_reads)):
            return None
        if left.isdisjoint(right):
            return False
        if len(left) == len(right) == 1:
            return True
        return None

    def template_argument(self, expression, active=()):
        self.checkpoint()
        if "$" not in expression:
            return expression
        reference = NAME_PART.fullmatch(expression)
        if reference is None:
            return None
        name = reference[1] or reference[2]
        if name in active or len(active) >= 512:
            return None
        self.retain_reads((name,))
        bindings = self.binding(name)
        if len(bindings) != 1:
            return None
        binding = next(iter(bindings))
        if binding.flavor == "undefined":
            return ""
        if binding.flavor == "simple":
            return binding.value if binding.value is not None else self.exact_reference(name, active)
        if binding.flavor == "recursive" and binding.value is not None:
            return self.exact_reference(name, active)
        return None

    def template_initializer(self, expression):
        if (
            self.template_mode is None or not self.original_namespace_valid
            or expression != expression.strip(MAKE_SPACE)
        ):
            return None
        forwarded = NAME_PART.fullmatch(expression)
        if forwarded:
            name = forwarded[1] or forwarded[2]
            record = self.template_values.get(name)
            unscoped = self.scope_context is None or all(value.scope is None for value in self.binding(name))
            if unscoped and record is not None and record[0] == self.version:
                return record[1]
        function = _make_function(expression)
        if function is None or function[0] in {"notdir", "addprefix", "filter", "filter-out", "findstring", "strip", "and"}:
            if function is None:
                try:
                    literal = self.literal_text(expression)
                except RecursionError:
                    return None
                if literal is not None:
                    return self.template_header_composition(expression)
            value = self.exact_initializer_value(expression)
            if value is not None:
                return "exact", value
            return self.template_header_composition(expression) if function is None else None
        operation, arguments = function
        if operation not in {"patsubst", "wildcard"}:
            return None
        if operation == "wildcard":
            exact = self.exact_initializer_value(expression)
            if exact is not None:
                return "exact", exact
        try:
            values = tuple(self.template_argument(argument) for argument in arguments)
        except RecursionError:
            return None
        if any(value is None for value in values):
            return None
        if operation == "patsubst" and len(values) == 3:
            pattern, replacement, words = values
            if not _supported_patsubst(pattern, replacement):
                return None
            for index, _ in enumerate(re.finditer(r"[^ \t\r\n\v\f]+", words)):
                self.checkpoint()
                if index >= 512:
                    return None
            return operation, values
        if operation == "wildcard" and len(values) == 1 and self.namespace is not None:
            return _template_wildcard_bound(values[0], self.namespace, self.budget)
        return None

    def exact_reference(self, name, active=()):
        self.checkpoint()
        if not self.original_namespace_valid or name in active or len(active) >= 512:
            return None
        self.retain_reads((name,))
        bindings = self.binding(name)
        if len(bindings) != 1:
            return None
        binding = next(iter(bindings))
        literal = self.literal_text("$(" + name + ")", active)
        if literal is not None:
            return literal if "$" not in literal and "\0" not in literal else None
        if binding.inherited:
            parts = []
            for part in self.binding_parts(binding):
                value = (
                    part.value if part.flavor == "simple" else ""
                    if part.flavor == "undefined" else self.exact_initializer_value(part.value, active=(*active, name))
                    if part.flavor == "recursive" and part.value is not None else None
                )
                if value is None or "$" in value or "\0" in value:
                    return None
                parts.append(value)
            value = parts[0]
            for tail in parts[1:]:
                value = _join_make_text((value, " " if value else "", tail), self.budget)
            return value
        if binding.flavor == "recursive" and binding.value is not None:
            return self.exact_initializer_value(binding.value, active=(*active, name))
        if binding.scope is not None:
            return None
        return self.template_snapshot(name) if binding.flavor == "simple" else None

    def template_snapshot(self, name):
        record = self.template_values.get(name)
        if record is None or record[0] != self.version:
            return None
        kind, value = record[1]
        if kind == "exact":
            return value
        if kind == "patsubst":
            # Evaluate the captured original relation, never a native claim.
            if any("$" in argument or "\0" in argument for argument in value):
                return None
            value = _join_make_text(_original_patsubst_parts(value, self.budget), self.budget)
            return value if "$" not in value and "\0" not in value else None
        return None

    def exact_initializer_value(self, expression, active=()):
        self.checkpoint()
        if not self.original_namespace_valid or len(active) >= 512:
            return None

        def resolve(part, body):
            self.checkpoint()
            name = _make_reference_base(body)
            if re.fullmatch(IDENTIFIER, name):
                if body == name:
                    return self.exact_reference(name, active)
                suffix = body[len(name):]
                if suffix.startswith(":") and suffix.count("=") == 1 and "$" not in suffix:
                    pattern, replacement = suffix[1:].split("=", 1)
                    if "%" not in pattern:
                        pattern, replacement = "%" + pattern, "%" + replacement
                    if not _supported_patsubst(pattern, replacement):
                        return None
                    words = self.exact_reference(name, active)
                    if words is None:
                        return None
                    return _join_make_text(
                        _original_patsubst_parts((pattern, replacement, words), self.budget), self.budget,
                    )
            function = _make_function(part)
            if function is None:
                return None
            operation, arguments = function
            if operation in {"origin", "flavor", "value"}:
                value = self.literal_text(part)
                return value if value is not None and "$" not in value and "\0" not in value else None
            if operation == "and":
                value = ""
                for argument in arguments:
                    value = self.exact_initializer_value(argument.strip(MAKE_SPACE), active=active)
                    if value is None or not value:
                        return value
                return value
            if operation == "wildcard" and len(arguments) == 1:
                if self.original_wildcard is None:
                    return None
                patterns = self.exact_initializer_value(arguments[0], active=active)
                if patterns is None:
                    return None
                try:
                    return self.original_wildcard(patterns)
                except _NamespaceUnavailable as error:
                    if str(error) not in self.namespace_holds and self.budget is not None:
                        self.budget.charge("cache", len(encoded(str(error))))
                    self.namespace_holds.add(str(error))
                    return None
            if operation == "patsubst":
                fact = self.template_initializer(part)
                if (fact is not None and fact[0] == "patsubst"
                        and all("$" not in value and "\0" not in value for value in fact[1])):
                    return _join_make_text(_original_patsubst_parts(fact[1], self.budget), self.budget)
                return None
            if (operation, len(arguments)) not in {
                ("notdir", 1), ("addprefix", 2), ("filter", 2), ("filter-out", 2), ("findstring", 2), ("strip", 1),
            }:
                return None
            values = [self.exact_initializer_value(argument, active=active) for argument in arguments]
            if any(value is None for value in values):
                return None
            if operation == "findstring":
                return values[0] if values[0] in values[1] else ""
            patterns = _original_filter_patterns(values[0], self.budget) if operation in {"filter", "filter-out"} else ()
            if patterns is None:
                return None

            def parts():
                first = True
                for match in re.finditer(r"[^ \t\r\n\v\f]+", values[-1]):
                    self.checkpoint()
                    if operation in {"filter", "filter-out"} and (
                        any(_patsubst_word(pattern, match[0]) for pattern in patterns) != (operation == "filter")
                    ):
                        continue
                    if not first:
                        yield " "
                    first = False
                    if operation == "addprefix":
                        yield values[0]
                        yield match[0]
                    elif operation == "notdir":
                        yield match[0].rsplit("/", 1)[-1]
                    else:
                        yield match[0]

            return _join_make_text(parts(), self.budget)

        try:
            value = _resolve_make_text(expression, resolve, self.budget)
            return value if value is not None and "$" not in value and "\0" not in value else None
        except RecursionError:
            return None

    def template_header_reference(self, name, active=()):
        self.checkpoint()
        if not self.original_namespace_valid or name in active or len(active) >= 512:
            return False
        self.retain_reads((name,))
        bindings = self.binding(name)
        if len(bindings) != 1:
            return False
        binding = next(iter(bindings))
        try:
            literal = self.literal_text("$(" + name + ")")
        except RecursionError:
            return False
        if literal is not None:
            return _template_header_data(literal)
        if binding.inherited:
            return False
        if binding.flavor == "recursive" and binding.value is not None:
            forwarded = NAME_PART.fullmatch(binding.value)
            if forwarded:
                return self.template_header_reference(forwarded[1] or forwarded[2], (*active, name))
        record = self.template_values.get(name)
        if binding.flavor == "simple" and record is not None and record[0] == self.version and record[1][0] == "exact":
            return _template_header_data(record[1][1])
        return bool(
            binding.flavor == "simple" and record is not None and record[0] == self.version
            and record[1][0] == "header-bound"
        )

    def template_header_composition(self, expression):
        self.checkpoint()
        if not self.original_namespace_valid:
            return None
        try:
            spans = sorted(_make_expression_spans(expression, require_complete=True),
                           key=lambda item: (item[0], -item[1]))
        except _UnresolvedName:
            return None
        if not spans:
            return None
        previous = 0
        for start, stop, _ in spans:
            self.checkpoint()
            if start < previous:
                continue
            if not _template_header_data(expression[previous:start]):
                return None
            part = expression[start:stop]
            reference = NAME_PART.fullmatch(part)
            if reference:
                # Only assignment-time evidence may supply a referenced bound.
                if not self.template_header_reference(reference[1] or reference[2]):
                    return None
            else:
                function = _make_function(part)
                if function is None or function[0] != "wildcard" or self.template_initializer(part) is None:
                    return None
            previous = stop
        if not _template_header_data(expression[previous:]):
            return None
        return "header-bound", ("composition", expression)

    def effect_initializer_value(self, expression, local):
        if local:
            pending, seen = [expression], set()
            while pending:
                self.checkpoint()
                value = pending.pop()
                if next(computed_selectors(value), None) is not None or references(value) & local:
                    return None
                for name in references(_without_literal_metadata(value)):
                    if name in seen:
                        continue
                    seen.add(name)
                    self.retain_reads((name,))
                    for binding in self.binding(name):
                        for part in self.binding_parts(binding):
                            if part.flavor == "recursive" and part.value is not None:
                                pending.append(part.value)
        return self.exact_initializer_value(expression)

    def effectful(self, expression, *, automatic=False):
        self.last_effect_input = None
        pending, active, complete = [(None, expression, False, frozenset(), None)], set(), set()
        while pending:
            self.checkpoint()
            name, value, finished, local, binding = pending.pop()
            if automatic and not finished:
                scoped = frozenset(_scoped_references(value))
                if scoped - local and self.budget is not None:
                    self.budget.charge("cache", len(encoded(sorted(scoped - local))))
                local |= scoped
            identity = name, local
            if finished:
                active.remove(identity)
                complete.add((name, value, local))
                continue
            if name is not None:
                if (name, value, local) in complete:
                    continue
                if identity in active:
                    self.last_effect_input = name
                    return True
                active.add(identity)
                pending.append((name, value, True, local, binding))
            value = _prune_and(
                value, lambda argument: self.effect_initializer_value(argument, local), self.budget,
                lazy=self.original_execution is not None,
            )
            if name is not None and self.original_execution is not None:
                self.original_execution(self, name, binding, value, local)
            pieces, previous, covered = [], 0, 0
            for start, stop, _ in sorted(_make_expression_spans(value), key=lambda item: (item[0], -item[1])):
                if start < covered:
                    continue
                covered = stop
                function = _make_function(value[start:stop])
                if function is None:
                    continue
                operation, arguments = function
                if operation not in MAKE_FUNCTIONS - {"call", "eval", "guile"}:
                    self.last_effect_input = name or operation
                    return True
                if self.original_execution is not None and operation in {"and", "or", "if"}:
                    chosen = []
                    if operation == "if":
                        if len(arguments) not in {2, 3}:
                            self.last_effect_input = "conditional-execution"
                            return True
                        condition = self.effect_initializer_value(arguments[0].strip(MAKE_SPACE), local)
                        if condition is None:
                            self.last_effect_input = "conditional-execution"
                            return True
                        chosen = [arguments[0], arguments[1] if condition else arguments[2] if len(arguments) == 3 else ""]
                    else:
                        for index, argument in enumerate(arguments):
                            chosen.append(argument)
                            if index + 1 == len(arguments):
                                break
                            condition = self.effect_initializer_value(argument.strip(MAKE_SPACE), local)
                            if condition is None:
                                self.last_effect_input = "conditional-execution"
                                return True
                            if (operation == "and" and not condition) or (operation == "or" and condition):
                                break
                    pending.extend((None, argument, False, local, None) for argument in chosen)
                elif operation == "foreach":
                    if len(arguments) != 3:
                        self.last_effect_input = "foreach-scope"
                        return True
                    binder = self.effect_initializer_value(arguments[0].strip(MAKE_SPACE), local)
                    if binder is None or re.fullmatch(IDENTIFIER, binder) is None:
                        self.last_effect_input = "foreach-scope"
                        return True
                    words = self.effect_initializer_value(arguments[1], local)
                    # Only the body sees the simple local; name/list expansion
                    # still has the incoming scope and its possible effects.
                    if words is None or words.strip(MAKE_SPACE):
                        scoped = local | {binder}
                        if self.budget is not None:
                            self.budget.charge("cache", len(encoded(sorted(scoped))))
                        pending.append((None, arguments[2], False, scoped, None))
                    pending.extend((None, argument, False, local, None) for argument in arguments[:2])
                elif operation in {"origin", "flavor", "value"}:
                    metadata_names = tuple(metadata for _, _, metadata in _literal_metadata(value[start:stop])
                                           if metadata not in local)
                    self.retain_reads(metadata_names)
                    if self.scope_lookup_guard is not None:
                        for metadata in metadata_names:
                            self.scope_lookup_guard(self, metadata)
                    if not tuple(_literal_metadata(value[start:stop])):
                        pending.extend((None, argument, False, local, None) for argument in arguments)
                else:
                    pending.extend((None, argument, False, local, None) for argument in arguments)
                pieces.append(value[previous:start])
                previous = stop
            if pieces:
                value = _join_make_text((*pieces, value[previous:]), self.budget)
            for body in make_expressions(value):
                operation = re.match(r"([^ \t\r\n\v\f]+)[ \t\r\n\v\f]+", body)
                if operation and operation[1] not in MAKE_FUNCTIONS - {"call", "eval", "guile"}:
                    self.last_effect_input = name or operation[1]
                    return True
            self.retain_reads(metadata for _, _, metadata in _literal_metadata(value))
            if self.scope_lookup_guard is not None:
                for _, _, metadata in _literal_metadata(value):
                    if metadata not in local:
                        self.scope_lookup_guard(self, metadata)
            value = _without_literal_metadata(value)
            dependencies = references(value)
            for body in make_expressions(value):
                if "$" not in _make_reference_base(body):
                    continue
                names = None if local else self.reference_names(body, tuple(item[0] for item in active))
                if names is None:
                    self.last_effect_input = "computed-selector"
                    return True
                dependencies.update(names)
            dependencies.difference_update(local)
            self.retain_reads(dependencies)
            for dependency in dependencies:
                if dependency in self.control_reads:
                    continue
                for binding in self.binding(dependency):
                    if binding.flavor == "unknown" or binding.flavor == "recursive" and binding.value is None:
                        self.last_effect_input = dependency
                        return True
                    for part in reversed(self.binding_parts(binding)):
                        if part.flavor == "unknown" or part.flavor == "recursive" and part.value is None:
                            self.last_effect_input = dependency
                            return True
                        if part.flavor == "recursive":
                            pending.append((dependency, part.value, False, local, part))
                        elif self.original_execution is not None:
                            self.original_execution(self, dependency, part, part.value, local)
        return False

    def evaluate(self, expression, *, active=True):
        self.checkpoint()
        function = _make_function(expression)
        if function is not None and function[0] == "eval" and len(function[1]) == 1:
            body = function[1][0]
            # Only literal bytes and paired dollars prove the emitted program.
            # This is not expansion of variable values or arbitrary functions.
            if "$" not in body.replace("$$", ""):
                body = strip_comment(body.replace("$$", "$"))
                assignment = MODE_ASSIGNMENT.fullmatch(body)
                target_assignment = MODE_TARGET_ASSIGNMENT.fullmatch(body)
                try:
                    if assignment is not None:
                        effect = self.assign(
                            assignment["name"], assignment["operator"], assignment["value"],
                            override="override" in body[:assignment.start("name")].split(), active=active,
                        )
                    elif target_assignment is not None:
                        effect = self.assign_targets(
                            target_assignment,
                            override="override" in body[target_assignment.end("target"):target_assignment.start("name")].split(),
                            active=active,
                        )
                    else:
                        effect = None
                except RecursionError as error:
                    raise MakeProbeError("unproven nested emitted assignment context") from error
                if effect is not None:
                    self.uncertain("emitted assignment invalidates invocation controls", bindings=False)
                    return ((body, effect),)
        if self.effectful(expression):
            self.uncertain()
        return ()

    def export_declarations(self, statement, active):
        directive = MAKE_DIRECTIVE.match(statement)
        if directive is None or directive[1] not in {"export", "unexport"}:
            return ()
        operand = statement[directive.end():].strip(MAKE_SPACE)
        if not operand:
            return ()
        names = self.literal_text(operand)
        if names is None:
            self.uncertain("unproven export declaration names")
            return ()
        created = []
        for name in re.findall(r"[^ \t\r\n\v\f]+", names):
            self.checkpoint()
            if name == ".VARIABLES":
                raise MakeProbeError("original source has an unsupported variable-universe read")
            if not re.fullmatch(IDENTIFIER, name):
                raise MakeProbeError("unproven export declaration name")
            previous = self.binding(name)
            if any(value.flavor == "unknown" for value in previous):
                self.uncertain("unproven original export declaration binding")
                return ()
            if not any(value.flavor == "undefined" for value in previous):
                continue
            # Named export/unexport creates this binding in GNU Make.
            values = {value for value in previous if value.flavor != "undefined"}
            values.add(_ModeBinding("file", "simple", ""))
            if active is None:
                values.update(previous)
            self.retain_binding(name, values)
            created.append(name)
        return tuple(created)

    def assign(self, name, operator, value, *, override=False, active=True, scope=None, literal_body=False):
        if active is False:
            return _AssignmentEffect(False, False)
        definitions = self.definitions if scope is None else self.target_definitions.get(scope, {})
        previous = (
            self.binding(name, scope)
            if operator in {"?=", "+=", "undefine"} or active is None
            or name in definitions or name in self.forced or self.version else ()
        )
        definitions = self.definitions if scope is None else self.target_definitions.get(scope, {})
        choices = previous or (UNDEFINED_BINDING,)
        actions = []
        for before in choices:
            applies = (
                None if before.origin == "unknown"
                else override or before.origin not in {"override", "command line"}
            )
            if operator == "?=" and before.flavor not in {"undefined", "unknown"}:
                applies = False
            if operator == "+=":
                immediate = None if before.flavor == "unknown" else before.flavor == "simple"
            else:
                immediate = operator in SIMPLE_ASSIGNMENT_OPERATORS or operator == "!="
            actions.append((before, applies, immediate))

        def joined(values):
            values = set(values)
            return values.pop() if len(values) == 1 else None

        effect = _AssignmentEffect(joined(row[1] for row in actions), joined(row[2] for row in actions))
        if name in INVOCATION_CONTROL_READS and scope is None and effect.applies is not False:
            self.control_reads.clear()
            self.control_values.clear()
            self.control_metadata.clear()
        origin = "override" if override else "file"
        if not literal_body:
            value = value.lstrip(MAKE_SPACE)
        version = self.version
        literal_choices = None
        template_value = None
        read_value = None
        if (scope is None or self.original_execution is not None) and (
            operator in SIMPLE_ASSIGNMENT_OPERATORS or operator == "+=" and effect.immediate is True
        ):
            try:
                literal_choices = self.literal_values(value)
            except RecursionError:
                literal_choices = None
            if scope is not None and self.original_execution is not None and literal_choices is None:
                exact = self.exact_initializer_value(value)
                if exact is not None:
                    literal_choices = frozenset((exact,))
            if operator in SIMPLE_ASSIGNMENT_OPERATORS and literal_choices is None:
                template_value = self.template_initializer(value)
            elif operator == "+=" and active is True and effect.applies is True:
                before_exact = self.exact_reference(name)
                rhs_exact = self.exact_initializer_value(value)
                if before_exact is not None and rhs_exact is not None:
                    appended = before_exact if not rhs_exact else _join_make_text(
                        (before_exact, " " if before_exact else "", rhs_exact), self.budget,
                    )
                    template_value = "exact", appended
        # GNU expands simple/shell RHSs before write-precedence rejection.
        # Append expansion instead depends on the original binding's flavor.
        emitted = ()
        if effect.immediate is not False:
            read_value = _prune_and(value, self.exact_initializer_value, self.budget)
            if scope is not None and references(read_value) and self.original_execution is None:
                self.uncertain("unproven target-specific RHS expansion context")
            emitted = self.evaluate(value, active=active)
        if emitted and definitions.get(name, ()) != previous:
            self.uncertain("emitted assignment changes its enclosing destination")
        result = set(previous) if active is None else set()
        for before, applies, immediate in actions:
            if applies is not True:
                result.add(before if self.version == version else UNPROVEN_BINDING)
            if applies is False:
                continue
            if operator == "undefine":
                result.add(UNDEFINED_BINDING)
            elif operator == "!=":
                # The shell runs now, but its output becomes a recursive Make
                # body. Neither the command text nor a later value proves it.
                result.add(_ModeBinding(origin, "recursive", None))
            elif operator in SIMPLE_ASSIGNMENT_OPERATORS or operator == "+=" and immediate is True:
                if emitted:
                    literals = (value[:len(value) - len(value.lstrip(MAKE_SPACE))] + value[len(value.rstrip(MAKE_SPACE)):],)
                elif literal_choices is not None:
                    literals = literal_choices
                else:
                    literals = (value if "$" not in value else None,)
                for literal in literals:
                    if operator == "+=":
                        if literal in {"", None}:
                            result.add(before)
                        if literal == "":
                            continue
                        literal = (
                            (before.value + " " if before.value else "") + literal
                            if before.value is not None and literal is not None else None
                        )
                    result.add(_ModeBinding(origin, "simple", literal))
            elif operator == "+=":
                if before.flavor == "unknown":
                    result.add(UNPROVEN_BINDING)
                elif not value and before.flavor != "undefined":
                    result.add(before)
                elif before.value is None:
                    result.add(_ModeBinding(origin, "recursive", None))
                else:
                    result.add(_ModeBinding(origin, "recursive", (before.value + " " if before.value else "") + value))
            else:
                if operator == "?=" and before.flavor == "unknown":
                    result.add(before)
                result.add(_ModeBinding(origin, "recursive", value))
        if self.version != version:
            result.add(UNPROVEN_BINDING)
        self.retain_binding(name, result, scope)
        if scope is None and effect.applies is not False:
            self.template_values.pop(name, None)
            if (
                active is True and effect.applies is True and self.version == version
                and self.original_namespace_valid and template_value is not None
            ):
                if self.budget is not None:
                    self.budget.charge("cache", len(encoded((name, version, template_value))))
                self.template_values[name] = version, template_value
        return effect._replace(emitted=emitted, read_value=read_value)

    def assign_targets(self, assignment, *, override=False, active=True):
        target = assignment["target"].strip(MAKE_SPACE)
        if "$" in target:
            try:
                target = self.literal_text(target)
                if target is None:
                    target = self.exact_target_text(assignment["target"].strip(MAKE_SPACE))
            except RecursionError:
                target = None
        if self.original_execution is not None:
            return self.assign_original_scopes(assignment, target, override=override, active=active)
        if not target or any(character in target for character in "$%*?[]\\;|"):
            self.uncertain("unproven target-specific assignment context")
            return _AssignmentEffect(None, None)
        effects = []
        for scope in re.findall(r"[^ \t\r\n\v\f]+", target):
            self.checkpoint()
            effects.append(self.assign(
                assignment["name"], assignment["operator"], assignment["value"],
                override=override, active=active, scope=scope,
            ))
        result = _AssignmentEffect(*(
            effects[0][index] if all(effect[index] == effects[0][index] for effect in effects) else None
            for index in range(2)
        ))
        return result._replace(emitted=tuple(item for effect in effects for item in effect.emitted))

    def assign_original_scopes(self, assignment, target, *, override, active):
        name, operator = assignment["name"], assignment["operator"]
        modifiers = assignment[0][assignment.end("target"):assignment.start("name")].replace(":", " ").split()
        if override or "private" in modifiers:
            raise MakeProbeError("original execution has an unproven target/private binding: " + name)
        selectors = _scope_words(target)
        if (
            active is not True or not selectors
            or operator not in {"=", ":=", "::=", "+="}
            or next(computed_selectors(assignment["target"]), None) is not None
            or name in INVOCATION_CONTROL_READS | SOURCE_HISTORY_CONTROLS | {"MAKELEVEL"}
            or any(_scoped_references(assignment["value"]))
        ):
            raise MakeProbeError("unproven original scoped assignment context: " + name)
        effects = []
        for selector in selectors:
            self.checkpoint()
            if selector.startswith(".") and "/" not in selector:
                raise MakeProbeError("unproven scoped special-target assignment")
            if any(record.selector != selector and _scopes_overlap(record.selector, selector)
                   for record in self.scope_declarations):
                raise MakeProbeError("unproven overlapping target/pattern bindings: " + selector)
            declaration = _ScopeDeclaration(selector, name, operator, self.site, self.version)
            if self.budget is not None:
                if len(self.scope_declarations) >= self.budget.limits.observation_count:
                    raise MakeProbeError("scoped binding count exceeds existing observation bound")
                self.budget.charge("cache", len(encoded(declaration)))
            previous = self.raw_binding(name, selector)
            inherited = (
                (selector, name) in self.inherited_appends
                or operator == "+=" and all(value.flavor == "undefined" for value in previous)
            )
            context = _ScopeContext(None if "%" in selector else selector, selector, "assignment")
            with self.using_scope(context):
                if operator in SIMPLE_ASSIGNMENT_OPERATORS:
                    if self.effectful(assignment["value"]):
                        raise MakeProbeError("unproven original scoped immediate RHS: " + name)
                    value = self.literal_text(assignment["value"].lstrip(MAKE_SPACE))
                    if value is None:
                        value = self.exact_initializer_value(assignment["value"].lstrip(MAKE_SPACE))
                    if value is None:
                        raise MakeProbeError("unproven original scoped immediate RHS: " + name)
                effect = self.assign(
                    name, operator, assignment["value"], active=active, scope=selector,
                )
            if effect.applies is True:
                if operator != "+=":
                    self.inherited_appends.discard((selector, name))
                elif inherited:
                    self.inherited_appends.add((selector, name))
            self.scope_declarations.append(declaration)
            effects.append(effect)
        result = _AssignmentEffect(*(
            effects[0][index] if all(effect[index] == effects[0][index] for effect in effects) else None
            for index in range(2)
        ))
        return result._replace(
            emitted=tuple(item for effect in effects for item in effect.emitted),
            read_value=effects[0].read_value if all(effect.read_value == effects[0].read_value for effect in effects) else None,
        )


def _condition_operands(arguments):
    if arguments.startswith("("):
        depth, separator, closing = 0, None, None
        for index, character in enumerate(arguments[1:], 1):
            if character == "(":
                depth += 1
            elif character == ")":
                if depth == 0:
                    closing = index
                    break
                depth -= 1
            elif character == "," and depth == 0 and separator is None:
                separator = index
        if separator is None or closing is None or arguments[closing + 1:].strip(MAKE_SPACE):
            return None
        left = arguments[1:separator].rstrip(" \t")
        right = arguments[separator + 1:closing].lstrip(MAKE_SPACE)
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
    return left, right


def _literal_condition(keyword, arguments):
    if keyword not in {"ifeq", "ifneq"} or "$" in arguments:
        return None
    operands = _condition_operands(arguments)
    if operands is None:
        return None
    left, right = operands
    return (left == right) == (keyword == "ifeq")


def _same_make_assignment(left, right):
    def parsed(value):
        value = strip_comment(value)
        for kind, pattern in (("global", MODE_ASSIGNMENT), ("target", MODE_TARGET_ASSIGNMENT)):
            assignment = pattern.fullmatch(value)
            if assignment:
                return (
                    kind, value[:assignment.start("name")],
                    assignment["name"], assignment["operator"], assignment["value"].lstrip(MAKE_SPACE),
                )
        return None
    first, second = parsed(left), parsed(right)
    return first is not None and first == second


def _mode_and(left, right):
    return False if left is False or right is False else True if left is True and right is True else None


def _mode_not(value):
    return None if value is None else not value


def _include_names(header, mode=None):
    include = re.fullmatch(r"(?:-?include|sinclude)(?:[ \t\r\n\v\f]+(.*))?", header)
    if include is None:
        return False
    expression = include[1] or ""
    if "$" in expression:
        function = _make_function(expression)
        if (
            mode is not None and mode.namespace is not None and mode.original_namespace_valid
            and function is not None and function[0] == "wildcard" and len(function[1]) == 1
        ):
            try:
                literal = mode.literal_text(function[1][0])
            except RecursionError:
                return None
            if literal is not None and not any(character in literal for character in "$*?[]~\\#;:|"):
                paths = re.findall(r"[^ \t\r\n\v\f]+", literal)
                for path in paths:
                    mode.checkpoint()
                    relative_path(path)
                return [path for path in paths if path in mode.namespace]
        if mode is None:
            return None
        try:
            original_expression = expression
            expression = mode.literal_text(expression)
            if expression is None and mode.original_include_value is not None:
                expression = mode.original_include_value(mode, original_expression)
        except RecursionError:
            return None
        if expression is None:
            return None
    if any(character in expression for character in "*?[]~\\\r\n\v\f"):
        return None
    paths = re.findall(r"[^ \t]+", expression)
    for path in paths:
        if mode is not None:
            mode.checkpoint()
        relative_path(path)
    return paths


def _make_logical_chunks(text):
    pending = []
    start, logical = 1, 0
    lines = text.split("\n")
    for index, line in enumerate(lines):
        has_lf = index < len(lines) - 1
        if has_lf and line.endswith("\r"):
            line = line[:-1]
        pending.append(line)
        slashes = len(line) - len(line.rstrip("\\"))
        if has_lf and slashes % 2:
            continue
        logical += 1
        yield _LogicalChunk("\n".join(pending), logical, start, index + 1)
        pending = []
        start = index + 2
    if pending:
        yield _LogicalChunk("\n".join(pending), logical + 1, start, len(lines))


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


def _empty_restart_test(keyword, arguments):
    operands = _condition_operands(arguments)
    return (
        keyword == "ifeq" and operands is not None and operands[1] == ""
        and operands[0].strip(MAKE_SPACE) in {"$(MAKE_RESTARTS)", "${MAKE_RESTARTS}"}
    )


def _phase_recipe_text(mode, line, target):
    if target is None:
        return None
    command = line[1:].lstrip(" \t")
    if command.startswith("@"):
        command = command[1:]
    if command.startswith(("-", "+")):
        return None
    directory = str(PurePosixPath(target).parent)
    for spelling in ("$(dir $@)", "${dir $@}"):
        command = command.replace(spelling, directory + "/")
    command = command.replace("$(@D)", directory).replace("${@D}", directory)
    command = command.replace("$(@)", target).replace("${@}", target).replace("$@", target)
    if any(_scoped_references(command)):
        return None
    try:
        return mode.literal_text(command)
    except RecursionError:
        return None


def make_source_units(
    text, *, mode=None, include=None, known_context=True, source_path="<Make source>",
    binding_modules=(), provisional=False,
):
    """GNU logical lines and whole define bodies; recipes keep their escapes."""
    if "\0" in text:
        raise MakeProbeError("Make source contains an unsupported NUL byte")
    text = text.removeprefix("\ufeff")
    chunks = iter(_make_logical_chunks(text))
    mode = _MakeSourceMode() if mode is None else mode
    pending_posix = False
    conditions, active = [], True
    phase, phase_target = None, None
    source_rule, recipe_ordinal = None, 0

    def record_pending():
        nonlocal pending_posix
        if pending_posix is not False and active is not False:
            if pending_posix is True and active is True:
                mode.posix = True
            else:
                mode.uncertain("unproven generated target")
            if active is True:
                pending_posix = False

    def contextual_unit(line, body=None, assignment=None, emitted=(), created_bindings=()):
        nonlocal phase_target, source_rule, recipe_ordinal
        statement = strip_comment(line).strip(MAKE_SPACE)
        kind = (
            "recipe" if line.startswith("\t") else "define" if body is not None
            else "assignment" if assignment is not None
            else "directive" if MAKE_DIRECTIVE.match(statement)
            else "rule" if _rule_separators(statement) else "expression"
        )
        target, command = None, None
        if binding_modules and kind == "rule":
            header, _ = split_inline_recipe(statement)
            separators = _rule_separators(header)
            try:
                left = header[:separators[0]].strip(MAKE_SPACE) if len(separators) == 1 else None
                target = left if left is not None and "$" not in left else mode.literal_text(left) if left is not None else None
            except RecursionError:
                target = None
            if phase is not None:
                phase_target = target
        elif phase is not None and kind == "recipe":
            command = _phase_recipe_text(mode, line, phase_target)
        selected_rule, ordinal = None, None
        if mode.original_execution is not None and active is not False:
            if kind == "rule":
                source_rule = mode.source_rule(statement)
                recipe_ordinal = 0
                if split_inline_recipe(statement)[1].strip(MAKE_SPACE):
                    recipe_ordinal = 1
                    ordinal = recipe_ordinal
                selected_rule = source_rule
            elif kind == "recipe":
                if line[1:].strip(MAKE_SPACE):
                    recipe_ordinal += 1
                    ordinal = recipe_ordinal if active is True else None
                selected_rule = source_rule
            elif statement and not re.match(r"^(?:ifeq|ifneq|ifdef|ifndef|else|endif)(?:[ \t]|$)", statement):
                source_rule, recipe_ordinal = None, 0
        return MakeSourceUnit(
            line, body, conditional_depth=len(conditions), active=active, assignment=assignment, emitted=emitted, kind=kind,
            phase=phase, phase_target=target, phase_command=command, reads=tuple(sorted(mode.reads)),
            created_bindings=created_bindings,
            source_rule=selected_rule, recipe_ordinal=ordinal,
            site=mode.site if mode.original_execution is not None else None,
        )

    for chunk in chunks:
        mode.reads.clear()
        raw = chunk.text
        mode.site = _SourceSite(source_path, chunk.logical, chunk.start, chunk.end)
        mode.checkpoint()
        include_request = None
        line = raw if raw.startswith("\t") else mode.collapse(raw, construct=True)
        declaration = strip_comment(line)
        header = declaration.strip(MAKE_SPACE)
        assignment = None if raw.startswith("\t") else MODE_ASSIGNMENT.fullmatch(declaration)
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
            phase_test = bool(
                binding_modules and not conditions and active is True and known_context
                and _empty_restart_test(keyword, arguments)
            )
            if phase_test:
                phase = tuple(mode.site)
            if keyword == "else":
                phase_target = None
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
                        choice = None if eligible is False else mode.condition(keyword, arguments)
                    else:
                        choice = True
                        conditions[-1][2] = True
                    conditions[-1][1] = _mode_not(_mode_and(_mode_not(seen), _mode_not(choice)))
                    active = _mode_and(eligible, choice)
                else:
                    eligible = active
                    choice = None if eligible is False else mode.condition(keyword, arguments)
                    conditions.append([active, choice, False])
                    active = _mode_and(active, choice)
                if eligible is not False and not phase_test and mode.effectful(arguments):
                    mode.uncertain()
            # The predicate is read in its enclosing context, even when its body is skipped.
            yield contextual_unit(line)._replace(active=active if keyword == "endif" else eligible)
            if keyword == "endif" and not conditions:
                phase, phase_target = None, None
            continue
        if header and not raw.startswith("\t"):
            # GNU collapses this line before recording the preceding rule.
            record_pending()
        definition = DEFINE.match(header) if not raw.startswith("\t") and not assignment else None
        if definition:
            header_site = mode.site
            body, depth = [], 1
            for body_chunk in chunks:
                mode.site = _SourceSite(source_path, body_chunk.logical, body_chunk.start, body_chunk.end)
                part = mode.collapse(body_chunk.text)
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
            mode.site = header_site._replace(end=body_chunk.end)
            body = "\n".join(body)
            effect = mode.assign(
                definition[1], header[definition.end():].strip(MAKE_SPACE) or "=", body,
                override="override" in header[:definition.start(1)].split(), active=active, literal_body=True,
            )
            yield contextual_unit(line, body, effect)
        else:
            effect = None
            emitted = ()
            created_bindings = ()
            if not raw.startswith("\t") and active is not False:
                if assignment:
                    effect = mode.assign(
                        assignment["name"], assignment["operator"], assignment["value"],
                        override="override" in declaration[:assignment.start("name")].split(),
                        active=_mode_and(active, None) if provisional else active,
                    )
                elif MODE_TARGET_ASSIGNMENT.fullmatch(declaration):
                    target_assignment = MODE_TARGET_ASSIGNMENT.fullmatch(declaration)
                    effect = mode.assign_targets(
                        target_assignment,
                        override="override" in declaration[target_assignment.end("target"):target_assignment.start("name")].split(),
                        active=active,
                    )
                else:
                    undefined = re.fullmatch(r"(override[ \t]+)?undefine[ \t]+(" + IDENTIFIER + ")", header)
                    if undefined:
                        mode.assign(undefined[2], "undefine", "", override=bool(undefined[1]), active=active)
                    if _unproven_assignment_destination(header):
                        mode.uncertain()
                    function = _make_function(header)
                    neutral_template = bool(
                        mode.template_mode is not None and active is True and known_context
                        and mode.template_mode(mode, header)
                    )
                    empty_result = neutral_template or function is not None and function[0] in EMPTY_RESULT_FUNCTIONS
                    target_posix = (
                        mode.target_posix(header) if not empty_result and not MAKE_DIRECTIVE.match(header) else False
                    )
                    source_header = split_inline_recipe(header)[0] if mode.original_execution is not None else header
                    emitted = () if neutral_template else mode.evaluate(source_header, active=active)
                    if mode.original_execution is not None:
                        created_bindings = mode.export_declarations(source_header, active)
                    included = _include_names(header, mode)
                    if included is not False:
                        if include is None:
                            mode.uncertain()
                        else:
                            include_request = (included, active, mode)
                    else:
                        if target_posix is None:
                            pending_posix = None
                        elif target_posix:
                            if mode.posix is not True and (active is None or not known_context):
                                raise MakeProbeError("unproven conditional/include .POSIX activation")
                            pending_posix = True
            yield contextual_unit(line, assignment=effect, emitted=emitted, created_bindings=created_bindings)
            if include_request is not None:
                include(*include_request)
    if conditions:
        raise MakeProbeError("Make parsing-mode context has an unterminated conditional")
    record_pending()


def _source_units(
    sources, *, assignments=(), budget=None, target=None, original_input=None, namespace=None,
    read_order=None, remade=False, native_exports=(), literal_modules=(), template_mode=None,
    original_target_value=None,
    original_wildcard=None,
    native_pass=None, admitted_missing=frozenset(), original_forced=(),
    original_include_value=None, original_execution=None, original_invocation_inputs=(),
):
    decoded = {}
    mode = _MakeSourceMode(
        definitions={name: value for _, name, value in assignments},
        forced=frozenset(name for origin, name, _ in assignments if origin == "command-line") | frozenset(original_forced),
        budget=budget,
        original_input=original_input,
        namespace=namespace,
        template_mode=template_mode,
        original_target_value=original_target_value,
        original_wildcard=original_wildcard,
        original_include_value=original_include_value,
        original_execution=original_execution,
        invocation_inputs=frozenset(original_invocation_inputs),
    )
    if target is not None:
        mode.bind_invocation(target)
    units, reading, unresolved_modes = {}, set(), []
    ordered, known_positions = [], set()
    visits, read_sources, failed_sources, native_parents = [], [], [], []
    native_index = 0
    unproven_include = False

    def visit(path, *, known=True):
        nonlocal native_index
        mode.checkpoint()
        if path in reading:
            raise MakeProbeError("Make include parsing-mode context is recursive")
        native = None
        if native_pass is not None:
            if native_index >= len(native_pass.visits):
                raise MakeProbeError("source interpretation has an unobserved native include")
            native = native_pass.visits[native_index]
            native_index += 1
            if (
                native.name.removeprefix("/repo/") != path
                or native.resolved.removeprefix("/repo/") != path
                or native.parent != (native_parents[-1] if native_parents else None)
            ):
                raise MakeProbeError("source interpretation differs from actual native visit/parent order")
            if native.error:
                if native.number not in admitted_missing or native.error != 2 or native.source is not None:
                    raise MakeProbeError("source interpretation cannot omit an unproven failed include")
                if budget is not None:
                    budget.charge("cache", len(encoded((path, native.number, native.error))))
                visits.append(path)
                failed_sources.append(path)
                return
            if native.source is None:
                raise MakeProbeError("successful source interpretation has no actual source bytes")
            try:
                decoded[path] = native.source.data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise MakeProbeError(f"Make census source is not UTF-8: {path}") from error
        elif path not in decoded:
            try:
                decoded[path] = sources[path].decode("utf-8")
            except UnicodeDecodeError as error:
                raise MakeProbeError(f"Make census source is not UTF-8: {path}") from error
        previous = units.get(path) if native is None else None
        if previous is None:
            units[path] = []
        reading.add(path)
        if budget is not None:
            budget.charge("cache", len(encoded(path)))
        visits.append(path)
        read_sources.append(path)
        if native is not None:
            native_parents.append(native.number)

        def included(names, active, current_mode):
            nonlocal unproven_include
            if names is None:
                unresolved_modes.append(current_mode.posix)
                current_mode.uncertain("unproven include outcome")
                unproven_include = True
                return
            if active is None and names:
                current_mode.uncertain("unproven include condition")
                unproven_include = True
            for name in names:
                if name in sources or native_pass is not None:
                    visit(name, known=known and active is True)
                else:
                    current_mode.uncertain("unobserved included source")
                    unproven_include = True

        count = 0
        # Revisit the original inputs; reuse only the identical source units,
        # never the effects of a previous include invocation.
        for unit in make_source_units(
            decoded[path], mode=mode, include=included, known_context=known, source_path=path,
            binding_modules=literal_modules, provisional=any(module.path == path for module in literal_modules),
        ):
            if previous is None:
                units[path].append(unit)
            elif count >= len(previous) or unit[:3] != previous[count][:3]:
                raise MakeProbeError("Make include source changed across original read contexts")
            position = len(ordered)
            if budget is not None:
                budget.charge("cache", len(encoded((path, position, unit))))
            if known:
                known_positions.add(position)
            ordered.append((path, position, unit))
            count += 1
        if previous is not None and count != len(previous):
            raise MakeProbeError("Make include source changed across original read contexts")
        reading.remove(path)
        if native is not None:
            native_parents.pop()

    if sources:
        visit(next(iter(sources)))
    if unproven_include:
        raise MakeProbeError("unproven original include outcome or source history")
    if native_pass is not None and native_index != len(native_pass.visits):
        raise MakeProbeError("source interpretation omitted an actual native visit")
    if read_order is not None and tuple(visits) != tuple(read_order):
        raise MakeProbeError("original include traversal differs from native MAKEFILE_LIST")
    if read_order is None:
        for path in sources:
            if path not in units:
                mode.posix = True if unresolved_modes and all(value is True for value in unresolved_modes) else None
                visit(path, known=False)
    return _SourceUnitStream(
        tuple(ordered), frozenset(known_positions), remade, tuple(read_sources), native_exports, literal_modules,
        mode_state=mode if native_pass is not None else None, failed_sources=tuple(failed_sources),
    )


def _ordered_source_units(units):
    if not isinstance(units, _SourceUnitStream):
        raise MakeProbeError("semantic traversal requires the original source occurrence stream")
    ordered, known_positions = [], set()
    for position, occurrence in enumerate(units.ordered):
        if occurrence[2].active is False:
            continue
        if position in units.known_positions:
            known_positions.add(len(ordered))
        ordered.append(occurrence)
    return tuple(ordered), frozenset(known_positions)


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
            elif staged or strict_dollars:
                raise _UnresolvedName("incomplete or unsupported dollar token")
        index += 1
    if (staged or require_complete) and stack:
        raise _UnresolvedName("incomplete dollar-bearing Make expression")


def make_expressions(line):
    for _, _, body in _make_expression_spans(line):
        yield body


def _scoped_references(line):
    for _, _, body in _make_expression_spans(line, short=True):
        name = _make_reference_base(body)
        if SCOPED.fullmatch("$(" + name + ")"):
            yield name


def _without_literal_metadata(expression):
    spans = [(start, stop) for start, stop, _ in _literal_metadata(expression)]
    for start, stop in sorted(spans, reverse=True):
        expression = expression[:start] + expression[stop:]
    return expression


def _literal_metadata(expression):
    for start, stop, body in _make_expression_spans(expression):
        match = re.fullmatch(r"(?:origin|flavor|value)[ \t\r\n\v\f]+(" + IDENTIFIER + ")", body)
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
    separators, slashes = [], 0
    for index, char in enumerate(header):
        if char == ":" and not slashes % 2 and not any(start <= index < stop for start, stop in spans):
            separators.append(index)
        slashes = slashes + 1 if char == "\\" else 0
    return separators


def _make_target_words(value):
    """The proven filename subset after target expansion, not a shell lexer."""
    if any(character in value for character in "\r\n\v\f"):
        return None
    words, word, index = [], [], 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            following = value[index + 1]
            if following in " \t:\\*?[%":
                word.append(following)
                index += 2
                continue
        if character in " \t":
            if word:
                words.append("".join(word))
                word = []
        elif character in ":;=*?[~#()":
            return None
        else:
            word.append(character)
        index += 1
    if word:
        words.append("".join(word))
    result = []
    for word in words:
        while word.startswith("./"):
            word = word[2:].lstrip("/")
        if word.startswith("/") or ".." in word.split("/"):
            return None
        result.append(word)
    return result


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


def _literal_binding_phases(units, observation, budget):
    ordered, known = _ordered_source_units(units)
    groups = {}
    for position, (path, _, unit) in enumerate(ordered):
        budget.remaining()
        if unit.phase is not None and strip_comment(unit.text).strip(MAKE_SPACE):
            groups.setdefault(unit.phase, []).append((position, unit))
    events = tuple(_event_command(event) for event in observation.events)
    matched, tests = set(), set()
    for phase, group in groups.items():
        rows = [unit for _, unit in group]
        headers = [strip_comment(unit.text).strip(MAKE_SPACE) for unit in rows]
        if (
            group[0][0] not in known or rows[0].conditional_depth != 1
            or headers[-1] != "endif" or headers.count("else") != 1
        ):
            raise MakeProbeError("unproven literal binding restart guard")
        middle = headers.index("else")
        first, later = rows[1:middle], rows[middle + 1:-1]
        if (
            len(first) < 2 or first[0].kind != "rule"
            or any(unit.kind != "recipe" or unit.phase_command is None for unit in first[1:])
            or len(later) != 1 or later[0].kind != "rule"
        ):
            raise MakeProbeError("literal binding phase must contain one producer rule and empty alternative")
        target = first[0].phase_target
        candidates = [module for module in units.literal_modules if module.path == target]
        if len(candidates) != 1 or later[0].phase_target != target:
            raise MakeProbeError("literal binding phase does not own the selected generated target")
        module = candidates[0]
        first_header, first_inline = split_inline_recipe(first[0].text)
        later_header, later_inline = split_inline_recipe(later[0].text)
        later_separators = _rule_separators(later_header)
        if (
            first_inline or later_inline or len(later_separators) != 1
            or later_header[later_separators[0] + 1:].strip(MAKE_SPACE)
        ):
            raise MakeProbeError("literal binding later phase is not recipe-less")
        commands = []
        for unit in first[1:]:
            normalized = _normalized_shell_commands(unit.phase_command, "literal binding phase")
            if len(normalized) != 1:
                raise MakeProbeError("literal binding phase command is not a proven single command")
            commands.append(normalized[0])
        native = []
        for command in events:
            normalized = _normalized_shell_commands(command, "native literal binding phase")
            native.append(normalized[0] if len(normalized) == 1 else None)
        publisher = _normalized_shell_commands(module.commands[0], "literal binding publisher")
        if len(publisher) != 1 or publisher[0] not in commands:
            raise MakeProbeError("literal binding phase omits its actual publication")
        starts = [offset for offset in range(len(native) - len(commands) + 1)
                  if native[offset:offset + len(commands)] == commands]
        if len(starts) != 1 or any(native.count(command) != 1 for command in commands):
            raise MakeProbeError("literal binding phase differs from actual producer dispatch")
        if target in matched:
            raise MakeProbeError("literal binding target has multiple phase guards")
        matched.add(target)
        tests.add(phase)
        for _, _, unit in ordered:
            if unit.kind != "rule" or unit.phase == phase:
                continue
            if unit.phase_target is None:
                raise MakeProbeError("literal binding output has unresolved original rule ownership")
            targets = _make_target_words(unit.phase_target)
            if targets is None or any("%" in name for name in targets) or target in targets:
                raise MakeProbeError("literal binding output has another possible rule owner")
    if matched != {module.path for module in units.literal_modules}:
        raise MakeProbeError("literal binding module lacks a proven first-pass-only guard")
    budget.charge("cache", len(encoded((sorted(matched), sorted(tests)))))
    return frozenset(tests)


def _template_header_data(value):
    return not any(
        character in "$\\#;:=|" or ord(character) < 32 and character != "\t" for character in value
    )


def _template_wildcard_bound(pattern, namespace, budget=None):
    if budget is not None:
        budget.remaining()
    if not _template_header_data(pattern) or any(character in pattern for character in MAKE_SPACE + "[]~"):
        return None
    try:
        relative_path(pattern)
    except MakeProbeError:
        return None
    parent = PurePosixPath(pattern).parent
    if any(character in str(parent) for character in "*?"):
        return None
    for name in namespace:
        if budget is not None:
            budget.remaining()
        if PurePosixPath(name).parent == parent and not _template_header_data(name):
            return None
    return "header-bound", (pattern,)


def _binding_reads(unit):
    ordinary = unit.active is True and unit.body is None
    if unit.text.startswith("\t"):
        context = "ordinary-recipe" if ordinary and unit.kind == "recipe" else "staged"
        yield _BindingRead(unit.text, context)
    else:
        header, inline = split_inline_recipe(unit.text)
        yield _BindingRead(header, header=True)
        if inline:
            context = "ordinary-recipe" if ordinary and unit.kind == "rule" else "staged"
            yield _BindingRead(inline, context)
    if unit.body is not None:
        yield _BindingRead(unit.body)


def _foreach_read_bindings(expression, budget=None, *, context="staged"):
    """Return possible local names, or no closed global-literal context."""
    if context not in {"staged", "ordinary-recipe"}:
        raise MakeProbeError("unproven local-binding expansion context")
    staged = context != "ordinary-recipe"
    names = set()
    try:
        if not staged:
            # A reparse or opaque invocation can activate escaped operations.
            # Keep the old conservative scan for that entire expression.
            for _, _, body in _make_expression_spans(
                expression, require_complete=True, strict_dollars=True,
            ):
                if budget is not None:
                    budget.remaining()
                if re.match(r"(?:eval|call|guile)[ \t\r\n\v\f]", body):
                    staged = True
                    break
        for start, stop, body in _make_expression_spans(
            expression, staged=staged, require_complete=True, strict_dollars=True,
        ):
            if budget is not None:
                budget.remaining()
            if not re.match(r"foreach[ \t\r\n\v\f]", body):
                continue
            try:
                function = _make_function(expression[start:stop])
            except MakeProbeError:
                return None
            if function is None or len(function[1]) != 3:
                return None
            name = function[1][0].strip(MAKE_SPACE)
            if not re.fullmatch(IDENTIFIER, name):
                return None
            if name not in names and budget is not None:
                budget.charge("cache", len(encoded(name)))
            names.add(name)
    except _UnresolvedName:
        return None
    return names


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


def _resolve_make_text(expression, resolve, budget):
    try:
        spans = []
        for span in _make_expression_spans(expression, require_complete=True, short=True):
            if budget is not None:
                budget.charge("cache", len(encoded(span)))
            spans.append(span)
    except _UnresolvedName:
        return None

    def parts():
        previous = 0
        for start, stop, body in sorted(spans, key=lambda item: (item[0], -item[1])):
            if start < previous:
                continue
            literal = expression[previous:start]
            if "$" in literal or "\0" in literal:
                yield None
                return
            yield literal
            yield resolve(expression[start:stop], body)
            previous = stop
        suffix = expression[previous:]
        yield None if "$" in suffix or "\0" in suffix else suffix

    return _join_make_text(parts(), budget)


def _supported_patsubst(pattern, replacement):
    return bool(
        pattern and pattern.count("%") <= 1 and replacement.count("%") <= 1
        and all(re.fullmatch(r"[A-Za-z0-9_./%+-]*", value) for value in (pattern, replacement))
    )


def _original_filter_patterns(value, budget):
    patterns = []
    for match in re.finditer(r"[^ \t\r\n\v\f]+", value):
        if budget is not None:
            budget.remaining()
        pattern = match[0]
        if not _supported_patsubst(pattern, ""):
            return None
        if budget is not None:
            budget.charge("cache", len(encoded(pattern)))
        patterns.append(pattern)
    return patterns


def _patsubst_word(pattern, word):
    if "%" not in pattern:
        return word == pattern
    prefix, suffix = pattern.split("%")
    return word.startswith(prefix) and word.endswith(suffix) and len(word) >= len(prefix) + len(suffix)


def _original_patsubst_parts(arguments, budget):
    if budget is not None:
        budget.remaining()
    pattern, replacement, words = arguments
    if not pattern:
        raise MakeProbeError("empty pattern lacks an original substitution proof")
    if "%" not in pattern:
        previous = 0
        for match in re.finditer(r"[^ \t\r\n\v\f]+", words):
            if budget is not None:
                budget.remaining()
            yield words[previous:match.start()]
            yield replacement if match[0] == pattern else match[0]
            previous = match.end()
        yield words[previous:]
        return
    first = True
    for match in re.finditer(r"[^ \t\r\n\v\f]+", words):
        if budget is not None:
            budget.remaining()
        word = match[0]
        prefix, suffix = pattern.split("%")
        matched = _patsubst_word(pattern, word)
        stem = word[len(prefix):len(word) - len(suffix) if suffix else len(word)]
        parts = (word,)
        if matched and "%" in replacement:
            before, after = replacement.split("%")
            parts = before, stem, after
        elif matched:
            parts = (replacement,)
        if matched and not replacement:
            continue
        if not first:
            yield " "
        first = False
        yield from parts


def _matches_original_patsubst(arguments, candidate, budget):
    """Check a native claim against already-bound literal initializer arguments."""
    budget.remaining()
    if not arguments[0]:
        return False
    offset = 0
    for part in _original_patsubst_parts(arguments, budget):
        if not candidate.startswith(part, offset):
            return False
        offset += len(part)
    return offset == len(candidate)


def _rule_template_call(expression):
    function = _make_function(expression)
    if function is None or function[0] != "foreach" or len(function[1]) != 3:
        return None
    variable, values, action = function[1]
    evaluated = _make_function(action)
    invoked = _make_function(evaluated[1][0]) if evaluated and evaluated[0] == "eval" and len(evaluated[1]) == 1 else None
    if invoked is None or invoked[0] != "call":
        return None
    arguments = [
        argument.strip(MAKE_SPACE) if index == 0 else argument
        for index, argument in enumerate(invoked[1])
    ]
    variable = variable.strip(MAKE_SPACE)
    if (
        not re.fullmatch(IDENTIFIER, variable) or len(arguments) != 2
        or arguments[1] not in {"$(" + variable + ")", "${" + variable + "}"}
        or not re.fullmatch(IDENTIFIER, arguments[0]) or arguments[0] in MAKE_FUNCTIONS
    ):
        raise MakeProbeError("unproven parameterized rule-template invocation")
    return variable, values.strip(MAKE_SPACE), arguments[0]


def _charge_template_expansion(budget, body, word, recipe_count):
    parameter_count = body.count("$(1)") + body.count("${1}")
    budget.charge("cache", len(encoded(body)) + parameter_count * (len(word) - 4) + 10 * (recipe_count + 1))


class _TemplateModeProof:
    def __init__(self, session, target, state, commands, observation, primary_source, observe_dispatch):
        self.session, self.target, self.state = session, target, state
        self.commands, self.observation = commands, observation
        self.primary_source, self.observe_dispatch = primary_source, observe_dispatch
        self.records = {}

    def native_value(self, name):
        if name not in self.records:
            actual = self.session.make(
                self.target, makefile=self.primary_source, definitions=(name,), assignments=self.state,
                commands=self.commands, observe_recipe_dispatch=self.observe_dispatch,
            )
            _stable_native_context(self.observation.semantics, actual.semantics)
            records = actual.semantics["definitions"]["global"]
            self.session.budget.charge("cache", len(encoded(records)))
            self.records.update(records)
        return self.records[name]

    def value(self, mode, name, *, active=()):
        mode.checkpoint()
        if not mode.original_namespace_valid or name in active or len(active) >= 512:
            return None
        mode.retain_reads((name,))
        bindings = mode.binding(name)
        if len(bindings) != 1:
            return None
        binding = next(iter(bindings))
        try:
            literal = mode.literal_text("$(" + name + ")")
        except RecursionError:
            return None
        if literal is not None:
            return literal
        if binding.flavor == "recursive" and binding.value is not None:
            return mode.exact_reference(name, active)
        record = mode.template_values.get(name)
        if binding.flavor != "simple" or record is None or record[0] != mode.version:
            return None
        kind, arguments = record[1]
        if kind == "exact":
            return arguments
        if kind != "patsubst":
            return None
        native = self.native_value(name)
        if native["origin"] != binding.origin or native["flavor"] != "simple":
            return None
        if _matches_original_patsubst(arguments, native["value"], self.session.budget):
            return native["value"]
        return None

    def header_data(self, mode, name, active=()):
        mode.checkpoint()
        if name in active or len(active) >= 512:
            return False
        value = self.value(mode, name)
        if value is not None:
            return _template_header_data(value)
        bindings = mode.binding(name)
        if len(bindings) != 1:
            return False
        binding = next(iter(bindings))
        if binding.flavor == "recursive" and binding.value is not None:
            forwarded = NAME_PART.fullmatch(binding.value)
            if forwarded:
                return self.header_data(mode, forwarded[1] or forwarded[2], (*active, name))
        record = mode.template_values.get(name)
        return bool(
            binding.flavor == "simple" and record is not None and record[0] == mode.version
            and record[1][0] == "header-bound"
        )

    def text(self, mode, expression):
        result = expression
        for match in reversed(list(NAME_PART.finditer(expression))):
            value = self.value(mode, match[1] or match[2])
            if value is None:
                return None
            result = _join_make_text((result[:match.start()], value, result[match.end():]), self.session.budget)
        return None if "$" in result else result

    def retain_call(self, mode, expression, macro, words):
        pass

    def __call__(self, mode, expression):
        self.session.budget.remaining()
        if mode.posix is None or not mode.original_namespace_valid:
            return False
        call = _rule_template_call(expression)
        if call is None:
            return False
        variable, values, macro_name = call
        macro = mode.binding(macro_name)
        if len(macro) != 1:
            return False
        macro = next(iter(macro))
        if macro.origin not in {"file", "override"} or macro.flavor != "recursive" or macro.value is None:
            return False
        parameter = self.text(mode, values.strip(MAKE_SPACE))
        if parameter is None:
            return False
        words = []
        for match in re.finditer(r"[^ \t\r\n\v\f]+", parameter):
            mode.checkpoint()
            if len(words) >= 512:
                raise MakeProbeError("original template parameters exceed the existing context bound")
            words.append(match[0])
        if any(not re.fullmatch(IDENTIFIER, word) for word in words):
            return False
        self.session.budget.charge("cache", len(encoded(macro.value)))
        left, right, recipes = _rule_template_parts(macro.value)
        for word in words:
            mode.checkpoint()
            _charge_template_expansion(self.session.budget, macro.value, word, len(recipes))
            substitute = lambda value: value.replace("$(1)", word).replace("${1}", word)
            target = self.text(mode, substitute(left))
            if target is None:
                return False
            target = target.strip(MAKE_SPACE)
            targets = _make_target_words(target)
            if (
                targets is None or len(targets) != 1 or targets[0].startswith(".")
                or any(character in target for character in MAKE_SPACE + "%*?[]")
                or not _template_header_data(target)
            ):
                return False
            relative_path(target)
            prerequisite = substitute(right)
            names = _template_reference_names(prerequisite)
            for name in names:
                if name == variable or not self.header_data(mode, name):
                    return False
            for body in make_expressions(prerequisite):
                wildcard = re.fullmatch(r"wildcard[ \t]+(.*)", body, re.S)
                if wildcard:
                    pattern = self.text(mode, wildcard[1])
                    if pattern is None or mode.namespace is None or _template_wildcard_bound(
                        pattern, mode.namespace, self.session.budget,
                    ) is None:
                        return False
            for recipe in recipes:
                for name in _template_reference_names(substitute(recipe), recipe=True):
                    value = self.value(mode, name)
                    if name == variable or value is None or any(
                        character == "$" or ord(character) < 32 and character != "\t" for character in value
                    ):
                        return False
        self.session.budget.charge("cache", len(encoded((mode.site, mode.version, mode.posix, macro_name, words))))
        self.retain_call(mode, expression, macro, words)
        return True


def _prepare_rule_templates(
    session, target, state, commands, observation, sources, *, primary_source,
    observe_dispatch=False, external_names=(), phase=None,
):
    if phase is None:
        read_order, remade, native_exports, literal_modules = _native_include_context(
            session, observation, sources, state, primary_source=primary_source, commands=commands,
        )
    else:
        read_order = tuple(visit.name.removeprefix("/repo/") for visit in phase.part.visits)
        remade, native_exports, literal_modules = False, phase.exports, ()
    original_inputs = {
        name: {"origin": "undefined", "flavor": "undefined", "value": ""}
        for module in literal_modules for name, _ in module.bindings
    }
    has_empty_witness = any(not value for value in session.snapshot.files.values())

    def original_input(name):
        if phase is not None:
            return phase.input(name)
        if name in INVOCATION_CONTROL_READS | {"MAKEFILE_LIST", "MAKE_RESTARTS", "MAKELEVEL"} or not has_empty_witness:
            return None
        if name not in original_inputs:
            original_inputs.update(session.original_make_inputs(target, (name,), assignments=state))
        return original_inputs[name]

    namespace = (
        set(session.snapshot.files) | {item.path for item in observation.generated}
        if phase is None else set(phase.namespace)
    )
    namespace.update(parent.as_posix() for name in tuple(namespace) for parent in PurePosixPath(name).parents if parent.as_posix() != ".")
    namespace.update(session.snapshot.gitlink_roots)
    session.budget.charge("cache", len(encoded(sorted(namespace))))
    if phase is None:
        template_mode = _TemplateModeProof(session, target, state, commands, observation, primary_source, observe_dispatch)
        original_namespace = session._original_namespace(
            observation, target=target, makefile=primary_source, assignments=state,
        )
        wildcard = lambda patterns: session._original_wildcard(original_namespace, patterns)
    else:
        template_mode = phase.template_mode()
        wildcard = phase.wildcard
    units = _source_units(
        sources, assignments=state if phase is None else (), budget=session.budget, target=target,
        original_input=original_input, namespace=frozenset(namespace),
        read_order=read_order, remade=remade, native_exports=native_exports, literal_modules=literal_modules,
        template_mode=template_mode,
        original_target_value=template_mode.text,
        original_wildcard=wildcard,
        native_pass=None if phase is None else phase.part,
        admitted_missing=frozenset() if phase is None else phase.missing,
        original_forced=() if phase is None else phase.forced,
        original_include_value=None if phase is None else template_mode.text,
        original_execution=None if phase is None else phase.record_execution,
        original_invocation_inputs=() if phase is None else tuple(name for _, name, _ in phase.state),
    )
    if literal_modules:
        units = units._replace(phase_tests=_literal_binding_phases(units, observation, session.budget))
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
        call = _rule_template_call(header) if unit.body is None and not header.startswith("\t") else None
        if call is None:
            continue
        variable, values, macro_name = call
        candidates = macros.get(macro_name, ())
        if len(candidates) != 1 or positions[candidates[0][0], candidates[0][1]] >= positions[path, index]:
            raise MakeProbeError("rule template lacks one original prior definition")
        callers.append((path, index, variable, values, candidates[0]))
    if not callers:
        return units, set(), set()
    session.budget.charge("cache", len(encoded([
        (path, index, unit.text, unit.body) for path, index, unit in ordered
    ])))
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
    globals_seen = template_mode.records
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
        if phase is not None:
            raise MakeProbeError("original per-pass templates cannot query terminal Make values")
        pending = sorted(set(names) - set(globals_seen))
        for offset in range(0, len(pending), 512):
            actual = session.make(
                target, makefile=primary_source, definitions=tuple(pending[offset:offset + 512]),
                assignments=state, commands=commands, observe_recipe_dispatch=observe_dispatch,
            )
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
        caller_site = ordered[position][2].site
        if position not in known_positions:
            raise MakeProbeError("rule-template caller lacks proven original source ordering")
        macro_name = DEFINE.match(strip_comment(macro.text))[1]
        if assignments.get(macro_name):
            raise MakeProbeError("rule-template macro has an additional assignment history")
        if phase is not None:
            original = template_mode.original_call(caller_site, ordered[position][2].text)
            if original.macro_name != macro_name or original.macro.value != macro.body:
                raise MakeProbeError("rule-template caller differs from its original source definition")
            replacements[path, index] = original.units
            graph_inputs.update(original.inputs)
            scoped.update(original.scoped)
            omitted.add((macro_path, macro_index))
            continue
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
            _charge_template_expansion(session.budget, macro.body, word, len(recipes))
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
            proved_header = target_text + ":" + prerequisite_text
            instantiated.append(MakeSourceUnit(
                proved_header, native_literal_header=True, site=caller_site,
            ))
            instantiated.extend(
                MakeSourceUnit(recipe.replace("$$", "$"), recipe_ordinal=ordinal, site=caller_site)
                for ordinal, recipe in enumerate(recipe_texts, 1)
            )
            scoped.add("1")
        replacements[path, index] = instantiated
        omitted.add((macro_path, macro_index))
        scoped.add(variable)
    if phase is not None:
        template_mode.require_complete()
    prepared, prepared_known = [], set()
    for position, (path, index, unit) in enumerate(ordered):
        replacement_units = [] if (path, index) in omitted else replacements.get((path, index), (unit,))
        for replacement in replacement_units:
            replacement = replacement._replace(
                conditional_depth=unit.conditional_depth, active=unit.active, reads=unit.reads,
                created_bindings=unit.created_bindings,
            )
            session.budget.charge("cache", len(encoded((path, len(prepared), replacement))))
            if position in known_positions:
                prepared_known.add(len(prepared))
            prepared.append((path, len(prepared), replacement))
    return _SourceUnitStream(
        tuple(prepared), frozenset(prepared_known), units.remade, units.read_sources, units.native_exports,
        units.literal_modules, units.phase_tests,
        units.mode_state, units.failed_sources,
    ), graph_inputs, scoped


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


def computed_selectors(line, *, budget=None):
    for _, _, body in _make_expression_spans(line, budget=budget):
        call = re.match(r"call[ \t]+", body)
        head = _make_reference_base(body[call.end():] if call else body, call=bool(call))
        if "$" in head:
            yield head


def selected_names(expressions, definitions, observed_values, *, unresolved=None, budget=None):
    if budget is not None:
        budget.remaining()
    completed = {}

    def checkpoint():
        if budget is not None:
            budget.remaining()

    def refuse(message):
        if budget is not None:
            budget.reject(message)
        raise MakeProbeError(message)

    def admit(size):
        if budget is not None:
            budget.charge("cache", size)

    def scan(value):
        checkpoint()
        limit = Limits.file_bytes if budget is None else budget.limits.file_bytes
        if len(value) > limit:
            refuse("computed selector exceeds the existing input byte bound")
        # Admit Unicode/encoding workspace before scanning or retaining spans.
        admit(64 + 12 * len(value))
        if not value.isascii() and len(value.encode("utf-8")) > limit:
            refuse("computed selector exceeds the existing input byte bound")

    def bounded_value(parts):
        checkpoint()
        size = sum(len(part) for part in parts)
        if size > 128:
            refuse("computed selector exceeds the public Make name length bound")
        admit(64 + 12 * size)
        return "".join(parts)

    def retain(destination, value):
        checkpoint()
        if value not in destination:
            if len(destination) >= 512:
                refuse("computed selector exceeds the existing bounded context plan")
            admit(64)
            destination.add(value)

    def native_constant(declarations):
        for value in declarations:
            checkpoint()
            if value != declarations[0]:
                return False
        value = declarations[0]
        spans = list(_make_expression_spans(value))
        if len(spans) != 1 or spans[0][:2] != (0, len(value)):
            return False
        operation = re.fullmatch(r"(?:subst|patsubst)[ \t]+([^$]*)", spans[0][2], re.S)
        return operation is not None

    def expand(template, active):
        scan(template)
        values, offset = {""}, 0
        for match in NAME_PART.finditer(template):
            checkpoint()
            literal = template[offset:match.start()]
            if "$" in literal:
                raise _UnresolvedName("computed selector contains an unsupported name expression")
            name = match[1] or match[2]
            if name in active:
                raise _UnresolvedName("computed selector has a cyclic name definition")
            if len(name) > 128:
                refuse("computed selector exceeds the public Make name length bound")
            if len(active) >= 512:
                refuse("computed selector exceeds the existing reference depth bound")
            admit(64 + 12 * len(name) + 8 * len(active))
            key = name, active
            choices = completed.get(key)
            if choices is None:
                declarations = definitions.get(name)
                choices = set()
                for value in observed_values.get(name, ()):
                    retain(choices, bounded_value((value,)))
                if declarations:
                    for value in declarations:
                        checkpoint()
                        if value is None:
                            raise _UnresolvedName("computed selector has an unresolved source definition")
                    admit(256 + 64 * (len(active) + 1))
                    nested = active | {name}
                    try:
                        for value in declarations:
                            for choice in expand(value, nested):
                                retain(choices, choice)
                    except _UnresolvedName:
                        if not choices or not native_constant(declarations):
                            raise
                elif not choices:
                    raise _UnresolvedName("computed selector lacks a closed literal or finite name definition")
                admit(256 + 64 * len(choices))
                completed[key] = choices = frozenset(choices)
            combined = set()
            for prefix in values:
                for choice in choices:
                    retain(combined, bounded_value((prefix, literal, choice)))
            values, offset = combined, match.end()
        if "$" in template[offset:]:
            raise _UnresolvedName("computed selector contains an unsupported name expression")
        suffix = template[offset:]
        combined = set()
        for value in values:
            retain(combined, bounded_value((value, suffix)))
        return combined

    selected = set()
    for expression in expressions:
        scan(expression)
        for selector in computed_selectors(expression, budget=budget):
            try:
                values = expand(selector, frozenset())
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
            for value in values:
                retain(selected, value)
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
    for body in make_expressions(line):
        match = re.fullmatch(r"(?:flavor|origin|value)[ \t\r\n\v\f]+(.*)", body, re.S)
        if match and not re.fullmatch(IDENTIFIER, match[1]):
            return True
    return False


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
    line = _prune_and(line)
    names = set()
    for start, stop, body in _make_expression_spans(line, short=True):
        name = _make_reference_base(body)
        if re.fullmatch(IDENTIFIER, name) or SCOPED.fullmatch("$(" + name + ")"):
            names.add(name)
        else:
            function = _make_function(line[start:stop])
            if function is not None and function[0] == "call":
                name = _make_reference_base(function[1][0].lstrip(MAKE_SPACE), call=True)
                if re.fullmatch(IDENTIFIER, name):
                    names.add(name)
    names.update(name for _, _, name in _literal_metadata(line))
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
    source_assignments=(), budget=None, source_target=None, original_read_check=None,
    execution_bindings=(),
):
    all_names, graph, recipe, introspection, defaults = set(), set(), set(), set(), set()
    dependencies = {}
    definitions, expressions, read_expressions, graph_expressions = {}, {}, {}, []
    eval_requests, deferred_evals, consumed_expressions = [], set(), []
    eval_raw_expressions = {}
    retained_declarations = set()
    ambiguous_assignment = False
    observed_values = {} if observed_values is None else observed_values
    stage_sinks, stage_roots = [], set()
    original_reads = set()
    source_defined = set()
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

    def retain_assignment(assignment, *, expanded_input=False, stores=True, effect=None):
        if expanded_input and not retain_once("assignment", assignment[0]):
            return
        name, value = assignment["name"], assignment["value"]
        read_value = (
            effect.read_value if effect is not None and effect.read_value is not None
            else _prune_and(value, budget=budget)
        )
        retain_defaults(assignment[0])
        if not stores:
            return
        source_defined.add(name)
        dependencies.setdefault(name, set())
        if not expanded_input or "$" not in value:
            dependencies[name].update(references(read_value))
        definitions.setdefault(name, []).append(
            value.lstrip(MAKE_SPACE)
            if assignment["operator"] in {"=", ":=", "::=", "?="} and not (expanded_input and "$" in value)
            else None
        )
        if not expanded_input or "$" not in value:
            expressions.setdefault(name, []).append(value)
            read_expressions.setdefault(name, []).append(read_value)

    def retain_rhs(name, value, effect):
        if effect is None:
            raise MakeProbeError("Make assignment lacks its original source context")
        if effect.immediate is None and "$" in value:
            raise MakeProbeError("unproven original Make append RHS timing")
        if effect.applies is False and effect.immediate is False:
            return
        value = effect.read_value if effect.read_value is not None else _prune_and(value, budget=budget)
        immediate = effect.immediate is True
        eval_requests.append((None if immediate else name, value, effect.emitted))
        if immediate:
            consumed_expressions.append(value)
        if any(body.startswith(("eval ", "eval\t")) for body in make_expressions(value)):
            if immediate:
                graph.update(references(value))
                graph_expressions.append(value)
                stage_sinks.append(value)
            else:
                deferred_evals.add(name)

    if reference_units is None:
        reference_units = _source_units(sources, assignments=source_assignments, budget=budget, target=source_target)
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
        for name in unit.created_bindings:
            source_defined.add(name)
            dependencies.setdefault(name, set())
            definitions.setdefault(name, []).append("")
            expressions.setdefault(name, []).append("")
            read_expressions.setdefault(name, []).append("")
        phase_condition = re.fullmatch(r"[ \t]*(ifeq)[ \t]+(.*)", strip_comment(raw))
        if (
            unit.phase in reference_units.phase_tests and phase_condition
            and _empty_restart_test(phase_condition[1], phase_condition[2])
        ):
            continue
        extend_known(original_reads, unit.reads)
        all_names.update(unit.reads)
        statement, inline_recipe = (raw, "") if raw.startswith("\t") else split_inline_recipe(raw)
        inline_recipe = _prune_and(inline_recipe, budget=budget)
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
            eval_requests.append((None, inline_recipe, ()))
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
            operator = line[start.end():].strip(MAKE_SPACE)
            retain_rhs(defining, unit.body, unit.assignment)
            if unit.assignment.applies is False and unit.assignment.immediate is False:
                continue
            read_body = (
                unit.assignment.read_value if unit.assignment.read_value is not None
                else _prune_and(unit.body, budget=budget)
            )
            names = references(read_body) | references(read_body.replace("$$", "$"))
            all_names.update(names)
            if unit.assignment.applies is not False:
                source_defined.add(defining)
                definitions.setdefault(defining, []).append(
                    unit.body if operator in {"", "=", ":=", "::=", "?="} else None
                )
                dependencies.setdefault(defining, set()).update(names)
                expressions.setdefault(defining, []).append(unit.body)
                read_expressions.setdefault(defining, []).append(read_body)
            introspection.update(name for _, _, name in _literal_metadata(read_body))
            continue
        assignment = None if raw.startswith("\t") else ASSIGNMENT.match(line)
        target_assignment = None if raw.startswith("\t") else TARGET_ASSIGNMENT.match(line)
        relevant = (
            not (assignment or target_assignment)
            or unit.assignment is None
            or unit.assignment.applies is not False or unit.assignment.immediate is not False
        )
        read_line = line
        if assignment or target_assignment:
            parsed = assignment or target_assignment
            read_value = (
                unit.assignment.read_value if unit.assignment is not None and unit.assignment.read_value is not None
                else _prune_and(parsed["value"], budget=budget)
            )
            read_line = line[:parsed.start("value")] + read_value
        names = references(read_line) if relevant else set()
        all_names.update(names)
        if relevant:
            introspection.update(name for _, _, name in _literal_metadata(read_line))
        if assignment:
            retain_rhs(assignment["name"], assignment["value"], unit.assignment)
            retain_assignment(assignment, stores=unit.assignment.applies is not False, effect=unit.assignment)
            if relevant:
                eval_raw_expressions.setdefault(assignment["value"], set()).add(assignment["value"].lstrip(MAKE_SPACE))
            if line.lstrip(MAKE_SPACE).startswith("export "):
                recipe.update(names)
                consumed_expressions.append("$(" + assignment["name"] + ")")
        elif target_assignment:
            retain_rhs(target_assignment["name"], target_assignment["value"], unit.assignment)
            retain_assignment(target_assignment, stores=unit.assignment.applies is not False, effect=unit.assignment)
            if relevant:
                eval_raw_expressions.setdefault(target_assignment["value"], set()).add(target_assignment["value"].lstrip(MAKE_SPACE))
            graph.update(references(target_assignment["target"]))
            graph_expressions.append(target_assignment["target"])
            consumed_expressions.append(target_assignment["target"])
        elif raw.startswith("\t"):
            line = _prune_and(line, budget=budget)
            consumed_expressions.append(line)
            eval_requests.append((None, line, ()))
            (graph if "$(eval" in line or "${eval" in line else recipe).update(names)
            if "$(eval" in line or "${eval" in line:
                graph_expressions.append(line)
                stage_sinks.append(line)
        else:
            line = _prune_and(line, budget=budget)
            consumed_expressions.append(line)
            eval_requests.append((None, line, unit.emitted))
            graph.update(names)
            graph_expressions.append(line.replace("$$", "$"))
            if "$(eval" in line or "${eval" in line:
                stage_sinks.append(line.replace("$$", "$"))
            separators = _rule_separators(line)
            if (
                secondary_expansion and separators and not unit.native_literal_header
                and not any(body.startswith(("eval ", "eval\t")) for body in make_expressions(line))
            ):
                stage_sinks.append(line[separators[0] + 1:].replace("$$", "$"))
                eval_requests.append((None, line[separators[0] + 1:].replace("$$", "$"), ()))
            conditional = CONDITIONAL_NAME.match(line)
            if conditional and "$" in conditional[1]:
                graph_expressions.append("$(" + conditional[1] + ")")
            secondary = {left or right for left, right in SECONDARY.findall(line)}
            graph.update(secondary)
            all_names.update(secondary)
        if not raw.startswith("\t"):
            retain_defaults(line)

    if execution_bindings:
        observed_values = {name: set(values) for name, values in observed_values.items()}
    for name, flavor, raw_value, value in execution_bindings:
        extend_known(all_names, {name})
        extend_known(original_reads, {name})
        dependencies.setdefault(name, set())
        if flavor == "simple":
            observed_values.setdefault(name, set()).add(raw_value)
            definitions.setdefault(name, []).append(raw_value if "$" not in raw_value else None)
            continue
        if flavor != "recursive":
            raise MakeProbeError("original execution has an unsupported binding flavor")
        dependencies[name].update(references(value))
        definitions.setdefault(name, []).append(raw_value)
        expressions.setdefault(name, []).append(raw_value)
        read_expressions.setdefault(name, []).append(value)
        consumed_expressions.append(value)
        eval_requests.append((None, value, ()))
        extend_known(all_names, references(value))
        if "$" not in value:
            observed_values.setdefault(name, set()).add(value)

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
                    names = selected_names(["$(" + name + ")"], definitions, observed_values, budget=budget)
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
                if assignment["operator"] == "+=" and "$$" in assignment["value"]:
                    # Escaped eval output needs the original emitted statement's
                    # binding, not this fresh parser or a final native flavor.
                    raise MakeProbeError("unproven emitted Make append RHS timing")
                retain_assignment(assignment, expanded_input=True, stores=unit.assignment.applies is not False)
            elif definition:
                if line[definition.end():].strip(MAKE_SPACE) == "+=" and "$$" in unit.body:
                    raise MakeProbeError("unproven emitted Make append RHS timing")
                if retain_once("define", (line, unit.body)):
                    source_defined.add(definition[1])
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

    def retain_emitted_assignments(records):
        for line, effect in records:
            assignment = ASSIGNMENT.fullmatch(line) or TARGET_ASSIGNMENT.fullmatch(line)
            if assignment is None:
                raise MakeProbeError("unproven emitted assignment destination")
            if not retain_once("bound-assignment", (line, effect)):
                continue
            retain_assignment(assignment, stores=effect.applies is not False)
            retain_rhs(assignment["name"], assignment["value"], effect)
            if effect.applies is not False or effect.immediate is not False:
                all_names.update(references(assignment["value"]))
                introspection.update(name for _, _, name in _literal_metadata(assignment["value"]))
                eval_raw_expressions.setdefault(assignment["value"], set()).add(assignment["value"].lstrip(MAKE_SPACE))
            if "target" in assignment.groupdict():
                names = references(assignment["target"])
                all_names.update(names)
                graph.update(names)
                graph_expressions.append(assignment["target"])
                consumed_expressions.append(assignment["target"])

    # Native environment membership is actual consumption, including bare,
    # computed and implicit exports; it does not expand unexported bodies.
    exports = set(reference_units.native_exports)
    extend_known(all_names, exports & SOURCE_HISTORY_CONTROLS)
    consumed, execution_roots, execution_dependencies = set(), set(), {}
    constant_writes, unsafe_constants = {}, set()
    unknown_writer = False
    for position, (_, _, unit) in enumerate(ordered):
        if unit.body is not None:
            defined = DEFINE.match(strip_comment(unit.text))
            if defined:
                unsafe_constants.add(defined[1])
        if unit.body is None and not unit.text.startswith("\t"):
            line = strip_comment(unit.text)
            unknown_writer |= bool(_unproven_assignment_destination(line))
            undefined = re.fullmatch(r"\s*(?:override\s+)?undefine\s+(" + IDENTIFIER + r")\s*", line)
            if undefined:
                unsafe_constants.add(undefined[1])
            assignment = ASSIGNMENT.fullmatch(line)
            target_assignment = TARGET_ASSIGNMENT.fullmatch(line)
            if target_assignment:
                unsafe_constants.add(target_assignment["name"])
            if assignment:
                name = assignment["name"]
                constant = (
                    assignment["value"].lstrip(MAKE_SPACE)
                    if position in known_positions and unit.active is True and unit.assignment is not None
                    and unit.assignment.applies is True and assignment["operator"] in {"=", ":=", "::="}
                    and "$" not in assignment["value"] else None
                )
                if budget is not None:
                    budget.charge("cache", len(encoded((name, constant))))
                constant_writes.setdefault(name, []).append(constant)
        for read in _binding_reads(unit):
            expression = strip_comment(read.text) if read.header else read.text
            local_bindings = _foreach_read_bindings(expression, budget, context=read.context)
            if local_bindings is None:
                unknown_writer = True
            else:
                unsafe_constants.update(local_bindings)
            for body in make_expressions(expression):
                if body.startswith(("call ", "call\t", "guile ", "guile\t")):
                    unknown_writer = True
                if body.startswith(("eval ", "eval\t")):
                    assignment = ASSIGNMENT.fullmatch(body[5:].lstrip(MAKE_SPACE))
                    if assignment is None:
                        unknown_writer = True
                    else:
                        unsafe_constants.add(assignment["name"])
    read_constants = {} if unknown_writer else {
        name: values[0] for name, values in constant_writes.items()
        if name not in unsafe_constants and len(values) == 1 and values[0] is not None
        and name not in PROTECTED_BINDINGS
    }
    def recipe_constant(expression):
        return _resolve_make_text(
            expression, lambda part, body: read_constants.get(body) if re.fullmatch(IDENTIFIER, body) else None,
            budget,
        )

    # Only deferred bodies not read during parsing may borrow this whole-source
    # immutable-literal context. Immediate reads retain their occurrence forms.
    for name, values in read_expressions.items():
        if name not in original_reads and name not in graph:
            read_expressions[name] = [_prune_and(value, recipe_constant, budget) for value in values]

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
        for name in sorted(exports & definitions.keys()):
            if retain_once("native-export-consumption", name):
                consumed_expressions.append("$(" + name + ")")
        unresolved, unresolved_execution = set(), set()
        for name, originals in list(expressions.items()):
            values = read_expressions.get(name, originals)
            executing = [_without_literal_metadata(value) for value in values]
            actual_dependencies = execution_dependencies.setdefault(name, set())
            extend_known(actual_dependencies, set().union(*(references(value) for value in executing)))
            errors = []
            extend_known(dependencies[name], selected_names(
                values, definitions, observed_values, unresolved=errors, budget=budget,
            ))
            if errors:
                unresolved.add(name)
            errors = []
            extend_known(actual_dependencies, selected_names(
                executing, definitions, observed_values, unresolved=errors, budget=budget,
            ))
            if errors:
                unresolved_execution.add(name)
        executing = [_without_literal_metadata(value) for value in consumed_expressions]
        extend_known(execution_roots, set().union(*(references(value) for value in executing)))
        root_errors = []
        for destination, values in ((graph, graph_expressions), (stage_roots, stage_sinks), (execution_roots, executing)):
            extend_known(destination, selected_names(
                values, definitions, observed_values, unresolved=root_errors, budget=budget,
            ))
        extend_known(consumed, closure(execution_roots, execution_dependencies))
        extend_known(graph, deferred_evals & consumed)
        extend_known(stage_roots, deferred_evals & consumed)

        ambiguous_history, proven_eval_expressions = False, set()
        for owner, expression, emitted in eval_requests:
            if owner is not None and owner not in consumed:
                continue
            proven = True
            if emitted:
                retain_emitted_assignments(emitted)
                bodies = emitted
            else:
                bodies = [body[5:].lstrip(MAKE_SPACE) for body in make_expressions(expression)
                          if body.startswith(("eval ", "eval\t"))]
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
    if reference_units.remade or reference_units.literal_modules:
        body_consumers = closure(consumed | set(template_graph_inputs), execution_dependencies)
        # Resolved graph selectors include ifdef's second, name-selected read.
        # Metadata endpoints are reads, not instructions to execute their bodies.
        read_names = graph | recipe | body_consumers | original_reads
        for name in body_consumers:
            read_names.update(dependencies.get(name, ()))
        if reference_units.remade and SOURCE_HISTORY_CONTROLS & (
            closure(all_names | graph | recipe, dependencies) | definitions.keys() | read_names
        ):
            raise MakeProbeError("unproven restart-sensitive Make source history")
    if reference_units.literal_modules:
        _certify_literal_bindings(
            reference_units.literal_modules, consumed_expressions, body_consumers, expressions,
            definitions, observed_values, defaults, exports, ambiguous_assignment, budget,
            read_names=read_names,
        )
    if original_read_check is not None:
        original_read_check(consumed_expressions, consumed, read_expressions)
    return {
        "all": closure(all_names | graph | recipe, dependencies),
        "graph": expanded_graph,
        "recipe": expanded_recipe,
        "recipe_only": expanded_recipe - expanded_graph,
        "introspection": closure(introspection, dependencies),
        "defaults": defaults,
        "defined": source_defined,
        "dependencies": dependencies,
        "execution_dependencies": execution_dependencies,
        "read_expressions": read_expressions,
        "read_constants": read_constants,
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
        "literal_binding_modules": tuple(module.path for module in reference_units.literal_modules),
    }


def _reads_variable_universe(expression):
    for body in make_expressions(expression):
        function = re.match(r"([^ \t\r\n\v\f]+)[ \t\r\n\v\f]+", body)
        call = function is not None and function[1] == "call"
        if _make_reference_base(body[function.end():] if call else body, call=call) == ".VARIABLES":
            return True
    return False


def _certify_literal_bindings(
    modules, roots, consumed, expressions, definitions, observed_values, defaults, exports,
    ambiguous_assignment, budget, *, read_names,
):
    writes = {name: value for module in modules for name, value in module.bindings}
    if ambiguous_assignment:
        raise MakeProbeError("literal binding module has an unproven assignment destination")
    if set(writes) & (defaults | exports):
        raise MakeProbeError("literal binding module has an external or exported consumer")
    for name, value in writes.items():
        if definitions.get(name) != [value]:
            raise MakeProbeError("literal binding module has another original definition")
    readers = [*roots, *(value for name in consumed for value in expressions.get(name, ()))]
    names = set(read_names)
    for expression in readers:
        if budget is not None:
            budget.remaining()
        names.update(references(expression))
        if _reads_variable_universe(expression):
            raise MakeProbeError("literal binding module has an opaque program/data/universe consumer")
        for body in make_expressions(expression):
            function = re.match(r"([^ \t\r\n\v\f]+)[ \t\r\n\v\f]+", body)
            if function and function[1] in {"file", "wildcard", "realpath", "eval", "guile"}:
                raise MakeProbeError("literal binding module has an opaque program/data/universe consumer")
        try:
            names.update(selected_names((expression,), definitions, observed_values, budget=budget))
        except _UnresolvedName as error:
            raise MakeProbeError("literal binding module has an unresolved possible consumer") from error
    if set(writes) & names:
        raise MakeProbeError("literal binding module has an original candidate consumer")
    if budget is not None:
        budget.charge("cache", len(encoded((sorted(writes), sorted(names)))))


def _literal_dependency_include(path, data, budget):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MakeProbeError(f"generated Make include is not UTF-8: {path}") from error
    for unit in make_source_units(text, mode=_MakeSourceMode(budget=budget), source_path=path):
        budget.remaining()
        line = strip_comment(unit.text).strip(MAKE_SPACE)
        if not line:
            continue
        separators = _rule_separators(line)
        if (
            unit.kind != "rule" or len(separators) != 1
            or any(character in line for character in "$%*?[]\\;|&=()")
        ):
            raise MakeProbeError(f"unproven generated include source history: {path}")
        targets, prerequisites = line[:separators[0]].split(), line[separators[0] + 1:].split()
        if not targets or any(target.startswith(".") for target in targets):
            raise MakeProbeError(f"unproven generated include source history: {path}")
        for name in targets:
            relative_path(name)
        for name in prerequisites:
            relative_path(name.removeprefix("/repo/"))


def _literal_binding_include(path, data, budget):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MakeProbeError(f"generated Make include is not UTF-8: {path}") from error
    units = [unit for unit in make_source_units(text, mode=_MakeSourceMode(budget=budget), source_path=path)
             if strip_comment(unit.text).strip(MAKE_SPACE)]
    if not any(unit.kind == "assignment" for unit in units):
        return None
    bindings = {}
    for unit in units:
        budget.remaining()
        line = strip_comment(unit.text)
        assignment = ASSIGNMENT.fullmatch(line)
        if (
            unit.kind != "assignment" or unit.body is not None or assignment is None
            or assignment["operator"] != ":=" or line[:assignment.start("name")].strip(MAKE_SPACE)
            or unit.conditional_depth or unit.active is not True
        ):
            raise MakeProbeError(f"unproven literal binding statement: {path}")
        name, value = assignment["name"], assignment["value"].lstrip(MAKE_SPACE)
        if name in PROTECTED_BINDINGS or name in bindings:
            raise MakeProbeError(f"protected or repeated generated binding: {name}")
        if not re.fullmatch(r"[A-Za-z0-9_./+ \t-]*", value):
            raise MakeProbeError(f"nonliteral generated binding value: {name}")
        bindings[name] = value
        if len(bindings) > 512:
            raise MakeProbeError("literal binding module exceeds the existing name bound")
    result = tuple(sorted(bindings.items()))
    budget.charge("cache", len(encoded((path, result))))
    return result


def _native_include_context(session, observation, sources, state, *, primary_source, commands=None):
    listing = observation.semantics["domains"]["MAKEFILE_LIST"]
    if listing["origin"] != "file" or listing["flavor"] != "simple":
        raise MakeProbeError("unproven native Make include listing")
    order = tuple(name.removeprefix("/repo/") for name in listing["value"].split())
    opened = {resolved.removeprefix("/repo/") for resolved, _ in observation.file_open_attempts}
    session.budget.charge("cache", len(encoded((order, sorted(opened)))))
    if (
        not order or order[0] != primary_source or next(iter(sources), None) != primary_source
        or primary_source not in opened or not set(order) <= set(sources)
        or not set(sources) <= opened
    ):
        raise MakeProbeError("native Make include names lack actual source-read evidence")
    exports = tuple(sorted({
        name for context in observation.semantics["native_dispatches"] for name in context["environment"]
    }))
    session.budget.charge("cache", len(encoded(exports)))
    generated = {item.path: item for item in observation.generated if item.path in order}
    if not generated:
        return order, False, exports, ()
    restart = observation.semantics["domains"].get("MAKE_RESTARTS")
    if (
        restart != {"origin": "environment", "flavor": "recursive", "value": "1"}
        or any(name in SOURCE_HISTORY_CONTROLS for _, name, _ in state)
    ):
        raise MakeProbeError("unproven native Make include restart history")
    published = {record[0]: record for record in observation.semantics["published_sources"]}
    modules = []
    for path, item in generated.items():
        session.budget.remaining()
        if path in session.snapshot.files or sources[path] != item.data:
            raise MakeProbeError(f"Make include source changed from its original capture: {path}")
        digest = hashlib.sha256(item.data).hexdigest()
        expected = (f"{0o100000 | item.mode:06o}", digest)
        versions = {
            (output[1], output[2])
            for record in observation.semantics["dynamic_commands"]
            for output in record.get("generated_outputs", ())
            if output[0] == path
        }
        session.budget.charge("cache", len(encoded(sorted(versions))))
        if (
            versions != {expected} or path not in published
            or published[path][2:5] != [item.mode, len(item.data), digest]
        ):
            raise MakeProbeError(f"unproven changed or unreceipted Make include: {path}")
        bindings = _literal_binding_include(path, item.data, session.budget)
        if bindings is None:
            # Keep the separate neutral dependency contract unchanged.
            _literal_dependency_include(path, item.data, session.budget)
            continue
        if commands is None or "MAKE_RESTARTS" in ENVIRONMENT:
            raise MakeProbeError("literal binding module lacks original invocation evidence")
        names = {name for name, _ in bindings}
        if names & {name for _, name, _ in state} or names & set(exports):
            raise MakeProbeError("literal binding module has an overridden or exported write")
        initial = session.original_make_inputs(observation.target, tuple(sorted(names)), assignments=state)
        if any(value != {"origin": "undefined", "flavor": "undefined", "value": ""} for value in initial.values()):
            raise MakeProbeError("literal binding module lacks original undefined inputs")
        writers = []
        for event in observation.events:
            session.budget.remaining()
            command = _event_command(event)
            registration = commands[command]
            if path in registration.outputs:
                if tuple(registration.outputs) != (path,):
                    raise MakeProbeError("literal binding writer has multiple output authority")
                writers.append(command)
        if len(writers) != 1:
            raise MakeProbeError("literal binding module requires one actual creation producer")
        for record in observation.semantics["dynamic_commands"]:
            if any(entry[0] == path for entry in record["command"]["inputs"]):
                raise MakeProbeError("literal binding module has an opaque producer data reader")
        modules.append(_LiteralBindingModule(path, bindings, tuple(writers)))
    if len(modules) > 1:
        raise MakeProbeError("multiple literal binding module histories are not proven")
    session.budget.charge("cache", len(encoded(modules)))
    return order, True, exports, tuple(modules)


def _loaded_sources(session, observation, *, primary_source):
    relative_path(primary_source)
    generated = {item.path: item.data for item in observation.generated}
    opened = set()
    for resolved, spelling in observation.file_open_attempts:
        try:
            name = relative_path(resolved.removeprefix("/repo/"))
            relative_path(spelling.removeprefix("/repo/"))
        except MakeProbeError as error:
            raise MakeProbeError(f"unadmitted Make file-open spelling: {spelling!r}") from error
        if name not in session.snapshot.files and name not in generated:
            raise MakeProbeError(f"unadmitted Make file-open: {spelling!r}")
        opened.add(name)
    if primary_source not in opened:
        raise MakeProbeError("invocation-selected Make source lacks actual open evidence")
    result = {}
    # The trusted -f selection and actual opens establish the available source
    # pool. MAKEFILE_LIST may describe visits, but cannot erase their bytes.
    for name in (primary_source, *sorted(opened - {primary_source})):
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
            found.update(selected_names(forms, definitions, literals, budget=session.budget))
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
    """Keep execution and literal metadata reads distinct in native pages."""
    def recipe_constant(expression):
        return _resolve_make_text(
            expression,
            lambda part, body: usage["read_constants"].get(body) if re.fullmatch(IDENTIFIER, body) else None,
            session.budget,
        )

    recipes = [
        _prune_and(entry["recipe"], recipe_constant, session.budget)
        for entry in observation.semantics["files"]
    ]
    if any(computed_introspection(recipe) for recipe in recipes):
        raise MakeProbeError("computed Make introspection in a consumed recipe lacks a sealed literal selector")
    executing = [_without_literal_metadata(recipe) for recipe in recipes]
    names = closure(
        set().union(*(references(recipe) for recipe in executing)),
        usage["execution_dependencies"],
    )
    try:
        names.update(closure(selected_names(
            executing,
            usage["definitions"], usage["observed_values"],
            budget=session.budget,
        ), usage["execution_dependencies"]))
    except _UnresolvedName as error:
        raise MakeProbeError(str(error)) from error
    if names & usage["unresolved"]:
        raise MakeProbeError("consumed recipe has an unresolved computed selector")
    exports = {
        name for context in observation.semantics["native_dispatches"]
        for name in context["environment"] if name in usage["defined"]
    }
    metadata_sources = recipes + [
        expression for name in names | exports
        for expression in usage["read_expressions"].get(name, usage["source_expressions"].get(name, ()))
    ]
    if any(computed_introspection(expression) for expression in metadata_sources):
        raise MakeProbeError("computed Make introspection in a consumed recipe lacks a sealed literal selector")
    metadata_names = {
        name for expression in metadata_sources for _, _, name in _literal_metadata(expression)
    }
    usage["recipe"].update(names | metadata_names | closure(exports, usage["dependencies"]))
    usage["all"].update(usage["recipe"])
    usage["recipe_only"] = usage["recipe"] - usage["graph"]
    pending = sorted(name for name in names - set(observed_names) if re.fullmatch(IDENTIFIER, name))
    combined = copy.deepcopy(observation.semantics)
    raw_pending = sorted(metadata_names - set(combined.get("definitions", {}).get("global", ())))
    while pending or raw_pending:
        chunk = tuple(pending[:512])
        del pending[:len(chunk)]
        raw_chunk = tuple(name for name in raw_pending if name not in chunk)[:512 - len(chunk)]
        raw_pending = [name for name in raw_pending if name not in raw_chunk]
        measured = session.make(
            target, variables=chunk, definitions=raw_chunk, assignments=state, commands=commands,
            observe_recipe_dispatch=observe_dispatch,
        )
        _stable_native_context(combined, measured.semantics)
        if measured.semantics.get("recipe_dispatches") != combined.get("recipe_dispatches"):
            raise MakeProbeError("native recipe dispatch changed across recipe observations")
        combined["domains"].update(measured.semantics["domains"])
        for previous, actual in zip(combined["files"], measured.semantics["files"]):
            previous["variables"].update(actual["variables"])
        if raw_chunk:
            metadata = measured.semantics["definitions"]
            session.budget.charge("cache", len(encoded(metadata)))
            if "definitions" not in combined:
                combined["definitions"] = copy.deepcopy(metadata)
            else:
                combined["definitions"]["global"].update(metadata["global"])
                for previous, actual in zip(combined["definitions"]["files"], metadata["files"]):
                    if previous["target"] != actual["target"]:
                        raise MakeProbeError("native metadata context changed across recipe observations")
                    previous["variables"].update(actual["variables"])
    return combined


def run_probe(
    loader, requested_targets, domains, dynamic_contracts, *, session,
    declared_external_names=(), environment_names=(), generated_path_names=(),
    symbolic_recipe_names=(), ambient_undefined_names=(), trusted_builtin_names=(),
    scoped_variable_names=(), escaped_literal_names=(), dispatch_targets=(), source_phases=False, **unused,
):
    if session is None or session.loader is not loader or session.snapshot is None:
        raise MakeProbeError("graph Make planning requires the selected shared report session")
    if unused:
        raise MakeProbeError("obsolete Make planner arguments are not supported")
    if type(source_phases) is not bool:
        raise MakeProbeError("source-phase planning requires a boolean selection")
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
    # Bind source recovery to the same explicit -f selection, not a list entry.
    primary_source = "Makefile"
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
                target, makefile=primary_source, variables=variables, assignments=state, commands=commands,
                observe_recipe_dispatch=observe_dispatch,
                **({"observe_source_journal": True, "source_journal_mode": "prewatched-directories"} if source_phases else {}),
            )
            if source_phases:
                from . import phase_census
                usage, loaded, phase_streams, phase_usages = phase_census.analyze(
                    session, observation, target, state, commands, primary_source=primary_source,
                    external_names=external | symbolic | environment,
                )
                source_union.update(loaded)
            else:
                loaded = _loaded_sources(session, observation, primary_source=primary_source)
                reference_units, template_inputs, template_scoped = _prepare_rule_templates(
                    session, target, state, commands, observation, loaded, observe_dispatch=observe_dispatch,
                    primary_source=primary_source,
                    external_names=external | symbolic | environment,
                )
                source_union.update((path, loaded[path]) for path in reference_units.read_sources)
            scopes = [observation.semantics["domains"], *(
                entry["variables"] for entry in observation.semantics["files"]
            )]
            observed_values = {
                name: set(domain.get("values", ())) | {
                    scope[name]["value"] for scope in scopes if scope[name]["origin"] != "undefined"
                }
                for name, domain in domains.items()
            }
            if not source_phases:
                usage = source_census(
                    loaded, observed_values=observed_values, reference_units=reference_units,
                    template_graph_inputs=template_inputs, template_scoped=template_scoped,
                    source_assignments=state,
                    budget=session.budget,
                    source_target=target,
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
            observed_values_and_metadata = {**semantics.get("definitions", {}).get("global", {}), **values}
            actual_undefined = {name for name in usage["all"] if name in observed_values_and_metadata
                                and observed_values_and_metadata[name]["origin"] == "undefined"}
            unknown_undefined = actual_undefined - undefined - set(domains) - trusted - scoped - escaped
            unknown_undefined.update(usage["all"] - usage["defined"] - set(observed_values_and_metadata)
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
                remaining_context = tuple(sorted(item for item in state if item[1] != name))
                domain_context = (name, tuple(choices), tuple(origins), remaining_context)
                if domain_context in planned_domains:
                    continue
                session.budget.charge("cache", len(encoded(domain_context)))
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
