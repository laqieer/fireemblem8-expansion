"""Closed bindings to existing cases; no candidate-supplied programs or plugins."""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

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
    SubjectSpec("TC-WORKFLOW-BUILD-CI-SPLIT-001", "ci-lane-history", "ci-lane-history"),
    SubjectSpec("TC-CJK-RESOURCE-STORAGE-001", "cjk-resource-index", "cjk-resource"),
    SubjectSpec("TC-LOCALIZATION-AUTHORED-SCOPE-001", "authored-scope-case", "authored-case"),
    SubjectSpec("TC-PROJECT-SELECTION-001", "project-selection", "project-selection"),
    SubjectSpec("TC-TEST-QUALITY-001", "build-workflow-topology", "build-topology"),
)
AOE_CORE = "src/expansion_aoe.c"
AOE_HEADER = "include/expansion_aoe.h"
AOE_REFERENCE = "src/expansion_aoe_reference.c"
AOE_DRIVER = "tools/gba-playtest/tests/c/expansion_aoe_driver.c"
AOE_DISABLED = "tools/gba-playtest/tests/c/expansion_aoe_disabled_driver.c"
REVIEW_SOURCE = "scripts/workflow_pilot/review_family.py"
STAGED_TOOL_PATHS = (
    "scripts/workflow_pilot/__init__.py",
    "scripts/workflow_pilot/raw_diff_check.py",
    REVIEW_SOURCE,
    "scripts/workflow_pilot/review_subjects.py",
    AOE_DRIVER,
    AOE_DISABLED,
)
CI_ADAPTIVE = "scripts/workflow_pilot/adaptive_gate.py"
CI_METADATA = "scripts/workflow_pilot/pr_metadata.py"
CI_EVIDENCE = "scripts/workflow_pilot/candidate_evidence.py"
CI_HISTORY_SOURCES = (CI_ADAPTIVE, CI_METADATA, CI_EVIDENCE)
EVENT_SCHEMA = "scripts/generated_data/eventlists/schema.py"
EVENT_SOURCE = "src/data/ch2_eventlists.json"
PHASES = ("CAN_USE", "BEGIN_USE", "EXECUTE", "AI_SELECT")
SHAPES = ("DIAMOND", "SQUARE", "CROSS")
CJK_CODEC = "scripts/fonttools/cjk/resource_index.py"
CJK_INDEXES = ("fonts/cjk/fehrr-sources.json", "fonts/cjk/runtime_usage.json")
CJK_FIXTURE_PREFIXES = (
    "fonts/cjk", "graphics/fonts/cjk", "src", "include", "texts", "assets", "reports",
)
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
BUILD_WORKFLOW = ".github/workflows/build.yml"
BUILD_TOPOLOGY = "tests/workflows/test_build_ci_topology.py"
BUILD_VERIFY = "scripts/upstream_port/verify.py"
BUILD_UPSTREAM_TEST = "tests/upstream_port/test_verify.py"
BUILD_ARCHIVAL_HELPER = "scripts/ci/install_archival_compilers.sh"
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


def local_python_inputs(tree, roots):
    pending = set(roots)
    captured = set()

    def module(name, required=False):
        if not (name == "scripts" or name.startswith("scripts.")):
            return
        prefix = name.replace(".", "/")
        paths = (prefix + ".py", prefix + "/__init__.py")
        found = next((path for path in paths if path in tree.entries), None)
        if required:
            review.require(found is not None or any(
                path.startswith(prefix + "/") for path in tree.entries
            ), "missing declared local Python import: " + name)
        if found is not None:
            pending.add(found)
        parts = prefix.split("/")
        for count in range(1, len(parts)):
            initializer = "/".join(parts[:count]) + "/__init__.py"
            if initializer in tree.entries:
                pending.add(initializer)

    while pending:
        path = pending.pop()
        if path in captured:
            continue
        captured.add(path)
        package = path.removesuffix(".py").split("/")[:-1]
        try:
            source = ast.parse(tree.read(path), filename=path)
        except SyntaxError as error:
            raise review.ReviewError("invalid imported Python source: " + path) from error
        for node in ast.walk(source):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module(alias.name, required=True)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    review.require(node.level <= len(package), "local import escaped its package")
                    base = ".".join(package[:len(package) - node.level + 1])
                    if node.module:
                        base += "." + node.module
                else:
                    base = node.module or ""
                module(base, required=True)
                for alias in node.names:
                    module(base + "." + alias.name)
    return tuple(sorted(captured))


def build_job_blocks(text):
    review.require(text.count("\njobs:\n") == 1, "missing or ambiguous Build jobs")
    jobs = text.split("\njobs:\n", 1)[1]
    starts = list(re.finditer(r"^  ([A-Za-z][A-Za-z0-9_-]*):\n", jobs, re.M))
    review.require(starts, "missing Build job declarations")
    review.unique([match.group(1) for match in starts], "Build jobs")
    return {
        match.group(1): jobs[
            match.end():starts[index + 1].start() if index + 1 < len(starts) else len(jobs)
        ]
        for index, match in enumerate(starts)
    }


def build_step_blocks(job):
    fields = list(re.finditer(r"^    ([A-Za-z_][A-Za-z0-9_-]*):", job, re.M))
    steps = [index for index, item in enumerate(fields) if item.group(1) == "steps"]
    review.require(len(steps) == 1, "missing or ambiguous Build steps")
    start = steps[0]
    section = job[fields[start].end():fields[start + 1].start()
                  if start + 1 < len(fields) else len(job)]
    starts = list(re.finditer(r"^    -(?:[ \t]|\Z)", section, re.M))
    review.require(starts, "empty or unsupported Build step sequence")
    return tuple(section[
        match.start():starts[index + 1].start() if index + 1 < len(starts) else len(section)
    ] for index, match in enumerate(starts))


def build_source_model(workflow, topology, verifier):
    def values(source, names):
        declarations = {}
        for item in ast.parse(source).body:
            if isinstance(item, ast.Assign) and len(item.targets) == 1:
                target = item.targets[0]
                if isinstance(target, ast.Name):
                    declarations.setdefault(target.id, []).append(item.value)

        def evaluate(node, seen):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Tuple):
                return tuple(evaluate(value, seen) for value in node.elts)
            if isinstance(node, ast.Name):
                review.require(len(declarations.get(node.id, ())) == 1 and node.id not in seen,
                               "unknown Build source declaration")
                return evaluate(declarations[node.id][0], seen | {node.id})
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return evaluate(node.left, seen) + evaluate(node.right, seen)
            raise review.ReviewError("unsupported Build source declaration")

        review.require(all(len(declarations.get(name, ())) == 1 for name in names),
                       "missing or ambiguous Build source declaration")
        return tuple(evaluate(declarations[name][0], {name}) for name in names)

    workers, = values(topology, ("COMBINED_WORKERS",))
    mirrored, expected = values(verifier, ("_COMBINED_JOBS", "_EXPECTED_JOBS"))
    review.require(isinstance(workers, tuple) and workers == mirrored and workers,
                   "Build worker declarations disagree")
    jobs = build_job_blocks(workflow)
    review.require(isinstance(expected, tuple) and set(jobs) == set(expected)
                   and len(jobs) == len(expected) and set(workers) <= set(jobs),
                   "Build job declarations disagree")
    model = {}
    for name, job in jobs.items():
        selectors = []
        for step in build_step_blocks(job):
            match = re.match(r"^    -[ \t]+(name|uses):[ \t]*(.+)$", step.split("\n", 1)[0])
            review.require(match is not None, "unsupported Build step identity")
            value = re.sub(r"[ \t]+#.*$", "", match.group(2)).strip()
            review.require(value and not value.startswith(("'", '"', "|", ">")),
                           "unsupported Build step scalar")
            selectors.append((match.group(1), value, "\n      run: |\n" in step))
        review.unique([(kind, value) for kind, value, _ in selectors],
                      "Build step identities within job")
        model[name] = selectors
    return workers, model


def build_reorder_field(job, field):
    matches = list(re.finditer(r"^    ([A-Za-z_][A-Za-z0-9_-]*):", job, re.M))
    selected = [index for index, item in enumerate(matches) if item.group(1) == field]
    review.require(len(selected) == 1, "missing or ambiguous Build job field")
    blocks = [job[item.start():matches[index + 1].start()
                  if index + 1 < len(matches) else len(job)]
              for index, item in enumerate(matches)]
    index = selected[0]
    ordered = blocks[1:] + blocks[:1] if index == 0 else (
        blocks[index:index + 1] + blocks[:index] + blocks[index + 1:])
    return job[:matches[0].start()] + "".join(ordered)


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


def fixture_inputs(tree, probes):
    if any(probe.startswith("cjk-resource:generated:") for probe in probes):
        return {path for prefix in CJK_FIXTURE_PREFIXES for path in tree.under(prefix)}
    return set()


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


