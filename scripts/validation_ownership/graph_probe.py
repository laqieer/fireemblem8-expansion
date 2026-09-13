"""Graph-domain planning over the shared native Make observer.

The lexical census classifies reference positions, never Make's target or
recipe behavior. Every recorded state comes from the caller's ProbeSession.
"""

from __future__ import annotations

import re
import copy
from collections import Counter

from .authority import encoded, relative_path
from .budget import MakeProbeError
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
DEFINE = re.compile(rf"^\s*(?:(?:export|override|private)\s+)*define\s+({IDENTIFIER})")
SECONDARY = re.compile(rf"\$\$(?:\(({IDENTIFIER})|\{{({IDENTIFIER})\}})")
NAME_PART = re.compile(rf"\$\(({IDENTIFIER})\)|\$\{{({IDENTIFIER})\}}")


class _UnresolvedName(ValueError):
    pass


def make_expressions(line):
    stack = []
    index = 0
    while index < len(line):
        if line[index:index + 2] == "$$":
            index += 2
            continue
        if line[index:index + 2] in {"$(", "${"}:
            stack.append((index + 2, ")" if line[index + 1] == "(" else "}"))
            index += 2
            continue
        if stack and line[index] == stack[-1][1]:
            start, _ = stack.pop()
            if start is not None:
                yield line[start:index]
        elif stack and line[index] == ("(" if stack[-1][1] == ")" else "{"):
            stack.append((None, stack[-1][1]))
        index += 1


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


def selected_names(expressions, definitions, observed_values):
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
            if name in observed_values:
                choices = observed_values[name]
            else:
                declarations = definitions.get(name)
                if not declarations or any(value is None for value in declarations):
                    raise _UnresolvedName("computed selector lacks a closed literal or finite name definition")
                choices = set()
                for value in declarations:
                    choices.update(expand(value, active | {name}))
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
            values = expand(selector, set())
            if not values or any(
                not re.fullmatch(IDENTIFIER, name) and not SCOPED.fullmatch("$(" + name + ")")
                for name in values
            ):
                raise _UnresolvedName("computed selector is not a closed set of variable identifiers")
            selected.update(values)
    return selected


def strip_comment(line):
    escaped = False
    result = []
    for character in line:
        if character == "#" and not escaped:
            break
        result.append(character)
        escaped = not escaped if character == "\\" else False
    return "".join(result)


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


