"""One selected component method with its original assertions and shared budget."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from . import policy
else:
    import policy


def candidate_api():
    from scripts.validation_ownership.authority import AuthorityLoader, GitTreeEntries
    from scripts.validation_ownership.budget import ProbeBudget
    from scripts.validation_ownership.graph_commands import MakeCommands, ROOT_RUNTIME_FILES
    from scripts.validation_ownership.make_probe import ProbeSession
    from scripts.validation_ownership.tests import test_toolchain_runtime as selected

    case = selected.ModernToolchainTests
    return SimpleNamespace(
        case=case, method=getattr(case, policy.COMPONENT_METHOD), root=selected.foundation.ROOT,
        loader=AuthorityLoader, entries=GitTreeEntries, session=ProbeSession, budget_type=ProbeBudget,
        runtime_files=ROOT_RUNTIME_FILES, commands=MakeCommands,
    )


def cleanup_state(session, budget, fixture, *, setup_completed):
    return {
        "budget_closed": budget.closed, "children": len(budget.children),
        "waiters": len(budget.producer_waiters),
        "retained_owners": None if session is None else len([
            owner for owner in session._file_owners.values() if owner.retained
        ]),
        "session_base_removed": None if session is None else session.base is None,
        "fixture_removed": None if not setup_completed else not fixture.directory.exists(),
    }


def summarize(observation, results, attempts, returned):
    receipts = observation.toolchain_receipts
    if type(receipts) not in (tuple, list) or len(receipts) != 2 or len(results) != 2 or any(
        type(data) is not bytes or len(data) > policy.ORIGINAL_LIMITS["file_bytes"] for data in receipts
    ):
        raise policy.GuardError("component lacks its two bounded returned recipe receipts")
    records = [policy.parse_json(data) for data in receipts]
    stages = [record["stages"] for record in records]
    if any(type(items) is not list or len(items) != 5 for items in stages):
        raise policy.GuardError("component checker stage extent changed")
    proofs = [items[-1]["intermediate"] for items in stages]
    commands = [
        row for row in observation.semantics["dynamic_commands"]
        if row["command"].get("toolchain_check") is True
    ]
    dispatches = [
        row for row in observation.semantics["native_dispatches"]
        if row["job"]["target"] == "expansion-modern-toolchain-check"
    ]
    if len(commands) != 1 or len(dispatches) != 2:
        raise policy.GuardError("component has incomplete native/semantic checker observations")
    references = [
        value for row in commands[0]["command"]["runtime_probes"] if "argv" in row
        for value in row["argv"] if type(value) is dict
    ]
    expected = {"kind": "toolchain-intermediate-ref", "version": 1, "role": "stage4-assembly"}
    if references != [expected, expected] or [
        record["native_dispatch_sequence"] for record in records
    ] != [row["sequence"] for row in dispatches]:
        raise policy.GuardError("component receipt sequence or typed reference differs from native evidence")
    bindings = [
        (items[-1]["launch_scope"], items[-1]["launch_binding"], items[-1]["workspace"])
        for items in stages
    ]
    contents = [(proof["writer"]["completed"]["extent"], proof["writer"]["completed"]["sha256"])
                for proof in proofs]
    result = {
        "make_attempts": attempts, "make_returned": returned, "checker_occurrences": len(results),
        "recipe_receipts": len(receipts),
        "dispatch_sequences": [record["native_dispatch_sequence"] for record in records],
        "producer_slots": [record["producer_slot"] for record in records],
        "stage_names": [[stage["stage"] for stage in items] for items in stages],
        "intermediate_versions": [proof["version"] for proof in proofs],
        "intermediate_complete": [proof["complete"] for proof in proofs],
        "retirement_absent": [proof["retirement"]["path_absent"] for proof in proofs],
        "retired_nlinks": [proof["retirement"]["after_identity"][6] for proof in proofs],
        "assembly_extents": [content[0] for content in contents],
        "content_equal": contents[0] == contents[1], "bindings_distinct": bindings[0] != bindings[1],
        "raw_receipts_distinct": receipts[0] != receipts[1],
        "semantic_records": len(commands), "typed_references": len(references),
    }
    policy.validate_component_observation(result)
    return result


class Recorder:
    def __init__(self, api, budget, sampler):
        self.api, self.budget, self.sampler = api, budget, sampler
        self.session = None
        self.session_valid = False
        self.session_attempts = self.capture_attempts = self.make_attempts = self.make_returned = 0
        self.observation = self.summary = None
        self.method_started = self.method_returned = False

    def session_for(self, case):
        if self.session_attempts or not self.method_started:
            raise policy.GuardError("component permits one method-owned session")
        self.session_attempts += 1
        fixture = case.fixture
        entries = self.api.entries(fixture.entries, budget=self.budget)
        loader = self.api.loader(fixture.root, entries, budget=self.budget)
        self.session = self.api.session(
            loader, scratch_root=fixture.scratch, budget=self.budget, runtime_files=self.api.runtime_files,
        )
        if (
            type(self.session) is not self.api.session or self.session.budget is not self.budget
            or self.session.loader is not loader or loader.budget is not self.budget
            or loader.entries.budget is not self.budget
        ):
            raise policy.GuardError("component session/loader/entries lost the exact issued budget")
        self.session_valid = True
        self.sampler.session = self.session
        return self.session

    @contextmanager
    def watch_make(self, session):
        if session is not self.session or session.budget is not self.budget or "make" in vars(session):
            raise policy.GuardError("component cannot observe a foreign or already overridden session")
        original = session.make

        def make(target, **keywords):
            if (
                self.make_attempts or target != policy.COMPONENT_TARGET
                or keywords.keys() != {"commands"} or type(keywords["commands"]) is not self.api.commands
                or keywords["commands"].session is not session
            ):
                raise policy.GuardError("component permits only its original single Make invocation")
            self.make_attempts += 1
            value = original(target, **keywords)
            self.make_returned += 1
            self.observation = value
            return value

        session.make = make
        try:
            yield
        finally:
            del session.make

    def capture_for(self, case, session):
        if self.capture_attempts or not self.method_started or session is not self.session:
            raise policy.GuardError("component capture is repeated or not method-owned")
        self.capture_attempts += 1
        with self.watch_make(session):
            returned = self.api.case.capture(case, session)
        if (
            type(returned) is not tuple or len(returned) != 2 or returned[0] is not self.observation
            or type(case.results) is not tuple or len(case.results) != 2
            or returned[1] is not case.results[0]
        ):
            raise policy.GuardError("component capture differs from its actual source return")
        self.summary = summarize(self.observation, case.results, self.make_attempts, self.make_returned)
        return returned

    def case(self):
        recorder = self

        class ContainedComponent(self.api.case):
            def session(self):
                return recorder.session_for(self)

            def capture(self, session):
                return recorder.capture_for(self, session)

        if getattr(ContainedComponent, policy.COMPONENT_METHOD) is not self.api.method:
            raise policy.GuardError("component replaced the selected original test method")
        return ContainedComponent(policy.COMPONENT_METHOD)

    def invoke(self, case):
        if self.method_started or getattr(type(case), policy.COMPONENT_METHOD) is not self.api.method:
            raise policy.GuardError("component method is repeated or foreign")
        self.method_started = True
        self.api.method(case)
        self.method_returned = True
        if (
            self.session_attempts != 1 or self.capture_attempts != 1
            or self.make_attempts != 1 or self.make_returned != 1 or self.summary is None
        ):
            raise policy.GuardError("source method returned without the complete original component")


def fixture_state(case):
    fixture = case.fixture
    source = fixture.root / "src/query.c"
    parents = fixture.root / "build/native"
    query = source.read_bytes() == b'#include "global.h"\n'
    count = sum(name.startswith("include/") and name.endswith(".h") for name in fixture.entries)
    absent = not parents.exists() and not parents.is_symlink()
    makefile = (fixture.root / "Makefile").read_text()
    empty = makefile.endswith("\nexpansion-modern-all: ;\n")
    no_text = "TEXT_PROCESS" not in makefile and "src/msg_data.c" not in fixture.entries
    if not query or not count or not absent or not empty or not no_text:
        raise policy.GuardError("selected original component fixture changed")
    return {
        "preexisting_query": query, "genuine_headers": count, "header_parents_absent": absent,
        "text_producer": not no_text, "empty_final_target": empty,
    }


def run(root, budget, config, sampler):
    if __package__:
        from .worker import require_contained
    else:
        from worker import require_contained
    require_contained(config)
    if root != Path("/repo") or config["mode"] != "component" or budget.deadline != config["deadline"] or budget.closed:
        raise policy.GuardError("component lacks its fixed contained source and original clock")
    api = candidate_api()
    if api.root != root or not isinstance(budget, api.budget_type):
        raise policy.GuardError("component source/budget origin differs from the selected candidate")
    recorder = Recorder(api, budget, sampler)
    case = recorder.case()
    primary = None
    setup_completed = False
    fixture = None
    try:
        sampler.phase = "component-setup"
        case.setUp()
        setup_completed = True
        fixture = fixture_state(case)
        sampler.phase = "component-method"
        recorder.invoke(case)
        policy.validate_component_counters(policy.counter_snapshot(budget, recorder.session), complete=True)
    except BaseException as error:
        primary = error
        raise
    finally:
        sampler.phase = "component-finalize"
        session = recorder.session
        known = getattr(case, "fixture", None)
        safe = setup_completed and (not recorder.session_attempts or recorder.session_valid) and (
            session is None and not budget.children and not budget.producer_waiters
            or session is not None and session.base is None and not budget.children and not budget.producer_waiters
            and not any(owner.retained for owner in session._file_owners.values())
        )
        cleanup_error = None
        if safe:
            try:
                case.tearDown()
            except BaseException as error:
                cleanup_error = error
        state = cleanup_state(
            session if recorder.session_valid else None, budget, known, setup_completed=setup_completed,
        )
        if primary is not None:
            primary.component_cleanup_state = state
            if cleanup_error is not None:
                primary.component_cleanup_error = policy.component_error_record(cleanup_error)
        elif cleanup_error is not None:
            cleanup_error.component_cleanup_state = state
            raise cleanup_error
        recorder.observation = None
        if hasattr(case, "results"):
            del case.results
    result = {
        "version": 1, "workload_kind": policy.WORKLOAD_KIND, "fixture_version": policy.FIXTURE_VERSION,
        "source_revision": policy.GRAPH, "base_revision": policy.BASE, "profile": policy.PROFILE,
        "method": policy.COMPONENT_CASE + "." + policy.COMPONENT_METHOD, "target": policy.COMPONENT_TARGET,
        "source_phases": False, "component_attempts": 1, "component_completed": recorder.method_returned,
        **policy.ABSENT_WORKLOADS, "fixture": fixture, "observation": recorder.summary,
        "counters": policy.counter_snapshot(budget, recorder.session), "cleanup": state,
    }
    policy.validate_component_result(result)
    return result
