from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import resource
import secrets
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import unittest
import venv
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.validation_ownership.authority import (
    AuthorityLoader, ENVIRONMENT, GitTreeEntries, GitTreeEntry, Snapshot, git_tree_entries,
)
from scripts.validation_ownership.budget import Limits, MakeProbeError, NAMESPACE_LAUNCHER, ProbeBudget
from scripts.validation_ownership.make_probe import (
    Command, ProbeSession, TRUSTED_ROOT, _event_command, _make_interpreter, _make_runtime,
    _read_events, _read_observation, _trusted_runtime_bytes, probe_generated_registry,
)


ROOT = Path(__file__).resolve().parents[3]


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/test-artifacts/ownership-foundation-tests" / secrets.token_hex(12)
        self.root = self.directory / "repo"
        self.root.mkdir(parents=True)
        self.entries = {}
        self.scratch = self.root / "build/probe"

    def tearDown(self):
        shutil.rmtree(self.directory)

    def add(self, path, value, mode="100644"):
        data = value.encode("utf-8") if isinstance(value, str) else value
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        self.entries[path] = GitTreeEntry(path, mode, "blob", hashlib.sha1(data).hexdigest())

    def session(self, **limits):
        budget = ProbeBudget(Limits(**limits))
        return ProbeSession(
            AuthorityLoader(self.root, GitTreeEntries(self.entries, budget=budget), budget=budget),
            scratch_root=self.scratch,
            budget=budget,
        )

    def assert_clean(self, session):
        self.assertFalse(session.cache)
        self.assertFalse(session.mappings)
        self.assertFalse(session.make_runtime)
        self.assertFalse(session.budget.children)
        self.assertIsNone(session.snapshot)
        self.assertIsNone(session.base)
        self.assertFalse(self.scratch.exists())

    def capture_tree(self, budget):
        def git(*args):
            return subprocess.run(
                ["/usr/bin/git", "-C", str(self.root), *args],
                env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
            ).stdout
        git("init", "--quiet")
        git("add", "--", *sorted(self.entries))
        revision = git("write-tree").decode("ascii").strip()
        return git_tree_entries(self.root, revision, budget=budget), revision

    def test_authority_stages_require_an_explicit_report_budget(self):
        self.add("Makefile", "all: ;\n")
        entries, revision = self.capture_tree(ProbeBudget())
        loader = self.session().loader
        for stage, operation in (
            ("capture", lambda: git_tree_entries(self.root, revision)),
            ("loader", lambda: AuthorityLoader(self.root, entries, revision)),
            ("session", lambda: ProbeSession(loader, scratch_root=self.scratch)),
        ):
            with self.subTest(stage=stage):
                with self.assertRaises(TypeError):
                    operation()
        self.assertFalse(self.scratch.exists())

    def test_authority_composition_rejects_foreign_and_detached_budgets(self):
        self.add("Makefile", "all: ;\n")
        budget = ProbeBudget()
        entries, revision = self.capture_tree(budget)
        loader = AuthorityLoader(self.root, entries, revision, budget=budget)
        self.assertIs(entries.budget, budget)
        self.assertIs(loader.budget, budget)
        before = (budget.started, budget.deadline, budget.runs, dict(budget.bytes))
        for wrong in (None, object(), ProbeBudget()):
            for stage, operation in (
                ("loader", lambda: AuthorityLoader(self.root, entries, revision, budget=wrong)),
                ("snapshot", lambda: Snapshot(loader, wrong)),
                ("session", lambda: ProbeSession(loader, scratch_root=self.scratch, budget=wrong)),
            ):
                with self.subTest(stage=stage, budget=wrong):
                    with self.assertRaisesRegex(MakeProbeError, "report budget"):
                        operation()
        for wrong in (None, object()):
            with self.subTest(capture_budget=wrong):
                with self.assertRaisesRegex(MakeProbeError, "explicit report budget"):
                    git_tree_entries(self.root, revision, budget=wrong)
        for detached in (dict(entries), entries.copy()):
            with self.assertRaisesRegex(MakeProbeError, "capture's report budget"):
                AuthorityLoader(self.root, detached, revision, budget=budget)
        self.assertEqual((budget.started, budget.deadline, budget.runs, budget.bytes), before)
        self.assertFalse(budget.children)
        self.assertFalse(self.scratch.exists())

    def test_authority_chain_keeps_capture_reads_snapshot_and_execution_on_one_budget(self):
        self.add("Makefile", "all: ;\n")
        budget = ProbeBudget()
        entries, revision = self.capture_tree(budget)
        started, deadline = budget.started, budget.deadline
        self.assertEqual(budget.runs, 1)
        capture_bytes = dict(budget.bytes)
        loader = AuthorityLoader(self.root, entries, revision, budget=budget)
        self.assertEqual(loader.read_blob("Makefile", "owned input"), b"all: ;\n")
        self.assertEqual(budget.runs, 2)
        self.assertGreater(budget.bytes["output"], capture_bytes["output"])
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            self.assertIs(session.budget, budget)
            self.assertIs(session.snapshot.budget, budget)
            self.assertIs(loader.budget, budget)
            self.assertIs(entries.budget, budget)
            self.assertEqual(session.snapshot.files["Makefile"], b"all: ;\n")
            self.assertEqual((budget.started, budget.deadline), (started, deadline))
            self.assertGreater(budget.runs, 2)
            self.assertGreater(budget.bytes["snapshot"], len(b"all: ;\n"))
            runs, charged = budget.runs, dict(budget.bytes)
            with self.assertRaisesRegex(MakeProbeError, "snapshot's report budget"):
                session.snapshot.materialize(self.directory / "unfunded", ["Makefile"], ProbeBudget())
            self.assertEqual((budget.runs, budget.bytes), (runs, charged))
            self.assertFalse((self.directory / "unfunded").exists())
            observed = session.make("all")
            self.assertEqual(observed.semantics["files"][0]["target"], "all")
            self.assertGreater(budget.runs, runs)
            self.assertGreater(session.processes_used, 0)
        self.assert_clean(session)
        self.assertIs(loader.budget, budget)
        self.assertTrue(budget.closed)
        self.assertEqual((budget.started, budget.deadline), (started, deadline))

    def test_capture_run_quota_cannot_be_reset_by_reads_snapshot_or_session(self):
        self.add("Makefile", "all: ;\n")
        for stage in ("read", "snapshot", "session"):
            with self.subTest(stage=stage):
                budget = ProbeBudget(Limits(runs=1))
                entries, revision = self.capture_tree(budget)
                loader = AuthorityLoader(self.root, entries, revision, budget=budget)
                session = ProbeSession(loader, scratch_root=self.scratch, budget=budget)
                with self.assertRaisesRegex(MakeProbeError, "aggregate process-launch budget"):
                    if stage == "read":
                        loader.read_blob("Makefile", "owned input")
                    elif stage == "snapshot":
                        Snapshot(loader, budget)
                    else:
                        with session:
                            self.fail("capture quota was reset")
                budget.close()
                self.assertEqual(budget.runs, 2)
                self.assertTrue(budget.failed)
                self.assert_clean(session)

    def test_direct_live_and_immutable_authority_reads_share_the_byte_quota(self):
        self.add("Makefile", "all: ;\n")
        self.add("input.bin", b"x"*2048)
        for immutable in (False, True):
            with self.subTest(immutable=immutable):
                budget = ProbeBudget(Limits(total_bytes=4096, file_bytes=4096))
                entries, revision = self.capture_tree(budget)
                loader = AuthorityLoader(
                    self.root, entries, revision if immutable else None, budget=budget,
                )
                captured = sum(budget.bytes.values())
                self.assertEqual(loader.read_blob("input.bin", "owned input"), b"x"*2048)
                self.assertGreaterEqual(sum(budget.bytes.values()), captured + 2048)
                with self.assertRaisesRegex(MakeProbeError, "aggregate .*byte budget"):
                    loader.read_blob("input.bin", "owned repeated input")
                budget.close()
                self.assertTrue(budget.failed)
                self.assertFalse(budget.children)
                self.assertFalse(self.scratch.exists())

    def test_expired_capture_cannot_start_another_authority_stage(self):
        self.add("Makefile", "all: ;\n")
        budget = ProbeBudget(Limits(seconds=1))
        entries, revision = self.capture_tree(budget)
        loader = AuthorityLoader(self.root, entries, revision, budget=budget)
        before = (budget.started, budget.deadline, budget.runs, dict(budget.bytes))
        time.sleep(max(0, budget.deadline - time.monotonic()) + 0.02)
        for stage, operation in (
            ("capture", lambda: git_tree_entries(self.root, revision, budget=budget)),
            ("loader", lambda: AuthorityLoader(self.root, entries, revision, budget=budget)),
            ("read", lambda: loader.read_blob("Makefile", "expired input")),
            ("snapshot", lambda: Snapshot(loader, budget)),
            ("session", lambda: ProbeSession(loader, scratch_root=self.scratch, budget=budget)),
        ):
            with self.subTest(stage=stage):
                with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline/budget"):
                    operation()
        self.assertEqual((budget.started, budget.deadline, budget.runs, budget.bytes), before)
        self.assertFalse(budget.children)
        self.assertFalse(self.scratch.exists())

    def test_report_budget_binds_one_terminal_session_lifetime(self):
        self.add("Makefile", "all: ;\n")
        budget = ProbeBudget()
        entries, revision = self.capture_tree(budget)
        loader = AuthorityLoader(self.root, entries, revision, budget=budget)
        second_loader = AuthorityLoader(self.root, entries, revision, budget=budget)
        second = ProbeSession(second_loader, scratch_root=self.scratch, budget=budget)
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            for another in (session, second):
                with self.subTest(owner=another.loader is loader):
                    with self.assertRaisesRegex(MakeProbeError, "already owns a probe session lifetime"):
                        with another:
                            self.fail("report acquired a second session")
            self.assertFalse(budget.closed)
            self.assertEqual(session.command(Command(("/usr/bin/printf", "original owner"))).stdout, b"original owner")
        self.assert_clean(session)
        before = (budget.started, budget.deadline, budget.runs, dict(budget.bytes))
        for stage, operation in (
            ("capture", lambda: git_tree_entries(self.root, revision, budget=budget)),
            ("loader", lambda: AuthorityLoader(self.root, entries, revision, budget=budget)),
            ("read", lambda: loader.read_blob("Makefile", "closed input")),
            ("snapshot", lambda: Snapshot(loader, budget)),
            ("session", lambda: ProbeSession(loader, scratch_root=self.scratch, budget=budget)),
        ):
            with self.subTest(closed_stage=stage):
                with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline/budget"):
                    operation()
        budget.close()
        self.assertTrue(budget.closed)
        self.assertEqual((budget.started, budget.deadline, budget.runs, budget.bytes), before)
        self.assertFalse(self.scratch.exists())

    def test_registered_python_reexec_rejects_before_replacement_startup(self):
        replacement = (
            "import json,sys\n"
            "open('/work/reexecuted','w').write(json.dumps("
            "[sys.flags.isolated,sys.flags.no_site,sys.flags.dont_write_bytecode]))\n"
        )
        operations = (
            ("first-launch", "", None),
            ("execve", "os.execve('/usr/bin/python3',argv,environment)", "post-bootstrap exec"),
            ("no-startup-flags", "os.execve('/usr/bin/python3',argv[0:1]+argv[2:],environment)", "post-bootstrap exec"),
            ("runtime-alias", "os.execve('/bin/python3',argv,environment)", "post-bootstrap exec"),
            ("raw-execve", "libc.syscall(59,b'/usr/bin/python3',vector,envp)", "post-bootstrap exec"),
            ("fork-execve",
             "child=os.fork()\n"
             "if child:\n"
             " _,status=os.waitpid(child,0)\n"
             " os._exit(os.waitstatus_to_exitcode(status))\n"
             "os.execve('/usr/bin/python3',argv,environment)", "post-bootstrap exec"),
            ("execveat-path", "libc.syscall(322,-100,b'/usr/bin/python3',vector,envp,0)", "unadmitted syscall 322"),
            ("execveat-fd",
             "descriptor=os.open('/usr/bin/python3',os.O_RDONLY)\n"
             "libc.syscall(322,descriptor,b'',vector,envp,0x1000)", "unadmitted syscall 322"),
        )
        for name, operation, rejected in operations:
            with self.subTest(entry=name):
                self.add("launch.py", (
                    "import ctypes,json,os,sys\n"
                    "flags=[sys.flags.isolated,sys.flags.no_site,sys.flags.dont_write_bytecode]\n"
                    "open('/work/initial','w').write(json.dumps(flags))\n"
                    "environment={key:value for key,value in os.environ.items() if key!='PYTHONDONTWRITEBYTECODE'}\n"
                    "argv=" + repr(["/usr/bin/python3", "-S", "-c", replacement]) + "\n"
                    "vector=(ctypes.c_char_p*(len(argv)+1))(*(value.encode() for value in argv),None)\n"
                    "envp=(ctypes.c_char_p*(len(environment)+1))("
                    "*(f'{key}={value}'.encode() for key,value in environment.items()),None)\n"
                    "libc=ctypes.CDLL(None)\n" + operation + "\n"
                ))
                markers = {}
                session = self.session()
                with session:
                    run = session._sandbox_run
                    def observing(root, **kwargs):
                        try:
                            return run(root, **kwargs)
                        finally:
                            for path in session.base.glob("command-*/output/*"):
                                markers[path.name] = json.loads(path.read_bytes())
                    with patch.object(session, "_sandbox_run", observing):
                        command = Command(("/usr/bin/python3", "/repo/launch.py"), code=("launch.py",))
                        if rejected is None:
                            self.assertEqual(session.command(command).stdout, b"")
                        else:
                            with self.assertRaisesRegex(MakeProbeError, rejected):
                                session.command(command)
                    self.assertEqual(markers, {"initial": [1, 1, 1]})
                self.assert_clean(session)

    def test_registered_native_reexec_and_inherited_entry_paths_reject(self):
        self.add("reexec.c", r'''
#define _GNU_SOURCE
#include <fcntl.h>
#include <sched.h>
#include <signal.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
static int mark(const char *path) {
    int fd=open(path,O_CREAT|O_WRONLY,0600);
    if(fd<0) return 1;
    return write(fd,"owned",5)!=5 || close(fd);
}
static int launch(void *unused) {
    char *args[]={"/native/tool","child",0};
    (void)unused;
    execve(args[0],args,environ);
    _exit(7);
}
int main(int argc,char **argv) {
    int status,fd; pid_t child;
    char *args[]={"/native/tool","child",0};
    if(argc!=2) return 1;
    if(!strcmp(argv[1],"child")) return mark("/work/reexecuted");
    if(mark("/work/initial")) return 2;
    if(!strcmp(argv[1],"initial")) return 0;
    if(!strcmp(argv[1],"execve")) return launch(0);
    if(!strcmp(argv[1],"execveat")) {
        syscall(SYS_execveat,AT_FDCWD,args[0],args,environ,0); return 3;
    }
    if(!strcmp(argv[1],"execveat-fd")) {
        fd=open(args[0],O_RDONLY);
        if(fd<0) return 3;
        syscall(SYS_execveat,fd,"",args,environ,AT_EMPTY_PATH); return 3;
    }
    if(!strcmp(argv[1],"fork")) child=fork();
    else if(!strcmp(argv[1],"vfork")) child=vfork();
    else if(!strcmp(argv[1],"clone-vfork")) {
        void *stack=mmap(0,65536,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        if(stack==MAP_FAILED) return 4;
        child=clone(launch,(char *)stack+65536,CLONE_VM|CLONE_VFORK|SIGCHLD,0);
    } else return 4;
    if(child<0) return 5;
    if(!child) return launch(0);
    if(waitpid(child,&status,0)!=child || status) return 6;
    return 0;
}
''')
        for entry in ("initial", "execve", "fork", "vfork", "clone-vfork", "execveat", "execveat-fd"):
            with self.subTest(entry=entry):
                markers = {}
                session = self.session()
                with session:
                    tool = session.compile_native(("reexec.c",))
                    run = session._sandbox_run
                    def observing(root, **kwargs):
                        try:
                            return run(root, **kwargs)
                        finally:
                            for path in session.base.glob("command-*/output/*"):
                                markers[path.name] = path.read_bytes()
                    with patch.object(session, "_sandbox_run", observing):
                        if entry == "initial":
                            self.assertEqual(session.native(tool, (entry,)).stdout, b"")
                        else:
                            expected = "unadmitted syscall 322" if entry.startswith("execveat") else "post-bootstrap exec"
                            with self.assertRaisesRegex(MakeProbeError, expected):
                                session.native(tool, (entry,))
                    self.assertEqual(markers, {"initial": b"owned"})
                self.assert_clean(session)

    def test_trusted_startup_vectors_exclude_owned_prefix_hooks(self):
        prefix = self.directory / "owned-python"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
        site = next(prefix.glob("lib/python*/site-packages"))
        markers = [self.directory / "pth-ran", self.directory / "sitecustomize-ran"]
        (site / "owned_hook.pth").write_text(
            "import sys,builtins; builtins.open(" + repr(str(markers[0]))
            + ",'w').write('owned pth'); sys.path.insert(0," + repr(str(site)) + ")\n"
        )
        (site / "sitecustomize.py").write_text(
            "open(" + repr(str(markers[1])) + ",'w').write('owned sitecustomize')\n"
        )
        recipe = subprocess.check_output(
            ["/usr/bin/make", "--no-print-directory", "-n", "-f",
             "scripts/validation_ownership/foundation.mk", "ownership-probe-check"],
            cwd=ROOT, env=ENVIRONMENT, text=True,
        )
        vectors = {"make-entry": shlex.split(recipe.strip())}
        documentation = (ROOT / "docs/ownership-probe-foundation.md").read_text()
        vectors["documented-entry"] = shlex.split(next(
            line for line in documentation.splitlines()
            if line.startswith("/usr/bin/python3 ") and "isolated_launcher.py" in line
        ))
        original = subprocess.Popen
        launched = []
        def recording(argv, *args, **kwargs):
            launched.append(list(argv))
            return original(argv, *args, **kwargs)
        self.add("Makefile", "all: ;\n")
        with patch("subprocess.Popen", recording):
            with self.session() as session:
                run = session._sandbox_run
                def capsule(root, **kwargs):
                    if kwargs["argv"][0] == "/usr/bin/python3":
                        vectors["registered-python"] = list(kwargs["argv"])
                    return run(root, **kwargs)
                with patch.object(session, "_sandbox_run", capsule):
                    session.command(Command(("/usr/bin/python3", "-c", "print('registered')")))
        self.assert_clean(session)
        for filename in ("lifecycle.py", "sandbox_exec.py"):
            path = str(TRUSTED_ROOT / filename)
            argv = next(argv for argv in launched if path in argv)
            script = argv.index(path)
            interpreter = max(index for index in range(script) if argv[index] == "/usr/bin/python3")
            vectors[filename] = argv[interpreter:script + 1]
        version = next(argv for argv in launched if any(
            argument.startswith("import sys; print('%d.%d'") for argument in argv
        ))
        query = next(index for index, argument in enumerate(version) if argument.startswith("import sys; print('%d.%d'"))
        interpreter = max(index for index in range(query) if version[index] == "/usr/bin/python3")
        vectors["version-query"] = version[interpreter:query + 1]
        for name, argv in vectors.items():
            with self.subTest(entry=name):
                tail = argv[1:] + (["--help"] if name in {"make-entry", "documented-entry"} else [])
                for no_site in (False, True):
                    for marker in markers:
                        marker.unlink(missing_ok=True)
                    options = tail if no_site else [argument for argument in tail if argument != "-S"]
                    result = subprocess.run(
                        [str(prefix / "bin/python"), *options], cwd=ROOT, env=ENVIRONMENT,
                        capture_output=True, timeout=15,
                    )
                    self.assertEqual([marker.exists() for marker in markers], [not no_site]*2, result.stderr)
                    if no_site and name not in {"lifecycle.py", "sandbox_exec.py"}:
                        self.assertEqual(result.returncode, 0, result.stderr)
                    if not no_site and name in {"make-entry", "documented-entry", "lifecycle.py", "sandbox_exec.py"}:
                        self.assertNotEqual(result.returncode, 0)

    def test_session_teardown_defers_signals_until_owned_state_is_removed(self):
        self.add("Makefile", "all: ;\n")
        for signum in (signal.SIGINT, signal.SIGTERM):
            for has_primary in (False, True):
                with self.subTest(signal=signum, primary=has_primary):
                    session = self.session()
                    primary = MakeProbeError("owned primary failure") if has_primary else None
                    seen = []
                    base = None
                    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
                    previous_handler = signal.getsignal(signum)
                    def caller_handler(received, frame):
                        seen.append((
                            received, session.base is None, not self.scratch.exists(),
                            not session.cache, not session.budget.children,
                            signal.pthread_sigmask(signal.SIG_BLOCK, ()) == previous_mask,
                        ))
                        raise RuntimeError("owned deferred termination")
                    signal.signal(signum, caller_handler)
                    remove = shutil.rmtree
                    def interrupted_remove(path, *args, **kwargs):
                        if base is not None and Path(path) == base:
                            os.kill(os.getpid(), signum)
                        return remove(path, *args, **kwargs)
                    try:
                        with self.assertRaises(MakeProbeError if has_primary else RuntimeError) as caught:
                            with patch("shutil.rmtree", interrupted_remove):
                                with session:
                                    base = session.base
                                    session.command(Command(("/usr/bin/printf", "cached")))
                                    if primary is not None:
                                        raise primary
                        if has_primary:
                            self.assertIs(caught.exception, primary)
                            self.assertTrue(primary.cleanup_errors)
                        self.assertEqual(seen, [(signum, True, True, True, True, True)])
                        self.assertIs(signal.getsignal(signum), caller_handler)
                        self.assert_clean(session)
                    finally:
                        signal.signal(signum, previous_handler)

    def test_cleanup_finishes_all_actions_and_replays_pending_signals_afterward(self):
        from scripts.validation_ownership.lifecycle import finish_cleanup
        files = [self.directory / "first", self.directory / "second"]
        for path in files:
            path.touch()
        primary = MakeProbeError("original operation failure")
        seen = []
        previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
        def handler(signum, frame):
            seen.append((signum, not any(path.exists() for path in files)))
            raise KeyboardInterrupt("deferred handler")
        for signum in previous:
            signal.signal(signum, handler)
        def first():
            files[0].unlink()
            for signum in previous:
                os.kill(os.getpid(), signum)
            raise OSError(errno.EIO, "owned cleanup diagnostic")
        try:
            finish_cleanup([first, files[1].unlink], primary=primary)
            self.assertEqual(set(seen), {(signal.SIGINT, True), (signal.SIGTERM, True)})
            self.assertEqual(len(primary.cleanup_errors), 3)
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, ()), previous_mask)
        finally:
            for signum, value in previous.items():
                signal.signal(signum, value)

    def test_per_call_teardown_defers_signals_and_preserves_primary_errors(self):
        self.add("Makefile", "all: ;\n")
        for boundary in ("report", "command-tree", "make-tree"):
            for failed in (False, True):
                with self.subTest(boundary=boundary, failed=failed):
                    self.add("Makefile", (
                        "$(error owned primary failure)\nall: ;\n"
                        if boundary == "make-tree" and failed else "all: ;\n"
                    ))
                    session = self.session()
                    sent = False
                    with session:
                        base = session.base
                        remove, unlink = shutil.rmtree, Path.unlink
                        def signal_once():
                            nonlocal sent
                            if not sent:
                                sent = True
                                os.kill(os.getpid(), signal.SIGTERM)
                        def removing(path, *args, **kwargs):
                            name = Path(path).name
                            if boundary == "command-tree" and name.startswith("command-"):
                                signal_once()
                            if boundary == "make-tree" and name.startswith("control-"):
                                signal_once()
                            return remove(path, *args, **kwargs)
                        def unlinking(path, *args, **kwargs):
                            if boundary == "report" and path.parent == base and path.name.startswith("report-"):
                                signal_once()
                            return unlink(path, *args, **kwargs)
                        with patch("shutil.rmtree", removing), patch.object(Path, "unlink", unlinking):
                            with self.assertRaises(MakeProbeError if failed else KeyboardInterrupt) as caught:
                                if boundary == "make-tree":
                                    session.make("all")
                                else:
                                    session.command(Command((
                                        "/usr/bin/python3", "-c", "import os; os._exit(7)" if failed else "print('ok')",
                                    )))
                        self.assertTrue(sent)
                        self.assertFalse(list(base.glob("report-*.json")))
                        self.assertFalse(list(base.glob("launch-*.json")))
                        self.assertFalse(list(base.glob("command-*")))
                        self.assertFalse(list(base.glob("make-root-*")))
                        self.assertFalse(list(base.glob("control-*")))
                        self.assertFalse(session.budget.children)
                        if failed:
                            self.assertIn("confined", str(caught.exception))
                            self.assertTrue(caught.exception.cleanup_errors)
                    self.assert_clean(session)

    def test_partial_call_setup_interruption_removes_preallocated_owned_paths(self):
        self.add("Makefile", "all: ;\n")
        for make in (False, True):
            with self.subTest(make=make):
                session = self.session()
                with session:
                    def partial(name, **kwargs):
                        (session.base / name).mkdir()
                        os.kill(os.getpid(), signal.SIGINT)
                    with patch.object(session, "_new_root", partial):
                        with self.assertRaises(KeyboardInterrupt):
                            session.make("all") if make else session.command(Command(("/usr/bin/printf", "ok")))
                    self.assertFalse(list(session.base.glob("*root-*")))
                    self.assertFalse(list(session.base.glob("command-*")))
                    self.assertFalse(list(session.base.glob("control-*")))
                self.assert_clean(session)

    def test_setup_failure_remains_primary_during_deferred_exit_signal(self):
        self.add("Makefile", "all: ;\n")
        session = self.session()
        primary = MakeProbeError("owned setup failure")
        seen = []
        previous = signal.getsignal(signal.SIGTERM)
        def handler(signum, frame):
            seen.append(session.base is None and not self.scratch.exists())
            raise KeyboardInterrupt("deferred setup exit")
        signal.signal(signal.SIGTERM, handler)
        remove = shutil.rmtree
        def removing(path, *args, **kwargs):
            if session.base is not None and Path(path) == session.base:
                os.kill(os.getpid(), signal.SIGTERM)
            return remove(path, *args, **kwargs)
        try:
            with patch.object(session, "_tools", side_effect=primary), patch("shutil.rmtree", removing):
                with self.assertRaises(MakeProbeError) as caught:
                    with session:
                        self.fail("failed setup entered")
            self.assertIs(caught.exception, primary)
            self.assertEqual(seen, [True])
            self.assertTrue(primary.cleanup_errors)
            self.assert_clean(session)
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_budget_cleanup_defers_signal_until_children_and_pipes_are_closed(self):
        for has_primary in (False, True):
            with self.subTest(primary=has_primary):
                budget = ProbeBudget(Limits(process_output_bytes=8 if has_primary else 1024))
                stopped = []
                observed = []
                descriptors = set(os.listdir("/proc/self/fd"))
                previous = signal.getsignal(signal.SIGINT)
                def handler(signum, frame):
                    child = stopped[0]
                    observed.append((
                        not budget.children, child.returncode is not None,
                        child.stdin.closed and child.stdout.closed and child.stderr.closed,
                        set(os.listdir("/proc/self/fd")) == descriptors,
                    ))
                    raise KeyboardInterrupt("owned budget teardown signal")
                signal.signal(signal.SIGINT, handler)
                stop = budget._terminate
                def stopping(child, privileged=False):
                    stopped.append(child)
                    os.kill(os.getpid(), signal.SIGINT)
                    stop(child, privileged)
                try:
                    with patch.object(budget, "_terminate", stopping):
                        with self.assertRaises(MakeProbeError if has_primary else KeyboardInterrupt) as caught:
                            budget.run(
                                ["/usr/bin/python3", "-I", "-S", "-c",
                                 "import os; os.read(0,10); os.write(1,b'x'*100)"],
                                env=ENVIRONMENT, input_data=b"owned",
                            )
                    self.assertEqual(observed, [(True, True, True, True)])
                    self.assertTrue(budget.failed)
                    if has_primary:
                        self.assertIn("output exceeds", str(caught.exception))
                        self.assertTrue(caught.exception.cleanup_errors)
                finally:
                    signal.signal(signal.SIGINT, previous)

    def test_cleanup_preserves_a_callers_already_blocked_signal_mask(self):
        from scripts.validation_ownership.lifecycle import finish_cleanup
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        previous_handler = signal.getsignal(signal.SIGTERM)
        seen = []
        signal.signal(signal.SIGTERM, lambda signum, frame: seen.append(signum))
        path = self.directory / "owned"
        path.touch()
        def removing():
            os.kill(os.getpid(), signal.SIGTERM)
            path.unlink()
        try:
            finish_cleanup([removing])
            self.assertFalse(path.exists())
            self.assertEqual(seen, [])
            self.assertIn(signal.SIGTERM, signal.sigpending())
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, ()), previous_mask | {signal.SIGTERM})
        finally:
            signal.sigtimedwait({signal.SIGTERM}, 0)
            signal.signal(signal.SIGTERM, previous_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def test_budget_payload_does_not_inherit_the_temporary_setup_mask(self):
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
        try:
            for added in (set(), {signal.SIGUSR1}):
                expected = previous_mask | added
                signal.pthread_sigmask(signal.SIG_SETMASK, expected)
                budget = ProbeBudget()
                result = budget.run(
                    ["/usr/bin/python3", "-I", "-S", "-c",
                     "import json,signal; print(json.dumps(sorted(signal.pthread_sigmask(signal.SIG_BLOCK,()))))"],
                    env=ENVIRONMENT,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), sorted(expected))
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, ()), expected)
                self.assertFalse(budget.children)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def test_watchdog_teardown_delivers_signal_after_sole_reaping(self):
        program = r'''
import os,signal,sys,time
sys.path.insert(0,sys.argv[1])
from scripts.validation_ownership import lifecycle
seen=[]
state={}
def handler(signum,frame):
    assert state["child"].returncode == 0
    seen.append(signum)
    raise RuntimeError("deferred caller signal")
signal.signal(signal.SIGTERM,handler)
original=lifecycle.terminate
def terminating(child):
    state["child"]=child
    os.kill(os.getpid(),signal.SIGTERM)
    original(child)
lifecycle.terminate=terminating
try:
    lifecycle.run(["/usr/bin/true"],time.monotonic()+5)
except RuntimeError as error:
    assert str(error) == "deferred caller signal"
else:
    raise AssertionError("termination was ignored")
assert seen == [signal.SIGTERM]
assert lifecycle.owned_children() == []
print("delivered after reaping")
'''
        with self.owned_process(["/usr/bin/python3", "-I", "-S", "-c", program, str(ROOT)]) as (child, descriptor):
            child.wait(timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr.read())
            self.assertEqual(child.stdout.read(), b"delivered after reaping\n")

    def test_default_termination_is_delivered_only_after_owned_session_removal(self):
        self.add("Makefile", "all: ;\n")
        program = r'''
import os,shutil,signal,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts.validation_ownership.authority import AuthorityLoader,GitTreeEntries,GitTreeEntry
from scripts.validation_ownership.budget import ProbeBudget
from scripts.validation_ownership.make_probe import ProbeSession
root,scratch=map(Path,sys.argv[2:4])
budget=ProbeBudget()
entries=GitTreeEntries({"Makefile":GitTreeEntry("Makefile","100644","blob","0"*40)},budget=budget)
session=ProbeSession(AuthorityLoader(root,entries,budget=budget),scratch_root=scratch,budget=budget)
# This control targets real file/state teardown and the default OS signal
# action; tool/namespace execution is exercised by the other process cases.
session._tools=lambda:None
session._compile_interceptor=lambda:None
signal.signal(signal.SIGTERM,signal.SIG_DFL)
original=shutil.rmtree
def removing(path,*args,**kwargs):
    if session.base is not None and Path(path)==session.base:
        os.kill(os.getpid(),signal.SIGTERM)
    return original(path,*args,**kwargs)
shutil.rmtree=removing
with session:
    pass
raise AssertionError("default termination was lost")
'''
        with self.owned_process([
            "/usr/bin/python3", "-I", "-S", "-c", program, str(ROOT), str(self.root), str(self.scratch),
        ]) as (child, descriptor):
            child.wait(timeout=10)
            self.assertEqual(child.returncode, -signal.SIGTERM, child.stderr.read())
        self.assertFalse(self.scratch.exists())
        self.assertEqual((self.root / "Makefile").read_text(), "all: ;\n")

    def traced_observation(
        self, number, arguments, descriptors, *, buffer=None, mode="command",
        observation_limit=1024*1024,
    ):
        from scripts.validation_ownership.syscall_guard import (
            GETREGS, SETOPTIONS, SYSCALL, Policy, Process, Registers, memory, ptrace, signed, trace_me,
        )
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        def operation():
            os.chdir(self.root)
            trace_me(lambda: None)
            libc.syscall(ctypes.c_long(number), *(ctypes.c_ulong(value & ((1 << 64)-1)) for value in arguments))
        with self.stopped_tracee(operation) as pid:
            ptrace(SETOPTIONS, pid, 0, 0x100001)
            policy = Policy({
                "root": str(self.directory), "mode": mode, "code": ["code.bin"],
                "sources": ["data/a", "data/b"], "enumerations": ["data"],
                "executables": [], "python_version": "3.12", "argv": [],
                "memory_limit": 256*1024*1024, "syscall_limit": 1024,
                "write_limit": 1024*1024, "observation_count": 1024,
                "observation_limit": observation_limit, "forbidden_paths": [],
            })
            state = Process("make" if mode == "make" else "command", memory_group=pid)
            state.fds.update(descriptors)
            policy.processes[pid] = state
            entered = False
            for _ in range(256):
                ptrace(SYSCALL, pid)
                waited, status = os.waitpid(pid, 0)
                self.assertEqual(waited, pid)
                self.assertTrue(os.WIFSTOPPED(status), status)
                registers = Registers()
                ptrace(GETREGS, pid, 0, ctypes.byref(registers))
                information = (ctypes.c_ubyte * 128)()
                ptrace(0x420E, pid, len(information), ctypes.byref(information))
                actual = (registers.rdi, registers.rsi, registers.rdx, registers.r10, registers.r8, registers.r9)
                expected = tuple(value & ((1 << 64)-1) for value in arguments)
                if information[0] == 1 and registers.orig_rax == number and actual[:len(expected)] == expected:
                    policy.entry(pid, state, registers)
                    self.assertFalse(policy.consumed)
                    self.assertFalse(policy.code_consumed)
                    self.assertFalse(policy.accessed)
                    entered = True
                elif information[0] == 2 and entered:
                    result = signed(registers.rax)
                    policy.leave(pid, state, registers)
                    return {
                        "result": result, "consumed": set(policy.consumed),
                        "code": set(policy.code_consumed), "accessed": set(policy.accessed),
                        "data": memory(pid, buffer, result) if buffer is not None and result > 0 else b"",
                        "fds": dict(state.fds),
                    }
            self.fail("owned syscall did not reach its exit")

    def test_failed_open_and_metadata_cannot_forge_registry_consumption(self):
        self.add("data/a", b"owned")
        operations = (
            "os.open('data/a',os.O_RDONLY|os.O_DIRECTORY)",
            "libc.syscall(2,b'data/a',os.O_RDONLY|os.O_DIRECTORY,0)",
            "libc.syscall(257,-100,b'data/a',os.O_RDONLY|os.O_DIRECTORY,0)",
            "libc.syscall(4,b'data/a',ctypes.c_void_p(1))",
            "libc.syscall(6,b'data/a',ctypes.c_void_p(1))",
            "libc.syscall(262,-100,b'data/a',ctypes.c_void_p(1),0)",
            "libc.syscall(332,-100,b'data/a',0,0x7ff,ctypes.c_void_p(1))",
            "libc.syscall(21,b'data/a',os.W_OK)",
            "libc.syscall(269,-100,b'data/a',os.W_OK)",
            "libc.syscall(439,-100,b'data/a',os.W_OK,0)",
            "os.readlink('data/a')",
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes,json,os\nlibc=ctypes.CDLL(None,use_errno=True)\n"
                    "try:\n result=" + operation + "\n assert result == -1\nexcept OSError:\n pass\n"
                    "print(json.dumps({'name':'forged','version':1,'record_count':1,'source_paths':['data/a']}))\n"
                ))
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "declared/consumed source mismatch"):
                    with session:
                        session.registry(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                            code=("reader.py",), sources=("data/a",),
                        ))
                self.assert_clean(session)

    def test_successful_metadata_open_and_content_remain_source_observations(self):
        self.add("data/a", b"owned")
        operations = (
            "os.close(os.open('data/a',os.O_RDONLY))",
            "os.close(os.open('data/a',os.O_PATH))",
            "assert os.stat('data/a').st_size == 5",
            "assert os.lstat('data/a').st_size == 5",
            "assert os.access('data/a',os.R_OK)",
            "assert open('data/a','rb').read(1) == b'o'",
            "fd=os.open('data/a',os.O_RDONLY)\nassert os.fstat(fd).st_size == 5\nos.close(fd)",
            "fd=os.open('data/a',os.O_RDONLY)\nview=mmap.mmap(fd,0,access=mmap.ACCESS_READ)\n"
            "assert view[:1] == b'o'\nview.close()\nos.close(fd)",
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import json,mmap,os\n" + operation + "\n"
                    "print(json.dumps({'name':'observed','version':1,'record_count':1,'source_paths':['data/a']}))\n"
                ))
                with self.session() as session:
                    self.assertEqual(session.registry(Command(
                        ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                        code=("reader.py",), sources=("data/a",),
                    ))["source_paths"], ["data/a"])
                self.assert_clean(session)

    def test_failed_and_partial_getdents_cannot_credit_unreturned_siblings(self):
        self.add("data/a", b"a")
        self.add("data/b", b"b")
        for number in (78, 217):
            for count in (1, 24):
                with self.subTest(number=number, count=count):
                    self.add("reader.py", (
                        "import ctypes,json,os\nlibc=ctypes.CDLL(None)\n"
                        "buffer=ctypes.create_string_buffer(24)\n"
                        "directory=os.open('data',os.O_RDONLY|os.O_DIRECTORY)\n"
                        f"size=libc.syscall({number},directory,buffer,{count})\n"
                        "os.write(2,buffer.raw[:size].hex().encode() if size>0 else b'')\n"
                        "os.close(directory)\n"
                        "print(json.dumps({'name':'forged','version':1,'record_count':2,"
                        "'source_paths':['data/a','data/b']}))\n"
                    ))
                    session = self.session()
                    reports = []
                    with session:
                        original = session._sandbox_run
                        def recording(root, **kwargs):
                            result, observed = original(root, **kwargs)
                            reports.append((result, observed))
                            return result, observed
                        with patch.object(session, "_sandbox_run", recording):
                            with self.assertRaisesRegex(MakeProbeError, "declared/consumed source mismatch"):
                                session.registry(Command(
                                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                                    code=("reader.py",), sources=("data/a", "data/b"), directories=("data",),
                                ))
                        raw = bytes.fromhex(reports[-1][0].stderr.decode())
                        names = set()
                        if raw:
                            self.assertEqual(len(raw), 24)
                            name = raw[19 if number == 217 else 18:].split(b"\0", 1)[0].decode()
                            if name in {"a", "b"}:
                                names.add("data/" + name)
                        self.assertEqual(set(reports[-1][1]["consumed"]), names)
                        self.assertLess(len(names), 2)
                    self.assert_clean(session)

    def test_fd_read_metadata_mmap_and_dup_credit_only_successful_exits(self):
        self.add("data/a", b"alpha")
        buffer = ctypes.create_string_buffer(256)
        address = ctypes.addressof(buffer)
        descriptor = os.open(self.root / "data/a", os.O_RDONLY)
        try:
            class Vector(ctypes.Structure):
                _fields_ = [("address", ctypes.c_void_p), ("length", ctypes.c_size_t)]
            vector = Vector(address, 1)
            failures = (
                (0, (descriptor, 1, 1)), (0, (descriptor, address, 0)),
                (17, (descriptor, address, 1, 1000)), (19, (descriptor, 0, 1)),
                (5, (descriptor, 1)), (138, (descriptor, 1)),
                (8, (descriptor, -1, os.SEEK_SET)), (292, (descriptor, descriptor, 0)),
                (72, (descriptor, 0, 0x7fffffff)),
                (9, (0, 0, 1, 2, descriptor, 0)), (9, (0, 4096, 1, 2, descriptor, 1)),
            )
            for number, arguments in failures:
                with self.subTest(number=number, arguments=arguments):
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    observed = self.traced_observation(number, arguments, {descriptor: "/repo/data/a"})
                    self.assertLessEqual(observed["result"], 0)
                    self.assertEqual(observed["consumed"], set())
            successes = (
                (0, (descriptor, address, 1)), (17, (descriptor, address, 1, 0)),
                (19, (descriptor, ctypes.addressof(vector), 1)),
                (5, (descriptor, address)), (138, (descriptor, address)),
                (8, (descriptor, 0, os.SEEK_END)), (32, (descriptor,)),
                (72, (descriptor, 0, 10)), (9, (0, 4096, 1, 2, descriptor, 0)),
            )
            for number, arguments in successes:
                with self.subTest(number=number, arguments=arguments):
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    observed = self.traced_observation(number, arguments, {descriptor: "/repo/data/a"})
                    self.assertGreaterEqual(observed["result"], 0)
                    self.assertEqual(observed["consumed"], {"data/a"})
            closed = self.traced_observation(3, (descriptor,), {descriptor: "/repo/data/a"})
            self.assertEqual(closed["result"], 0)
            self.assertNotIn(descriptor, closed["fds"])
            self.assertEqual(closed["consumed"], set())
        finally:
            os.close(descriptor)

    def test_getdents_credits_returned_names_and_not_failure_eof_or_tail(self):
        from scripts.validation_ownership.syscall_guard import directory_entries
        self.add("data/a", b"a")
        self.add("data/b", b"b")
        libc = ctypes.CDLL(None)
        buffer = ctypes.create_string_buffer(4096)
        address = ctypes.addressof(buffer)
        for number in (78, 217):
            descriptor = os.open(self.root / "data", os.O_RDONLY | os.O_DIRECTORY)
            try:
                failures = ((descriptor, address, 1), (descriptor, 1, 4096))
                for arguments in failures:
                    observed = self.traced_observation(number, arguments, {descriptor: "/repo/data"})
                    self.assertLess(observed["result"], 0)
                    self.assertEqual(observed["consumed"], set())
                os.lseek(descriptor, 0, os.SEEK_SET)
                partial = self.traced_observation(
                    number, (descriptor, address, 24), {descriptor: "/repo/data"}, buffer=address,
                )
                expected = {"data/" + name for name in directory_entries(partial["data"], wide=number == 217)}
                self.assertEqual(partial["consumed"], expected)
                self.assertLess(len(expected), 2)
                os.lseek(descriptor, 0, os.SEEK_SET)
                complete = self.traced_observation(
                    number, (descriptor, address, 4096), {descriptor: "/repo/data"}, buffer=address,
                )
                self.assertEqual(complete["consumed"], {"data/a", "data/b"})
                # Populate the caller buffer with old valid records, but set the
                # shared directory offset to EOF before observation begins.
                ctypes.memmove(address, complete["data"], len(complete["data"]))
                while libc.syscall(number, descriptor, buffer, 4096) > 0:
                    pass
                eof = self.traced_observation(
                    number, (descriptor, address, 4096), {descriptor: "/repo/data"}, buffer=address,
                )
                self.assertEqual(eof["result"], 0)
                self.assertEqual(eof["consumed"], set())
            finally:
                os.close(descriptor)

    def test_code_and_make_path_observations_wait_for_success(self):
        self.add("code.bin", b"code")
        buffer = ctypes.create_string_buffer(256)
        descriptor = os.open(self.root / "code.bin", os.O_RDONLY)
        try:
            for mode, collection, expected in (
                ("command", "code", {"code.bin"}),
                ("make", "accessed", {"/repo/code.bin"}),
            ):
                with self.subTest(mode=mode):
                    failed = self.traced_observation(
                        5, (descriptor, 1), {descriptor: "/repo/code.bin"}, mode=mode,
                    )
                    self.assertLess(failed["result"], 0)
                    self.assertEqual(failed[collection], set())
                    success = self.traced_observation(
                        5, (descriptor, ctypes.addressof(buffer)), {descriptor: "/repo/code.bin"}, mode=mode,
                    )
                    self.assertEqual(success["result"], 0)
                    self.assertEqual(success[collection], expected)
        finally:
            os.close(descriptor)

    def test_directory_names_do_not_credit_nested_or_symlink_referents(self):
        self.add("code.bin", b"code")
        self.add("data/a", b"a")
        self.add("data/b", b"b")
        (self.root / "alias").symlink_to("data/a")
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        buffer = ctypes.create_string_buffer(4096)
        try:
            observed = self.traced_observation(
                217, (descriptor, ctypes.addressof(buffer), 4096),
                {descriptor: "/repo"}, buffer=ctypes.addressof(buffer),
            )
            self.assertGreater(observed["result"], 0)
            self.assertEqual(observed["code"], {"code.bin"})
            self.assertEqual(observed["consumed"], set())
        finally:
            os.close(descriptor)

    def test_directory_observation_transfer_and_buffer_limits_are_bounded(self):
        from scripts.validation_ownership.syscall_guard import SYSCALL_MEMORY_LIMIT, Violation
        self.add("data/a", b"a")
        self.add("data/b", b"b")
        buffer = ctypes.create_string_buffer(SYSCALL_MEMORY_LIMIT + 1)
        descriptor = os.open(self.root / "data", os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaisesRegex(Violation, "syscall memory bound"):
                self.traced_observation(
                    217, (descriptor, ctypes.addressof(buffer), len(buffer)),
                    {descriptor: "/repo/data"},
                )
            with self.assertRaisesRegex(Violation, "directory-observation byte budget"):
                self.traced_observation(
                    217, (descriptor, ctypes.addressof(buffer), 4096),
                    {descriptor: "/repo/data"}, observation_limit=1,
                )
        finally:
            os.close(descriptor)

    def test_directory_parser_rejects_malformed_actual_records(self):
        from scripts.validation_ownership.syscall_guard import Violation, directory_entries
        self.add("data/a", b"a")
        self.add("data/b", b"b")
        buffer = ctypes.create_string_buffer(4096)
        for number in (78, 217):
            descriptor = os.open(self.root / "data", os.O_RDONLY | os.O_DIRECTORY)
            try:
                observed = self.traced_observation(
                    number, (descriptor, ctypes.addressof(buffer), 4096),
                    {descriptor: "/repo/data"}, buffer=ctypes.addressof(buffer),
                )
            finally:
                os.close(descriptor)
            data = observed["data"]
            self.assertEqual(set(directory_entries(data, wide=number == 217)), {"a", "b"})
            invalid_length = bytearray(data)
            invalid_length[16:18] = b"\0\0"
            invalid_name = bytearray(data)
            invalid_name[19 if number == 217 else 18] = 0xff
            for malformed in (data[:-1], bytes(invalid_length), bytes(invalid_name)):
                with self.assertRaises(Violation):
                    directory_entries(malformed, wide=number == 217)

    @staticmethod
    def signal_sender_source():
        return r'''
#define _GNU_SOURCE
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
static long send_signal(int operation, pid_t target) {
    siginfo_t info;
    memset(&info,0,sizeof(info));
    info.si_signo=SIGUSR1; info.si_code=SI_QUEUE;
    info.si_pid=getpid(); info.si_uid=getuid(); info.si_value.sival_int=42;
    switch(operation) {
    case SYS_kill: return syscall(SYS_kill,target,SIGUSR1);
    case SYS_tkill: return syscall(SYS_tkill,target,SIGUSR1);
    case SYS_tgkill: return syscall(SYS_tgkill,target,target,SIGUSR1);
    case SYS_rt_sigqueueinfo: return syscall(SYS_rt_sigqueueinfo,target,SIGUSR1,&info);
    case SYS_rt_tgsigqueueinfo: return syscall(SYS_rt_tgsigqueueinfo,target,target,SIGUSR1,&info);
    default: return -1;
    }
}
static int receive_signal(int operation, const sigset_t *mask) {
    siginfo_t info; struct timespec timeout={2,0};
    if(sigtimedwait(mask,&info,&timeout)!=SIGUSR1) return 4;
    if((operation==SYS_rt_sigqueueinfo || operation==SYS_rt_tgsigqueueinfo)
       && info.si_value.sival_int!=42) return 5;
    return write(1,"delivered\n",10)==10 ? 0 : 6;
}
int main(int argc,char **argv) {
    int operation, ready[2], status; char byte; sigset_t mask;
    if(argc!=3) return 1;
    operation=atoi(argv[1]); sigemptyset(&mask); sigaddset(&mask,SIGUSR1);
    if(sigprocmask(SIG_BLOCK,&mask,0)) return 2;
    if(!strcmp(argv[2],"self")) {
        if(send_signal(operation,getpid())) return 3;
        return receive_signal(operation,&mask);
    }
    if(pipe(ready)) return 7;
    pid_t child=fork(); if(child<0) return 8;
    if(!child) {
        if(write(ready[1],"R",1)!=1) _exit(9);
        _exit(receive_signal(operation,&mask));
    }
    if(read(ready[0],&byte,1)!=1 || send_signal(operation,child)) return 10;
    if(waitpid(child,&status,0)!=child || status) return 11;
    return 0;
}
'''

    def test_pid_signal_families_allow_self_delivery_but_reject_owned_siblings(self):
        self.add("signals.c", self.signal_sender_source())
        operations = (62, 129, 200, 234, 297)
        with self.session() as session:
            tool = session.compile_native(("signals.c",))
            for operation in operations:
                with self.subTest(operation=operation, target="self"):
                    self.assertEqual(session.native(tool, (str(operation), "self")).stdout, b"delivered\n")
        self.assert_clean(session)
        for operation in operations:
            with self.subTest(operation=operation, target="owned sibling"):
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "cross-process signal target"):
                    with session:
                        tool = session.compile_native(("signals.c",))
                        session.native(tool, (str(operation), "other"))
                self.assert_clean(session)

    def test_signal_policy_rejects_group_broadcast_and_mixed_thread_targets(self):
        from scripts.validation_ownership.syscall_guard import Process, Registers, Violation
        with self.owned_process([
            "/usr/bin/python3", "-I", "-S", "-c", "import os; os.read(0,1)",
        ]) as (recipient, descriptor):
            sender = os.getpid()
            policy = self.memory_policy(64*1024*1024)
            policy.config.update(syscall_limit=100, write_limit=1024)
            state = Process("command")
            for operation in (62, 129, 200, 234, 297):
                targets = [(target, target) for target in (0, -1, -recipient.pid, recipient.pid)]
                if operation in (234, 297):
                    targets += [(sender, recipient.pid), (recipient.pid, sender), (sender, 0), (0, sender)]
                for group, thread in targets:
                    with self.subTest(operation=operation, group=group, thread=thread):
                        registers = Registers(
                            orig_rax=operation, rdi=group & ((1 << 64)-1),
                            rsi=thread & ((1 << 64)-1),
                        )
                        # Exercise real policy only; group/broadcast requests
                        # never reach a kernel, even in the negative control.
                        with self.assertRaisesRegex(Violation, "cross-process signal target"):
                            policy.entry(sender, state, registers)
            self.assertIsNone(recipient.poll())

    def test_descriptor_async_and_timer_signal_routes_remain_unadmitted(self):
        controls = [
            ("libc.syscall(424,-1,signal.SIGUSR1,0,0)", "pidfd signal"),
            ("libc.syscall(434,-1,0)", "unadmitted syscall"),
            ("libc.syscall(438,-1,-1,0)", "unadmitted syscall"),
            ("libc.syscall(222,-1,0,0)", "unadmitted syscall"),
            ("libc.syscall(223,-1,0,0,0)", "unadmitted syscall"),
            ("libc.syscall(244,-1,0)", "unadmitted syscall"),
            ("fcntl.fcntl(pipe[0],fcntl.F_SETOWN,os.getpid())", "unknown fcntl"),
            ("fcntl.fcntl(pipe[0],10,signal.SIGUSR1)", "unknown fcntl"),
            ("fcntl.fcntl(pipe[0],15,struct.pack('ii',1,os.getpid()))", "unknown fcntl"),
            ("fcntl.fcntl(pipe[0],1026,0)", "unknown fcntl"),
            ("libc.syscall(16,pipe[0],0x40045436,signal.SIGUSR1)", "unknown ioctl"),
        ]
        for operation, expected in controls:
            with self.subTest(operation=operation):
                program = (
                    "import ctypes,fcntl,os,signal,struct\nlibc=ctypes.CDLL(None)\npipe=os.pipe()\n"
                    "try:\n " + operation + "\nexcept OSError:\n pass\n"
                )
                self.add("signal_route.py", program)
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-S", "/repo/signal_route.py"),
                            code=("signal_route.py",),
                        ))
                self.assert_clean(session)

    def registry_fixture(self, name):
        self.add("registry.py", (
            "import json,sys\nopen('/work/entry','wb').close()\n"
            "print(json.dumps({'name':sys.argv[1],'version':1,"
            "'record_count':1,'source_paths':[]}))\n"
        ))
        return Command(
            ("/usr/bin/python3", "-I", "-B", "/repo/registry.py", name), code=("registry.py",),
        )

    def test_registry_helper_requires_an_explicit_active_report_session(self):
        command = self.registry_fixture("first")
        inactive = self.session()
        loader = inactive.loader
        with patch.object(ProbeSession, "__enter__", side_effect=AssertionError("implicit session")) as enter:
            with self.assertRaises(TypeError):
                probe_generated_registry(loader, command=command)
            with self.assertRaises(TypeError):
                probe_generated_registry(loader, scratch_root=self.scratch, command=command)
            for session in (None, object(), inactive):
                with self.subTest(session=session):
                    with self.assertRaisesRegex(MakeProbeError, "active ProbeSession"):
                        probe_generated_registry(loader, command=command, session=session)
            enter.assert_not_called()
        self.assertFalse(self.scratch.exists())
        with self.session() as closed:
            pass
        self.assert_clean(closed)
        with self.assertRaisesRegex(MakeProbeError, "active ProbeSession"):
            probe_generated_registry(closed.loader, command=command, session=closed)

    def test_registry_helper_rejects_foreign_loader_or_budget_without_launch(self):
        command = self.registry_fixture("first")
        with self.session() as session:
            runs = session.budget.runs
            foreign = self.session().loader
            with self.assertRaisesRegex(MakeProbeError, "loader/budget differs"):
                probe_generated_registry(foreign, command=command, session=session)
            with patch.object(session.loader, "budget", ProbeBudget()):
                with self.assertRaisesRegex(MakeProbeError, "loader/budget differs"):
                    probe_generated_registry(session.loader, command=command, session=session)
            self.assertEqual(session.budget.runs, runs)
            self.assertFalse(session.cache)
        self.assert_clean(session)

    def test_registry_helper_calls_share_cache_deadline_and_creation_quota(self):
        first = self.registry_fixture("first")
        second = self.registry_fixture("second")
        with self.session(created_files=1) as session:
            budget, started, deadline = session.budget, session.budget.started, session.budget.deadline
            observed = probe_generated_registry(session.loader, command=first, session=session)
            self.assertEqual(observed["name"], "first")
            runs, processes = budget.runs, session.processes_used
            self.assertEqual(session.files_created, 1)
            self.assertEqual(probe_generated_registry(session.loader, command=first, session=session), observed)
            self.assertEqual((budget.runs, session.processes_used, session.files_created), (runs, processes, 1))
            self.assertEqual(len(session.cache), 1)
            self.assertIs(session.budget, budget)
            self.assertEqual((budget.started, budget.deadline), (started, deadline))
            with self.assertRaisesRegex(MakeProbeError, "file-creation budget"):
                probe_generated_registry(session.loader, command=second, session=session)
            with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline/budget"):
                probe_generated_registry(session.loader, command=first, session=session)
        self.assert_clean(session)

    def test_registry_helper_cannot_reuse_cache_past_the_report_deadline(self):
        command = self.registry_fixture("cached")
        with self.session() as session:
            probe_generated_registry(session.loader, command=command, session=session)
            budget = session.budget
            started, runs = budget.started, budget.runs
            budget.limits = replace(budget.limits, seconds=time.monotonic() - started + 0.02)
            deadline = budget.deadline
            time.sleep(max(0, deadline - time.monotonic()) + 0.03)
            with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
                probe_generated_registry(session.loader, command=command, session=session)
            self.assertIs(session.budget, budget)
            self.assertIs(session.loader.budget, budget)
            self.assertEqual((budget.started, budget.deadline, budget.runs), (started, deadline, runs))
        self.assert_clean(session)

    def test_make_and_registry_helper_consume_the_same_creation_budget(self):
        command = self.registry_fixture("registry")
        self.add("make_data.py", "open('/work/make-entry','wb').close()\nprint('dep')\n")
        self.add("Makefile", "VALUE := $(shell python3 -I -B make_data.py)\nall: $(VALUE)\ndep: ;\n")
        with self.session(created_files=1) as session:
            session.make("all", commands={
                "python3 -I -B make_data.py": Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/make_data.py"), code=("make_data.py",),
                ),
            })
            self.assertEqual(session.files_created, 1)
            with self.assertRaisesRegex(MakeProbeError, "file-creation budget"):
                probe_generated_registry(session.loader, command=command, session=session)
        self.assert_clean(session)

    def test_recursive_bind_restricts_submounts_and_preserves_explicit_exceptions(self):
        self.add("Makefile", "all: ;\n")
        fixture = self.directory / "mount fixture"
        for name in ("source/nested", "restricted", "runtime", "root/inherited", "root/repo", "root/control"):
            (fixture / name).mkdir(parents=True, exist_ok=True)
        (fixture / "source/value").write_bytes(b"owned")
        (fixture / "source/helper").touch()
        shutil.copyfile("/usr/bin/true", fixture / "source/program")
        (fixture / "source/program").chmod(0o755)
        program = r'''
import ctypes,errno,json,os,shutil,subprocess,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from sandbox_exec import bind
fixture=Path(sys.argv[2])
source=fixture/"source"
root=fixture/"root"
libc=ctypes.CDLL(None,use_errno=True)
def tmpfs(path,flags=0):
    assert libc.mount(b"tmpfs",os.fsencode(path),b"tmpfs",flags,b"size=1m,mode=0755")==0,ctypes.get_errno()
    (path/"value").write_bytes(b"owned")
    shutil.copyfile("/usr/bin/true",path/"program")
    (path/"program").chmod(0o755)
tmpfs(source/"nested")
(source/"nested/deep").mkdir()
tmpfs(source/"nested/deep",os.ST_NOEXEC)
tmpfs(root/"inherited")
relative=("", "nested", "nested/deep")
original={name:os.statvfs(source/name).f_flag for name in relative}
def readonly(path):
    try: (path/"value").write_bytes(b"forged")
    except OSError as error: assert error.errno==errno.EROFS,error
    else: raise AssertionError("readonly mount accepted a write")
def executable(path,allowed):
    try: result=subprocess.run([str(path)],check=False)
    except OSError as error:
        assert not allowed and error.errno in (errno.EACCES,errno.EPERM),error
    else: assert allowed and result.returncode==0,result
restricted=fixture/"restricted"
bind(source,restricted)
for name in relative:
    path=restricted/name
    assert os.statvfs(path).f_flag & 15 == 15
    readonly(path); executable(path/"program",False)
runtime=fixture/"runtime"
bind(source,runtime,executable=True)
for name in relative:
    path=runtime/name
    assert os.statvfs(path).f_flag & 7 == 7
    readonly(path)
    executable(path/"program",not bool(original[name] & os.ST_NOEXEC))
bind(root,root,executable=True)
assert os.statvfs(root/"inherited").f_flag & 7 == 7
readonly(root/"inherited")
bind(source,root/"repo")
bind(source,root/"control",writable=True)
for name in relative:
    assert os.statvfs(root/"repo"/name).f_flag & 15 == 15
    path=root/"control"/name
    assert os.statvfs(path).f_flag & 15 == 14
    (path/"value").write_bytes(b"explicit writable exception")
    executable(path/"program",False)
bind(source/"nested/program",root/"control/helper",executable=True)
assert os.statvfs(root/"control/helper").f_flag & 15 == 7
executable(root/"control/helper",True)
assert {name:os.statvfs(source/name).f_flag for name in relative} == original
for name in relative: (source/name/"value").write_bytes(b"source remains writable")
print(json.dumps({"submount_levels":3,"source_flags_unchanged":True,
                  "root_sealed":True,"writable_exception":True,"executable_exception":True}))
'''
        with self.session() as session:
            result = session.budget.run(
                [*session.launcher, "/usr/bin/python3", "-I", "-S", "-c", program,
                 str(TRUSTED_ROOT), str(fixture)],
                env=ENVIRONMENT, privileged=session.sudo_drop,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {
                "submount_levels": 3, "source_flags_unchanged": True,
                "root_sealed": True, "writable_exception": True, "executable_exception": True,
            })
        self.assert_clean(session)
        self.assertFalse((fixture / "source/nested/value").exists())
        self.assertFalse((fixture / "root/inherited/value").exists())

    def test_recursive_mount_attribute_failure_has_no_top_only_fallback(self):
        from scripts.validation_ownership import sandbox_exec
        target = self.directory / "attribute target"
        target.mkdir()
        calls = []
        def unavailable(number, descriptor, path, flags, attributes, size):
            value = ctypes.cast(attributes, ctypes.POINTER(sandbox_exec.MountAttributes)).contents
            calls.append((number.value, path.value, flags.value, value.attr_set, value.attr_clr,
                          value.propagation, value.userns_fd, size.value))
            self.assertEqual(os.fstat(descriptor.value).st_ino, target.stat().st_ino)
            ctypes.set_errno(errno.ENOSYS)
            return -1
        library = SimpleNamespace(syscall=unavailable)
        descriptors = set(os.listdir("/proc/self/fd"))
        with patch.object(sandbox_exec, "mount") as mount, patch.object(
            sandbox_exec.ctypes, "CDLL", return_value=library,
        ):
            with self.assertRaises(OSError) as caught:
                sandbox_exec.bind(self.root, target)
        self.assertEqual(caught.exception.errno, errno.ENOSYS)
        mount.assert_called_once_with(self.root, target, sandbox_exec.MS_BIND | sandbox_exec.MS_REC)
        self.assertEqual(calls, [(442, b"", 0x9000, 15, 0, 0, 0, 32)])
        self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)

    def test_root_setup_rejects_unsupported_recursive_attributes_before_supervision(self):
        from scripts.validation_ownership import sandbox_exec
        config = self.directory / "mount-config.json"
        config.write_text(json.dumps({"root": str(self.root), "mounts": []}))
        supervise = Mock(return_value=0)
        flags = Mock(wraps=sys.flags, isolated=True, no_site=True)
        with patch.object(sys, "path", list(sys.path)), patch.object(
            sys, "argv", ["sandbox_exec.py", str(config)],
        ), patch.object(
            sys, "flags", flags,
        ), patch.object(sandbox_exec, "mount"), patch.object(
            sandbox_exec, "recursive_attributes", side_effect=OSError(errno.ENOSYS, "unsupported"),
        ), patch.dict(sys.modules, {"syscall_guard": SimpleNamespace(supervise=supervise)}):
            with self.assertRaises(OSError) as caught:
                sandbox_exec.main()
        self.assertEqual(caught.exception.errno, errno.ENOSYS)
        supervise.assert_not_called()

    @contextmanager
    def owned_process(self, argv):
        child = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, env=ENVIRONMENT,
        )
        descriptor = os.pidfd_open(child.pid)
        try:
            yield child, descriptor
        finally:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            os.close(descriptor)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()

    def test_reaped_process_handles_never_signal_an_unrelated_owned_canary(self):
        from scripts.validation_ownership import lifecycle
        for terminate in (ProbeBudget._terminate, lifecycle.terminate):
            with self.subTest(terminate=terminate):
                with self.owned_process([
                    "/usr/bin/python3", "-I", "-S", "-c",
                    "import os; os.write(1,b'ready\\n'); os.read(0,1)",
                ]) as (canary, descriptor):
                    self.assertEqual(canary.stdout.readline(), b"ready\n")
                    completed = subprocess.Popen(["/usr/bin/true"], start_new_session=True)
                    completed.wait()
                    # Model kernel reuse without exhausting PID space or
                    # addressing anything except this test-owned canary.
                    completed.pid = canary.pid
                    terminate(completed)
                    self.assertIsNone(canary.poll())
                    canary.stdin.write(b"x")
                    canary.stdin.flush()
                    self.assertEqual(canary.wait(timeout=5), 0)

    def test_tracee_cleanup_uses_pinned_identity_after_numeric_pid_reuse(self):
        from scripts.validation_ownership.syscall_guard import Process, signal_tracees
        with self.owned_process([
            "/usr/bin/python3", "-I", "-S", "-c",
            "import os; os.write(1,b'ready\\n'); os.read(0,1)",
        ]) as (canary, descriptor):
            self.assertEqual(canary.stdout.readline(), b"ready\n")
            with self.owned_process(["/usr/bin/true"]) as (completed, dead_identity):
                completed.wait()
                signal_tracees({canary.pid: Process("command", pidfd=dead_identity)})
                self.assertIsNone(canary.poll())
                canary.stdin.write(b"x")
                canary.stdin.flush()
                self.assertEqual(canary.wait(timeout=5), 0)

    def test_budget_normal_completion_reaps_group_and_escaped_descendants(self):
        for escaped in (False, True):
            with self.subTest(escaped=escaped):
                natural = self.directory / ("natural-exit-" + str(escaped))
                program = (
                    "import os,time\nready=os.pipe()\nchild=os.fork()\n"
                    "if child==0:\n"
                    + (" os.setsid()\n" if escaped else "")
                    + " os.write(ready[1],b'x')\n time.sleep(2)\n"
                    f" open({str(natural)!r},'wb').write(b'not terminated')\n"
                    " os._exit(0)\n"
                    "os.read(ready[0],1)\nos.write(1,str(child).encode()+b'\\n')\nos._exit(0)\n"
                )
                budget = ProbeBudget(Limits(seconds=5))
                result = budget.run(
                    ["/usr/bin/python3", "-I", "-S", "-c", program], env=ENVIRONMENT,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                descendant = int(result.stdout)
                self.assertFalse(natural.exists())
                self.assertFalse(Path(f"/proc/{descendant}").exists())
                self.assertFalse(budget.children)

    def test_watchdog_payload_stdin_is_binary_and_separate_from_lifetime(self):
        data = bytes(range(256)) * 400
        program = (
            "import os\n"
            "for descriptor in range(3,32):\n"
            " try: os.fstat(descriptor)\n"
            " except OSError: pass\n"
            " else: raise SystemExit(7)\n"
            "while True:\n"
            " data=os.read(0,4096)\n"
            " if not data: break\n"
            " os.write(1,data)\n"
        )
        budget = ProbeBudget()
        output = budget.run(
            ["/usr/bin/python3", "-I", "-S", "-c", program],
            env=ENVIRONMENT, input_data=data,
        )
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(output.stdout, data)
        self.assertFalse(budget.children)

    def test_budget_launch_interruption_waits_for_handle_ownership(self):
        original = subprocess.Popen
        launched = []
        def interrupted(signum, frame):
            raise KeyboardInterrupt("owned launch interruption")
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            for payload in (None, b"bounded input"):
                with self.subTest(payload=payload):
                    budget = ProbeBudget(Limits(seconds=5))
                    def starting(argv, **kwargs):
                        child = original(argv, **kwargs)
                        launched.append(child)
                        os.kill(os.getpid(), signal.SIGTERM)
                        return child
                    with patch("subprocess.Popen", starting):
                        with self.assertRaises(KeyboardInterrupt):
                            budget.run(
                                ["/usr/bin/python3", "-I", "-S", "-c", "import time; time.sleep(2)"],
                                env=ENVIRONMENT, input_data=payload,
                            )
                    self.assertFalse(budget.children)
                    self.assertIsNotNone(launched[-1].poll())
        finally:
            signal.signal(signal.SIGTERM, previous)
            for child in launched:
                child.stdin.close()
                child.wait(timeout=5)
                child.stdout.close()
                child.stderr.close()

    def test_missing_pidfd_support_rejects_before_payload_or_tracee_launch(self):
        from scripts.validation_ownership import lifecycle, syscall_guard
        read, write = os.pipe()
        before = set(os.listdir("/proc/self/fd"))
        try:
            with patch("signal.pidfd_send_signal", side_effect=OSError(errno.ENOSYS, "unsupported")), patch(
                "subprocess.Popen",
            ) as launch, patch("os.fork") as fork:
                with self.assertRaises(OSError):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=read)
                with self.assertRaises(OSError):
                    syscall_guard.supervise({}, None)
                launch.assert_not_called()
                fork.assert_not_called()
            self.assertEqual(set(os.listdir("/proc/self/fd")), before)
        finally:
            os.close(read)
            os.close(write)

    @staticmethod
    def stack_growth_source():
        return r'''
#include <signal.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
static __attribute__((noinline)) void grow(int ready, int finish) {
    volatile unsigned char stack[2*1024*1024];
    unsigned i; char byte;
    for (i=0;i<sizeof(stack);i+=4096) stack[i]=7;
    if (ready<0) {
        if (write(1,"STACKS_HELD\n",12)!=12) _exit(3);
    } else {
        if (write(ready,"G",1)!=1 || read(finish,&byte,1)!=1) _exit(3);
    }
    if (stack[4096]!=7) _exit(4);
}
int main(int argc, char **argv) {
    int start[2], ready[2], finish[2], i, status, count; char byte;
    if(argc!=2) return 1;
    count=atoi(argv[1]);
    if(count==0) { raise(SIGSTOP); grow(-1,-1); return 0; }
    if(count<1 || count>8 || pipe(start)||pipe(ready)||pipe(finish)) return 1;
    for (i=0;i<count;++i) {
        pid_t child=fork();
        if (child<0) return 2;
        if (!child) { if(read(start[0],&byte,1)!=1) _exit(2); grow(ready[1],finish[0]); _exit(0); }
    }
    if(write(start[1],"XXXXXXXX",count)!=count) return 3;
    for(i=0;i<count;++i) if(read(ready[0],&byte,1)!=1) return 4;
    if(write(1,"STACKS_HELD\n",12)!=12) return 5;
    if(write(finish[1],"XXXXXXXX",count)!=count) return 6;
    for(i=0;i<count;++i) if(wait(&status)<0 || status) return 7;
    return 0;
}
'''

    def memory_policy(self, limit):
        from scripts.validation_ownership.syscall_guard import Policy
        return Policy({
            "root": str(self.root), "mode": "command", "code": [], "sources": [],
            "enumerations": [], "executables": [], "python_version": "3.12",
            "argv": [], "memory_limit": limit,
        })

    def test_post_fork_stack_growth_is_funded_before_execution(self):
        self.add("stack.c", self.stack_growth_source())
        with self.session() as session:
            tool = session.compile_native(("stack.c",))
            session.budget.limits = replace(session.budget.limits, address_space_bytes=64*1024*1024)
            self.assertEqual(session.native(tool, ("2",)).stdout, b"STACKS_HELD\n")
        self.assert_clean(session)
        session = self.session()
        with session:
            tool = session.compile_native(("stack.c",))
            session.budget.limits = replace(session.budget.limits, address_space_bytes=32*1024*1024)
            with self.assertRaisesRegex(MakeProbeError, "aggregate address-space"):
                session.native(tool, ("8",))
        self.assert_clean(session)

    def test_kernel_virtual_credits_block_stack_faults_without_sampling(self):
        from scripts.validation_ownership.syscall_guard import Process
        self.add("stack.c", self.stack_growth_source())
        with self.session() as session:
            tool = session.compile_native(("stack.c",))
            for funded in (False, True):
                with self.subTest(funded=funded):
                    with self.owned_process([str(tool.path), "0"]) as (child, descriptor):
                        waited, status = os.waitpid(child.pid, os.WUNTRACED)
                        self.assertEqual(waited, child.pid)
                        self.assertTrue(os.WIFSTOPPED(status))
                        resource.prlimit(child.pid, resource.RLIMIT_CORE, (0, 0))
                        policy = self.memory_policy(64*1024*1024)
                        size = policy.virtual_memory(child.pid)
                        if funded:
                            policy.config["memory_limit"] = size + 128*1024
                            record = Process("command", memory_group=child.pid)
                            policy.processes[child.pid] = record
                            policy.reserve_memory(child.pid, record, 0)
                            self.assertEqual(resource.prlimit(child.pid, resource.RLIMIT_AS)[0], record.memory_limit)
                            self.assertLessEqual(record.memory_limit, policy.config["memory_limit"])
                        signal.pidfd_send_signal(descriptor, signal.SIGCONT)
                        child.wait(timeout=5)
                        self.assertEqual(child.returncode, -signal.SIGSEGV if funded else 0)
                        output = child.stdout.read()
                        self.assertEqual(output, b"" if funded else b"STACKS_HELD\n")
        self.assert_clean(session)

    def test_kernel_limits_are_funded_by_one_aggregate_virtual_pool(self):
        from scripts.validation_ownership.syscall_guard import Process, Violation
        program = [
            "/usr/bin/python3", "-I", "-S", "-c",
            "import os,signal; os.kill(os.getpid(),signal.SIGSTOP); os.read(0,1)",
        ]
        with self.owned_process(program) as (first, first_fd), self.owned_process(program) as (second, second_fd):
            for child in (first, second):
                _, status = os.waitpid(child.pid, os.WUNTRACED)
                self.assertTrue(os.WIFSTOPPED(status))
            policy = self.memory_policy(64*1024*1024)
            records = {
                child.pid: Process("command", memory_group=child.pid)
                for child in (first, second)
            }
            policy.processes = records
            policy.reserve_memory(first.pid, records[first.pid], 4*1024*1024)
            policy.reserve_memory(second.pid, records[second.pid], 0)
            limits = {
                pid: resource.prlimit(pid, resource.RLIMIT_AS)[0] for pid in records
            }
            self.assertEqual(limits, {pid: record.memory_limit for pid, record in records.items()})
            self.assertLessEqual(sum(limits.values()), policy.config["memory_limit"])
            with self.assertRaisesRegex(Violation, "aggregate address-space"):
                policy.reserve_memory(second.pid, records[second.pid], policy.config["memory_limit"])
            with self.assertRaisesRegex(Violation, "before kernel grant"):
                policy.assign_memory(records[first.pid], policy.config["memory_limit"])
            self.assertEqual(limits, {
                pid: resource.prlimit(pid, resource.RLIMIT_AS)[0] for pid in records
            })
            policy.reserve_memory(first.pid, records[first.pid], 0, copies=1)
            self.assertGreater(records[first.pid].memory_reservation, 0)
            self.assertLessEqual(sum(
                record.memory_limit + record.memory_reservation for record in records.values()
            ), policy.config["memory_limit"])
            records[first.pid].memory_reservation = 0
            policy.reserve_exec(second.pid, records[second.pid])
            self.assertLessEqual(sum(
                resource.prlimit(pid, resource.RLIMIT_AS)[0] for pid in records
            ), policy.config["memory_limit"])
            policy.finish_exec(second.pid, records[second.pid])
            self.assertLessEqual(policy.memory_peak, policy.config["memory_limit"])

    def test_repeated_vfork_exec_preserves_virtual_credit_ownership(self):
        self.add("exec.c", (
            "#define _GNU_SOURCE\n#include <sys/wait.h>\n#include <unistd.h>\n"
            "int main(int argc,char **argv) {\n"
            " int i,status; (void)argv;\n"
            " if(argc>1) return write(1,\"child\\n\",6)==6 ? 0 : 1;\n"
            " for(i=0;i<4;++i) {\n"
            "  pid_t child=vfork();\n"
            "  if(child<0) return 2;\n"
            "  if(!child) { execl(\"/native/tool\",\"/native/tool\",\"child\",(char *)0); _exit(3); }\n"
            "  if(waitpid(child,&status,0)!=child || status) return 4;\n"
            " }\n return write(1,\"parent\\n\",7)==7 ? 0 : 5;\n}\n"
        ))
        with self.session() as session:
            tool = session.compile_native(("exec.c",))
            session.budget.limits = replace(session.budget.limits, address_space_bytes=32*1024*1024)
            run = session._sandbox_run
            def compiler_driver(root, **kwargs):
                # This owned driver exercises the compiler's supported repeated
                # exec domain; registered commands now deliberately deny reexec.
                self.assertEqual(kwargs["mode"], "command")
                self.assertEqual(kwargs["argv"], ["/native/tool"])
                kwargs["mode"] = "compile"
                return run(root, **kwargs)
            with patch.object(session, "_sandbox_run", compiler_driver):
                self.assertEqual(session.native(tool).stdout, b"child\n"*4 + b"parent\n")
        self.assert_clean(session)

    def test_shared_vm_growth_counts_every_live_member(self):
        self.add("shared.c", (
            "#define _GNU_SOURCE\n#include <sched.h>\n#include <signal.h>\n"
            "#include <stdlib.h>\n#include <sys/mman.h>\n#include <sys/wait.h>\n#include <unistd.h>\n"
            "static int child(void *argument) {\n"
            " size_t size=(size_t)argument;\n"
            " void *area=mmap(0,size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);\n"
            " if(area==MAP_FAILED) _exit(3);\n"
            " if(write(1,\"shared\\n\",7)!=7 || munmap(area,size)) _exit(4);\n _exit(0);\n}\n"
            "int main(int argc,char **argv) {\n"
            " if(argc!=2) return 1;\n"
            " void *stack=mmap(0,65536,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);\n"
            " if(stack==MAP_FAILED) return 2;\n"
            " int status; size_t size=strtoul(argv[1],0,10)*1024*1024;\n"
            " pid_t pid=clone(child,(char *)stack+65536,CLONE_VM|CLONE_VFORK|SIGCHLD,(void *)size);\n"
            " if(pid<0 || waitpid(pid,&status,0)!=pid || status) return 3;\n"
            " return munmap(stack,65536)!=0;\n}\n"
        ))
        for size in (4, 16):
            with self.subTest(size=size):
                session = self.session()
                with session:
                    tool = session.compile_native(("shared.c",))
                    session.budget.limits = replace(session.budget.limits, address_space_bytes=32*1024*1024)
                    if size == 4:
                        self.assertEqual(session.native(tool, (str(size),)).stdout, b"shared\n")
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "aggregate address-space"):
                            session.native(tool, (str(size),))
                self.assert_clean(session)

    def test_make_option_channels_cannot_inject_unrequested_evaluation(self):
        self.add("Makefile", (
            "ifeq ($(INJECTED),yes)\nDEP = injected\nelse\nDEP = ordinary\nendif\n"
            "all: $(DEP)\n\t@printf '%s' '$^'\ninjected ordinary: ;\n"
        ))
        self.add("other.mk", "INJECTED := yes\n")
        before = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env={**ENVIRONMENT, "GNUMAKEFLAGS": "--eval=INJECTED=yes"},
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(before.stdout, b"injected")
        for name in ("GNUMAKEFLAGS", "MAKEFLAGS"):
            for origin in ("environment", "command-line"):
                for value in ("--eval=INJECTED=yes", "-f other.mk"):
                    with self.subTest(name=name, origin=origin, value=value):
                        session = self.session()
                        with session:
                            runs = session.budget.runs
                            with self.assertRaisesRegex(MakeProbeError, "execution-authority Make assignment"):
                                session.make("all", assignments=((origin, name, value),))
                            self.assertEqual(session.budget.runs, runs)
                        self.assert_clean(session)

    def ordinary_assignment_context(self, assignments, names):
        environment, cli = dict(ENVIRONMENT), []
        for origin, name, value in assignments:
            if origin == "environment":
                environment[name] = value
            else:
                cli.append(name + "=" + value)
        normal = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", *cli, "all"],
            cwd=self.root, env=environment, capture_output=True, check=True, timeout=10,
        )
        lines = normal.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 1 + 3*len(names), normal.stdout)
        return (
            [{"name": name, "order_only": False} for name in lines[0].split()],
            {
                name: dict(zip(("value", "origin", "flavor"), lines[1 + index*3:4 + index*3]))
                for index, name in enumerate(names)
            },
        )

    def test_equivalent_assignment_order_preserves_actual_make_identity(self):
        self.add("Makefile", (
            "all: $(B)\n"
            "\t@printf '%s\\n' '$^' '$(A)' '$(origin A)' '$(flavor A)' "
            "'$(B)' '$(origin B)' '$(flavor B)'\n"
            "one-two: ;\n"
        ))
        for origins in (
            ("environment", "environment"), ("command-line", "command-line"),
            ("environment", "command-line"), ("command-line", "environment"),
        ):
            with self.subTest(origins=origins):
                assignments = ((origins[0], "A", "one"), (origins[1], "B", "$(A)-two"))
                normal, observed = [], []
                with self.session() as session:
                    for order in (assignments, tuple(reversed(assignments))):
                        context = self.ordinary_assignment_context(order, ("A", "B"))
                        result = session.make("all", variables=("A", "B"), assignments=order)
                        self.assertEqual(result.semantics["files"][0]["prerequisites"], context[0])
                        self.assertEqual(result.semantics["domains"], context[1])
                        self.assertEqual(result.events, ())
                        normal.append(context)
                        observed.append(result)
                self.assert_clean(session)
                self.assertEqual(normal[0], normal[1])
                self.assertEqual(observed[0].execution_digest, observed[1].execution_digest)
                self.assertEqual(observed[0].semantic_digest, observed[1].semantic_digest)
                self.assertEqual(observed[0].semantics, observed[1].semantics)

    def test_assignment_identity_preserves_values_origins_and_observed_order(self):
        names = ("A", "B", "STATE")
        recipe = (
            "\t@printf '%s\\n' '$^' '$(A)' '$(origin A)' '$(flavor A)' "
            "'$(B)' '$(origin B)' '$(flavor B)' '$(STATE)' '$(origin STATE)' '$(flavor STATE)'\n"
        )
        self.add("Makefile", "all: $(B)\n" + recipe + "one-two other-two: ;\n")
        states = (
            (("command-line", "A", "one"), ("command-line", "B", "$(A)-two")),
            (("command-line", "A", "other"), ("command-line", "B", "$(A)-two")),
            (("environment", "A", "one"), ("command-line", "B", "$(A)-two")),
        )
        with self.session() as session:
            results = []
            for state in states:
                normal = self.ordinary_assignment_context(state, names)
                result = session.make("all", variables=names, assignments=state)
                self.assertEqual(result.semantics["files"][0]["prerequisites"], normal[0])
                self.assertEqual(result.semantics["domains"], normal[1])
                results.append(result)
            self.assertEqual(len({result.semantic_digest for result in results}), len(states))
            self.assertEqual(len({result.execution_digest for result in results}), 1)
        self.assert_clean(session)
        self.add("Makefile", (
            "STATE = $(MAKEOVERRIDES)\n"
            "all: $(if $(filter B=%,$(firstword $(MAKEOVERRIDES))),right,left)\n"
            + recipe + "right left: ;\n"
        ))
        with self.session() as session:
            results = []
            for state in (states[0], tuple(reversed(states[0]))):
                normal = self.ordinary_assignment_context(state, names)
                result = session.make("all", variables=names, assignments=state)
                self.assertEqual(result.semantics["files"][0]["prerequisites"], normal[0])
                self.assertEqual(result.semantics["domains"], normal[1])
                results.append(result)
            self.assertNotEqual(
                results[0].semantics["files"][0]["prerequisites"],
                results[1].semantics["files"][0]["prerequisites"],
            )
            self.assertNotEqual(results[0].semantics["domains"], results[1].semantics["domains"])
            self.assertNotEqual(results[0].semantic_digest, results[1].semantic_digest)
        self.assert_clean(session)

    def test_conditional_graphs_match_ordinary_make_not_probe_markers(self):
        controls = (
            "ifeq ($(SHELL),/bin/vo-shell)",
            "ifeq ($(origin SHELL),command line)",
            "ifeq ($(MAKE),/bin/vo-make)",
            "ifeq ($(origin MAKE),command line)",
            "ifeq ($(origin .SHELLFLAGS),command line)",
            "ifneq ($(findstring n,$(firstword $(MAKEFLAGS))),)",
            "ifneq ($(findstring B,$(firstword $(MAKEFLAGS))),)",
            "ifneq ($(findstring j1,$(MAKEFLAGS)),)",
            "ifneq ($(findstring --no-print-directory,$(MAKEFLAGS)),)",
            "ifneq ($(origin LD_PRELOAD),undefined)",
            "ifneq ($(origin VO_OBSERVE_TARGET),undefined)",
            "ifneq ($(origin SOURCE_DATE_EPOCH),undefined)",
        )
        for condition in controls:
            with self.subTest(condition=condition):
                self.add("Makefile", (
                    condition + "\nDEP = hidden\nelse\nDEP = genuine\nendif\n"
                    "all: $(DEP)\n\t@printf '%s' '$^'\nhidden genuine: ;\n"
                ))
                normal = subprocess.run(
                    ["/usr/bin/make", "-f", "Makefile", "all"],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
                )
                self.assertEqual(normal.stdout, b"genuine")
                with self.session() as session:
                    observed = session.make("all")
                    self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                        {"name": normal.stdout.decode("ascii"), "order_only": False},
                    ])
                    self.assertEqual(observed.events, ())
                self.assert_clean(session)

    def test_default_file_and_requested_domains_preserve_production_make_context(self):
        names = (
            "SHELL", "MAKE", "MAKE_COMMAND", "MAKEFLAGS", "MFLAGS", "MAKELEVEL",
            "LD_PRELOAD", "GNUMAKEFLAGS", "MODE", "FLAGS", "FLAGS_ORIGIN", "FLAGS_FLAVOR",
        )
        aliases = "".join(
            f"CTX_{index}_{field} = $({form}{name})\n"
            for index, name in enumerate(names)
            for field, form in (("value", ""), ("origin", "origin "), ("flavor", "flavor "))
        )
        arguments = " ".join(
            f"'$(CTX_{index}_{field})'"
            for index in range(len(names)) for field in ("value", "origin", "flavor")
        )
        for prefix, assignments in (
            ("MODE ?= file\n", ()),
            ("SHELL := /bin/bash\nMODE ?= file\n", (("environment", "MODE", "environment"),)),
            (".POSIX:\nMODE ?= file\n", (("command-line", "MODE", "command"),)),
        ):
            with self.subTest(prefix=prefix, assignments=assignments):
                self.add("Makefile", (
                    prefix + "FLAGS = $(.SHELLFLAGS)\nFLAGS_ORIGIN = $(origin .SHELLFLAGS)\n"
                    "FLAGS_FLAVOR = $(flavor .SHELLFLAGS)\n" + aliases
                    + "ifeq ($(MODE),command)\nDEP = command\nelse\nDEP = ordinary\nendif\n"
                    + f"all: $(DEP)\n\t@printf '%s\\n' '$^' {arguments}\n"
                    + "command ordinary: ;\n"
                ))
                environment = dict(ENVIRONMENT)
                cli = []
                for origin, name, value in assignments:
                    if origin == "environment":
                        environment[name] = value
                    else:
                        cli.append(name + "=" + value)
                normal = subprocess.run(
                    ["/usr/bin/make", "-f", "Makefile", *cli, "all"],
                    cwd=self.root, env=environment, capture_output=True, check=True, timeout=10,
                )
                lines = normal.stdout.decode("ascii").splitlines()
                self.assertEqual(len(lines), 1 + 3 * len(names), normal.stdout)
                expected = {
                    name: dict(zip(("value", "origin", "flavor"), lines[1 + index * 3:4 + index * 3]))
                    for index, name in enumerate(names)
                }
                with self.session() as session:
                    observed = session.make("all", variables=names, assignments=assignments)
                    self.assertEqual(observed.semantics["domains"], expected)
                    self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                        {"name": lines[0], "order_only": False},
                    ])
                    self.assertEqual(observed.events, ())
                self.assert_clean(session)

    def test_secondary_expansion_keeps_target_specific_shell_context(self):
        self.add("Makefile", (
            ".SECONDEXPANSION:\nall: SHELL := /bin/bash\n"
            "all: $$(if $$(filter /bin/bash,$$(SHELL)),file-shell,wrong-context)\n"
            "\t@printf '%s' '$^'\nfile-shell wrong-context: ;\n"
        ))
        normal = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(normal.stdout, b"file-shell")
        with self.session() as session:
            observed = session.make("all", variables=("SHELL",))
            self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                {"name": "file-shell", "order_only": False},
            ])
            self.assertEqual(observed.semantics["files"][0]["variables"]["SHELL"], {
                "value": "/bin/bash", "origin": "file", "flavor": "simple",
            })
            self.assertEqual(observed.semantics["domains"]["SHELL"]["value"], "/bin/sh")
        self.assert_clean(session)

    def test_recipe_commands_are_metadata_but_make_expansion_effects_still_reject(self):
        self.add("Makefile", "all:\n\t@printf '%s' recipe > recipe-effect\n")
        subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        effect = self.root / "recipe-effect"
        self.assertEqual(effect.read_bytes(), b"recipe")
        effect.unlink()
        with self.session() as session:
            observed = session.make("all")
            self.assertFalse(effect.exists())
            self.assertFalse((session.tree / "recipe-effect").exists())
            self.assertEqual(observed.events, ())
            self.assertIn("recipe-effect", observed.semantics["files"][0]["recipe"])
        self.assert_clean(session)
        self.add("Makefile", "all:\n\t$(file >recipe-effect,forged)\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "write outside"):
            with session:
                session.make("all")
        self.assertFalse(effect.exists())
        self.assert_clean(session)

    def test_dispatch_classifies_identical_recipe_and_expansion_by_native_context(self):
        for prefix in ("", ".POSIX:\n"):
            for command in ("printf %s dynamic", "printf '%s' dynamic; printf ''"):
                with self.subTest(prefix=prefix, command=command):
                    self.add("Makefile", (
                        prefix + f"VALUE := $(shell {command})\nall: $(VALUE)\n"
                        f"\t@{command}\ndynamic: ;\n"
                    ))
                    with self.session() as session:
                        observed = session.make(
                            "all", variables=("VALUE",),
                            commands={command: Command(("/usr/bin/printf", "%s", "dynamic"))},
                        )
                        self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "dynamic")
                        self.assertEqual(len(observed.events), 1)
                        self.assertEqual(observed.events[0]["match"], 0)
                        self.assertEqual(observed.stdout, b"")
                    self.assert_clean(session)

    def test_recursive_and_makefile_remake_dispatch_still_requires_real_mappings(self):
        self.add("Makefile", "all:\n\t+@printf %s recursive\n")
        for registered in (False, True):
            with self.subTest(registered=registered):
                session = self.session()
                with session:
                    if registered:
                        observed = session.make("all", commands={
                            "printf %s recursive": Command(("/usr/bin/printf", "%s", "recursive")),
                        })
                        self.assertEqual(observed.stdout, b"recursive")
                        self.assertEqual(len(observed.events), 1)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "unregistered eager/recursive"):
                            session.make("all")
                self.assert_clean(session)
        self.add("Makefile", "include missing.mk\nmissing.mk:\n\t@printf missing\nall: ;\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "unregistered eager/recursive"):
            with session:
                session.make("all")
        self.assert_clean(session)

    def test_private_dispatch_and_observer_inputs_cannot_be_candidate_authority(self):
        for operation in (
            "$(file </control/interceptor)",
            "$(file </lib/vo-observer.so)",
            "$(wildcard /lib/*)",
        ):
            with self.subTest(operation=operation):
                self.add("Makefile", f"VALUE := {operation}\nall: ;\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "channel denied|observer image|directory enumeration"):
                    with session:
                        session.make("all")
                self.assert_clean(session)
        from scripts.validation_ownership.syscall_guard import VO_READY, VO_DISPATCH, VO_QUERY_KIND
        for marker in (VO_READY, VO_DISPATCH, VO_QUERY_KIND):
            with self.subTest(marker=marker):
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "unauthenticated"):
                    with session:
                        session.command(Command((
                            "/usr/bin/python3", "-I", "-c",
                            "import ctypes; ctypes.CDLL(None).syscall(39, ctypes.c_ulong("
                            + str(marker) + "), 0, 0)",
                        )))
                self.assert_clean(session)

    def test_partial_scratch_setup_releases_created_parents_and_descriptors(self):
        self.add("Makefile", "all: ;\n")
        original_open, original_mkdir = os.open, os.mkdir
        failures = ["tracked", "open", "mkdir", "long-name", "interrupt"]
        if os.geteuid() != 0:
            failures.append("inaccessible")
        for failure in failures:
            with self.subTest(failure=failure):
                self.scratch = self.root / "partial" / "parents" / ("x" * 300 if failure == "long-name" else "leaf")
                entries = dict(self.entries)
                if failure == "tracked":
                    entries["partial/parents/leaf"] = GitTreeEntry(
                        "partial/parents/leaf", "100644", "blob", "0" * 40,
                    )
                primary = OSError(errno.EIO, "owned scratch setup failure")
                def opening(path, flags, *args, **kwargs):
                    if path == "leaf" and kwargs.get("dir_fd") is not None:
                        if failure == "open":
                            raise primary
                        if failure == "interrupt":
                            raise KeyboardInterrupt("owned scratch interruption")
                    return original_open(path, flags, *args, **kwargs)
                def making(path, mode=0o777, *, dir_fd=None):
                    if path == "leaf" and failure == "mkdir":
                        raise primary
                    result = original_mkdir(path, mode, dir_fd=dir_fd)
                    if path == "leaf" and failure == "inaccessible":
                        os.chmod(path, 0, dir_fd=dir_fd)
                    return result
                descriptors = set(os.listdir("/proc/self/fd"))
                budget = ProbeBudget()
                session = ProbeSession(
                    AuthorityLoader(self.root, GitTreeEntries(entries, budget=budget), budget=budget),
                    scratch_root=self.scratch, budget=budget,
                )
                with patch("os.open", opening), patch("os.mkdir", making):
                    with self.assertRaises(KeyboardInterrupt if failure == "interrupt" else MakeProbeError) as caught:
                        with session:
                            self.fail("unsafe scratch setup was admitted")
                if failure in {"open", "mkdir"}:
                    self.assertIs(caught.exception.__cause__, primary)
                self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)
                self.assertFalse((self.root / "partial").exists())
                self.assert_clean(session)

    def test_scratch_setup_interruption_waits_for_resource_ownership(self):
        self.add("Makefile", "all: ;\n")
        original = os.mkdir
        sent = False
        def creating(path, mode=0o777, *, dir_fd=None):
            nonlocal sent
            result = original(path, mode, dir_fd=dir_fd)
            if dir_fd is not None and str(path).startswith("probe-"):
                os.kill(os.getpid(), signal.SIGTERM)
                sent = True
            return result
        session = self.session()
        with patch("os.mkdir", creating):
            with self.assertRaises(KeyboardInterrupt):
                with session:
                    self.fail("setup interruption was lost")
        self.assertTrue(sent)
        self.assert_clean(session)

    def test_scratch_cleanup_preserves_existing_parents_and_primary_failure(self):
        self.add("Makefile", "all: ;\n")
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep").write_bytes(b"not allocator-owned")
        self.scratch = existing / "created" / "leaf"
        original_open, original_remove = os.open, os.rmdir
        primary = OSError(errno.EIO, "primary setup failure")
        def opening(path, flags, *args, **kwargs):
            if path == "leaf":
                raise primary
            return original_open(path, flags, *args, **kwargs)
        session = self.session()
        with patch("os.open", opening):
            with self.assertRaises(MakeProbeError) as caught:
                with session:
                    self.fail("setup failure disappeared")
        self.assertIs(caught.exception.__cause__, primary)
        self.assertEqual((existing / "keep").read_bytes(), b"not allocator-owned")
        self.assertFalse((existing / "created").exists())
        self.assert_clean(session)

        def removing(path, *args, **kwargs):
            if path == "leaf":
                raise PermissionError("modeled owned cleanup failure")
            return original_remove(path, *args, **kwargs)
        primary = OSError(errno.EIO, "primary setup failure")
        session = self.session()
        with patch("os.open", opening), patch("os.rmdir", removing):
            with self.assertRaises(MakeProbeError) as caught:
                with session:
                    self.fail("setup failure disappeared")
        self.assertIs(caught.exception.__cause__, primary)
        if hasattr(primary, "__notes__"):
            self.assertTrue(any("owned scratch cleanup failed" in note for note in primary.__notes__))
        self.assertEqual((existing / "keep").read_bytes(), b"not allocator-owned")

    def test_privileged_cleanup_delegates_before_wait_and_never_hides_permission_errors(self):
        child = SimpleNamespace(pid=999999999, stdin=Mock(), wait=Mock(), returncode=None)
        with patch("os.killpg", side_effect=PermissionError("modeled root-owned group")) as kill:
            ProbeBudget._terminate(child)
            ProbeBudget._terminate(child, privileged=True)
            kill.assert_not_called()
            self.assertEqual(child.stdin.close.call_count, 2)
            self.assertEqual(child.wait.call_count, 2)
        from scripts.validation_ownership import lifecycle
        child.wait.reset_mock()
        with patch("os.waitid", return_value=None), patch(
            "os.killpg", side_effect=PermissionError("owned watchdog signal denied"),
        ):
            with self.assertRaises(PermissionError):
                lifecycle.terminate(child)
            child.wait.assert_not_called()
        for argv, supplied in ((["/usr/bin/true"], None), ([*NAMESPACE_LAUNCHER, "/usr/bin/true"], b"input")):
            with patch("subprocess.Popen") as launch:
                with self.assertRaisesRegex(MakeProbeError, "guarded PID-namespace lifecycle"):
                    ProbeBudget().run(argv, env=ENVIRONMENT, privileged=True, input_data=supplied)
                launch.assert_not_called()
        mode = os.stat("/usr/bin/unshare")
        elevated = list(mode)
        elevated[0] |= stat.S_ISUID
        with patch.object(lifecycle.os, "stat", return_value=os.stat_result(elevated)), patch(
            "subprocess.Popen",
        ) as launch:
            with self.assertRaisesRegex(MakeProbeError, "unsupported privileged namespace lifecycle"):
                ProbeBudget().run([*NAMESPACE_LAUNCHER, "/usr/bin/true"], env=ENVIRONMENT, privileged=True)
            launch.assert_not_called()
        with patch.object(lifecycle.os, "getxattr", return_value=b"modeled file capabilities"), patch(
            "subprocess.Popen",
        ) as launch:
            with self.assertRaisesRegex(MakeProbeError, "file capabilities"):
                ProbeBudget().run([*NAMESPACE_LAUNCHER, "/usr/bin/true"], env=ENVIRONMENT, privileged=True)
            launch.assert_not_called()

    def test_privileged_budget_uses_real_watchdog_without_running_sudo(self):
        budget = ProbeBudget()
        original = subprocess.Popen
        invocations = []
        def same_uid_fixture(argv, **kwargs):
            self.assertEqual(argv[:7], [
                "/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-S", "-B",
            ])
            self.assertEqual(Path(argv[7]), TRUSTED_ROOT / "lifecycle.py")
            self.assertEqual(float(argv[8]), budget.deadline)
            self.assertEqual(argv[9], "--")
            self.assertEqual(tuple(argv[10:10 + len(NAMESPACE_LAUNCHER)]), NAMESPACE_LAUNCHER)
            self.assertEqual(kwargs["stdin"], subprocess.PIPE)
            self.assertTrue(kwargs["close_fds"])
            invocations.append(argv)
            # Keep the real watchdog/payload, but not the privilege or namespace
            # launcher. These fixtures must work where user namespaces reject.
            owned = [
                *argv[3:10], *argv[10 + len(NAMESPACE_LAUNCHER):],
            ]
            return original(owned, **kwargs)
        with patch("subprocess.Popen", same_uid_fixture), patch(
            "scripts.validation_ownership.budget.os.killpg",
            side_effect=PermissionError("outer caller cannot signal privileged groups"),
        ) as kill:
            result = budget.run(
                [*NAMESPACE_LAUNCHER, "/usr/bin/printf", "%s", "guarded payload"],
                env=ENVIRONMENT, privileged=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"guarded payload")
            kill.assert_not_called()
        self.assertEqual(len(invocations), 1)
        self.assertFalse(budget.children)

    def test_sudo_preflight_and_capsules_share_the_privileged_lifecycle_contract(self):
        self.add("Makefile", "all: ;\n")
        session = self.session()
        original_run = session.budget.run
        original_file = Path.is_file
        guarded = []
        def fake_namespace(argv, **kwargs):
            if argv[0] == "/usr/bin/unshare" and "--user" in argv:
                return subprocess.CompletedProcess(argv, 1, b"", b"modeled user namespace denial")
            if kwargs.get("privileged"):
                self.assertEqual(tuple(argv[:len(NAMESPACE_LAUNCHER)]), NAMESPACE_LAUNCHER)
                guarded.append(argv)
                if argv[-1] != "/usr/bin/true":
                    config = json.loads(Path(argv[-1]).read_bytes())
                    self.assertTrue(config["sudo_drop"])
                    Path(config["report"]).write_text(json.dumps({
                        "ok": True, "returncode": 0, "error": None,
                        "consumed": [], "code_consumed": [], "accessed": [],
                        "processes": 1, "syscalls": 1, "written_bytes": 0,
                        "created_files": 0, "memory_peak": 1, "observation_bytes": 0,
                    }))
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            return original_run(argv, **kwargs)
        with patch.object(session.budget, "run", fake_namespace), patch.object(
            Path, "is_file", lambda path: str(path) == "/usr/bin/sudo" or original_file(path),
        ), patch("os.getuid", return_value=1000), patch("os.getgid", return_value=1000):
            with session:
                self.assertTrue(session.sudo_drop)
                session._sandbox_run(
                    session._new_root("lifecycle-contract"), mode="command",
                    argv=["/usr/bin/true"], environment=ENVIRONMENT, mounts=[],
                )
        self.assertEqual(len(guarded), 2)
        self.assertEqual(guarded[0][-1], "/usr/bin/true")
        self.assertEqual(Path(guarded[1][-2]).name, "sandbox_exec.py")
        self.assert_clean(session)

    def test_privileged_lifetime_closes_on_budget_rejection_and_interruption(self):
        original = subprocess.Popen
        def same_uid_fixture(argv, **kwargs):
            self.assertEqual(argv[:3], ["/usr/bin/sudo", "-n", "--"])
            self.assertEqual(tuple(argv[10:10 + len(NAMESPACE_LAUNCHER)]), NAMESPACE_LAUNCHER)
            return original([*argv[3:10], *argv[10 + len(NAMESPACE_LAUNCHER):]], **kwargs)
        for action in ("deadline", "output", "interrupt"):
            with self.subTest(action=action):
                budget = ProbeBudget(Limits(
                    seconds=0.5 if action == "deadline" else 10,
                    process_output_bytes=32 if action == "output" else 1024,
                ))
                charge = budget.charge
                def interrupt_output(category, size):
                    if action == "interrupt" and category == "output":
                        raise KeyboardInterrupt("modeled caller interruption")
                    charge(category, size)
                ready = self.directory / ("watchdog-ready-" + action)
                program = (
                    "import os,time\n"
                    f"with open({str(ready)!r}, 'wb') as marker: marker.write(b'payload started')\n"
                    "os.fork()\n"
                )
                if action != "deadline":
                    program += "os.write(1, b'x'*100)\n"
                program += "time.sleep(20)\n"
                with patch("subprocess.Popen", same_uid_fixture), patch.object(
                    budget, "charge", interrupt_output,
                ), patch("os.killpg", side_effect=PermissionError("outer caller lacks permission")) as kill:
                    expected = {
                        "deadline": "aggregate probe deadline",
                        "output": "process output exceeds streaming byte bound",
                        "interrupt": "modeled caller interruption",
                    }
                    with self.assertRaisesRegex(
                        KeyboardInterrupt if action == "interrupt" else MakeProbeError, expected[action],
                    ):
                        budget.run(
                            [*NAMESPACE_LAUNCHER, "/usr/bin/python3", "-I", "-c", program],
                            env=ENVIRONMENT, privileged=True,
                        )
                    kill.assert_not_called()
                self.assertEqual(ready.read_bytes(), b"payload started")
                self.assertFalse(budget.children)
                self.assertLess(time.monotonic() - budget.started, 5)

    def test_watchdog_rejects_missing_lifetime_and_kernel_support_before_launch(self):
        from scripts.validation_ownership import lifecycle
        read, write = os.pipe()
        try:
            with patch.object(lifecycle, "prctl", side_effect=OSError("unsupported kernel")), patch(
                "subprocess.Popen",
            ) as launch:
                with self.assertRaisesRegex(OSError, "unsupported kernel"):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=read)
                launch.assert_not_called()
            os.close(write)
            write = None
            with patch.object(lifecycle, "prctl"), patch("subprocess.Popen") as launch:
                with self.assertRaisesRegex(BrokenPipeError, "before namespace launch"):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=read)
                launch.assert_not_called()
            with open("/dev/null", "rb") as nonpipe, patch("subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "lifetime pipe"):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=nonpipe.fileno())
                launch.assert_not_called()
        finally:
            os.close(read)
            if write is not None:
                os.close(write)

    def test_watchdog_reaps_owned_orphans_on_completion_eof_deadline_and_signal(self):
        program = (
            "import ctypes,json,os,signal,sys,time\n"
            "death = ctypes.c_int()\n"
            "assert ctypes.CDLL(None).prctl(2, ctypes.byref(death), 0, 0, 0) == 0\n"
            "assert death.value == signal.SIGKILL\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            " time.sleep(20)\n os._exit(0)\n"
            "print(json.dumps([os.getpid(), child]), flush=True)\n"
            "if sys.argv[1] != 'complete': time.sleep(20)\n"
        )
        for action in ("complete", "eof", "deadline", "signal"):
            with self.subTest(action=action):
                started = time.monotonic()
                watchdog = subprocess.Popen(
                    ["/usr/bin/python3", "-I", "-S", "-B", str(TRUSTED_ROOT / "lifecycle.py"),
                     str(started + (1 if action == "deadline" else 10)), "--",
                     "/usr/bin/python3", "-I", "-c", program, action],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    close_fds=True, start_new_session=True, env=ENVIRONMENT,
                )
                pids = []
                try:
                    with selectors.DefaultSelector() as ready:
                        ready.register(watchdog.stdout, selectors.EVENT_READ)
                        self.assertTrue(ready.select(3), "owned payload did not start")
                    line = watchdog.stdout.readline()
                    if not line:
                        self.fail(watchdog.stderr.read())
                    pids = json.loads(line)
                    if action == "eof":
                        watchdog.stdin.close()
                    elif action == "signal":
                        os.kill(watchdog.pid, signal.SIGTERM)
                    watchdog.wait(timeout=5)
                    self.assertEqual(watchdog.returncode, 0 if action == "complete" else 125)
                    self.assertLess(time.monotonic() - started, 5)
                    for pid in pids:
                        self.assertFalse(Path(f"/proc/{pid}").exists(), f"owned PID {pid} was not reaped")
                finally:
                    if watchdog.poll() is None:
                        watchdog.stdin.close()
                        watchdog.wait(timeout=5)
                    watchdog.stdin.close()
                    watchdog.stdout.close()
                    watchdog.stderr.close()

    def test_make_runtime_uses_captured_non_multiarch_closure_and_real_make(self):
        trusted = dict(_make_runtime(ProbeBudget()))
        interpreter = _make_interpreter(trusted["/usr/bin/make"])
        relocated = {
            name if name in {"/usr/bin/make", interpreter} else "/usr/lib/" + Path(name).name: data
            for name, data in trusted.items()
        }
        listing = "\tlinux-vdso.so.1 (0x1)\n" + "".join(
            f"\t{Path(name).name} => {name} (0x2)\n"
            for name in relocated if name not in {"/usr/bin/make", interpreter}
        ) + f"\t{interpreter} (0x3)\n"
        self.add("Makefile", "VALUE := captured-runtime\nall: dependency\ndependency: ;\n")
        session = self.session()
        original_run = session.budget.run
        def runtime_listing(argv, **kwargs):
            if argv == [interpreter, "--list", "/usr/bin/make"]:
                self.assertEqual(kwargs["env"], ENVIRONMENT)
                self.assertEqual(kwargs["cwd"], Path("/"))
                return subprocess.CompletedProcess(argv, 0, listing.encode("ascii"), b"")
            return original_run(argv, **kwargs)
        def captured_runtime(name, budget):
            budget.charge("control", len(relocated[name]))
            return relocated[name]
        with patch(
            "scripts.validation_ownership.make_probe._trusted_runtime_bytes", captured_runtime,
        ), patch.object(session.budget, "run", runtime_listing):
            with session:
                captured = dict(session.make_runtime)
                self.assertEqual(captured, relocated)
                relocated.clear()
                with patch(
                    "scripts.validation_ownership.make_probe._trusted_runtime_bytes",
                    side_effect=AssertionError("captured runtime was read again"),
                ):
                    root = session._new_root("inspect-runtime", make=True)
                    for name, data in captured.items():
                        target = root / name.lstrip("/")
                        self.assertEqual(target.read_bytes(), data)
                        self.assertFalse(target.stat().st_mode & 0o222)
                    self.assertFalse((root / "lib/x86_64-linux-gnu/libc.so.6").exists())
                    self.assertTrue((root / "usr/lib/libc.so.6").is_file())
                    observation = session.make("all", variables=("VALUE",))
                    self.assertEqual(observation.semantics["domains"]["VALUE"]["value"], "captured-runtime")
                    self.assertEqual(observation.semantics["files"][0]["prerequisites"], [
                        {"name": "dependency", "order_only": False},
                    ])
        self.assert_clean(session)

    def test_runtime_capture_rejects_mutable_aliases_and_malformed_elf_or_listing(self):
        alias = self.directory / "make"
        alias.symlink_to("/usr/bin/make")
        with self.assertRaisesRegex(MakeProbeError, "trusted system"):
            _trusted_runtime_bytes(str(alias), ProbeBudget())
        original = Path.lstat
        resolved = Path("/usr/bin/make").resolve()
        def mutable(path):
            value = original(path)
            if path == resolved:
                fields = list(value)
                fields[0] |= stat.S_IWGRP
                return os.stat_result(fields)
            return value
        with patch.object(Path, "lstat", mutable):
            with self.assertRaisesRegex(MakeProbeError, "mutable/untrusted"):
                _trusted_runtime_bytes("/usr/bin/make", ProbeBudget())
        binary = Path("/usr/bin/make").read_bytes()
        invalid_headers = bytearray(binary)
        invalid_headers[56:58] = b"\0\0"
        for data in (b"", b"\x7fELF", bytes(invalid_headers)):
            with self.assertRaises(MakeProbeError):
                _make_interpreter(data)
        for output in (
            b"", b"\tlibc.so.6 => not found\n",
            b"\tlibc.so.6 => /work/libc.so.6 (0x1)\n",
            b"\tlibc.so.6 => /usr/lib/../bin/make (0x1)\n",
        ):
            with self.subTest(output=output):
                budget = ProbeBudget()
                with patch.object(budget, "run", return_value=subprocess.CompletedProcess([], 0, output, b"")):
                    with self.assertRaises(MakeProbeError):
                        _make_runtime(budget)

    def test_directory_permission_attacks_reject_without_masked_errors_or_residue(self):
        controls = [
            ("os.chmod('/work', 0)", "pathname permission loss"),
            ("os.chmod('/work/nested', 0)", "pathname permission loss"),
            ("os.chmod('nested', 0, dir_fd=directory)", "pathname permission loss"),
            ("os.fchmod(directory, 0)", "directory permission changes"),
            ("os.fchmod(os.dup(directory), 0)", "directory permission changes"),
            ("ctypes.CDLL(None).syscall(452, directory, b'nested', 0, 0)", "unadmitted syscall"),
            ("os.mkdir('/work/locked', 0)", "untraversable directory creation"),
            ("os.mkdir('locked', 0, dir_fd=directory)", "untraversable directory creation"),
            ("os.umask(0o700)\nos.mkdir('/work/locked')", "owner permission masking"),
        ]
        for operation, expected in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes,os\nos.mkdir('/work/nested', 0o700)\n"
                    "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
                    "try:\n " + operation.replace("\n", "\n ") + "\n"
                    "except OSError:\n pass\nos._exit(7)\n"
                ))
                session = self.session()
                descriptors = []
                with session:
                    original = session._sandbox_run
                    def retain_owned_directory(root, **kwargs):
                        for mount in kwargs["mounts"]:
                            if mount["target"] == "/work":
                                descriptors.append(os.open(
                                    mount["source"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                ))
                        return original(root, **kwargs)
                    try:
                        with patch.object(session, "_sandbox_run", retain_owned_directory):
                            with self.assertRaisesRegex(MakeProbeError, expected):
                                session.command(Command(
                                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                                ))
                        for descriptor in descriptors:
                            self.assertEqual(os.fstat(descriptor).st_mode & 0o700, 0o700)
                    finally:
                        # Regression controls remain safe even against the old
                        # guard: restore only retained, explicitly owned fixtures.
                        for descriptor in descriptors:
                            os.fchmod(descriptor, 0o700)
                            for name in ("nested", "locked"):
                                try:
                                    os.chmod(name, 0o700, dir_fd=descriptor, follow_symlinks=False)
                                except FileNotFoundError:
                                    pass
                            os.close(descriptor)
                self.assert_clean(session)

    def test_safe_output_permissions_and_regular_file_fchmod_remain_supported(self):
        self.add("reader.py", (
            "import os,stat\nos.umask(0o077)\nos.mkdir('/work/owned', 0o700)\n"
            "descriptor = os.open('/work/owned/file', os.O_CREAT | os.O_WRONLY, 0o600)\n"
            "os.fchmod(descriptor, 0)\nos.fchmod(descriptor, 0o644)\nos.close(descriptor)\n"
            "os.chmod('/work/owned/file', 0o755)\nos.chmod('/work/owned', 0o700)\n"
            "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.chmod('owned', 0o700, dir_fd=directory)\nos.close(directory)\n"
            "assert stat.S_IMODE(os.stat('/work/owned/file').st_mode) == 0o755\n"
            "print('safe permissions')\n"
        ))
        with self.session() as session:
            output = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
            ))
            self.assertEqual(output.stdout, b"safe permissions\n")
        self.assert_clean(session)

    @contextmanager
    def stopped_tracee(self, setup):
        from scripts.validation_ownership.syscall_guard import ptrace, SETOPTIONS
        child = os.fork()
        if child == 0:
            try:
                setup()
                os._exit(0)
            except BaseException:
                os._exit(125)
        stopped = False
        try:
            waited, status = os.waitpid(child, 0)
            self.assertEqual(waited, child)
            stopped = os.WIFSTOPPED(status)
            self.assertTrue(stopped, status)
            ptrace(SETOPTIONS, child, 0, 0x100000)
            yield child
        finally:
            if stopped:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)

    def test_ptrace_bootstrap_restores_post_drop_memory_observation(self):
        from scripts.validation_ownership.syscall_guard import memory, ptrace, trace_me, TRACEME
        libc = ctypes.CDLL(None, use_errno=True)
        buffer = ctypes.create_string_buffer(b"owned")
        def drop_dumpability():
            if libc.prctl(4, 0, 0, 0, 0):
                raise OSError(ctypes.get_errno(), "cannot model post-setuid dumpability")
        def previous_bootstrap():
            drop_dumpability()
            ptrace(TRACEME, 0)
            os.kill(os.getpid(), signal.SIGSTOP)
        with self.stopped_tracee(previous_bootstrap) as child:
            with self.assertRaises(OSError) as caught:
                memory(child, ctypes.addressof(buffer), 6)
            self.assertEqual(caught.exception.errno, errno.EIO)
        with self.stopped_tracee(lambda: trace_me(drop_dumpability)) as child:
            self.assertEqual(memory(child, ctypes.addressof(buffer), 6), b"owned\0")

    def test_ptrace_pathname_stops_at_nul_before_an_unmapped_page(self):
        from scripts.validation_ownership.syscall_guard import cstring, memory, trace_me, Violation
        libc = ctypes.CDLL(None, use_errno=True)
        libc.mmap.restype = ctypes.c_void_p
        libc.mmap.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_long,
        ]
        libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        page = os.sysconf("SC_PAGE_SIZE")
        address = libc.mmap(None, page * 2, 3, 0x22, -1, 0)
        self.assertNotIn(address, (None, ctypes.c_void_p(-1).value))
        try:
            for payload, expected in (
                (b"end\0", "end"), (b"\xc3\xa9\0", "é"), (b"\0", ""),
                (b"\xff\0", "strict UTF-8"), (b"x" * 4096, "pathname exceeds bound"),
            ):
                with self.subTest(payload_length=len(payload), expected=expected):
                    start = address + page - len(payload)
                    ctypes.memmove(start, payload, len(payload))
                    def unmap_guard_page():
                        if libc.munmap(address + page, page):
                            raise OSError(ctypes.get_errno(), "cannot unmap owned guard page")
                    with self.stopped_tracee(lambda: trace_me(unmap_guard_page)) as child:
                        with self.assertRaises(OSError) as caught:
                            memory(child, address + page - 4, 8)
                        self.assertEqual(caught.exception.errno, errno.EIO)
                        if payload in (b"\xff\0", b"x" * 4096):
                            with self.assertRaisesRegex(Violation, expected):
                                cstring(child, start)
                        else:
                            self.assertEqual(cstring(child, start), expected)
        finally:
            self.assertEqual(libc.munmap(address, page * 2), 0)

    def test_authentic_make_target_and_domain_semantics(self):
        self.add("Makefile", "MODE ?= red\ninclude rules.mk\n")
        self.add("rules.mk", (
            "MODE_DEPS = dep-$(MODE)\n"
            "define rule\n"
            "$(1): $$(MODE_DEPS) | order\n"
            "\t@printf '%s\\n' '$$@'\n"
            "endef\n"
            "$(eval $(call rule,owned))\n"
            "dep-red dep-blue order: ;\n"
            ".PHONY: owned\n"
        ))
        with self.session() as session:
            observations = session.variants(
                "owned", [(), (("command-line", "MODE", "blue"),)],
                variables=("MODE",), owner_inputs=("rules.mk",),
            )
            for observation, expected in zip(observations, ("red", "blue")):
                target = observation.semantics["files"][0]
                self.assertEqual(target["target"], "owned")
                self.assertEqual(target["prerequisites"], [
                    {"name": "dep-" + expected, "order_only": False},
                    {"name": "order", "order_only": True},
                ])
                self.assertIn("printf", target["recipe"])
                self.assertEqual(observation.semantics["domains"]["MODE"]["value"], expected)
            self.assertNotEqual(observations[0].semantic_digest, observations[1].semantic_digest)
        self.assert_clean(session)

    def test_raw_binary_registered_output_and_concrete_source(self):
        self.add("data/value.bin", b"\x00\xff\r\n")
        self.add("reader.py", "import os\nos.write(1, open('data/value.bin', 'rb').read())\n")
        with self.session() as session:
            output = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                code=("reader.py",), sources=("data/value.bin",),
            ))
            self.assertEqual(output.stdout, b"\x00\xff\r\n")
            self.assertEqual(output.consumed, ("data/value.bin",))
        self.assert_clean(session)

    def test_native_prefixed_controls_reproduce_descriptor_forgery(self):
        """Real benign pre-fix Make load/SHELL controls write their inherited FD."""
        channel_path = self.directory / "pre-fix-channel"
        with channel_path.open("wb") as channel:
            fd = channel.fileno()
            self.add("payload.c", (
                "#include <unistd.h>\nint plugin_is_GPL_compatible;\n"
                f"static void payload(void) {{ write({fd}, \"forged\", 6); }}\n"
                "int payload_gmk_setup(void) { payload(); return 1; }\n"
                "int main(void) { payload(); return 0; }\n"
            ))
            for flags, name in ((("-shared", "-fPIC"), "payload.so"), ((), "payload")):
                compiled = subprocess.run(
                    ["/usr/bin/cc", *flags, str(self.root / "payload.c"), "-o", str(self.root / name)],
                    env={**ENVIRONMENT, "TMPDIR": str(self.directory)}, capture_output=True, timeout=20,
                )
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                self.add(name, (self.root / name).read_bytes(), "100755")
            for prefix in (
                "load ./payload.so\n",
                "override SHELL := ./payload\nX := $(shell ignored)\n",
            ):
                with self.subTest(prefix=prefix):
                    self.add("Makefile", prefix + "all: ;\n")
                    channel.seek(0)
                    channel.truncate()
                    before = subprocess.run(
                        ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
                        cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                        pass_fds=(fd,), capture_output=True, timeout=10,
                    )
                    self.assertEqual(before.returncode, 0, before.stderr)
                    self.assertEqual(channel_path.read_bytes(), b"forged")
                    channel.seek(0)
                    channel.truncate()
                    session = self.session()
                    with self.assertRaises(MakeProbeError):
                        with session:
                            session.make("all")
                    self.assertEqual(channel_path.read_bytes(), b"")
                    self.assert_clean(session)

    def test_make_cannot_open_observation_mapping_event_or_fd_paths(self):
        controls = [
            "$(file >/control/events,forged)",
            "$(file >/control/result,VOMAKE1)",
            "$(file </control/map/count)",
            "include /control/result",
            "$(eval $(file >/control/events,forged))",
            "$(file >/proc/self/fd/3,forged)",
            "$(file >/dev/fd/3,forged)",
            "$(file >/repo/../control/result,forged)",
        ]
        for payload in controls:
            with self.subTest(payload=payload):
                self.add("Makefile", payload + "\nall: ;\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "channel denied|namespace denied"):
                    with session:
                        session.make("all")
                self.assert_clean(session)

    def test_make_native_dispatch_and_shell_flags_are_not_authority(self):
        controls = [
            "override SHELL := /usr/bin/make\nX := $(shell --version)\n",
            "override SHELL := /lib64/ld-linux-x86-64.so.2\nX := $(shell ignored)\n",
            "override .SHELLFLAGS := -ec\nX := $(shell printf ok)\n",
            "override SHELL := /repo/native\nX := $(shell ignored)\n",
        ]
        self.add("native", Path("/usr/bin/true").read_bytes(), "100755")
        for payload in controls:
            with self.subTest(payload=payload):
                self.add("Makefile", payload + "all: ;\n")
                session = self.session()
                with self.assertRaises(MakeProbeError):
                    with session:
                        session.make("all")
                self.assert_clean(session)

    def test_stdout_cannot_forge_native_target_or_domain_results(self):
        self.add("Makefile", (
            "$(info VOMAKE1 fake-domain blue)\n"
            "$(info Considering target file 'forged'.)\n"
            "$(info Makefile:1: update target 'all' due to: forged)\n"
            "MODE := red\nall: genuine\n\t@printf ok\n"
            "genuine: ;\n"
        ))
        with self.session() as session:
            result = session.make("all", variables=("MODE",))
            self.assertIn(b"forged", result.stdout)
            self.assertEqual(result.semantics["domains"]["MODE"]["value"], "red")
            self.assertEqual(result.semantics["files"][0]["prerequisites"], [
                {"name": "genuine", "order_only": False},
            ])
            self.assertEqual({item["target"] for item in result.semantics["files"]}, {"all", "genuine"})
        self.assert_clean(session)

    def test_parse_time_file_reads_are_confined_and_source_symlinks_reject(self):
        self.add("owner.txt", b"real")
        self.add("Makefile", "VALUE := $(file <owner.txt)\nall: ;\n")
        with self.session() as session:
            result = session.make("all", variables=("VALUE",), owner_inputs=("owner.txt",))
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "real")
        self.assert_clean(session)
        (self.root / "link").symlink_to("owner.txt")
        self.entries["link"] = GitTreeEntry("link", "120000", "blob", "0" * 40)
        self.add("Makefile", "VALUE := $(file <link)\nall: ;\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "nonregular candidate source"):
            with session:
                session.make("all")
        self.assert_clean(session)

    def test_eager_command_replay_after_parse_failure_is_exact_and_real(self):
        self.add("value.txt", "alpha\n")
        self.add("reader.py", "import os\nos.write(1, open('value.txt', 'rb').read())\n")
        self.add("Makefile", (
            "VALUE := $(shell python3 -I -B reader.py)\n"
            "ifeq ($(VALUE),)\n$(error value has not been supplied)\nendif\nall: ;\n"
        ))
        command = Command(
            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
            code=("reader.py",), sources=("value.txt",),
        )
        with self.session() as session:
            result = session.make(
                "all", variables=("VALUE",), owner_inputs=("Makefile", "value.txt"),
                commands={"python3 -I -B reader.py": command},
            )
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "alpha")
            self.assertTrue(result.events)
            self.assertTrue(all(event["match"] == 0 for event in result.events))
            self.assertEqual(len(session.cache), 1)
        self.assert_clean(session)

    def test_make_identity_excludes_discarded_replay_inputs(self):
        self.add("choice.txt", "genuine first")
        self.add("choose.py", "import os\nos.write(1, open('choice.txt','rb').read().split()[0])\n")
        self.add("discarded.txt", "first unused result")
        self.add("discarded.py", "import os\nos.write(1, open('discarded.txt','rb').read())\n")
        selected = "python3 -I -S -B choose.py"
        discarded = "python3 -I -S -B discarded.py"
        self.add("Makefile", (
            f"SELECT := $(shell {selected})\nifeq ($(SELECT),)\n"
            f"UNUSED := $(shell {discarded})\nendif\n"
            "all: $(SELECT)\n\t@printf '%s' '$^'\ngenuine: ;\n"
        ))
        commands = {
            selected: Command(
                ("/usr/bin/python3", "/repo/choose.py"),
                code=("choose.py",), sources=("choice.txt",),
            ),
            discarded: Command(
                ("/usr/bin/python3", "/repo/discarded.py"),
                code=("discarded.py",), sources=("discarded.txt",),
            ),
        }
        observations = []
        for label, path, value in (
            ("baseline", None, None),
            ("discarded-source-output", "discarded.txt", "second unused result"),
            ("discarded-code", "discarded.py",
             "import os\nos.write(1, open('discarded.txt','rb').read().upper())\n"),
            ("selected-source", "choice.txt", "genuine second"),
            ("selected-code-output", "choose.py",
             "import os\nos.write(1, open('choice.txt','rb').read().split()[0] + b'\\n')\n"),
        ):
            with self.subTest(change=label):
                if path:
                    self.add(path, value)
                normal = subprocess.run(
                    ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
                    env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
                )
                self.assertEqual(normal.stdout, b"genuine")
                with self.session() as session:
                    observed = session.make(
                        "all", variables=("SELECT",), owner_inputs=("Makefile",), commands=commands,
                    )
                    observations.append(observed)
                    self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                        {"name": normal.stdout.decode("ascii"), "order_only": False},
                    ])
                    self.assertEqual([_event_command(event) for event in observed.events], [selected])
                    dynamic = observed.semantics["dynamic_commands"]
                    self.assertEqual(len(dynamic), 1)
                    self.assertEqual(dynamic[0]["command"]["argv"], list(commands[selected].argv))
                    self.assertEqual(
                        {item[0] for item in dynamic[0]["command"]["inputs"]},
                        {"choose.py", "choice.txt"},
                    )
                    self.assertEqual(
                        {result.consumed for result in session.cache.values()},
                        {("choice.txt",), ("discarded.txt",)},
                    )
                self.assert_clean(session)
        self.assertEqual(len({item.semantic_digest for item in observations[:3]}), 1)
        self.assertEqual(len({item.execution_digest for item in observations}), 5)
        self.assertNotEqual(observations[2].semantic_digest, observations[3].semantic_digest)
        self.assertNotEqual(observations[3].semantic_digest, observations[4].semantic_digest)
        outputs = [item.semantics["dynamic_commands"][0]["output_sha256"] for item in observations]
        self.assertEqual(len(set(outputs[:4])), 1)
        self.assertNotEqual(outputs[3], outputs[4])

    def test_make_identity_follows_the_last_late_resolved_branch(self):
        outer = "printf %s enabled"
        inner = "printf %s genuine"
        discarded = "printf %s unused"
        self.add("Makefile", (
            f"OUTER := $(shell {outer})\nifeq ($(OUTER),enabled)\n"
            f"INNER := $(shell {inner})\nifeq ($(INNER),)\n"
            f"UNUSED := $(shell {discarded})\nendif\nendif\nall: $(INNER)\ngenuine: ;\n"
        ))
        commands = {
            key: Command(("/usr/bin/printf", "%s", value))
            for key, value in ((outer, "enabled"), (inner, "genuine"), (discarded, "unused"))
        }
        with self.session() as session:
            observed = session.make(
                "all", variables=("OUTER", "INNER"), owner_inputs=("Makefile",), commands=commands,
            )
            self.assertEqual({_event_command(event) for event in observed.events}, {outer, inner})
            self.assertTrue(all(event["match"] == 0 for event in observed.events))
            self.assertEqual(
                {tuple(item["command"]["argv"]) for item in observed.semantics["dynamic_commands"]},
                {commands[outer].argv, commands[inner].argv},
            )
            self.assertEqual({item.stdout for item in session.cache.values()}, {b"enabled", b"genuine", b"unused"})
        self.assert_clean(session)

    def test_make_final_command_identity_deduplicates_aliases_and_declarations(self):
        self.add("left/value.txt", "left")
        self.add("right/value.txt", "right")
        self.add("reader.py", (
            "import os\nfor path in ('left/value.txt','right/value.txt'):\n"
            " os.write(1, open(path,'rb').read())\n"
        ))
        direct = "python3 -I -S -B reader.py"
        compound = direct + "; printf ''"
        self.add("Makefile", (
            f"FIRST := $(shell {direct})\nSECOND := $(shell {compound})\n"
            f"AGAIN := $(shell {direct})\nall: ;\n"
        ))
        original = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",),
            sources=("left/*.txt", "right/*.txt"), directories=("left", "right"),
        )
        equivalent = replace(
            original, code=("reader.py", "reader.py"),
            sources=("right/value.txt", "left/value.txt"), directories=("right", "left"),
        )
        observations = []
        with self.session() as session:
            for commands in (
                {direct: original, compound: original},
                {compound: equivalent, direct: original},
            ):
                observed = session.make(
                    "all", variables=("FIRST", "SECOND", "AGAIN"),
                    owner_inputs=("Makefile",), commands=commands,
                )
                observations.append(observed)
                self.assertEqual(len(observed.events), 3)
                self.assertEqual({_event_command(event) for event in observed.events}, {direct, compound})
                self.assertEqual(len(observed.semantics["dynamic_commands"]), 1)
                self.assertEqual(
                    {item["value"] for item in observed.semantics["domains"].values()}, {"leftright"},
                )
            self.assertEqual(observations[0].semantic_digest, observations[1].semantic_digest)
        self.assert_clean(session)

    def test_discarded_replay_commands_remain_authorized_and_aggregate_charged(self):
        selected = "printf %s genuine"
        discarded = "python3 -I -S -B discarded.py"
        self.add("declared.txt", "unused")
        self.add("Makefile", (
            f"SELECT := $(shell {selected})\nifeq ($(SELECT),)\n"
            f"UNUSED := $(shell {discarded})\nendif\nall: $(SELECT)\ngenuine: ;\n"
        ))
        commands = {
            selected: Command(("/usr/bin/printf", "%s", "genuine")),
            discarded: Command(
                ("/usr/bin/python3", "/repo/discarded.py"),
                code=("discarded.py",), sources=("declared.txt",),
            ),
        }
        for boundary, expected in (
            ("unregistered", "unregistered eager/recursive"),
            ("failed-source", "declared/consumed source mismatch"),
            ("mapping-quota", "mapping"),
        ):
            with self.subTest(boundary=boundary):
                self.add("discarded.py", (
                    "import os\ntry: os.open('declared.txt', os.O_RDONLY | os.O_DIRECTORY)\n"
                    "except OSError: pass\nprint('unused')\n"
                    if boundary == "failed-source"
                    else "print(open('declared.txt').read())\n"
                ))
                limits = {"mapping_bytes": len(selected.encode("utf-8")) + len(b"genuine") + 4}
                session = self.session(**(limits if boundary == "mapping-quota" else {}))
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.make("all", commands=(
                            {selected: commands[selected]} if boundary == "unregistered" else commands
                        ))
                self.assert_clean(session)

    def test_registry_source_open_mmap_stat_and_directory_glob_are_real(self):
        self.add("data/a.bin", b"ab")
        self.add("data/b.bin", b"cd")
        self.add("reader.py", (
            "import glob, mmap, os\n"
            "paths = sorted(glob.glob('data/*.bin'))\n"
            "for path in paths:\n"
            " assert os.stat(path).st_size == 2\n"
            " with open(path, 'rb') as source:\n"
            "  with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as view:\n"
            "   os.write(1, view[:])\n"
        ))
        with self.session() as session:
            output = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                code=("reader.py",), sources=("data/*.bin",), directories=("data",),
            ))
            self.assertEqual(output.stdout, b"abcd")
            self.assertEqual(output.consumed, ("data/a.bin", "data/b.bin"))
        self.assert_clean(session)

    def test_undeclared_sources_reject_even_when_errors_are_caught(self):
        self.add("data/admitted", "yes")
        self.add("hidden/value", "secret-test-fixture")
        operations = [
            "open('hidden/value').read()",
            "os.stat('hidden/value')",
            "os.lstat('hidden/value')",
            "os.access('hidden/value', os.R_OK)",
            "os.listdir('hidden')",
            "list(glob.iglob('hidden/*'))",
            "os.readlink('hidden/value')",
            "mmap.mmap(os.open('hidden/value', os.O_RDONLY), 0, access=mmap.ACCESS_READ)",
            "os.stat('hid' + 'den/' + 'value')",
            "os.stat('/usr/share/doc')",
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import glob, mmap, os\nopen('data/admitted').read()\n"
                    "try:\n " + operation + "\nexcept OSError:\n pass\nprint('accepted')\n"
                ))
                # Negative control uses the same real function and files without
                # confinement, rather than a string assertion about its source.
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, capture_output=True, timeout=10, env=ENVIRONMENT,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertIn(b"accepted", before.stdout)
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                            code=("reader.py",), sources=("data/admitted",),
                        ))
                self.assert_clean(session)

    def test_registry_cannot_reach_channels_descriptors_or_escape_syscalls(self):
        operations = [
            "open('/control/events', 'wb').write(b'forged')",
            "open('/control/result', 'wb').write(b'forged')",
            "open('/proc/self/fd/3', 'rb').read()",
            "os.fstat(3)",
            "os.open('/repo', os.O_PATH); os.fstat(100)",
            "ctypes.CDLL(None).syscall(101, 0, 0, 0, 0)",
            "ctypes.CDLL(None).syscall(319, b'payload', 0)",
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes, os\ntry:\n " + operation
                    + "\nexcept OSError:\n pass\nprint('accepted')\n"
                ))
                session = self.session()
                with self.assertRaises(MakeProbeError):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_declared_reported_and_consumed_sources_must_agree(self):
        self.add("data/a", "a")
        self.add("data/b", "b")
        for read, reported, expected in (
            ("open('data/a').read()", ["data/a"], "declared/consumed"),
            ("open('data/a').read(); open('data/b').read()", ["data/a"], "declared/reported/consumed"),
        ):
            self.add("reader.py", read + "\nprint(" + repr(json.dumps({
                "name": "fixture", "version": 1, "record_count": 2, "source_paths": reported,
            })) + ")\n")
            session = self.session()
            with self.assertRaisesRegex(MakeProbeError, expected):
                with session:
                    session.registry(Command(
                        ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                        code=("reader.py",), sources=("data/a", "data/b"),
                    ))
            self.assert_clean(session)

    def test_semantic_identity_excludes_unrelated_snapshot_and_live_cache_drift(self):
        self.add("Makefile", "VALUE := $(file <owner.txt)\nall: ;\n")
        self.add("owner.txt", "one")
        self.add("notes.md", "documentation")
        self.add("other.c", "int unrelated;\n")
        identities = []
        for path, content in (
            (None, None), ("notes.md", "new unrelated documentation"),
            ("other.c", "int unrelated = 2;\n"), ("owner.txt", "two"),
        ):
            if path:
                # Deliberately do not change Git entry IDs: live-byte identity
                # must not accidentally reuse an index-only command namespace.
                (self.root / path).write_text(content)
            with self.session() as session:
                result = session.make("all", variables=("VALUE",), owner_inputs=("Makefile", "owner.txt"))
                output = session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "-c", "import os; os.write(1, open('owner.txt','rb').read())"),
                    sources=("owner.txt",),
                ))
                identities.append((result.semantic_digest, result.execution_digest, output.stdout))
            self.assert_clean(session)
        self.assertEqual(len({row[0] for row in identities[:3]}), 1)
        self.assertEqual(len({row[1] for row in identities}), 4)
        self.assertNotEqual(identities[0][0], identities[3][0])
        self.assertEqual([row[2] for row in identities], [b"one", b"one", b"one", b"two"])

    def test_snapshot_is_stable_within_one_session(self):
        self.add("owner.txt", "one")
        command = Command(
            ("/usr/bin/python3", "-I", "-B", "-c", "import os; os.write(1,open('owner.txt','rb').read())"),
            sources=("owner.txt",),
        )
        with self.session() as session:
            first = session.command(command)
            (self.root / "owner.txt").write_text("changed")
            second = session.command(command)
            self.assertIs(first, second)
            self.assertEqual(second.stdout, b"one")
        self.assert_clean(session)

    def test_variant_limit_rejects_before_any_variant_launch(self):
        self.add("Makefile", "all: ;\n")
        with self.session(states=2) as session:
            runs = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "before launch"):
                session.variants("all", [(), (), ()])
            self.assertEqual(session.budget.runs, runs)
        self.assert_clean(session)

    def test_global_deadline_is_not_reset_per_process(self):
        before = time.monotonic()
        for _ in range(2):
            subprocess.run(
                ["/usr/bin/python3", "-I", "-c", "import time; time.sleep(0.18)"],
                env=ENVIRONMENT, timeout=0.3, check=True,
            )
        self.assertGreater(time.monotonic() - before, 0.3)
        budget = ProbeBudget(Limits(seconds=0.3))
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            for _ in range(2):
                budget.run(
                    ["/usr/bin/python3", "-I", "-c", "import time; time.sleep(0.18)"],
                    env=ENVIRONMENT,
                )
        self.assertFalse(budget.children)
        self.assertLess(time.monotonic() - budget.started, 1.5)

    def test_streaming_output_cache_and_scratch_storage_are_aggregate_bounded(self):
        for limits, program, expected in (
            ({"process_output_bytes": 512}, "import os\nos.write(1,b'x'*10000)", "output"),
            ({"cache_bytes": 200}, "import os\nos.write(1,b'x'*300)", "cache"),
            ({"sandbox_bytes": 2048}, "open('/work/a','wb').write(b'x'*5000)", "aggregate sandbox"),
            ({"created_files": 2}, "[open('/work/'+str(n),'wb').close() for n in range(4)]", "creation"),
        ):
            with self.subTest(limits=limits):
                self.add("reader.py", program)
                session = self.session(**limits)
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_candidate_symlink_and_relocated_directory_aliases_reject(self):
        controls = [
            "os.symlink('../../repo', '/work/alias')\n"
            "assert os.access('/work/alias/reader.py', os.R_OK)\n"
            "assert not os.access('/work/alias/undeclared', os.R_OK)\n",
            "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.symlink('../../repo', 'alias', dir_fd=directory)\n",
            "os.rename('/work/a/b', '/work/b')\n"
            "assert os.access('../../../repo/reader.py', os.R_OK)\n"
            "assert not os.access('../../../repo/undeclared', os.R_OK)\n",
            "directory = os.open('.', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.rename('/work/a/b', '/work/b')\n"
            "assert os.access('../../../repo/reader.py', os.R_OK, dir_fd=directory)\n",
            "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.rename('a/b', 'b', src_dir_fd=directory, dst_dir_fd=directory)\n"
            "assert os.access('../../../repo/reader.py', os.R_OK)\n",
            "assert ctypes.CDLL(None).syscall(316, -100, b'/work/a/b', -100, b'/work/b', 0) == 0\n"
            "assert os.access('../../../repo/reader.py', os.R_OK)\n",
        ]
        for operation in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes, os\nos.makedirs('/work/a/b/c')\nos.chdir('/work/a/b/c')\n"
                    + operation + "print('alias admitted')\n"
                ))
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "symlink creation|directory-entry relocation"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_trusted_runtime_symlinks_use_the_authorized_source_destination(self):
        runtime = self.directory / "runtime"
        runtime.mkdir()
        (runtime / "alias").symlink_to("../../../../repo")
        (runtime / "absolute").symlink_to("/repo")
        (runtime / "file").symlink_to("/repo/data/admitted")
        self.add("data/admitted", b"owned")
        prefix = "/usr/lib/x86_64-linux-gnu/gconv/"
        for name in ("alias", "absolute", "alias/../repo"):
            for operation, accepted in (
                (
                    "descriptor = os.open(alias + '/data/admitted', os.O_RDONLY)\n"
                    "descriptor = os.dup(descriptor)\n"
                    "with mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ) as view:\n"
                    " os.write(1, view[:])\n", True,
                ),
                ("os.access(alias + '/undeclared', os.R_OK)\n", False),
                ("os.chdir(alias)\nos.access('undeclared', os.R_OK)\n", False),
                (
                    "directory = os.open(alias, os.O_RDONLY | os.O_DIRECTORY)\n"
                    "os.access('undeclared', os.R_OK, dir_fd=directory)\n", False,
                ),
            ):
                with self.subTest(alias=name, operation=operation):
                    self.add("reader.py", (
                        "import mmap, os\nalias = " + repr(prefix + name) + "\n" + operation
                    ))
                    session = self.session()
                    with session:
                        run = session._sandbox_run
                        def with_runtime_alias(root, **kwargs):
                            kwargs["mounts"].append(session._mount(runtime, prefix.rstrip("/")))
                            return run(root, **kwargs)
                        with patch.object(session, "_sandbox_run", with_runtime_alias):
                            command = Command(
                                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                                code=("reader.py",), sources=("data/admitted",) if accepted else (),
                            )
                            if accepted:
                                result = session.command(command)
                                self.assertEqual(result.stdout, b"owned")
                                self.assertEqual(result.consumed, ("data/admitted",))
                            else:
                                with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                                    session.command(command)
                    self.assert_clean(session)
        self.add("reader.py", (
            "import os, stat\nlink = " + repr(prefix + "file") + "\n"
            "assert stat.S_ISLNK(os.lstat(link).st_mode)\n"
            "assert os.readlink(link) == '/repo/data/admitted'\n"
            "descriptor = os.open(link, os.O_PATH | os.O_NOFOLLOW)\n"
            "assert stat.S_ISLNK(os.fstat(descriptor).st_mode)\n"
            "assert os.readlink('', dir_fd=descriptor) == '/repo/data/admitted'\n"
            "os.close(descriptor)\nprint('nofollow metadata')\n"
        ))
        with self.session() as session:
            run = session._sandbox_run
            def with_runtime_alias(root, **kwargs):
                kwargs["mounts"].append(session._mount(runtime, prefix.rstrip("/")))
                return run(root, **kwargs)
            with patch.object(session, "_sandbox_run", with_runtime_alias):
                result = session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
                self.assertEqual(result.stdout, b"nofollow metadata\n")
                self.assertEqual(result.consumed, ())
        self.assert_clean(session)

    @staticmethod
    def mapping_program(operation):
        return (
            "import ctypes, mmap, os\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "libc.mmap.restype = ctypes.c_void_p\n"
            "libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, "
            "ctypes.c_int, ctypes.c_int, ctypes.c_long]\n"
            "libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]\n"
            "libc.mremap.restype = ctypes.c_void_p\n"
            "libc.mremap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, "
            "ctypes.c_size_t, ctypes.c_int, ctypes.c_void_p]\n"
            "libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]\n"
            "def mapping(protection, flags, descriptor=-1):\n"
            " address = libc.mmap(None, 4096, protection, flags, descriptor, 0)\n"
            " assert address not in (None, ctypes.c_void_p(-1).value), ctypes.get_errno()\n"
            " return address\n"
            "def child_write(address):\n"
            " child = os.fork()\n"
            " if child == 0:\n"
            "  ctypes.memmove(address, b'child\\0', 6)\n"
            "  os._exit(0)\n"
            " assert os.waitpid(child, 0)[1] == 0\n"
            + operation
        )

    def test_shared_mapping_protection_upgrade_and_fork_reject(self):
        for protection in (0, 1):
            with self.subTest(protection=protection):
                self.add("reader.py", self.mapping_program(
                    f"address = mapping({protection}, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)\n"
                    "assert libc.mprotect(address, 4096, 3) == 0\n"
                    "ctypes.memmove(address, b'parent\\0', 7)\n"
                    "child_write(address)\n"
                    "assert ctypes.string_at(address, 6) == b'child\\0'\n"
                    "class Vector(ctypes.Structure):\n"
                    " _fields_ = [('base', ctypes.c_void_p), ('length', ctypes.c_size_t)]\n"
                    "vector = Vector.from_address(address + 128)\n"
                    "child = os.fork()\n"
                    "if child == 0:\n"
                    " ctypes.memmove(address, b'reader.py\\0', 10)\n"
                    " ctypes.memmove(address + 256, b'child\\n', 6)\n"
                    " vector.base, vector.length = address + 256, 6\n"
                    " os._exit(0)\n"
                    "assert os.waitpid(child, 0)[1] == 0\n"
                    "assert libc.access(ctypes.c_void_p(address), os.R_OK) == 0\n"
                    "assert libc.writev(1, ctypes.byref(vector), 1) == 6\n"
                    "assert libc.munmap(address, 4096) == 0\nprint('shared across fork')\n"
                ))
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=10,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertEqual(before.stdout, b"child\nshared across fork\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "shared anonymous"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_mutable_backing_file_mappings_reject_even_readonly_private_aliases(self):
        for flags in ("mmap.MAP_PRIVATE", "mmap.MAP_SHARED"):
            with self.subTest(flags=flags):
                self.add("reader.py", self.mapping_program(
                    "path = os.environ.get('MAPPING_FIXTURE', '/work/backing')\n"
                    "with open(path, 'wb') as stream: stream.write(b'parent\\0' + b'\\0'*4089)\n"
                    "original = os.open(path, os.O_RDONLY)\n"
                    "descriptor = os.dup(original)\nos.close(original)\n"
                    f"address = mapping(1, {flags}, descriptor)\nos.close(descriptor)\n"
                    "child = os.fork()\n"
                    "if child == 0:\n"
                    " descriptor = os.open(path, os.O_WRONLY)\n"
                    " assert os.pwrite(descriptor, b'child\\0', 0) == 6\n"
                    " os.close(descriptor)\n os._exit(0)\n"
                    "assert os.waitpid(child, 0)[1] == 0\n"
                    "assert ctypes.string_at(address, 6) == b'child\\0'\n"
                    "assert libc.munmap(address, 4096) == 0\nprint('mutable backing observed')\n"
                ))
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, env={
                        **ENVIRONMENT, "MAPPING_FIXTURE": str(self.directory / "backing"),
                    }, capture_output=True, timeout=10,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertEqual(before.stdout, b"mutable backing observed\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "mutable backing"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_protection_and_remap_alias_families_reject(self):
        self.add("data/page", b"immutable" + b"\0" * (4096 - 9))
        controls = [
            (
                "address = mapping(1, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
                "assert libc.mprotect(address, 4096, 3) == 0\n",
                (), "writable memory protection",
            ),
            (
                "descriptor = os.open('data/page', os.O_RDONLY)\n"
                "address = mapping(1, mmap.MAP_SHARED, descriptor)\n"
                "alias = libc.mremap(address, 0, 4096, 1, None)\n"
                "assert alias != ctypes.c_void_p(-1).value\n"
                "assert ctypes.string_at(alias, 9) == b'immutable'\n",
                ("data/page",), "remap alias",
            ),
        ]
        for flags in (3, 5, 7):
            controls.append((
                "address = mapping(3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
                "destination = mapping(3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
                "ctypes.memmove(address, b'private\\0', 8)\n"
                f"alias = libc.mremap(address, 4096, 4096, {flags}, destination)\n"
                "assert alias != ctypes.c_void_p(-1).value\n"
                "assert ctypes.string_at(alias, 8) == b'private\\0'\n",
                (), "remap alias",
            ))
        for operation, sources, expected in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", self.mapping_program(operation + "print('upgrade admitted')\n"))
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=10,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertEqual(before.stdout, b"upgrade admitted\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                            code=("reader.py",), sources=sources,
                        ))
                self.assert_clean(session)

    def test_private_mapping_resize_fork_and_read_protection_stay_supported(self):
        self.add("reader.py", self.mapping_program(
            "address = mapping(3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
            "ctypes.memmove(address, b'parent\\0', 7)\nchild_write(address)\n"
            "assert ctypes.string_at(address, 7) == b'parent\\0'\n"
            "address = libc.mremap(address, 4096, 8192, 1, None)\n"
            "assert address != ctypes.c_void_p(-1).value\n"
            "assert ctypes.string_at(address, 7) == b'parent\\0'\n"
            "assert libc.mprotect(address, 8192, 1) == 0\n"
            "assert libc.munmap(address, 8192) == 0\n"
            "print('private fork and resize')\n"
        ))
        with self.session() as session:
            result = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
            ))
            self.assertEqual(result.stdout, b"private fork and resize\n")
        self.assert_clean(session)

    def test_shared_clone_state_rejects_but_suspended_parent_spawn_stays_supported(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sched.h>\n#include <signal.h>\n"
            "#include <stdio.h>\n#include <stdlib.h>\n#include <sys/mman.h>\n"
            "#include <sys/wait.h>\n#include <unistd.h>\n"
            "static int child(void *argument) { (void)argument; _exit(0); }\n"
            "int main(int argc, char **argv) {\n"
            " if(argc != 2) return 2;\n"
            " void *stack = mmap(NULL, 16384, PROT_READ | PROT_WRITE,\n"
            "  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);\n"
            " if(stack == MAP_FAILED) return 3;\n"
            " int flags = (int)strtoul(argv[1], NULL, 0) | SIGCHLD;\n"
            " pid_t pid = clone(child, (char *)stack + 16384, flags, NULL);\n"
            " int status;\n"
            " if(pid < 0 || waitpid(pid, &status, 0) != pid || status != 0) return 4;\n"
            " if(munmap(stack, 16384)) return 5;\n"
            " puts(\"owned child reaped\"); return 0;\n}\n"
        ))
        with self.session() as session:
            tool = session.compile_native(("native.c",))
            for flags in (0, 0x100 | 0x4000):
                self.assertEqual(session.native(tool, (str(flags),)).stdout, b"owned child reaped\n")
        self.assert_clean(session)
        for flags in (0x100, 0x200, 0x400):
            with self.subTest(flags=flags):
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "shared-memory candidate threads|shared-state clone"):
                    with session:
                        tool = session.compile_native(("native.c",))
                        session.native(tool, (str(flags),))
                self.assert_clean(session)

    def test_alternate_memory_alias_and_creation_interfaces_remain_fail_closed(self):
        # Invalid IDs/addresses keep these calls harmless even if an admission
        # regression lets the kernel see them. No global IPC object is created.
        for number in (29, 30, 31, 67, 133, 216, 259, 310, 311, 319, 323, 329, 425, 437, 440):
            with self.subTest(syscall=number):
                self.add("reader.py", (
                    "import ctypes\n"
                    f"ctypes.CDLL(None).syscall({number}, -1, -1, -1, -1, -1, -1)\n"
                ))
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, f"unadmitted syscall {number}"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_all_supported_creation_attempts_reserve_the_aggregate_quota(self):
        controls = [
            ("", "os.close(os.open('/work/'+str(index), os.O_CREAT | os.O_WRONLY, 0o600))"),
            ("", "os.close(libc.syscall(85, ('/work/'+str(index)).encode(), 0o600))"),
            ("", "os.mkdir('/work/'+str(index))"),
            ("", "os.mkdir(str(index), dir_fd=directory)"),
            ("", "os.close(os.open('/work', os.O_TMPFILE | os.O_RDWR, 0o600))"),
            ("", "os.close(libc.syscall(2, b'/work', os.O_TMPFILE | os.O_RDWR, 0o600))"),
            ("open('/work/seed', 'wb').close()\n", "os.link('/work/seed', '/work/'+str(index))"),
            ("open('/work/seed', 'wb').close()\n",
             "os.link('seed', str(index), src_dir_fd=directory, dst_dir_fd=directory)"),
        ]
        for setup, operation in controls:
            for allowed in (True, False):
                with self.subTest(operation=operation, allowed=allowed):
                    self.add("reader.py", (
                        "import ctypes, os\nlibc = ctypes.CDLL(None)\nos.chdir('/work')\n"
                        "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
                        + setup + "for index in range(2):\n " + operation + "\nprint('created')\n"
                    ))
                    creations = 2 + bool(setup)
                    session = self.session(created_files=creations if allowed else 1)
                    if allowed:
                        with session:
                            output = session.command(Command(
                                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                            ))
                            self.assertEqual(output.stdout, b"created\n")
                            self.assertEqual(session.files_created, creations)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "file-creation budget"):
                            with session:
                                session.command(Command(
                                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                                ))
                        self.assertEqual(session.files_created, 2)
                    self.assert_clean(session)

    def test_unsupported_creation_and_empty_path_link_cannot_bypass_low_quota(self):
        controls = [
            ("os.symlink('missing', '/work/link')", "symlink creation"),
            ("os.symlink('missing', 'link', dir_fd=directory)", "symlink creation"),
            ("os.mkfifo('/work/fifo')", "unadmitted syscall"),
            (
                "descriptor = os.open('/work', os.O_TMPFILE | os.O_RDWR, 0o600)\n"
                "libc.syscall(265, descriptor, b'', directory, b'link', 0x1000)",
                "file-creation budget",
            ),
        ]
        for operation, expected in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes, os\nlibc = ctypes.CDLL(None)\nos.chdir('/work')\n"
                    "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
                    + operation + "\nprint('unsupported creation admitted')\n"
                ))
                session = self.session(created_files=1)
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_creation_quota_is_not_reset_between_commands(self):
        self.add("Makefile", "all: ;\n")
        with self.session(created_files=1) as session:
            for index in range(2):
                command = Command((
                    "/usr/bin/python3", "-I", "-B", "-c",
                    "import os; os.close(os.open('/work', os.O_TMPFILE | os.O_RDWR, 0o600))",
                    str(index),
                ))
                if index == 0:
                    session.command(command)
                    self.assertEqual(session.files_created, 1)
                else:
                    with self.assertRaisesRegex(MakeProbeError, "file-creation budget"):
                        session.command(command)
        self.assert_clean(session)

    def test_fanout_bound_and_parent_interrupt_clean_descendants(self):
        self.add("reader.py", (
            "import os,time\n"
            "for n in range(5):\n"
            " if os.fork()==0:\n"
            "  time.sleep(20)\n"
            "  os._exit(0)\n"
            "time.sleep(20)\n"
        ))
        session = self.session(processes=3)
        with self.assertRaisesRegex(MakeProbeError, "descendant-process"):
            with session:
                session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)
        self.add("reader.py", "import os,time\nif os.fork()==0: time.sleep(20)\ntime.sleep(20)\n")
        session = self.session()
        with self.assertRaises(KeyboardInterrupt):
            with session:
                timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
                timer.start()
                try:
                    session.command(Command(
                        ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                    ))
                finally:
                    timer.cancel()
                    timer.join()
        self.assert_clean(session)

    def test_strict_named_protocols_reject_binary_and_truncated_frames(self):
        self.add("reader.py", "import os\nos.write(1,b'\\xff\\x00\\r\\n')\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "strict utf-8"):
            with session:
                session.registry(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)
        for raw in (b"", b"VOMAKE1\0", b"forged", b"VOMAKE1\0" + b"\xff" * 4):
            with self.assertRaises(MakeProbeError):
                _read_observation(raw, "all", ())
        for raw in (b"x", b"\xff" * 20, b"\0" * 20):
            with self.assertRaises(MakeProbeError):
                _read_events(raw, expected_mapping_count=0)

    def test_worktree_symlink_escape_and_invalid_limit_values_fail(self):
        self.add("Makefile", "all: ;\n")
        self.add("nested/owner", "data")
        (self.root / "nested/owner").unlink()
        (self.root / "nested").rmdir()
        (self.root / "nested").symlink_to(self.directory)
        with self.assertRaises(MakeProbeError):
            with self.session():
                self.fail("symlinked source must not be materialized")
        self.assertFalse(self.scratch.exists())
        for value in (0, -1, float("nan"), float("inf"), 3601, True):
            with self.assertRaises(MakeProbeError):
                Limits(seconds=value)

    def test_pattern_rules_and_target_specific_variable_values_are_native(self):
        self.add("Makefile", (
            "LOCAL = global\n"
            "%.out: LOCAL = pattern-$*\n"
            "%.out: %.src | order\n\t@printf '%s' '$(LOCAL)'\n"
            "foo.out: LOCAL = target-$*\n"
            "foo.src bar.src order: ;\n"
        ))
        with self.session() as session:
            for target, expected in (("foo.out", "target-foo"), ("bar.out", "pattern-bar")):
                result = session.make(target, variables=("LOCAL",))
                self.assertEqual(result.semantics["files"][0]["variables"]["LOCAL"]["value"], expected)
                self.assertEqual(result.semantics["domains"]["LOCAL"]["value"], "global")
                self.assertEqual(result.semantics["files"][0]["prerequisites"], [
                    {"name": target.replace(".out", ".src"), "order_only": False},
                    {"name": "order", "order_only": True},
                ])
        self.assert_clean(session)

    def test_event_mapping_and_pending_byte_bounds_cover_real_commands(self):
        value = "x" * 512
        source_command = "printf %s " + value
        self.add("Makefile", "VALUE := $(shell " + source_command + ")\nall: ;\n")
        commands = {source_command: Command(("/usr/bin/printf", "%s", value))}
        with self.session() as session:
            result = session.make("all", variables=("VALUE",), commands=commands)
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], value)
        self.assert_clean(session)
        for limits in ({"event_bytes": 96}, {"mapping_bytes": 32}):
            with self.subTest(limits=limits):
                session = self.session(**limits)
                with self.assertRaises(MakeProbeError):
                    with session:
                        session.make("all", variables=("VALUE",), commands=commands)
                self.assert_clean(session)
        with self.session(pending_bytes=8192) as session:
            remaining = session.budget.limits.pending_bytes - session.budget.bytes.get("pending", 0)
            previous_runs = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "pending byte"):
                session.command(Command(("/usr/bin/printf", "%s", "x" * remaining)))
            self.assertEqual(previous_runs, session.budget.runs)
        self.assert_clean(session)

    def test_native_tools_compile_and_run_only_in_channel_free_capsules(self):
        self.add("native.c", (
            "#include <stdio.h>\n"
            "int main(void) { int ch; FILE *f=fopen(\"data/value\", \"rb\");"
            "if(!f) return 2; while((ch=fgetc(f))!=EOF) putchar(ch); return fclose(f); }\n"
        ))
        self.add("data/value", b"native\x00\xff")
        with self.session() as session:
            tool = session.compile_native(("native.c",))
            output = session.native(tool, sources=("data/value",))
            self.assertEqual(output.stdout, b"native\x00\xff")
            self.assertEqual(output.consumed, ("data/value",))
            self.assertTrue(tool.path.is_file())
            self.assertEqual(tool.path.read_bytes()[:4], b"\x7fELF")
            tool.path.chmod(0o700)
            tool.path.write_bytes(b"not the sealed ELF")
            with self.assertRaisesRegex(MakeProbeError, "sealed native tool changed"):
                session.native(tool, sources=("data/value",))
        self.assert_clean(session)

    def test_native_candidate_cannot_write_channels_or_read_inherited_fds(self):
        for operation in (
            'fopen("/control/events", "wb")',
            'fdopen(3, "w")',
        ):
            self.add("native.c", (
                "#define _POSIX_C_SOURCE 200809L\n#include <stdio.h>\n"
                f"int main(void) {{ FILE *f = {operation};"
                'if(f) { fputs("forged", f); fclose(f); } return 0; }\n'
            ))
            session = self.session()
            with self.assertRaises(MakeProbeError):
                with session:
                    tool = session.compile_native(("native.c",))
                    session.native(tool)
            self.assert_clean(session)

    def test_real_immutable_tree_consumer_reports_make_and_bundle_sources(self):
        from scripts.validation_ownership import consumer
        sessions, budgets, registry_sessions = [], [], []
        initialize = consumer.ProbeSession.__init__
        entries, registry = consumer.git_tree_entries, consumer.probe_generated_registry
        def creating(session, *args, **kwargs):
            initialize(session, *args, **kwargs)
            sessions.append(session)
        def loading(*args, **kwargs):
            budgets.append(kwargs["budget"])
            return entries(*args, **kwargs)
        def discovering(loader, *, command, session):
            self.assertIs(loader, session.loader)
            self.assertIs(session.budget, budgets[0])
            self.assertGreater(session.budget.states, 0)
            registry_sessions.append(session)
            return registry(loader, command=command, session=session)
        with patch.object(consumer.ProbeSession, "__init__", creating), patch.object(
            consumer, "git_tree_entries", loading,
        ), patch.object(consumer, "probe_generated_registry", discovering):
            result = consumer.check(ROOT, "HEAD")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(registry_sessions, sessions)
        self.assertEqual(budgets, [sessions[0].budget])
        self.assertEqual(result["scope"], "ownership-probe-foundation")
        self.assertEqual(result["make"]["target"], "localization-check")
        self.assertEqual(result["make"]["semantics"]["files"][0]["prerequisites"], [
            {"name": "localization-generate", "order_only": False},
        ])
        self.assertEqual(result["generated_registry"]["name"], "chapterbundle")
        self.assertEqual(result["generated_registry"]["source_paths"], ["src/data/ch2_bundle.json"])
        self.assertGreater(result["generated_registry"]["record_count"], 0)

    def test_cxx_native_compilation_and_worker_failure_are_confined(self):
        self.add("native.cpp", "#include <cstdio>\nint main() { std::puts(\"cxx\"); }\n")
        with self.session() as session:
            tool = session.compile_native(("native.cpp",), cxx=True)
            self.assertEqual(session.native(tool).stdout, b"cxx\n")
        self.assert_clean(session)
        self.add("reader.py", "import os\nos._exit(7)\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "unsuccessfully: 7"):
            with session:
                session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)

    def test_fifo_input_and_malformed_native_result_do_not_leave_residue(self):
        self.add("Makefile", "all: ;\n")
        (self.root / "Makefile").unlink()
        os.mkfifo(self.root / "Makefile")
        started = time.monotonic()
        with self.assertRaises(MakeProbeError):
            with self.session():
                self.fail("FIFO input was admitted as a regular source")
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(self.scratch.exists())
        for data in (b"", b"\x7fELF", b"\0" * 128):
            with self.assertRaises(MakeProbeError):
                ProbeSession._validate_native(data)

    def test_direct_argument_boundaries_cannot_collide_and_quote_refactors_survive(self):
        registration = Command(("/usr/bin/printf", "%s", "a b"))
        commands = {
            "printf %s 'a b'": registration,
            'printf "%s" "a b"': registration,
        }
        values = []
        for expression in ("printf %s 'a b'", 'printf "%s" "a b"'):
            # Isolate argv semantics from the separate identity of recipe-owning
            # source bytes: this goal deliberately has no recipe.
            self.add("Makefile", "VALUE := $(shell " + expression + ")\n.PHONY: all\nall:\n")
            with self.session() as session:
                result = session.make("all", variables=("VALUE",), commands=commands)
                self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "a b")
                values.append(result.semantic_digest)
            self.assert_clean(session)
        self.assertEqual(values[0], values[1])
        self.add("Makefile", "VALUE := $(shell printf %s a b)\nall: ;\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "unregistered eager"):
            with session:
                session.make("all", variables=("VALUE",), commands=commands)
        self.assert_clean(session)

    def test_live_symlink_and_mode_state_bind_execution_not_unrelated_owner(self):
        self.add("Makefile", "all: ;\n")
        self.add("data/one", "one")
        self.add("data/two", "two")
        link = self.root / "data/link"
        link.symlink_to("one")
        self.entries["data/link"] = GitTreeEntry("data/link", "120000", "blob", "0" * 40)
        states = []
        for change in (None, "mode", "symlink"):
            if change == "mode":
                (self.root / "data/one").chmod(0o755)
            elif change == "symlink":
                link.unlink()
                link.symlink_to("two")
            with self.session() as session:
                result = session.make("all")
                states.append((result.execution_digest, result.semantic_digest))
            self.assert_clean(session)
        self.assertEqual(len({row[0] for row in states}), 3)
        self.assertEqual(len({row[1] for row in states}), 1)
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "symlink/gitlink"):
                session.sources(("data/*",))

    def test_memory_and_filesystem_observations_are_aggregate_bounded(self):
        self.add("reader.py", (
            "import os,time\n"
            "allocation = bytearray(16*1024*1024)\n"
            "for index in range(6):\n"
            " if os.fork()==0:\n"
            "  time.sleep(20)\n"
            "  os._exit(0)\n"
            "time.sleep(20)\n"
        ))
        session = self.session(address_space_bytes=96 * 1024 * 1024)
        with self.assertRaisesRegex(MakeProbeError, "aggregate address-space"):
            with session:
                session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)
        self.add("Makefile", "all: ;\n")
        with self.session(entries=64) as session:
            session.make("all")
        self.assert_clean(session)
        self.add("Makefile", "$(foreach n," + " ".join(map(str, range(200))) + ",$(file <missing$(n)))\nall: ;\n")
        session = self.session(entries=64)
        with self.assertRaisesRegex(MakeProbeError, "filesystem-observation"):
            with session:
                session.make("all")
        self.assert_clean(session)

    def test_one_session_cannot_hide_parallel_workers(self):
        self.add("Makefile", "all: ;\n")
        errors = []
        with self.session() as session:
            runs = session.budget.runs
            def other_worker():
                try:
                    session.make("all")
                except MakeProbeError as error:
                    errors.append(error)
            worker = threading.Thread(target=other_worker)
            worker.start()
            worker.join()
            self.assertEqual(len(errors), 1)
            self.assertEqual(session.budget.runs, runs)
            self.assertTrue(session.budget.failed)
        self.assert_clean(session)


if __name__ == "__main__":
    unittest.main()
