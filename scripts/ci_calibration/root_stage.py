"""Observe one original public report, without replacing any authority method."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

if __package__:
    from . import observation_failure, policy
else:
    import observation_failure
    import policy


def candidate_api():
    from scripts.validation_ownership import graph_report, reporter
    from scripts.validation_ownership.authority import AuthorityLoader, GitTreeEntries
    from scripts.validation_ownership.budget import ProbeBudget
    from scripts.validation_ownership.make_probe import ProbeSession

    return SimpleNamespace(
        module=graph_report, check=graph_report.check, serializer=reporter.normalized_json,
        loader=AuthorityLoader, entries=GitTreeEntries, session=ProbeSession,
        budget_type=ProbeBudget, runtime_files=graph_report.ROOT_RUNTIME_FILES,
    )


def cleanup_state(session, budget):
    return {
        "budget_closed": budget.closed, "session_started": budget.session_started,
        "children": len(budget.children), "waiters": len(budget.producer_waiters),
        "retained_owners": None if session is None else sum(
            owner.retained for owner in session._file_owners.values()
        ),
        "session_base_removed": None if session is None else session.base is None,
        "active_views": None if session is None else len(session._views),
        "constructor_restored": None, "report_released": None, "serialization_released": None,
        "source_imports_restored": None, "source_imports_released": None,
    }


class ReportMeasurement:
    def __init__(self, root, budget, config, sampler, changes):
        self.root, self.budget, self.config, self.sampler = root, budget, config, sampler
        self.changes = changes
        self.binding = policy.validate_report_binding(config["report_binding"])
        self.states = {**dict.fromkeys(policy.REPORT_STATES, 0), "completed": False}
        self.api = self.session = None
        self.session_valid = False
        self.raw_report = self.raw_serialization = None
        self.summary = self.counters = self.cleanup = self.serialized_bytes = None
        self.first = None
        self.first_stage = None
        self.secondary = []
        self.observer = None
        self.import_failed = False

    def observe_imports(self, action, stage="import-observation"):
        try:
            action()
        except BaseException as error:
            self.import_failed = True
            self.secondary.append({"stage": stage, "error": policy.component_secondary_error(error)})

    def fail(self, stage, error):
        if stage not in policy.REPORT_ERROR_STAGES:
            raise policy.GuardError("unknown report failure stage")
        if self.first is None:
            self.first = error
            self.first_stage = stage
        elif error is not self.first:
            self.secondary.append({"stage": stage, "error": policy.component_secondary_error(error)})
        self.states["completed"] = False

    def construct(self, loader, *, scratch_root, budget, runtime_files):
        if self.states["session_attempts"] or self.states["check_attempts"] != 1:
            raise policy.GuardError("report attempted a repeated or unowned session")
        if (
            budget is not self.budget or type(loader) is not self.api.loader
            or loader.budget is not budget or type(loader.entries) is not self.api.entries
            or loader.entries.budget is not budget or loader.root != self.root
            or loader.revision != policy.GRAPH or loader.entries.capture != (self.root, policy.GRAPH)
            or scratch_root != self.root / "build/test-artifacts/validation-ownership"
            or runtime_files is not self.api.runtime_files
        ):
            raise policy.GuardError("report changed the original CURRENT loader, budget or runtime inventory")
        self.states["session_attempts"] += 1
        self.session = self.api.session(
            loader, scratch_root=scratch_root, budget=budget, runtime_files=runtime_files,
        )
        self.states["session_constructed"] += 1
        if (
            type(self.session) is not self.api.session or self.session.loader is not loader
            or self.session.budget is not budget or self.session.runtime_paths != runtime_files
        ):
            raise policy.GuardError("original report constructor returned a foreign session")
        self.session_valid = True
        self.sampler.session = self.session
        self.observe_imports(lambda: self.observer.bind_imports(self))
        return self.session

    def collect(self):
        try:
            self.cleanup = cleanup_state(self.session if self.session_valid else None, self.budget)
            if self.observer is not None:
                self.cleanup.update(self.observer.import_cleanup())
            policy.validate_report_cleanup(self.cleanup)
        except BaseException as error:
            self.cleanup = None
            self.fail("cleanup-observation", error)
        try:
            self.counters = policy.counter_snapshot(
                self.budget, self.session if self.session_valid else None,
            )
        except BaseException as error:
            self.counters = None
            self.fail("counter-observation", error)

    def withdraw(self, original):
        # Each withdrawal is independent; even an after-effect exception leaves
        # that observation unavailable and cannot erase the first failure.
        for stage, field, action in (
            ("constructor-reference", "constructor_restored",
             lambda: setattr(self.api.module, "ProbeSession", original)),
            ("report-reference", "report_released", lambda: setattr(self, "raw_report", None)),
            ("serialization-reference", "serialization_released",
             lambda: setattr(self, "raw_serialization", None)),
        ):
            try:
                action()
                if field == "constructor_restored" and self.api.module.ProbeSession is not original:
                    raise policy.GuardError("report constructor reference did not restore")
                if self.cleanup is not None:
                    self.cleanup[field] = True
            except BaseException as error:
                if self.cleanup is not None:
                    self.cleanup[field] = None
                self.fail(stage, error)

    def run(self):
        if __package__:
            from .worker import require_contained
        else:
            from worker import require_contained
        require_contained(self.config)
        if (
            self.root != Path("/repo") or self.config["mode"] != "report"
            or self.budget.deadline != self.config["deadline"] or self.budget.closed
            or self.budget.session_started or self.states["check_attempts"]
            or policy.changed_path_binding(self.changes) != self.binding["changed_paths"]
        ):
            raise policy.GuardError("report lacks its one unchanged budget, clock and immutable path set")
        self.api = candidate_api()
        original = self.api.module.ProbeSession
        if (
            original is not self.api.session or self.api.module.check is not self.api.check
            or not isinstance(self.budget, self.api.budget_type)
        ):
            raise policy.GuardError("report source API or original constructor is already replaced")
        local_observer = self.observer is None
        if local_observer:
            self.observer = observation_failure.Observer(self.api.session, self.budget)
        stage = "check"
        try:
            self.api.module.ProbeSession = self.construct
            self.sampler.phase = "public-report"
            self.states["check_attempts"] += 1
            try:
                self.observe_imports(lambda: self.observer.start_imports(self))
                self.raw_report = self.api.check(
                    self.root, budget=self.budget, revision=policy.GRAPH, base_revision=policy.BASE,
                    changed_paths=tuple(self.changes), lifecycle=True,
                )
            finally:
                self.observe_imports(self.observer.restore_imports)
            self.states["check_returned"] += 1
            stage = "serialization"
            self.sampler.phase = "report-serialization"
            self.states["serialization_attempts"] += 1
            self.raw_serialization = self.api.serializer(self.raw_report)
            self.states["serialization_returned"] += 1
            if type(self.raw_serialization) is not bytes or not self.raw_serialization:
                raise policy.GuardError("source serializer did not return its real report bytes")
            self.serialized_bytes = len(self.raw_serialization)
        except BaseException as error:
            self.fail(stage, error)
        finally:
            self.sampler.phase = "report-finalize"
            try:
                self.collect()
                if self.first is None:
                    try:
                        self.summary = policy.summarize_report(
                            self.raw_report, self.changes, self.counters, self.binding,
                        )
                    except BaseException as error:
                        self.fail("validation", error)
            finally:
                self.withdraw(original)
                if self.first is None or local_observer:
                    self.observe_imports(lambda: self.observer.close_imports(self), "import-reference")
                imports = getattr(self.observer, "imports", None)
                if imports is not None:
                    self.secondary.extend(imports.secondary)
                    self.import_failed |= imports.failed or imports.note_failed
                if self.first is None and self.import_failed:
                    self.fail("import-observation", policy.GuardError("original source import observation failed"))
        if self.first is not None:
            raise self.first
        self.states["completed"] = True
        result = {
            "version": 1, "binding": self.binding, "states": dict(self.states),
            "summary": self.summary, "serialized_bytes": self.serialized_bytes,
            "counters": self.counters, "cleanup": self.cleanup,
        }
        try:
            policy.validate_report_result(result, self.binding)
        except BaseException as error:
            self.fail("validation", error)
            raise
        return result
