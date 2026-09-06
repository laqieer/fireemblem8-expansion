#!/usr/bin/env python3
"""Closed isolated-startup launcher for protected workflow-pilot modes."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODES = frozenset(
    {
        "anchor-refs",
        "baseline",
        "classify-event",
        "hydrate",
        "lifecycle-check",
        "reporter-tests",
    }
)
LIFECYCLE_CHECKS = frozenset({"workflow-pilot-reporter", "workflow-pilot-tests"})


@contextmanager
def _bootstrap_signal_guard():
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    missing, handlers, pending = object(), {}, {}
    try:
        handlers = {number: signal.getsignal(number) for number in signal.valid_signals()
                    if callable(signal.getsignal(number))}
        signal.pthread_sigmask(signal.SIG_BLOCK, handlers)
        if threading.current_thread() is threading.main_thread():
            pending = dict.fromkeys(handlers, missing)

            def defer(number, frame):
                pending[number] = frame

            for number in handlers:
                signal.signal(number, defer)
        yield
    finally:
        if pending:
            for number, handler in handlers.items():
                signal.signal(number, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        for number, frame in pending.items():
            if frame is not missing:
                handlers[number](number, frame)


class _BootstrapGitChild:
    """The trusted launcher cannot import the capsule owner before proving its Git bytes."""

    def __init__(self):
        self.process = None
        self.active = False

    def __enter__(self):
        if not all(callable(getattr(signal, name, None)) for name in
                   ("pthread_sigmask", "valid_signals", "getsignal", "signal")):
            raise ValueError("sealed Git bootstrap requires POSIX signal supervision")
        if not callable(getattr(os, "waitid", None)) or not hasattr(os, "WNOWAIT"):
            raise ValueError("sealed Git bootstrap requires owned-child exit observation")
        self.active = True
        return self

    def start(self, command, environment):
        if not self.active or self.process is not None:
            raise ValueError("Git bootstrap launch requires its active single owner")
        with _bootstrap_signal_guard():
            self.process = subprocess.Popen(
                command, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, close_fds=True, start_new_session=True)
        return self.process

    def owns_child(self):
        if self.process is None or self.process.returncode is not None:
            return False
        try:
            os.waitid(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return False
        return True

    def close(self):
        process, failure = self.process, None
        if process is None:
            self.active = False
            return
        try:
            if self.owns_child():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    failure = error
                finally:
                    if self.owns_child():
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            if self.owns_child():
                                try:
                                    os.kill(process.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                finally:
                                    if self.owns_child():
                                        process.wait()
        except BaseException as error:
            if failure is None:
                failure = error
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except BaseException as error:
                        if failure is None:
                            failure = error
            self.active = False
        if failure is not None:
            raise failure

    def __exit__(self, kind, value, traceback):
        try:
            with _bootstrap_signal_guard():
                self.close()
        except BaseException as cleanup_error:
            if value is not None:
                raise value.with_traceback(traceback) from cleanup_error
            raise


def _bootstrap_git(root, environment, *args, bound=2 * 1024 * 1024):
    with _BootstrapGitChild() as owner:
        process = owner.start(
            ["/usr/bin/git", "--no-replace-objects", "-c", "core.fsmonitor=false",
             "-C", str(root), *args], environment)
        result = {process.stdout.fileno(): bytearray(), process.stderr.fileno(): bytearray()}
        deadline = time.monotonic() + 15
        with selectors.DefaultSelector() as selector:
            for fd in result:
                selector.register(fd, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("sealed classifier Git bootstrap timed out")
                for ready, _ in selector.select(remaining):
                    raw = os.read(ready.fd, 65536)
                    if not raw:
                        selector.unregister(ready.fd)
                    result[ready.fd].extend(raw)
                    if len(result[ready.fd]) > bound:
                        raise ValueError("sealed classifier Git bootstrap exceeds bound")
        if process.returncode is None and not owner.owns_child():
            raise ValueError("sealed classifier Git bootstrap lost child ownership")
        if process.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise ValueError("sealed classifier requires locally available exact Git authority")
        return bytes(result[process.stdout.fileno()])


def run_sealed_classifier(arguments: list[str]) -> int:
    """Bootstrap the capsule runtime from Git bytes, not a validated pathname."""
    import hashlib
    import types

    environment = {
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }

    def git(*args, bound=2 * 1024 * 1024):
        return _bootstrap_git(ROOT, environment, *args, bound=bound)

    def object_bytes(kind, oid):
        if len(oid) != 40 or any(c not in "0123456789abcdef" for c in oid):
            raise ValueError("invalid bootstrap Git identity")
        source = git("cat-file", kind, oid)
        header = kind.encode() + b" " + str(len(source)).encode() + b"\0"
        if hashlib.sha1(header + source).hexdigest() != oid:
            raise ValueError("bootstrap bytes differ from Git object identity")
        return source

    if git("rev-parse", "--show-object-format") != b"sha1\n":
        raise ValueError("sealed classifier requires Git SHA-1 object format")
    revision = git("rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("sealed classifier requires an exact commit")
    path = "scripts/workflow_pilot/sealed_capsule.py"
    commit = object_bytes("commit", revision)
    header = commit.split(b"\n", 1)[0]
    if not header.startswith(b"tree "):
        raise ValueError("bootstrap commit has no exact tree")
    oid = header[5:].decode("ascii")
    components = path.split("/")
    for index, component in enumerate(components):
        raw, offset, entries = object_bytes("tree", oid), 0, {}
        while offset < len(raw):
            space, end = raw.find(b" ", offset), raw.find(b"\0", offset)
            if space < offset or end <= space or end + 21 > len(raw):
                raise ValueError("malformed bootstrap tree")
            name = raw[space + 1:end]
            if name in entries:
                raise ValueError("duplicate bootstrap tree entry")
            entries[name] = (raw[offset:space], raw[end + 1:end + 21].hex())
            offset = end + 21
        if component.encode() not in entries:
            raise ValueError("sealed capsule runtime is missing from exact tree")
        mode, oid = entries[component.encode()]
        modes = {b"100644", b"100755"} if index == len(components) - 1 else {b"40000"}
        if mode not in modes:
            raise ValueError("sealed capsule runtime has an unsafe tree entry")
    source = object_bytes("blob", oid)
    runtime = types.ModuleType("_workflow_capsule_transport")
    sys.modules[runtime.__name__] = runtime
    exec(compile(source, "sealed:runtime-transport", "exec", dont_inherit=True), runtime.__dict__)
    spec = runtime.CapsuleSpec(
        trees={"base": revision},
        programs={"classify-event": "scripts/workflow_pilot/event_classifier.py"},
    )
    with runtime.prepare(ROOT, spec) as prepared:
        bundle = runtime._Bundle(prepared.bundle_fd.read())
        # CLI parsing and runner-owned I/O are transport, not assertion execution.
        # Even this transport module is loaded from the sealed artifact bytes.
        classifier = types.ModuleType("_workflow_classifier_transport")
        sys.modules[classifier.__name__] = classifier
        exec(compile(bundle.program("classify-event"), "sealed:classifier-transport",
                     "exec", dont_inherit=True), classifier.__dict__)

        def classify(**request):
            try:
                result = prepared.execute("classify-event", request)
            except runtime.CapsuleError as error:
                raise classifier.EventClassificationError(str(error)) from error
            return classifier.EventDecision(**result.value)

        return classifier.main(arguments, classifier=classify)


def clear_ambient_git_environment() -> None:
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            del os.environ[name]


def controlled_repository_root(arguments: list[str]) -> Path:
    positions = [
        index
        for index, argument in enumerate(arguments)
        if argument == "--repository-root"
    ]
    if len(positions) != 1 or positions[0] + 1 >= len(arguments):
        raise ValueError("mode requires exactly one --repository-root")
    root = Path(arguments[positions[0] + 1]).resolve(strict=True)
    if root != ROOT:
        raise ValueError(
            f"--repository-root must identify controlled source root {ROOT}"
        )
    return root


def run_reporter_tests(arguments: list[str]) -> int:
    if arguments:
        raise ValueError("reporter-tests mode accepts no arguments")
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "scripts" / "workflow_pilot" / "tests"),
        pattern="test_*.py",
        top_level_dir=str(ROOT),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def run_lifecycle_check(arguments: list[str]) -> int:
    if len(arguments) != 6 or arguments[::2] != [
        "--artifact-root",
        "--authority-root",
        "--check",
    ]:
        raise ValueError("lifecycle-check requires its exact closed arguments")
    artifact_root = Path(arguments[1]).resolve(strict=True)
    authority_root = Path(arguments[3]).resolve(strict=True)
    check_id = arguments[5]
    if artifact_root != ROOT:
        raise ValueError(f"--artifact-root must identify launcher root {ROOT}")
    if check_id not in LIFECYCLE_CHECKS:
        raise ValueError("lifecycle check is not allowlisted")

    from scripts.workflow_pilot import reporter

    try:
        authority_root = reporter.validate_repository_root(authority_root)
    except reporter.PilotDataError as error:
        raise ValueError(str(error)) from error
    if check_id == "workflow-pilot-reporter":
        try:
            fixture = reporter.load_json(ROOT / reporter.BASELINE_FIXTURE_PATH)
            decisions = reporter.load_json(ROOT / reporter.DECISION_RECORD_PATH)
            report = reporter.build_report(fixture, decisions, authority_root)
            reporter.check_expected(
                report,
                reporter.load_json(ROOT / reporter.BASELINE_EXPECTED_PATH),
            )
        except reporter.PilotDataError as error:
            raise ValueError(str(error)) from error
        return 0

    os.environ["WORKFLOW_PILOT_TEST_AUTHORITY_ROOT"] = str(authority_root)
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "scripts.workflow_pilot.tests.test_reporter."
        "BaselineFixtureTests.test_frozen_baseline_and_expected_values"
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def dispatch(mode: str, arguments: list[str]) -> int:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(sorted(MODES))}")
    os.chdir(ROOT)
    if mode == "reporter-tests":
        return run_reporter_tests(arguments)
    if mode == "lifecycle-check":
        return run_lifecycle_check(arguments)
    if mode == "classify-event":
        return run_sealed_classifier(arguments)

    controlled_repository_root(arguments)
    if mode == "anchor-refs":
        from scripts.workflow_pilot import hydrate_authority

        return hydrate_authority.print_anchor_refs(arguments)
    if mode == "hydrate":
        from scripts.workflow_pilot import hydrate_authority

        return hydrate_authority.main(arguments)

    from scripts.workflow_pilot import reporter

    return reporter.main(arguments)


def main(argv: list[str] | None = None) -> int:
    if not sys.flags.isolated:
        print(
            "workflow-pilot-launcher: isolated Python startup (-I) is required",
            file=sys.stderr,
        )
        return 2
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print("workflow-pilot-launcher: mode is required", file=sys.stderr)
        return 2
    clear_ambient_git_environment()
    if arguments[0] != "classify-event":
        sys.path.insert(0, str(ROOT))
    try:
        return dispatch(arguments[0], arguments[1:])
    except (OSError, ValueError) as error:
        print(f"workflow-pilot-launcher: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
