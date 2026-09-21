"""Original per-pass source interpretation; no caller-supplied phase authority."""

from __future__ import annotations

from itertools import chain
from pathlib import PurePosixPath
import sys
import traceback
from .authority import encoded, relative_path
from .budget import MakeProbeError
from .graph_commands import _shell_tokens
from .make_probe import _NamespaceUnavailable
from . import graph_probe as graph


ORIGINS = {
    0: "default", 1: "environment", 2: "file", 4: "command line", 5: "override",
}
UNSUPPORTED_INPUT_FLAGS = 0x7FFFDA


def _path(name):
    return relative_path(name.removeprefix("/repo/"))


def _has_namespace(expression):
    expression = graph._without_literal_metadata(expression)
    return any(
        function is not None and function[0] == "wildcard"
        for start, stop, _ in graph._make_expression_spans(expression)
        for function in (graph._make_function(expression[start:stop]),)
    )


def _recipe_export_context(origin, event):
    return origin["stage"] == "after-read" and event["job"]["kind"] == "recipe"


def _export_expands(binding):
    if binding.flavor not in {"simple", "recursive"} or binding.origin not in ORIGINS.values():
        raise MakeProbeError("export lacks its effective original binding")
    # GNU preserves imported environment bytes even for recursive variables.
    return binding.flavor == "recursive" and binding.origin != "environment"


def _fixed_environment_binding(name, binding):
    return (
        binding.origin in {"default", "environment"} and name in graph.ENVIRONMENT
        and binding.value == graph.ENVIRONMENT[name] and "$" not in binding.value
    )


class SourceTemplates(graph._TemplateModeProof):
    def value(self, mode, name, *, active=()):
        return mode.exact_reference(name, active)

    def text(self, mode, expression):
        return mode.exact_initializer_value(expression)

    def native_value(self, name):
        raise MakeProbeError("original per-pass interpretation cannot borrow a terminal native value")