def _members(spec: SubjectSpec, tree, head) -> tuple[review.Obligation, ...]:
    result = []

    def add(family, role, name, producer, consumer, representation, revalidation,
            probe, inputs, *, kind="host", profile="host", evidence=("positive", "adversarial"),
            execution_inputs=()):
        result.append(review.Obligation(
            spec.key, family, role + ":" + name, role, producer, consumer,
            representation, revalidation, probe, profile, evidence, tuple(sorted(inputs)), kind,
            tuple(sorted(execution_inputs))))

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
    elif spec.model == "build-topology":
        workers, jobs = build_source_model(
            tree.read(BUILD_WORKFLOW).decode(), tree.read(BUILD_TOPOLOGY),
            tree.read(BUILD_VERIFY))
        closure = set(local_python_inputs(
            head, (BUILD_TOPOLOGY, BUILD_VERIFY, BUILD_UPSTREAM_TEST)))
        closure.update({
            BUILD_WORKFLOW, BUILD_ARCHIVAL_HELPER, "tests/workflows/__init__.py",
        })
        closure.difference_update({
            "scripts/workflow_pilot/__init__.py", "scripts/workflow_pilot/raw_diff_check.py",
            REVIEW_SOURCE, "scripts/workflow_pilot/review_subjects.py",
        })
        for job, selectors in jobs.items():
            representation = json.dumps(selectors, separators=(",", ":"))
            add("wire", "validators", "boundary-" + job, BUILD_WORKFLOW,
                BUILD_TOPOLOGY + ":_step_blocks", representation,
                "shallow and Unicode blanks cannot hide step fields",
                "build-topology:boundary:" + job, {BUILD_WORKFLOW, BUILD_TOPOLOGY},
                evidence=("positive", "adversarial"))
            add("wire", "validators", "target-boundary-" + job, BUILD_WORKFLOW,
                BUILD_VERIFY + ":run_gates", representation,
                "misindented job fields reject at each target boundary",
                "build-topology:target-boundary:" + job,
                {BUILD_WORKFLOW, BUILD_VERIFY, BUILD_ARCHIVAL_HELPER},
                evidence=("positive", "adversarial"))
            if any(literal for _, _, literal in selectors):
                add("wire", "replay", job, BUILD_WORKFLOW,
                    BUILD_TOPOLOGY + ":_literal_run_script and " + BUILD_VERIFY,
                    representation, "literal scripts retain commands across shallow blanks",
                    "build-topology:literal:" + job,
                    {BUILD_WORKFLOW, BUILD_TOPOLOGY, BUILD_VERIFY})
        for job in workers:
            representation = json.dumps(jobs[job], separators=(",", ":"))
            add("wire", "producers", job, BUILD_WORKFLOW, BUILD_TOPOLOGY + ":_errors",
                representation, "equivalent worker mappings preserve topology and sequence",
                "build-topology:producer:" + job,
                {BUILD_WORKFLOW, BUILD_TOPOLOGY, BUILD_VERIFY})
            add("wire", "consumers", job, BUILD_WORKFLOW, BUILD_VERIFY + ":run_gates",
                representation, "target gates accept equivalent and reject unsafe mappings",
                "build-topology:consumer:" + job,
                {BUILD_WORKFLOW, BUILD_VERIFY, BUILD_ARCHIVAL_HELPER})
            for name, source in (
                ("topology-order", BUILD_TOPOLOGY + ":_errors"),
                ("upstream-order", BUILD_VERIFY + ":_parse_workflow_structure_text"),
            ):
                add("wire", "validators", name + "-" + job, BUILD_WORKFLOW, source,
                    representation, "source parser accepts equivalent reordered workflow",
                    "build-topology:" + name + ":" + job,
                    {BUILD_WORKFLOW, BUILD_TOPOLOGY, BUILD_VERIFY})
        for name, test_path, test_class, test_method in (
            ("topology-order", BUILD_TOPOLOGY, "ConsolidatedBuildTopologyTests",
             "test_worker_blocks_can_precede_other_job_fields"),
            ("upstream-order", BUILD_UPSTREAM_TEST, "VerifyCliCwdTests",
             "test_worker_mapping_order_preserves_target_gate_equivalence"),
        ):
            add("wire", "validators", "source-test-" + name, BUILD_WORKFLOW,
                f"{test_path}:{test_class}.{test_method}", json.dumps(sorted(workers)),
                "actual source-revision regression test covers worker field permutations",
                "build-topology:source-test:" + name,
                {BUILD_WORKFLOW, BUILD_TOPOLOGY, BUILD_VERIFY, BUILD_UPSTREAM_TEST})
        add("wire", "stale-bindings", "width-script", BUILD_WORKFLOW,
            BUILD_VERIFY + ":run_gates", "target literal width-gate script",
            "target command after shallow blank cannot be ignored",
            "build-topology:stale:width-script",
            {BUILD_WORKFLOW, BUILD_TOPOLOGY, BUILD_VERIFY, BUILD_ARCHIVAL_HELPER})
        result = [replace(item, execution_inputs=tuple(sorted(set(item.inputs) | closure)))
                  for item in result]
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
    elif spec.model == "cjk-resource":
        paths = {
            CJK_CODEC, *CJK_INDEXES, "scripts/fonttools/cjk/package.py",
            "scripts/fonttools/cjk/inventory.py", "scripts/fonttools/cjk/cli.py",
        }
        closure = local_python_inputs(head, (
            CJK_CODEC, "scripts/fonttools/cjk/package.py",
            "scripts/fonttools/cjk/inventory.py", "scripts/fonttools/cjk/cli.py",
        ))
        for family in ("generated", "wire", "resource"):
            for role in review.FAMILIES[family]:
                producer = (CJK_CODEC + ":encode_index"
                            if family == "generated" and role == "outputs"
                            else "scripts/fonttools/cjk/package.py:write_resource_index"
                            if family == "generated" and role == "drift-checks" else CJK_CODEC)
                consumer = ("scripts/fonttools/cjk/package.py:check_compact_assets"
                            if family == "generated" and role == "consumers"
                            else "scripts/fonttools/cjk/inventory.py:check_generated_files"
                            if family == "generated" and role == "drift-checks" else CJK_CODEC)
                add(family, role, "resource-index", producer, consumer,
                    "plain JSON or version1 xz-base64 resource envelope",
                    "complete values, physical checksums, stream reuse and bounded rejection",
                    "cjk-resource:" + family + ":" + role,
                    paths | {
                        "scripts/localization/game_catalog/build.py",
                        "scripts/localization/game_catalog/control_streams.py",
                    } if family == "generated" else paths)
        result = [replace(member, execution_inputs=tuple(sorted(set(closure) | paths)))
                  for member in result]
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
            if role != "producers":
                inputs.update({
                    "scripts/generated_data/chapterbundle/schema.py",
                    "scripts/assets/tmx.py",
                })
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
        for role in review.FAMILIES["generated"]:
            add("generated", role, "selected-schemas", PROJECT_SCHEMA_INPUTS,
                "scripts/generated_data/cli.py", declaration,
                "all declared selected sources, actual output namespace and artifact consumers",
                "project-generated:" + role,
                (PROJECT_DECLARATION, PROJECT_REGISTRY, PROJECT_SCHEMA_INPUTS,
                 "scripts/generated_data/cli.py"),
                evidence=("positive", "adversarial", "generated"))
        for role in review.FAMILIES["resource"]:
            add("resource", role, "compiled-context", "modern.mk:MODERN_CFLAGS",
                "src/expansion_metadata.c:gExpansionMetadata",
                "FE8_PROJECT profile and embedded ExpansionMetadata",
                "real default/selected debug/release records and readonly/RAM contribution",
                "project-resource:" + role,
                (PROJECT_DECLARATION, "modern.mk", "src/expansion_metadata.c",
                 "include/expansion_metadata.h"),
                kind="arm-object", profile="debug-release-aapcs-" + role,
                evidence=("compile", "default") if role == "disabled" else ("compile",))
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
        generated_inputs = event_validation_inputs(tree) | set(tree.under("texts/expansion"))
        generated_inputs.update(tree.under("assets"))
        generated_inputs.update({
            "src/face.c", "src/portrait_data.c", "src/unit_icon_wait_data.c",
        })
        for asset in json.loads(tree.read("assets/manifest.json"))["assets"]:
            generated_inputs.update(asset["sources"])
        resource_inputs = generated_inputs | {
            "src/expansion_metadata.c", "Makefile",
            "src/banim_data.c", "linker_script_banim.txt",
            "graphics/banim/assets/img/banim_img_rules.mk",
            *(path for path in tree.entries if "/" not in path and path.endswith(".mk")),
        }
        result = [replace(
            member, execution_inputs=tuple(sorted(
                set(closure) | (generated_inputs if member.family == "generated"
                               else resource_inputs if member.family == "resource"
                               else {
                                   "src/data/chapter_settings.json", "src/data/data_8B363C.c",
                                   "assets/tmx/Ch2Map.tmx", "graphics/map/layout/Ch1Map.mar",
                                   "graphics/map/layout/Ch1Map.json",
                               } if member.family == "wire" and member.role != "producers"
                               else set())
            )),
        ) for member in result]
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
            "validators": "validate_reviewer_activity", "replay": "RoundState.observe",
            "stale-bindings": "assess_handoff",
        }
        for family in ("lifecycle", "wire"):
            for role in review.FAMILIES[family]:
                add(family, role, "review-session", REVIEW_SOURCE + ":" + predicates[role],
                    "trusted coordinator", "typed requests/rounds/task observations",
                    "exact scope/head/round and sticky hold", family + ":" + role,
                    (REVIEW_SOURCE,))
    elif spec.model == "ci-lane-history":
        closure = set(local_python_inputs(tree, CI_HISTORY_SOURCES)) - set(STAGED_TOOL_PATHS)
        predicates = {
            ("lifecycle", "entries"): (CI_METADATA + ":_run_mode", CI_ADAPTIVE + ":_history_lane",
                                       CI_HISTORY_SOURCES),
            ("lifecycle", "preservation"): (CI_ADAPTIVE + ":_history_lane",
                                            CI_ADAPTIVE + ":_reserved_dispatch",
                                            CI_HISTORY_SOURCES),
            ("lifecycle", "resets"): (CI_ADAPTIVE + ":_history_lane",
                                      CI_ADAPTIVE + ":_history_lane",
                                      CI_HISTORY_SOURCES),
            ("lifecycle", "terminals"): (CI_METADATA + ":_pre_full_preparation",
                                         CI_ADAPTIVE + ":_reserved_dispatch",
                                         CI_HISTORY_SOURCES),
            ("wire", "producers"): (CI_METADATA + ":_run_mode", CI_METADATA + ":_run_mode",
                                    (CI_METADATA, CI_EVIDENCE)),
            ("wire", "consumers"): (CI_ADAPTIVE + ":_history_lane",
                                    CI_ADAPTIVE + ":_finalize_intake",
                                    CI_HISTORY_SOURCES),
            ("wire", "validators"): (CI_EVIDENCE + ":preflight_success",
                                     CI_ADAPTIVE + ":_history_lane", CI_HISTORY_SOURCES),
            ("wire", "replay"): (CI_ADAPTIVE + ":_history_lane", CI_ADAPTIVE + ":_history_lane",
                                 CI_HISTORY_SOURCES),
            ("wire", "stale-bindings"): (CI_ADAPTIVE + ":_history_lane",
                                         CI_ADAPTIVE + ":_reserved_dispatch",
                                         CI_HISTORY_SOURCES),
        }
        for path, names in (
            (CI_ADAPTIVE, {"_history_lane", "_finalize_intake", "_reserved_dispatch"}),
            (CI_METADATA, {"_run_mode", "_pre_full_preparation", "_current_full_authorization"}),
            (CI_EVIDENCE, {"preflight_success"}),
        ):
            functions = [item.name for item in ast.parse(tree.read(path)).body
                         if isinstance(item, ast.FunctionDef)]
            review.require(all(functions.count(name) == 1 for name in names),
                           "CI history predicate needs a reviewed finite model")
        for (family, role), (producer, consumer, paths) in predicates.items():
            add(family, role, "ci-lane-history", producer, consumer,
                "bound historical/current Build jobs and candidate identity",
                "exact lane, retry, metadata and dispatch authority",
                "ci-history:" + family + ":" + role, paths, execution_inputs=closure)
    else:
        raise review.ReviewError("unknown finite source model")
    for path in {path for member in result for path in member.inputs}:
        tree.oid(path)
    return tuple(result)


def expand_members(spec: SubjectSpec, origin_tree, candidate_tree):
    before = _members(spec, origin_tree, candidate_tree)
    after = _members(spec, candidate_tree, candidate_tree)
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
    if family == "project-generated":
        return _project_generated(role, root, project, modules, declare)
    if family == "project-resource":
        return _project_resources(role, root, project)
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
        if role != "producers":
            checks += _project_map_dimensions(root, project)
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
            result = run_process([
                "make", "--no-print-directory", "-s", "NODEP=1",
                "FE8_PROJECT=" + project.name, "MODERN_CONFIG=debug", "MODERN_ABI=aapcs",
                "PYTHON=" + sys.executable, "PYTHON3=" + sys.executable,
                "game-localization-validate", "game-localization-generate",
                "game-localization-check", "game-localization-width-check",
                "game-localization-leakage-audit", "game-localization-leakage-check",
                "game-localization-budget",
            ], cwd=root, env=os.environ.copy(), timeout=60, new_session=False)
            check(result.returncode == 0,
                  "valid English-only standalone locale work failed: "
                  + result.stderr.decode(errors="replace")[-1500:])
            check(not (root / "build/projects" / project.name
                       / "probe/debug/aapcs/generated/game-localization").exists(),
                  "English-only standalone work generated translated payloads")
            checks += 2
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


def _project_map_dimensions(root, project):
    import shutil
    from scripts.generated_data.chapterbundle.schema import read_chapter_map_dimensions
    from scripts.generated_data.diagnostics import GeneratedDataError

    checks = 0
    manifest = project / "selected-map-assets.json"
    for kind, chapter, symbol, original_sources, expected in (
        ("tiled-tmx-map-layout", 2, "Ch2Map", ("assets/tmx/Ch2Map.tmx",), (15, 15)),
        ("chapter-map-layout", 1, "Ch1Map",
         ("graphics/map/layout/Ch1Map.mar", "graphics/map/layout/Ch1Map.json"), (15, 10)),
    ):
        sources = []
        for original in original_sources:
            target = project / Path(original).name
            shutil.copyfile(root / original, target)
            sources.append(target.relative_to(root).as_posix())
        asset = {
            "id": "SELECTED_MAP", "kind": kind, "sources": sources,
            "ownership": {"symbol": symbol},
            "resources": {"mapWidth": expected[0], "mapHeight": expected[1]},
        }

        def read():
            manifest.write_text(json.dumps({"assets": [asset]}))
            return read_chapter_map_dimensions(
                chapter, chapter_settings_path=str(root / "src/data/chapter_settings.json"),
                asset_table_path=str(root / "src/data/data_8B363C.c"),
                asset_manifest_path=str(manifest), require_manifest=True,
            )

        def reject():
            nonlocal checks
            try:
                read()
            except GeneratedDataError:
                checks += 1
                return
            raise ContractViolation("invalid selected map source/dimensions were accepted")

        try:
            observed = read()
        except GeneratedDataError as error:
            raise ContractViolation("valid selected " + kind + " was rejected") from error
        check(observed == expected, "selected map kind lost its actual source dimensions")
        checks += 1
        asset["resources"]["mapWidth"] += 1
        reject()
        asset["resources"]["mapWidth"] -= 1
        source = root / sources[-1]
        original = source.read_bytes()
        try:
            source.write_bytes(b"not an authored map\n")
            reject()
            source.unlink()
            reject()
        finally:
            source.write_bytes(original)
        asset["sources"] = []
        reject()
    return checks


def _project_resources(role, root, project):
    from scripts.modernize import expansion_config as config
    from scripts.modernize.verify_rom_header import (
        EXPANSION_METADATA_STRUCT, ExpansionMetadataError, verify_expansion_metadata,
    )

    selected = project.name if role == "enabled" else "default"
    expected_size = (32 if role == "enabled" else 16) * 1024 * 1024
    objcopy = command([os.environ["MODERN_CC"], "-print-prog-name=objcopy"]).decode().strip()
    review.require(bool(objcopy), "selected ARM compiler did not resolve objcopy")
    checks = 0
    for preset in ("debug", "release"):
        directory = root / "build/project-resources" / role / preset
        directory.mkdir(parents=True, exist_ok=True)
        recipe = "review-resource-flags:;@echo REVIEW_RESOURCE_FLAGS $(MODERN_CFLAGS)"
        output = command([
            "make", "--no-print-directory", "-n", "-s", "--eval=" + recipe,
            "review-resource-flags", "expansion-modern-toolchain-check",
            "FE8_PROJECT=" + selected, "MODERN_CONFIG=" + preset, "MODERN_ABI=aapcs",
            "EXPANSION_BUILD_ID=abcdef12", "PYTHON=" + sys.executable,
            "MODERN_CC=" + os.environ["MODERN_CC"],
        ]).decode()
        flags = [line for line in output.splitlines()
                 if line.startswith("echo REVIEW_RESOURCE_FLAGS ")]
        review.require(len(flags) == 1, "actual Make compiler flags are missing or ambiguous")
        object_path, binary = directory / "metadata.o", directory / "metadata.bin"
        command([
            os.environ["MODERN_CC"], *shlex.split(flags[0])[2:],
            "-c", "src/expansion_metadata.c", "-o", str(object_path),
        ])
        command([
            objcopy, "-O", "binary", "--only-section=.rodata",
            str(object_path), str(binary),
        ])
        expected = config.load_identity(
            root / "config.mk", config_preset=preset, abi="aapcs", rom_size=None,
            repo_root=root, build_id_override="abcdef12", project=selected,
        )
        try:
            metadata = verify_expansion_metadata(binary.read_bytes(), expected.to_dict())
        except ExpansionMetadataError as error:
            raise ContractViolation("compiled project identity differs: " + str(error)) from error
        check(metadata["rom_size_bytes"] == expected_size
              and metadata["config_preset"] == preset and metadata["abi"] == "aapcs",
              "compiled default/selected resource profile differs")
        symbols = command([
            os.environ["MODERN_NM"], "-S", "--defined-only", "--extern-only", str(object_path),
        ]).decode().splitlines()
        check(len(symbols) == 1 and symbols[0].split()[-1] == "gExpansionMetadata"
              and int(symbols[0].split()[1], 16) == EXPANSION_METADATA_STRUCT.size,
              "embedded metadata lost its public sized symbol")
        sizes = command([os.environ["MODERN_SIZE"], "-B", str(object_path)]).decode().splitlines()
        allocated = tuple(int(value) for value in sizes[-1].split()[:3])
        check(allocated == (EXPANSION_METADATA_STRUCT.size, 0, 0),
              "project identity changed its fixed readonly or mutable-memory contribution")
        checks += 4
    return {
        "kind": "arm-object", "checks": checks,
        "detail": "real Make/AAPCS debug/release embedded records and sections; not full ROM/RAM",
    }


