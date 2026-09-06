"""Closed bindings to existing cases; no candidate-supplied programs or plugins."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import types

from scripts.workflow_pilot import review_family as review


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


def event_validation_inputs(tree, dependencies):
    shared = {
        "scripts/generated_data/" + name + ".py" for name in (
            "__init__", "cli", "registry", "schema", "diagnostics", "json_loader",
            "validators", "character_refs", "cparse", "cgen", "manifest", "idspace",
        )
    }
    for name in {"eventlists", *dependencies, "chapterobjectives", "supports"}:
        shared.update(path for path in tree.under("scripts/generated_data/" + name)
                      if path.endswith(".py"))
    shared.update({
        "scripts/assets/__init__.py", "scripts/assets/tmx.py", "assets/manifest.json",
        "src/data/chapter_settings.json", "src/data/data_8B363C.c",
        "include/bmunit.h", "include/bmtrick.h",
    })
    shared.update("include/constants/" + name + ".h"
                  for name in ("characters", "classes", "items", "chapters", "event-flags"))
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


def enum_members(source: bytes, name: str, prefix: str) -> tuple[str, ...]:
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", source.decode(), flags=re.S)
    found = re.findall(r"\benum\s+" + re.escape(name) + r"\s*\{([^{}]*)\}", text)
    review.require(len(found) == 1, f"missing or ambiguous finite enum {name}")
    names = []
    for entry in found[0].split(","):
        entry = entry.strip()
        if not entry:
            continue
        parsed = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?:\s*=\s*(0x[0-9a-fA-F]+|\d+))?", entry)
        review.require(parsed is not None and parsed[1].startswith(prefix),
                       f"unsupported finite enum entry in {name}")
        names.append(parsed[1][len(prefix):])
    review.unique(names, "enum entries")
    return tuple(names)


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
            probe, inputs, *, profile="host", evidence=("positive", "adversarial")):
        result.append(review.Obligation(
            spec.key, family, role + ":" + name, role, producer, consumer,
            representation, revalidation, probe, profile, evidence, tuple(sorted(inputs))))

    if spec.model == "aoe":
        header = tree.read(AOE_HEADER)
        phases = enum_members(header, "ExpansionAoEItemPhase", "EXPANSION_AOE_ITEM_")
        shapes = enum_members(header, "ExpansionAoEShapeKind", "EXPANSION_AOE_SHAPE_")
        review.require(set(phases) == {*PHASES, "PHASE_COUNT"}
                       and set(shapes) == {*SHAPES, "COUNT"},
                       "added/deleted AoE enum member needs a reviewed probe")
        inputs = (AOE_CORE, AOE_HEADER)
        for phase in PHASES:
            add("action", "actions", phase, AOE_CORE + ":ExpansionAoE_DispatchItem",
                "ExpansionAoEItemHandler(context)", "ExpansionAoEItemContext.phase",
                "invalid phase and reentrant dispatch", "aoe-phase:" + phase, inputs)
        add("action", "items", "routes", AOE_CORE + ":ExpansionAoE_ValidateItemRouteTable",
            "ExpansionAoE_DispatchItem", "ExpansionAoEItemRouteTable",
            "key/item/policy/duplicate/capacity checks", "aoe-items", inputs)
        for shape in SHAPES:
            add("action", "targets", shape, AOE_CORE + ":ExpansionAoE_BuildTargetSet",
                "ExpansionAoE_Execute", "ExpansionAoEShape/ExpansionAoETargetSet",
                "invalid radius and incomplete target rejection",
                "aoe-shape:" + shape, inputs)
        for name, probe in (("capacity-filter", "aoe-targets"), ("stable-slots", "aoe-slots"),
                            ("execution", "aoe-execution")):
            add("action", "targets", name, AOE_CORE + ":ExpansionAoE_BuildTargetSet",
                "ExpansionAoE_Execute", "stable unit IDs and bounded target set",
                "capacity/invalid slots/hidden/stale placement", probe, inputs)
        resources = (*inputs, AOE_REFERENCE, "include/expansion_aoe_reference.h")
        for role in ("enabled", "disabled"):
            add("resource", role, "reference", AOE_REFERENCE + ":ExpansionAoEReference_Apply",
                "reference native driver", "FE8_EXPANSION_AOE_REFERENCE",
                "default-off API is inert", "aoe-reference:" + role, resources,
                profile=role, evidence=("positive", "adversarial") if role == "enabled"
                else ("default",))
            add("resource", role, "objects", AOE_REFERENCE, "AAPCS link and EWRAM",
                "ELF symbols/sections", "disabled callback/probe omission",
                "aoe-arm:" + role, resources, profile="aapcs-" + role,
                evidence=("compile", "default") if role == "disabled" else ("compile",))
    elif spec.model == "eventlists":
        defaults, dependencies = schema_declaration(tree.read(EVENT_SCHEMA))
        review.require(defaults.get("default_source") == EVENT_SOURCE,
                       "changed owner needs an explicit reviewed binding")
        validation_inputs = event_validation_inputs(tree, dependencies)
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
                "generated-owner:" + name, paths)
        hand = defaults["default_hand_source"]
        inventory = defaults["default_inventory_path"]
        add("generated", "outputs", "eventlists", EVENT_SCHEMA + ":generate_c",
            hand, defaults["default_output_name"], "generated C parses and round-trips",
            "generated-output", inputs | {hand}, evidence=("positive", "adversarial", "generated"))
        add("generated", "consumers", "eventlists", EVENT_SOURCE, hand,
            "typed chapter event group", "all declared dependency references resolve",
            "generated-consumer", inputs | {hand})
        add("generated", "drift-checks", "eventlists", EVENT_SCHEMA + ":build_inventory",
            inventory, "committed generated inventory", "regenerate and compare",
            "generated-drift", inputs | {inventory}, evidence=("positive", "generated"))
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
    result = subprocess.run(argv, input=stdin, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("tool execution unavailable: " + result.stderr.decode(errors="replace")[-2000:])
    return result.stdout


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
    result = subprocess.run(args, capture_output=True, timeout=20)
    review.require(result.returncode in (0, 1), "native execution unavailable")
    check(result.returncode == 0, result.stderr.decode(errors="replace")[-2000:])
    return {"kind": "native", "checks": 1, "detail": "selected native assertions satisfied"}


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
        sections = command([os.environ["MODERN_SIZE"], "-A", *objects]).decode()
        sizes = [int(item) for item in re.findall(r"^ewram_data\s+(\d+)", sections, re.M)]
        check(sum(sizes) <= 128, "AoE EWRAM budget exceeded")
        text = [int(item) for item in re.findall(r"^\.text\s+(\d+)", sections, re.M)]
        check(sum(text) <= 8 * 1024, "AoE text budget exceeded")
    return {"kind": "arm-object", "checks": len(objects),
            "detail": "AAPCS object symbols/sections checked; not target-ROM execution"}


def _generated(probe: str) -> dict:
    from scripts.generated_data import registry
    from scripts.generated_data import cli
    from scripts.generated_data.diagnostics import DiagnosticCollector, GeneratedDataError
    from scripts.generated_data.eventlists import parser

    name = probe.partition(":")[2] if probe.startswith("generated-owner:") else "eventlists"
    schema = registry.REGISTRY.resolve(name)
    try:
        event_schema = registry.REGISTRY.resolve("eventlists")
        records, diagnostics = cli._load_and_validate(schema, schema.default_source)
        if name in event_schema.optional_dependency_tables():
            _, event_diagnostics = cli._load_and_validate(event_schema, event_schema.default_source)
            diagnostics.extend(event_diagnostics.errors)
    except GeneratedDataError as error:
        raise ContractViolation(str(error)) from error
    check(not diagnostics.errors, "\n".join(str(item) for item in diagnostics.errors)[:2000])
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
    if probe.startswith(("lifecycle:", "wire:")):
        return _session_probe(probe)
    raise review.ReviewError("unregistered probe")


def worker(probes: list[str]) -> list[dict]:
    results = []
    for probe in probes:
        try:
            result = {**run_probe(probe), "verdict": "satisfied"}
        except ContractViolation as error:
            result = {"verdict": "contract-violation", "checks": 1, "detail": str(error)}
        except Exception as error:
            result = {"verdict": "unavailable", "checks": 0,
                      "detail": f"{type(error).__name__}: {error}"}
        results.append({"probe": probe, **result})
    return results