class SourcePass:
    def __init__(self, proof, part, image, exports, target, state, commands, primary_source):
        self.proof, self.session, self.part, self.image = proof, proof.session, part, image
        self.exports = tuple(sorted(exports))
        self.target, self.state, self.commands, self.primary_source = target, state, commands, primary_source
        if len(part.inputs) != 1 or part.inputs[0].parent:
            raise MakeProbeError("original source interpretation requires a proven global input scope")
        self.inputs = {row.name: row for row in part.inputs[0].variables}
        self.read_inputs = set()
        self.forced = frozenset(name for name, row in self.inputs.items() if (row.flags >> 26) & 7 == 4)
        self.missing = proof.missing.get(part.number, frozenset())
        self.namespace = set(image.directories)
        for directory, children in image.members.items():
            self.namespace.update(name if directory == "." else directory + "/" + name for name, _ in children)
        self.sources = {}
        for visit in part.visits:
            if visit.source is not None:
                self.sources[_path(visit.resolved)] = visit.source.data
        self.patterns = []
        self.executions = {}
        self.expanding_exports = set()
        self.data_exports = set()
        self.deferred_execution = False
        self.unproven_scoped_names = set()
        self.recipe_contexts = {}
        self.job_contexts = {}
        self.issued_contexts = {}
        self.scope_parents = {}
        self.scope_graph_error = None
        self.scope_mode = None

    def input(self, name):
        self.read_inputs.add(name)
        if name in graph.SOURCE_HISTORY_CONTROLS | graph.INVOCATION_CONTROL_READS | {"MAKELEVEL"}:
            return None
        row = self.inputs.get(name)
        if row is None:
            return {"origin": "undefined", "flavor": "undefined", "value": ""}
        origin = (row.flags >> 26) & 7
        if row.flags & UNSUPPORTED_INPUT_FLAGS or origin not in ORIGINS:
            raise MakeProbeError("unproven special/private/dynamic original input: " + name)
        return {
            "origin": ORIGINS[origin], "flavor": "recursive" if row.flags & 1 else "simple", "value": row.value,
        }

    def template_mode(self):
        return SourceTemplates(
            self.session, self.target, self.state, self.commands, self.proof.observation,
            self.primary_source, False,
        )

    def record_execution(self, mode, name, binding, value, local):
        if self.deferred_execution and name in self.unproven_scoped_names:
            self.require_scoped_context(mode, name)
        if (name not in self.inputs and name not in mode.scoped_names()) or binding.flavor == "undefined":
            return
        if binding.flavor not in {"simple", "recursive"} or value is None:
            raise MakeProbeError("original execution lacks its effective binding: " + name)
        if _fixed_environment_binding(name, binding):
            return
        if binding.flavor == "recursive":
            if graph.references(value) & local:
                raise MakeProbeError("original input execution has an unproven local binding: " + name)
            executing = graph._without_literal_metadata(value)
            if graph._reads_variable_universe(executing):
                raise MakeProbeError("original source has an unsupported variable-universe read")
            if self.deferred_execution and _has_namespace(executing):
                raise MakeProbeError("deferred original namespace use lacks a source-time snapshot")
            if self.deferred_execution and name in mode.scoped_names():
                if local and mode.effect_initializer_value("$(" + name + ")", local) is None:
                    raise MakeProbeError("unproven local-sensitive scoped binding: " + name)
                if mode.literal_text("$(" + name + ")") is None and mode.exact_reference(name) is None:
                    raise MakeProbeError("unproven original scoped value at use: " + name)
        key = name, binding.flavor, binding.value, value
        if key not in self.executions:
            self.session.budget.charge("cache", len(encoded(key)))
            self.executions[key] = None

    def require_scoped_context(self, mode, name):
        context = mode.scope_context
        if name not in self.unproven_scoped_names or not (
            self.deferred_execution or context is not None and context.kind in {"recipe", "obligation"}
        ):
            return
        if (
            context is None or self.issued_contexts.get(id(context)) is not context
            or mode is not self.scope_mode or name not in mode.scoped_names()
        ):
            raise MakeProbeError("original execution has an unproven target/private binding: " + name)
        self.proof.require_live()
        if self.scope_graph_error is not None or context.target not in self.scope_parents:
            raise MakeProbeError(
                "scoped use lacks its original source ancestry: " + name
                + (": " + self.scope_graph_error if self.scope_graph_error is not None else "")
            )
        pending, seen = list(self.scope_parents[context.target]), set()
        if len(pending) > 1:
            raise MakeProbeError("unproven shared-child scoped context: " + name)
        while pending:
            mode.checkpoint()
            parent = pending.pop()
            if parent == context.target:
                raise MakeProbeError("unproven cyclic scoped context")
            if parent in seen:
                raise MakeProbeError("unproven cyclic scoped context")
            seen.add(parent)
            selectors = mode.matching_scopes(parent)
            if any((selector, name) in mode.declared_scopes() for selector in selectors):
                raise MakeProbeError("unproven parent-inherited scoped binding: " + name)
            parents = self.scope_parents.get(parent, ())
            if len(parents) > 1:
                raise MakeProbeError("unproven shared-child scoped context: " + name)
            pending.extend(parents)

    @staticmethod
    def matching_rule(rule, target):
        if rule is None or rule.targets is None:
            return ()
        return tuple(
            selector for selector in rule.targets
            if graph._scope_pattern_stem(selector, target) is not None
        )

    def rendered_recipe(self, mode, expression, context):
        rule = context.rule
        pattern = rule.target_pattern or context.selector
        stem = graph._scope_pattern_stem(pattern, context.target)
        if stem is None or rule.prerequisites is None:
            return None
        first = rule.first_prerequisite
        if first is None:
            return None
        if "%" in first:
            if "%" not in pattern:
                return None
            first = first.replace("%", stem)
        automatic = {"@": context.target, "<": first}
        if "%" in pattern:
            automatic["*"] = stem

        def resolve(part, body):
            base = graph._make_reference_base(body)
            modifier = base[-1:] if len(base) == 2 and base[-1:] in {"D", "F"} else ""
            name = base[:-1] if modifier else base
            if name in automatic:
                value = automatic[name]
                if modifier:
                    value = PurePosixPath(value).parent.as_posix() if modifier == "D" else PurePosixPath(value).name
                suffix = body[len(base):]
                if suffix:
                    if not suffix.startswith(":") or suffix.count("=") != 1 or "$" in suffix:
                        return None
                    source, replacement = suffix[1:].split("=", 1)
                    if "%" not in source:
                        source, replacement = "%" + source, "%" + replacement
                    if not graph._supported_patsubst(source, replacement):
                        return None
                    return graph._join_make_text(
                        graph._original_patsubst_parts((source, replacement, value), self.session.budget),
                        self.session.budget,
                    )
                return value
            value = mode.literal_text(part)
            return mode.exact_initializer_value(part) if value is None else value

        expression = expression.lstrip(graph.MAKE_SPACE)
        while expression[:1] in {"@", "-", "+"}:
            expression = expression[1:].lstrip(graph.MAKE_SPACE)
        normalized = graph.SCOPED.sub(
            lambda match: "$(" + next(item for item in match.groups() if item is not None) + ")",
            expression,
        )
        self.session.budget.charge("cache", len(encoded(normalized)))
        return graph._resolve_make_text(normalized, resolve, self.session.budget)

    def retain_scope_context(self, unit, context):
        if len(self.issued_contexts) >= self.session.budget.limits.observation_count:
            raise MakeProbeError("scoped use count exceeds existing observation bound")
        self.session.budget.charge("cache", len(encoded(context)))
        self.issued_contexts[id(context)] = context
        self.recipe_contexts.setdefault(id(unit), []).append(context)
        if context.kind == "recipe":
            self.job_contexts[context.job] = context

    @staticmethod
    def corroborates_recipe(rendered, event):
        if event["arguments"][0] in {"/bin/sh", "/bin/bash"}:
            expected = graph._normalized_shell_commands(rendered, "original scoped recipe")
            actual = graph._normalized_shell_commands(graph._event_command(event), "native scoped recipe")
            return bool(expected) and expected == actual
        try:
            tokens = _shell_tokens(rendered, "original scoped direct recipe")
        except MakeProbeError:
            return False
        return (
            not any(token.operator or token.io_number for token in tokens)
            and [token.value for token in tokens] == event["arguments"]
        )

    def prepare_scope_contexts(self, mode, stream, recipes):
        self.scope_mode = mode
        rules = {unit.source_rule for _, _, unit in stream.ordered
                 if unit.active is not False and unit.source_rule is not None}
        if any(rule.targets is None for rule in rules):
            self.scope_graph_error = "unproven source target constructor"
        else:
            pending = [self.target]
            self.scope_parents = {self.target: set()}
            while pending and self.scope_graph_error is None:
                mode.checkpoint()
                parent = pending.pop()
                for rule in rules:
                    matches = self.matching_rule(rule, parent)
                    if not matches:
                        continue
                    if len(matches) != 1 or rule.prerequisites is None:
                        self.scope_graph_error = "unproven source prerequisite context"
                        break
                    selector, = matches
                    pattern = rule.target_pattern or selector
                    stem = graph._scope_pattern_stem(pattern, parent)
                    for prerequisite in rule.prerequisites:
                        mode.checkpoint()
                        if (
                            rule.target_pattern is None and "%" in selector and "/" not in selector
                            and "/" in parent and "%" in prerequisite and not prerequisite.startswith("%")
                        ):
                            self.scope_graph_error = "unproven directory-sensitive prerequisite pattern"
                            break
                        if "%" in prerequisite and "%" not in pattern:
                            self.scope_graph_error = "unproven source prerequisite stem"
                            break
                        child = prerequisite.replace("%", stem) if "%" in prerequisite and stem is not None else prerequisite
                        if graph._scope_words(child, patterns=False) != (child,):
                            self.scope_graph_error = "unproven source prerequisite name"
                            break
                        new_child = child not in self.scope_parents
                        if new_child:
                            if len(self.scope_parents) >= self.session.budget.limits.observation_count:
                                raise MakeProbeError("scoped ancestry exceeds existing observation bound")
                        if new_child or parent not in self.scope_parents[child]:
                            self.session.budget.charge("cache", len(encoded((parent, child))))
                        if new_child:
                            self.scope_parents[child] = set()
                            pending.append(child)
                        if parent not in self.scope_parents[child]:
                            self.scope_parents[child].add(parent)
        original_recipes = {}
        for unit, expression in recipes:
            if unit.source_rule is not None:
                original_recipes.setdefault(unit.source_rule, []).append(expression)
        native_recipes = {
            item["target"]: item["recipe"] for item in self.proof.observation.semantics["files"]
        }
        jobs = self.proof.jobs.get(self.part.number, ())
        for event in jobs:
            self.session.budget.remaining()
            job = event["job"]
            candidates = [
                unit for unit, _ in recipes
                if unit.active is True and unit.recipe_ordinal == job["command_line"]
                and len(self.matching_rule(unit.source_rule, job["target"])) == 1
            ]
            if not candidates:
                raise MakeProbeError("scoped source has an unproven native recipe occurrence")
            if len(candidates) != 1:
                for unit in candidates:
                    self.recipe_contexts.setdefault(id(unit), []).append(None)
                continue
            unit, = candidates
            rule = unit.source_rule
            original = "".join(expression.rstrip("\n") + "\n" for expression in original_recipes[rule])
            if native_recipes.get(job["target"]) != original:
                self.recipe_contexts.setdefault(id(unit), []).append(None)
                continue
            context = graph._ScopeContext(
                job["target"], self.matching_rule(rule, job["target"])[0], "recipe",
                rule, job["command_line"], event["sequence"],
            )
            expression = next(expression for candidate, expression in recipes if candidate is unit)
            with mode.using_scope(context):
                rendered = self.rendered_recipe(mode, expression, context)
            if rendered is None:
                self.recipe_contexts.setdefault(id(unit), []).append(None)
                continue
            if not self.corroborates_recipe(rendered, event):
                self.recipe_contexts.setdefault(id(unit), []).append(None)
                continue
            self.retain_scope_context(unit, context)
        # Required source contexts remain obligations even if this pass remakes
        # its includes before dispatching the final goal. They are not fake jobs.
        native_slots = {(event["job"]["target"], event["job"]["command_line"]) for event in jobs}
        if self.scope_graph_error is None:
            for unit, _ in recipes:
                if unit.active is not True or unit.recipe_ordinal is None:
                    continue
                for target in self.scope_parents:
                    mode.checkpoint()
                    matches = self.matching_rule(unit.source_rule, target)
                    if len(matches) != 1 or (target, unit.recipe_ordinal) in native_slots:
                        continue
                    context = graph._ScopeContext(
                        target, matches[0], "obligation", unit.source_rule, unit.recipe_ordinal,
                    )
                    self.retain_scope_context(unit, context)

    def deferred_expression(self, mode, expression, context):
        if context is None:
            return mode.effectful(expression, automatic=True)
        with mode.using_scope(context):
            return mode.effectful(expression, automatic=True)

    def classify_export(self, mode, name, context):
        if context is not None:
            with mode.using_scope(context):
                return self.classify_export(mode, name, None)
        bindings = mode.binding(name)
        if len(bindings) != 1:
            raise MakeProbeError("export has an ambiguous original binding: " + name)
        binding = next(iter(bindings))
        if _export_expands(binding):
            self.expanding_exports.add(name)
            if mode.effectful("$(" + name + ")", automatic=True):
                raise MakeProbeError("original exported execution has an unproven effect or binding")
        elif not _fixed_environment_binding(name, binding):
            if name not in self.data_exports:
                self.session.budget.charge("cache", len(encoded((
                    "export-data", name, binding.origin, binding.flavor, binding.value,
                ))))
            self.data_exports.add(name)

    def prepare_deferred_reads(self, stream):
        mode = stream.mode_state
        if not mode.original_namespace_valid:
            detail = ""
            if mode.first_uncertainty is not None:
                site, reason, name = mode.first_uncertainty
                detail = ": " + (site.label() if site is not None else "<original input>") + ": " + reason
                if name:
                    detail += " [" + name + "]"
            raise MakeProbeError("original source has an unproven execution context" + detail)
        self.unproven_scoped_names = set().union(
            *(values.keys() for values in mode.target_definitions.values())
        ) if mode.target_definitions else set()
        recipes = []
        for _, _, unit in stream.ordered:
            if unit.active is False:
                continue
            if unit.text.startswith("\t"):
                recipes.append((unit, unit.text[1:]))
            else:
                header, inline = graph.split_inline_recipe(unit.text)
                if graph.CONDITIONAL_NAME.match(header):
                    self.check_reads((header,), (), {})
                assignment = graph.ASSIGNMENT.fullmatch(graph.strip_comment(header))
                if assignment is not None and "private" in header[:assignment.start("name")].split():
                    self.unproven_scoped_names.add(assignment["name"])
                if inline:
                    recipes.append((unit, inline))
        if mode.scope_declarations:
            self.prepare_scope_contexts(mode, stream, recipes)
        mode.scope_lookup_guard = self.require_scoped_context
        controls = graph.INVOCATION_CONTROL_READS | graph.SOURCE_HISTORY_CONTROLS | {"MAKELEVEL"}
        self.deferred_execution = True
        try:
            for unit, expression in recipes:
                for context in self.recipe_contexts.get(id(unit), (None,)):
                    if self.deferred_expression(mode, expression, context):
                        raise MakeProbeError("original deferred execution has an unproven effect or binding")
            if mode.scope_declarations:
                for event in self.proof.jobs.get(self.part.number, ()):
                    context = self.job_contexts.get(event["sequence"])
                    for name in event["environment"].keys() - controls:
                        self.classify_export(mode, name, context)
            else:
                for name in self.exports:
                    if name not in controls:
                        self.classify_export(mode, name, None)
        finally:
            self.deferred_execution = False

    def check_reads(self, roots, consumed, expressions):
        if ".VARIABLES" in self.exports:
            raise MakeProbeError("original source has an unsupported variable-universe read")
        readers = chain(roots, (value for name in consumed for value in expressions.get(name, ())))
        for expression in readers:
            self.session.budget.remaining()
            value = graph._without_literal_metadata(expression)
            conditional = graph.CONDITIONAL_NAME.match(value)
            if (
                graph._reads_variable_universe(value)
                or conditional and conditional[1].strip(graph.MAKE_SPACE) == ".VARIABLES"
            ):
                raise MakeProbeError("original source has an unsupported variable-universe read")

    def wildcard(self, patterns):
        self.proof.require_live()
        def directory(name):
            for forbidden in self.image.forbidden:
                if name == forbidden or name.startswith(forbidden + "/"):
                    raise _NamespaceUnavailable("phase namespace enters an unadmitted original object")
            return self.image.members.get(name, ())
        value = self.session._wildcard_image(self.image, patterns, directory)
        self.session.budget.charge("cache", len(encoded((self.part.number, patterns, value))))
        self.patterns.append((patterns, value))
        return value


