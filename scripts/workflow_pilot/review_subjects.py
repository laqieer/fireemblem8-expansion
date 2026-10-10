"""Closed bindings to existing cases; no candidate-supplied programs or plugins."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import importlib
import io
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import symtable
import sys
import types
import unittest
from unittest.mock import patch

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
    SubjectSpec("TC-OWNERSHIP-SEALED-PLATFORM-STORAGE-001",
                "sealed-platform-storage", "platform-storage"),
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
PLATFORM_SOURCE = "scripts/validation_ownership/runtime_image.py"
PLATFORM_TEST = "scripts/validation_ownership/tests/test_platform_image.py"
PLATFORM_OWNER = "tests/workflows/test_ownership_probe.py"
PLATFORM_MAKE = "scripts/validation_ownership/foundation.mk"
PLATFORM_TOPOLOGY = "tests/workflows/test_build_ci_topology.py"
PLATFORM_CONDITIONS = "scripts/workflow_pilot/tests/test_adaptive_gate.py"
PLATFORM_WORKFLOW = ".github/workflows/build.yml"
PLATFORM_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size",
    "st_mtime_ns", "st_ctime_ns",
)
PLATFORM_DEPENDENCIES = (
    "scripts/validation_ownership/__init__.py",
    "scripts/validation_ownership/tests/__init__.py",
    "scripts/validation_ownership/authority.py",
    "scripts/validation_ownership/budget.py",
    "scripts/validation_ownership/lifecycle.py",
    "scripts/validation_ownership/producer_channel.py",
)


def platform_parse_only(paths):
    executed = {PLATFORM_SOURCE, PLATFORM_TEST, *PLATFORM_DEPENDENCIES}
    inventories = {PLATFORM_OWNER, PLATFORM_TOPOLOGY, PLATFORM_CONDITIONS}
    return {path for path in paths if path not in executed and (
        path in inventories or (
            path.startswith("scripts/validation_ownership/tests/") and path.endswith(".py")
        )
    )}


def platform_inventory_path(path):
    return Path("build/platform-inventory") / (path + ".source")

def platform_parser_path(path):
    return Path("build/platform-trusted-parsers") / (path + ".source")


def platform_inputs(tree):
    """Execution imports are closed; other native suites are parsed, never imported."""
    inputs = {PLATFORM_SOURCE, PLATFORM_TEST, *PLATFORM_DEPENDENCIES,
              PLATFORM_OWNER, PLATFORM_MAKE, PLATFORM_TOPOLOGY,
              PLATFORM_CONDITIONS, PLATFORM_WORKFLOW}
    for path in (PLATFORM_SOURCE, PLATFORM_TEST, *PLATFORM_DEPENDENCIES):
        parsed = ast.parse(tree.read(path))
        package = path.removesuffix(".py").replace("/", ".").rpartition(".")[0]
        if path.endswith("/__init__.py"):
            package = path[:-len("/__init__.py")].replace("/", ".")
        for node in ast.walk(parsed):
            modules = []
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parent = package.split(".")[:len(package.split(".")) - node.level + 1]
                    modules = [".".join((*parent, node.module or ""))]
                else:
                    modules = [node.module or ""]
            for module in modules:
                if module.startswith(("scripts.", "tests.")):
                    relative = module.replace(".", "/") + ".py"
                    review.require(relative in inputs,
                                   "platform execution import needs a reviewed closure")
    # These files define the complete Make selection, not another execution grant.
    for path in tree.under("scripts/validation_ownership/tests"):
        if path.endswith(".py"):
            inputs.add(path)
    return inputs


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
    elif spec.model == "platform-storage":
        inputs = platform_inputs(tree)
        # AST identifies the reviewed finite mutation predicates. Runtime/test
        # outcomes, not this structural check, establish coverage.
        platform_mutation(tree.read(PLATFORM_SOURCE), "admission")
        platform_mutation(tree.read(PLATFORM_SOURCE), "identity")
        platform_mutation(tree.read(PLATFORM_SOURCE), "workspace")
        for role, name, predicate, probe in (
            ("producers", "complete-capture", "RuntimeImage.__init__", "capture"),
            ("consumers", "complete-materialization", "RuntimeImage.materialize", "materialize"),
            ("validators", "source-admission", "RuntimeImage.__init__", "admission"),
            ("replay", "sealed-body", "RuntimeImage.require_sealed", "sealed"),
            ("stale-bindings", "source-identity", "RuntimeImage.__init__/_identity", "identity"),
        ):
            add("wire", role, name, PLATFORM_SOURCE + ":" + predicate, PLATFORM_TEST,
                "complete owned body / original-suite semantic mutation",
                "real API inputs and unchanged baseline suite", "platform:" + probe, inputs)
        for role, predicate, probe in (
            ("entries", "RuntimeImage.__init__", "entries"),
            ("preservation", "cleanup_scope/finish_cleanup", "preservation"),
            ("resets", "ProbeBudget.charge/remaining", "resets"),
            ("terminals", "RuntimeImage.close/close_images", "terminals"),
        ):
            add("lifecycle", role, "owned-storage", PLATFORM_SOURCE + ":" + predicate,
                PLATFORM_TEST, "owned source/backing/destination/primary exception",
                "capture/materialize success, failure and interruption",
                "platform:" + probe, inputs)
        for role, name, probe in (
            ("enabled", "bounded-slices", "workspace"),
            ("disabled", "byte-body", "bytes"),
        ):
            add("resource", role, name, PLATFORM_SOURCE + ":materialize_image/__getitem__",
                PLATFORM_TEST, "sealed budget-owned / compatibility caller-owned bytes",
                "workspace, quota, deadline and closed-body controls",
                "platform:" + probe, inputs, profile=role)
        for role in review.FAMILIES["generated"]:
            add("generated", role, "probe-inventory", PLATFORM_MAKE, PLATFORM_WORKFLOW,
                "parsed Make argv / source test inventory / full-only workflow owner",
                "selected versus expected modules and enabled/disabled owner",
                "platform-owner:" + role, inputs, kind="parsed",
                evidence=("positive", "adversarial", "generated"))
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


def platform_mutation(source, kind):
    """Remove one finite production predicate, retaining all other operations."""
    parsed = ast.parse(source)
    providers = [node for node in parsed.body
                 if isinstance(node, ast.ClassDef) and node.name == "RuntimeImage"]
    review.require(len(providers) == 1, "unknown platform provider")
    identities = [node for node in parsed.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_identity"]
    review.require(len(identities) == 1, "unknown platform identity")
    returns = [node for node in identities[0].body if isinstance(node, ast.Return)]
    review.require(len(returns) == 1 and isinstance(returns[0].value, ast.Tuple)
                   and all(isinstance(node, ast.Attribute) for node in returns[0].value.elts)
                   and len(returns[0].value.elts) == len(PLATFORM_FIELDS)
                   and {node.attr for node in returns[0].value.elts} == set(PLATFORM_FIELDS),
                   "changed platform identity fields need a reviewed model")
    method_name = "__getitem__" if kind == "workspace" else "__init__"
    methods = [node for node in providers[0].body
               if isinstance(node, ast.FunctionDef) and node.name == method_name]
    review.require(len(methods) == 1, "unknown platform predicate")
    changed = 0
    for node in ast.walk(methods[0]):
        if not isinstance(node, ast.If):
            continue
        terms = tuple(ast.walk(node.test))
        if kind == "admission" and any(
            isinstance(term, ast.Attribute) and term.attr == "st_uid"
            for term in terms
        ):
            review.require(isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.Or)
                           and len(node.test.values) == 4, "unknown admission guard")
            node.test = ast.Constant(False)
            changed += 1
        elif kind == "identity" and any(
            isinstance(term, ast.Call) and isinstance(term.func, ast.Name)
            and term.func.id == "_identity" for term in terms
        ):
            review.require(isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.Or)
                           and len(node.test.values) == 3, "unknown postcapture identity guard")
            node.test = node.test.values[0]
            changed += 1
        elif kind == "workspace" and isinstance(node.test, ast.Compare) and (
            isinstance(node.test.left, ast.Name)
            and len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Gt)
            and isinstance(node.test.comparators[0], ast.Name)
            and node.test.comparators[0].id == "BLOCK_BYTES"
        ):
            node.test = ast.Constant(False)
            changed += 1
    review.require(changed == 1, "missing/ambiguous platform mutation predicate")
    return ast.fix_missing_locations(parsed), method_name


def _platform_suite(module, names=None):
    loader = unittest.TestLoader()
    suite = (loader.loadTestsFromTestCase(module.PlatformImageTests) if names is None else
             unittest.TestSuite(module.PlatformImageTests(name) for name in names))
    review.require(not loader.errors and suite.countTestCases() > 0, "empty platform test selection")
    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    check(not result.skipped and not result.expectedFailures
          and not result.unexpectedSuccesses,
          "provider tests skipped or used expected-failure outcomes")
    return result


def _platform_tests(names):
    module = importlib.import_module(PLATFORM_TEST.removesuffix(".py").replace("/", "."))
    result = _platform_suite(module, names)
    check(result.testsRun == len(names) and result.wasSuccessful(),
          "selected actual provider tests failed: " + str(result.failures + result.errors)[:1800])
    return result.testsRun


@probe_result("host")
def _platform_coverage(kind):
    module = importlib.import_module(PLATFORM_TEST.removesuffix(".py").replace("/", "."))
    provider = importlib.import_module(PLATFORM_SOURCE.removesuffix(".py").replace("/", "."))
    baseline = _platform_suite(module)
    check(baseline.wasSuccessful(), "original source suite fails before mutation")
    parsed, method = platform_mutation(Path(PLATFORM_SOURCE).read_bytes(), kind)
    mutant = types.ModuleType("scripts.validation_ownership._review_platform_mutant")
    mutant.__package__ = "scripts.validation_ownership"
    exec(compile(parsed, PLATFORM_SOURCE + ":semantic-mutation", "exec"), mutant.__dict__)
    with patch.object(provider.RuntimeImage, method, getattr(mutant.RuntimeImage, method)):
        result = _platform_suite(module)
    review.require(result.testsRun == baseline.testsRun and not result.errors,
                   "mutation execution incomplete or errored")
    expected = {"admission": 9, "identity": 16, "workspace": 3}[kind]
    check(len(result.failures) == expected,
          f"coverage gap, not an old runtime violation: original {baseline.testsRun}-case suite "
          f"has {len(result.failures)}/{expected} input assertion failures after actual {kind} "
          "predicate removal; original unmutated suite passed")
    return {"kind": "host", "checks": baseline.testsRun + result.testsRun,
            "detail": f"original unmutated {baseline.testsRun}-case suite passed; actual {kind} "
                      f"predicate removal killed by all {expected} independent input assertions; "
                      "coverage observation, not a claim of historical runtime malfunction"}


@probe_result("host")
def _platform(probe):
    from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
    from scripts.validation_ownership.runtime_image import (
        RuntimeImage, close_images, image_digest, materialize_image,
    )
    cases = {
        "capture": (
            "test_complete_body_and_materialization_use_actual_immutable_backing",
            "test_real_frontend_body_uses_snapshot_not_source_file_allowance",
        ),
        "materialize": (
            "test_failed_materialization_removes_owned_file_and_preserves_failure",
            "test_materialization_handles_actual_short_writes_and_failed_open",
        ),
        "entries": (
            "test_source_descriptor_closes_if_fdopen_handoff_fails",
            "test_complete_storage_and_work_quotas_fail_without_leaking_descriptors",
        ),
        "preservation": (
            "test_capture_preserves_primary_with_source_and_backing_close_failures",
            "test_materialization_preserves_primary_with_combined_cleanup_failures",
        ),
    }
    if probe in cases:
        checks = _platform_tests(cases[probe])
    else:
        body = b"caller-owned compatibility body"
        target = Path("build/platform-output")
        budget = ProbeBudget()
        image = None
        try:
            if probe == "bytes":
                check(materialize_image(target, body) == len(body) and target.read_bytes() == body,
                      "compatibility bytes do not materialize completely")
                check(image_digest(body) == hashlib.sha256(body).hexdigest(),
                      "compatibility digest differs")
                target.unlink()
                cache = {"bytes": body}
                close_images(cache)
                check(cache == {}, "compatibility cache cleanup failed")
                original_open = os.fdopen
                for amount in (0, None, -1, len(body) + 1, True, object()):
                    streams = []

                    def wrap(descriptor, *args, **kwargs):
                        stream = original_open(descriptor, *args, **kwargs)
                        streams.append(stream)
                        return types.SimpleNamespace(
                            write=lambda data, amount=amount: amount, close=stream.close,
                        )

                    with patch("scripts.validation_ownership.runtime_image.os.fdopen", wrap):
                        try:
                            materialize_image(target, body)
                        except MakeProbeError:
                            pass
                        else:
                            check(False, "compatibility invalid progress succeeded")
                    check(not target.exists() and len(streams) == 1 and streams[0].closed,
                          "compatibility failure retained owned file/descriptor")
            else:
                image = RuntimeImage(Path("/usr/bin/make").resolve(), budget)
                if probe == "sealed":
                    import errno
                    import fcntl
                    from scripts.validation_ownership.runtime_image import IMAGE_SEALS
                    descriptor = image.descriptor
                    check(fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) == IMAGE_SEALS,
                          "backing seals differ")
                    before = image.digest(), image[:64]
                    for action in (lambda: os.pwrite(descriptor, b"X", 0),
                                   lambda: os.ftruncate(descriptor, 0),
                                   lambda: os.ftruncate(descriptor, len(image) + 1)):
                        try:
                            action()
                        except OSError as error:
                            check(error.errno == errno.EPERM, "wrong seal refusal")
                        else:
                            check(False, "sealed mutation succeeded")
                        check((image.digest(), image[:64]) == before, "sealed replay changed body")
                elif probe == "resets":
                    charged = dict(budget.bytes)
                    budget.limits = Limits(snapshot_bytes=len(image))
                    for _ in range(2):
                        try:
                            image.materialize(target)
                        except MakeProbeError:
                            pass
                        else:
                            check(False, "exhausted owner budget resumed")
                    check(budget.failed and budget.bytes["snapshot"] == charged["snapshot"]
                          and all(budget.bytes[key] >= value for key, value in charged.items())
                          and not target.exists(),
                          "failure reset charges or opened destination")
                elif probe == "terminals":
                    descriptor = image.descriptor
                    cache = {"owned": image, "bytes": body}
                    close_images(cache)
                    close_images(cache)
                    image.close()
                    check(cache == {} and image.descriptor == -1, "terminal retained ownership")
                    try:
                        os.fstat(descriptor)
                    except OSError as error:
                        import errno
                        check(error.errno == errno.EBADF, "wrong descriptor retirement")
                    else:
                        check(False, "owned descriptor remains open")
                    for call in (image.digest, lambda: image[:1], lambda: image.materialize(target)):
                        try:
                            call()
                        except MakeProbeError:
                            pass
                        else:
                            check(False, "closed owned body remained usable")
                    check(not target.exists(), "closed body opened a destination")
                else:
                    raise review.ReviewError("unknown platform API probe")
            checks = 3
        finally:
            if image is not None:
                image.close()
            budget.close()
    return {"kind": "host", "checks": checks,
            "detail": probe + ": actual capture/materialize/ownership inputs executed"}


def _platform_parsers():
    """Execute only the existing finite parser functions, not their suite imports."""
    names = {"_job_blocks", "_direct_job_if", "_run_block_commands",
             "_step_blocks", "_direct_step_mapping_fields"}
    parsed = ast.parse(platform_parser_path(PLATFORM_TOPOLOGY).read_bytes())
    functions = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
                 and node.name in names]
    review.require({node.name for node in functions} == names, "missing owner parsers")
    namespace = {"re": re, "ast": ast}
    exec(compile(ast.Module(body=functions, type_ignores=[]), PLATFORM_TOPOLOGY, "exec"), namespace)
    parsed = ast.parse(platform_parser_path(PLATFORM_CONDITIONS).read_bytes())
    predicates = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
                  and node.name == "workflow_condition"]
    classes = [node for node in parsed.body if isinstance(node, ast.ClassDef)
               and node.name == "WorkflowTests"]
    review.require(len(predicates) == len(classes) == 1, "missing owner condition parser")
    contexts = [node for node in classes[0].body if isinstance(node, ast.FunctionDef)
                and node.name == "context"]
    review.require(len(contexts) == 1, "missing finite owner contexts")
    exec(compile(ast.Module(body=[*predicates, *contexts], type_ignores=[]),
                 PLATFORM_CONDITIONS, "exec"), namespace)
    return types.SimpleNamespace(**namespace)

def platform_owner_recipe(source):
    targets = {"ownership-probe-check", "ownership-probe-test"}
    recipes, phony, current = {}, None, None
    lines = []
    for line in source.split("\n"):
        line = line.removesuffix("\r")
        if lines and lines[-1].endswith("\\"):
            previous = lines.pop()
            review.require(previous.startswith("\t") and not previous.endswith("\\\\"),
                           "owner continuation requires one literal recipe escape")
            review.require("'" not in previous and '"' not in previous,
                           "quoted owner continuation requires a reviewed model")
            lines.append(previous[:-1] + line.removeprefix("\t"))
        else:
            lines.append(line)
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        review.require("$" not in line, "owner Make expansion requires a reviewed model")
        if line.startswith("\t"):
            review.require(current is not None and current not in recipes,
                           "owner recipe is missing, duplicated or multiline")
            words = shlex.split(line.strip())
            review.require(words and not any(char in line for char in ";|&<>`"),
                           "owner recipe is not a literal command")
            recipes[current] = words
        elif line.startswith(".PHONY:"):
            review.require(phony is None, "duplicate owner PHONY declaration")
            phony = line.removeprefix(".PHONY:").split()
            review.require(len(phony) == len(targets) and set(phony) == targets,
                           "owner PHONY target set changed")
            current = None
        else:
            match = re.fullmatch(r"(ownership-probe-check|ownership-probe-test)\s*:\s*", line)
            review.require(match is not None and match[1] not in recipes,
                           "owner Make requires literal standalone targets")
            review.require(current is None or current in recipes, "owner target lacks a recipe")
            current = match[1]
    review.require(phony is not None and set(recipes) == targets,
                   "owner Make target or recipe is incomplete")
    review.require(recipes["ownership-probe-check"] == [
        "/usr/bin/python3", "-I", "-S", "-B", "scripts/validation_ownership/isolated_launcher.py",
    ], "owner check recipe changed")
    return recipes["ownership-probe-test"]


@probe_result("parsed")
def _platform_owner(role):
    parsed = ast.parse(platform_inventory_path(PLATFORM_OWNER).read_bytes())
    declarations = [node for node in parsed.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "PROBE_TEST_MODULES"
                            for target in node.targets)]
    review.require(len(declarations) == 1, "unknown owner inventory")
    expected = ast.literal_eval(declarations[0].value)
    review.require(isinstance(expected, tuple) and all(isinstance(name, str) for name in expected),
                   "nonfinite owner inventory")
    argv = platform_owner_recipe(Path(PLATFORM_MAKE).read_text())
    check(argv[:3] == ["python3", "-m", "unittest"] and argv[-1:] == ["-v"],
          "generated Make test command changed")
    selected = argv[3:-1]
    check(len(selected) == len(set(selected)), "duplicate generated test modules")
    provider_module = PLATFORM_TEST.removesuffix(".py").replace("/", ".")
    inventories = {}
    for name in sorted(set(selected) | set(expected)):
        review.require(name.startswith("scripts.validation_ownership.tests.test_")
                       and all(part.isidentifier() for part in name.split(".")),
                       "unknown native test selector")
        path = name.replace(".", "/") + ".py"
        content = (Path(path) if path == PLATFORM_TEST else platform_inventory_path(path)).read_bytes()
        source = ast.parse(content)
        symbols = symtable.symtable(content, path, "exec")
        hooks = [symbols.lookup(name) for name in ("load_tests", "__getattr__", "__dir__")
                 if name in symbols.get_identifiers()]
        review.require(
            not any(hook.is_assigned() or hook.is_imported() or hook.is_namespace()
                    for hook in hooks)
            and not any(isinstance(node, ast.ImportFrom)
                        and any(alias.name == "*" for alias in node.names)
                        for node in ast.walk(source)),
            "custom selection needs a reviewed model",
        )
        inventories[name] = tuple(
            name + "." + node.name + "." + method.name
            for node in source.body if isinstance(node, ast.ClassDef)
            and any(isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                    and base.value.id == "unittest" and base.attr == "TestCase"
                    for base in node.bases)
            for method in node.body if isinstance(method, ast.FunctionDef)
            and method.name.startswith("test_")
        )
        check(bool(inventories[name]), "parsed owner module has no finite native cases")
    if role in {"owners", "drift-checks"}:
        check(len(expected) == len(set(expected)) and sorted(selected) == sorted(expected),
              "parsed Make selected-versus-expected owner inventory differs")
        check(sorted(case for name in selected for case in inventories[name])
              == sorted(case for name in expected for case in inventories[name]),
              "parsed Make selected-versus-expected case inventory differs")
        check(provider_module in expected, "provider absent from owner inventory")
    elif role == "outputs":
        check(provider_module in selected, "generated Make output omits provider")
        module = importlib.import_module(provider_module)
        suite = unittest.TestLoader().loadTestsFromModule(module)

        def identifiers(items):
            for item in items:
                if isinstance(item, unittest.TestSuite):
                    yield from identifiers(item)
                else:
                    yield item.id()

        check(sorted(identifiers(suite)) == sorted(inventories[provider_module]),
              "actual provider selection differs from parsed generated inventory")
    elif role == "consumers":
        parsers = _platform_parsers()
        jobs = parsers._job_blocks(Path(PLATFORM_WORKFLOW).read_text())
        owners = []
        for name, job in jobs.items():
            for step in parsers._step_blocks(job):
                for run in parsers._run_block_commands(step):
                    if "ownership-probe-test" in run:
                        check(shlex.split(run) == [
                            "make", "-f", PLATFORM_MAKE, "ownership-probe-test",
                        ], "workflow changed generated consumer command")
                        fields = parsers._direct_step_mapping_fields(step)
                        check(fields is not None and len(fields) == len(set(fields))
                              and {"name", "run"} <= set(fields) <= {"name", "run", "id"},
                              "workflow test consumer is conditional, duplicated or ignorable")
                        owners.append(name)
        check(owners == ["extended-host-tests"], "generated provider has missing/duplicate owner")
        condition = parsers._direct_job_if(jobs[owners[0]])
        for event in ("pull_request", "push", "workflow_dispatch"):
            for mode in ("full", "metadata-only", "review-first"):
                context = parsers.context(None, event, mode)
                check(parsers.workflow_condition(condition, context) == (mode == "full"),
                      "full/disabled owner resource selection differs")
    else:
        raise review.ReviewError("unknown platform owner role")
    return {"kind": "parsed", "checks": 2,
            "detail": role + ": actual Make argv/source inventory/parsed workflow selection"}


def run_probe(probe: str) -> dict:
    allowed = {
        *(f"aoe-phase:{phase}" for phase in PHASES),
        *(f"aoe-shape:{shape}" for shape in SHAPES),
        "aoe-items", "aoe-targets", "aoe-slots", "aoe-execution",
        "aoe-reference:enabled", "aoe-reference:disabled", "aoe-arm:enabled", "aoe-arm:disabled",
        "generated-output", "generated-consumer", "generated-drift",
        *(f"{family}:{role}" for family in ("lifecycle", "wire") for role in review.FAMILIES[family]),
        *(f"platform:{name}" for name in (
            "admission", "identity", "workspace", "capture", "materialize", "entries",
            "preservation", "sealed", "resets", "terminals", "bytes",
        )),
        *(f"platform-owner:{role}" for role in review.FAMILIES["generated"]),
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
    if probe.startswith(("lifecycle:", "wire:")):
        return _session_probe(probe)
    if probe.startswith("platform-owner:"):
        return _platform_owner(probe.partition(":")[2])
    if probe.startswith("platform:"):
        name = probe.partition(":")[2]
        return (_platform_coverage(name) if name in {"admission", "identity", "workspace"}
                else _platform(name))
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
