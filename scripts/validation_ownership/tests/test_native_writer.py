"""Original native job admission and regular-file writer cases."""

import errno
import hashlib
import json
import os
import unittest
from unittest.mock import patch

from scripts.validation_ownership.authority import encoded, parse_json
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.tests import test_foundation as foundation


class NativeWriterTests(unittest.TestCase):
    setUp = foundation.FoundationTests.setUp
    tearDown = foundation.FoundationTests.tearDown
    add = foundation.FoundationTests.add
    session = foundation.FoundationTests.session
    assert_clean = foundation.FoundationTests.assert_clean
    native_supervisor = foundation.FoundationTests.native_supervisor

    def test_native_command_admission_runs_original_jobs_once_without_replay(self):
        shell = "printf '%s' \"$$\""
        self.add("Makefile", (
            "FIRST := $(shell " + shell.replace("$", "$$") + ")\n"
            "SECOND := $(shell " + shell.replace("$", "$$") + ")\n"
            "all:\n\t@printf '%s\\n' '$(FIRST)' '$(SECOND)'\n"
        ))
        requests = []
        class Commands:
            def __getitem__(self, argv):
                requests.append(argv)
                return Command(argv)
        session = self.session()
        with session:
            completed, semantics, observed = session._native_make_readonly(
                "all", variables=("FIRST", "SECOND"), observe_reads=True,
                observe_runtime_completions=True, commands=Commands(),
                native_executables=("/usr/bin/printf",),
            )
            jobs = sorted([
                parse_json(row.removeprefix("native-job:").encode(), "original admitted job")
                for row in observed["accessed"] if row.startswith("native-job:")
            ], key=lambda row: row["sequence"])
            self.assertEqual(len(requests), 3)
            self.assertEqual(len(jobs), 3)
            self.assertEqual([tuple(row["argv"]) for row in jobs], requests)
            self.assertEqual(requests[:2], [("/bin/sh", "-c", shell)] * 2)
            self.assertEqual(
                completed.stdout,
                (str(jobs[0]["pid"]) + "\n" + str(jobs[1]["pid"]) + "\n").encode(),
            )
            self.assertEqual(semantics["domains"]["FIRST"]["value"], str(jobs[0]["pid"]))
            self.assertEqual(semantics["domains"]["SECOND"]["value"], str(jobs[1]["pid"]))
            self.assertEqual(observed["rendezvous"], {
                "issued": 3, "completed": 3, "pending_peak": 1, "publication": None,
            })
            self.assertEqual(len({row["pid"] for row in jobs}), 3)
            self.assertEqual(jobs[0]["admission"], jobs[1]["admission"])
            self.assertTrue(all(row["waited"] and row["returncode"] == 0 for row in jobs))
        self.assert_clean(session)


    def test_native_original_make_job_writes_only_its_admitted_output_once(self):
        from scripts.validation_ownership import read_epochs
        recipe = "printf final > result; printf once"
        self.add("Makefile", "all:\n\t@" + recipe + "\n")
        requests = []
        class Commands:
            def __getitem__(self, argv):
                requests.append(argv)
                return Command(argv, outputs=("result",))
        session = self.session()
        with session:
            completed, semantics, observed, generated = session._native_make_writable(
                "all", outputs=("result",), observe_reads=True,
                observe_runtime_completions=True, commands=Commands(),
            )
            self.assertEqual(completed.stdout, b"once")
            self.assertEqual(completed.stderr, b"")
            self.assertEqual(
                [(item.path, item.data, item.mode) for item in generated],
                [("result", b"final", 0o644)],
            )
            jobs = [
                parse_json(row.removeprefix("native-job:").encode(), "original writer job")
                for row in observed["accessed"] if row.startswith("native-job:")
            ]
            self.assertEqual(requests, [("/bin/sh", "-c", recipe)])
            self.assertEqual(len(jobs), 1)
            job, = jobs
            self.assertEqual(job["admission"]["outputs"], ["result"])
            self.assertTrue(job["waited"])
            self.assertEqual(job["returncode"], 0)
            effects = [
                parse_json(row.removeprefix("native-output:").encode(), "original writer effect")
                for row in observed["accessed"] if row.startswith("native-output:")
            ]
            self.assertTrue(effects)
            self.assertTrue(all(row["owner"] == job["sequence"] for row in effects))
            self.assertTrue(any(row["kind"] == "output-write" and row["pid"] == job["pid"] for row in effects))
            trace = observed["read_trace"]
            self.assertEqual(trace["version"], read_epochs.WRITABLE_VERSION)
            self.assertEqual(trace["output_authority"]["paths"], ["result"])
            self.assertEqual(set(trace["output_authority"]), {"paths", "jobs"})
            self.assertEqual(read_epochs.native_output_effects(trace), sorted(effects, key=lambda row: row["sequence"]))
            redundant = json.loads(json.dumps(trace))
            redundant["output_authority"]["effects"] = read_epochs.native_output_effects(redundant)
            self.assertGreater(len(encoded(redundant)), len(encoded(trace)))
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "output authority"):
                read_epochs.validate_trace(
                    redundant, redundant["scope"], count_limit=100000, file_limit=10000000,
                )
            read_epochs.validate_trace(
                trace, trace["scope"], count_limit=100000, file_limit=10000000,
            )
            for field, value in (("fd", 999), ("revision", 100), ("path", "/repo/other"), ("result", 0)):
                with self.subTest(archive_field=field):
                    invalid = json.loads(json.dumps(trace))
                    effect = next(row for row in read_epochs.native_output_effects(invalid) if row["kind"] == "output-write")
                    effect[field] = value
                    machine = next(
                        row for row in invalid["machine"]["events"]
                        if row["kind"] == "native-output" and row["event"]["sequence"] == effect["sequence"]
                    )
                    machine["event"] = effect
                    machine["sha256"] = hashlib.sha256(encoded(effect)).hexdigest()
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            immutable = json.loads(json.dumps(trace))
            immutable["version"] = read_epochs.RUNTIME_VERSION
            with self.assertRaises(read_epochs.ReadEpochError):
                read_epochs.validate_trace(
                    immutable, immutable["scope"], count_limit=100000, file_limit=10000000,
                )
        self.assert_clean(session)


    def test_native_original_make_output_plan_refuses_other_jobs_and_source_collisions(self):
        self.add("Makefile", "all:\n\t@printf final > result\n")
        for outputs, command_outputs, readonly, expected in (
            (("result",), ("result",), True, "requests output authority"),
            (("result",), (), True, "readonly native Make filesystem write denied"),
            (("result", "other"), ("other",), False, "issued output authority"),
            (("other",), ("result",), False, "escape its issued namespace"),
            (("Makefile",), ("Makefile",), False, "conflicts with immutable source"),
        ):
            with self.subTest(outputs=outputs, command_outputs=command_outputs, readonly=readonly):
                class Commands:
                    def __getitem__(self, argv):
                        return Command(argv, outputs=command_outputs)
                session = self.session()
                with session:
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        if readonly:
                            session._native_make_readonly(
                                "all", observe_reads=True, observe_runtime_completions=True,
                                commands=Commands(),
                            )
                        else:
                            session._native_make_writable(
                                "all", outputs=outputs, observe_reads=True,
                                observe_runtime_completions=True, commands=Commands(),
                            )
                self.assert_clean(session)


    def test_native_original_make_failed_open_and_duplicate_preserve_actual_output(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <unistd.h>\n#include <fcntl.h>\n#include <errno.h>\n"
            "#include <sys/syscall.h>\nint main(void){int fd;"
            "fd=open(\"/repo/result\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"final\",5)!=5)return 1;"
            "if(open(\"/repo/result\",O_CREAT|O_EXCL|O_WRONLY,0644)!=-1||errno!=EEXIST)return 2;"
            "if(syscall(SYS_dup3,fd,fd,0)!=-1||errno!=EINVAL)return 3;"
            "if(fcntl(fd,F_DUPFD,-1)!=-1||errno!=EINVAL)return 4;"
            "if(dup2(fd,-1)!=-1||errno!=EBADF)return 5;"
            "if(close(fd)||write(1,\"once\",4)!=4)return 6;return 0;}\n"
        ))
        self.add("Makefile", "all:\n\t@/native/tool\n")
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool, outputs=("result",))
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("result",), commands=Commands(), native_tool=tool,
                observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"once", b""))
            self.assertEqual([(row.data, row.mode) for row in generated], [(b"final", 0o644)])
            trace = observed["read_trace"]
            failures = [
                row for row in read_epochs.native_output_effects(trace)
                if row["kind"] == "output-operation-failed"
            ]
            self.assertEqual([(row["operation"], row["result"]) for row in failures], [
                ("open", -errno.EEXIST), ("dup", -errno.EINVAL),
                ("dup", -errno.EINVAL), ("dup", -errno.EBADF),
            ])
            for operation, field, value in (
                ("open", "flags", os.O_APPEND), ("open", "source", "/repo/other"),
                ("dup", "descriptor", 999), ("dup", "duplicate_kind", []),
                ("dup", "target", True), ("dup", "flags", os.O_APPEND),
            ):
                with self.subTest(operation=operation, field=field):
                    invalid = json.loads(json.dumps(trace))
                    event = next(
                        row for row in read_epochs.native_output_effects(invalid)
                        if row["kind"] == "output-operation-failed" and row["operation"] == operation
                    )
                    event[field] = value
                    machine = next(
                        row for row in invalid["machine"]["events"]
                        if row["kind"] == "native-output" and row["event"]["sequence"] == event["sequence"]
                    )
                    machine["sha256"] = hashlib.sha256(encoded(event)).hexdigest()
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
        self.assert_clean(session)


    def test_native_writable_make_admission_failure_exhausts_whole_session(self):
        self.add("Makefile", "all:\n\t@printf final > result\n")
        session = self.session()
        with session:
            with self.assertRaisesRegex(MakeProbeError, "exact output paths"):
                session._native_make_writable("all", outputs=())
            self.assertTrue(session.budget.failed)
            self.assertTrue(session.budget.closed)
            with self.assertRaisesRegex(MakeProbeError, "deadline/budget exhausted"):
                session.budget.remaining()
        self.assert_clean(session)


    def test_native_original_make_actual_forked_output_alias_and_parent_wire(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <unistd.h>\n#include <fcntl.h>\n#include <sys/wait.h>\n"
            "int main(void){int fd,status;pid_t child;"
            "fd=open(\"/repo/result\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"first\",5)!=5)return 1;child=fork();if(child<0)return 2;"
            "if(!child){if(dup2(fd,7)!=7||write(7,\"final\",5)!=5||close(7))_exit(3);_exit(0);}"
            "if(waitpid(child,&status,0)!=child||!WIFEXITED(status)||WEXITSTATUS(status))return 4;"
            "if(close(fd)||write(1,\"once\",4)!=4)return 5;return 0;}\n"
        ))
        self.add("Makefile", "all:\n\t@/native/tool\n")
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool, outputs=("result",))
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("result",), commands=Commands(), native_tool=tool,
                observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"once", b""))
            self.assertEqual([(row.data, row.mode) for row in generated], [(b"firstfinal", 0o644)])
            trace = observed["read_trace"]
            job, = trace["output_authority"]["jobs"]
            inherited, = [
                row for row in read_epochs.native_output_effects(trace) if row["kind"] == "output-inherit"
            ]
            self.assertEqual(inherited["parent"], job["pid"])
            self.assertNotEqual(inherited["pid"], job["pid"])
            self.assertIn(inherited["pid"], [row.get("child") for row in job["tree"]])
            self.assertTrue(any(
                row["kind"] == "output-write" and row["pid"] == inherited["pid"] and row["fd"] == 7
                for row in read_epochs.native_output_effects(trace)
            ))
            for parent in ([], True, 99999999):
                with self.subTest(parent=parent):
                    invalid = json.loads(json.dumps(trace))
                    effect = next(row for row in read_epochs.native_output_effects(invalid) if row["kind"] == "output-inherit")
                    effect["parent"] = parent
                    machine = next(
                        row for row in invalid["machine"]["events"]
                        if row["kind"] == "native-output" and row["event"]["sequence"] == effect["sequence"]
                    )
                    machine["sha256"] = hashlib.sha256(encoded(effect)).hexdigest()
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "malformed parent"):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
        self.assert_clean(session)


    def test_native_original_make_truncating_reopen_and_close_errno_wire_match_live_model(self):
        from scripts.validation_ownership import read_epochs
        recipe = "printf first > result; printf final > result; printf once"
        self.add("Makefile", "all:\n\t@" + recipe + "\n")
        class Commands:
            def __getitem__(self, argv):
                return Command(argv, outputs=("result",))
        session = self.session()
        with session:
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("result",), commands=Commands(), observe_reads=True,
                observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"once", b""))
            self.assertEqual([(row.data, row.mode) for row in generated], [(b"final", 0o644)])
            trace = observed["read_trace"]
            effects = read_epochs.native_output_effects(trace)
            truncated, = [row for row in effects if row["kind"] == "output-truncate"]
            self.assertEqual(truncated["identity"][3], 0)
            read_epochs.validate_trace(trace, trace["scope"], count_limit=100000, file_limit=10000000)
            active_writer = json.loads(json.dumps(trace))
            machine = active_writer["machine"]["events"]
            index = next(
                index for index, row in enumerate(machine)
                if row["kind"] == "native-output" and row["event"]["kind"] == "output-truncate"
            )
            previous = [
                row["event"] for row in machine[:index]
                if row["kind"] == "native-output" and row["event"]["kind"] == "output-write"
            ][-1]
            opened = dict(next(row for row in effects if row["kind"] == "output-open"))
            opened.update(
                fd=999, description=max(row.get("description", 0) for row in effects) + 1,
                identity=previous["identity"], revision=previous["revision"],
            )
            extra = dict(machine[index])
            extra["event"] = opened
            machine.insert(index, extra)
            closed = {key: truncated[key] for key in (
                "sequence", "owner", "serial", "revision", "path", "pid",
            )}
            closed.update(kind="output-close", fd=999, description=opened["description"])
            extra = dict(machine[index + 2])
            extra["event"] = closed
            machine.insert(index + 3, extra)
            for number, row in enumerate(read_epochs.native_output_effects(active_writer), 1):
                row["sequence"] = number
            for number, row in enumerate(machine, 1):
                row["seq"] = number
                if row["kind"] == "native-output":
                    row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "truncating open"):
                read_epochs.validate_trace(
                    active_writer, active_writer["scope"], count_limit=100000, file_limit=10000000,
                )
            for field, value in (("revision", 100), ("fd", 999), ("identity", truncated["identity"][:3] + [1] + truncated["identity"][4:])):
                with self.subTest(truncate_field=field):
                    invalid = json.loads(json.dumps(trace))
                    effect = next(row for row in read_epochs.native_output_effects(invalid) if row["kind"] == "output-truncate")
                    effect[field] = value
                    machine = next(
                        row for row in invalid["machine"]["events"]
                        if row["kind"] == "native-output" and row["event"]["sequence"] == effect["sequence"]
                    )
                    machine["event"] = effect
                    machine["sha256"] = hashlib.sha256(encoded(effect)).hexdigest()
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "truncating open"):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            for result in (-errno.EINTR, -errno.EIO, -errno.ENOSPC, -errno.EDQUOT):
                with self.subTest(released_close_errno=result):
                    compatible = json.loads(json.dumps(trace))
                    effect = next(row for row in read_epochs.native_output_effects(compatible) if row["kind"] == "output-close")
                    del effect["description"]
                    effect.update(kind="output-close-failed", result=result)
                    machine = next(
                        row for row in compatible["machine"]["events"]
                        if row["kind"] == "native-output" and row["event"]["sequence"] == effect["sequence"]
                    )
                    machine["event"] = effect
                    machine["sha256"] = hashlib.sha256(encoded(effect)).hexdigest()
                    read_epochs.validate_trace(
                        compatible, compatible["scope"], count_limit=100000, file_limit=10000000,
                    )
            for result in (-errno.EBADF, -errno.EINVAL):
                with self.subTest(close_errno=result):
                    invalid = json.loads(json.dumps(trace))
                    original = next(
                        row for row in read_epochs.native_output_effects(invalid) if row["kind"] == "output-close"
                    )
                    failure = {key: value for key, value in original.items() if key != "description"}
                    failure.update(kind="output-close-failed", result=result)
                    machine_index = next(
                        index for index, row in enumerate(invalid["machine"]["events"])
                        if row["kind"] == "native-output" and row["event"]["sequence"] == original["sequence"]
                    )
                    mirror = dict(invalid["machine"]["events"][machine_index])
                    mirror["event"] = failure
                    invalid["machine"]["events"].insert(machine_index, mirror)
                    for number, row in enumerate(read_epochs.native_output_effects(invalid), 1):
                        row["sequence"] = number
                    for number, row in enumerate(invalid["machine"]["events"], 1):
                        row["seq"] = number
                        if row["kind"] == "native-output":
                            row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "released-FD errno"):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
        self.assert_clean(session)


    def test_native_original_make_distinct_jobs_bind_their_actual_output_owners(self):
        from scripts.validation_ownership import read_epochs
        recipes = (
            "printf '%s' \"$$\" > first; printf first",
            "printf '%s' \"$$\" > second; printf second",
        )
        self.add("Makefile", "all:\n" + "".join("\t@" + row.replace("$", "$$") + "\n" for row in recipes))
        requests = []
        class Commands:
            def __getitem__(self, argv):
                requests.append(argv)
                index = recipes.index(argv[-1])
                return Command(argv, outputs=(("first", "second")[index],))
        session = self.session()
        with session:
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("first", "second"), observe_reads=True,
                observe_runtime_completions=True, commands=Commands(),
            )
            jobs = observed["read_trace"]["output_authority"]["jobs"]
            effects = read_epochs.native_output_effects(observed["read_trace"])
            self.assertEqual(completed.stdout, b"firstsecond")
            self.assertEqual(completed.stderr, b"")
            self.assertEqual(requests, [("/bin/sh", "-c", row) for row in recipes])
            self.assertEqual(len(jobs), 2)
            self.assertNotEqual(jobs[0]["pid"], jobs[1]["pid"])
            self.assertNotEqual(jobs[0]["admission"]["owner"], jobs[1]["admission"]["owner"])
            self.assertEqual(
                [(row.path, row.data, row.mode) for row in generated],
                [(name, str(job["pid"]).encode(), 0o644) for name, job in zip(("first", "second"), jobs)],
            )
            for name, job in zip(("first", "second"), jobs):
                rows = [row for row in effects if row["path"] == "/repo/" + name]
                self.assertTrue(rows)
                self.assertEqual({row["owner"] for row in rows}, {job["sequence"]})
            invalid = json.loads(json.dumps(observed["read_trace"]))
            opens = [row for row in read_epochs.native_output_effects(invalid) if row["kind"] == "output-open"]
            first_description, second_description = [row["description"] for row in opens]
            for effect in read_epochs.native_output_effects(invalid):
                if effect.get("description") == second_description:
                    effect["description"] = first_description
            for machine in invalid["machine"]["events"]:
                if machine["kind"] == "native-output":
                    effect = machine["event"]
                    machine["event"] = effect
                    machine["sha256"] = hashlib.sha256(encoded(effect)).hexdigest()
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "reused a live descriptor"):
                read_epochs.validate_trace(
                    invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                )
        self.assert_clean(session)


    def test_native_original_make_first_wire_refuses_unimplemented_namespace_mutations(self):
        self.add("native.c", (
            "#define _POSIX_C_SOURCE 200809L\n#include <unistd.h>\n#include <sys/stat.h>\n"
            "#include <string.h>\nint main(int argc,char **argv){if(argc!=2)return 2;"
            "if(!strcmp(argv[1],\"mkdir\"))return mkdir(\"/repo/result\",0700);"
            "return symlink(\"/dev/null\",\"/repo/result\");}\n"
        ))
        for command, expected in (
            ("symlink", "candidate symlink creation is forbidden"),
            ("mkdir", "issued output authority"),
        ):
            with self.subTest(command=command):
                self.add("Makefile", "all:\n\t@/native/tool " + command + "\n")
                session = self.session()
                with session:
                    tool = session.compile_native(("native.c",))
                    class Commands:
                        def __getitem__(self, argv):
                            return Command(argv, native_tool=tool, outputs=("result",))
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session._native_make_writable(
                            "all", outputs=("result",), native_tool=tool,
                            commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                        )
                self.assert_clean(session)


    def test_native_original_make_separate_open_lineage_and_successful_exec_reconcile_cloexec(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <unistd.h>\n#include <string.h>\n"
            "#include <errno.h>\n#include <sys/syscall.h>\n"
            "int main(int argc,char **argv){int fd,other,copy;char *next[]={\"/native/tool\",\"next\",0};"
            "if(argc!=2)return 1;if(!strcmp(argv[1],\"next\")){fd=open(\"/dev/null\",O_RDONLY);"
            "if(fd!=3||write(1,\"once\",4)!=4||close(fd))return 2;return 0;}"
            "fd=open(\"/repo/result\",O_CREAT|O_EXCL|O_WRONLY|O_CLOEXEC,0644);"
            "if(fd!=3)return 3;other=open(\"/repo/result\",O_RDONLY|O_CLOEXEC);"
            "if(other!=4)return 4;copy=dup(fd);if(copy!=5||write(copy,\"final\",5)!=5)return 5;"
            "if(!strcmp(argv[1],\"close\")&&fcntl(copy,F_SETFD,FD_CLOEXEC))return 6;"
            "if(syscall(SYS_execve,next[0],next,(char **)1)!=-1||errno!=EFAULT"
            "||fcntl(fd,F_GETFD)!=FD_CLOEXEC||fcntl(other,F_GETFD)!=FD_CLOEXEC)return 8;"
            "execv(next[0],next);return 7;}\n"
        ))
        for mode in ("close", "retain"):
            with self.subTest(mode=mode):
                self.add("Makefile", "all:\n\t@/native/tool " + mode + "\n")
                session = self.session()
                with session:
                    tool = session.compile_native(("native.c",))
                    class Commands:
                        def __getitem__(self, argv):
                            return Command(argv, native_tool=tool, outputs=("result",))
                    completed, _, observed, generated = session._native_make_writable(
                        "all", outputs=("result",), native_tool=tool, commands=Commands(),
                        observe_reads=True, observe_runtime_completions=True,
                    )
                    self.assertEqual(completed.stdout, b"once")
                    self.assertEqual(completed.stderr, b"")
                    self.assertEqual([(row.data, row.mode) for row in generated], [(b"final", 0o644)])
                    trace = observed["read_trace"]
                    effects = read_epochs.native_output_effects(trace)
                    opens = [row for row in effects if row["kind"] == "output-open"]
                    self.assertEqual(len(opens), 2)
                    self.assertEqual(opens[0]["serial"], opens[1]["serial"])
                    self.assertNotEqual(opens[0]["description"], opens[1]["description"])
                    closures = [row for row in effects if row["kind"] == "output-exec-close"]
                    self.assertEqual({row["fd"] for row in closures}, {3, 4, 5} if mode == "close" else {3, 4})
                    self.assertEqual({row["generation"] for row in closures}, {2})
                    job, = trace["output_authority"]["jobs"]
                    self.assertEqual([row["generation"] for row in job["tree"] if row["kind"] == "exec"], [1, 2])
                    self.assertEqual(any(row["kind"] == "output-close" and row["fd"] == 5 for row in effects), mode == "retain")
                    failures = [row for row in effects if row["kind"] == "output-operation-failed"]
                    self.assertEqual([(row["operation"], row["result"]) for row in failures], [("exec", -errno.EFAULT)])
                    premature = json.loads(json.dumps(trace))
                    machine_events = premature["machine"]["events"]
                    failure_index = next(
                        index for index, row in enumerate(machine_events)
                        if row["kind"] == "native-output"
                        and row["event"]["kind"] == "output-operation-failed"
                    )
                    failure = machine_events.pop(failure_index)
                    first_output = next(
                        index for index, row in enumerate(machine_events) if row["kind"] == "native-output"
                    )
                    machine_events.insert(first_output, failure)
                    for number, row in enumerate(read_epochs.native_output_effects(premature), 1):
                        row["sequence"] = number
                    for number, row in enumerate(machine_events, 1):
                        row["seq"] = number
                        if row["kind"] == "native-output":
                            row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "live source binding"):
                        read_epochs.validate_trace(
                            premature, premature["scope"], count_limit=100000, file_limit=10000000,
                        )
                    for generation in (1, 3, True):
                        with self.subTest(exec_generation=generation):
                            invalid = json.loads(json.dumps(trace))
                            effect = next(
                                row for row in read_epochs.native_output_effects(invalid)
                                if row["kind"] == "output-exec-close"
                            )
                            effect["generation"] = generation
                            machine = next(
                                row for row in invalid["machine"]["events"]
                                if row["kind"] == "native-output"
                                and row["event"]["sequence"] == effect["sequence"]
                            )
                            machine["event"] = effect
                            machine["sha256"] = hashlib.sha256(encoded(effect)).hexdigest()
                            with self.assertRaisesRegex(read_epochs.ReadEpochError, "actual image generation"):
                                read_epochs.validate_trace(
                                    invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                                )
                    invalid = json.loads(json.dumps(trace))
                    old, new = [row["description"] for row in opens]
                    for effect in read_epochs.native_output_effects(invalid):
                        if effect.get("description") == new:
                            effect["description"] = old
                    for machine in invalid["machine"]["events"]:
                        if machine["kind"] == "native-output":
                            effect = machine["event"]
                            machine["event"] = effect
                            machine["sha256"] = hashlib.sha256(encoded(effect)).hexdigest()
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "reused a live descriptor"):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
                self.assert_clean(session)


    def test_native_command_owner_binds_different_sealed_tools_and_captured_host_images(self):
        self.add("first.c", '#include <stdio.h>\nint main(void) { puts("first"); return 0; }\n')
        self.add("second.c", '#include <stdio.h>\nint main(void) { puts("second"); return 0; }\n')
        self.add("Makefile", "all: ; @/native/tool\n")
        session = self.session()
        with session:
            first = session.compile_native(("first.c",))
            second = session.compile_native(("second.c",))
            self.assertNotEqual(first.digest, second.digest)
            owners = []
            snapshot = session.snapshot
            argv = ("/native/tool",)
            for tool, expected in ((first, b"first\n"), (second, b"second\n"), (first, b"first\n")):
                completed, _, observed = session._native_make_readonly(
                    "all", observe_reads=True, observe_runtime_completions=True,
                    native_tool=tool, commands={argv: Command(argv, native_tool=tool)},
                )
                self.assertEqual(completed.stdout, expected)
                job, = [
                    parse_json(value.removeprefix("native-job:").encode(), "sealed executable owner")
                    for value in observed["accessed"] if value.startswith("native-job:")
                ]
                owners.append(job["admission"]["owner"])
                self.assertIs(session.snapshot, snapshot)
            self.assertNotEqual(owners[0], owners[1])
            self.assertEqual(owners[0], owners[2])
        self.assert_clean(session)
        self.add("Makefile", "all: ; @v=host; printf '%s' \"$$v\"\n")
        session = self.session()
        with session:
            captured = session._captured_native_runtime
            owners = []
            argv = ("/bin/sh", "-c", "v=host; printf '%s' \"$v\"")
            for suffix in (b"", b"distinct-captured-image", b""):
                def image(path):
                    return tuple(
                        (name, data + suffix if name == "/usr/bin/sh" else data)
                        for name, data in captured(path)
                    )
                with patch.object(session, "_captured_native_runtime", image):
                    completed, _, observed = session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True,
                        commands={argv: Command(argv)},
                    )
                self.assertEqual(completed.stdout, b"host")
                job, = [
                    parse_json(value.removeprefix("native-job:").encode(), "captured executable owner")
                    for value in observed["accessed"] if value.startswith("native-job:")
                ]
                owners.append(job["admission"]["owner"])
            self.assertNotEqual(owners[0], owners[1])
            self.assertEqual(owners[0], owners[2])
        self.assert_clean(session)


    def test_native_command_admission_rejects_missing_substituted_and_writable_commands(self):
        argv = ("/bin/sh", "-c", "v=original; printf '%s' \"$v\"")
        self.add("Makefile", "all: ; @v=original; printf '%s' \"$$v\"\n")
        for commands, error in (
            ({}, "original native argv lacks its sealed Command"),
            ({argv: False}, "native readonly Command differs"),
            ({argv: Command(("/bin/sh", "-c", "printf replaced"))}, "native readonly Command differs"),
            ({argv: Command(argv, outputs=("output",))}, "native readonly Command differs"),
            ({argv: Command(argv, dependency_only=True)}, "native readonly Command differs"),
            ({argv: Command(argv, sources=("missing",))}, "source"),
            ({argv: Command(argv, code=("missing",))}, "immutable source view"),
            ({argv: Command(argv, directories=("missing",))}, "directory declaration is absent"),
            ({argv: Command(argv, directories=("Makefile",))}, "not an active directory"),
            ({argv: Command(argv, directories=("../outside",))}, "path"),
        ):
            session = self.session()
            with self.subTest(error=error, commands=commands), session:
                with self.assertRaisesRegex(MakeProbeError, error):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, commands=commands,
                    )
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)
        session = self.session()
        with session:
            completed, _, observed = session._native_make_readonly(
                "all", observe_reads=True, observe_runtime_completions=True,
                commands={argv: Command(argv, sources=("Makefile",))},
            )
            self.assertEqual(completed.stdout, b"original")
            self.assertEqual(observed["rendezvous"]["issued"], 1)
            self.assertEqual(observed["rendezvous"]["completed"], 1)
        self.assert_clean(session)


    def test_native_command_admission_preserves_root_directories_and_stock_shell_alias(self):
        self.add("sub/input", "source\n")
        self.add("Makefile", "all: ; @v=original; printf '%s' \"$$v\"\n")
        argv = ("/bin/sh", "-c", "v=original; printf '%s' \"$v\"")
        for resources in ((), ("/bin/printf",)):
            session = self.session(runtime_files=resources)
            with self.subTest(runtime_files=resources), session:
                owners = []
                for directories in ((), (".", "sub"), ("sub", ".", "sub")):
                    completed, _, observed = session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True,
                        commands={argv: Command(argv, directories=directories)},
                    )
                    self.assertEqual(completed.stdout, b"original")
                    job, = [
                        parse_json(row.removeprefix("native-job:").encode(), "root/alias admission")
                        for row in observed["accessed"] if row.startswith("native-job:")
                    ]
                    self.assertEqual(job["argv"], list(argv))
                    self.assertEqual(job["executable"], "/usr/bin/sh" if resources else "/bin/sh")
                    self.assertTrue(job["waited"])
                    self.assertEqual(job["returncode"], 0)
                    self.assertEqual(observed["rendezvous"]["issued"], 1)
                    self.assertEqual(observed["rendezvous"]["completed"], 1)
                    owners.append(job["admission"]["owner"])
                self.assertNotEqual(owners[0], owners[1])
                self.assertEqual(owners[1], owners[2])
            self.assert_clean(session)


    def test_native_command_owner_binds_descendant_tools_and_shared_library_closure(self):
        self.add("first.c", '#include <stdio.h>\nint main(void) { puts("first"); return 0; }\n')
        self.add("second.c", '#include <stdio.h>\nint main(void) { puts("second"); return 0; }\n')
        self.add("Makefile", "all: ; @/native/tool; :\n")
        argv = ("/bin/sh", "-c", "/native/tool; :")
        session = self.session()
        with session:
            first = session.compile_native(("first.c",))
            second = session.compile_native(("second.c",))
            owners = []
            for tool, expected in ((first, b"first\n"), (second, b"second\n"), (first, b"first\n")):
                completed, _, observed = session._native_make_readonly(
                    "all", observe_reads=True, observe_runtime_completions=True,
                    native_tool=tool, commands={argv: Command(argv)},
                )
                self.assertEqual(completed.stdout, expected)
                job, = [
                    parse_json(value.removeprefix("native-job:").encode(), "descendant code closure")
                    for value in observed["accessed"] if value.startswith("native-job:")
                ]
                self.assertEqual(job["argv"], list(argv))
                self.assertTrue(any(
                    event["kind"] == "exec" and event["path"] == "/native/tool"
                    and event["pid"] != job["pid"] for event in job["tree"]
                ))
                owners.append(job["admission"]["owner"])
            with self.subTest(member="descendant"):
                self.assertNotEqual(owners[0], owners[1])
                self.assertEqual(owners[0], owners[2])
        self.assert_clean(session)
        self.add("Makefile", "all: ; @v=library; printf '%s' \"$$v\"\n")
        argv = ("/bin/sh", "-c", "v=library; printf '%s' \"$v\"")
        session = self.session()
        with session:
            captured = session._captured_native_runtime
            images = captured("/usr/bin/sh")
            library, = [path for path, _ in images if path.endswith("/libc.so.6")]
            original_make = session.make_runtime
            owners = []
            for suffix in (b"", b"distinct-shared-library-image", b""):
                def changed(rows):
                    return tuple((path, data + suffix if path == library else data) for path, data in rows)
                session.make_runtime = changed(original_make)
                with patch.object(session, "_captured_native_runtime", lambda path: changed(captured(path))):
                    completed, _, observed = session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True,
                        commands={argv: Command(argv)},
                    )
                self.assertEqual(completed.stdout, b"library")
                job, = [
                    parse_json(value.removeprefix("native-job:").encode(), "shared code closure")
                    for value in observed["accessed"] if value.startswith("native-job:")
                ]
                owners.append(job["admission"]["owner"])
            session.make_runtime = original_make
            with self.subTest(member="shared-library"):
                self.assertNotEqual(owners[0], owners[1])
                self.assertEqual(owners[0], owners[2])
        self.assert_clean(session)


    def test_native_command_admission_resolver_view_deadline_and_quota_failures_cleanup(self):
        import copy
        argv = ("/bin/sh", "-c", "v=original; printf '%s' \"$v\"")
        self.add("Makefile", "all: ; @v=original; printf '%s' \"$$v\"\n")
        for fault, error in (
            ("view", "resolver changed its immutable view"),
            ("deadline", "deadline"),
            ("quota", "cache"),
            ("cancel", "canceled original admission"),
        ):
            session = self.session()
            class Commands:
                def __getitem__(self, key):
                    self_test.assertEqual(key, argv)
                    if fault == "view":
                        session.snapshot = copy.copy(session.snapshot)
                    elif fault == "deadline":
                        session.budget.started -= session.budget.limits.seconds
                    elif fault == "quota":
                        session.budget.charge("cache", session.budget.limits.cache_bytes + 1)
                    else:
                        raise MakeProbeError("canceled original admission")
                    return Command(argv)
            self_test = self
            with self.subTest(fault=fault), session:
                with self.assertRaisesRegex(MakeProbeError, error):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, commands=Commands(),
                    )
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)


    def test_native_command_admission_rejects_live_and_returned_binding_mutations(self):
        argv = ("/bin/sh", "-c", "v=original; printf '%s' \"$v\"")
        self.add("Makefile", "all: ; @v=original; printf '%s' \"$$v\"\n")
        cases = (
            (
                "original=guard.Policy.begin_native_job\n"
                "def changed(self,pid,state,path):\n"
                " state.native_admission=None\n"
                " return original(self,pid,state,path)\n"
                "guard.Policy.begin_native_job=changed\n",
                "native job lacks its actual original Make dispatch",
            ),
            (
                "original=guard.Policy.entry\n"
                "def changed(self,pid,state,r):\n"
                " if r.orig_rax==59 and state.dispatch is not None and self.native_admission:\n"
                "  state.native_admission={**state.native_admission,'owner':'0'*64}\n"
                " return original(self,pid,state,r)\n"
                "guard.Policy.entry=changed\n",
                "native exec entry differs from its issued Command admission",
            ),
            (
                "original=guard.Policy.observe\n"
                "def changed(self,name,value):\n"
                " if name=='accessed' and value.startswith('native-job:'):\n"
                "  row=json.loads(value[len('native-job:'):])\n"
                "  row['admission']['owner']='0'*64\n"
                "  value='native-job:'+guard.encoded(row).decode('ascii')\n"
                " return original(self,name,value)\n"
                "guard.Policy.observe=changed\n",
                "native job differs from its issued Command admission",
            ),
            (
                "original=guard.parse_json\n"
                "def changed(raw,label):\n"
                " row=original(raw,label)\n"
                " if label=='native Command admission reply':row['sequence']+=1\n"
                " return row\n"
                "guard.parse_json=changed\n",
                "native Command reply is foreign, stale",
            ),
            (
                "original=guard.parse_json\n"
                "def changed(raw,label):\n"
                " row=original(raw,label)\n"
                " if label=='native Command admission reply':row['input_sha256']='0'*64\n"
                " return row\n"
                "guard.parse_json=changed\n",
                "native Command reply is foreign, stale",
            ),
            (
                "original=guard.ProducerChannel.finish\n"
                "def changed(self,raw):\n"
                " row=guard.parse_json(raw,'test terminal');row['completed']-=1\n"
                " return original(self,guard.encoded(row))\n"
                "guard.ProducerChannel.finish=changed\n",
                "partial or inconsistent live producer completion",
            ),
        )
        for body, error in cases:
            session = self.session()
            with self.subTest(error=error), self.native_supervisor(body), session:
                with self.assertRaisesRegex(MakeProbeError, error):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True,
                        commands={argv: Command(argv)},
                    )
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)


    def test_native_original_prespawn_inputs_bind_entry_and_exec_stop(self):
        self.add("Makefile", "all: ; @v=original; printf '%s' \"$$v\"\n")
        cases = (
            (
                "original=guard.Policy.entry\n"
                "def changed(self,pid,state,r):\n"
                " if r.orig_rax==59 and state.dispatch is not None and self.native_readonly:\n"
                "  pointer=int.from_bytes(guard.memory(pid,r.rsi+16,8),'little')\n"
                "  text=guard.cstring(pid,pointer,limit=65536)\n"
                "  offset=text.index('original')\n"
                "  guard.replace_memory(pid,pointer+offset,b'foreign!')\n"
                " return original(self,pid,state,r)\n"
                "guard.Policy.entry=changed\n",
                "native exec entry differs from original pre-spawn dispatch inputs",
            ),
            (
                "guard.Policy.observe_native_inputs=lambda *args:None\n",
                "native job lacks its actual original Make dispatch",
            ),
            (
                "original=guard.Policy.observe_native_inputs\n"
                "def changed(self,pid,state,pointer,size):\n"
                " original(self,pid,state,pointer,size)\n"
                " path,inputs=state.native_inputs\n"
                " state.native_inputs=(path,{**inputs,'argv':['/bin/sh','-c','printf foreign']})\n"
                "guard.Policy.observe_native_inputs=changed\n",
                "native exec entry differs from original pre-spawn dispatch inputs",
            ),
            (
                "original=guard.Policy.observe_native_inputs\n"
                "def changed(self,pid,state,pointer,size):\n"
                " original(self,pid,state,pointer,size)\n"
                " path,inputs=state.native_inputs\n"
                " state.native_inputs=(path,{**inputs,'cwd':'/foreign'})\n"
                "guard.Policy.observe_native_inputs=changed\n",
                "native exec entry differs from original pre-spawn dispatch inputs",
            ),
            (
                "original=guard.Policy.observe_native_inputs\n"
                "def changed(self,pid,state,pointer,size):\n"
                " original(self,pid,state,pointer,size)\n"
                " return original(self,pid,state,pointer,size)\n"
                "guard.Policy.observe_native_inputs=changed\n",
                "native dispatch inputs lack their original pre-spawn owner",
            ),
            (
                "original=guard.Policy.observe_native_inputs\n"
                "def changed(self,pid,state,pointer,size):return original(self,pid+1,state,pointer,size)\n"
                "guard.Policy.observe_native_inputs=changed\n",
                "native dispatch inputs lack their original pre-spawn owner",
            ),
            (
                "original=guard.Policy.observe_native_inputs\n"
                "def changed(self,pid,state,pointer,size):return original(self,pid,state,pointer,size-8)\n"
                "guard.Policy.observe_native_inputs=changed\n",
                "native dispatch inputs lack their original pre-spawn owner",
            ),
            (
                "original=guard.Policy.bind_native_job\n"
                "def changed(self,pid,state):\n"
                " path,inputs=state.native_inputs\n"
                " state.native_inputs=(path,{**inputs,'argv':['/bin/sh','-c','printf foreign']})\n"
                " return original(self,pid,state)\n"
                "guard.Policy.bind_native_job=changed\n",
                "native exec-stop inputs differ from original pre-spawn dispatch",
            ),
        )
        for body, error in cases:
            session = self.session()
            with self.subTest(error=error), self.native_supervisor(body), session:
                with self.assertRaisesRegex(MakeProbeError, error):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True,
                    )
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)
        session = self.session()
        with session:
            completed, _, observed = session._native_make_readonly(
                "all", observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual(completed.stdout, b"original")
            jobs = [
                parse_json(row.removeprefix("native-job:").encode(), "original native input")
                for row in observed["accessed"] if row.startswith("native-job:")
            ]
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["argv"], ["/bin/sh", "-c", "v=original; printf '%s' \"$v\""])
            self.assertEqual(jobs[0]["cwd"], "/repo")
        self.assert_clean(session)