class OriginalSourceProof:
    def __init__(self, session, observation):
        self.session, self.observation = session, observation
        self.images = session._source_phase_images(observation)
        self.archive = session._original_source_archive(observation)
        journal = observation.source_journal
        if journal is None or journal["closed"] is not True or journal["scope"] != self.archive.scope:
            raise MakeProbeError("original source phases require the complete issued mutation journal")
        if len(self.images) != len(self.archive.passes):
            raise MakeProbeError("original source phases omit entry namespace images")
        self.publications = {
            event["producer"]: event for event in observation.source_effects["events"]
            if event["kind"] == "publication"
        }
        self.exec_entries = {event["exec"]: event["seq"] for event in observation.read_trace["events"] if event["kind"] == "exec"}
        parts = {part.number: part for part in self.archive.passes}
        for transaction in journal["transactions"]:
            session.budget.remaining()
            if transaction["operation"].startswith("cleanup"):
                if transaction["producer"] is not None or transaction["origin"] is not None:
                    raise MakeProbeError("source cleanup borrowed a native read epoch")
                continue
            events = journal["events"][transaction["event_start"]:transaction["event_end"]]
            if not any(event.get("space", "source") == "source" for event in events):
                continue
            origin = transaction["origin"]
            part = parts.get(origin["pass"])
            publication = self.publications.get(transaction["producer"])
            following = self.exec_entries.get(origin["exec"] + 1)
            if (
                part is None or publication is None or following is None or origin["scope"] != self.archive.scope
                or origin["stage"] != "after-read" or origin["visit"] is not None
                or origin["rebuilding_makefiles"] is not True or origin["exec"] != part.exec
                or not part.exit_seq <= origin["trace_seq"] <= publication["trace_seq"] < following
            ):
                raise MakeProbeError("source mutation lacks a closed actual after-read/remake/reexec interval")
        self.versions = {}
        for event in self.publications.values():
            confirmation = event["confirmation"]
            if confirmation.get("kind") == "filesystem":
                after = confirmation["after"]
                values = [] if after is None else [{
                    "path": after[0], "mode": after[2], "size": after[3], "sha256": after[4], "identity": after[5],
                }]
            else:
                values = confirmation["outputs"]
            for value in values:
                key = value["path"], tuple(value["identity"])
                self.versions.setdefault(key, []).append((event["trace_seq"], value))
        self.missing = {}
        for index, part in enumerate(self.archive.passes):
            missing = set()
            for visit in part.visits:
                name = _path(visit.resolved)
                if _path(visit.name) != name:
                    raise MakeProbeError("source phase has an unproven include search/alias")
                if visit.error:
                    if visit.error != 2 or visit.source is not None or index + 1 == len(self.archive.passes):
                        raise MakeProbeError("source phase cannot omit an unclosed failed include")
                    image = self.images[index]
                    parent = PurePosixPath(name).parent.as_posix()
                    if any(child == PurePosixPath(name).name for child, _ in image.members.get(parent, ())):
                        raise MakeProbeError("failed include was not absent in its original entry image")
                    future = [item for item in self.archive.passes[index + 1].visits
                              if _path(item.resolved) == name and item.error == 0]
                    if not future:
                        raise MakeProbeError("failed source has no successful next-pass visit")
                    if not any(self._published_visit(item, minimum=part.exit_seq) for item in future):
                        raise MakeProbeError("failed source has no actual intervening publication")
                    missing.add(visit.number)
                elif name in session.snapshot.files:
                    if (
                        visit.source is None or visit.source.data != session.snapshot.files[name]
                        or visit.source.mode != int(session.loader.entries[name].mode, 8) & 0o777
                    ):
                        raise MakeProbeError("original source phase differs from its immutable input")
                elif not self._published_visit(visit):
                    raise MakeProbeError("source phase has an unreceipted original source version")
            self.missing[part.number] = frozenset(missing)
        origins = {event["origin"]: event for event in observation.source_effects["events"] if event["kind"] == "origin"}
        dispatches = {event["dispatch"]: origins[event["origin"]]
                      for event in observation.source_effects["events"] if event["kind"] == "dispatch"}
        self.exports = {}
        self.jobs = {}
        for event in observation.semantics["native_dispatches"]:
            origin = dispatches[event["sequence"]]
            if _recipe_export_context(origin, event):
                self.exports.setdefault(origin["pass"], set()).update(event["environment"])
                self.session.budget.charge("cache", len(encoded((origin["pass"], event["sequence"]))))
                self.jobs.setdefault(origin["pass"], []).append(event)

    def _published_visit(self, visit, minimum=0):
        if visit.source is None:
            return False
        name = _path(visit.resolved)
        for opened in visit.opens:
            if opened.result < 0:
                continue
            for sequence, value in self.versions.get((name, opened.identity), ()):
                if (
                    minimum <= sequence < opened.seq and value["mode"] == visit.source.mode
                    and value["size"] == len(visit.source.data) and value["sha256"] == visit.source.sha256
                ):
                    return True
        return False

    def require_live(self):
        self.session._source_phase_images(self.observation)


