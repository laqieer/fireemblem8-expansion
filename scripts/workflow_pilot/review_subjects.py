"""Closed bindings to existing cases; no candidate-supplied programs or plugins."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import types

from scripts.workflow_pilot import review_family as review
from scripts.workflow_pilot.raw_diff_check import ProcessCleanupError, run_process


@dataclass(frozen=True)
class SubjectSpec:
    case_id: str
    name: str
    model: str

    @property
    def key(self):
        return self.case_id + "/" + self.name


BINDINGS = (
    SubjectSpec("TC-GAMEPLAY-006", "aoe-item-dispatch", "aoe"),
    SubjectSpec("TC-CORE-004", "generated-eventlists", "eventlists"),
    SubjectSpec("TC-WORKFLOW-REVIEW-FAMILY-001", "review-session", "review-session"),
    SubjectSpec("TC-LOCALIZATION-AUTHORED-SCOPE-001", "authored-scope-case", "authored-case"),
    SubjectSpec("TC-WORKFLOW-REVIEW-FAMILY-001", "authored-case-binding", "authored-binding"),
)
AOE_CORE = "src/expansion_aoe.c"
AOE_HEADER = "include/expansion_aoe.h"
AOE_REFERENCE = "src/expansion_aoe_reference.c"
AOE_DRIVER = "tools/gba-playtest/tests/c/expansion_aoe_driver.c"
AOE_DISABLED = "tools/gba-playtest/tests/c/expansion_aoe_disabled_driver.c"
REVIEW_SOURCE = "scripts/workflow_pilot/review_family.py"
EVENT_SCHEMA = "scripts/generated_data/eventlists/schema.py"
EVENT_SOURCE = "src/data/ch2_eventlists.json"
PHASES = ("CAN_USE", "BEGIN_USE", "EXECUTE", "AI_SELECT")
SHAPES = ("DIAMOND", "SQUARE", "CROSS")
CASE_REGISTRY = "docs/test-cases/registry.json"
CASE_TEMPLATE = "docs/test-cases/template.md"
CASE_DOCUMENT = "docs/test-cases/localization.md"
CASE_REFERENCE = "docs/game_localization_catalog.md"
LOCALE_CASE_CONSUMER = "tools/gba-playtest/tests/test_locale_tester_cases.py"
LOCALE_CASE_PROGRAM = "build/review-locale-case-consumer.py"
BINDING_SOURCE = "scripts/workflow_pilot/review_subjects.py"
BINDING_CAPTURE = "build/review-subject-binding.py"
BINDING_FIXTURE = "build/authored-binding-fixture"
AUTHORED_CASE_ID = "TC-LOCALIZATION-AUTHORED-SCOPE-001"
AUTHORED_FEATURE = "full-game-localization"
AUTHORED_FEATURE_CASES = frozenset({
    "TC-LOCALIZATION-003", "TC-CJK-INVENTORY-ORDER-001", AUTHORED_CASE_ID,
})
CASE_SECTIONS = frozenset({
    "Actions", "Expected result", "Negative control",
    "Interactions and save compatibility", "Automation", "Cleanup and limitations",
})


def authored_case_declaration(raw):
    catalog = json.loads(raw)
    features = [item for item in catalog["features"] if item["id"] == AUTHORED_FEATURE]
    cases = [item for item in catalog["cases"] if item["id"] == AUTHORED_CASE_ID]
    review.require(len(features) == len(cases) == 1, "ambiguous authored case ownership")
    feature, case = features[0], cases[0]
    review.require(set(feature["required_cases"]) == AUTHORED_FEATURE_CASES
                   and len(feature["required_cases"]) == len(AUTHORED_FEATURE_CASES),
                   "changed authored feature case members need a reviewed finite model")
    review.require(case["document"] == CASE_DOCUMENT
                   and feature["reference"] == CASE_REFERENCE,
                   "remapped canonical procedure paths need a reviewed finite model")
    return catalog, feature, case


def locale_case_metadata(raw):
    declarations = []
    for item in ast.parse(raw).body:
        if (isinstance(item, ast.Assign) and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
                and isinstance(item.value, ast.Dict)
                and any(isinstance(key, ast.Constant) and key.value == AUTHORED_FEATURE
                        for key in item.value.keys)):
            declarations.append((item.targets[0].id, ast.literal_eval(item.value)))
    review.require(len(declarations) == 1, "unknown locale case metadata declaration")
    return declarations[0]


def locale_case_program_fixture(raw, variant):
    source = ast.parse(raw)
    functions = [
        function for owner in source.body
        if isinstance(owner, ast.ClassDef) and owner.name == "LocaleTesterCaseSemanticTests"
        for function in owner.body
        if isinstance(function, ast.FunctionDef)
        and function.name == "test_registry_owns_the_eight_stable_localization_cases"
    ]
    review.require(len(functions) == 1, "reviewed consumer selector changed")
    if variant == "rename":
        names = {node.id for node in ast.walk(functions[0])
                 if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}

        class RenameLocal(ast.NodeTransformer):
            def visit_Name(self, node):
                if node.id in names:
                    node.id = "fixture_" + node.id
                return node

        RenameLocal().visit(functions[0])
    predicates = [
        node for node in ast.walk(functions[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "assertEqual" and len(node.args) == 2
    ]
    review.require(bool(predicates), "reviewed consumer assertion model changed")
    if variant == "reject":
        for node in predicates:
            node.args[0] = ast.Call(
                func=ast.Name(id="set", ctx=ast.Load()), args=[], keywords=[],
            )
    elif variant == "swap":
        for node in predicates:
            node.args.reverse()
    else:
        review.require(variant in {"rename", "format"}, "unknown reviewed fixture variant")
    return ast.unparse(ast.fix_missing_locations(source)).encode()


def event_validation_inputs(tree):
    shared = {path for prefix in ("include", "src/data", "scripts/generated_data")
              for path in tree.under(prefix)}
    shared.update(path for path in tree.entries if path.startswith("reports/generated_data_"))
    shared.update({
        "scripts/assets/__init__.py", "scripts/assets/tmx.py", "assets/manifest.json",
        "src/events/ch2-eventinfo.h", "src/events_udefs.c",
        "src/events_shoplist.c", "src/events_trapdata.c",
    })
    manifest = json.loads(tree.read("assets/manifest.json"))
    for asset in manifest["assets"]:
        if {"mapWidth", "mapHeight"} <= set(asset.get("resources", {})):
            shared.update(asset["sources"])
    for path in tree.under("src/data"):
        if path.endswith("_bundle.json"):
            shared.update(item["file"] for item in json.loads(tree.read(path))["externalReferences"])
    return shared


def resolve_subject(case_id: str, subject: str, catalog: dict) -> SubjectSpec:
    matches = [item for item in BINDINGS if (item.case_id, item.name) == (case_id, subject)]
    review.require(len(matches) == 1, "unknown subject: a reviewed finite binding is required")
    cases = [item for item in catalog["cases"] if item["id"] == case_id]
    review.require(len(cases) == 1 and cases[0]["automation"],
                   "binding must reference one existing automated tester case")
    return matches[0]


def enum_members(source: bytes, name: str, prefix: str) -> dict[str, int]:
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", source.decode(), flags=re.S)
    found = re.findall(r"\benum\s+" + re.escape(name) + r"\s*\{([^{}]*)\}", text)
    review.require(len(found) == 1, f"missing or ambiguous finite enum {name}")
    values = {}
    next_value = 0
    for entry in found[0].split(","):
        entry = entry.strip()
        if not entry:
            continue
        parsed = re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_]*)(?:\s*=\s*(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*))?",
            entry)
        review.require(parsed is not None and parsed[1].startswith(prefix),
                       f"unsupported finite enum entry in {name}")
        member = parsed[1][len(prefix):]
        review.require(member not in values, "duplicate enum entries")
        if parsed[2] is not None:
            literal = parsed[2]
            base = 16 if literal.lower().startswith("0x") else 8 if literal.startswith("0") else 10
            next_value = int(literal, base)
        values[member] = next_value
        next_value += 1
    return values


def schema_declaration(source: bytes, *, dependencies=True) -> tuple[dict, tuple[str, ...]]:
    tree = ast.parse(source)
    classes = [item for item in tree.body if isinstance(item, ast.ClassDef)
               and item.name.endswith("TableSchema") and item.name != "TableSchema"]
    review.require(len(classes) == 1, "ambiguous generated-data schema")
    defaults = {}
    table_dependencies = []
    for item in classes[0].body:
        if isinstance(item, ast.Assign) and len(item.targets) == 1:
            target = item.targets[0]
            if isinstance(target, ast.Name) and target.id.startswith("default_"):
                defaults[target.id] = ast.literal_eval(item.value)
        if dependencies and isinstance(item, ast.FunctionDef) and item.name in {
                "dependency_tables", "optional_dependency_tables"}:
            returns = [node for node in item.body if isinstance(node, ast.Return)]
            review.require(len(returns) == 1, "finite dependency model must be explicit")
            table_dependencies.extend(ast.literal_eval(returns[0].value))
    review.require(all(isinstance(item, str) for item in table_dependencies),
                   "unknown dependency model")
    review.unique(table_dependencies, "schema dependencies")
    return defaults, tuple(table_dependencies)


def subject_declaration_nodes(raw):
    source = ast.parse(raw)
    declarations = [
        item.value for item in source.body if isinstance(item, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "BINDINGS"
                for target in item.targets)
    ]
    review.require(len(declarations) == 1
                   and isinstance(declarations[0], (ast.Tuple, ast.List)),
                   "unknown subject declaration needs a reviewed finite model")
    aliases = {"SubjectSpec"}
    for item in source.body:
        if (isinstance(item, ast.Assign) and isinstance(item.value, ast.Name)
                and item.value.id in aliases):
            aliases.update(target.id for target in item.targets if isinstance(target, ast.Name))
    fields = ("case_id", "name", "model")
    result = []
    for node in declarations[0].elts:
        review.require(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                       and node.func.id in aliases and len(node.args) <= len(fields),
                       "unknown subject constructor needs a reviewed finite model")
        values = dict(zip(fields, node.args))
        for keyword in node.keywords:
            review.require(keyword.arg in fields and keyword.arg not in values,
                           "unknown or duplicate subject field")
            values[keyword.arg] = keyword.value
        review.require(set(values) == set(fields)
                       and all(isinstance(value, ast.Constant) and isinstance(value.value, str)
                               for value in values.values()),
                       "nonliteral subject fields need a reviewed finite model")
        result.append((node, {key: value.value for key, value in values.items()}))
    return source, result


def _members(spec: SubjectSpec, tree) -> tuple[review.Obligation, ...]:
    result = []

    def add(family, role, name, producer, consumer, representation, revalidation,
            probe, inputs, *, kind="host", profile="host", evidence=("positive", "adversarial")):
        result.append(review.Obligation(
            spec.key, family, role + ":" + name, role, producer, consumer,
            representation, revalidation, probe, profile, evidence, tuple(sorted(inputs)), kind))

    if spec.model == "aoe":
        header = tree.read(AOE_HEADER)
        phases = enum_members(header, "ExpansionAoEItemPhase", "EXPANSION_AOE_ITEM_")
        shapes = enum_members(header, "ExpansionAoEShapeKind", "EXPANSION_AOE_SHAPE_")
        review.require(phases == {name: index for index, name in enumerate((*PHASES, "PHASE_COUNT"))}
                       and shapes == {name: index for index, name in enumerate((*SHAPES, "COUNT"))},
                       "changed/aliased AoE enum names or values need a reviewed probe")
        inputs = tuple(sorted({AOE_CORE, AOE_REFERENCE, *tree.under("include")}))
        for phase in PHASES:
            add("action", "actions", phase, AOE_CORE + ":ExpansionAoE_DispatchItem",
                "ExpansionAoEItemHandler(context)", "ExpansionAoEItemContext.phase",
                "invalid phase and reentrant dispatch", "aoe-phase:" + phase, inputs, kind="native")
        add("action", "items", "routes", AOE_CORE + ":ExpansionAoE_ValidateItemRouteTable",
            "ExpansionAoE_DispatchItem", "ExpansionAoEItemRouteTable",
            "key/item/policy/duplicate/capacity checks", "aoe-items", inputs, kind="native")
        for shape in SHAPES:
            add("action", "targets", shape, AOE_CORE + ":ExpansionAoE_BuildTargetSet",
                "ExpansionAoE_Execute", "ExpansionAoEShape/ExpansionAoETargetSet",
                "invalid radius and incomplete target rejection",
                "aoe-shape:" + shape, inputs, kind="native")
        for name, probe in (("capacity-filter", "aoe-targets"), ("stable-slots", "aoe-slots"),
                            ("execution", "aoe-execution")):
            add("action", "targets", name, AOE_CORE + ":ExpansionAoE_BuildTargetSet",
                "ExpansionAoE_Execute", "stable unit IDs and bounded target set",
                "capacity/invalid slots/hidden/stale placement", probe, inputs, kind="native")
        resources = inputs
        for role in ("enabled", "disabled"):
            add("resource", role, "reference", AOE_REFERENCE + ":ExpansionAoEReference_Apply",
                "reference native driver", "FE8_EXPANSION_AOE_REFERENCE",
                "default-off API is inert", "aoe-reference:" + role, resources,
                kind="native", profile=role, evidence=("positive", "adversarial") if role == "enabled"
                else ("default",))
            add("resource", role, "objects", AOE_REFERENCE, "AAPCS link and EWRAM",
                "ELF symbols/sections", "disabled callback/probe omission",
                "aoe-arm:" + role, resources, kind="arm-object", profile="aapcs-" + role,
                evidence=("compile", "default") if role == "disabled" else ("compile",))
    elif spec.model == "eventlists":
        defaults, dependencies = schema_declaration(tree.read(EVENT_SCHEMA))
        review.require(defaults.get("default_source") == EVENT_SOURCE,
                       "changed owner needs an explicit reviewed binding")
        validation_inputs = event_validation_inputs(tree)
        inputs = {EVENT_SCHEMA, EVENT_SOURCE} | validation_inputs
        for name in ("eventlists", *dependencies):
            relative = "scripts/generated_data/" + name + "/schema.py"
            values, _ = schema_declaration(tree.read(relative), dependencies=False)
            source = values.get("default_source")
            review.require(isinstance(source, str), "missing generated owner source")
            sources = tree.under(source) if source == "src/data" else (source,)
            paths = {relative, *sources} | validation_inputs
            inputs.update(paths)
            add("generated", "owners", name, relative, EVENT_SCHEMA, source,
                "schema validation and malformed input rejection",
                "generated-owner:" + name, paths, kind="parsed")
        hand = defaults["default_hand_source"]
        inventory = defaults["default_inventory_path"]
        add("generated", "outputs", "eventlists", EVENT_SCHEMA + ":generate_c",
            hand, defaults["default_output_name"], "generated C parses and round-trips",
            "generated-output", inputs | {hand}, kind="parsed",
            evidence=("positive", "adversarial", "generated"))
        add("generated", "consumers", "eventlists", EVENT_SOURCE, hand,
            "typed chapter event group", "all declared dependency references resolve",
            "generated-consumer", inputs | {hand}, kind="parsed")
        add("generated", "drift-checks", "eventlists", EVENT_SCHEMA + ":build_inventory",
            inventory, "committed generated inventory", "regenerate and compare",
            "generated-drift", inputs | {inventory}, kind="parsed", evidence=("positive", "generated"))
    elif spec.model == "authored-binding":
        _, bindings = subject_declaration_nodes(tree.read(BINDING_SOURCE))
        declarations = [
            fields for _, fields in bindings if fields == {
                "case_id": AUTHORED_CASE_ID, "name": "authored-scope-case",
                "model": "authored-case",
            }
        ]
        review.require(len(declarations) == 1,
                       "unknown authored-case source selector needs a reviewed finite model")
        for role in review.FAMILIES["generated"]:
            add("generated", role, "authored-case-binding", BINDING_SOURCE + ":run_probe",
                "reviewed closed fixture driver", "authored-case:" + role,
                "real production verdicts for valid and adversarial captured inputs",
                "authored-binding:" + role, (BINDING_SOURCE,), kind="host")
        for role in review.FAMILIES["wire"]:
            add("wire", role, "fixture-program", BINDING_SOURCE + ":run_probe",
                "reviewed fixture producer and consumer", "authored-binding:consumers",
                "equivalent reviewed programs preserve actual production fixture behavior",
                "authored-fixture:" + role, (BINDING_SOURCE,), kind="host")
    elif spec.model == "authored-case":
        _, feature, case = authored_case_declaration(tree.read(CASE_REGISTRY))
        mapping = json.dumps({
            "cases": sorted(feature["required_cases"]), "document": case["document"],
            "reference": feature["reference"],
        }, sort_keys=True)
        paths = {
            "owners": {CASE_REGISTRY},
            "outputs": {CASE_REGISTRY, CASE_TEMPLATE, case["document"]},
            "consumers": {CASE_REGISTRY, LOCALE_CASE_CONSUMER},
            "drift-checks": {CASE_REGISTRY, case["document"], feature["reference"]},
        }
        predicates = {
            "owners": CASE_REGISTRY,
            "outputs": CASE_TEMPLATE,
            "consumers": LOCALE_CASE_CONSUMER,
            "drift-checks": feature["reference"],
        }
        for role in review.FAMILIES["generated"]:
            add("generated", role, AUTHORED_FEATURE, predicates[role],
                case["document"], mapping,
                "canonical owner, complete public procedure, consumer equality and guide link",
                "authored-case:" + role, paths[role], kind="parsed",
                evidence=("positive", "adversarial"))
    elif spec.model == "review-session":
        predicates = {
            "entries": "ReviewSession.begin", "preservation": "RoundState.observe",
            "resets": "RoundState.observe", "terminals": "RoundState.dispose",
            "producers": "parse_json", "consumers": "validate_request",
            "validators": "validate_request", "replay": "RoundState.observe",
            "stale-bindings": "assess_handoff",
        }
        for family in ("lifecycle", "wire"):
            for role in review.FAMILIES[family]:
                add(family, role, "review-session", REVIEW_SOURCE + ":" + predicates[role],
                    "trusted coordinator", "typed requests/rounds/task observations",
                    "exact scope/head/round and sticky hold", family + ":" + role,
                    (REVIEW_SOURCE,))
    else:
        raise review.ReviewError("unknown finite source model")
    for path in {path for member in result for path in member.inputs}:
        tree.oid(path)
    return tuple(result)


def expand_members(spec: SubjectSpec, origin_tree, candidate_tree):
    before = _members(spec, origin_tree)
    after = _members(spec, candidate_tree)
    # Deletion is not an exemption from a previously accepted obligation.
    review.require({item.identity for item in before} == {item.identity for item in after},
                   "added/deleted members require a reviewed finite model and removal evidence")
    review.require(before == after, "production mapping changed between origin and candidate")
    review.validate_members(after)
    return after


class ContractViolation(Exception):
    pass


def check(condition, message):
    if not condition:
        raise ContractViolation(message)


def command(argv, *, stdin=None):
    result = run_process(argv, input=stdin, cwd=Path.cwd(), env=os.environ.copy(),
                         timeout=60, new_session=False)
    if result.returncode:
        raise RuntimeError("tool execution unavailable: " + result.stderr.decode(errors="replace")[-2000:])
    return result.stdout


def fixture_git(root, *arguments):
    return command([
        "/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
        "-c", "gc.auto=0", "-c", "maintenance.auto=0", "-c", "user.name=Review fixture",
        "-c", "user.email=review@example.invalid", "-C", str(root), *arguments,
    ]).decode().strip()


def probe_result(kind):
    """Keep failure metadata with the executor that actually ran, not its route."""
    def decorate(function):
        def run(*args):
            try:
                return {"verdict": "satisfied", **function(*args)}
            except ContractViolation as error:
                return {"kind": kind, "verdict": "contract-violation", "checks": 1,
                        "detail": f"{type(error).__name__}: {error}"[:review.MAX_DETAIL]}
            except Exception as error:
                return {"kind": kind, "verdict": "unavailable", "checks": 0,
                        "detail": f"{type(error).__name__}: {error}"[:review.MAX_DETAIL]}
        return run
    return decorate


@probe_result("native")
def _native(probe: str) -> dict:
    enabled = probe != "aoe-reference:disabled"
    executable = Path("build/native-enabled" if enabled else "build/native-disabled")
    if not executable.exists():
        paths = [AOE_REFERENCE, AOE_DISABLED]
        if enabled:
            paths = [AOE_CORE, AOE_REFERENCE, AOE_DRIVER]
        flags = ["-DFE8_EXPANSION_MODERN_BUILD=1"]
        if enabled:
            flags.append("-DFE8_EXPANSION_AOE_REFERENCE=1")
        command(["/usr/bin/gcc", "-std=gnu89", "-Werror=declaration-after-statement",
                 "-Werror=implicit-function-declaration", "-Werror=implicit-int",
                 "-Iinclude", "-Iinclude/generated", *flags, *paths, "-o", str(executable)])
    selector = {
        "aoe-items": "items", "aoe-targets": "targets", "aoe-slots": "slots",
        "aoe-execution": "execution",
        "aoe-reference:enabled": "reference",
    }.get(probe)
    if probe.startswith("aoe-phase:"):
        selector = "phase:" + probe.split(":")[1]
    if probe.startswith("aoe-shape:"):
        selector = "shape:" + probe.split(":")[1]
    args = [str(executable.resolve())]
    if enabled:
        review.require(selector is not None, "unknown native selector")
        args.append(selector)
    result = run_process(args, cwd=Path.cwd(), env=os.environ.copy(),
                         timeout=20, new_session=False)
    review.require(result.returncode in (0, 1), "native execution unavailable")
    check(result.returncode == 0, result.stderr.decode(errors="replace")[-2000:])
    return {"kind": "native", "checks": 1, "detail": "selected native assertions satisfied"}


@probe_result("parsed")
def _authored_case(probe):
    from scripts import check_docs

    catalog, feature, case = authored_case_declaration(Path(CASE_REGISTRY).read_bytes())
    role = probe.partition(":")[2]
    indexed = {item["id"]: item for item in catalog["cases"]}
    owners_valid = all(
        name in indexed and indexed[name].get("feature_id") == AUTHORED_FEATURE
        and indexed[name].get("automation") and indexed[name].get("profiles")
        for name in feature["required_cases"])
    if role == "owners":
        check(owners_valid,
              "authored feature cases lost their owner or executable profile")
    elif role == "consumers":
        if not owners_valid:
            return {"kind": "parsed", "verdict": "unavailable", "checks": 0,
                    "blocked_by": ["owners:" + AUTHORED_FEATURE],
                    "detail": "canonical case-owner prerequisite failed"}
        _, expected = locale_case_metadata(Path(LOCALE_CASE_CONSUMER).read_bytes())
        program = Path(LOCALE_CASE_PROGRAM).read_bytes()
        name, _ = locale_case_metadata(program)
        module = types.ModuleType("reviewed_locale_case_consumer")
        module.__file__ = str(Path(LOCALE_CASE_CONSUMER).resolve())
        exec(compile(program, LOCALE_CASE_PROGRAM, "exec"), module.__dict__)
        setattr(module, name, expected)
        consumer = module.LocaleTesterCaseSemanticTests(
            "test_registry_owns_the_eight_stable_localization_cases")
        try:
            consumer.test_registry_owns_the_eight_stable_localization_cases()
        except AssertionError as error:
            raise ContractViolation(str(error)) from error
    else:
        text = Path(case["document"]).read_text()
        stripped = check_docs.strip_fenced_blocks(text)
        headings = [(index, check_docs.parse_atx_heading(line))
                    for index, line in enumerate(stripped.splitlines())]
        start = [index for index, heading in headings
                 if heading and heading[0] == 2
                 and heading[1].startswith(AUTHORED_CASE_ID + ":")]
        check(len(start) == 1, "authored procedure heading is missing or ambiguous")
        end = next((index for index, heading in headings
                    if index > start[0] and heading and heading[0] <= 2),
                   len(stripped.splitlines()))
        selected = [(index, heading) for index, heading in headings
                    if start[0] < index < end and heading and heading[0] == 3]
        if role == "outputs":
            template = check_docs.strip_fenced_blocks(Path(CASE_TEMPLATE).read_text())
            required = [heading[1] for line in template.splitlines()
                        if (heading := check_docs.parse_atx_heading(line))
                        and heading[0] == 3]
            review.require(set(required) == CASE_SECTIONS and len(required) == len(CASE_SECTIONS),
                           "changed tester template sections need a reviewed finite model")
            required_slugs = {check_docs.github_heading_slug(name) for name in required}
            actual_slugs = [check_docs.github_heading_slug(heading[1])
                            for _, heading in selected]
            check(all(actual_slugs.count(name) == 1 for name in required_slugs),
                  "authored procedure must retain every separate tester template section")
            lines = text.splitlines()
            check(all(
                "\n".join(lines[index + 1:
                                selected[position + 1][0]
                                if position + 1 < len(selected) else end]).strip()
                for position, (index, heading) in enumerate(selected)
                if check_docs.github_heading_slug(heading[1]) in required_slugs),
                "every required tester section needs observable procedure content")
        elif role == "drift-checks":
            anchors = dict(zip(
                (index for index, heading in headings if heading),
                check_docs.compute_heading_slugs(stripped)))
            check(case["anchor"] == anchors[start[0]],
                  "authored procedure anchor differs from canonical registry")
            reference = check_docs.strip_fenced_blocks(Path(feature["reference"]).read_text())
            destination = os.path.relpath(case["document"], Path(feature["reference"]).parent)
            target = destination + "#" + case["anchor"]
            check(target in {link for _, link in
                             check_docs.extract_internal_link_targets(reference)},
                  "authoritative feature guide does not link to the authored procedure")
        else:
            raise review.ReviewError("unknown authored case probe")
    return {"kind": "parsed", "checks": 1,
            "detail": "parsed canonical tester format/member contract; not locale or ROM behavior"}


@probe_result("host")
def _authored_binding(probe):
    module = types.ModuleType("captured_authored_case_binding")
    module.__file__ = str(Path(BINDING_CAPTURE).resolve())
    sys.modules[module.__name__] = module
    exec(compile(Path(BINDING_CAPTURE).read_bytes(), BINDING_CAPTURE, "exec"), module.__dict__)
    role = probe.partition(":")[2]
    saved_root = Path.cwd()
    fixture = Path(BINDING_FIXTURE).resolve()
    paths = (CASE_REGISTRY, CASE_DOCUMENT, LOCALE_CASE_CONSUMER, LOCALE_CASE_PROGRAM)
    saved = {path: (fixture / path).read_bytes() for path in paths}
    checks = 0
    os.chdir(fixture)
    try:
        baseline = module.run_probe("authored-case:" + role)
        review.require(baseline["verdict"] != "unavailable",
                       "captured production predicate backend unavailable")
        check(baseline["verdict"] == "satisfied" and baseline["checks"] > 0,
              "captured production predicate rejects the valid reviewed fixture")
        checks += 1

        def rejected():
            observed = module.run_probe("authored-case:" + role)
            check(observed["verdict"] == "contract-violation" and observed["checks"] > 0,
                  "captured production predicate misclassifies its actual counterexample: "
                  + repr(observed)[:1000])

        if role == "owners":
            for missing in ("record", "automation"):
                catalog = json.loads(saved[CASE_REGISTRY])
                sibling = next(item for item in catalog["cases"]
                               if item["id"] == "TC-LOCALIZATION-003")
                if missing == "record":
                    catalog["cases"].remove(sibling)
                else:
                    sibling.pop("automation")
                Path(CASE_REGISTRY).write_text(json.dumps(catalog))
                rejected()
                checks += 1
        elif role == "consumers":
            Path(LOCALE_CASE_PROGRAM).write_bytes(
                locale_case_program_fixture(saved[LOCALE_CASE_PROGRAM], "reject"))
            rejected()
            checks += 1
            Path(LOCALE_CASE_PROGRAM).write_bytes(saved[LOCALE_CASE_PROGRAM])
            source = ast.parse(saved[LOCALE_CASE_CONSUMER])
            name, expected = locale_case_metadata(saved[LOCALE_CASE_CONSUMER])
            expected["unknown-feature"] = set()
            for item in source.body:
                if (isinstance(item, ast.Assign) and len(item.targets) == 1
                        and isinstance(item.targets[0], ast.Name)
                        and item.targets[0].id == name):
                    item.value = ast.parse(repr(expected), mode="eval").body
            Path(LOCALE_CASE_CONSUMER).write_text(ast.unparse(source))
            rejected()
            checks += 1
        elif role == "outputs":
            document = saved[CASE_DOCUMENT].decode()
            prefix, procedure = document.split("## " + AUTHORED_CASE_ID + ":", 1)
            for heading in sorted(CASE_SECTIONS):
                changed = procedure.replace("### " + heading, "**" + heading + "**", 1)
                review.require(changed != procedure, "reviewed section fixture model changed")
                Path(CASE_DOCUMENT).write_text(prefix + "## " + AUTHORED_CASE_ID + ":" + changed)
                rejected()
                checks += 1
        elif role == "drift-checks":
            catalog = json.loads(saved[CASE_REGISTRY])
            case = next(item for item in catalog["cases"] if item["id"] == AUTHORED_CASE_ID)
            case["anchor"] = (
                "tc-localization-003-production-expansion-and-full-game-locale-catalogs")
            Path(CASE_REGISTRY).write_text(json.dumps(catalog))
            rejected()
            checks += 1
            from scripts.workflow_pilot.trusted_review_gate import GitTree

            if not Path(".git").exists():
                fixture_git(fixture, "init", "-q")
            fixture_git(fixture, "add", ".")
            fixture_git(fixture, "commit", "-qm", "wrong procedure anchor")
            before = fixture_git(fixture, "rev-parse", "HEAD")
            Path(CASE_REGISTRY).write_bytes(saved[CASE_REGISTRY])
            fixture_git(fixture, "add", CASE_REGISTRY)
            fixture_git(fixture, "commit", "-qm", "correct procedure anchor")
            after = fixture_git(fixture, "rev-parse", "HEAD")
            before_tree, after_tree = GitTree(fixture, before), GitTree(fixture, after)
            spec = next(item for item in module.BINDINGS if item.name == "authored-scope-case")
            module.expand_members(spec, after_tree, after_tree)
            try:
                module.expand_members(spec, before_tree, after_tree)
            except module.review.ReviewError as error:
                raise ContractViolation(
                    "stale anchor content cannot be repaired through the production model: "
                    + str(error)) from error
            checks += 1
        else:
            raise review.ReviewError("unknown captured binding probe")
        return {"kind": "host", "checks": checks,
                "detail": "captured production run_probe executed against reviewed finite fixtures"}
    finally:
        for path, raw in saved.items():
            Path(path).write_bytes(raw)
        os.chdir(saved_root)


@probe_result("host")
def _authored_fixture(probe):
    module = types.ModuleType("captured_authored_fixture_producer")
    module.__file__ = str(Path(BINDING_CAPTURE).resolve())
    sys.modules[module.__name__] = module
    captured = Path(BINDING_CAPTURE).read_bytes()
    exec(compile(captured, BINDING_CAPTURE, "exec"), module.__dict__)
    fixture = Path(BINDING_FIXTURE).resolve()
    module.BINDING_CAPTURE = str(Path(BINDING_SOURCE).resolve())
    module.BINDING_FIXTURE = str(fixture)
    program = fixture / LOCALE_CASE_PROGRAM
    original = program.read_bytes()
    binding_path = fixture / BINDING_SOURCE
    binding_original = binding_path.read_bytes() if binding_path.exists() else None
    root = Path.cwd()
    role = probe.partition(":")[2]
    variant = {
        "producers": "format", "consumers": "rename", "validators": "format",
        "replay": "rename", "stale-bindings": "swap",
    }.get(role)
    review.require(variant is not None, "unknown reviewed fixture wire selector")
    try:
        baseline = module.run_probe("authored-binding:consumers")
        review.require(baseline["verdict"] != "unavailable",
                       "captured fixture producer backend unavailable")
        check(baseline["verdict"] == "satisfied" and baseline["checks"] > 0,
              "captured fixture producer rejects its valid reviewed program")
        if role == "validators":
            source = ast.parse(original)
            for node in ast.walk(source):
                if (isinstance(node, ast.FunctionDef)
                        and node.name == "test_registry_owns_the_eight_stable_localization_cases"):
                    node.name = "unavailable_reviewed_selector"
            program.write_text(ast.unparse(source))
            unknown = module.run_probe("authored-binding:consumers")
            check(unknown["verdict"] == "unavailable" and unknown["checks"] == 0,
                  "unknown reviewed selector received semantic evidence credit")
            return {"kind": "host", "checks": 2,
                    "detail": "captured producer validated known and unknown reviewed selectors"}
        changed = locale_case_program_fixture(original, variant)
        program.write_bytes(changed)
        observed = module.run_probe("authored-binding:consumers")
        check(observed["verdict"] == "satisfied" and observed["checks"] > 0,
              "equivalent reviewed program breaks captured fixture producer: "
              + repr(observed)[:1000])
        check(Path.cwd() == root and program.read_bytes() == changed,
              "captured fixture producer leaked cwd or program mutation")
        checks = 3
        if role == "replay":
            program.write_bytes(original)
            replay = module.run_probe("authored-binding:consumers")
            check(replay["verdict"] == "satisfied" and replay["checks"] > 0,
                  "fixture replay retained a stale reviewed-program binding")
            checks += 1
        if role == "stale-bindings":
            from scripts.workflow_pilot.trusted_review_gate import GitTree
            spec = module.resolve_subject(
                "TC-WORKFLOW-REVIEW-FAMILY-001", "authored-case-binding",
                json.loads((fixture / CASE_REGISTRY).read_bytes()),
            )
            binding_path.parent.mkdir(parents=True, exist_ok=True)
            binding_path.write_bytes(captured)
            if not (fixture / ".git").exists():
                fixture_git(fixture, "init", "-q")
            fixture_git(fixture, "add", BINDING_SOURCE)
            fixture_git(fixture, "commit", "-qm", "captured subject declarations")
            baseline_tree = GitTree(fixture, fixture_git(fixture, "rev-parse", "HEAD"))
            baseline = module.expand_members(spec, baseline_tree, baseline_tree)
            review.require(bool(baseline), "captured declaration backend has no members")
            source, bindings = subject_declaration_nodes(captured)
            for node, fields in bindings:
                node.args = []
                node.keywords = [ast.keyword(arg=key, value=ast.Constant(value=fields[key]))
                                 for key in ("model", "name", "case_id")]
            equivalent = ast.unparse(ast.fix_missing_locations(source)).encode()
            if equivalent != captured:
                binding_path.write_bytes(equivalent)
                fixture_git(fixture, "add", BINDING_SOURCE)
                fixture_git(fixture, "commit", "-qm", "equivalent keyword subject declarations")
            equivalent_tree = GitTree(fixture, fixture_git(fixture, "rev-parse", "HEAD"))
            try:
                observed = module.expand_members(spec, baseline_tree, equivalent_tree)
            except module.review.ReviewError as error:
                raise ContractViolation("equivalent keyword bindings were rejected") from error
            check(observed == baseline, "equivalent bindings changed captured production members")
            checks += 2
        return {"kind": "host", "checks": checks,
                "detail": "captured fixture producer executed on equivalent reviewed programs"}
    finally:
        program.write_bytes(original)
        if binding_original is not None:
            binding_path.write_bytes(binding_original)
        elif binding_path.exists():
            binding_path.unlink()
        os.chdir(root)


@probe_result("arm-object")
def _arm(enabled: bool) -> dict:
    common = [os.environ["MODERN_CC"], "-mcpu=arm7tdmi", "-mthumb",
              "-mthumb-interwork", "-mabi=aapcs", "-std=gnu89", "-ffreestanding",
              "-fno-builtin", "-Iinclude", "-Iinclude/generated",
              "-DFE8_EXPANSION_MODERN_BUILD=1"]
    if enabled:
        common.append("-DFE8_EXPANSION_AOE_REFERENCE=1")
    paths = (AOE_CORE, AOE_REFERENCE) if enabled else (AOE_REFERENCE,)
    objects = []
    for index, path in enumerate(paths):
        output = f"build/arm-{enabled}-{index}.o"
        command([*common, "-c", path, "-o", output])
        objects.append(output)
    symbols = command([os.environ["MODERN_NM"], "-S", objects[-1]]).decode()
    defined = {line.split()[-1] for line in symbols.splitlines()
               if len(line.split()) >= 4 and line.split()[-2].upper() != "U"}
    required = {"gExpansionAoEReferenceProbe", "ExpansionAoEReference_Heal"}
    check(required <= defined if enabled else not (required & defined),
          "enabled/disabled reference ELF symbol contract violated")
    if enabled:
        core_symbols = command([os.environ["MODERN_NM"], "-S", objects[0]]).decode()
        entries = {line.split()[-1]: int(line.split()[1], 16)
                   for line in core_symbols.splitlines() if len(line.split()) == 4}
        check("sItemRoutes" not in entries, "core retained an always-live route registry")
        check("sItemDispatchActive" in entries and entries["sItemDispatchActive"] <= 4,
              "dispatch reentrancy state budget violated")
        sizes, text = [], []
        for obj in objects:
            sections = command([os.environ["MODERN_SIZE"], "-A", obj]).decode()
            ewram = [int(item) for item in re.findall(r"^ewram_data\s+(\d+)", sections, re.M)]
            check(len(ewram) == 1 and ewram[0] > 0,
                  "enabled AoE object lacks EWRAM placement: " + obj)
            sizes.extend(ewram)
            text.extend(int(item) for item in re.findall(r"^\.text\s+(\d+)", sections, re.M))
        check(sum(sizes) <= 128, "AoE EWRAM budget exceeded")
        check(sum(text) <= 8 * 1024, "AoE text budget exceeded")
    return {"kind": "arm-object", "checks": len(objects),
            "detail": "AAPCS object symbols/sections checked; not target-ROM execution"}


@probe_result("parsed")
def _generated(probe: str) -> dict:
    from scripts.generated_data import registry
    from scripts.generated_data import cli
    from scripts.generated_data.diagnostics import DiagnosticCollector, GeneratedDataError
    from scripts.generated_data.eventlists import parser

    name = probe.partition(":")[2] if probe.startswith("generated-owner:") else "eventlists"
    schema = registry.REGISTRY.resolve(name)

    def validated(selected):
        try:
            records, diagnostics = cli._load_and_validate(selected, selected.default_source)
            return records, diagnostics.errors
        except GeneratedDataError as error:
            return None, [error]

    records, errors = validated(schema)
    if probe.startswith("generated-owner:"):
        check(not errors, "\n".join(str(item) for item in errors)[:review.MAX_DETAIL])
        event_schema = registry.REGISTRY.resolve("eventlists")
        if name in event_schema.optional_dependency_tables():
            _, errors = validated(event_schema)
    if errors:
        return {"kind": "parsed", "verdict": "unavailable", "checks": 0,
                "blocked_by": ["owners:eventlists"],
                "detail": ("eventlists prerequisite failed: " +
                           "\n".join(str(item) for item in errors))[:review.MAX_DETAIL]}
    if probe.startswith("generated-owner:"):
        malformed = Path("build/malformed-owner.json")
        malformed.write_text('{"schema_version":null}', encoding="utf-8")
        try:
            bad = schema.load_records(str(malformed))
            bad_diagnostics = DiagnosticCollector()
            schema.validate(bad, bad_diagnostics)
            rejected = bool(bad_diagnostics.errors)
        except GeneratedDataError:
            rejected = True
        check(rejected, "owner accepted malformed representation")
    elif probe == "generated-drift":
        check(Path(schema.default_inventory_path).read_text() == schema.build_inventory(records),
              "committed inventory drift")
    elif probe == "generated-consumer":
        errors = schema.round_trip_errors(records, schema.default_hand_source)
        check(not errors, "\n".join(str(item) for item in errors)[:2000])
        altered = Path("build/altered-consumer.h")
        altered.write_text("", encoding="utf-8")
        try:
            rejected = bool(schema.round_trip_errors(records, str(altered)))
        except GeneratedDataError:
            rejected = True
        check(rejected,
              "consumer parser failed to detect omitted output")
    elif probe == "generated-output":
        output = Path("build") / schema.default_output_name
        output.write_text(schema.generate_c(records, schema.default_source), encoding="utf-8")
        parsed = parser.parse_hand_written(str(output), records)
        check(not parser.compare_records(records, parsed, hand_path=str(output)),
              "generated output does not parse back to its typed source")
        output.write_text("", encoding="utf-8")
        try:
            rejected = bool(schema.round_trip_errors(records, str(output)))
        except GeneratedDataError:
            rejected = True
        check(rejected,
              "generated output omission was not detected")
    else:
        raise review.ReviewError("unknown generated-data probe")
    return {"kind": "parsed", "checks": 2 if probe != "generated-drift" else 1,
            "detail": name + ": actual schema/producer/consumer contract checked"}


@probe_result("host")
def _session_probe(probe: str) -> dict:
    # This is a registered production subject, not an import-name trust test.
    source = Path("build/review-subject.py").read_bytes()
    module = types.ModuleType("_review_subject")
    sys.modules[module.__name__] = module
    exec(compile(source, "reviewed-subject:review_family.py", "exec"), module.__dict__)
    a, b = "a" * 40, "b" * 40

    def decision(number, outcome="changes-requested", head=a):
        return module.Triage(module.ReviewFact(str(number), head, "copilot", "COMMENTED",
                            f"2026-01-01T00:00:{number:02d}Z", "triaged content", ()), outcome)

    def rejects(call):
        try:
            call()
        except ValueError:
            return True
        return False

    state = module.RoundState()
    if probe in {"lifecycle:preservation", "lifecycle:terminals", "wire:replay"}:
        for number in (1, 2, 3):
            state.observe(decision(number))
        held = state.hold
        check(held == ("3", a), "third request must hold")
        state.observe(decision(4, "clean", b))
        check(state.hold == held, "later clean/new head cleared architecture hold")
        if probe == "wire:replay":
            check(rejects(lambda: state.observe(decision(4))), "duplicate review accepted")
        if probe == "lifecycle:terminals":
            bad = module.Disposition("3", b, "coordinator", "redesign", "new model")
            check(rejects(lambda: state.dispose(bad, "coordinator")), "wrong-head disposition")
            state.dispose(module.Disposition("3", a, "coordinator", "redesign", "new model"),
                          "coordinator")
            check(state.hold is None, "valid architecture disposition did not resume")
    elif probe == "lifecycle:resets":
        state.observe(decision(1))
        state.observe(decision(2, "clean"))
        state.observe(decision(3))
        check(state.consecutive == 1 and state.hold is None, "clean-before-hold reset")
        check(rejects(lambda: state.dispose(
            module.Disposition("1", a, "coordinator", "redesign", "bad"), "coordinator")),
            "disposition without hold accepted")
    elif probe == "lifecycle:entries":
        calls = []
        runtime = types.SimpleNamespace(start=lambda **kw: calls.append(kw) or "task-1")
        session = module.ReviewSession("coordinator", "implementer", frozenset({"case"}), a)
        session.begin(runtime, "reviewer")
        check(calls[0]["role"] == "code-review", "wrong review tool role")
        check(rejects(lambda: session.begin(runtime, "reviewer-2")), "overlapping reviewer")
        check(rejects(lambda: session.read_action("push", lambda: calls.append("push"))),
              "reviewer mutation dispatched")
        check(len(calls) == 1, "denied action reached runtime")
    else:
        raw = {"schema_version": 1, "repository": "owner/repo", "pull_request": 1,
               "base_sha": a, "candidate_sha": b,
               "subjects": [{"case_id": "TC-TEST-001", "subject": "fixture"}], "findings": []}
        check(module.validate_request(module.parse_json(json.dumps(raw).encode())) == raw,
              "valid request round trip failed")
        if probe == "wire:producers":
            check(rejects(lambda: module.parse_json(b'{"x":1,"x":2}')),
                  "duplicate field emitted as valid input")
        elif probe == "wire:consumers":
            raw["pass"] = True
            check(rejects(lambda: module.validate_request(raw)), "success label admitted")
        elif probe == "wire:validators":
            raw["candidate_sha"] = "HEAD"
            check(rejects(lambda: module.validate_request(raw)), "non-exact head admitted")
        elif probe == "wire:stale-bindings":
            key = module.subject_key(raw["subjects"][0])
            members = tuple(module.Obligation(
                key, "resource", role + ":fixture", role, "fixture producer", "fixture consumer",
                "typed observation", "exact head", "fixture", "host", ("positive",),
                (REVIEW_SOURCE,)) for role in ("enabled", "disabled"))
            observations = tuple(module.Observation(
                item, b, a, ((REVIEW_SOURCE, a),), "satisfied", item.evidence,
                "controlled reducer input", 1, "host") for item in members)
            session = module.ReviewSession("coordinator", "implementer", frozenset({key}), b,
                                           identity=("owner/repo", 1, a))
            arguments = dict(tool_revision=a, remote_reviews=(), triage=(), pre_review_required=False)
            result = module.assess_handoff(raw, members, observations, session, **arguments)
            check(result["candidate_sha"] == b, "correct-head observations did not join")
            session.advance(a)
            check(rejects(lambda: module.assess_handoff(
                raw, members, observations, session, **arguments)), "stale session head admitted")
        else:
            raise review.ReviewError("unknown review-session probe")
    return {"kind": "host", "checks": 2, "detail": "registered production reducer executed"}


def run_probe(probe: str) -> dict:
    allowed = {
        *(f"aoe-phase:{phase}" for phase in PHASES),
        *(f"aoe-shape:{shape}" for shape in SHAPES),
        "aoe-items", "aoe-targets", "aoe-slots", "aoe-execution",
        "aoe-reference:enabled", "aoe-reference:disabled", "aoe-arm:enabled", "aoe-arm:disabled",
        "generated-output", "generated-consumer", "generated-drift",
        *("authored-case:" + role for role in review.FAMILIES["generated"]),
        *("authored-binding:" + role for role in review.FAMILIES["generated"]),
        *("authored-fixture:" + role for role in review.FAMILIES["wire"]),
        *(f"{family}:{role}" for family in ("lifecycle", "wire") for role in review.FAMILIES[family]),
    }
    if probe.startswith("generated-owner:"):
        from scripts.generated_data import registry
        review.require(probe.partition(":")[2] in registry.REGISTRY.all_names(),
                       "unregistered generated owner")
    else:
        review.require(probe in allowed, "unregistered probe")
    if probe.startswith("aoe-arm:"):
        return _arm(probe == "aoe-arm:enabled")
    if probe.startswith("aoe-"):
        return _native(probe)
    if probe.startswith("generated-"):
        return _generated(probe)
    if probe.startswith("authored-case:"):
        return _authored_case(probe)
    if probe.startswith("authored-binding:"):
        return _authored_binding(probe)
    if probe.startswith("authored-fixture:"):
        return _authored_fixture(probe)
    if probe.startswith(("lifecycle:", "wire:")):
        return _session_probe(probe)
    raise review.ReviewError("unregistered probe")


def worker(probes: list[str]) -> list[dict]:
    results = []
    for probe in probes:
        try:
            result = run_probe(probe)
        except Exception as error:
            result = {"kind": None, "verdict": "unavailable", "checks": 0,
                      "detail": f"{type(error).__name__}: {error}"[:review.MAX_DETAIL]}
        results.append({"probe": probe, "blocked_by": [], **result})
    return results
