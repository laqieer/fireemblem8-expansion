"""Closed bindings to existing cases; no candidate-supplied programs or plugins."""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
import io
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
    SubjectSpec("TC-PROJECT-SELECTION-001", "project-selection", "project-selection"),
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
AUTHORED_CASE_ID = "TC-LOCALIZATION-AUTHORED-SCOPE-001"
AUTHORED_FEATURE = "full-game-localization"
AUTHORED_FEATURE_CASES = frozenset({
    "TC-LOCALIZATION-003", "TC-CJK-INVENTORY-ORDER-001", AUTHORED_CASE_ID,
})
CASE_SECTIONS = frozenset({
    "Actions", "Expected result", "Negative control",
    "Interactions and save compatibility", "Automation", "Cleanup and limitations",
})
PROJECT_DECLARATION = "project_selection.mk"
PROJECT_CONFIG = "scripts/modernize/expansion_config.py"
PROJECT_REGISTRY = "scripts/generated_data/registry.py"
PROJECT_SCHEMA_INPUTS = "scripts/generated_data/schema.py"
PROJECT_CONTEXT_FIELDS = frozenset({
    "sources", "message_header", "localization_registry", "localization_catalogs", "asset_manifest",
})
PROJECT_SCHEMAS = frozenset({
    "supports", "units", "shops", "traps", "items", "eventscripts", "eventlists",
    "chapterbundle", "classes", "characters", "terrainstats", "movecost",
    "weapontriangle", "ui_presentation", "chapterobjectives", "autoplaystrategies",
})
PROJECT_SETTINGS = frozenset({
    "MODERN_ROM_SIZE", "EXPANSION_ROM_TITLE", "EXPANSION_ENABLED_LOCALES",
    "EXPANSION_DEFAULT_LOCALE", "EXPANSION_SAVE_COMPAT_EPOCH",
})
PROJECT_GAME_OPTIONS = frozenset({
    "mapping", "width-registry", "fixed-width-aliases", "eu-mapping",
    "ja-indexed", "ja-raw", "zh-indexed", "zh-raw", "latin-span-review",
    "unicode-script-review", "raw-closure",
})