_NAMESPACE_MAP_CELL = sys.getsizeof({None: None})
_NAMESPACE_LIST_CELL = sys.getsizeof([None])


class _NamespaceRecord:
    """Measure a diagnostic projection before allocating its containers."""

    def __init__(self, budget, admitted=None):
        self.budget, self.admitted = budget, admitted
        self.count = self.size = self.storage = 0

    def account(self, *, count=0, size=0, storage=0):
        self.budget.remaining()
        count, size, storage = self.count + count, self.size + size, self.storage + storage
        if count > self.budget.limits.observation_count:
            raise MakeProbeError("namespace attribution exceeds existing observation bound")
        if size > self.budget.limits.file_bytes:
            raise MakeProbeError("namespace attribution exceeds existing file/record admission")
        if self.admitted is not None and any(
            actual > allowed for actual, allowed in zip((count, size, storage), self.admitted)
        ):
            raise MakeProbeError("namespace attribution changed after diagnostic admission")
        self.count, self.size, self.storage = count, size, storage

    def array(self, values):
        self.account(count=1, size=2, storage=sys.getsizeof([]))
        result = [] if self.admitted is not None else None
        for index, value in enumerate(values):
            self.account(size=bool(index), storage=_NAMESPACE_LIST_CELL)
            if result is not None:
                result.append(value)
        return result

    def mapping(self, fields):
        self.account(count=1, size=2, storage=sys.getsizeof({}))
        result = {} if self.admitted is not None else None
        for index, (key, value) in enumerate(fields):
            if not isinstance(key, str):
                raise MakeProbeError("namespace attribution has a non-string field")
            self.value(key)
            self.account(size=1 + bool(index), storage=_NAMESPACE_MAP_CELL)
            if result is not None:
                result[key] = value
        return result

    def value(self, value):
        if isinstance(value, dict):
            return self.mapping((key, self.value(item)) for key, item in value.items())
        if isinstance(value, (tuple, list)):
            if isinstance(value, tuple):
                self.account(storage=sys.getsizeof(value))
            result = self.array(self.value(item) for item in value)
            return tuple(result) if result is not None and isinstance(value, tuple) else result
        self.account(count=1, storage=sys.getsizeof(value))
        if isinstance(value, str):
            self.account(size=2)
            for character in value:
                code = ord(character)
                self.account(size=(
                    2 if character in '"\\\b\f\n\r\t' else 1 if 32 <= code <= 126
                    else 6 if code <= 0xFFFF else 12
                ))
        elif value is None:
            self.account(size=4)
        elif type(value) is bool:
            self.account(size=4 if value else 5)
        elif type(value) is int:
            self.account(size=len(encoded(value)))
        else:
            raise MakeProbeError("namespace attribution has an unsupported data value")
        return value if self.admitted is not None else None