def source_census(sources, *, observed_values=None):
    all_names, graph, recipe, introspection, defaults = set(), set(), set(), set(), set()
    dependencies = {}
    definitions, expressions, graph_expressions = {}, {}, []
    observed_values = {} if observed_values is None else observed_values
    secondary_expansion = False
    for path, data in sources.items():
        try:
            lines = data.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise MakeProbeError(f"Make census source is not UTF-8: {path}") from error
        defining = None
        for raw in lines:
            statement, inline_recipe = (raw, "") if raw.startswith("\t") else split_inline_recipe(raw)
            line = statement if raw.startswith("\t") else strip_comment(statement)
            if not raw.startswith("\t") and re.match(r"^\s*\.SECONDEXPANSION\s*:", line):
                secondary_expansion = True
            if not raw.startswith("\t") and computed_introspection(line.replace("$$", "$")):
                raise MakeProbeError(f"computed Make introspection lacks a sealed literal selector: {path}")
            if inline_recipe:
                inline_names = references(inline_recipe)
                all_names.update(inline_names)
                if "$(eval" in inline_recipe or "${eval" in inline_recipe:
                    graph.update(inline_names)
                    graph_expressions.append(inline_recipe)
                else:
                    recipe.update(inline_names)
            start = DEFINE.match(line)
            if start and defining is None:
                defining = start.group(1)
                dependencies.setdefault(defining, set())
                expressions.setdefault(defining, [])
                definitions.setdefault(defining, []).append(None)
                continue
            names = references(line)
            all_names.update(names)
            introspection.update(INTROSPECTION.findall(line))
            if defining is not None:
                if line.strip() == "endef":
                    defining = None
                    continue
                names.update(references(line.replace("$$", "$")))
                all_names.update(names)
                dependencies[defining].update(names)
                expressions[defining].append(line.replace("$$", "$"))
                if "$(eval" in line or "${eval" in line:
                    graph.add(defining)
                continue
            assignment = None if raw.startswith("\t") else ASSIGNMENT.match(line)
            target_assignment = None if raw.startswith("\t") else TARGET_ASSIGNMENT.match(line)
            if assignment:
                dependencies.setdefault(assignment["name"], set()).update(references(assignment["value"]))
                definitions.setdefault(assignment["name"], []).append(
                    assignment["value"].lstrip() if assignment["operator"] in {"=", ":=", "::=", "?="} else None
                )
                expressions.setdefault(assignment["name"], []).append(assignment["value"])
                if line.lstrip().startswith("export "):
                    recipe.update(names)
                if "$(eval" in assignment["value"] or "${eval" in assignment["value"]:
                    graph.update(names)
                    graph_expressions.append(assignment["value"])
            elif target_assignment:
                dependencies.setdefault(target_assignment["name"], set()).update(references(target_assignment["value"]))
                definitions.setdefault(target_assignment["name"], []).append(
                    target_assignment["value"].lstrip()
                    if target_assignment["operator"] in {"=", ":=", "::=", "?="} else None
                )
                expressions.setdefault(target_assignment["name"], []).append(target_assignment["value"])
                graph.update(references(target_assignment["target"]))
                graph_expressions.append(target_assignment["target"])
                if "$(eval" in target_assignment["value"] or "${eval" in target_assignment["value"]:
                    graph.update(names)
                    graph_expressions.append(target_assignment["value"])
            elif raw.startswith("\t"):
                (graph if "$(eval" in line or "${eval" in line else recipe).update(names)
                if "$(eval" in line or "${eval" in line:
                    graph_expressions.append(line)
            else:
                graph.update(names)
                graph_expressions.append(line.replace("$$", "$"))
                conditional = CONDITIONAL_NAME.match(line)
                if conditional and "$" in conditional[1]:
                    graph_expressions.append("$(" + conditional[1] + ")")
                secondary = {left or right for left, right in SECONDARY.findall(line)}
                graph.update(secondary)
                all_names.update(secondary)
            found = list(DEFAULT.finditer(line))
            if "?=" in line and not found:
                raise MakeProbeError(f"Make external-default declaration has a dynamic name: {path}")
            defaults.update(match["name"] for match in found
                            if "override" not in match["modifiers"].split())
    unresolved = set()
    for name, values in expressions.items():
        try:
            dependencies[name].update(selected_names(values, definitions, observed_values))
        except _UnresolvedName:
            unresolved.add(name)
    try:
        graph.update(selected_names(graph_expressions, definitions, observed_values))
    except _UnresolvedName as error:
        raise MakeProbeError(str(error)) from error
    expanded_graph = closure(graph, dependencies)
    if expanded_graph & unresolved:
        raise MakeProbeError("graph dependency has an unresolved computed selector")
    expanded_recipe = closure(recipe, dependencies)
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
        "stage_expressions": graph_expressions + [
            value for name in expanded_graph for value in expressions.get(name, ())
        ],
        "secondary_expansion": secondary_expansion,
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
    if not usage["secondary_expansion"] and not evals:
        return
    if any("$" in dependency["name"] for item in observation.semantics["files"] for dependency in item["prerequisites"]):
        raise MakeProbeError("staged Make graph retains unresolved dollar-bearing prerequisites")
    required = set(usage["graph"])
    measured, records = set(), {}
    while True:
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
                if raw in {"$", "$$", "$(", "${"}:
                    fragments.add(name)
                if flavor == "recursive" or usage["secondary_expansion"] or evals:
                    forms.append(raw)
                if flavor == "recursive" and "$$" in raw and (usage["secondary_expansion"] or evals):
                    forms.append(raw.replace("$$", "$"))
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
        if usage["secondary_expansion"] and not evals and fragments & required:
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
            scopes = [observation.semantics["domains"], *(
                entry["variables"] for entry in observation.semantics["files"]
            )]
            observed_values = {
                name: set(domain.get("values", ())) | {
                    scope[name]["value"] for scope in scopes if scope[name]["origin"] != "undefined"
                }
                for name, domain in domains.items()
            }
            usage = source_census(loaded, observed_values=observed_values)
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
        aggregate = source_census(source_union, observed_values={
            name: set().union(*(usage["observed_values"].get(name, set()) for usage in usages))
            for name in domains
        })
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
                "defaults": sorted(aggregate["defaults"] & external),
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
