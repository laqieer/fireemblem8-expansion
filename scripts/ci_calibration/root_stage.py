"""Observe one original public report, without replacing any authority method."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace, FunctionType
from contextlib import ExitStack
import dataclasses
import ast
import importlib
import subprocess
import time

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

    def release_imports(self):
        closing = self.observer.finish_imports(self)
        if closing is not None:
            self.secondary.append(closing)
            self.import_failed = True

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
                    self.observe_imports(self.release_imports, "import-reference")
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


class NativeRefusalObservation:
    """Literal diagnostics only; never source execution or qualification authority."""

    def __init__(self, session_type, root):
        self.session_type = session_type
        self.code = None
        self.globals = None
        self.catalog = ()
        self.reason = "catalog-unavailable"
        self.retired = False
        try:
            method = type.__getattribute__(session_type, "__dict__").get("_sandbox_run")
            if type(method) is not FunctionType or (
                method.__globals__.get("__name__") != "scripts.validation_ownership.make_probe"
                or type.__getattribute__(session_type, "__module__") != "scripts.validation_ownership.make_probe"
            ):
                self.reason = "original-code-unavailable"
                return
            self.code = method.__code__
            self.globals = method.__globals__
            rows, extent = [], 0
            for module in policy.NATIVE_REFUSAL_MODULES:
                with (root / "scripts/validation_ownership" / (module + ".py")).open("rb") as stream:
                    raw = stream.read(policy.NATIVE_REFUSAL_BYTES - extent + 1)
                extent += len(raw)
                if extent > policy.NATIVE_REFUSAL_BYTES:
                    self.reason = "catalog-bound"
                    return
                tree = ast.parse(raw)
                count = 0
                for node in ast.walk(tree):
                    count += 1
                    if count > 262144:
                        self.reason = "catalog-bound"
                        return
                    if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                        continue
                    for argument in node.exc.args:
                        if isinstance(argument, ast.Constant) and type(argument.value) is str:
                            if len(argument.value) > policy.ERROR_BYTES or len(rows) >= 4096:
                                self.reason = "catalog-bound"
                                return
                            rows.append((argument.value, module, node.lineno))
            self.catalog = tuple(rows)
            self.reason = None
        except (OSError, SyntaxError, ValueError, RecursionError):
            self.reason = "catalog-unavailable"

    def retire(self):
        self.catalog = ()
        self.code = self.globals = self.session_type = None
        self.retired = True

    def capture(self, error, session):
        try:
            return self._capture(error, session)
        finally:
            self.retire()

    def _capture(self, error, session):
        def unavailable(reason):
            return policy.native_refusal_unavailable(reason)

        if self.retired:
            return unavailable("retired")
        if self.reason is not None:
            return unavailable(self.reason)
        if type(session) is not self.session_type:
            return unavailable("session-unavailable")
        current, seen, frames, found = error, set(), set(), []
        while current is not None:
            if id(current) in seen or len(seen) >= 32:
                return unavailable("exception-chain-bound")
            seen.add(id(current))
            trace = BaseException.__getattribute__(current, "__traceback__")
            while trace is not None:
                if id(trace) in frames or len(frames) >= 256:
                    return unavailable("frame-bound")
                frames.add(id(trace))
                frame = trace.tb_frame
                if frame.f_code is self.code and frame.f_globals is self.globals:
                    local = frame.f_locals
                    if type(local) is not dict or len(local) > 256 or local.get("self") is not session:
                        return unavailable("foreign-session")
                    observed = local.get("observed")
                    if type(observed) is not dict or len(observed) > 256 or any(type(key) is not str for key in observed):
                        return unavailable("malformed-observation")
                    text, ok, returncode = (
                        observed.get("error"), observed.get("ok"), observed.get("returncode"),
                    )
                    if type(text) is not str or len(text) > policy.ERROR_BYTES or (
                        type(ok) is not bool or type(returncode) is not int
                        or not -(1 << 31) <= returncode < (1 << 31)
                    ):
                        return unavailable("malformed-observation")
                    sites = sorted(set(
                        (module, line) for literal, module, line in self.catalog if literal == text
                    ))
                    status = "unique" if len(sites) == 1 else "ambiguous" if sites else "unknown"
                    found.append({
                        "status": "observed", "reason": None, "ok": ok, "returncode": returncode,
                        "match": status, "sites": [{"module": module, "line": line} for module, line in sites],
                    })
                trace = trace.tb_next
            cause = BaseException.__getattribute__(current, "__cause__")
            current = cause if cause is not None else BaseException.__getattribute__(current, "__context__")
        if len(found) != 1:
            return unavailable("frame-ambiguous" if found else "frame-unobserved")
        return policy.validate_native_refusal(found[0])


class NativeRecorder:
    """Finite test-reference injection; production authority methods stay original."""

    def __init__(self, config):
        self.config = config
        self.selection = policy.native_selection(config["profile"], config["selector"])
        self.states = dict.fromkeys((
            "budget_requests", "session_attempts", "make_attempts", "make_returned",
            "command_attempts", "command_returned", "report_attempts",
            "verifier_attempts", "h1_attempts", "serialization_attempts",
        ), 0)
        self.session = self.case = self.budget = None
        self.operations, self.archives, self.children = [], [], []
        self.archive_refusals = []
        self.seen = set()
        self.bindings = {}
        self.observations = []
        self.members = {}
        self.abi_version = None
        self.first = None
        self.first_stage = None
        self.failure_kind = None
        self.progress_stage = "worker-entrypoint"
        self.secondary_errors = 0
        self.method_returned = False
        self.references_restored = False
        self.refusal_observer = None
        self.primary_error = None
        self.refusal = policy.native_refusal_unavailable("not-captured")

    def capture_first(self, error):
        self.primary_error = policy.component_secondary_error(error)
        if self.refusal_observer is not None:
            try:
                self.refusal = self.refusal_observer.capture(error, self.session)
            except BaseException:
                self.refusal = policy.native_refusal_unavailable("projection-failed")
                self.refusal_observer.retire()

    def remaining(self):
        if time.monotonic() >= self.config["deadline"]:
            raise policy.GuardError("native outer preparation deadline exhausted")
        if self.budget is not None:
            self.budget.remaining()

    def bind(self, stack, owner, name, replacement):
        original = getattr(owner, name)
        present = name in vars(owner)
        self.bindings.setdefault((id(owner), name), (owner, name, present, vars(owner).get(name)))
        setattr(owner, name, replacement)

        def restore():
            if getattr(owner, name) is not replacement:
                raise policy.GuardError("native test reference changed before withdrawal")
            if present:
                setattr(owner, name, original)
            else:
                delattr(owner, name)

        stack.callback(restore)

    def budget_for(self, limits=None):
        self.remaining()
        if self.states["budget_requests"]:
            raise policy.GuardError("native selector attempted another original lifetime")
        selected = self.api.limits() if limits is None else limits
        if type(selected) is not self.api.limits or dataclasses.asdict(selected) != dataclasses.asdict(self.budget.limits):
            raise policy.GuardError("native selector changed its authored original Limits")
        self.states["budget_requests"] += 1
        return self.budget

    def session_for(self, loader, *, scratch_root, budget, runtime_files=()):
        self.remaining()
        if self.states["session_attempts"] or budget is not self.budget or (
            type(loader) is not self.api.loader or loader.budget is not budget
            or type(loader.entries) is not self.api.entries or loader.entries.budget is not budget
            or self.case is None or loader.root != self.case.fixture.root
            or scratch_root != self.case.fixture.scratch
        ):
            raise policy.GuardError("native session lost its original fixture/loader/entries ownership")
        self.states["session_attempts"] += 1
        self.session = self.api.session(
            loader, scratch_root=scratch_root, budget=budget, runtime_files=runtime_files,
        )
        return self.session

    def popen(self, *args, **keywords):
        self.remaining()
        if len(self.children) >= self.budget.limits.runs:
            raise policy.GuardError("native Popen observation extent exhausted")
        child = self.api.popen(*args, **keywords)
        self.children.append(child)
        return child

    def operation(self, kind, original, *args, **keywords):
        self.remaining()
        if len(self.operations) >= 16:
            raise policy.GuardError("native operation observation extent exhausted")
        count = kind + "_attempts"
        self.states[count] += 1
        row = {"kind": kind, "ordinal": self.states[count], "returned": False}
        self.operations.append(row)
        member = None
        if kind == "make":
            target = args[0] if args else None
            primary = keywords.get("makefile", "Makefile")
            if self.selection["selector"] != policy.NATIVE_SELECTORS[0]:
                member = "terminal-preparation"
            elif target == "all" and primary == "native-completion.mk":
                member = "source-family"
            elif target == "print-ASSET_OUTPUT_DIR" and primary in {"assets.mk", "Makefile"}:
                inputs = keywords.get("assignments")
                if type(inputs) is not tuple or len(inputs) != 5:
                    raise policy.GuardError("native profile lacks its finite original input vector")
                profiles = (
                    ("", "0", "build/expansion-modern", "assets/manifest.json", "default"),
                    ("0xCE", "0", "build/native-completion-alt", "assets/manifest.json", "alt"),
                    ("", "1", "build/native-completion-custom",
                     "assets/manifests/custom-spell-reference.json", "custom"),
                    ("0xCE", "1", "build/native-completion-alt-custom",
                     "assets/manifests/custom-spell-reference.json", "alt-custom"),
                )
                for cap, custom, root, manifest, profile in profiles:
                    if inputs == (
                        ("command-line", "PYTHON", "python3"),
                        ("command-line", "FE8_ITEM_ID_CAP", cap),
                        ("command-line", "EXPANSION_CUSTOM_SPELL_EFFECTS", custom),
                        ("command-line", "MODERN_BUILD_ROOT", root),
                        ("command-line", "ASSET_MANIFEST", manifest),
                    ):
                        member = ("standalone" if primary == "assets.mk" else "modern") + "-" + profile
                if member is None:
                    raise policy.GuardError("native Make profile is outside the authored input table")
            else:
                raise policy.GuardError("native method launched an unselected Make operation")
        value = original(*args, **keywords)
        row["returned"] = True
        self.states[kind + "_returned"] += 1
        if kind == "make":
            self.observations.append(value)
            self.members[id(value)] = member
        return value

    def archive(self, original, observed):
        closed, failed = self.budget.closed, self.budget.failed
        try:
            value = original(observed)
        except BaseException:
            if len(self.archive_refusals) >= 16:
                raise policy.GuardError("native archive refusal observation extent exhausted")
            self.archive_refusals.append({
                "budget_closed_before": closed, "budget_failed_before": failed,
                "budget_failed_after": self.budget.failed,
            })
            raise
        if id(observed) not in self.seen:
            if len(self.archives) >= 16:
                raise policy.GuardError("native semantic archive extent exhausted")
            self.seen.add(id(observed))
            if id(observed) not in self.members or not any(observed is row for row in self.observations):
                raise policy.GuardError("native archive is not an actual selected Make return")
            member = self.members[id(observed)]
            visits = [visit for part in value.passes for visit in part.visits]
            completion_bindings = all(
                any(
                    visit.number == completion.visit and visit.source is not None
                    and visit.source.number == completion.source
                    for visit in part.visits
                )
                for part in value.passes for completion in part.completions
            )
            self.archives.append({
                "member": member, "trace_version": value.version, "abi_version": self.abi_version,
                "passes": len(value.passes), "visits": sum(len(part.visits) for part in value.passes),
                "sources": len(value.sources),
                "completions": sum(len(part.completions) for part in value.passes),
                "source_closed": None if observed.source_phases is None else observed.source_phases["closed"],
                "journal_closed": None if observed.source_journal is None else observed.source_journal["closed"],
                "frontier_consumed": None,
                "parent_links": sum(visit.parent is not None for visit in visits),
                "raw_pinned_path_differences": sum(
                    opened.result >= 0 and visit.name != opened.path
                    for visit in visits for opened in visit.opens
                ),
                "publication_opens": sum(
                    opened.result >= 0 and opened.custody["kind"] in {"publication", "prior-publication"}
                    for visit in visits for opened in visit.opens
                ),
                "completion_visits_bound": completion_bindings,
                "publication_order_bound": None,
            })
        return value

    def watch_session(self, stack):
        session = self.session
        for name, kind in (("make", "make"), ("command", "command")):
            original = getattr(session, name)
            self.bind(stack, session, name, lambda *a, _o=original, _k=kind, **kw: self.operation(_k, _o, *a, **kw))
        original = session._original_source_archive
        self.bind(stack, session, "_original_source_archive", lambda observed: self.archive(original, observed))
        original_abi = session._original_read_abi

        def abi(*args, **keywords):
            value = original_abi(*args, **keywords)
            if keywords.get("completions") is True and value["version"] == 2:
                self.abi_version = value["version"]
                for row in self.archives:
                    row["abi_version"] = value["version"]
            return value

        self.bind(stack, session, "_original_read_abi", abi)

    def run(self):
        if __package__:
            from .worker import require_contained
        else:
            from worker import require_contained
        require_contained(self.config)
        if self.config.get("mode") != "native-completion" or (
            self.config.get("source_revision") != policy.NATIVE_SOURCE or "report_binding" in self.config
        ):
            raise policy.GuardError("native adapter has no contained source-only invocation")
        self.progress_stage = "candidate-import"
        from scripts.validation_ownership import budget as budgeting
        from scripts.validation_ownership.authority import AuthorityLoader, GitTreeEntries
        from scripts.validation_ownership.make_probe import ProbeSession
        from scripts.validation_ownership.tests import test_foundation as foundation
        self.api = SimpleNamespace(
            limits=budgeting.Limits, budget=budgeting.ProbeBudget, loader=AuthorityLoader,
            entries=GitTreeEntries, session=ProbeSession, popen=subprocess.Popen,
        )
        self.refusal_observer = NativeRefusalObservation(ProbeSession, Path("/repo"))
        self.progress_stage = "setup"
        terminal_short = self.selection["selector"] in policy.NATIVE_SELECTORS[-2:]
        limits = self.api.limits(seconds=20) if terminal_short else self.api.limits()
        start = self.config["deadline"] - policy.GRAPH_SECONDS

        @dataclasses.dataclass
        class OriginalClockBudget(budgeting.ProbeBudget):
            def __post_init__(inner):
                inner.started = start

        self.budget = OriginalClockBudget(limits)
        initial_limits = dataclasses.asdict(limits)
        self.progress_stage = "candidate-import"
        first = None
        withdrawal_error = None
        setup = False
        try:
            self.remaining()
            module_name, class_name, method_name = self.selection["selector"].rsplit(".", 2)
            selected = importlib.import_module(module_name)
            owner = getattr(selected, class_name)
            method = getattr(owner, method_name)
            self.case = owner(method_name)
            with ExitStack() as stack:
                try:
                    self.bind(stack, subprocess, "Popen", self.popen)
                    self.bind(stack, selected, "ProbeBudget", self.budget_for)
                    self.bind(stack, foundation, "ProbeBudget", self.budget_for)
                    # The positive method uses its module's direct constructor, not case.session().
                    proxy = SimpleNamespace(**vars(selected.make_probe))
                    session_constructor = self.session_for

                    def construct(*args, **keywords):
                        value = session_constructor(*args, **keywords)
                        self.watch_session(stack)
                        return value

                    proxy.ProbeSession = construct
                    self.bind(stack, selected, "make_probe", proxy)
                    self.bind(stack, foundation, "ProbeSession", construct)
                    if self.selection["selector"] == policy.NATIVE_SELECTORS[0]:
                        analyzer = selected.phase_census
                        phase_proxy = SimpleNamespace(**vars(analyzer))

                        def analyze(*args, **keywords):
                            value = analyzer.analyze(*args, **keywords)
                            observed = args[1]
                            archive = self.session._original_source_archive(observed)
                            phases = [stream.mode_state.scope_lookup_guard.__self__ for stream in value[2]]
                            complete = len(phases) == len(archive.passes) and all(
                                phase.next_completion == len(phase.part.completions) for phase in phases
                            )
                            for row in self.archives:
                                if row["member"] == self.members.get(id(observed)):
                                    row["frontier_consumed"] = complete
                            return value

                        phase_proxy.analyze = analyze
                        self.bind(stack, selected, "phase_census", phase_proxy)
                    self.progress_stage = "setup"
                    self.remaining()
                    self.case.setUp()
                    setup = True
                    self.progress_stage = "method"
                    method(self.case)
                    self.method_returned = True
                except BaseException as error:
                    self.first = error
                    self.first_stage = self.progress_stage
                    self.capture_first(error)
                    raise
                finally:
                    self.progress_stage = "finalize"
        except BaseException as error:
            if self.first is None:
                self.first = error
                self.first_stage = self.progress_stage
                self.capture_first(error)
            elif error is not self.first:
                self.secondary_errors += 1
                withdrawal_error = error
            first = self.first
            self.failure_kind = (
                "source-refusal" if isinstance(first, budgeting.MakeProbeError) else
                "authored-assertion" if isinstance(first, AssertionError) else
                "harness-guard" if isinstance(first, policy.GuardError) else "unexpected"
            )
        finally:
            self.progress_stage = "finalize"
            self.references_restored = all(
                (name in vars(owner)) == present and (not present or vars(owner)[name] is original)
                for owner, name, present, original in self.bindings.values()
            )
            if not self.references_restored:
                if first is None:
                    self.first = first = policy.GuardError("native reference restoration failed")
                    self.first_stage = "finalize"
                    self.failure_kind = "harness-guard"
                    self.capture_first(first)
                else:
                    self.secondary_errors += 1
            try:
                if self.budget is not None:
                    self.budget.close()
                safe = not self.budget.children and not self.budget.producer_waiters and (
                    self.session is None or self.session.base is None
                    and not self.session._views
                    and not any(owner.retained for owner in self.session._file_owners.values())
                )
                if setup and safe:
                    self.case.tearDown()
            except BaseException as error:
                if first is None:
                    self.first = first = error
                    self.first_stage = "finalize"
                    self.failure_kind = "cleanup"
                    self.capture_first(first)
                else:
                    self.secondary_errors += 1
            self.observations.clear()
            self.refusal_observer.retire()
        if withdrawal_error is not None:
            raise withdrawal_error
        ended = time.monotonic()
        self.first = first
        cleanup = cleanup_state(self.session, self.budget)
        native_cleanup = {name: cleanup[name] for name in (
            "budget_closed", "children", "waiters", "retained_owners", "session_base_removed",
        )}
        native_cleanup.update(
            fixture_removed=None if not setup else not self.case.fixture.directory.exists(),
            references_restored=self.references_restored,
        )
        self.progress_stage = "counter-publication"
        result = {
            "version": 2, "primary_error": self.primary_error,
            "refusal": self.refusal if first is not None else {
                "status": "success", "reason": None, "ok": None, "returncode": None,
                "match": None, "sites": [],
            },
            "selection": self.selection, "states": self.states, "operations": self.operations,
            "clock": {
                "started": self.budget.started, "deadline": self.budget.deadline,
                "ended_at": ended, "elapsed_seconds": ended - self.budget.started,
                "limits_seconds": self.budget.limits.seconds,
                "outer_deadline": self.config["deadline"],
                "limits_unchanged": dataclasses.asdict(self.budget.limits) == initial_limits,
            },
            "archives": self.archives, "counters": policy.counter_snapshot(self.budget, self.session),
            "archive_refusals": self.archive_refusals,
            "cleanup": native_cleanup, "children": {
                "created": len(self.children),
                "terminal": sum(child.returncode is not None for child in self.children),
                "nonzero": sum(child.returncode not in (None, 0) for child in self.children),
                "unknown": sum(child.returncode is None for child in self.children),
            },
            "method_returned": self.method_returned, "first_stage": self.first_stage,
            "failure_kind": self.failure_kind,
            "secondary_errors": self.secondary_errors,
            "machine_holds": list(policy.NATIVE_MACHINE_HOLDS), "qualification": "incomplete",
        }
        self.progress_stage = "result-validation"
        policy.validate_native_result(result, self.selection)
        return result, first is None