def _namespace_attribution_size(record, budget):
    projection = _NamespaceRecord(budget)
    projection.value(record)
    return projection.size


def _namespace_carrier_path(budget, readers, carriers, dependencies, snapshots, condition, stage):
    # One predecessor per queued name; shared prefixes never become queued paths.
    bound = len(readers)
    if condition in {"namespace-dependency", "export-namespace-dependency"}:
        for children in dependencies.values():
            budget.remaining()
            bound += len(children)
            if bound > budget.limits.observation_count:
                raise MakeProbeError("namespace attribution exceeds existing observation bound")
    if bound > budget.limits.observation_count:
        raise MakeProbeError("namespace attribution exceeds existing observation bound")
    storage = (
        2 * sys.getsizeof({}) + 3 * sys.getsizeof([])
        + bound * (2 * _NAMESPACE_MAP_CELL + 3 * _NAMESPACE_LIST_CELL)
    )
    if storage > budget.limits.file_bytes:
        raise MakeProbeError("namespace attribution workspace exceeds existing file/record admission")
    stage[0] = "retention-accounting"
    budget.charge("cache", storage)
    stage[0] = "source-data"
    parents, pending, path = {}, [], []
    if condition in {"namespace-dependency", "export-namespace-dependency"}:
        for name in readers:
            if name not in snapshots and name not in parents:
                parents[name] = None
                pending.append(name)
        while pending:
            budget.remaining()
            name = pending.pop()
            if name in carriers:
                while name is not None:
                    budget.remaining()
                    path.append(name)
                    name = parents[name]
                path.reverse()
                break
            for child in dependencies.get(name, ()):
                budget.remaining()
                if child not in parents and child not in snapshots:
                    if len(parents) >= bound:
                        raise MakeProbeError("namespace dependency extent changed after admission")
                    parents[child] = name
                    pending.append(child)
    relevant = dict.fromkeys(readers)
    relevant.update((name, None) for name in path)
    return path, relevant


