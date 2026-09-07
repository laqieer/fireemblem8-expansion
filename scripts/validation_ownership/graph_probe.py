"""Graph-domain planning over the shared native Make observer.

The lexical census classifies reference positions, never Make's target or
recipe behavior. Every recorded state comes from the caller's ProbeSession.
"""

from __future__ import annotations

import re
import copy

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
INTROSPECTION = re.compile(rf"\$\((?:flavor|origin|value)\s+({IDENTIFIER})\)")
CONDITIONAL = re.compile(rf"^\s*(?:ifdef|ifndef)\s+({IDENTIFIER})")
ASSIGNMENT = re.compile(
    rf"^\s*(?:(?:export|override|private)\s+)*(?P<name>{IDENTIFIER})\s*"
    r"(?P<operator>\?=|::=|:=|\+=|!=|=)(?P<value>.*)$"
)
TARGET_ASSIGNMENT = re.compile(
    rf"^(?P<target>.*?)\s*:\s*(?:(?:export|override|private)\s+)*"
    rf"(?P<name>{IDENTIFIER})\s*(?:\?=|::=|:=|\+=|!=|=)(?P<value>.*)$"
)
DEFINE = re.compile(rf"^\s*(?:(?:export|override|private)\s+)*define\s+({IDENTIFIER})")
SECONDARY = re.compile(rf"\$\$(?:\(({IDENTIFIER})|\{{({IDENTIFIER})\}})")


def strip_comment(line):
    escaped = False
    result = []
    for character in line:
        if character == "#" and not escaped:
            break
        result.append(character)
        escaped = not escaped if character == "\\" else False
    return "".join(result)


def references(line):
    names = {next(value for value in match.groups() if value is not None)
             for pattern in (REFERENCE, SCOPED) for match in pattern.finditer(line)}
    names.update(INTROSPECTION.findall(line))
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


def source_census(sources):
    all_names, graph, recipe, introspection, defaults = set(), set(), set(), set(), set()
    dependencies = {}
    computed = set()
    for path, data in sources.items():
        try:
            lines = data.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise MakeProbeError(f"Make census source is not UTF-8: {path}") from error
        defining = None
        for raw in lines:
            line = strip_comment(raw)
            start = DEFINE.match(line)
            if start and defining is None:
                defining = start.group(1)
                dependencies.setdefault(defining, set())
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
                if "$($" in line or "${$" in line:
                    computed.add(defining)
                if "$(eval" in line or "${eval" in line:
                    graph.add(defining)
                continue
            assignment = None if raw.startswith("\t") else ASSIGNMENT.match(line)
            target_assignment = None if raw.startswith("\t") else TARGET_ASSIGNMENT.match(line)
            if assignment:
                dependencies.setdefault(assignment["name"], set()).update(references(assignment["value"]))
                if line.lstrip().startswith("export "):
                    recipe.update(names)
                if "$(eval" in assignment["value"] or "${eval" in assignment["value"]:
                    graph.update(names)
                if "$($" in assignment["value"] or "${$" in assignment["value"]:
                    computed.add(assignment["name"])
            elif target_assignment:
                dependencies.setdefault(target_assignment["name"], set()).update(references(target_assignment["value"]))
                graph.update(references(target_assignment["target"]))
                if "$(eval" in target_assignment["value"] or "${eval" in target_assignment["value"]:
                    graph.update(names)
                if "$($" in target_assignment["value"] or "${$" in target_assignment["value"]:
                    computed.add(target_assignment["name"])
            elif raw.startswith("\t"):
                (graph if "$(eval" in line or "${eval" in line else recipe).update(names)
            else:
                graph.update(names)
                secondary = {left or right for left, right in SECONDARY.findall(line)}
                graph.update(secondary)
                all_names.update(secondary)
            found = list(DEFAULT.finditer(line))
            if "?=" in line and not found:
                raise MakeProbeError(f"Make external-default declaration has a dynamic name: {path}")
            defaults.update(match["name"] for match in found
                            if "override" not in match["modifiers"].split())
    for name in computed:
        dependencies[name].update(defaults)
    expanded_graph = closure(graph, dependencies)
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
    }