def _project_generated(role, root, project, modules, declare):
    import shutil
    from scripts.generated_data import cli, idspace, registry
    from scripts.generated_data.schema import SchemaInputs

    sources = {}
    for name in sorted(PROJECT_SCHEMAS):
        schema = registry.REGISTRY.resolve(name)
        original = Path(schema.default_source)
        selected = project / "providers" / name / original.name
        selected.parent.mkdir(parents=True, exist_ok=True)
        if original.is_dir():
            selected.mkdir(exist_ok=True)
            for path in schema.source_paths(str(original)):
                shutil.copyfile(path, selected / Path(path).name)
        else:
            shutil.copyfile(original, selected)
        sources[name] = selected.relative_to(root).as_posix()
    bundle_schema = registry.REGISTRY.resolve("chapterbundle")
    for path in bundle_schema.source_paths(sources["chapterbundle"]):
        path = Path(path)
        document = json.loads(path.read_bytes())
        for name, table in document.get("tables", {}).items():
            if name in sources:
                table["source"] = sources[name]
        for name, field in (
            ("supports", "supportOwners"), ("chapterobjectives", "chapterObjectives"),
            ("autoplaystrategies", "autoplayStrategies"),
        ):
            if field in document:
                document[field]["source"] = sources[name]
        path.write_text(json.dumps(document, indent=2) + "\n")
    declare(
        inherit=modules - set(sources), replacement=set(sources),
        extra="".join(f"FE8_PROJECT_SOURCE_{name} := {path}\n"
                      for name, path in sorted(sources.items())),
    )
    serialized = command([
        "make", "--no-print-directory", "-s", "-f", PROJECT_DECLARATION, "-f", "-",
        "review-project-generation", f"FE8_PROJECT={project.name}",
        "MODERN_CONFIG=debug", "MODERN_ABI=aapcs",
        "MODERN_BUILD_ROOT=build/review-project-generation",
        "MODERN_PROJECT_MSG_HEADER=include/constants/msg.h", "PYTHON=" + sys.executable,
    ], stdin=(
        b"review-project-generation:\n"
        b"\t@printf '%s\\n' $(FE8_PROJECT_OUTPUT_DIR) $(FE8_PROJECT_SCHEMA_ARGS)\n"
    )).decode().splitlines()
    output_root = Path(serialized[0])
    check(output_root == Path("build/review-project-generation/projects") / project.name
          / "probe/debug/aapcs", "producer lost a selected namespace dimension")
    arguments = serialized[1:]
    outputs = output_root / "data"
    inventories = output_root / "inventories"
    inputs = SchemaInputs(sources=sources, message_header="include/constants/msg.h")
    committed = {
        path: Path(path).read_bytes()
        for path in (registry.REGISTRY.resolve(name).default_inventory_path
                     for name in sorted(PROJECT_SCHEMAS))
    }
    checks = 1

    def invoke(action, name):
        errors = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(errors):
            try:
                status = cli.main([
                    *arguments, "--inventory-dir", str(inventories),
                    action, "--table", name,
                    *([] if action == "validate" else ["--out-dir", str(outputs)]),
                ])
            except SystemExit as error:
                raise RuntimeError("generated CLI arguments rejected: " + errors.getvalue()) from error
        return status, errors.getvalue()

    def corrupt_source(name, action, consumer=None):
        schema = inputs.resolve(name)
        path = Path(schema.source_paths(sources[name])[0])
        review.require(path.resolve().is_relative_to(project.resolve()),
                       "selected loader escaped the owned fixture")
        previous = path.read_bytes()
        try:
            path.write_text("{ invalid selected JSON }\n")
            try:
                status, diagnostic = invoke(action, consumer or name)
            except json.JSONDecodeError:
                return
            check(status != 0 and bool(diagnostic.strip()),
                  "invalid selected owner was ignored or silently accepted")
        finally:
            path.write_bytes(previous)

    def object_symbols(schema, path, directory, selected_inputs):
        directory.mkdir(parents=True, exist_ok=True)
        if schema.name == "items":
            (directory / "id_space_active.h").write_text(
                idspace.render_active_header(env={}, inputs=selected_inputs))
        output = directory / path.with_suffix(".o").name
        command([
            os.environ["MODERN_CC"], "-mcpu=arm7tdmi", "-mthumb",
            "-mthumb-interwork", "-mabi=aapcs", "-std=gnu89",
            "-ffreestanding", "-fno-builtin", "-DFE8_EXPANSION_MODERN_BUILD=1",
            "-Iinclude", "-Iinclude/generated", "-I" + str(directory),
            "-c", str(path), "-o", str(output),
        ])
        symbols = command([os.environ["MODERN_NM"], "-S", "--defined-only", str(output)])
        check(bool(symbols.strip()), "generated ARM object lost every defined symbol")
        return symbols

    for name in sorted(PROJECT_SCHEMAS):
        schema = inputs.resolve(name)
        if role == "owners":
            status, diagnostic = invoke("validate", name)
            check(status == 0, "valid selected owner rejected: " + diagnostic[-1000:])
            corrupt_source(name, "validate")
            checks += 2
            continue
        status, diagnostic = invoke("generate", name)
        check(status == 0, "valid selected producer rejected: " + diagnostic[-1000:])
        inventory = inventories / Path(schema.default_inventory_path).name
        targets = [inventory]
        if schema.default_output_name:
            targets.append(outputs / schema.default_output_name)
        check(all(path.is_file() and path.stat().st_size for path in targets),
              "selected producer did not materialize its complete output set")
        if role == "outputs":
            saved = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in targets}
            status, diagnostic = invoke("generate", name)
            check(status == 0 and saved == {
                str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in targets
            }, "unchanged producer rewrote or lost selected output")
            checks += 2
        elif role == "consumers":
            if schema.default_output_name:
                records, diagnostics = cli._load_and_validate(
                    schema, sources[name], dep_source_overrides=sources, inputs=inputs)
                review.require(not diagnostics.errors, "selected consumer prerequisite failed")
                default_inputs = SchemaInputs()
                default = default_inputs.resolve(name)
                original, diagnostics = cli._load_and_validate(
                    default, default.default_source, inputs=default_inputs)
                review.require(not diagnostics.errors, "default consumer prerequisite failed")
                baseline_directory = output_root / "objects" / name / "default"
                baseline_directory.mkdir(parents=True, exist_ok=True)
                baseline_source = baseline_directory / default.default_output_name
                baseline_source.write_text(default.generate_c(original, default.default_source))
                baseline = object_symbols(default, baseline_source, baseline_directory, default_inputs)
                observed = object_symbols(
                    schema, outputs / schema.default_output_name,
                    output_root / "objects" / name / "selected", inputs)
                check(observed == baseline,
                      "equal selected records changed actual ARM symbol layout")
                checks += 3
            else:
                consumer = {"chapterbundle": "chapterobjectives", "eventscripts": "eventlists"}[name]
                status, diagnostic = invoke("generate", consumer)
                check(status == 0, "selected metadata consumer rejected: " + diagnostic[-1000:])
                corrupt_source(name, "generate", consumer)
                checks += 2
        elif role == "drift-checks":
            status, diagnostic = invoke("check", name)
            check(status == 0, "valid selected artifact reported drift: " + diagnostic[-1000:])
            path = targets[-1]
            previous = path.read_bytes()
            try:
                path.write_bytes(b"")
                status, diagnostic = invoke("check", name)
                check(status == 0 and path.read_bytes() == previous,
                      name + ": selected build-local artifact did not heal from current source")
            finally:
                path.write_bytes(previous)
            checks += 2
        else:
            raise review.ReviewError("unknown direct project generated probe")
    if role == "outputs":
        from scripts.generated_data import manifest
        schema = registry.REGISTRY.resolve("supports")
        payload = json.loads(Path(schema.default_source).read_bytes())
        review.require(len(payload["records"]) > 3, "manifest source control needs distinct counts")
        payload["records"] = payload["records"][:3]
        source = project / "manifest-supports.json"
        source.write_text(json.dumps(payload))
        shared = SchemaInputs({"supports": str(source.relative_to(root))})
        rows = {entry.name: entry for entry in manifest.collect_entries(inputs=shared)}
        check(rows["supports"].record_count == 3,
              "public manifest ignored the shared selected source context")
        defaults = {entry.name: entry for entry in manifest.collect_entries()}
        check(defaults["supports"].record_count > 3,
              "selected manifest source leaked into a later default call")
        checks += 2
    check(all(Path(path).read_bytes() == content for path, content in committed.items()),
          "selected generation changed a committed framework inventory")
    return {
        "kind": "host", "checks": checks,
        "detail": "all16 actual selected schema/CLI paths; consumer ARM objects are not ROM evidence",
    }


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
def _cjk_resource(probe):
    import base64
    import hashlib
    import lzma
    import math
    import shutil
    import struct
    import zlib
    from scripts.fonttools.cjk import cli, package, resource_index as codec

    _, family, role = probe.split(":")
    checks = 0
    limit = 4 * 1024 * 1024
    operation_errors = (
        ArithmeticError, AttributeError, LookupError, RuntimeError,
        TypeError, ValueError, lzma.LZMAError,
    )

    def verify(condition, detail):
        nonlocal checks
        check(condition, detail)
        checks += 1

    def canonical(value):
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                           allow_nan=False) + "\n").encode()

    def reference_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate reference resource key")
            value[key] = item
        return value

    def reference_number(token):
        value = float(token)
        if not math.isfinite(value):
            raise ValueError("nonfinite reference resource number")
        return value

    def reference_json(data):
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=reference_object,
            parse_float=reference_number, parse_constant=reference_number,
        )
        if not isinstance(value, dict):
            raise ValueError("reference resource must be an object")
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return value

    def reference(data):
        if len(data) > limit:
            raise ValueError("reference resource index exceeds 4 MiB")
        value = reference_json(data)
        if value.get("kind") != "cjk-resource-index":
            return data
        if (set(value) != {"kind", "version", "encoding", "decoded_bytes", "payload"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["encoding"] != "xz-base64" or not isinstance(value["payload"], str)):
            raise ValueError("unsupported reference resource envelope")
        expected = value["decoded_bytes"]
        if type(expected) is not int or not 0 <= expected <= limit:
            raise ValueError("reference resource size exceeds its bound")
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=64 * 1024 * 1024)
        decoded = decoder.decompress(
            base64.b64decode(value["payload"], validate=True), max_length=limit + 1,
        )
        if len(decoded) > limit or len(decoded) != expected or not decoder.eof or decoder.unused_data:
            raise ValueError("reference resource size or single-stream boundary differs")
        if reference_json(decoded).get("kind") == "cjk-resource-index":
            raise ValueError("nested reference resource envelope")
        return decoded

    def envelope(data, stream=None):
        return {
            "kind": "cjk-resource-index", "version": 1, "encoding": "xz-base64",
            "decoded_bytes": len(data),
            "payload": base64.b64encode(lzma.compress(data) if stream is None else stream).decode(),
        }

    def reject(call, error=codec.ResourceIndexError):
        nonlocal checks
        try:
            call()
        except error as failure:
            checks += 1
            return failure
        except operation_errors as failure:
            raise ContractViolation("parser failure bypassed the typed resource boundary") from failure
        raise ContractViolation("invalid resource input was accepted")

    def accepted(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except operation_errors as failure:
            raise ContractViolation("valid resource operation was rejected: " + str(failure)) from failure

    def produced(data):
        verify(isinstance(data, bytes), "resource producer did not emit bytes")
        try:
            return reference_json(reference(data))
        except (ValueError, TypeError, RecursionError, lzma.LZMAError) as failure:
            raise ContractViolation("resource producer emitted invalid storage: " + str(failure)) from failure

    def decoded(data, expected):
        value = accepted(codec.decode_index, data)
        verify(canonical(value) == canonical(expected), "complete typed resource values differ")
        return value

    def owned_fonts(name, *, inventory=False):
        root = Path("build") / name
        prefixes = ("fonts/cjk", "graphics/fonts/cjk")
        if inventory:
            prefixes += ("src", "include", "texts", "assets", "reports")
        for prefix in prefixes:
            shutil.copytree(prefix, root / prefix, dirs_exist_ok=True)
        if inventory:
            for command in (
                ["git", "init", "--quiet"], ["git", "add", "--", "src", "include"],
            ):
                result = run_process(command, cwd=root, env=os.environ.copy(),
                                     timeout=20, new_session=False)
                if result.returncode:
                    raise OSError("owned tracked-source fixture unavailable: "
                                  + result.stderr.decode(errors="replace")[-1000:])
        return root

    def legacy_fonts(name):
        root = owned_fonts(name)
        manifest_path = root / "graphics/fonts/cjk/manifest.json"
        baseline = (root / "fonts/cjk/febuilder-baseline-manifest.json").read_bytes()
        (root / "fonts/cjk/febuilder-manifest.json").write_bytes(baseline)
        report_path = "fonts/cjk/reports/febuilder-generation-report.json"
        report = json.loads((root / report_path).read_bytes())
        metadata = json.loads(manifest_path.read_bytes())
        payload_bytes = aligned_bytes = 0
        for job in json.loads(baseline)["jobs"]:
            prefix = job["locale"] + "." + ("system" if job["styles"] == ["item"] else "talk")
            names = package.compact_asset_filenames(prefix)
            for kind, filename in names.items():
                data = (root / "fonts/cjk/febuilder-baseline" / filename).read_bytes()
                relative = "graphics/fonts/cjk/" + filename
                (root / relative).write_bytes(data)
                metadata["assets"][prefix][kind] = {
                    "path": relative, "byte_count": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                payload_bytes += len(data)
                aligned_bytes += (len(data) + 3) & ~3
            codepoints = (root / "graphics/fonts/cjk" / names["codepoints"]).read_bytes()
            scalars = struct.unpack("<" + "I" * (len(codepoints) // 4), codepoints)
            (root / "fonts/cjk" / job["corpus"]["path"]).write_text(
                "".join(chr(scalar) for scalar in scalars), encoding="utf-8",
            )
            metadata["assets"][prefix]["glyph_count"] = len(scalars)
        metadata["schema_version"] = 1
        metadata["sources"]["febuilder_manifest"]["sha256"] = hashlib.sha256(baseline).hexdigest()
        metadata["sources"].pop("fehrr_priority")
        metadata["sources"]["febuilder_package"] = {
            "disposition": "temporary maintainer artifact under build/tmp; not committed",
            "payload_tree_sha256": report["payloadTreeSha256"],
            "full_tree_sha256": report["fullTreeSha256"],
            "package_report_sha256": hashlib.sha256((root / report_path).read_bytes()).hexdigest(),
        }
        metadata["rom_budget"] = {
            "payload_bytes": payload_bytes, "four_byte_aligned_blob_bytes": aligned_bytes,
            "bytes_per_glyph": 69,
        }
        manifest_path.write_bytes(canonical(metadata))
        accepted(package.check_compact_assets, root)
        return root

    def source_checkout(scalar=None, *, blank=False, glyphs=None):
        selection = str(scalar) if glyphs is None else "-".join(
            str(glyphs[prefix]) for prefix in sorted(glyphs)
        )
        root = Path("build/review-fehrr-generator-source-" + selection + "-" + str(blank))
        root.mkdir(parents=True)
        paths = {
            Path("glyph") / locale / "font.fontall.txt"
            for locale in package.FEHRR_LOCALES.values()
        }
        paths.update(
            Path(directory) / "font.fontall.txt"
            for styles in package.FEHRR_SUPPLEMENTAL_TIERS.values()
            for tiers in styles.values() for _, directory in tiers
        )
        for relative in paths:
            review.require(not relative.is_absolute() and ".." not in relative.parts,
                           "FEHRR source fixture path escaped its owner")
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")
        if scalar is not None or glyphs is not None:
            def chunk(kind, data):
                return (struct.pack(">I", len(data)) + kind + data
                        + struct.pack(">I", zlib.crc32(kind + data)))

            scanlines = (
                b"\0" + (b"\xff\xff\xff" if blank else b"\0\0\0") + b"\xff\xff\xff" * 15
                + (b"\0" + b"\xff\xff\xff" * 16) * 15
            )
            png = (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(scanlines))
                + chunk(b"IEND", b"")
            )
            for locale, source_locale in package.FEHRR_LOCALES.items():
                directory = root / "glyph" / source_locale
                rows = []
                for runtime_style, style, name in (
                    ("system", "item", "FontItem_fixture.png"),
                    ("talk", "text", "FontText_fixture.png"),
                ):
                    (directory / name).write_bytes(png)
                    selected = scalar if glyphs is None else glyphs[locale + "." + runtime_style]
                    rows.append(f"{chr(selected)}\t{style}\t9\t{name}\n")
                (directory / "font.fontall.txt").write_text("".join(rows), encoding="utf-8")
        for arguments in (
            ["git", "init", "--quiet"],
            ["git", "remote", "add", "origin", package.FEHRR_REPOSITORY],
            ["git", "add", "--", "glyph"],
            ["git", "-c", "user.name=Source fixture",
             "-c", "user.email=source-fixture@example.invalid",
             "-c", "core.hooksPath=/dev/null", "commit", "--quiet",
             "-m", "owned empty-map source fixture"],
        ):
            result = run_process(arguments, cwd=root, env=os.environ.copy(),
                                 timeout=20, new_session=False)
            if result.returncode:
                raise OSError("owned source fixture unavailable: "
                              + result.stderr.decode(errors="replace")[-1000:])
        return root

    originals = []
    for relative in CJK_INDEXES:
        with Path(relative).open("rb") as stream:
            originals.append(stream.read(limit + 1))
    stocks = []
    for data in originals:
        stocks.append(json.loads(reference(data)))
    seed = {"id": 1, "values": [True, 1.0, "\u4e00"]}
    plain = canonical(seed)
    wrapped = canonical(envelope(plain))
    path = Path("build/review-resource-index.json")

    if family == "generated":
        if role == "owners":
            for data, value in zip(originals, stocks):
                decoded(data, value)
            for invalid in (b"[]", b"\xff", b'{"a":1,"a":2}'):
                reject(lambda invalid=invalid: codec.decode_index(invalid))
        elif role == "outputs":
            for value in stocks:
                verify(accepted(codec.encode_index, value) == canonical(value),
                       "existing canonical plain output changed")
                stored = accepted(codec.encode_index, value, packed=True)
                independent = produced(stored)
                verify(canonical(independent) == canonical(value), "packed output lost fields/types")
                verify(accepted(codec.encode_index, value, packed=True) == stored,
                       "fresh same-encoder compression is not deterministic")
            manifest = "graphics/fonts/cjk/manifest.json"
            root = owned_fonts("review-resource-selected-only-glyph")
            baseline = set()
            for asset in json.loads((root / manifest).read_bytes())["assets"].values():
                data = (root / "fonts/cjk/febuilder-baseline"
                        / Path(asset["codepoints"]["path"]).name).read_bytes()
                baseline.update(struct.unpack("<" + "I" * (len(data) // 4), data))
            scalar = next(value for value in range(0x3400, 0x4DC0) if value not in baseline)
            jobs_path = root / "fonts/cjk/febuilder-manifest.json"
            jobs = json.loads(jobs_path.read_bytes())
            corpus = chr(scalar).encode("utf-8")
            for job in jobs["jobs"]:
                (root / "fonts/cjk" / job["corpus"]["path"]).write_bytes(corpus)
                job["corpus"]["sha256"] = hashlib.sha256(corpus).hexdigest()
            jobs_path.write_bytes(canonical(jobs))
            source = source_checkout(scalar)
            outputs = accepted(package.build_runtime_split_assets, root, source)
            metadata = produced(outputs[manifest])
            verify(all(
                outputs[asset["codepoints"]["path"]] == struct.pack("<I", scalar)
                and outputs[asset["widths"]["path"]] == bytes([9])
                and outputs[asset["bitmap"]["path"]] == b"\1" + bytes(63)
                for asset in metadata["assets"].values()
            ), "actual FEHRR-only scalar required an unrelated baseline glyph")
            source = source_checkout()
            for generator in (package.build_runtime_split_assets, package.build_fehrr_priority_assets):
                for packed in (False, True):
                    root = legacy_fonts(
                        "review-generator-" + generator.__name__ + "-" + str(packed))
                    (root / CJK_INDEXES[0]).write_bytes(
                        accepted(codec.encode_index, stocks[0], packed=packed))
                    before = {
                        asset[kind]["path"]: (root / asset[kind]["path"]).read_bytes()
                        for asset in json.loads((root / manifest).read_bytes())["assets"].values()
                        for kind in ("bitmap", "codepoints", "widths")
                    }
                    outputs = accepted(generator, root, source)
                    verify(isinstance(outputs, dict)
                           and set(outputs) == {*before, CJK_INDEXES[0], manifest},
                           "actual font generator lost complete output ownership")
                    stored = outputs[CJK_INDEXES[0]]
                    lock = produced(stored)
                    metadata = produced(outputs[manifest])
                    verify((json.loads(stored).get("kind") == "cjk-resource-index") == packed,
                           "actual font generator changed the stored representation")
                    verify(metadata["sources"]["fehrr_priority"]["sha256"]
                           == hashlib.sha256(stored).hexdigest(),
                           "actual font generator bound a different physical index")
                    verify(all(outputs[path] == data for path, data in before.items())
                           and all(asset["glyph_count"] == 0 for asset in lock["assets"].values()),
                           "owned empty-source fallback changed complete atlas ownership/bytes")
                    for relative, data in outputs.items():
                        (root / relative).write_bytes(data)
                    accepted(package.check_compact_assets, root)
                    repeated = accepted(generator, root, source)
                    verify(repeated == outputs,
                           "actual generator rejected or changed its own schema-2 output")
            prefixes = ("ja.system", "ja.talk", "zh-Hans.system", "zh-Hans.talk")

            def selections(lock):
                return {
                    prefix: tuple(int(row["scalar"][2:], 16) for row in asset["glyphs"])
                    for prefix, asset in lock["assets"].items()
                }

            for schema in (1, 2):
                make_root = legacy_fonts if schema == 1 else owned_fonts
                seed_root = make_root("review-bound-selection-seed-" + str(schema))
                seed_metadata = json.loads((seed_root / manifest).read_bytes())
                corpora = {}
                for prefix, asset in seed_metadata["assets"].items():
                    data = (seed_root / asset["codepoints"]["path"]).read_bytes()
                    corpora[prefix] = set(struct.unpack("<" + "I" * (len(data) // 4), data))
                review.require(set(corpora) == set(prefixes),
                               "bound-selection fixture asset coverage drifted")
                groups = (
                    {"ja": ("ja.system", "ja.talk"),
                     "zh-Hans": ("zh-Hans.system", "zh-Hans.talk")}
                    if schema == 1 else {prefix: (prefix,) for prefix in prefixes}
                )
                pairs = {}
                for group, targets in groups.items():
                    shared = set.intersection(*(corpora[prefix] for prefix in targets))
                    elsewhere = set.union(*(values for prefix, values in corpora.items()
                                            if prefix not in targets))
                    exclusive = sorted(value for value in shared - elsewhere
                                       if value > 0xFF and value != 0x3000)
                    review.require(len(exclusive) >= 2,
                                   "bound-selection fixture needs two isolated corpus scalars")
                    pairs[group] = tuple(exclusive[:2])
                owners = {prefix: group for group, targets in groups.items() for prefix in targets}
                stable_glyphs = {prefix: pairs[owners[prefix]][0] for prefix in prefixes}
                stable_source = source_checkout(glyphs=stable_glyphs)
                changed_sources = {
                    group: source_checkout(glyphs={
                        prefix: pairs[owners[prefix]][1] if prefix in targets
                        else stable_glyphs[prefix]
                        for prefix in prefixes
                    })
                    for group, targets in groups.items()
                }
                expected_selection = {prefix: (scalar,) for prefix, scalar in stable_glyphs.items()}
                base_outputs = accepted(package.build_runtime_split_assets, seed_root, stable_source)
                verify(selections(produced(base_outputs[CJK_INDEXES[0]])) == expected_selection,
                       "bound-selection fixture did not select one scalar per asset")
                for packed in (False, True):
                    seeded = dict(base_outputs)
                    seeded[CJK_INDEXES[0]] = accepted(
                        codec.encode_index, produced(base_outputs[CJK_INDEXES[0]]), packed=packed)
                    metadata = produced(base_outputs[manifest])
                    metadata["sources"]["fehrr_priority"]["sha256"] = hashlib.sha256(
                        seeded[CJK_INDEXES[0]]).hexdigest()
                    seeded[manifest] = canonical(metadata)
                    root = make_root("review-bound-selection-" + str(packed) + "-" + str(schema))
                    bound_metadata = json.loads((root / manifest).read_bytes())
                    for relative, data in seeded.items():
                        if relative != manifest:
                            (root / relative).write_bytes(data)
                    if schema == 2:
                        bound_metadata = metadata
                    else:
                        bound_lock = produced(seeded[CJK_INDEXES[0]])
                        for asset in bound_lock["assets"].values():
                            asset["source_style"] = asset["preferred_source_style"]
                        stored = accepted(codec.encode_index, bound_lock, packed=packed)
                        (root / CJK_INDEXES[0]).write_bytes(stored)
                        bound_metadata["assets"] = {
                            prefix: {
                                **asset,
                                "source_priority": {
                                    "fehrr_glyph_count": bound_lock["assets"][prefix]["glyph_count"],
                                    "febuilder_fallback_glyph_count":
                                        bound_lock["assets"][prefix]["febuilder_fallback_glyph_count"],
                                    "source_locale": bound_lock["assets"][prefix]["source_locale"],
                                    "source_style": bound_lock["assets"][prefix]["source_style"],
                                },
                            }
                            for prefix, asset in metadata["assets"].items()
                        }
                        bound_metadata["rom_budget"] = metadata["rom_budget"]
                        bound_metadata["sources"]["fehrr_priority"] = {
                            "path": CJK_INDEXES[0],
                            "sha256": hashlib.sha256(stored).hexdigest(),
                            "policy": "FEHRR original-game glyphs and widths before FEBuilderGBA fallback",
                        }
                    (root / manifest).write_bytes(canonical(bound_metadata))
                    accepted(package.check_compact_assets, root)
                    unchanged = accepted(package.build_fehrr_priority_assets, root, stable_source)
                    verify(selections(produced(unchanged[CJK_INDEXES[0]])) == expected_selection,
                           "unchanged bound source selection was rejected or changed")
                    before = {relative: (root / relative).read_bytes() for relative in seeded}
                    for group, changed_source in changed_sources.items():
                        preview = accepted(package.build_runtime_split_assets, root, changed_source)
                        changed_selection = {
                            prefix: (pairs[group][1],) if prefix in groups[group]
                            else expected_selection[prefix]
                            for prefix in prefixes
                        }
                        verify(selections(produced(preview[CJK_INDEXES[0]])) == changed_selection,
                               "source fixture changed a count or an unrelated asset selection")
                        for operation in (
                            package.build_fehrr_priority_assets,
                            package.write_fehrr_priority_assets,
                        ):
                            reject(lambda operation=operation: operation(root, changed_source),
                                   package.CjkFontError)
                            verify(all((root / relative).read_bytes() == data
                                       for relative, data in before.items()),
                                   "changed bound selection published an output before refusing")
                    written = accepted(package.write_fehrr_priority_assets, root, stable_source)
                    verify(written == unchanged
                           and all((root / relative).read_bytes() == data
                                   for relative, data in written.items()),
                           "unchanged bound selection did not publish its complete valid output")
                    accepted(package.check_compact_assets, root)
            root = legacy_fonts("review-generator-blank-fallback")
            corpora = package._job_corpora(root, package._expected_jobs(root))
            common = set.intersection(*(set(value[2]) for value in corpora.values()))
            scalar = next(value for value in sorted(common) if value >= 0x4E00)
            source = source_checkout(scalar, blank=True)
            outputs = accepted(package.write_fehrr_priority_assets, root, source)
            accepted(package.check_compact_assets, root)
            lock = produced(outputs[CJK_INDEXES[0]])
            verify(all(any(
                row["scalar"] == package.scalar_text(scalar)
                and row["reason"] == (
                    "configured FEHRR glyph is blank and no visible same-game fallback exists"
                )
                for row in asset["febuilder_fallbacks"]
            ) for asset in lock["assets"].values()),
                   "actual blank-glyph fallback lost its supported source reason")
        elif role == "consumers":
            root = Path("build/review-resource-consumer")
            source = root / CJK_INDEXES[0]
            source.parent.mkdir(parents=True, exist_ok=True)
            for packed in (False, True):
                stored = accepted(codec.encode_index, stocks[0], packed=packed)
                source.write_bytes(stored)
                priority = {
                    "path": CJK_INDEXES[0], "sha256": hashlib.sha256(stored).hexdigest(),
                    "policy": "FEHRR original-game glyphs and widths before FEBuilderGBA fallback",
                }
                value = accepted(package._read_fehrr_lock, root, {"fehrr_priority": priority})
                verify(canonical(value) == canonical(stocks[0]), "real priority reader changed values")
                priority["sha256"] = "0" * 64
                reject(lambda: package._read_fehrr_lock(root, {"fehrr_priority": priority}),
                       package.CjkFontError)
            root = owned_fonts("review-resource-maintainer")
            manifest = "graphics/fonts/cjk/manifest.json"
            original_assets = {
                asset[kind]["path"]: (root / asset[kind]["path"]).read_bytes()
                for asset in json.loads((root / manifest).read_bytes())["assets"].values()
                for kind in ("bitmap", "codepoints", "widths")
            }
            for packed in (False, True):
                outputs = accepted(package.write_resource_index, root, "fehrr", packed=packed)
                accepted(package.check_compact_assets, root)
                stored = (root / CJK_INDEXES[0]).read_bytes()
                decoded(stored, stocks[0])
                verify((json.loads(stored).get("kind") == "cjk-resource-index") == packed,
                       "actual maintainer ignored the requested representation")
                verify(set(outputs) == {CJK_INDEXES[0], manifest}
                       and all((root / path).read_bytes() == value for path, value in outputs.items())
                       and all((root / path).read_bytes() == value
                               for path, value in original_assets.items()),
                       "actual conversion changed its output ownership or atlas bytes")
            legacy = legacy_fonts("review-resource-unbound-legacy")
            saved = {name: (legacy / name).read_bytes() for name in (CJK_INDEXES[0], manifest)}
            for packed in (False, True):
                reject(lambda packed=packed: package.write_resource_index(
                    legacy, "fehrr", packed=packed), package.CjkFontError)
                verify(all((legacy / name).read_bytes() == value for name, value in saved.items()),
                       "unbound legacy priority refusal changed a stored output")
            root = owned_fonts("review-resource-cli")
            accepted(package.check_compact_assets, root)
            broken = b"invalid index"
            cause = str(reject(lambda: codec.decode_index(broken)))
            (root / CJK_INDEXES[0]).write_bytes(broken)
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            errors = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(errors):
                status = accepted(cli.main, [
                    "--root", str(root), "repack-index", "--index", "fehrr", "--format", "packed",
                ])
            verify(status == 1 and cause in errors.getvalue(),
                   "actual schema2 CLI hid the codec diagnostic")
            verify(all(path.read_bytes() == value for path, value in before.items()),
                   "actual failed schema2 CLI changed an existing output")
        elif role == "drift-checks":
            alternate = canonical(envelope(plain, lzma.compress(plain, preset=0)))
            path.write_bytes(alternate)
            verify(accepted(codec.encode_like_index, seed, path) == alternate,
                   "unchanged valid alternate stream was recompressed")
            verify(accepted(codec.encode_like_index, seed, path, packed=True) == alternate,
                   "explicit unchanged packed stream was recompressed")
            path.write_bytes(b"invalid index")
            reject(lambda: codec.encode_like_index(seed, path))
            verify(path.read_bytes() == b"invalid index", "failed writer changed input")
            root = owned_fonts("review-resource-inventory", inventory=True)
            inventory_path = "fonts/cjk/inventory.json"
            manifest_path = "graphics/fonts/cjk/manifest.json"
            original_inventory = (root / inventory_path).read_bytes()
            original_manifest = (root / manifest_path).read_bytes()
            initially_packed = json.loads(originals[1]).get("kind") == "cjk-resource-index"
            for packed in (not initially_packed, initially_packed):
                outputs = accepted(package.write_resource_index, root, "runtime-usage", packed=packed)
                stored = (root / CJK_INDEXES[1]).read_bytes()
                decoded(stored, stocks[1])
                verify((json.loads(stored).get("kind") == "cjk-resource-index") == packed,
                       "runtime-usage maintainer changed the requested format")
                expected_inventory = json.loads(original_inventory)
                expected_inventory["outputs"][CJK_INDEXES[1]] = {
                    "byte_count": len(stored), "sha256": hashlib.sha256(stored).hexdigest(),
                }
                inventory_data = canonical(expected_inventory)
                expected_manifest = json.loads(original_manifest)
                expected_manifest["sources"]["inventory"]["sha256"] = hashlib.sha256(
                    inventory_data).hexdigest()
                verify(set(outputs) == {CJK_INDEXES[1], inventory_path, manifest_path}
                       and all((root / name).read_bytes() == value
                               for name, value in outputs.items())
                       and (root / inventory_path).read_bytes() == inventory_data
                       and (root / manifest_path).read_bytes() == canonical(expected_manifest),
                       "inventory writer changed its ownership or physical checksum chain")
            mapping = json.loads(
                (root / "texts/locales/mapping/fe8u_target_map.json").read_bytes()
            )
            literal_source = next(
                row["source"]["regional_sources"]["ja"]["provenance"]["source_path"]
                for row in mapping["rows"]
                if row["source"].get("regional_sources", {}).get("ja", {}).get("kind")
                == "literal"
            )
            for relative in (literal_source, "assets/portrait_registry.json"):
                selected_source = root / relative
                saved = selected_source.read_bytes()
                selected_source.unlink()
                reject(lambda: package.check_generated_files(root), ValueError)
                selected_source.write_bytes(saved)
        else:
            raise review.ReviewError("unknown resource generation role")
    elif family == "wire":
        if role == "producers":
            stored = json.loads(accepted(codec.encode_index, seed, packed=True))
            verify(set(stored) == {"kind", "version", "encoding", "decoded_bytes", "payload"}
                   and stored["kind"] == "cjk-resource-index"
                   and type(stored["version"]) is int and stored["version"] == 1
                   and stored["encoding"] == "xz-base64"
                   and type(stored["decoded_bytes"]) is int
                   and stored["decoded_bytes"] == len(plain),
                   "public envelope fields/version/types differ")
            verify(reference(canonical(stored)) == plain,
                   "producer payload differs from canonical complete input")
            reject(lambda: codec.encode_index({"value": float("nan")}))
            for value in ({"value": "\ud800"}, {"\udfff": "value"}, {"value": ["\ud800"]}):
                reject(lambda value=value: codec.encode_index(value))
        elif role == "consumers":
            for number in (1e308, -1e308):
                value = {"value": number}
                decoded(canonical(value), value)
                decoded(canonical(envelope(canonical(value))), value)
            paired = b'{"value":"\\ud83d\\ude00"}'
            decoded(paired, {"value": "\U0001F600"})
            decoded(canonical(envelope(paired)), {"value": "\U0001F600"})
            for token in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
                data = ('{"value":' + token + "}").encode()
                for stored in (data, canonical(envelope(data))):
                    reject(lambda stored=stored: codec.decode_index(stored))
            for data in (
                b'{"value":' + b"[" * 16000 + b"0" + b"]" * 16000 + b"}",
                b'{"value":' + b"1" * 5000 + b"}",
                b'{"value":"\\ud800"}', b'{"\\udfff":"value"}', b'{"value":["\\ud800"]}',
            ):
                for stored in (data, canonical(envelope(data))):
                    reject(lambda stored=stored: codec.decode_index(stored))
        elif role == "validators":
            base = envelope(plain)
            stream = base64.b64decode(base["payload"])
            cases = [
                {**base, "version": 2}, {**base, "version": True},
                {**base, "encoding": "unknown"}, {**base, "extra": 0},
                {**base, "decoded_bytes": True}, {**base, "decoded_bytes": len(plain) + 1},
                *({**base, "payload": item} for item in (None, False, 1, [], {}, "%%%")),
                envelope(canonical(base)), envelope(plain, stream[:-4]),
                envelope(plain, stream + b"x"), envelope(plain, stream * 2),
                envelope(plain, stream[:-8] + bytes(8)),
            ]
            for value in cases:
                reject(lambda value=value: codec.decode_index(canonical(value)))
        elif role == "replay":
            for packed in (False, True):
                path.write_bytes(accepted(codec.encode_index, seed, packed=packed))
                changed = {"id": 2, "values": [False, 2.0]}
                actual = accepted(codec.encode_like_index, changed, path)
                decoded(actual, changed)
                verify((json.loads(actual).get("kind") == "cjk-resource-index") == packed,
                       "updated writer changed stored representation")
        elif role == "stale-bindings":
            for value in (seed, {"id": 2}, seed):
                path.write_bytes(accepted(codec.encode_index, value, packed=True))
                verify(canonical(accepted(codec.load_index, path)) == canonical(value),
                       "reopened index retained stale file contents")
            path.write_bytes(b"invalid index")
            reject(lambda: codec.load_index(path))
        else:
            raise review.ReviewError("unknown resource wire role")
    elif family == "resource":
        packed = role == "enabled"
        stored = accepted(codec.encode_index, seed, packed=packed)
        decoded(stored, seed)
        if not packed:
            verify(stored == plain, "plain representation changed")
        prefix, suffix = b'{"x":"', b'"}'
        oversized = prefix + b"x" * (limit + 1 - len(prefix) - len(suffix)) + suffix
        if packed:
            candidate = envelope(oversized)
            candidate["decoded_bytes"] = limit
            reject(lambda: codec.decode_index(canonical(candidate)))
            for property_value in (27, 28):
                stream = bytearray(lzma.compress(plain))
                header_end = 12 + (stream[12] + 1) * 4
                verify(bytes(stream[14:16]) == b"\x21\x01", "unsupported XZ fixture header")
                stream[16] = property_value
                stream[header_end - 4:header_end] = struct.pack(
                    "<I", zlib.crc32(stream[12:header_end - 4]))
                independent = lzma.LZMADecompressor(
                    format=lzma.FORMAT_XZ, memlimit=80 * 1024 * 1024)
                verify(independent.decompress(stream, max_length=len(plain) + 1) == plain
                       and independent.eof and not independent.unused_data,
                       "independent large-dictionary control is invalid")
                bounded = canonical(envelope(plain, stream))
                if property_value == 27:
                    decoded(bounded, seed)
                else:
                    reject(lambda: codec.decode_index(bounded))
        else:
            reject(lambda: codec.decode_index(oversized))
        path.write_bytes(oversized)
        reject(lambda: codec.read_index_bytes(path))
    else:
        raise review.ReviewError("unknown resource family")
    return {"kind": "host", "checks": checks,
            "detail": "real codec, legacy/schema2 consumer, inventory format/drift and resource observations"}


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

    class CandidateReader:
        review_candidate_reader = True

        def __init__(self):
            self.root = Path.cwd().resolve()
            self.base = a
            self.head = b
            base_data, head_data = b"base source", b"candidate source"
            self.base_tree = self._tree(a, base_data)
            self.head_tree = self._tree(b, head_data)
            self.candidate_binding = {
                "resolved_root": str(self.root), "base": a, "head": b,
            }

        @staticmethod
        def _tree(revision, data):
            oid = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            return types.SimpleNamespace(
                root=Path.cwd().resolve(), revision=revision,
                entries={"case.txt": ("100644", "blob", oid)}, data=data, oid=oid,
            )

        def preview(self, path, side="head"):
            return {
                "path": path, "side": side,
                "revision": self.base if side == "base" else self.head,
            }

        def describe(self, path, side="head"):
            tree = self.base_tree if side == "base" else self.head_tree
            return {
                **self.preview(path, side), "present": True, "mode": "100644",
                "kind": "blob", "oid": tree.oid,
            }

        def __call__(self, path, side="head"):
            return {**self.describe(path, side),
                    "data": (self.base_tree if side == "base" else self.head_tree).data}

    def completed_session(owners, pull_request, owner, task, root_session, *,
                          interaction="interaction-1",
                          started_at="2026-01-01T00:00:01Z",
                          activity_at="2026-01-01T00:00:03Z",
                          completed_at="2026-01-01T00:00:05Z",
                          extra_events=(), finish=True):
        scope = frozenset({"case"})
        reader = CandidateReader()
        candidate_read = reader.describe("case.txt")
        candidate_summary = module.CandidateReadSummary(
            candidate_read["path"], candidate_read["side"], candidate_read["revision"],
            candidate_read["present"], candidate_read["mode"], candidate_read["oid"])
        events = (
            (root_session, {
                "type": "subagent.started", "timestamp": started_at, "agentId": owner,
                "data": {"toolCallId": task, "interactionId": interaction},
            }),
            (root_session + "-reviewer", {
                "type": "assistant.message", "timestamp": activity_at,
                "agentId": owner,
                "data": {"interactionId": interaction, "turnId": "turn-1"},
            }),
            *extra_events,
        )
        report = json.dumps({
            "candidate_sha": b, "scope": sorted(scope), "findings": [],
        })
        result = types.SimpleNamespace(
            task=task, owner=owner, role="code-review", head=b,
            subjects=tuple(sorted(scope)), completed=True,
            actions=("view",), files=1, findings=(), candidate_reads=(candidate_summary,),
            started_at=started_at, completed_at=completed_at,
            interaction_id=interaction,
            instruction_commitment=module.CliReviewRuntime.COMMITMENT,
            logical_closed=False, cli_events=events, report=report,
        )
        runtime = types.SimpleNamespace(
            root_session_id=root_session,
            start=lambda **kw: task, read=lambda observed_task: result)
        session = module.ReviewSession(
            "coordinator", "implementer", scope, b,
            identity=("owner/repo", pull_request, a), owners=owners,
            readers={"read-candidate": reader})
        session.begin(runtime, owner)
        if finish:
            session.finish(runtime)
            return session
        return session, runtime

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
            owners = module.ReviewOwnership()
            first = completed_session(owners, 1, "reviewer-a", "dispatch-a", "root-a")
            reader = CandidateReader()
            scope = frozenset({"case"})
            targets = {}
            for side in ("base", "head"):
                targets[("case.txt", side)] = {
                    "tool_path": str(Path.cwd() / ("review-" + side + ".txt")),
                    "content": reader("case.txt", side)["data"],
                    "summary": reader.describe("case.txt", side),
                    "encoding": "utf-8",
                }
            assigned = json.dumps(
                [[path, side, target["tool_path"], target["encoding"]]
                 for (path, side), target in sorted(targets.items())],
                ensure_ascii=True, separators=(",", ":"))
            prompt = review._review_instructions(b, scope, assigned)

            def native_event(kind, second, agent, data):
                return ("root-b", {
                    "type": kind, "timestamp": f"2026-01-01T00:01:{second:02d}Z",
                    "agentId": agent, "data": data,
                })

            events = [
                native_event("tool.execution_start", 0, "coordinator", {
                    "toolCallId": "dispatch-b", "toolName": "task",
                    "arguments": {"prompt": prompt, "agent_type": "code-review"},
                }),
                native_event("subagent.started", 1, "reviewer-b", {
                    "toolCallId": "dispatch-b", "interactionId": "interaction-1",
                    "agentType": "code-review",
                }),
                native_event("user.message", 2, "reviewer-b", {
                    "source": "agent-root-b", "interactionId": "interaction-1",
                    "turnId": "turn-0", "transformedContent": prompt,
                }),
                native_event("assistant.message", 3, "reviewer-b", {
                    "interactionId": "interaction-1", "turnId": "turn-0",
                    "phase": "commentary", "content": module.CliReviewRuntime.COMMITMENT,
                }),
            ]
            for index, (_, target) in enumerate(sorted(targets.items())):
                call_id = f"view-{index}"
                events.extend((
                    native_event("tool.execution_start", 4 + index * 2, "reviewer-b", {
                        "turnId": "turn-0", "toolCallId": call_id, "toolName": "view",
                        "arguments": {"path": target["tool_path"]},
                    }),
                    native_event("tool.execution_complete", 5 + index * 2, "reviewer-b", {
                        "interactionId": "interaction-1", "turnId": "turn-0",
                        "toolCallId": call_id, "success": True,
                        "result": {"content": target["content"].decode("utf-8")},
                    }),
                ))
            events.extend((
                native_event("assistant.message", 8, "reviewer-b", {
                    "interactionId": "interaction-1", "turnId": "turn-1",
                    "phase": "final_answer",
                    "content": json.dumps({"candidate_sha": b, "scope": sorted(scope),
                                           "findings": []}),
                }),
                native_event("subagent.completed", 9, "reviewer-b", {
                    "toolCallId": "dispatch-b",
                }),
                native_event("tool.execution_complete", 3, "coordinator", {
                    "toolCallId": "dispatch-b", "success": True,
                }),
            ))
            baseline_runtime = module.CliReviewRuntime(
                events, dispatch_id="dispatch-b", root_session_id="root-b",
                candidate_reader=reader, required_reads=targets)
            baseline = module.ReviewSession(
                "coordinator", "implementer", scope, b,
                identity=("owner/repo", 2, a), owners=module.ReviewOwnership(),
                readers={"read-candidate": reader})
            baseline.begin(baseline_runtime, "reviewer-b")
            check(not rejects(lambda: baseline.finish(baseline_runtime)),
                  "valid CLI terminal did not admit its report")
            check(baseline.report is not None and baseline.report.head == b,
                  "valid CLI terminal published the wrong report")
            native = module.CliReviewRuntime(
                events, dispatch_id="dispatch-b", root_session_id="root-b",
                candidate_reader=reader, required_reads=targets)
            second = module.ReviewSession(
                "coordinator", "implementer", scope, b,
                identity=("owner/repo", 2, a), owners=owners,
                readers={"read-candidate": reader})
            second.begin(native, "reviewer-b")
            check(len(native.read("dispatch-b").candidate_reads) == 2,
                  "valid terminal baseline was not readable")
            check(first.review_state((), (), pre_review_required=True)[0],
                  "first review was invalidated before the late observation")
            late_a = ("root-a-reviewer", {
                "type": "assistant.turn_start", "timestamp": "2026-01-01T00:02:00Z",
                "agentId": "reviewer-a", "data": {"turnId": "late"},
            })
            late_b = ("root-b", {
                "type": "assistant.turn_start", "timestamp": "2026-01-01T00:02:00Z",
                "agentId": "reviewer-b", "data": {"turnId": "late"},
            })
            native.events = tuple(events) + (late_b,)
            check(rejects(lambda: native.read("dispatch-b")),
                  "late reviewer activity was admitted")
            native.events = first.latest_cli_events + tuple(events) + (late_a, late_b)
            check(rejects(lambda: second.finish(native)),
                  "late reviewer activity was admitted at terminal")
            check(rejects(lambda: first.review_state(
                (), (), pre_review_required=True)),
                "failed terminal read lost another closed reviewer's late activity")
            second.abort(native)
            check(second.lease.outcome == "aborted" and second.report is None,
                  "observed completion could not abort invalid review")
            owners = module.ReviewOwnership()
            prior = completed_session(owners, 1, "reviewer-a", "dispatch-a", "root-a")
            overlapping = json.loads(json.dumps(events).replace("T00:01:", "T00:00:"))
            for _, event in overlapping:
                if event.get("type") == "subagent.completed":
                    event["data"]["toolCallId"] = "contradictory-task"
            uncertain_runtime = module.CliReviewRuntime(
                overlapping, dispatch_id="dispatch-b", root_session_id="root-b",
                candidate_reader=reader, required_reads=targets)
            uncertain = module.ReviewSession(
                "coordinator", "implementer", scope, b,
                identity=("owner/repo", 1, a), owners=owners,
                readers={"read-candidate": reader})
            uncertain.begin(uncertain_runtime, "reviewer-b")
            check(rejects(lambda: uncertain.finish(uncertain_runtime)),
                  "contradictory terminal admitted a review")
            check(rejects(lambda: prior.review_state((), (), pre_review_required=True)),
                  "observed possible overlap did not invalidate the prior report")
            check(rejects(lambda: uncertain.abort(uncertain_runtime))
                  and not uncertain.lease.finished,
                  "contradictory terminal released ownership")

            def alternate_events(source, *, minute="02"):
                return json.loads(json.dumps(source)
                    .replace("root-b", "root-c")
                    .replace("reviewer-b", "reviewer-c")
                    .replace("dispatch-b", "dispatch-c")
                    .replace("T00:01:", f"T00:{minute}:"))

            def native_session(owners, source, root, reviewer, dispatch):
                runtime = module.CliReviewRuntime(
                    source, dispatch_id=dispatch, root_session_id=root,
                    candidate_reader=reader, required_reads=targets)
                session = module.ReviewSession(
                    "coordinator", "implementer", scope, b,
                    identity=("owner/repo", 1, a), owners=owners,
                    readers={"read-candidate": reader})
                session.begin(runtime, reviewer)
                return session, runtime

            owners = module.ReviewOwnership()
            prior, prior_runtime = native_session(
                owners, events, "root-b", "reviewer-b", "dispatch-b")
            prior.finish(prior_runtime)
            changed_completion = json.loads(json.dumps(next(
                item for item in events if item[1].get("type") == "subagent.completed")))
            changed_completion[1]["timestamp"] = "2026-01-01T00:02:05Z"
            check(rejects(lambda: prior.observe_cli_events((changed_completion,))),
                  "changed terminal did not invalidate its original report")
            later, later_runtime = native_session(
                owners, alternate_events(events), "root-c", "reviewer-c", "dispatch-c")
            check(rejects(lambda: later.finish(later_runtime)),
                  "changed terminal interval was forgotten for a later review")

            owners = module.ReviewOwnership()
            original = json.loads(json.dumps(events))
            for _, event in original:
                if event.get("type") == "subagent.completed":
                    event["timestamp"] = "2026-01-01T00:01:30Z"
            prior, prior_runtime = native_session(
                owners, original, "root-b", "reviewer-b", "dispatch-b")
            changed_start = json.loads(json.dumps(original))
            for _, event in changed_start:
                if event.get("type") == "subagent.started":
                    event["timestamp"] = "2026-01-01T00:01:20Z"
            prior_runtime.events = tuple(changed_start)
            check(rejects(lambda: prior.finish(prior_runtime)),
                  "changed assignment admitted a review")
            prior.abort(prior_runtime)
            later, later_runtime = native_session(
                owners, alternate_events(events, minute="01"),
                "root-c", "reviewer-c", "dispatch-c")
            check(rejects(lambda: later.finish(later_runtime)),
                  "terminal-only abort forgot the originally observed start")

            owners = module.ReviewOwnership()
            prior, prior_runtime = native_session(
                owners, events, "root-b", "reviewer-b", "dispatch-b")
            prior.finish(prior_runtime)
            partial = [item for item in events if item[1].get("type") in {
                "subagent.started", "subagent.completed"}]
            prior.observe_cli_events(partial)
            later, later_runtime = native_session(
                owners, alternate_events(events), "root-c", "reviewer-c", "dispatch-c")
            check(not rejects(lambda: later.finish(later_runtime)),
                  "partial unchanged interval blocked a sequential reviewer")

            owners = module.ReviewOwnership()
            later, later_runtime = native_session(
                owners, alternate_events(events), "root-c", "reviewer-c", "dispatch-c")
            later.finish(later_runtime)
            prior, prior_runtime = native_session(
                owners, events, "root-b", "reviewer-b", "dispatch-b")
            changed_start = json.loads(json.dumps(next(
                item for item in events if item[1].get("type") == "subagent.started")))
            changed_start[1]["timestamp"] = "2026-01-01T00:02:05Z"
            check(rejects(lambda: prior.observe_cli_events((changed_start,))),
                  "changed active start was not considered")
            check(rejects(lambda: later.review_state((), (), pre_review_required=True)),
                  "changed active start did not invalidate a possible overlap")

            owners = module.ReviewOwnership()
            prior, prior_runtime = native_session(
                owners, events, "root-b", "reviewer-b", "dispatch-b")
            competitor_events = alternate_events(events, minute="01")
            for _, event in competitor_events:
                if event.get("type") == "subagent.started":
                    event["agentId"] = "unexpected-reviewer"
            competitor_runtime = module.CliReviewRuntime(
                competitor_events, dispatch_id="dispatch-c", root_session_id="root-c",
                candidate_reader=reader, required_reads=targets)
            competitor = module.ReviewSession(
                "coordinator", "implementer", scope, b,
                identity=("owner/repo", 1, a), owners=owners,
                readers={"read-candidate": reader})
            check(rejects(lambda: competitor.begin(competitor_runtime, "reviewer-c")),
                  "overlapping contradictory owner was admitted")
            check(rejects(lambda: prior.finish(prior_runtime)),
                  "contradictory competing start was discarded")
            check(rejects(lambda: competitor.abort(competitor_runtime))
                  and not competitor.lease.finished,
                  "contradictory competing start was retired")
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
        owners = module.ReviewOwnership()
        first_owner = "reviewer-a"
        first = completed_session(owners, 1, first_owner, "dispatch-1", "root-a",
                                  interaction="interaction-a")
        check(first.report is not None, "first completed review did not publish a report")
        same_root_calls = []
        reused = module.ReviewSession(
            "coordinator", "implementer", frozenset({"case"}), b,
            identity=("owner/repo", 1, a), owners=owners)
        same_root = types.SimpleNamespace(
            root_session_id="root-a",
            start=lambda **kw: same_root_calls.append(kw) or "dispatch-2")
        check(rejects(lambda: reused.begin(same_root, first_owner)),
              "reviewer identity was reused in its root session")
        check(not same_root_calls, "closed reviewer was dispatched again")
        second_owner = "reviewer-b"
        late_activity = ("root-a", {
            "type": "assistant.turn_start", "timestamp": "2026-01-01T00:00:20Z",
            "agentId": first_owner, "data": {"turnId": "late"},
        })
        try:
            second = completed_session(
                owners, 2, second_owner, "dispatch-1", "root-b",
                interaction="interaction-b", started_at="2026-01-01T00:00:06Z",
                activity_at="2026-01-01T00:00:08Z",
                completed_at="2026-01-01T00:00:10Z", extra_events=(late_activity,))
        except module.ReviewError as error:
            raise ContractViolation(
                "late activity from reviewer A invalidated reviewer B: " + str(error)) from error
        check(second.report is not None, "second completed review did not publish a report")
        check(second.review_state((), (), pre_review_required=False)[0],
              "unrelated reviewer report lost readiness")
        check(rejects(lambda: first.review_state((), (), pre_review_required=True)),
              "late activity in another review's snapshot did not invalidate its owner")
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
            validator = getattr(module, "validate_reviewer_activity", None)
            owner, dispatch, interaction = "reviewer", "dispatch-1", "interaction-1"
            started_at, completed_at = "2026-01-01T00:00:01Z", "2026-01-01T00:00:05Z"
            if callable(validator):
                valid = (
                    ("root-session", {
                        "type": "subagent.started", "timestamp": started_at, "agentId": owner,
                        "data": {"toolCallId": dispatch, "interactionId": interaction},
                    }),
                    ("review-session", {
                        "type": "assistant.message", "timestamp": "2026-01-01T00:00:03Z",
                        "agentId": owner,
                        "data": {"interactionId": interaction, "turnId": "turn-1"},
                    }),
                )
                accepted = validator(
                    valid, owner=owner, dispatch=dispatch, interaction=interaction,
                    started_at=started_at, completed_at=completed_at)
                check(len(accepted) == 2, "valid assigned reviewer activity was rejected")
                contradictory_message = ("review-session", {
                    "type": "assistant.message", "timestamp": "2026-01-01T00:00:04Z",
                    "agentId": owner,
                    "data": {"interactionId": "other-interaction", "turnId": "turn-2"},
                })
                check(rejects(lambda: validator(
                    valid + (contradictory_message,), owner=owner, dispatch=dispatch,
                    interaction=interaction, started_at=started_at, completed_at=completed_at)),
                    "contradictory initial interaction was admitted")
                additional_dispatch = ("root-session", {
                    "type": "subagent.started", "timestamp": "2026-01-01T00:00:04Z",
                    "agentId": owner,
                    "data": {"toolCallId": "dispatch-2", "interactionId": "other-interaction"},
                })
                check(rejects(lambda: validator(
                    valid + (additional_dispatch,), owner=owner, dispatch=dispatch,
                    interaction=interaction, started_at=started_at, completed_at=completed_at)),
                    "additional initial dispatch was admitted")
            owners = module.ReviewOwnership()
            session = completed_session(
                owners, 2, owner, dispatch, "root-session", interaction=interaction,
                completed_at=completed_at)
            late_prohibited_event = ("root-session-reviewer", {
                "type": "tool.execution_start", "timestamp": completed_at,
                "agentId": owner,
                "data": {
                    "interactionId": interaction, "turnId": "turn-1",
                    "toolCallId": "late-bash", "toolName": "bash",
                    "arguments": {"command": "not permitted"},
                },
            })
            check(rejects(lambda: session.observe_cli_events((late_prohibited_event,))),
                  "late in-turn prohibited activity remained valid")
            check(rejects(lambda: session.review_state(
                (), (), pre_review_required=True)),
                "late in-turn prohibited activity did not invalidate readiness")
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
            for operation in ("finish", "abort"):
                first, first_runtime = completed_session(
                    module.ReviewOwnership(), 1, "reviewer", "dispatch", "root-a", finish=False)
                second, second_runtime = completed_session(
                    module.ReviewOwnership(), 2, "reviewer", "dispatch", "root-b", finish=False)
                check(rejects(lambda: getattr(first, operation)(second_runtime)),
                      "other root runtime was accepted for " + operation)
                check(not first.lease.finished and first.report is None,
                      "wrong root changed the assigned review")
                first.finish(first_runtime)
                second.finish(second_runtime)
                check(first.review_state((), (), pre_review_required=True)[0]
                      and second.review_state((), (), pre_review_required=True)[0],
                      "matching root reviews did not remain valid")
        else:
            raise review.ReviewError("unknown review-session probe")
    return {"kind": "host", "checks": 2, "detail": "registered production reducer executed"}


@probe_result("host")
def _ci_history(probe: str) -> dict:
    from datetime import datetime, timedelta, timezone
    from itertools import combinations
    from scripts.workflow_pilot import adaptive_gate, candidate_evidence, pr_metadata

    checks = 0

    def equal(action, expected, label):
        nonlocal checks
        try:
            actual = action()
        except ValueError as error:
            raise ContractViolation(f"{label}: {error}") from error
        check(actual == expected, label)
        checks += 1

    def rejects(action, label):
        nonlocal checks
        try:
            action()
        except ValueError:
            checks += 1
            return
        raise ContractViolation(label)

    def changed(value, **updates):
        return replace(value, **updates)

    def job(name, conclusion, number, index):
        classifier = name in {"event-classifier", "metadata-classifier",
                              "review-first-classifier"}
        offset = (2 if name == "event-identity" else 4 if name == "event-router"
                  else 6 if classifier else 10 if name in {"summary", "patch-release"} else 8)
        created = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
            minutes=number * 3, seconds=offset)
        return pr_metadata.JobState(
            job_id=number * 100 + index + 1, run_id=number, name=name,
            status="completed", conclusion=conclusion,
            runner_name=None if conclusion == "skipped" else "runner",
            created_at=created, started_at=created,
            completed_at=created if conclusion == "skipped" else created + timedelta(seconds=1),
            candidate_binding=binding if classifier else None,
            candidate_base_ref=pr.base_ref if classifier else None)

    head, base = "a" * 40, "b" * 40
    pr = pr_metadata.PullRequestState(
        repository="owner/repo", repository_id=123, repository_owner_id=456,
        pull_request_id=789, pull_request_node_id="PR_fixture", number=42,
        head_sha=head, head_ref="topic", base_sha=base, base_ref="master",
        title="fixture", body="", updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    binding = (pr.number, head, base)
    workers = (
        "host-tests", "workflow-pilot-tests", "build", "extended-host-tests",
        "ownership-probe-tests", "game-localization-tests", "legacy",
    )
    new_workers = ("workflow-pilot-tests", "ownership-probe-tests", "game-localization-tests")

    def run(kind, number, *, historical=False, event="pull_request"):
        classifier = {
            "preflight": "review-first-classifier",
            "metadata": "metadata-classifier",
            "full": "event-classifier",
        }[kind]
        definitions = [("event-identity", "success"), ("event-router", "success"),
                       (classifier, "success")]
        for name in workers:
            if historical and name in new_workers:
                continue
            conclusion = (
                "success" if kind == "full" or name in {"host-tests", "build"} else "skipped")
            definitions.append((name, conclusion))
        definitions.append(("summary", "failure" if kind == "preflight" else "success"))
        jobs = tuple(job(name, conclusion, number, index)
                     for index, (name, conclusion) in enumerate(definitions))
        mode = {"preflight": "review-first", "metadata": "metadata-only", "full": "full"}[kind]
        if historical:
            mode = "historical-" + mode
        created = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=number * 3)
        return pr_metadata.RunState(
            run_id=number, run_attempt=1, run_number=number, workflow_id=7,
            head_sha=head, head_branch=pr.head_ref, created_at=created,
            run_started_at=created + timedelta(seconds=1),
            updated_at=created + timedelta(seconds=30),
            candidate_binding=binding, candidate_base_ref=pr.base_ref,
            binding="explicit-same", mode=mode, event=event, status="completed",
            conclusion="failure" if kind == "preflight" else "success", jobs=jobs)

    def edit_job(value, name, **updates):
        return changed(value, jobs=tuple(
            changed(item, **updates) if item.name == name else item for item in value.jobs))

    def statuses(value):
        return {item.name: (item.status, item.conclusion) for item in value.jobs}

    def lane(*runs, pull_request=pr):
        return adaptive_gate._history_lane(pull_request, base, runs)

    def classified(value):
        return changed(value, mode=pr_metadata._run_mode(
            value.jobs, run_id=value.run_id, status=value.status))

    old = run("preflight", 1, historical=True)
    current = run("preflight", 2)
    dispatch = run("full", 3, event="workflow_dispatch")
    concurrent = run("full", 3)
    old_metadata = run("metadata", 4, historical=True)
    record = {
        "pr_number": pr.number, "head_sha": head, "base_sha": base, "base_ref": pr.base_ref,
        "watermark": 2, "dispatch_requested_at": "2026-01-01T00:08:00Z",
        "full_run_id": None, "full_attempt": None,
    }

    if probe == "ci-history:lifecycle:entries":
        equal(lambda: lane(), None, "empty history should have no initial lane")
        equal(lambda: lane(classified(old)), "review-first", "historical preflight lost its lane")
        equal(lambda: lane(classified(current)), "review-first", "current preflight lost its lane")
        equal(lambda: lane(classified(old), classified(current)), "review-first",
              "compatible preflights disagree")
        equal(lambda: pr_metadata._run_mode(
            old_metadata.jobs, run_id=old_metadata.run_id, status=old_metadata.status),
              "historical-metadata-only", "historical metadata misclassified")
        rejects(lambda: lane(classified(old_metadata)),
                "metadata alone established an initial lane")
        rejects(lambda: lane(changed(old, event="workflow_dispatch")),
                "non-pull-request event established an initial lane")
        rejects(lambda: lane(changed(old, status="in_progress")),
                "active preflight established an initial lane")
        rejects(lambda: lane(changed(old, conclusion="success")),
                "successful preflight established an initial lane")
        rejects(lambda: lane(changed(current, mode="active-review-first", status="in_progress")),
                "active review-first run established an initial lane")
    elif probe == "ci-history:lifecycle:preservation":
        equal(lambda: lane(old, dispatch), "review-first", "historical provenance was discarded")
        equal(lambda: adaptive_gate._reserved_dispatch(record, pr, dispatch, (old, dispatch)),
              False, "old preflight authorized a current dispatch")
        equal(lambda: adaptive_gate._reserved_dispatch(record, pr, dispatch, (current, dispatch)),
              True, "current preflight failed to authorize its reserved dispatch")
        rejects(lambda: pr_metadata._current_full_authorization((old, current)),
                "preflight history authorized a full Build")
    elif probe == "ci-history:lifecycle:resets":
        retry = run("preflight", 5)
        for first in (old, current):
            for adapter in ("host-tests", "build"):
                for conclusion in ("failure", "cancelled", "timed_out"):
                    failed = edit_job(first, adapter, conclusion=conclusion)
                    equal(lambda: lane(failed, retry), "review-first", "valid retry lost its lane")
                    rejects(lambda: lane(failed), "failed preflight alone established a lane")
    elif probe == "ci-history:lifecycle:terminals":
        equal(lambda: pr_metadata._pre_full_preparation((old, current, old_metadata)),
              True, "current preflight with historical metadata was refused")
        equal(lambda: pr_metadata._pre_full_preparation((old,)), False,
              "old preflight alone authorized metadata preparation")
        equal(lambda: pr_metadata._pre_full_preparation((old, current, concurrent)), False,
              "full run allowed pre-full preparation")
        equal(lambda: adaptive_gate._reserved_dispatch(record, pr, dispatch, (current, dispatch)),
              True, "bound current dispatch was refused")
        wrong_workflow = changed(dispatch, workflow_id=8)
        equal(lambda: adaptive_gate._reserved_dispatch(record, pr, wrong_workflow,
                                                       (current, wrong_workflow)),
              False, "different workflow authorized a dispatch")
        rejects(lambda: pr_metadata._current_full_authorization((old, current)),
                "preflight became full-Build authority")
    elif probe == "ci-history:wire:producers":
        metadata = run("metadata", 6)
        historical_full = run("full", 7, historical=True)
        for value, expected in (
            (old, "historical-review-first"), (current, "review-first"),
            (old_metadata, "historical-metadata-only"), (metadata, "metadata-only"),
            (historical_full, "historical-full"), (dispatch, "full"),
        ):
            equal(lambda: pr_metadata._run_mode(
                value.jobs, run_id=value.run_id, status=value.status),
                  expected, f"{expected} shape misclassified")
        equal(lambda: pr_metadata._run_mode(
            tuple(reversed(current.jobs)), run_id=current.run_id, status="completed"),
              "review-first", "job ordering changed classification")
        for value in (current, metadata, dispatch):
            for count in (1, 2):
                for kept in combinations(new_workers, count):
                    partial = tuple(item for item in value.jobs
                                    if item.name not in new_workers or item.name in kept)
                    rejects(lambda: pr_metadata._run_mode(
                        partial, run_id=value.run_id, status=value.status),
                            "partial current job shape accepted")
        publisher = job("patch-release", "skipped", old.run_id, len(old.jobs))
        equal(lambda: pr_metadata._run_mode(
            (*old.jobs, publisher), run_id=old.run_id, status="completed"),
              "historical-review-first", "skipped historical publisher rejected")
        rejects(lambda: pr_metadata._run_mode(
            (*old.jobs, changed(publisher, conclusion="success", runner_name="runner")),
            run_id=old.run_id, status="completed"), "active historical publisher accepted")
    elif probe == "ci-history:wire:consumers":
        equal(lambda: lane(old, dispatch), "review-first",
              "historical preflight plus current full dispatch lost its initial lane")
        equal(lambda: lane(old, current), "review-first", "current and historical lanes disagree")
        rejects(lambda: lane(old, concurrent), "contradictory pull-request full run accepted")
        equal(lambda: pr_metadata._pre_full_preparation((old, current, old_metadata)),
              True, "historical metadata blocked valid current preparation")
        provisional = {
            "base_sha": base, "mode": "concurrent", "full_run_id": None,
            "dispatch_requested_at": None, "abandoned_reason": None,
            "intake_control": {"repository": pr.repository, "repository_id": pr.repository_id},
        }
        equal(lambda: (adaptive_gate._finalize_intake(provisional, pr, (old, dispatch)),
                       provisional["mode"], "intake_control" in provisional),
              (None, "review-first", False), "historical lane was not finalized")
        rejects(lambda: adaptive_gate._finalize_intake(
            {"base_sha": base, "mode": "concurrent"}, pr, (old, dispatch)),
            "contradictory finalized intake accepted")
    elif probe == "ci-history:wire:validators":
        import inspect
        try:
            inspect.signature(candidate_evidence.preflight_success).bind(
                statuses(old), historical=True)
        except TypeError:
            historical_arg = {}
        else:
            historical_arg = {"historical": True}
        equal(lambda: candidate_evidence.preflight_success(statuses(old), **historical_arg), True,
              "valid historical preflight rejected")
        equal(lambda: candidate_evidence.preflight_success(statuses(current)), True,
              "valid current preflight rejected")
        for value, kwargs in ((old, historical_arg), (current, {})):
            for name in (
                "event-identity", "event-router", "review-first-classifier",
                "host-tests", "build", "extended-host-tests", "legacy", "summary",
            ):
                missing = statuses(value)
                del missing[name]
                equal(lambda: candidate_evidence.preflight_success(missing, **kwargs), False,
                      f"missing {name} accepted")
                incorrect = dict(statuses(value))
                conclusion = incorrect[name][1]
                incorrect[name] = ("completed", "failure" if conclusion == "success" else "success")
                equal(lambda: candidate_evidence.preflight_success(incorrect, **kwargs), False,
                      f"incorrect {name} accepted")
                incorrect[name] = ("in_progress", None)
                equal(lambda: candidate_evidence.preflight_success(incorrect, **kwargs), False,
                      f"active {name} accepted")
        for name in new_workers:
            incomplete = statuses(current)
            del incomplete[name]
            equal(lambda: candidate_evidence.preflight_success(incomplete), False,
                  "missing current worker accepted")
            incorrect = dict(statuses(current))
            incorrect[name] = ("completed", "success")
            equal(lambda: candidate_evidence.preflight_success(incorrect), False,
                  "active current worker accepted")
            unexpected = dict(statuses(old))
            unexpected[name] = ("completed", "skipped")
            equal(lambda: candidate_evidence.preflight_success(unexpected, **historical_arg), False,
                  "partial historical worker shape accepted")
        publisher = {**statuses(old), "patch-release": ("completed", "skipped")}
        equal(lambda: candidate_evidence.preflight_success(publisher, **historical_arg), True,
              "skipped historical publisher rejected")
        publisher["patch-release"] = ("completed", "success")
        equal(lambda: candidate_evidence.preflight_success(publisher, **historical_arg), False,
              "active historical publisher accepted")
        retry = run("preflight", 5)

        def reject_with_retry(malformed, label):
            rejects(lambda: lane(malformed, retry), label)
            for adapter in ("host-tests", "build"):
                for conclusion in ("failure", "cancelled", "timed_out"):
                    failed = edit_job(malformed, adapter, conclusion=conclusion)
                    rejects(lambda: lane(failed, retry), label + " with failed " + adapter)

        for value in (old, current):
            for name in ("event-identity", "event-router", "review-first-classifier",
                         "extended-host-tests", "legacy", "summary"):
                conclusion = "failure" if name in {
                    "event-identity", "event-router", "review-first-classifier",
                } else "success"
                malformed = edit_job(value, name, conclusion=conclusion, runner_name="runner")
                reject_with_retry(malformed, f"malformed {name} accepted with a valid retry")
            if value is current:
                for name in new_workers:
                    malformed = edit_job(value, name, conclusion="success", runner_name="runner")
                    reject_with_retry(malformed, f"malformed {name} accepted with a valid retry")
        duplicate = changed(current, jobs=(*current.jobs, current.jobs[0]))
        rejects(lambda: lane(duplicate), "duplicate job identity accepted")
    elif probe == "ci-history:wire:replay":
        failed = edit_job(old, "host-tests", conclusion="failure")
        retry = run("preflight", 5)
        equal(lambda: lane(failed, retry), "review-first", "successful retry was blocked")
        equal(lambda: lane(retry, failed), "review-first", "retry depended on observation order")
        rejects(lambda: lane(failed), "failed attempt alone established authority")
        rejects(lambda: lane(failed, retry, concurrent), "contradictory replay accepted")
    elif probe == "ci-history:wire:stale-bindings":
        foreign = (
            changed(concurrent, head_sha="c" * 40),
            changed(concurrent, head_branch="other"),
            changed(concurrent, candidate_binding=(pr.number, head, "c" * 40)),
            changed(concurrent, candidate_base_ref="other"),
        )
        for unrelated in foreign:
            equal(lambda: lane(current, unrelated), "review-first",
                  "unrelated run changed the lane")
        advanced = changed(pr, base_sha="c" * 40)
        equal(lambda: lane(old, current, pull_request=advanced), "review-first",
              "unrelated live base advance changed the frozen initial lane")
        equal(lambda: adaptive_gate._reserved_dispatch(record, advanced, dispatch,
                                                       (current, dispatch)),
              True, "unrelated live base advance invalidated a bound dispatch")
        for unbound in (changed(old, candidate_binding=None),
                        changed(old, candidate_base_ref=None)):
            rejects(lambda: lane(unbound, current), "unbound historical preflight accepted")
        equal(lambda: pr_metadata._pre_full_preparation(
            (current, changed(old, binding="unbound", candidate_binding=None))),
              False, "unbound history authorized metadata preparation")
        pinned = {**record, "full_run_id": dispatch.run_id, "full_attempt": dispatch.run_attempt}
        equal(lambda: adaptive_gate._reserved_dispatch(pinned, pr, dispatch, (current, dispatch)),
              True, "exact pinned dispatch was rejected")
        for field in ("full_run_id", "full_attempt"):
            stale = {**pinned, field: pinned[field] + 1}
            equal(lambda: adaptive_gate._reserved_dispatch(
                stale, pr, dispatch, (current, dispatch)),
                False, f"stale dispatch {field} accepted")
    else:
        raise review.ReviewError("unknown CI history probe")
    return {
        "kind": "host", "checks": checks,
        "detail": "selected production history predicates executed",
    }


@probe_result("host")
def _build_topology(probe):
    from scripts.upstream_port import verify
    from tests.workflows import test_build_ci_topology as topology
    from tests.upstream_port import test_verify as upstream_tests

    workers, model = build_source_model(
        Path(BUILD_WORKFLOW).read_text(), Path(BUILD_TOPOLOGY).read_bytes(),
        Path(BUILD_VERIFY).read_bytes())
    parts = probe.split(":")
    review.require(len(parts) == 3 and parts[0] == "build-topology",
                   "unknown Build topology probe")
    role, name = parts[1:]
    review.require(
        (role in {"boundary", "target-boundary", "literal"} and name in model)
        or (role in {"producer", "consumer", "topology-order", "upstream-order"}
            and name in workers)
        or (role == "source-test" and name in {"topology-order", "upstream-order"})
        or (role == "stale" and name == "width-script"),
        "unregistered Build topology member")
    if role == "source-test":
        if name == "topology-order":
            test_class = topology.ConsolidatedBuildTopologyTests
            test_name = "test_worker_blocks_can_precede_other_job_fields"
            module = topology
            attribute = "WORKFLOW"
        else:
            test_class = upstream_tests.VerifyCliCwdTests
            test_name = "test_worker_mapping_order_preserves_target_gate_equivalence"
            module = upstream_tests
            attribute = "BUILD_WORKFLOW_PATH"
        original = Path(BUILD_WORKFLOW).read_text()
        job = topology._job_blocks(original)["host-tests"]
        changed = original.replace(job, build_reorder_field(job, "steps"), 1)
        review.require(changed != original, "worker order control did not change the workflow")
        with tempfile.TemporaryDirectory(prefix="build-worker-order-") as directory:
            path = Path(directory) / "build.yml"
            path.write_text(changed)
            workflow = path if module is topology else str(path)
            with mock.patch.object(module, attribute, workflow):
                result = unittest.TextTestRunner(stream=io.StringIO()).run(
                    unittest.TestSuite([test_class(test_name)]))
        if result.errors:
            return {"kind": "host", "verdict": "unavailable", "checks": 0,
                    "detail": (result.errors[0][1] or "source test errored")[-review.MAX_DETAIL:]}
        for outcome, records in (
            ("skipped", result.skipped),
            ("expected-failure", result.expectedFailures),
            ("unexpected-success", result.unexpectedSuccesses),
        ):
            if records:
                return {"kind": "host", "verdict": "unavailable", "checks": 0,
                        "detail": f"source test outcome is non-evidentiary: {outcome}"}
        if result.failures:
            return {"kind": "host", "verdict": "contract-violation", "checks": 1,
                    "detail": (result.failures[0][1] or "source test failed")[-review.MAX_DETAIL:]}
        review.require(result.testsRun == 1, "source test did not run exactly once")
        return {"kind": "host", "checks": 1,
                "detail": "actual source-revision test passed on steps-first worker mapping"}
    text = Path(BUILD_WORKFLOW).read_text()
    jobs = build_job_blocks(text)
    checks = 0

    def observe(condition, message):
        nonlocal checks
        checks += 1
        check(condition, message)

    def target(directory):
        root = Path(directory) / "target"
        root.mkdir()
        command(["/usr/bin/git", "init", "-q", str(root)])
        workflow = root / BUILD_WORKFLOW
        workflow.parent.mkdir(parents=True)
        workflow.write_text(text)
        helper = root / BUILD_ARCHIVAL_HELPER
        helper.parent.mkdir(parents=True)
        shutil.copyfile(BUILD_ARCHIVAL_HELPER, helper)
        return root, workflow

    def rejected(root, workflow, changed):
        workflow.write_text(changed)
        try:
            verify.run_gates(str(root), dry_run=True)
        except ValueError:
            return True
        return False

    def width_step():
        found = [step for job in jobs.values() for step in build_step_blocks(job)
                 if step.startswith(
                     "    - name: Run full-game localization width contract (issue #18)\n")]
        review.require(len(found) == 1, "missing or ambiguous width gate")
        return found[0]

    if role == "boundary":
        job = jobs[name]
        steps = build_step_blocks(job)
        for index, step in enumerate(steps):
            for blank in ("", "    ", "    \r", "    \t", "        ",
                          "    \u0085", "    \u2028", "    \u2029"):
                changed = step.replace(
                    "\n", "\n" + blank + "\n      continue-on-error: true\n", 1)
                parsed = topology._step_blocks(job.replace(step, changed, 1))
                observe(index < len(parsed) and "      continue-on-error: true" in parsed[index],
                        f"{name} step {index} lost a field after {blank!r}")
        width = width_step()
        if width in steps:
            for blank in ("    ", "    \u0085", "    \u2028", "    \u2029"):
                changed = text.replace(width, width + blank + "\n      continue-on-error: true\n", 1)
                observe(bool(topology._errors(changed, False)), "width gate admitted hidden failure")
    elif role == "target-boundary":
        job = jobs[name]
        review.require(text.count(job) == 1 and job.count("    steps:\n") == 1,
                       "ambiguous target job or steps")
        with tempfile.TemporaryDirectory(prefix="build-target-boundary-") as directory:
            root, workflow = target(directory)
            baseline = verify.run_gates(str(root), dry_run=True)
            observe(len(baseline) == len(verify.gates()) and all(not item.ran for item in baseline),
                    f"{name} baseline dry-run failed")
            for position in ("after-header", "before-steps", "after-steps"):
                for indent in (1, 2, 3, 4):
                    field = " " * indent + "permissions: write-all\n"
                    if position == "after-header":
                        changed_job = field + job
                    elif position == "before-steps":
                        changed_job = job.replace("    steps:\n", field + "    steps:\n", 1)
                    else:
                        changed_job = job + field
                    changed = text.replace(job, changed_job, 1)
                    observe(rejected(root, workflow, changed),
                            f"{name} accepted {indent}-space field {position}")
    elif role == "literal":
        steps = [step for step in build_step_blocks(jobs[name]) if "\n      run: |\n" in step]
        review.require(steps, "literal member has no source scripts")
        for index, step in enumerate(steps):
            script = topology._literal_run_script(step)
            commands = topology._run_block_commands(step)
            for blank in ("    ", "", " ", "      ", "        ", "        \t", "    \r"):
                changed = step.replace("      run: |\n", "      run: |\n" + blank + "\n", 1)
                expected = blank[8:] + "\n" + script
                observe(topology._literal_run_script(changed) == expected,
                        f"{name} literal {index} lost script after {blank!r}")
                observe(topology._run_block_commands(changed) == commands,
                        f"{name} literal {index} changed commands after {blank!r}")
                lines = changed.split("\n")
                start = lines.index("      run: |") + 1
                observe(verify._literal_run_script(lines, start, len(lines), "|", name) == expected,
                        f"{name} target literal {index} lost script after {blank!r}")
    elif role == "producer":
        baseline = verify._parse_workflow_structure_text(text)
        observe(not topology._errors(text, False), "baseline topology rejected")
        for field in ("steps", "env"):
            changed = text.replace(jobs[name], build_reorder_field(jobs[name], field), 1)
            observe(changed != text, f"{name} {field} did not move")
            observe(not topology._errors(changed, False), f"{name} {field} rejected")
            observe(verify._parse_workflow_structure_text(changed) == baseline,
                    f"{name} {field} changed parsed workflow")
    elif role in {"topology-order", "upstream-order"}:
        baseline = verify._parse_workflow_structure_text(text)
        fields = ("steps", "env") if role == "topology-order" else ("steps",)
        for field in fields:
            reordered = build_reorder_field(jobs[name], field)
            changed = text.replace(jobs[name], reordered, 1)
            observe(changed != text, f"{name} {field} did not move")
            if role == "topology-order":
                observe(not topology._errors(changed, False),
                        f"{name} topology validator rejected equivalent reordered {field}")
            try:
                parsed = verify._parse_workflow_structure_text(changed)
            except ValueError as error:
                observe(False, f"{name} verifier rejected equivalent reordered {field}: {error}")
            else:
                observe(parsed == baseline,
                        f"{name} reordered {field} changed the parsed workflow")
    elif role == "consumer":
        with tempfile.TemporaryDirectory(prefix="build-target-") as directory:
            root, workflow = target(directory)
            baseline = verify.run_gates(str(root), dry_run=True)
            observe(len(baseline) == len(verify.gates()) and all(not item.ran for item in baseline),
                    "baseline dry-run did not describe all gates")
            for field in ("steps", "env"):
                changed = text.replace(jobs[name], build_reorder_field(jobs[name], field), 1)
                review.require(changed != text, f"{name} {field} did not move")
                workflow.write_text(changed)
                try:
                    result = verify.run_gates(str(root), dry_run=True)
                except ValueError as error:
                    observe(False, f"{name} rejected equivalent reordered {field}: {error}")
                else:
                    observe(len(result) == len(baseline)
                            and all(not item.ran for item in result),
                            f"{name} reordered {field} changed selected gates")
            for before, after in (
                ("    runs-on: ubuntu-24.04", "    runs-on: self-hosted"),
                ("    steps:\n", "    container: ubuntu:latest\n    steps:\n"),
            ):
                changed_job = jobs[name].replace(before, after, 1)
                review.require(changed_job != jobs[name], "missing unsafe mutation location")
                changed = text.replace(jobs[name], changed_job, 1)
                observe(rejected(root, workflow, changed), f"{name} unsafe target admitted")
    else:
        width = width_step()
        commands = topology._run_block_commands(width)
        review.require(commands, "width gate has no commands")
        last = "        " + commands[-1] + "\n"
        review.require(width.count(last) == 1, "ambiguous width command")
        changed = text.replace(width, width.replace(last, last + "    \n        exit 73\n", 1), 1)
        with tempfile.TemporaryDirectory(prefix="build-stale-") as directory:
            root, workflow = target(directory)
            baseline = verify.run_gates(str(root), dry_run=True)
            observe(len(baseline) == len(verify.gates()), "baseline target rejected")
            observe(rejected(root, workflow, changed), "target ignored command after shallow blank")
    return {"kind": "host", "checks": checks, "detail": "selected Build source behavior exercised"}


def run_probe(probe: str) -> dict:
    allowed = {
        *(f"aoe-phase:{phase}" for phase in PHASES),
        *(f"aoe-shape:{shape}" for shape in SHAPES),
        "aoe-items", "aoe-targets", "aoe-slots", "aoe-execution",
        "aoe-reference:enabled", "aoe-reference:disabled", "aoe-arm:enabled", "aoe-arm:disabled",
        "generated-output", "generated-consumer", "generated-drift",
        *("cjk-resource:" + family + ":" + role for family in ("generated", "wire", "resource")
          for role in review.FAMILIES[family]),
        *("authored-case:" + role for role in review.FAMILIES["generated"]),
        *(f"ci-history:{family}:{role}" for family in ("lifecycle", "wire")
          for role in review.FAMILIES[family]),
        *("project:" + role for role in review.FAMILIES["action"]),
        *("project-life:" + role for role in review.FAMILIES["lifecycle"]),
        *("project-wire:" + role for role in review.FAMILIES["wire"]),
        *("project-generated:" + role for role in review.FAMILIES["generated"]),
        *("project-resource:" + role for role in review.FAMILIES["resource"]),
        *(f"{family}:{role}" for family in ("lifecycle", "wire") for role in review.FAMILIES[family]),
    }
    if probe.startswith("build-topology:"):
        return _build_topology(probe)
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
    if probe.startswith("cjk-resource:"):
        return _cjk_resource(probe)
    if probe.startswith("authored-case:"):
        return _authored_case(probe)
    if probe.startswith("ci-history:"):
        return _ci_history(probe)
    if probe.startswith(("project:", "project-life:", "project-wire:", "project-generated:",
                         "project-resource:")):
        kind = "arm-object" if probe.startswith("project-resource:") else "host"
        return probe_result(kind)(_project)(probe)
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