def _namespace_projection(out, phase, mode, usage, *, condition, occurrence, expression,
                          value, readers, carriers, path, relevant, unresolved,
                          unsafe, unknown, snapshot_checks, causes):
    def unresolved_text(error):
        if isinstance(error, str):
            return error
        if isinstance(error, graph._UnresolvedName) and len(error.args) == 1 and isinstance(error.args[0], str):
            return error.args[0]
        raise MakeProbeError("namespace attribution has an unsupported source reason")

    def fact(name):
        bindings = mode.definitions.get(name)
        original = phase.inputs.get(name)
        source_fact = mode.template_values.get(name)
        return out.mapping((
            ("bindings", out.value(None) if bindings is None else out.array(
                out.value({"origin": binding.origin, "flavor": binding.flavor}) for binding in bindings
            )),
            ("binding_status", out.value(
                "unavailable" if bindings is None else "version-mismatch"
                if mode.binding_versions.get((None, name), 0) != mode.version else "stored-original-binding"
            )),
            ("binding_version", out.value(mode.binding_versions.get((None, name)))),
            ("mode_version", out.value(mode.version)),
            ("source_fact_kind", out.value(None if source_fact is None else source_fact[1][0])),
            ("source_fact_version", out.value(None if source_fact is None else source_fact[0])),
            ("original_input_flags", out.value(None if original is None else original.flags)),
            ("read_forms", out.value(usage["read_expressions"].get(name, ()))),
            ("dependency_names", out.array(out.value(child) for child in usage["execution_dependencies"].get(name, ()))),
            ("namespace_carrier", out.value(name in carriers)),
            ("unsafe", out.value(name in unsafe)),
            ("target_scopes", out.array(out.value(scope) for scope, definitions in mode.target_definitions.items()
                                       if name in definitions)),
            ("snapshot_decision", out.value(
                "disabled-by-unknown-writer" if unknown else snapshot_checks.get(name, "not-examined")
            )),
            ("assignment_site_status", out.value("unavailable-not-retained-by-global-binding")),
        ))

    source = {"status": "unavailable", "reason": "export aggregate has no unique source occurrence"}
    contexts = ()
    if occurrence is not None:
        filename, index, unit = occurrence
        site = unit.site
        visit, visits = None, 0
        for candidate in phase.part.visits:
            out.budget.remaining()
            if candidate.resolved.removeprefix("/repo/") == filename:
                visit, visits = candidate.number, visits + 1
        source = {
            "path": filename, "stream_position": index,
            "site": None if site is None else {
                "path": site.path, "logical": site.logical, "start": site.start, "end": site.end,
            },
            "visit": visit if visits == 1 else None,
            "visit_status": "unique-original-visit" if visits == 1 else "unavailable-or-repeated",
            "rule_number": None if unit.source_rule is None else unit.source_rule.number,
            "recipe_ordinal": unit.recipe_ordinal, "active": unit.active,
        }
        contexts = phase.recipe_contexts.get(id(unit), ())
    return out.mapping((
        ("kind", out.value("source-refusal-attribution-not-a-proof")),
        ("pass", out.value(phase.part.number)), ("exec", out.value(phase.part.exec)),
        ("scope", out.value(phase.proof.archive.scope)), ("source", out.value(source)),
        ("condition", out.value(condition)), ("expression", out.value(expression)),
        ("read_form", out.value(value)), ("unresolved", out.array(out.value(unresolved_text(error)) for error in unresolved)),
        ("reader_names", out.array(out.value(name) for name in readers)),
        ("carrier_path", out.value(path)), ("unknown_writer", out.value(unknown)),
        ("unsafe_unknown_causes", out.value(causes)),
        ("snapshot_facts", out.mapping((name, fact(name)) for name in relevant)),
        ("use_associations", out.array(out.value(
            {"kind": "unproved"} if context is None else {
                "kind": context.kind, "target": context.target, "ordinal": context.ordinal, "job": context.job,
            }
        ) for context in contexts)),
        ("association_status", out.value("recorded" if contexts else "unavailable-not-inferred")),
        ("use_kind", out.value(
            "active-source-obligation; actual job status requires an association"
            if occurrence is not None else "export-read aggregate; no unique source occurrence"
        )),
    ))


def _namespace_attribution(phase, mode, usage, stage, *, names, snapshots,
                           attribution_overflow, **decision):
    budget = phase.session.budget
    budget.remaining()
    if attribution_overflow:
        raise MakeProbeError("namespace attribution exceeds existing diagnostic admission")
    path, relevant = _namespace_carrier_path(
        budget, names["readers"], names["carriers"], usage["execution_dependencies"],
        snapshots, decision["condition"], stage,
    )
    stage[0] = "bounded-serialization"
    measured = _NamespaceRecord(budget)
    _namespace_projection(
        measured, phase, mode, usage, readers=names["readers"], carriers=names["carriers"],
        path=path, relevant=relevant, **decision,
    )
    extent = measured.count, measured.size, measured.storage
    stage[0] = "retention-accounting"
    budget.charge("cache", measured.size + measured.storage)
    stage[0] = "bounded-serialization"
    result = _namespace_projection(
        _NamespaceRecord(budget, admitted=extent), phase, mode, usage,
        readers=names["readers"], carriers=names["carriers"], path=path, relevant=relevant, **decision,
    )
    result["reader_names"].sort()
    for fact in result["snapshot_facts"].values():
        fact["dependency_names"].sort()
        fact["target_scopes"].sort()
    return result