def project_declaration_model(tree):
    source = ast.parse(tree.read(PROJECT_REGISTRY))
    functions = {item.name: item for item in source.body if isinstance(item, ast.FunctionDef)}
    schemas = {}
    for item in source.body:
        if not (isinstance(item, ast.Expr) and isinstance(item.value, ast.Call)
                and isinstance(item.value.func, ast.Attribute)
                and item.value.func.attr == "register_factory"):
            continue
        args = item.value.args
        review.require(len(args) == 3 and isinstance(args[2], ast.Name),
                       "unknown project schema factory needs a reviewed finite model")
        name, version = ast.literal_eval(args[0]), ast.literal_eval(args[1])
        review.require(args[2].id in functions,
                       "missing project schema factory needs a reviewed finite model")
        factory = functions[args[2].id]
        imports = [node for node in factory.body if isinstance(node, ast.ImportFrom)]
        returns = [node.value for node in factory.body if isinstance(node, ast.Return)]
        review.require(
            len(imports) == 1 and imports[0].level == 1 and len(imports[0].names) == 1
            and len(returns) == 1 and isinstance(returns[0], ast.Call)
            and isinstance(returns[0].func, ast.Name)
            and returns[0].func.id == (imports[0].names[0].asname or imports[0].names[0].name)
            and not returns[0].args and not returns[0].keywords,
            "unknown project schema owner needs a reviewed finite model")
        schemas[name] = (version, imports[0].module)
    review.require(schemas == {name: (1, name + ".schema") for name in PROJECT_SCHEMAS},
                   "project schema members or owners need a reviewed finite model")
    config = ast.parse(tree.read(PROJECT_CONFIG))
    validators = [
        item for function in config.body
        if isinstance(function, ast.FunctionDef) and function.name == "validate_project_requirements"
        for item in function.body
        if isinstance(item, ast.Assign) and isinstance(item.value, ast.Dict)
    ]
    review.require(len(validators) == 1
                   and {ast.literal_eval(key) for key in validators[0].value.keys} == PROJECT_SETTINGS,
                   "project setting members need a reviewed finite model")
    make = re.sub(r"\\\n[ \t]*", " ", tree.read(PROJECT_DECLARATION).decode())
    declarations = {}
    for name in ("GAME_OPTIONS", "GAME_CJK_LOCALES", "GAME_EU_LOCALES", "FONT_SLOTS"):
        matches = re.findall(r"^FE8_PROJECT_" + name + r"\s*:=\s*([^\n#]*)", make, re.M)
        review.require(len(matches) == 1, "missing or ambiguous project declaration")
        declarations[name] = tuple(sorted(matches[0].split()))
    expected = {
        "GAME_OPTIONS": tuple(sorted(PROJECT_GAME_OPTIONS)),
        "GAME_CJK_LOCALES": ("ja", "zh-Hans"),
        "GAME_EU_LOCALES": ("de", "es", "fr", "it"),
        "FONT_SLOTS": ("src/data/localized_font_data.c", "src/fontgrp-data.c"),
    }
    review.require(declarations == expected, "project provider declarations need a reviewed finite model")
    contexts = [
        item for declaration in ast.parse(tree.read(PROJECT_SCHEMA_INPUTS)).body
        if isinstance(declaration, ast.ClassDef) and declaration.name == "SchemaInputs"
        for item in declaration.body
        if isinstance(item, ast.FunctionDef) and item.name == "__init__"
    ]
    review.require(len(contexts) == 1, "unknown project input constructor")
    parameters = {argument.arg for argument in (
        *contexts[0].args.posonlyargs, *contexts[0].args.args, *contexts[0].args.kwonlyargs,
    )} - {"self"}
    review.require(contexts[0].args.vararg is None and contexts[0].args.kwarg is None
                   and parameters in (PROJECT_CONTEXT_FIELDS,
                                      PROJECT_CONTEXT_FIELDS - {"asset_manifest"}),
                   "project context fields need a reviewed finite model")
    return json.dumps({"schemas": schemas, "settings": sorted(PROJECT_SETTINGS),
                       "declarations": declarations,
                       "context_fields": sorted(PROJECT_CONTEXT_FIELDS)}, sort_keys=True)


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
        if isinstance(item, ast.Assign) and len(item.targets) == 1:
            target = item.targets[0]
        elif isinstance(item, ast.AnnAssign):
            target = item.target
        else:
            continue
        if (isinstance(target, ast.Name)
                and isinstance(item.value, ast.Dict)
                and any(isinstance(key, ast.Constant) and key.value == AUTHORED_FEATURE
                        for key in item.value.keys)):
            declarations.append((target.id, ast.literal_eval(item.value)))
    review.require(len(declarations) == 1, "unknown locale case metadata declaration")
    return declarations[0]


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
    elif spec.model == "project-selection":
        declaration = project_declaration_model(tree)
        for role, name, inputs in (
            ("actions", "profile-settings", (PROJECT_DECLARATION, PROJECT_CONFIG, "config.mk")),
            ("items", "provider-partition", (PROJECT_DECLARATION, PROJECT_REGISTRY)),
            ("targets", "modern-abi", (PROJECT_CONFIG, PROJECT_DECLARATION)),
        ):
            add("action", role, name, PROJECT_DECLARATION, PROJECT_CONFIG, declaration,
                "real public frontend with valid and adversarial declarations",
                "project:" + role, inputs)
        for role in review.FAMILIES["lifecycle"]:
            add("lifecycle", role, "invocation-context", PROJECT_CONFIG,
                PROJECT_DECLARATION, declaration,
                "public selection and metadata CLI preserve, reset and terminate invocation state",
                "project-life:" + role, (PROJECT_DECLARATION, PROJECT_CONFIG, "config.mk"))
        for role in review.FAMILIES["wire"]:
            inputs = {PROJECT_SCHEMA_INPUTS, PROJECT_REGISTRY}
            if role == "producers":
                inputs = {PROJECT_DECLARATION, PROJECT_CONFIG, "config.mk"}
            elif role == "stale-bindings":
                inputs.update({
                    "scripts/generated_data/items/schema.py",
                    "scripts/generated_data/json_loader.py", "scripts/generated_data/validators.py",
                })
            add("wire", role, "selected-inputs", PROJECT_DECLARATION,
                PROJECT_SCHEMA_INPUTS, declaration,
                "actual serialized Make arguments and invocation-local schema consumption",
                "project-wire:" + role, inputs)
        tool_paths = {
            REVIEW_SOURCE, "scripts/workflow_pilot/review_subjects.py", AOE_DRIVER, AOE_DISABLED,
            "scripts/workflow_pilot/__init__.py", "scripts/workflow_pilot/raw_diff_check.py",
            "scripts/workflow_pilot/trusted_review_gate.py", "scripts/workflow_pilot/reporter.py",
        }
        static_inputs = {
            PROJECT_DECLARATION, "config.mk", "include/constants/msg.h",
            *json.loads(declaration)["declarations"]["FONT_SLOTS"],
        }
        closure = tuple(sorted(
            path for path, (mode, kind, _) in tree.entries.items()
            if mode in {"100644", "100755"} and kind == "blob"
            and path not in tool_paths and "/tests/" not in "/" + path
            and (path in static_inputs or path.startswith("scripts/") and path.endswith(".py"))
        ))
        result = [replace(member, execution_inputs=closure) for member in result]
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
            "drift-checks": {CASE_REGISTRY, case["document"]},
        }
        predicates = {
            "owners": CASE_REGISTRY,
            "outputs": CASE_TEMPLATE,
            "consumers": LOCALE_CASE_CONSUMER,
            "drift-checks": case["document"],
        }
        for role in review.FAMILIES["generated"]:
            add("generated", role, AUTHORED_FEATURE, predicates[role],
                case["document"], mapping,
                "canonical owner, complete public procedure, consumer equality and indexed anchor",
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
        else:
            raise review.ReviewError("unknown authored case probe")
    return {"kind": "parsed", "checks": 1,
            "detail": "parsed canonical tester format/member contract; not locale or ROM behavior"}


@probe_result("host")
def _project(probe):
    from scripts.modernize import expansion_config as config
    from scripts.generated_data import registry
    from scripts.generated_data.diagnostics import GeneratedDataError
    from scripts.generated_data.schema import SchemaInputs, parse_source_overrides

    root = Path.cwd()
    project = root / "examples/review-project-source"
    project.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("input.json", "{}\n"), ("source.c", "int selected_source;\n"),
        ("texts.txt", "#0\nNative[X]\n"), ("defs.txt", "[X] = 0\n"),
    ):
        (project / name).write_text(content)
    modules = {
        *PROJECT_SCHEMAS, "assets", "code", "game-text", "ui-text", "fonts",
        *("game-" + name for name in PROJECT_GAME_OPTIONS),
        *("game-authored-" + locale for locale in ("ja", "zh-Hans", "fr", "de", "es", "it")),
        *("game-indexed-" + locale for locale in ("fr", "de", "es", "it")),
    }
    review.require(set(registry.REGISTRY.all_names()) == PROJECT_SCHEMAS,
                   "unknown production registry domain")
    relative = project.relative_to(root).as_posix()
    settings = {
        "MODERN_ROM_SIZE": "32M", "EXPANSION_ROM_TITLE": "NATIVEPROBE",
        "EXPANSION_ENABLED_LOCALES": "en", "EXPANSION_DEFAULT_LOCALE": "en",
        "EXPANSION_SAVE_COMPAT_EPOCH": "256",
    }

    def declare(*, inherit=None, replacement=(), extra="", selected_settings=None):
        chosen = settings if selected_settings is None else selected_settings
        (project / "project.mk").write_text(
            "FE8_PROJECT_PROFILES := probe\nFE8_PROJECT_DEFAULT_PROFILE := probe\n"
            + "FE8_PROJECT_REQUIRED_SETTINGS := " + " ".join(sorted(chosen)) + "\n"
            + "".join(f"FE8_PROJECT_probe_{key} := {value}\n" for key, value in chosen.items())
            + "FE8_PROJECT_INHERIT := " + " ".join(sorted(modules if inherit is None else inherit)) + "\n"
            + "FE8_PROJECT_REPLACE := " + " ".join(sorted(replacement)) + "\n" + extra
        )

    def selection():
        return config.load_project_selection(root, project.name)

    def identity(**overrides):
        return config.load_identity(
            root / "config.mk", config_preset="debug", abi=overrides.pop("abi", "aapcs"),
            rom_size=overrides.pop("rom_size", None), repo_root=root,
            build_id_override="abcdef12", project=project.name, **overrides,
        )

    def rejects(function):
        try:
            function()
        except config.ConfigError:
            return
        raise ContractViolation("production frontend accepted an invalid project declaration")

    declare()
    baseline = selection()
    review.require(baseline["project"] == project.name, "project frontend backend unavailable")
    checks = 1
    family, _, role = probe.partition(":")
    if family == "project-wire":
        fields = {
            "message_header": relative + "/selected-msg.h",
            "asset_manifest": relative + "/selected-assets.json",
            "localization_registry": relative + "/selected-ui.json",
            "localization_catalogs": {"en": relative + "/selected-en.json"},
        }
        sources = {name: relative + "/input.json" for name in sorted(PROJECT_SCHEMAS)}
        baseline = SchemaInputs()
        review.require(baseline.resolve("items").name == "items",
                       "schema input backend unavailable")
        try:
            selected = SchemaInputs(sources=sources, **fields)
        except TypeError as error:
            raise ContractViolation("accepted schema input fields were rejected") from error
        if role == "producers":
            declare(inherit=modules - {"items"}, replacement={"items"},
                    extra=f"FE8_PROJECT_SOURCE_items := {relative}/input.json\n")
            recipe = b"review-project-arguments:\n\t@printf '%s\\n' $(FE8_PROJECT_SCHEMA_ARGS)\n"
            values = command([
                "make", "--no-print-directory", "-s", "-f", PROJECT_DECLARATION, "-f", "-",
                "review-project-arguments", f"FE8_PROJECT={project.name}",
                "MODERN_PROJECT_MSG_HEADER=" + fields["message_header"],
                "PYTHON=" + sys.executable,
            ], stdin=recipe).decode().splitlines()
            check(values == [
                "--source-override", "items=" + relative + "/input.json",
                "--message-header", fields["message_header"],
                "--asset-manifest", "assets/manifest.json",
            ], "Make serialized a different selected owner/header/manifest")
            check(parse_source_overrides(values[1:2]) == {"items": relative + "/input.json"},
                  "serialized provider did not bind the actual schema parser")
            checks += 2
        elif role == "consumers":
            for name in sorted(PROJECT_SCHEMAS):
                schema = selected.resolve(name)
                check(schema.name == name and schema.replacement_source
                      and selected.source(schema) == sources[name],
                      "selected schema lost its concrete source owner")
                check(all(getattr(schema, key, None) == value for key, value in fields.items()),
                      "selected schema lost invocation-local payload fields")
                default = baseline.resolve(name)
                check(not default.replacement_source
                      and baseline.source(default) == default.default_source
                      and all(getattr(default, key, None) is None for key in fields),
                      "selected invocation mutated cached default schema fields")
                checks += 3
        elif role == "validators":
            valid = [name + "=" + sources[name] for name in sorted(PROJECT_SCHEMAS)]
            check(parse_source_overrides(valid) == sources,
                  "valid serialized source owners were rejected")
            for invalid in (
                ["unknown=input.json"], ["items="], ["=input.json"], ["items"],
                ["items=one.json", "items=two.json"],
            ):
                try:
                    parse_source_overrides(invalid)
                except GeneratedDataError:
                    checks += 1
                else:
                    raise ContractViolation("invalid serialized provider was accepted")
        elif role == "replay":
            other = SchemaInputs(
                sources={name: relative + "/other.json" for name in sources},
                message_header=relative + "/other-msg.h", asset_manifest=relative + "/other-assets.json",
            )
            for name in sorted(PROJECT_SCHEMAS):
                first = selected.resolve(name)
                second = other.resolve(name)
                fresh = baseline.resolve(name)
                check(first.message_header == fields["message_header"]
                      and second.message_header == relative + "/other-msg.h"
                      and fresh.message_header is None,
                      "replayed schema resolution rebound another invocation")
                check(selected.source(first) == sources[name]
                      and other.source(second) == relative + "/other.json"
                      and all(getattr(first, key, None) == value for key, value in fields.items()),
                      "replay retained stale source or mutated an earlier schema")
                checks += 2
        elif role == "stale-bindings":
            source = project / "symbolic-items.json"
            source.write_text(json.dumps({
                "$schema": "fe8.items.v1", "items": [{
                    "item": "ITEM_NONE", "weaponType": "ITYPE_SWORD", "iconId": 0,
                    "nameTextId": "MSG_REVIEW_CONTEXT",
                }],
            }))
            header = root / fields["message_header"]
            header.write_text("#define MSG_REVIEW_CONTEXT 256\n#define MSG_COUNT 1024\n")
            schema = selected.resolve("items")
            check(schema.message_header == str(header.relative_to(root)),
                  "item consumer lost the selected message header binding")
            try:
                first = schema.load_records(str(source))[0].name_text_id
                header.write_text("#define MSG_REVIEW_CONTEXT 257\n#define MSG_COUNT 1024\n")
                second = schema.load_records(str(source))[0].name_text_id
            except GeneratedDataError as error:
                raise ContractViolation("valid selected message symbol was rejected") from error
            check((first, second) == (256, 257),
                  "item loader ignored or cached a stale selected header")
            checks += 2
        else:
            raise review.ReviewError("unknown direct project wire probe")
    elif family == "project-life":
        def default_identity():
            return config.load_identity(
                root / "config.mk", config_preset="debug", abi="aapcs", rom_size=None,
                repo_root=root, build_id_override="abcdef12", project="default",
            )

        if role == "entries":
            check(config.load_project_selection(root, "default") == {
                "project": "default", "profile": "default",
                "settings": {}, "asset_manifest": None,
            }, "default selection acquired project state")
            check(identity().project_profile == "probe",
                  "declared default profile was not selected")
            for name in ("../escape", "bad name", "bad/name", ""):
                rejects(lambda name=name: config.load_project_selection(root, name))
            rejects(lambda: config.load_project_selection(root, project.name, "absent"))
            rejects(lambda: config.load_project_selection(root, "default", "probe"))
            checks += 8
        elif role == "preservation":
            default = default_identity()
            check("project" not in default.to_dict()
                  and "project_profile" not in default.to_dict()
                  and "project" not in default.fingerprint_fields(),
                  "selected context leaked into default metadata")
            saved = default.to_dict()
            selected = identity()
            check(selected.rom_size_bytes == 32 * 1024 * 1024
                  and selected.save_compat_epoch == 256,
                  "selected identity lost its independent resource/save settings")
            rejects(lambda: identity(rom_size="16M"))
            check(default.to_dict() == saved and default_identity().to_dict() == saved,
                  "selected or failed invocation changed default identity")
            checks += 4
        elif role == "resets":
            first = identity()
            saved = first.to_dict()
            declare(selected_settings={
                **settings, "MODERN_ROM_SIZE": "16M", "EXPANSION_ROM_TITLE": "RESETVALID",
                "EXPANSION_SAVE_COMPAT_EPOCH": "512",
            })
            updated = identity()
            check(updated.rom_size_bytes == 16 * 1024 * 1024
                  and updated.rom_title == "RESETVALID" and updated.save_compat_epoch == 512
                  and updated.config_fingerprint != first.config_fingerprint,
                  "a new invocation reused stale profile settings")
            declare()
            check(identity().to_dict() == saved and first.to_dict() == saved,
                  "restoring declaration failed to reset context without mutation")
            checks += 2
        elif role == "terminals":
            output = root / "build/project-lifecycle-output"
            arguments = [
                "generate", "--config-mk", str(root / "config.mk"), "--config", "debug",
                "--abi", "aapcs", "--rom-size", "32M",
                "--repo-root", str(root), "--build-id", "abcdef12",
                "--project", project.name, "--output-dir", str(output),
            ]
            with redirect_stdout(io.StringIO()):
                check(config.main(arguments) == 0, "valid metadata CLI did not complete")
            saved = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                     for path in output.iterdir()}
            check(set(saved) == {"expansion_build_metadata.json", "expansion_build_metadata.mk"},
                  "metadata CLI did not publish its complete owned output set")
            declare(selected_settings={**settings, "UNKNOWN_REQUIRED_SETTING": "1"})
            errors = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(errors):
                check(config.main(arguments) != 0 and bool(errors.getvalue().strip()),
                      "invalid metadata invocation silently completed")
            check({path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                   for path in output.iterdir()} == saved,
                  "failed invocation changed previously published metadata")
            declare()
            with redirect_stdout(io.StringIO()):
                check(config.main(arguments) == 0, "metadata CLI failed to recover")
            check({path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                   for path in output.iterdir()} == saved,
                  "unchanged recovery rewrote valid metadata")
            checks += 6
        else:
            raise review.ReviewError("unknown direct project lifecycle probe")
    elif role == "actions":
        valid = identity()
        check(valid.project == project.name and valid.project_profile == "probe",
              "public identity lost selected invocation context")
        for key, overrides in (
            ("MODERN_ROM_SIZE", {"rom_size": "16M"}),
            ("EXPANSION_ROM_TITLE", {"rom_title": "OTHER"}),
            ("EXPANSION_ENABLED_LOCALES", {"enabled_locales": "en,ja"}),
            ("EXPANSION_DEFAULT_LOCALE", {"default_locale": "en"}),
            ("EXPANSION_SAVE_COMPAT_EPOCH", {"save_compat_epoch": "512"}),
        ):
            chosen = dict(settings)
            if key == "EXPANSION_DEFAULT_LOCALE":
                chosen.update(EXPANSION_ENABLED_LOCALES="en,ja", EXPANSION_DEFAULT_LOCALE="ja")
            declare(selected_settings=chosen)
            identity()
            rejects(lambda: identity(**overrides))
            checks += 1
        declare(selected_settings={**settings, "UNKNOWN_REQUIRED_SETTING": "1"})
        rejects(selection)
        checks += 1
    elif role == "items":
        for module in sorted(modules):
            extra = ""
            if module == "code":
                extra = f"FE8_PROJECT_C_SOURCES := {relative}/source.c\n"
            elif module == "fonts":
                extra = f"FE8_PROJECT_FONT_REPLACEMENTS := src/fontgrp-data.c={relative}/source.c\n"
            elif module == "game-text":
                extra = (f"FE8_PROJECT_TEXT_MAIN := {relative}/texts.txt\n"
                         f"FE8_PROJECT_TEXT_DEFS := {relative}/defs.txt\n")
            elif module == "ui-text":
                extra = (f"FE8_PROJECT_UI_REGISTRY := {relative}/input.json\n"
                         f"FE8_PROJECT_UI_CATALOGS := en={relative}/input.json\n")
            else:
                extra = f"FE8_PROJECT_SOURCE_{module} := {relative}/input.json\n"
            declare(inherit=modules - {module}, replacement={module}, extra=extra)
            selection()
            declare(inherit=modules, replacement={module}, extra=extra)
            rejects(selection)
            declare(inherit=modules - {module})
            rejects(selection)
            checks += 3
    elif role == "targets":
        identity()
        rejects(lambda: identity(abi="apcs-gnu"))
        checks += 1
    else:
        raise review.ReviewError("unknown direct project probe")
    return {"kind": "host", "checks": checks,
            "detail": "real project declaration/identity functions executed; no compiler/ROM claim"}


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
        *("project:" + role for role in review.FAMILIES["action"]),
        *("project-life:" + role for role in review.FAMILIES["lifecycle"]),
        *("project-wire:" + role for role in review.FAMILIES["wire"]),
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
    if probe.startswith(("project:", "project-life:", "project-wire:")):
        return _project(probe)
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
