"""One genuine bounded original-root stage, not a public/full graph report."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from . import policy
else:
    import policy


def candidate_api():
    from scripts.validation_ownership import graph_probe, phase_census, reporter
    from scripts.validation_ownership.authority import AuthorityLoader, GitTreeEntries
    from scripts.validation_ownership.graph_commands import ROOT_RUNTIME_FILES
    from scripts.validation_ownership.graph_report import capture
    from scripts.validation_ownership.make_probe import ProbeSession
    from scripts.validation_ownership.tests import test_foundation as foundation

    return SimpleNamespace(
        graph=graph_probe, phases=phase_census, reporter=reporter, capture=capture,
        loader=AuthorityLoader, entries=GitTreeEntries, session=ProbeSession,
        runtime_files=ROOT_RUNTIME_FILES, fixture=foundation.FoundationTests, root=foundation.ROOT,
    )


def fixture_makefile(modern, makefile, logical_chunks):
    chunks = list(logical_chunks(modern))
    def logical(prefix):
        matches = [chunk.text for chunk in chunks if chunk.text.startswith(prefix)]
        if len(matches) != 1:
            raise policy.GuardError("original fixture source no longer has its unique construction")
        return matches[0]
    toolchain = modern[modern.index("expansion-modern-toolchain-check:\n"):
                       modern.index("\nexpansion-modern-cohort:")]
    header = modern[modern.index("$(MODERN_ALL_C_HEADER_DEPS): $(MODERN_OUTPUT_DIR)/%.headers.d: %.c\n"):
                    modern.index("\nexpansion-modern-clean:")]
    lines = makefile.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("src/msg_data.c:")]
    if len(starts) != 1:
        raise policy.GuardError("original fixture lost its unique genuine text rule")
    text_rule = "\n".join(lines[starts[0]:starts[0] + 2]) + "\n"
    return (
        ".DEFAULT_GOAL := expansion-modern-all\n"
        "PYTHON := python3\nTEXT_TOOLS := scripts/texttools\nTEXT_DIR := texts\n"
        "TEXT_PROCESS := $(PYTHON) $(TEXT_TOOLS)/textprocess.py\n"
        "TEXT_MAIN := $(TEXT_DIR)/texts.txt\nTEXT_DEFS := $(TEXT_DIR)/textdefs.txt\n"
        "TEXT_SRC := $(TEXT_MAIN)\nTEXT_HEADER := include/constants/msg.h\n"
        "MODERN_OUTPUT_DIR := build/modern/release/aapcs\nMODERN_CC := arm-none-eabi-gcc\n"
        "MODERN_CONFIG := release\nMODERN_ABI := aapcs\n"
        "MODERN_BINUTILS_FLAG :=\nMODERN_DRIVER_FLAGS :=\n"
        "MODERN_ARCH_FLAGS := -mcpu=arm7tdmi -mthumb -mthumb-interwork\n"
        "MODERN_LANGUAGE_FLAGS := -std=gnu11\nMODERN_ABI_FLAGS := -mabi=aapcs\n"
        "MODERN_DEFINE_FLAGS := -DMODERN=1 -DNONMATCHING=1 -DBUGFIX=1\n"
        "MODERN_INCLUDE_FLAGS := -isystem /usr/include/newlib -Iinclude -I.\n"
        "MODERN_CFLAGS := $(MODERN_ARCH_FLAGS) $(MODERN_LANGUAGE_FLAGS) -ffreestanding "
        "$(MODERN_DEFINE_FLAGS) $(MODERN_INCLUDE_FLAGS)\n"
        "MODERN_GENERATED_HEADER_BASENAME_RE := missing\\.h\n"
        + logical("MODERN_ALL_C_SOURCES ?=") + "\n"
        + logical("ifeq (,$(findstring src/msg_data.c,") + "\n"
        + logical("MODERN_ALL_C_SOURCES += src/msg_data.c") + "\nendif\n"
        + logical("MODERN_ALL_C_OBJECTS := $(addprefix") + "\n"
        + logical("MODERN_ALL_OBJECTS :=") + "\n"
        + logical("MODERN_ALL_C_HEADER_DEPS :=") + "\n"
        + logical("MODERN_ALL_SOURCE_GOALS :=") + "\n"
        + logical("expansion-modern-all: expansion-modern-toolchain-check") + "\n"
        "\t@printf 'Built %s modern relocatable objects in %s\\n' '$(words $(MODERN_ALL_OBJECTS))' '$(MODERN_OUTPUT_DIR)'\n"
        "expansion-modern-bgm-registry-check:\n\t@:\n"
        ".PHONY: expansion-modern-toolchain-check expansion-modern-bgm-registry-check\n"
        + toolchain + "\n"
        + logical("$(MODERN_ALL_C_HEADER_DEPS): |") + "\n"
        + logical("$(MODERN_OUTPUT_DIR)/%.o: %.c") + "\n"
        "\t@mkdir -p \"$(@D)\"\n"
        "\t\"$(MODERN_CC)\" $(MODERN_CFLAGS) -MMD -MP -MF \"$(@:.o=.d)\" -MQ \"$@\" -c \"$<\" -o \"$@\"\n"
        + text_rule + header
    )


def populate_fixture(root, fixture, logical_chunks):
    fixture.add("Makefile", fixture_makefile(
        (root / "modern.mk").read_text(), (root / "Makefile").read_text(), logical_chunks,
    ))
    fixture.add("src/.keep", "original empty C namespace\n")
    for name in ("scripts/texttools/textprocess.py", "scripts/texttools/huffman.py", "texts/texts.txt", "texts/textdefs.txt"):
        fixture.add(name, (root / name).read_bytes())
    for path in sorted((root / "include").rglob("*.h")):
        fixture.add(path.relative_to(root).as_posix(), path.read_bytes())
    return require_initial_absence(fixture.root)


def require_initial_absence(root):
    for path in (root / "src/msg_data.c", root / "build/modern"):
        if path.exists() or path.is_symlink():
            raise policy.GuardError("original root fixture has precreated C or public parents")
    return {"generated_c": True, "public_parents": True}


def selected_policies(api, root, budget):
    loader = api.capture(root, policy.GRAPH, budget)
    metadata = api.reporter._selected_make_data(loader, None, required=True)
    domains = api.reporter.load_make_prerequisite_domains(loader, required=True, _metadata=metadata)
    ambient = api.reporter.load_make_ambient_contracts(loader, required=True, _metadata=metadata)
    trusted, scoped, escaped = api.reporter.load_make_typed_variable_contracts(loader, required=True, _metadata=metadata)
    names = {"MODERN_ALL_C_SOURCES"}
    if (
        not names <= set(metadata.data["ambient_inputs"]["allowed_names"])
        or not names <= set(metadata.data["prerequisite_domains"]["tracked_fallback_names"])
        or domains.get("MODERN_ALL_C_SOURCES") != {"name": "MODERN_ALL_C_SOURCES", "kind": "tracked-fallback"}
    ):
        raise policy.GuardError("original root's declared tracked-fallback policy changed")
    return {name: {"kind": domains[name]["kind"]} for name in names}, metadata.contracts, {
        "source_phases": True, "declared_external_names": names, "environment_names": names,
        "scoped_variable_names": scoped, "trusted_builtin_names": trusted,
        "ambient_undefined_names": {name for name, value in ambient.items() if value["category"] == "undefined"},
        "escaped_literal_names": escaped,
    }


def toolchain_summary(command):
    if command.get("returncode") != 0:
        raise policy.GuardError("failed original toolchain check cannot supply root acceptance")
    observations = command.get("runtime_probes", ())
    result = {
        "executed": command.get("executed"),
        "stages": [],
        "stdin": [],
        "repository_inputs": len(command.get("inputs", ())),
        "sdk_inputs": len(command.get("runtime_inputs", ())),
    }
    for stage in ("version", "target", "assembler", "syntax", "compile"):
        events = [row for row in observations if row.get("stage") == stage and "sequence" in row]
        events.sort(key=lambda row: row["sequence"])
        if [row["sequence"] for row in events] != list(range(1, len(events) + 1)):
            raise policy.GuardError("toolchain native sequence is incomplete")
        result["stages"].append({"stage": stage, "executed": [row["path"] for row in events]})
    for stage, expected in (
        ("syntax", '#include "global.h"\n'),
        ("compile", "void modern_arm7tdmi_thumb_probe(void) {}\n"),
    ):
        events = [row for row in observations if row.get("stage") == stage and "stdin" in row]
        if len(events) != 1 or events[0]["stdin"] != expected or events[0]["eof"] is not True:
            raise policy.GuardError("original toolchain stdin observation differs from the actual contract")
        result["stdin"].append({"stage": stage, "bytes": len(events[0]["stdin"].encode()), "eof": True})
    policy.validate_root_toolchain(result)
    return result


class Recorder:
    """Bind summaries to real returned objects, never registration intentions."""

    def __init__(self, session, budget):
        self.session, self.budget = session, budget
        self.pending = {}
        self.completed = []
        self.attempted = False

    def input_state(self):
        result = {}
        for name in self.session.snapshot.files:
            info = (self.session.tree / name).stat(follow_symlinks=False)
            result[name] = (
                info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns, info.st_nlink,
            )
        self.budget.charge("control", len(policy.encoded(result)))
        return result

    def observe(self, observation, target, keywords, inputs):
        if not keywords.get("observe_source_journal"):
            return
        if (
            target != policy.ROOT_TARGET or self.session.budget is not self.budget
            or id(observation) in self.pending
            or keywords.get("makefile") != "Makefile"
            or keywords.get("source_journal_mode") != "prewatched-directories"
        ):
            raise policy.GuardError("root observation escaped its original selected context")
        state = tuple(keywords.get("assignments", ()))
        self.session._original_namespace(observation, target=target, makefile="Makefile", assignments=state)
        archive = self.session._original_source_archive(observation)
        images = self.session._source_phase_images(observation)
        if (
            len(images) != len(archive.passes) or not images
            or observation.source_journal is None or observation.source_journal.get("closed") is not True
            or observation.source_journal.get("scope") != archive.scope
        ):
            raise policy.GuardError("root lacks genuine closed native source evidence")
        jobs = {"text": 0, "toolchain": 0, "header": 0}
        for row in observation.semantics["native_dispatches"]:
            name = row["job"]["target"]
            for key, wanted in (("text", "src/msg_data.c"), ("toolchain", "expansion-modern-toolchain-check"),
                                ("header", policy.ROOT_HEADER)):
                if name == wanted:
                    jobs[key] += 1
        toolchains = [
            toolchain_summary(row["command"]) for row in observation.semantics["dynamic_commands"]
            if row["command"].get("toolchain_check") is True
        ]
        if inputs is None or self.input_state() != inputs:
            raise policy.GuardError("original source input metadata changed during the native root observation")
        for name, expected in self.session.snapshot.files.items():
            if self.budget.read_bytes(self.session.tree / name, "control") != expected:
                raise policy.GuardError("original source input changed during the native root observation")
        summary = {
            "state": [list(row) for row in state], "passes": len(archive.passes),
            "source_journal_closed": True, "native_jobs": jobs, "toolchain": toolchains,
            "generated": sorted(
                ({"path": item.path, "mode": item.mode, "size": len(item.data)} for item in observation.generated),
                key=lambda row: row["path"],
            ),
            "inputs_unchanged": True,
        }
        self.pending[id(observation)] = observation, state, summary

    def analyzed(self, session, observation, target, state, result):
        current = self.pending.pop(id(observation), None)
        if (
            session is not self.session or session.budget is not self.budget
            or target != policy.ROOT_TARGET or current is None or current[0] is not observation
            or current[1] != tuple(state) or not isinstance(result, tuple) or len(result) != 4
        ):
            raise policy.GuardError("source analysis is foreign, repeated or not paired with native completion")
        usage, sources, streams, parts = result
        if not isinstance(usage, dict) or len(streams) != len(parts) or len(parts) != current[2]["passes"]:
            raise policy.GuardError("source analysis omitted an actual original pass")
        current[2].update(analyzed_passes=len(parts), source_files=sorted(sources))
        self.completed.append(current[2])

    @contextmanager
    def intercept(self, phases):
        session = self.session
        original_make = session.make
        original_analyze = phases.analyze
        def make(target, **keywords):
            inputs = self.input_state() if keywords.get("observe_source_journal") else None
            result = original_make(target, **keywords)
            self.observe(result, target, keywords, inputs)
            return result
        def analyze(owner, observation, target, state, commands, **keywords):
            result = original_analyze(owner, observation, target, state, commands, **keywords)
            self.analyzed(owner, observation, target, state, result)
            return result
        session.make, phases.analyze = make, analyze
        try:
            yield
        finally:
            del session.make
            phases.analyze = original_analyze

    def invoke(self, api, loader, domains, contracts, options):
        if self.attempted or self.session.budget is not self.budget or options.get("source_phases") is not True:
            raise policy.GuardError("original root invocation is repeated or lacks its exact source mode/budget")
        self.attempted = True
        with self.intercept(api.phases):
            result = api.graph.run_probe(
                loader, {policy.ROOT_TARGET}, domains, contracts, session=self.session, **options,
            )
        if not isinstance(result, dict) or set(result) != {policy.ROOT_TARGET} or self.pending or not self.completed:
            raise policy.GuardError("original root returned without complete matching native/phase results")
        record = result[policy.ROOT_TARGET]
        return {
            "states": [variant["state"] for variant in record["record"]["variants"]],
            "used_domains": record["prerequisite_domain_census"]["used"],
            "enumerated_domains": record["prerequisite_domain_census"]["enumerated"],
        }


def cleanup_state(session, budget, fixture):
    return {
        "budget_closed": budget.closed, "children": len(budget.children),
        "waiters": len(budget.producer_waiters),
        "retained_owners": len([owner for owner in session._file_owners.values() if owner.retained]),
        "session_base_removed": session.base is None,
        "fixture_removed": not fixture.directory.exists(),
    }


def run(root, budget, config):
    if __package__:
        from .worker import require_contained
    else:
        from worker import require_contained
    require_contained(config)
    if root != Path("/repo") or config["mode"] != "root" or budget.deadline != config["deadline"] or budget.closed:
        raise policy.GuardError("root stage lacks its exact contained source and injected clock")
    api = candidate_api()
    if api.root != root:
        raise policy.GuardError("fixture helpers were not imported from the pinned candidate")
    domains, contracts, options = selected_policies(api, root, budget)
    fixture = api.fixture()
    fixture.setUp()
    session = None
    primary = None
    try:
        absence = populate_fixture(root, fixture, api.graph._make_logical_chunks)
        loader = api.loader(fixture.root, api.entries(fixture.entries, budget=budget), budget=budget)
        session = api.session(
            loader, scratch_root=fixture.scratch, budget=budget, runtime_files=api.runtime_files,
        )
        with session:
            require_initial_absence(session.tree)
            recorder = Recorder(session, budget)
            planner = recorder.invoke(api, loader, domains, contracts, options)
        state = cleanup_state(session, budget, fixture)
        if (
            not state["budget_closed"] or state["children"] or state["waiters"]
            or state["retained_owners"] or not state["session_base_removed"]
        ):
            raise policy.GuardError("native root completed with unclosed or retained inner ownership")
    except BaseException as error:
        primary = error
        raise
    finally:
        safe = session is None or (
            session.base is None and not budget.children and not budget.producer_waiters
            and not any(owner.retained for owner in session._file_owners.values())
        )
        if safe:
            try:
                fixture.tearDown()
            except BaseException as error:
                if primary is None:
                    raise
                primary.add_note("owned fixture cleanup failed: " + str(error))
        elif primary is not None:
            primary.add_note("uncertain root fixture retained; outer containment is not inner ownership cleanup")
        if primary is not None:
            primary.root_cleanup_state = (
                cleanup_state(session, budget, fixture) if session is not None else {
                    "budget_closed": budget.closed, "children": len(budget.children),
                    "waiters": len(budget.producer_waiters), "retained_owners": 0,
                    "session_base_removed": True, "fixture_removed": not fixture.directory.exists(),
                }
            )
    result = {
        "version": 1, "workload_kind": policy.WORKLOAD_KIND, "fixture_version": policy.FIXTURE_VERSION,
        "source_revision": policy.GRAPH, "base_revision": policy.BASE, "target": policy.ROOT_TARGET,
        "source_phases": True, "root_check_attempts": 1, "root_check_completed": True,
        "graph_check_attempts": 0, "complete_repository_report": False, "initial_absence": absence,
        "observations": recorder.completed, "planner": planner, "cleanup": cleanup_state(session, budget, fixture),
    }
    policy.validate_root_result(result)
    return result