def _namespace_refusal(phase, mode, usage, message, *, condition, occurrence,
                       expression, value, names, unresolved, snapshots, unsafe,
                       unknown, snapshot_checks, causes, attribution_overflow):
    failure = MakeProbeError(message)
    stage = ["source-data"]
    try:
        failure.source_attribution = _namespace_attribution(
            phase, mode, usage, stage, condition=condition, occurrence=occurrence,
            expression=expression, value=value, names=names, unresolved=unresolved,
            snapshots=snapshots, unsafe=unsafe, unknown=unknown,
            snapshot_checks=snapshot_checks, causes=causes, attribution_overflow=attribution_overflow,
        )
    except BaseException as diagnostic:
        failure.source_attribution_unavailable = stage[0]
        kind = type(diagnostic).__name__
        failure.source_attribution_failure_type = (
            kind if len(kind) <= phase.session.budget.limits.file_bytes
            else "unavailable-type-name-exceeds-existing-bound"
        )
        kind = None
        # Only diagnostic frames are discarded. The caller's original source
        # failure context survives on the primary exception raised below.
        traceback.clear_frames(diagnostic.__traceback__)
        diagnostic.__traceback__ = None
        diagnostic.__cause__ = diagnostic.__context__ = None
    if hasattr(failure, "source_attribution_unavailable"):
        note = (
            "namespace attribution unavailable during " + failure.source_attribution_unavailable
            + " (" + failure.source_attribution_failure_type + ")"
        )
        failure.add_note(note)
        raise failure from MakeProbeError(note)
    raise failure


def _deferred_namespace_check(phase, stream, usage):
    causes, snapshot_checks = [], {}
    try:
        _check_deferred_namespace(phase, stream, usage, causes, snapshot_checks)
    finally:
        causes.clear()
        snapshot_checks.clear()


def _check_deferred_namespace(phase, stream, usage, causes, snapshot_checks):
    mode = stream.mode_state
    if not mode.original_namespace_valid:
        raise MakeProbeError("original source mode has unresolved effect timing")
    if not phase.patterns:
        return
    unsafe = set().union(*(values.keys() for values in mode.target_definitions.values())) if mode.target_definitions else set()
    unknown = False
    attribution_overflow = False
    attribution_count = attribution_storage = 0
    budget = phase.session.budget

    def admit_note(values, names=()):
        nonlocal attribution_overflow, attribution_count, attribution_storage
        if attribution_overflow:
            return False
        count = attribution_count + len(values) + len(names)
        storage = attribution_storage + _NAMESPACE_MAP_CELL * len(values) + _NAMESPACE_LIST_CELL * len(names)
        # These transient notes borrow source objects. Check their extents
        # before growth without spending a successful source path's budget.
        for value in chain(values, names):
            storage += sys.getsizeof(value)
            if isinstance(value, str):
                storage += 12 * len(value)
        available = min(
            budget.limits.file_bytes,
            budget.cumulative_limit("cache_bytes") - budget.bytes.get("cache", 0),
            budget.cumulative_limit("total_bytes") - sum(budget.bytes.values()),
        )
        if count > budget.limits.observation_count or storage > available:
            attribution_overflow = True
            causes.clear()
            snapshot_checks.clear()
            return False
        attribution_count, attribution_storage = count, storage
        return True

    def note_cause(record):
        names = record.get("names", ())
        if admit_note(tuple(value for key, value in record.items() if key != "names"), names):
            if names:
                record["names"] = sorted(names)
            causes.append(record)
    recipes = []
    for occurrence in stream.ordered:
        filename, index, unit = occurrence
        if unit.active is False:
            continue
        for read in graph._binding_reads(unit):
            expression = read.text
            locals_ = graph._foreach_read_bindings(
                expression, phase.session.budget, context=read.context,
            )
            if locals_ is None:
                unknown = True
                note_cause({"kind": "unproved-local-binder-analysis", "path": filename,
                            "stream_position": index, "site": unit.site})
            else:
                unsafe.update(locals_)
                if locals_:
                    note_cause({"kind": "local-binders", "path": filename,
                                "stream_position": index, "site": unit.site, "names": locals_})
            for body in graph.make_expressions(expression):
                if body.startswith(("eval ", "eval\t")):
                    assignment = graph.ASSIGNMENT.fullmatch(body[5:].lstrip(graph.MAKE_SPACE))
                    if assignment is None:
                        unknown = True
                        note_cause({"kind": "unproved-eval-writer", "path": filename,
                                    "stream_position": index, "site": unit.site})
                    else:
                        unsafe.add(assignment["name"])
                        note_cause({"kind": "eval-writer", "path": filename,
                                    "stream_position": index, "site": unit.site, "name": assignment["name"]})
        if unit.text.startswith("\t"):
            recipes.append((occurrence, unit.text[1:]))
        else:
            _, inline = graph.split_inline_recipe(unit.text)
            if inline:
                recipes.append((occurrence, inline))
    snapshots = set()
    def note_snapshot(name, decision):
        if admit_note((name, decision)):
            snapshot_checks[name] = decision
    if not unknown:
        for name, values in tuple(mode.definitions.items()):
            if name in unsafe:
                note_snapshot(name, "unsafe-binding")
                continue
            if len(values) != 1:
                note_snapshot(name, "ambiguous-binding")
                continue
            value = next(iter(values))
            if value.flavor != "simple":
                note_snapshot(name, "not-simple")
            elif mode.exact_reference(name) is not None:
                snapshots.add(name)
                note_snapshot(name, "exact-original-snapshot")
            else:
                note_snapshot(name, "exact-original-value-unavailable")
    carriers = {name for name, values in usage["source_expressions"].items() if any(_has_namespace(value) for value in values)}
    carriers.update(name for name in phase.read_inputs if name in phase.inputs and _has_namespace(phase.inputs[name].value))
    dependencies = usage["execution_dependencies"]
    def reaches(names):
        pending, seen = list(names), set()
        while pending:
            name = pending.pop()
            if name in seen or name in snapshots:
                continue
            seen.add(name)
            if name in carriers:
                return True
            pending.extend(dependencies.get(name, ()))
        return False
    for occurrence, expression in recipes:
        value = graph._without_literal_metadata(expression)
        unresolved = []
        names = graph.references(value) | graph.selected_names(
            (value,), usage["definitions"], usage["observed_values"], unresolved=unresolved,
        )
        condition = (
            "unresolved-selector" if unresolved else "direct-wildcard" if _has_namespace(value)
            else "namespace-dependency" if reaches(names) else None
        )
        if condition is not None:
            _namespace_refusal(
                phase, mode, usage, "deferred namespace use lacks an original parse-time snapshot",
                condition=condition, occurrence=occurrence, expression=expression, value=value,
                names={"readers": names, "carriers": carriers}, unresolved=unresolved,
                snapshots=snapshots, unsafe=unsafe, unknown=unknown, snapshot_checks=snapshot_checks,
                causes=causes, attribution_overflow=attribution_overflow,
            )
    if reaches(phase.expanding_exports):
        _namespace_refusal(
            phase, mode, usage, "exported namespace body crosses an unproven mutation interval",
            condition="export-namespace-dependency", occurrence=None, expression=None, value=None,
            names={"readers": phase.expanding_exports, "carriers": carriers}, unresolved=(),
            snapshots=snapshots, unsafe=unsafe, unknown=unknown, snapshot_checks=snapshot_checks,
            causes=causes, attribution_overflow=attribution_overflow,
        )


