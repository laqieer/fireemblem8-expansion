"""Original per-pass source interpretation; no caller-supplied phase authority."""

from __future__ import annotations

from itertools import chain
from pathlib import PurePosixPath
from .authority import encoded, relative_path
from .budget import MakeProbeError
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
        self.deferred_execution = False
        self.unproven_scoped_names = set()

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
            raise MakeProbeError("original execution has an unproven target/private binding: " + name)
        if name not in self.inputs or binding.flavor == "undefined":
            return
        if binding.flavor not in {"simple", "recursive"} or value is None:
            raise MakeProbeError("original execution lacks its effective binding: " + name)
        if (
            binding.origin in {"default", "environment"} and name in graph.ENVIRONMENT
            and binding.value == graph.ENVIRONMENT[name] and "$" not in binding.value
        ):
            return
        if binding.flavor == "recursive":
            if graph.references(value) & local:
                raise MakeProbeError("original input execution has an unproven local binding: " + name)
            executing = graph._without_literal_metadata(value)
            if graph._reads_variable_universe(executing):
                raise MakeProbeError("original source has an unsupported variable-universe read")
            if self.deferred_execution and _has_namespace(executing):
                raise MakeProbeError("deferred original namespace use lacks a source-time snapshot")
        key = name, binding.flavor, binding.value, value
        if key not in self.executions:
            self.session.budget.charge("cache", len(encoded(key)))
            self.executions[key] = None

    def prepare_deferred_reads(self, stream):
        mode = stream.mode_state
        if not mode.original_namespace_valid:
            raise MakeProbeError("original source has an unproven execution context")
        self.unproven_scoped_names = set().union(
            *(values.keys() for values in mode.target_definitions.values())
        ) if mode.target_definitions else set()
        recipes = []
        for _, _, unit in stream.ordered:
            if unit.active is False:
                continue
            if unit.text.startswith("\t"):
                recipes.append(unit.text[1:])
            else:
                header, inline = graph.split_inline_recipe(unit.text)
                assignment = graph.ASSIGNMENT.fullmatch(graph.strip_comment(header))
                if assignment is not None and "private" in header[:assignment.start("name")].split():
                    self.unproven_scoped_names.add(assignment["name"])
                if inline:
                    recipes.append(inline)
        controls = graph.INVOCATION_CONTROL_READS | graph.SOURCE_HISTORY_CONTROLS | {"MAKELEVEL"}
        roots = chain(recipes, ("$(" + name + ")" for name in self.exports if name not in controls))
        self.deferred_execution = True
        try:
            for expression in roots:
                if mode.effectful(expression, automatic=True):
                    raise MakeProbeError("original deferred execution has an unproven effect or binding")
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
        for event in observation.semantics["native_dispatches"]:
            origin = dispatches[event["sequence"]]
            if origin["stage"] == "after-read" and event["job"]["kind"] == "recipe" and event["kind"] == "value":
                self.exports.setdefault(origin["pass"], set()).update(event["environment"])

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


def _deferred_namespace_check(phase, stream, usage):
    mode = stream.mode_state
    if not mode.original_namespace_valid:
        raise MakeProbeError("original source mode has unresolved effect timing")
    if not phase.patterns:
        return
    unsafe = set().union(*(values.keys() for values in mode.target_definitions.values())) if mode.target_definitions else set()
    unknown = False
    recipes = []
    for _, _, unit in stream.ordered:
        if unit.active is False:
            continue
        expressions = [unit.text, *(() if unit.body is None else (unit.body,))]
        for expression in expressions:
            locals_ = graph._foreach_read_bindings(expression, phase.session.budget)
            if locals_ is None:
                unknown = True
            else:
                unsafe.update(locals_)
            for body in graph.make_expressions(expression):
                if body.startswith(("eval ", "eval\t")):
                    assignment = graph.ASSIGNMENT.fullmatch(body[5:].lstrip(graph.MAKE_SPACE))
                    if assignment is None:
                        unknown = True
                    else:
                        unsafe.add(assignment["name"])
        if unit.text.startswith("\t"):
            recipes.append(unit.text[1:])
        else:
            _, inline = graph.split_inline_recipe(unit.text)
            if inline:
                recipes.append(inline)
    snapshots = set()
    if not unknown:
        for name, values in tuple(mode.definitions.items()):
            if name in unsafe or len(values) != 1:
                continue
            value = next(iter(values))
            if value.flavor == "simple" and mode.exact_reference(name) is not None:
                snapshots.add(name)
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
    for expression in recipes:
        value = graph._without_literal_metadata(expression)
        unresolved = []
        names = graph.references(value) | graph.selected_names(
            (value,), usage["definitions"], usage["observed_values"], unresolved=unresolved,
        )
        if unresolved or _has_namespace(value) or reaches(names):
            raise MakeProbeError("deferred namespace use lacks an original parse-time snapshot")
    if reaches(phase.exports):
        raise MakeProbeError("exported namespace body crosses an unproven mutation interval")


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
        usage = graph.source_census(
            phase.sources, reference_units=stream, template_graph_inputs=inputs, template_scoped=scoped,
            source_assignments=state, budget=session.budget, source_target=target,
            original_read_check=phase.check_reads,
            execution_bindings=tuple(phase.executions),
        )
        original_names = {name for name, _, _, _ in phase.executions}
        exported = graph.closure(
            set(phase.exports) & (usage["defined"] | original_names), usage["dependencies"],
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