def _loaded_sources(session, commands, observation):
    values = observation.semantics["domains"]["MAKEFILE_LIST"]["value"].split()
    result = {}
    for name in values:
        name = name.removeprefix("/repo/")
        relative_path(name)
        if name in session.snapshot.files:
            result[name] = session.snapshot.files[name]
        elif name in commands.generated:
            result[name] = commands.generated[name]
        else:
            raise MakeProbeError(f"GNU Make loaded an unadmitted include: {name}")
    return result


def _semantic(semantics):
    result = copy.deepcopy(semantics)
    result.pop("owner_inputs", None)
    result["domains"] = {name: value for name, value in result["domains"].items()
                         if name not in {"MAKEFILE_LIST", "MAKE_RESTARTS"}}
    result["files"] = [
        {**entry, "recipe": "\n".join(
            line.rstrip() for line in entry["recipe"].splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ), "variables": {name: value for name, value in entry["variables"].items()
                        if name not in {"MAKEFILE_LIST", "MAKE_RESTARTS"}}}
        for entry in result["files"]
    ]
    return result


def _recipe_domains(session, target, state, commands, observation, usage, observed_names):
    """Measure referenced recipe values through bounded native variable pages."""
    names = closure(
        {name for entry in observation.semantics["files"] for name in references(entry["recipe"])},
        usage["dependencies"],
    )
    pending = sorted(name for name in names - set(observed_names) if re.fullmatch(IDENTIFIER, name))
    combined = copy.deepcopy(observation.semantics)
    for index in range(0, len(pending), 512):
        chunk = tuple(pending[index:index + 512])
        measured = session.make(target, variables=chunk, assignments=state, commands=commands)
        structural = lambda value: [
            {key: entry[key] for key in ("target", "source", "recipe", "prerequisites")}
            for entry in value["files"]
        ]
        if structural(measured.semantics) != structural(combined):
            raise MakeProbeError("Make graph changed while observing its recipe values")
        if measured.semantics["dynamic_commands"] != combined["dynamic_commands"]:
            raise MakeProbeError("Make command provenance changed across recipe observations")
        combined["domains"].update(measured.semantics["domains"])
        for previous, actual in zip(combined["files"], measured.semantics["files"]):
            previous["variables"].update(actual["variables"])
    return combined


def run_probe(
    loader, requested_targets, domains, dynamic_contracts, *, session,
    declared_external_names=(), environment_names=(), generated_path_names=(),
    symbolic_recipe_names=(), ambient_undefined_names=(), trusted_builtin_names=(),
    scoped_variable_names=(), escaped_literal_names=(), **unused,
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
    variables = tuple(sorted({"MAKEFILE_LIST", "MAKE_RESTARTS", *domains, *external}
                             - {name for name in external if not re.fullmatch(IDENTIFIER, name)}))
    if len(variables) > 512:
        raise MakeProbeError("graph domain observation exceeds the public variable bound")
    commands = MakeCommands(session, dynamic_contracts)
    results = {}
    for target in sorted(requested_targets):
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
            observation = session.make(target, variables=variables, assignments=state, commands=commands)
            loaded = _loaded_sources(session, commands, observation)
            source_union.update(loaded)
            usage = source_census(loaded)
            usages.append(usage)
            semantics = _recipe_domains(session, target, state, commands, observation, usage, variables)
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
                            session.budget.charge("pending", len(encoded(replacement)))
                            pending.append(replacement)
                            planned.add(key)
                        enumerated.add(name)
        aggregate = source_census(source_union)
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
            "symbolic_recipe_names": sorted(symbolic & aggregate["recipe_only"]),
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