def union_usages(usages):
    result = {}
    set_fields = {
        "all", "graph", "recipe", "introspection", "defaults", "defined", "unresolved", "stage_graph",
    }
    set_maps = {"dependencies", "execution_dependencies"}
    list_maps = {"read_expressions", "definitions", "source_expressions", "observed_values"}
    lists = {"graph_expressions", "stage_sinks", "stage_expressions"}
    for key in usages[0]:
        if key in set_fields:
            result[key] = set().union(*(value[key] for value in usages))
        elif key in set_maps | list_maps:
            names = set().union(*(value[key].keys() for value in usages))
            result[key] = {}
            for name in names:
                values = [item for value in usages for item in value[key].get(name, ())]
                result[key][name] = set(values) if key in set_maps else list(dict.fromkeys(values))
        elif key in lists:
            result[key] = list(dict.fromkeys(item for value in usages for item in value[key]))
        elif key == "read_constants":
            first = usages[0][key]
            result[key] = {name: value for name, value in first.items()
                           if all(name in usage[key] and usage[key][name] == value for usage in usages)}
        elif key == "secondary_expansion":
            result[key] = any(value[key] for value in usages)
        elif key == "proven_eval_expressions":
            result[key] = set.intersection(*(set(value[key]) for value in usages))
        elif key == "literal_binding_modules":
            result[key] = tuple(sorted(set().union(*(value[key] for value in usages))))
        elif key != "recipe_only":
            raise MakeProbeError("source phase union lacks an explicit census field contract: " + key)
    result["recipe_only"] = result["recipe"] - result["graph"]
    return result


def analyze(session, observation, target, state, commands, *, primary_source="Makefile", external_names=()):
    session._original_namespace(observation, target=target, makefile=primary_source, assignments=state)
    proof = OriginalSourceProof(session, observation)
    streams, usages, sources = [], [], {}
    for part, image in zip(proof.archive.passes, proof.images):
        phase = SourcePass(proof, part, image, proof.exports.get(part.number, ()), target, state, commands, primary_source)
        if not part.visits or _path(part.visits[0].name) != primary_source:
            raise MakeProbeError("source phase lost its actual selected primary")
        stream, inputs, scoped = graph._prepare_rule_templates(
            session, target, state, commands, observation, phase.sources,
            primary_source=primary_source, external_names=external_names, phase=phase,
        )
        phase.prepare_deferred_reads(stream)
        stream = stream._replace(native_exports=tuple(sorted(phase.expanding_exports)))
        usage = graph.source_census(
            phase.sources, reference_units=stream, template_graph_inputs=inputs, template_scoped=scoped,
            source_assignments=state, budget=session.budget, source_target=target,
            original_read_check=phase.check_reads,
            execution_bindings=tuple(phase.executions),
        )
        original_names = {name for name, _, _, _ in phase.executions}
        exported = graph.closure(
            (phase.expanding_exports & (usage["defined"] | original_names)) | phase.data_exports,
            usage["dependencies"],
        )
        added = exported - usage["recipe"]
        if added:
            session.budget.charge("cache", len(encoded(sorted(added))))
        usage["recipe"].update(exported)
        usage["all"].update(exported)
        usage["recipe_only"] = usage["recipe"] - usage["graph"]
        _deferred_namespace_check(phase, stream, usage)
        streams.append(stream)
        usages.append(usage)
        sources.update(phase.sources)
    proof.require_live()
    combined = union_usages(usages)
    session.budget.charge("cache", len(encoded({
        key: sorted(value) if isinstance(value, set) else value
        for key, value in combined.items() if key in {"all", "graph", "recipe", "defaults", "read_constants"}
    })))
    return combined, sources, tuple(streams), tuple(usages)
