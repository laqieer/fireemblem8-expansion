"""Original native job admission and regular-file writer cases."""

import errno
import hashlib
import json
import os
import unittest
from unittest.mock import patch

from scripts.validation_ownership.authority import encoded, native_command_owner, parse_json
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

    def _native_image_operand_fixture(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <unistd.h>\n"
            "#include <sys/stat.h>\n#include <sys/file.h>\n#include <sys/wait.h>\n"
            "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n"
            "static int helper(const char *mode,int fd,int lock){int other,status;pid_t child;char temp[128],number[32],locknumber[32];"
            "if(write(fd,\"inherited\",9)!=9||fchmod(fd,0644)||flock(lock,LOCK_EX)||flock(lock,LOCK_UN))return 10;"
            "if(!strcmp(mode,\"grandchild\")){"
            "snprintf(number,sizeof(number),\"%d\",fd);snprintf(locknumber,sizeof(locknumber),\"%d\",lock);"
            "child=fork();if(child<0)return 13;if(!child){"
            "execl(\"/native/tool\",\"/native/tool\",\"inherited\",number,locknumber,(char*)0);return 14;}"
            "if(waitpid(child,&status,0)!=child||status)return 15;return close(fd)||close(lock);}"
            "if(!strcmp(mode,\"inherited\"))return close(fd)||close(lock);"
            "if(!strcmp(mode,\"foreign-mkdir\"))return mkdir(\"/repo/stage/new\",0700);"
            "if(!strcmp(mode,\"foreign-rmdir\"))return rmdir(\"/repo/stage/empty\");"
            "if(!strcmp(mode,\"foreign-remove\"))return unlink(\"/repo/stage/second\");"
            "if(!strcmp(mode,\"foreign-replace\"))return rename(\"/repo/stage/second\",\"/repo/stage/tmp\");"
            "if(!strcmp(mode,\"foreign-mode\")){other=open(\"/repo/stage/second\",O_RDONLY);"
            "return other<0||fchmod(other,0600);}"
            "if(!strcmp(mode,\"foreign-lock\"))other=open(\"/repo/stage/lock\",O_RDWR|O_NOFOLLOW);"
            "else if(!strcmp(mode,\"foreign-atomic\"))other=open(\"/repo/stage/.asset-manifest-write-abcdefgh\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "else if(!strcmp(mode,\"foreign-pid\")){"
            "snprintf(temp,sizeof(temp),\"/repo/stage/second.%ld.tmp\",(long)getppid());"
            "other=open(temp,O_CREAT|O_EXCL|O_WRONLY,0644);}"
            "else if(!strcmp(mode,\"foreign-temp\")||!strcmp(mode,\"own-temp\"))"
            "other=open(\"/repo/stage/tmp\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "else other=open(\"/repo/stage/second\",O_WRONLY|O_TRUNC);"
            "if(other<0||write(other,\"own\",3)!=3||close(other))return 11;"
            "if(!strcmp(mode,\"own-temp\")&&rename(\"/repo/stage/tmp\",\"/repo/stage/second\"))return 12;"
            "return close(fd)||close(lock);}"
            "int main(int argc,char **argv){int fd,other,lock,status;pid_t child;"
            "char number[32],locknumber[32];const char *mode=getenv(\"MODE\");"
            "if(argc==4)return helper(argv[1],atoi(argv[2]),atoi(argv[3]));"
            "if(mkdir(\"/repo/stage\",0700)||mkdir(\"/repo/stage/empty\",0700))return 1;"
            "fd=open(\"/repo/stage/first\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "other=open(\"/repo/stage/second\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "lock=open(\"/repo/stage/lock\",O_CREAT|O_RDWR|O_NOFOLLOW,0600);"
            "if(fd<0||other<0||lock<0||close(other))return 2;"
            "snprintf(number,sizeof(number),\"%d\",fd);snprintf(locknumber,sizeof(locknumber),\"%d\",lock);"
            "child=fork();if(child<0)return 3;if(!child){"
            "if(!strcmp(mode,\"fork-foreign\"))_exit(helper(\"foreign-open\",fd,lock));"
            "if(!strcmp(mode,\"fork-inherited\"))_exit(helper(\"inherited\",fd,lock));"
            "execl(\"/native/tool\",\"/native/tool\",mode,number,locknumber,(char*)0);return 4;}"
            "if(waitpid(child,&status,0)!=child||status||close(fd)||close(lock))return 5;return 0;}\n"
        ))
        outputs = ("stage/first", "stage/second")
        resources = (
            ("directory", "stage"), ("directory", "stage/empty"), ("directory", "stage/new"),
            ("shared-lock", "stage/lock"), ("temporary", "stage/tmp"),
            ("pid-temporary", "stage/second"), ("atomic-temporary", "stage/.asset-manifest-write-"),
        )
        return outputs, resources

    def test_native_image_operands_preserve_inherited_descriptions_without_parent_path_authority(self):
        from scripts.validation_ownership import read_epochs
        outputs, resources = self._native_image_operand_fixture()
        for mode in (
            "inherited", "own", "own-temp", "grandchild", "fork-inherited",
            "foreign-open", "foreign-mkdir", "foreign-rmdir",
            "foreign-remove", "foreign-replace", "foreign-mode", "foreign-lock",
            "foreign-atomic", "foreign-pid", "foreign-temp", "fork-foreign", "missing",
        ):
            self.add("Makefile", "all:\n\t@MODE=" + mode + " /native/tool\n\t@v=done; printf '%s' \"$$v\"\n")
            session = self.session()
            with self.subTest(mode=mode), session:
                tool = session.compile_native(("native.c",))
                requests = []
                class Commands:
                    def __getitem__(self, argv):
                        requests.append(argv)
                        helper = len(argv) == 4 and argv[0] == "/native/tool"
                        final = len(argv) == 3 and argv[:2] == ("/bin/sh", "-c") and argv[2].startswith("v=done;")
                        if helper and argv[1] == "missing":
                            raise KeyError(argv)
                        return Command(
                            argv, native_tool=tool,
                            outputs=() if final else (
                                (("stage/second",) if argv[1] in {"own", "own-temp"} else ()) if helper else outputs
                            ),
                            native_resources=(("temporary", "stage/tmp"),) if helper and argv[1] == "own-temp" else (
                                () if helper or final else resources
                            ),
                        )
                def run():
                    return session._native_make_writable(
                        "all", outputs=outputs, native_resources=resources, native_tool=tool,
                        commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                    )
                if mode not in {"inherited", "own", "own-temp", "grandchild", "fork-inherited"}:
                    expected = "original native argv lacks its sealed Command" if mode == "missing" else "issued output authority"
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        run()
                    self.assertTrue(session.budget.failed)
                else:
                    completed, _, observed, generated = run()
                    self.assertEqual((completed.stdout, completed.stderr), (b"done", b""))
                    self.assertEqual({row.path: row.data for row in generated}, {
                        "stage/first": b"inheritedinherited" if mode == "grandchild" else b"inherited",
                        "stage/second": b"own" if mode in {"own", "own-temp"} else b"",
                    })
                    self.assertEqual({row.path: row.mode for row in generated}, {
                        "stage/first": 0o644, "stage/second": 0o644,
                    })
                    trace = observed["read_trace"]
                    images = [
                        row["event"] for row in trace["machine"]["events"]
                        if row["kind"] == "native-tree" and row["event"]["kind"] == "exec"
                    ]
                    self.assertEqual([tuple(row["argv"]) for row in images], requests)
                    self.assertEqual([row["admission"]["sequence"] for row in images], list(range(1, len(images) + 1)))
                    self.assertEqual(observed["rendezvous"]["issued"], len(images))
                    self.assertEqual([job["sequence"] for job in trace["output_authority"]["jobs"]], [1, 2])
                    self.assertEqual(
                        [job["admission"]["sequence"] for job in trace["output_authority"]["jobs"]],
                        [1, len(images)],
                    )
                    helper_images = [row for row in images if len(row["argv"]) == 4]
                    self.assertEqual(len(helper_images), 0 if mode == "fork-inherited" else 2 if mode == "grandchild" else 1)
                    read_epochs.validate_trace(trace, trace["scope"], count_limit=100000, file_limit=10000000)
                    for field, value in (
                        ("owner", "0" * 64), ("sequence", 1), ("input_sha256", "0" * 64),
                        ("outputs", list(outputs)), ("resources", [list(row) for row in resources]),
                    ):
                        invalid = json.loads(json.dumps(trace))
                        image = next((
                            row for row in invalid["output_authority"]["jobs"][0]["tree"]
                            if row["kind"] == "exec" and len(row["argv"]) == 4
                        ), None)
                        if image is None:
                            continue
                        image["admission"][field] = value
                        with self.subTest(image_field=field):
                            with self.assertRaises(read_epochs.ReadEpochError):
                                read_epochs.validate_trace(
                                    invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                                )
            self.assert_clean(session)

    def test_native_returned_image_plan_is_bound_to_independent_issued_authorization(self):
        outputs, resources = self._native_image_operand_fixture()
        self.add("Makefile", "all:\n\t@MODE=inherited /native/tool\n")
        for mutation in ("output", "resource", "sequence"):
            body = (
                "from authority import native_command_owner\n"
                "original=guard.supervise\n"
                "def altered(config,drop):\n"
                " status=original(config,drop)\n"
                " if config.get('native_output_paths') and status==0:\n"
                "  path=Path(config['report']);report=json.loads(path.read_text());trace=report['read_trace']\n"
                "  image=next(row['event'] for row in trace['machine']['events']"
                " if row['kind']=='native-tree' and row['event']['kind']=='exec' and len(row['event']['argv'])==4)\n"
                "  admission=image['admission']\n"
                + ("  admission['outputs'].append('stage/second')\n" if mutation == "output" else
                   "  admission['resources'].append(['temporary','stage/tmp'])\n" if mutation == "resource" else
                   "  admission['sequence']=1\n")
                + "  admission['owner']=native_command_owner(admission['closure'],admission['outputs'],admission['resources'])\n"
                "  def update(tree):\n"
                "   for event in tree:\n"
                "    if event['kind']=='exec' and event['pid']==image['pid'] and event['generation']==image['generation']:\n"
                "     event['admission']=admission.copy()\n"
                "  for job in trace['output_authority']['jobs']:update(job['tree'])\n"
                "  for row in trace['machine']['events']:\n"
                "   if row['kind']=='native-tree' and row['event']['kind']=='exec'"
                " and row['event']['pid']==image['pid'] and row['event']['generation']==image['generation']:\n"
                "    row['event']['admission']=admission.copy();row['sha256']=guard.hashlib.sha256(guard.encoded(row['event'])).hexdigest()\n"
                "  for index,value in enumerate(report['accessed']):\n"
                "   if value.startswith('native-job:'):\n"
                "    job=json.loads(value.removeprefix('native-job:'));update(job['tree'])\n"
                "    report['accessed'][index]='native-job:'+json.dumps(job)\n"
                "  path.write_text(json.dumps(report))\n"
                " return status\n"
                "guard.supervise=altered\n"
            )
            session = self.session()
            with self.subTest(mutation=mutation), session:
                tool = session.compile_native(("native.c",))
                class Commands:
                    def __getitem__(self, argv):
                        helper = len(argv) == 4 and argv[0] == "/native/tool"
                        return Command(
                            argv, native_tool=tool, outputs=() if helper else outputs,
                            native_resources=() if helper else resources,
                        )
                with self.native_supervisor(body), self.assertRaisesRegex(
                    MakeProbeError, "issued request" if mutation == "sequence" else
                    "native image differs from its issued operand admission",
                ):
                    session._native_make_writable(
                        "all", outputs=outputs, native_resources=resources, native_tool=tool,
                        commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                    )
            self.assert_clean(session)

    def test_native_failed_path_operations_use_current_image_and_fork_only_plans(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <unistd.h>\n"
            "#include <sys/stat.h>\n#include <sys/wait.h>\n#include <errno.h>\n"
            "#ifdef DIRECTORY\n#define OUTPUT \"/repo/stage/result\"\n"
            "#else\n#define OUTPUT \"/repo/result\"\n#endif\n"
            "int main(int argc,char **argv){int fd,status;pid_t child;(void)argv;"
            "if(argc>1)return 0;\n"
            "#ifdef DIRECTORY\n"
            "if(mkdir(\"/repo/stage\",0700))return 1;\n"
            "#endif\n"
            "fd=open(OUTPUT,O_CREAT|O_EXCL|O_WRONLY,0644);if(fd<0||close(fd))return 2;"
            "if(open(OUTPUT,O_CREAT|O_EXCL|O_WRONLY,0644)!=-1||errno!=EEXIST)return 3;\n"
            "#ifdef DIRECTORY\n"
            "if(mkdir(\"/repo/stage\",0700)!=-1||errno!=EEXIST)return 4;\n"
            "if(rmdir(\"/repo/stage\")!=-1||errno!=ENOTEMPTY)return 5;\n"
            "#endif\n"
            "child=fork();if(child<0)return 6;"
            "if(!child){execl(\"/native/tool\",\"/native/tool\",\"helper\",(char*)0);return 7;}"
            "if(waitpid(child,&status,0)!=child||status)return 8;return 0;}\n"
        ))
        self.add("Makefile", "all:\n\t@/native/tool\n")
        for directory in (False, True):
            outputs = ("stage/result" if directory else "result",)
            resources = (("directory", "stage"),) if directory else ()
            session = self.session()
            with self.subTest(directory=directory), session:
                tool = session.compile_native(
                    ("native.c",), defines=("DIRECTORY",) if directory else (),
                )
                class Commands:
                    def __getitem__(self, argv):
                        helper = len(argv) == 2 and argv[0] == "/native/tool"
                        return Command(
                            argv, native_tool=tool, outputs=() if helper else outputs,
                            native_resources=() if helper else resources,
                        )
                _, _, observed, generated = session._native_make_writable(
                    "all", outputs=outputs, native_resources=resources, native_tool=tool,
                    commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                )
                self.assertEqual([(row.path, row.data) for row in generated], [(outputs[0], b"")])
                trace = observed["read_trace"]
                failures = [
                    row for row in trace["machine"]["events"]
                    if row["kind"] == "native-output" and row["event"]["kind"] == "output-operation-failed"
                ]
                self.assertEqual({row["event"]["operation"] for row in failures}, {"open", "mkdir", "rmdir"} if directory else {"open"})
                for failure in failures:
                    for phase in ("start", "exec"):
                        invalid = json.loads(json.dumps(trace))
                        rows = invalid["machine"]["events"]
                        moved = next(row for row in rows if row["seq"] == failure["seq"])
                        rows.remove(moved)
                        anchor = next(
                            row for row in rows if row["kind"] == "native-tree"
                            and row["event"]["kind"] == phase and (
                                phase == "start" or len(row["event"]["argv"]) == 2
                            )
                        )
                        moved["event"]["pid"] = anchor["event"]["pid"]
                        rows.insert(rows.index(anchor) + 1, moved)
                        effect_number = 0
                        for number, row in enumerate(rows, 1):
                            row["seq"] = number
                            if row["kind"] == "native-output":
                                effect_number += 1
                                row["event"]["sequence"] = effect_number
                                row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                        with self.subTest(operation=failure["event"]["operation"], phase=phase):
                            with self.assertRaisesRegex(read_epochs.ReadEpochError, "current image"):
                                read_epochs.validate_trace(
                                    invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                                )
            self.assert_clean(session)

    def test_native_failed_path_operation_owners_bind_actual_dispatch_without_reassigning_creators(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <unistd.h>\n"
            "#include <sys/stat.h>\n#include <stdio.h>\n#include <errno.h>\n"
            "#ifdef DIRECTORY\n#define OUTPUT \"/repo/stage/result\"\n"
            "#else\n#define OUTPUT \"/repo/result\"\n#endif\n"
            "int main(int argc,char **argv){int fd;(void)argv;if(argc>1)return 0;\n"
            "#ifdef DIRECTORY\n"
            "if(mkdir(\"/repo/stage\",0700))return 1;\n"
            "#endif\n"
            "fd=open(OUTPUT,O_CREAT|O_EXCL|O_WRONLY,0644);if(fd<0||close(fd))return 2;"
            "if(open(OUTPUT,O_CREAT|O_EXCL|O_WRONLY,0644)!=-1||errno!=EEXIST)return 3;\n"
            "#ifdef DIRECTORY\n"
            "if(mkdir(\"/repo/stage\",0700)!=-1||errno!=EEXIST)return 4;\n"
            "if(rmdir(\"/repo/stage\")!=-1||errno!=ENOTEMPTY)return 5;\n"
            "if(unlink(\"/repo/stage/missing\")!=-1||errno!=ENOENT)return 6;\n"
            "if(rename(\"/repo/stage/missing\",OUTPUT)!=-1||errno!=ENOENT)return 7;\n"
            "#endif\nreturn 0;}\n"
        ))
        self.add("Makefile", "all:\n\t@/native/tool\n\t@/native/tool again\n")
        for directory in (False, True):
            outputs = ("stage/result" if directory else "result",)
            resources = (("directory", "stage"), ("temporary", "stage/missing")) if directory else ()
            session = self.session()
            with self.subTest(directory=directory), session:
                tool = session.compile_native(
                    ("native.c",), defines=("DIRECTORY",) if directory else (),
                )
                class Commands:
                    def __getitem__(self, argv):
                        return Command(argv, native_tool=tool, outputs=outputs, native_resources=resources)
                _, _, observed, generated = session._native_make_writable(
                    "all", outputs=outputs, native_resources=resources, native_tool=tool,
                    commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                )
                self.assertEqual([(row.path, row.data) for row in generated], [(outputs[0], b"")])
                trace = observed["read_trace"]
                failures = [
                    row for row in trace["machine"]["events"]
                    if row["kind"] == "native-output" and row["event"]["kind"] == "output-operation-failed"
                ]
                self.assertEqual([row["sequence"] for row in trace["output_authority"]["jobs"]], [1, 2])
                self.assertEqual({row["dispatch"] for row in failures}, {1})
                self.assertEqual(
                    {row["event"]["operation"] for row in failures},
                    {"open", "mkdir", "rmdir", "remove", "replace"} if directory else {"open"},
                )
                for failure in failures:
                    self.assertEqual(failure["event"]["owner"], failure["dispatch"])
                    invalid = json.loads(json.dumps(trace))
                    moved = next(row for row in invalid["machine"]["events"] if row["seq"] == failure["seq"])
                    moved["event"]["owner"] = 2
                    moved["sha256"] = hashlib.sha256(encoded(moved["event"])).hexdigest()
                    with self.subTest(operation=failure["event"]["operation"]):
                        with self.assertRaisesRegex(
                            read_epochs.ReadEpochError,
                            "actual pathname dispatch" if directory else "actual job binding",
                        ):
                            read_epochs.validate_trace(
                                invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                            )
            self.assert_clean(session)

    def test_native_later_job_mkdir_preserves_earlier_directory_creator(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#include <sys/stat.h>\n#include <errno.h>\n"
            "int main(int argc,char **argv){(void)argv;if(argc==1){"
            "return mkdir(\"/repo/stage\",0700)!=0;}"
            "if(mkdir(\"/repo/stage\",0700)!=-1||errno!=EEXIST)return 2;"
            "return mkdir(\"/repo/stage/next\",0700)!=0;}\n"
        ))
        self.add("Makefile", "all:\n\t@/native/tool\n\t@/native/tool next\n\t@printf final > result\n")
        resources = (("directory", "stage"), ("directory", "stage/next"))
        for admitted in (True, False):
            session = self.session()
            with self.subTest(admitted=admitted), session:
                tool = session.compile_native(("native.c",))
                class Commands:
                    def __getitem__(self, argv):
                        if argv[0] == "/native/tool":
                            selected = resources if admitted or len(argv) == 1 else (resources[1],)
                            return Command(argv, native_tool=tool, native_resources=selected)
                        return Command(argv, outputs=("result",))
                if not admitted:
                    self.addCleanup(self.assert_clean, session)
                    with self.assertRaisesRegex(MakeProbeError, "write lacks its issued output authority"):
                        session._native_make_writable(
                            "all", outputs=("result",), native_resources=resources, native_tool=tool,
                            commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                        )
                    continue
                completed, _, observed, generated = session._native_make_writable(
                    "all", outputs=("result",), native_resources=resources, native_tool=tool,
                    commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                )
                self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
                self.assertEqual([(row.path, row.data, row.mode) for row in generated], [
                    ("result", b"final", 0o644),
                ])
                trace = observed["read_trace"]
                effects = read_epochs.native_output_effects(trace)
                failed, = [row for row in effects if row["kind"] == "output-operation-failed"]
                self.assertEqual((failed["owner"], failed["operation"], failed["result"]), (2, "mkdir", -errno.EEXIST))
                created = [row for row in effects if row["kind"] == "output-mkdir"]
                self.assertEqual([(row["owner"], row["path"]) for row in created], [
                    (1, "/repo/stage"), (2, "/repo/stage/next"),
                ])
                first_identity = created[0]["identity"]
                self.assertEqual(failed["preimages"][0], ["/repo/stage", first_identity, []])
                for field, value in (("owner", 1), ("preimages", []), ("flags", 0)):
                    invalid = json.loads(json.dumps(trace))
                    machine = next(
                        row for row in invalid["machine"]["events"]
                        if row["kind"] == "native-output" and row["event"]["kind"] == "output-operation-failed"
                    )
                    machine["event"][field] = value
                    machine["sha256"] = hashlib.sha256(encoded(machine["event"])).hexdigest()
                    with self.subTest(field=field), self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            self.assert_clean(session)

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
            self.assertTrue(all(set(row["admission"]) == {"owner", "input_sha256"} for row in jobs))
            self.assertTrue(all(
                "admission_owner" not in row for row in observed["read_trace"]["machine"]["events"]
                if row["kind"] == "execute"
            ))
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
            read_epochs.validate_native_output_authority(
                trace, count_limit=100000, file_limit=5, reserve=lambda size: None,
            )
            with self.assertRaises(read_epochs.ReadEpochError):
                read_epochs.validate_native_output_authority(
                    trace, count_limit=100000, file_limit=4, reserve=lambda size: None,
                )
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
            missing = json.loads(json.dumps(trace))
            missing["output_authority"]["paths"].append("missing")
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "retained"):
                read_epochs.validate_trace(missing, missing["scope"], count_limit=100000, file_limit=10000000)
            for field, value in (
                ("fd", 999), ("revision", 100), ("path", "/repo/other"),
                ("result", 0), ("result", 6), ("result", 10000001),
            ):
                with self.subTest(archive_field=field, value=value):
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

    def test_native_original_make_overwrite_return_is_bound_to_its_stopped_request(self):
        from scripts.validation_ownership import read_epochs
        recipe = "printf '%0100d' 0 > result; printf final 1<>result"
        self.add("Makefile", "all:\n\t@" + recipe + "\n")
        class Commands:
            def __getitem__(self, argv):
                return Command(argv, outputs=("result",))
        session = self.session()
        with session:
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("result",), observe_reads=True,
                observe_runtime_completions=True, commands=Commands(),
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
            self.assertEqual(
                [(item.path, item.data, item.mode) for item in generated],
                [("result", b"final" + b"0" * 95, 0o644)],
            )
            trace = observed["read_trace"]
            writes = [row for row in read_epochs.native_output_effects(trace) if row["kind"] == "output-write"]
            self.assertEqual([row["result"] for row in writes], [100, 5])
            self.assertEqual([row["identity"][3] for row in writes], [100, 100])
            entries = [row for row in read_epochs.native_output_effects(trace) if row["kind"] == "output-write-entry"]
            self.assertEqual([row["requested_count"] for row in entries], [100, 5])
            self.assertEqual([row["offset"] for row in entries], [0, 0])
            self.assertEqual([row["request"] for row in entries], [row["request"] for row in writes])
            for kind, field, value in (
                ("output-write", "result", 6),
                ("output-write", "request", entries[0]["request"]),
                ("output-write", "request", True),
                ("output-write-entry", "request", entries[0]["request"]),
                ("output-write-entry", "requested_count", 4),
                ("output-write-entry", "requested_count", True),
                ("output-write-entry", "offset", 10000000),
                ("output-write-entry", "description", 999),
                ("output-write-entry", "fd", 999),
                ("output-write-entry", "revision", 999),
                ("output-write-entry", "identity", entries[0]["identity"]),
            ):
                with self.subTest(kind=kind, field=field):
                    invalid = json.loads(json.dumps(trace))
                    row = [
                        row for row in invalid["machine"]["events"]
                        if row["kind"] == "native-output" and row["event"]["kind"] == kind
                    ][1]
                    row["event"][field] = value
                    row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            for missing in ("output-write-entry", "output-write"):
                with self.subTest(missing=missing):
                    invalid = json.loads(json.dumps(trace))
                    rows = invalid["machine"]["events"]
                    removed = [
                        row for row in rows
                        if row["kind"] == "native-output" and row["event"]["kind"] == missing
                    ][1]
                    rows.remove(removed)
                    effect_number = 0
                    for number, row in enumerate(rows, 1):
                        row["seq"] = number
                        if row["kind"] == "native-output":
                            effect_number += 1
                            row["event"]["sequence"] = effect_number
                            row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                    expected = "stopped request" if missing == "output-write-entry" else "pending write return"
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, expected):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
        self.assert_clean(session)

    def test_native_original_make_remakes_and_reads_generated_include_once(self):
        from scripts.validation_ownership import read_epochs
        recipe = "printf 'VALUE := produced\\n' > generated.mk"
        all_recipe = "v=once; printf '%s' \"$$v\""
        self.add("Makefile", (
            "-include generated.mk\n"
            "generated.mk:\n\t@" + recipe + "\n"
            "all:\n\t@" + all_recipe + "\n"
        ))
        requests = []
        class Commands:
            def __getitem__(self, argv):
                requests.append(argv)
                return Command(
                    argv, outputs=("generated.mk",) if argv == ("/bin/sh", "-c", recipe) else (),
                )
        session = self.session()
        with session:
            completed, semantics, observed, generated = session._native_make_writable(
                "all", outputs=("generated.mk",), variables=("VALUE",),
                observe_reads=True, observe_runtime_completions=True, commands=Commands(),
            )
            self.assertEqual(completed.stdout, b"once")
            self.assertEqual(completed.stderr, b"")
            self.assertEqual(semantics["domains"]["VALUE"]["value"], "produced")
            self.assertEqual(
                [(item.path, item.data, item.mode) for item in generated],
                [("generated.mk", b"VALUE := produced\n", 0o644)],
            )
            self.assertEqual(requests.count(("/bin/sh", "-c", recipe)), 1)
            self.assertEqual(len(requests), 2)
            trace = observed["read_trace"]
            opened = [
                row for row in trace["events"]
                if row["kind"] == "source-open" and row["path"] == "generated.mk"
                and row["source"] is not None
            ]
            self.assertEqual(len(opened), 1)
            self.assertNotEqual(opened[0]["custody"]["kind"], "snapshot")
            retired = [
                row for row in trace["machine"]["events"]
                if row["kind"] == "pin-retired" and row["visit"] == opened[0]["visit"]
            ]
            self.assertEqual(len(retired), 1)
            read_epochs.validate_trace(
                trace, trace["scope"], count_limit=100000, file_limit=10000000,
            )
            entry = trace["machine"]["events"][opened[0]["custody"]["entry"] - 1]
            self.assertEqual(entry["kind"], "generated-source-entry")
            self.assertEqual(entry["visit"], opened[0]["visit"])
            self.assertEqual(entry["identity"], opened[0]["identity"])
            self.assertEqual(entry["owner"], 1)
            for field, value in (
                ("owner", 2), ("owner", True), ("serial", 99), ("serial", True),
                ("revision", entry["revision"] + 1), ("revision", entry["revision"] - 1),
                ("revision", True), ("visit", 99), ("pid", 1),
                ("path", "other.mk"), ("path", []), ("sha256", "0" * 64),
                ("identity", []), ("identity", None),
                ("identity", [True] * 7), ("trace_seq", True),
            ):
                with self.subTest(generated_entry_field=field, value=value):
                    invalid = json.loads(json.dumps(trace))
                    invalid["machine"]["events"][entry["seq"] - 1][field] = value
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            for custody in (
                {"kind": "snapshot"}, {"kind": "native-output", "entry": True},
                {"kind": "native-output", "entry": 1},
                {"kind": "native-output", "entry": entry["seq"] + 1},
            ):
                with self.subTest(generated_custody=custody):
                    invalid = json.loads(json.dumps(trace))
                    invalid["events"][opened[0]["seq"] - 1]["custody"] = custody
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            for machine in (
                None, {}, [], 1,
                {"version": 1, "closed": True},
                {"version": 1, "closed": True, "events": None},
                {"version": 1, "closed": True, "events": 1},
                {"version": 1, "closed": True, "events": {}},
                {"version": 1, "closed": True, "events": []},
                {**trace["machine"], "version": True},
                {**trace["machine"], "closed": 1},
                {**trace["machine"], "extra": 1},
            ):
                with self.subTest(machine_envelope=machine):
                    invalid = json.loads(json.dumps(trace))
                    invalid["machine"] = machine
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
        self.assert_clean(session)

    def test_native_original_make_generated_reader_refuses_writes_before_mutation(self):
        recipe = (
            "printf '%s\\n' 'VALUE := produced' "
            "'TOUCH := $$(shell printf hacked > generated.mk)' > generated.mk"
        )
        for makefile, expected, expected_requests in (
            (
                "-include generated.mk\ngenerated.mk:\n\t@" + recipe + "\nall:\n\t@:\n",
                "native output open would destroy an active source/writer version",
                [
                    ("/bin/sh", "-c", recipe.replace("$$", "$")),
                    ("/bin/sh", "-c", "printf hacked > generated.mk"),
                ],
            ),
            (
                "$(file >generated.mk,unissued)\nall:\n\t@:\n",
                "native job write lacks its issued output authority",
                [],
            ),
        ):
            with self.subTest(refusal=expected):
                self.add("Makefile", makefile)
                requests = []
                class Commands:
                    def __getitem__(self, argv):
                        requests.append(argv)
                        return Command(argv, outputs=("generated.mk",))
                session = self.session()
                with session:
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session._native_make_writable(
                            "all", outputs=("generated.mk",),
                            observe_reads=True, observe_runtime_completions=True, commands=Commands(),
                        )
                self.assertEqual(requests, expected_requests)
                self.assert_clean(session)

    def test_generated_source_return_keeps_observed_retirement_and_rejects_unobserved_change(self):
        import ctypes
        import struct
        import time
        from types import SimpleNamespace
        from scripts.validation_ownership import read_epochs
        from scripts.validation_ownership.native_outputs import NativeOutputs
        from scripts.validation_ownership.producer_channel import publication_identity
        from scripts.validation_ownership.read_trace import NativeReadTrace

        for generated, mutate, foreign_pin in (
            (True, False, False), (False, False, False),
            (True, True, False), (True, False, True),
        ):
            with self.subTest(generated=generated, unobserved_mutation=mutate, foreign_pin=foreign_pin):
                effects = []
                outputs = NativeOutputs(
                    deadline=lambda: None, charge=lambda amount: None, file_limit=65536,
                    emit=lambda kind, **fields: effects.append({"kind": kind, **fields}),
                )
                descriptors = []
                trace = NativeReadTrace.__new__(NativeReadTrace)
                trace.active = []
                def create(name, data):
                    descriptor = os.open(self.root / name, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                    descriptors.append(descriptor)
                    outputs.opened(
                        owner=1, pid=1, descriptor=descriptor, pin=descriptor,
                        path="/repo/" + name, writing=True,
                    )
                    outputs.before_write(1, descriptor, descriptor)
                    outputs.written(1, descriptor, descriptor, os.write(descriptor, data))
                    outputs.closed(1, descriptor, 0)
                    return descriptor
                stem = "generated-" + str(generated) + "-" + str(mutate) + "-" + str(foreign_pin)
                old_name, new_name = stem + ".mk", stem + ".tmp"
                try:
                    old = create(old_name, b"VALUE := old\n")
                    source = outputs.capture(
                        owner=1, path="/repo/" + old_name, descriptor=old, observed=False,
                    )
                    new = create(new_name, b"VALUE := new\n")
                    os.replace(self.root / new_name, self.root / old_name)
                    outputs.replaced(
                        owner=1, source="/repo/" + new_name, destination="/repo/" + old_name,
                        source_pin=new, retired_pin=old, result=0,
                    )
                    self.assertTrue(source.object.retired)
                    self.assertTrue(any(
                        effect["kind"] == "output-retire" and effect["serial"] == source.object.serial
                        for effect in effects
                    ))
                    self.assertEqual(source.object.identity[6], 0)
                    self.assertNotEqual(source.object.serial, outputs.objects["/repo/" + old_name].serial)
                    self.assertEqual(os.pread(old, 65536, 0), b"VALUE := old\n")
                    self.assertEqual((self.root / old_name).read_bytes(), b"VALUE := new\n")
                    if mutate:
                        os.pwrite(old, b"X", 0)
                    resolved = ctypes.create_string_buffer(("/repo/" + old_name).encode())
                    status = ctypes.create_string_buffer(struct.pack(
                        "<QQQQIiQQQ", 0, ctypes.addressof(resolved), 0, 0, 0, 0, 0, 0, 0,
                    ))
                    trace.version = read_epochs.WRITABLE_VERSION
                    trace.runtime = True
                    trace.config = {"deadline": time.monotonic() + 10}
                    trace.native = SimpleNamespace(
                        publication_identity=publication_identity,
                        memory=lambda pid, address, size: ctypes.string_at(address, size),
                    )
                    trace.policy = SimpleNamespace(
                        charge_metadata=lambda amount: None,
                        reserve_trace_observation=lambda: None,
                        native_outputs=SimpleNamespace(custody=outputs),
                    )
                    trace.pid = os.getpid()
                    trace.execs = trace.passes = 1
                    trace.events, trace.machine = [], []
                    trace.pass_frame = trace.io = None
                    trace.invocations = [{"kind": "source", "visit": 1}]
                    trace.active = [{
                        "stack": 1000, "visit": 1, "name": "/repo/" + old_name, "flags": 0,
                        "source": 1, "pin": os.dup(new if foreign_pin else old), "closed": True,
                        "identity": source.identity, "path": "/repo/" + old_name,
                        **({"generated": source} if generated else {}),
                    }]
                    registers = SimpleNamespace(rsp=1008, rax=ctypes.addressof(status))
                    if generated and not mutate and not foreign_pin:
                        trace.source_return(registers)
                        self.assertTrue(source.closed)
                        self.assertFalse(source.object.readers)
                        self.assertFalse(outputs.pins)
                        self.assertFalse(trace.active)
                        self.assertEqual(trace.machine[-1]["kind"], "pin-retired")
                        outputs.finish()
                    else:
                        with self.assertRaises(MakeProbeError):
                            trace.source_return(registers)
                        self.assertFalse(source.closed)
                finally:
                    trace.close()
                    outputs.close()
                    for descriptor in descriptors:
                        os.close(descriptor)


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
            premature = json.loads(json.dumps(trace))
            events = premature["machine"]["events"]
            settlement = next(
                row for row in events if row["kind"] == "native-output" and row["event"]["kind"] == "output-settled"
            )
            events.remove(settlement)
            earlier_close = next(
                index for index, row in enumerate(events)
                if row["kind"] == "native-output" and row["event"]["kind"] == "output-close"
                and row["event"]["pid"] == inherited["pid"]
            )
            events.insert(earlier_close, settlement)
            number = 0
            for sequence, row in enumerate(events, 1):
                row["seq"] = sequence
                if row["kind"] == "native-output":
                    number += 1
                    row["event"]["sequence"] = number
                    row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "settlement"):
                read_epochs.validate_trace(premature, premature["scope"], count_limit=100000, file_limit=10000000)
            for phase in ("before-fork", "before-start", "after-exit"):
                with self.subTest(actor_phase=phase):
                    invalid = json.loads(json.dumps(trace))
                    events = invalid["machine"]["events"]
                    kind = "output-inherit" if phase == "before-fork" else "output-close"
                    moved = [
                        row for row in events if row["kind"] == "native-output"
                        and (row["event"]["kind"] != "output-inherit" if phase == "before-start" else row["event"]["kind"] == kind)
                        and row["event"].get("pid") == inherited["pid"]
                    ]
                    self.assertTrue(moved)
                    events[:] = [row for row in events if row not in moved]
                    anchor = next(
                        index for index, row in enumerate(events)
                        if row["kind"] == "native-tree" and (
                            row["event"]["kind"] == "fork" and row["event"]["child"] == inherited["pid"]
                            if phase == "before-fork" else
                            row["event"]["kind"] == "start" and row["event"]["pid"] == inherited["pid"]
                            if phase == "before-start" else
                            row["event"]["kind"] == "exit" and row["event"]["pid"] == inherited["pid"]
                        )
                    )
                    position = anchor if phase != "after-exit" else anchor + 1
                    events[position:position] = moved
                    for number, row in enumerate(read_epochs.native_output_effects(invalid), 1):
                        row["sequence"] = number
                    for number, row in enumerate(events, 1):
                        row["seq"] = number
                        if row["kind"] == "native-output":
                            row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "live job actor"):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
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
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "live job actor"):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
        self.assert_clean(session)


    def test_native_output_authority_covers_actual_output_free_jobs(self):
        from scripts.validation_ownership import read_epochs
        self.add("Makefile", "all:\n\t@printf final > result\n\t@printf '%s' once\n")
        requests = []
        class Commands:
            def __getitem__(self, argv):
                requests.append(argv)
                if argv == ("/bin/sh", "-c", "printf final > result"):
                    return Command(argv, outputs=("result",))
                if argv == ("printf", "%s", "once"):
                    return Command(argv)
                raise KeyError(argv)
        session = self.session()
        with session:
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("result",), commands=Commands(),
                native_executables=("/usr/bin/printf",),
                observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"once", b""))
            self.assertEqual([(item.path, item.data, item.mode) for item in generated], [("result", b"final", 0o644)])
            trace = observed["read_trace"]
            self.assertEqual(len(requests), 2)
            self.assertEqual([job["admission"]["outputs"] for job in trace["output_authority"]["jobs"]], [["result"], []])
            read_epochs.validate_trace(trace, trace["scope"], count_limit=100000, file_limit=10000000)
            missing = json.loads(json.dumps(trace))
            missing["output_authority"]["jobs"].pop()
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "actual job dispatch"):
                read_epochs.validate_trace(missing, missing["scope"], count_limit=100000, file_limit=10000000)
        self.assert_clean(session)


    def test_native_returned_archive_owner_is_bound_to_issued_command(self):
        self.add("Makefile", "all:\n\t@printf final > first\n\t@printf final > second\n")
        resources = (("temporary", "spare"),)
        class Commands:
            def __getitem__(self, argv):
                if argv not in {("/bin/sh", "-c", "printf final > " + name) for name in ("first", "second")}:
                    raise KeyError(argv)
                return Command(argv, outputs=(argv[-1].split()[-1],))
        for mutation in ("output", "resource", "global-resource"):
            body = (
                "from authority import native_command_owner\n"
                "original=guard.supervise\n"
                "def altered(config,drop):\n"
                " status=original(config,drop)\n"
                " if config.get('native_output_paths') and status==0:\n"
                "  path=Path(config['report']);report=json.loads(path.read_text())\n"
                "  trace=report['read_trace'];admission=trace['output_authority']['jobs'][0]['admission']\n"
                + ("  admission['outputs'].append('second')\n" if mutation == "output" else
                   "  admission['resources'].append(['temporary','spare'])\n" if mutation == "resource" else
                   "  trace['output_authority']['resources'].append(['temporary','unissued'])\n")
                + "  admission['owner']=native_command_owner(admission['closure'],admission['outputs'],admission['resources'])\n"
                "  root=trace['output_authority']['jobs'][0]\n"
                "  def update(tree):\n"
                "   for event in tree:\n"
                "    if event['kind']=='exec' and event['pid']==root['pid'] and event['generation']==1:\n"
                "     event['admission']=admission.copy()\n"
                "  update(root['tree'])\n"
                "  for event in trace['machine']['events']:\n"
                "   if event['kind']=='execute' and not event['make'] and event['dispatch']==1:\n"
                "    event['admission_owner']=admission['owner']\n"
                "   if event['kind']=='native-tree' and event['event']['kind']=='exec'"
                " and event['event']['pid']==root['pid'] and event['event']['generation']==1:\n"
                "    event['event']['admission']=admission.copy();event['sha256']=guard.hashlib.sha256(guard.encoded(event['event'])).hexdigest()\n"
                "  for index,value in enumerate(report['accessed']):\n"
                "   if value.startswith('native-job:'):\n"
                "    job=json.loads(value.removeprefix('native-job:'));update(job['tree'])\n"
                "    if job['pid']==root['pid']:job['admission']=admission.copy()\n"
                "    report['accessed'][index]='native-job:'+json.dumps(job)\n"
                "  path.write_text(json.dumps(report))\n"
                " return status\n"
                "guard.supervise=altered\n"
            )
            session = self.session()
            with self.subTest(returned_plan=mutation), self.native_supervisor(body), session:
                with self.assertRaisesRegex(MakeProbeError, "issued Command"):
                    session._native_make_writable(
                        "all", outputs=("first", "second"), native_resources=resources,
                        commands=Commands(), observe_reads=True, observe_runtime_completions=True,
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
            broadened = json.loads(json.dumps(observed["read_trace"]))
            broadened["output_authority"]["jobs"][0]["admission"]["outputs"].append("second")
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "Command owner"):
                read_epochs.validate_trace(broadened, broadened["scope"], count_limit=100000, file_limit=10000000)
            for mutation in ("missing-output", "borrow-owner", "borrow-closure", "recommit-plan", "recommit-closure"):
                with self.subTest(owner_plan=mutation):
                    invalid = json.loads(json.dumps(observed["read_trace"]))
                    admission = invalid["output_authority"]["jobs"][0]["admission"]
                    if mutation == "missing-output":
                        admission["outputs"] = []
                    elif mutation == "borrow-owner":
                        admission["owner"] = jobs[1]["admission"]["owner"]
                    elif mutation == "borrow-closure":
                        admission["closure"] = jobs[1]["admission"]["closure"]
                    elif mutation == "recommit-plan":
                        admission["outputs"].append("second")
                    else:
                        admission["closure"] = jobs[1]["admission"]["closure"]
                    if mutation.startswith("recommit"):
                        admission["owner"] = native_command_owner(admission["closure"], admission["outputs"])
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "Command owner"):
                        read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
            for value in (None, True, [], "x" * 64):
                with self.subTest(closure_shape=value):
                    invalid = json.loads(json.dumps(observed["read_trace"]))
                    invalid["output_authority"]["jobs"][0]["admission"]["closure"] = value
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
            for make, value in ((True, jobs[0]["admission"]["owner"]), (False, None), (False, True), (False, "x" * 64)):
                with self.subTest(machine_owner_make=make, value=value):
                    invalid = json.loads(json.dumps(observed["read_trace"]))
                    execute = next(row for row in invalid["machine"]["events"] if row["kind"] == "execute" and row["make"] is make)
                    execute["admission_owner"] = value
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
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


    def test_native_original_make_directory_roles_preserve_actual_namespace_lifecycle(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sys/stat.h>\n#include <fcntl.h>\n"
            "#include <unistd.h>\n#include <errno.h>\n"
            "int main(void){int fd;"
            "if(mkdir(\"/repo/stage\",0700))return 1;"
            "if(mkdir(\"/repo/stage\",0700)!=-1||errno!=EEXIST)return 2;"
            "if(mkdir(\"/repo/stage/empty\",0700)||rmdir(\"/repo/stage/empty\"))return 3;"
            "fd=open(\"/repo/stage/generated.mk\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"VALUE := native\\n\",16)!=16||close(fd))return 4;"
            "return 0;}\n"
        ))
        self.add("Makefile", "all:\n\t@/native/tool\n")
        resources = (("directory", "stage"), ("directory", "stage/empty"))
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool, outputs=("stage/generated.mk",), native_resources=resources)
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("stage/generated.mk",), native_resources=resources,
                native_tool=tool, commands=Commands(), observe_reads=True,
                observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
            self.assertEqual([(row.path, row.data, row.mode) for row in generated], [
                ("stage/generated.mk", b"VALUE := native\n", 0o644),
            ])
            trace = observed["read_trace"]
            effects = read_epochs.native_output_effects(trace)
            self.assertEqual(len([row for row in effects if row["kind"] == "output-mkdir"]), 2)
            self.assertEqual(len([row for row in effects if row["kind"] == "output-rmdir"]), 1)
            failed, = [row for row in effects if row["kind"] == "output-operation-failed"]
            self.assertEqual((failed["operation"], failed["result"]), ("mkdir", -errno.EEXIST))
            for kind, field, value in (
                ("output-mkdir", "pid", 999),
                ("output-mkdir", "path", "/repo/foreign"),
                ("output-rmdir", "identity", [0] * 7),
                ("output-directory-change", "entries", ["foreign"]),
                ("output-directory-change", "before", [0] * 7),
                ("output-operation-failed", "preimages", []),
            ):
                with self.subTest(kind=kind, field=field):
                    invalid = json.loads(json.dumps(trace))
                    row = next(row for row in invalid["machine"]["events"] if row["kind"] == "native-output" and row["event"]["kind"] == kind)
                    row["event"][field] = value
                    row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
        self.assert_clean(session)

    def test_native_original_make_resource_roles_separate_temporary_lock_and_final_versions(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sys/stat.h>\n#include <sys/file.h>\n"
            "#include <fcntl.h>\n#include <unistd.h>\n#include <stdio.h>\n#include <string.h>\n#include <errno.h>\n"
            "int main(int argc,char **argv){int lock,fd,other;char temp[256];"
            "if(argc!=2)return 1;if(!strcmp(argv[1],\"create\")&&mkdir(\"/repo/stage\",0700))return 2;"
            "lock=open(\"/repo/stage/generation.lock\",O_CREAT|O_RDWR|O_NOFOLLOW,0600);"
            "if(lock<0||flock(lock,LOCK_EX))return 3;"
            "other=open(\"/repo/stage/generation.lock\",O_CREAT|O_RDWR|O_NOFOLLOW,0600);"
            "if(other<0||flock(other,LOCK_EX|LOCK_NB)!=-1||errno!=EAGAIN||close(other))return 8;"
            "if(!strcmp(argv[1],\"create\")){"
            "fd=open(\"/repo/stage/.asset-manifest-write-abcdefgh\",O_CREAT|O_EXCL|O_WRONLY,0600);"
            "if(fd<0||write(fd,\"VALUE := native\\n\",16)!=16||fchmod(fd,0644)||close(fd))return 4;"
            "if(rename(\"/repo/stage/.asset-manifest-write-abcdefgh\",\"/repo/stage/generated.mk\"))return 5;"
            "if(rename(\"/repo/stage/.asset-manifest-write-abcdefgh\",\"/repo/stage/generated.mk\")!=-1||errno!=ENOENT)return 10;"
            "if(unlink(\"/repo/stage/.asset-manifest-write-abcdefgh\")!=-1||errno!=ENOENT)return 11;"
            "if(rmdir(\"/repo/stage\")!=-1||errno!=ENOTEMPTY)return 12;"
            "}else{snprintf(temp,sizeof(temp),\"/repo/stage/generated.mk.%ld.tmp\",(long)getpid());"
            "fd=open(temp,O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"discard\",7)!=7||close(fd)||unlink(temp))return 6;}"
            "if(!strcmp(argv[1],\"create\")&&flock(lock,LOCK_UN))return 7;"
            "if(close(lock))return 9;return 0;}\n"
        ))
        self.add("Makefile", "all:\n\t@/native/tool create\n\t@/native/tool reopen\n")
        resources = (
            ("directory", "stage"), ("shared-lock", "stage/generation.lock"),
            ("atomic-temporary", "stage/.asset-manifest-write-"),
            ("pid-temporary", "stage/generated.mk"),
        )
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool, outputs=("stage/generated.mk",), native_resources=resources)
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("stage/generated.mk",), native_resources=resources + (("temporary", "stage/unused"),),
                native_tool=tool, commands=Commands(), observe_reads=True,
                observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
            self.assertEqual([(row.path, row.data, row.mode) for row in generated], [
                ("stage/generated.mk", b"VALUE := native\n", 0o644),
            ])
            trace = observed["read_trace"]
            effects = read_epochs.native_output_effects(trace)
            lock_opens = [row for row in effects if row["kind"] == "output-open" and row["path"] == "/repo/stage/generation.lock"]
            self.assertEqual([row["owner"] for row in lock_opens], [1, 1, 1, 1])
            self.assertEqual([row["operation_owner"] for row in lock_opens], [1, 1, 2, 2])
            self.assertEqual(len({row["description"] for row in lock_opens}), 4)
            self.assertTrue(all(not row["writing"] for row in lock_opens))
            locks = [row for row in effects if row["kind"] == "output-lock"]
            self.assertEqual(len(locks), 5)
            self.assertEqual([row["result"] for row in locks], [0, -errno.EAGAIN, 0, 0, -errno.EAGAIN])
            self.assertEqual(len([row for row in effects if row["kind"] == "output-lock-release"]), 1)
            self.assertEqual(len([row for row in effects if row["kind"] == "output-mode"]), 1)
            self.assertEqual(len([row for row in effects if row["kind"] == "output-replace"]), 1)
            self.assertEqual(len([row for row in effects if row["kind"] == "output-retire"]), 1)
            failed = [row for row in effects if row["kind"] == "output-operation-failed"]
            self.assertEqual([(row["operation"], row["result"]) for row in failed], [
                ("replace", -errno.ENOENT), ("remove", -errno.ENOENT), ("rmdir", -errno.ENOTEMPTY),
            ])
            read_epochs.validate_trace(trace, trace["scope"], count_limit=100000, file_limit=10000000)
            creations = [
                row for row in effects if row["kind"] == "output-mkdir"
                or row["kind"] == "output-open" and (
                    row["path"] == "/repo/stage/generation.lock" and row["operation_owner"] == 1
                    or row["path"].startswith("/repo/stage/.asset-manifest-write-")
                    or row["path"].endswith(".tmp")
                )
            ]
            seen = set()
            for creation in creations:
                if creation["serial"] in seen:
                    continue
                seen.add(creation["serial"])
                with self.subTest(foreign_creator=creation["path"]):
                    invalid = json.loads(json.dumps(trace))
                    foreign = 2 if creation["owner"] == 1 else 1
                    for row in invalid["machine"]["events"]:
                        if row["kind"] == "native-output" and row["event"].get("serial") == creation["serial"]:
                            row["event"]["owner"] = foreign
                            row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "creating dispatch"):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            broadened = json.loads(json.dumps(trace))
            broadened["output_authority"]["jobs"][0]["admission"]["resources"].append(["temporary", "stage/unused"])
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "Command owner"):
                read_epochs.validate_trace(broadened, broadened["scope"], count_limit=100000, file_limit=10000000)
            for mutation in ("omit-unused-pid", "reorder", "recommit"):
                with self.subTest(resource_plan=mutation):
                    invalid = json.loads(json.dumps(trace))
                    admission = invalid["output_authority"]["jobs"][0]["admission"]
                    if mutation == "omit-unused-pid":
                        admission["resources"].remove(["pid-temporary", "stage/generated.mk"])
                    elif mutation == "reorder":
                        admission["resources"].reverse()
                    else:
                        admission["resources"].append(["temporary", "stage/unused"])
                        admission["owner"] = native_command_owner(
                            admission["closure"], admission["outputs"], admission["resources"],
                        )
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "Command owner"):
                        read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
            leftover = json.loads(json.dumps(trace))
            rows = leftover["machine"]["events"]
            temporary_retirement = next(
                row["event"] for row in rows if row["kind"] == "native-output"
                and row["event"]["kind"] == "output-retire" and ".tmp" in row["event"]["destination"]
            )
            rows[:] = [
                row for row in rows if not (
                    row["kind"] == "native-output" and (
                        row["event"]["kind"] == "output-retire"
                        and row["event"]["serial"] == temporary_retirement["serial"]
                        or row["event"]["kind"] == "output-directory-change"
                        and row["event"]["operation"] == "remove"
                        and row["event"]["source"] == temporary_retirement["destination"]
                    )
                )
            ]
            number = 0
            for sequence, row in enumerate(rows, 1):
                row["seq"] = sequence
                if row["kind"] == "native-output":
                    number += 1
                    row["event"]["sequence"] = number
                    row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "unretired temporary"):
                read_epochs.validate_trace(leftover, leftover["scope"], count_limit=100000, file_limit=10000000)
            for kind, field, value in (
                ("output-lock", "description", 999),
                ("output-lock", "mode", 1),
                ("output-replace", "source", "/repo/foreign"),
                ("output-replace", "source", []),
                ("output-replace", "source", None),
                ("output-mode", "mode", 0o600),
                ("output-retire", "operation_owner", 1),
                ("output-operation-failed", "preimages", []),
                ("output-operation-failed", "operation", []),
            ):
                with self.subTest(kind=kind, field=field):
                    invalid = json.loads(json.dumps(trace))
                    row = next(row for row in invalid["machine"]["events"] if row["kind"] == "native-output" and row["event"]["kind"] == kind)
                    row["event"][field] = value
                    row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
            released_templates = [trace]
            for error in (errno.EINTR, errno.EIO, errno.ENOSPC, errno.EDQUOT):
                released = json.loads(json.dumps(trace))
                release_index = next(
                    index for index, row in enumerate(released["machine"]["events"])
                    if row["kind"] == "native-output" and row["event"]["kind"] == "output-lock-release"
                )
                close = released["machine"]["events"][release_index + 1]
                self.assertEqual(close["event"]["kind"], "output-close")
                close["event"].update(kind="output-close-failed", result=-error)
                close["sha256"] = hashlib.sha256(encoded(close["event"])).hexdigest()
                read_epochs.validate_trace(released, released["scope"], count_limit=100000, file_limit=10000000)
                released_templates.append(released)
            for template in released_templates:
                invalid = json.loads(json.dumps(template))
                rows = invalid["machine"]["events"]
                rows[:] = [row for row in rows if not (row["kind"] == "native-output" and row["event"]["kind"] == "output-lock-release")]
                effect_number = 0
                for number, row in enumerate(rows, 1):
                    row["seq"] = number
                    if row["kind"] == "native-output":
                        effect_number += 1
                        row["event"]["sequence"] = effect_number
                        row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                with self.assertRaisesRegex(read_epochs.ReadEpochError, "last close omitted its lock release"):
                    read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
            for operation in ("replace-lock", "remove-directory", "open-lock-truncate"):
                invalid = json.loads(json.dumps(trace))
                row = next(
                    row for row in invalid["machine"]["events"]
                    if row["kind"] == "native-output" and row["event"]["kind"] == (
                        "output-replace" if operation == "replace-lock" else "output-operation-failed"
                    )
                )
                effect = row["event"]
                if operation == "replace-lock":
                    effect["path"] = "/repo/stage/generation.lock"
                    expected = "native replacement lost"
                else:
                    directory = [
                        other for other in effects if other["kind"] == "output-directory-change"
                        and other["sequence"] < effect["sequence"]
                    ][-1]
                    effect.pop("flags", None)
                    effect["destination"] = None
                    if operation == "remove-directory":
                        effect.update(operation="remove", source="/repo/stage")
                        effect["preimages"] = [["/repo/stage", directory["identity"], directory["entries"]]]
                        expected = "namespace operation escaped its resource role matrix"
                    else:
                        effect.update(operation="open", source="/repo/stage/generation.lock", flags=os.O_RDWR | os.O_TRUNC)
                        effect["preimages"] = [
                            ["/repo/stage/generation.lock", lock_opens[0]["identity"], None],
                            ["/repo/stage", directory["identity"], directory["entries"]],
                        ]
                        expected = "failed open changed"
                row["sha256"] = hashlib.sha256(encoded(effect)).hexdigest()
                with self.subTest(role_matrix=operation):
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, expected):
                        read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
            for field, value in (("description", 999), ("fd", 999), ("mode", 1)):
                invalid = json.loads(json.dumps(trace))
                row = next(row for row in invalid["machine"]["events"] if row["kind"] == "native-output" and row["event"]["kind"] == "output-lock-release")
                row["event"][field] = value
                row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                with self.assertRaisesRegex(read_epochs.ReadEpochError, "last actual description binding|actual descriptor"):
                    read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
        self.assert_clean(session)

    def test_native_original_make_replaces_pinned_generated_source_with_actual_recipe(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sys/stat.h>\n#include <fcntl.h>\n"
            "#include <unistd.h>\n#include <stdio.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){int fd;const char *data;"
            "if(argc!=2)return 1;"
            "if(!strcmp(argv[1],\"create\")){if(mkdir(\"/repo/stage\",0700))return 2;"
            "fd=open(\"/repo/stage/generated.mk\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "data=\"VALUE := old\\nREPLACEMENT := $(shell /native/tool replace)\\nAFTER := old\\n\";"
            "}else{fd=open(\"/repo/stage/.asset-manifest-write-abcdefgh\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "data=\"VALUE := new\\nAFTER := new\\n\";}"
            "if(fd<0||write(fd,data,strlen(data))!=(ssize_t)strlen(data)||close(fd))return 3;"
            "if(!strcmp(argv[1],\"replace\")&&rename(\"/repo/stage/.asset-manifest-write-abcdefgh\","
            "\"/repo/stage/generated.mk\"))return 4;return 0;}\n"
        ))
        self.add("Makefile", (
            "-include stage/generated.mk\n"
            "stage/generated.mk:\n\t@/native/tool create\n"
            "all:\n\t@printf '%s|%s' '$(VALUE)' '$(AFTER)'\n"
        ))
        resources = (("directory", "stage"), ("atomic-temporary", "stage/.asset-manifest-write-"))
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    if argv[0] == "/native/tool":
                        return Command(argv, native_tool=tool, outputs=("stage/generated.mk",), native_resources=resources)
                    return Command(argv)
            completed, semantics, observed, generated = session._native_make_writable(
                "all", variables=("VALUE", "AFTER"), outputs=("stage/generated.mk",),
                native_resources=resources, native_tool=tool, commands=Commands(),
                native_executables=("/usr/bin/printf",),
                observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"old|old", b""))
            self.assertEqual(semantics["domains"]["VALUE"]["value"], "old")
            self.assertEqual(semantics["domains"]["AFTER"]["value"], "old")
            self.assertEqual([(row.path, row.data, row.mode) for row in generated], [
                ("stage/generated.mk", b"VALUE := new\nAFTER := new\n", 0o644),
            ])
            trace = observed["read_trace"]
            effects = read_epochs.native_output_effects(trace)
            retired, = [row for row in effects if row["kind"] == "output-retire"]
            replaced, = [row for row in effects if row["kind"] == "output-replace"]
            self.assertEqual((retired["owner"], retired["operation_owner"]), (1, 2))
            self.assertNotEqual(retired["serial"], replaced["serial"])
            lease, = [row for row in trace["machine"]["events"] if row["kind"] == "generated-source-entry"]
            self.assertEqual(lease["serial"], retired["serial"])
            self.assertEqual(lease["identity"][:5], retired["identity"][:5])
            self.assertEqual((lease["identity"][6], retired["identity"][6]), (1, 0))
            returned = [row for row in trace["machine"]["events"] if row["kind"] == "pin-retired" and row["visit"] == lease["visit"]]
            self.assertEqual(len(returned), 1)
            read_epochs.validate_trace(trace, trace["scope"], count_limit=100000, file_limit=10000000)
        self.assert_clean(session)

    def test_native_original_make_resource_roles_refuse_unissued_and_shared_content_effects(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sys/stat.h>\n#include <fcntl.h>\n"
            "#include <unistd.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){int fd;if(argc!=2)return 1;"
            "if(mkdir(\"/repo/stage\",0700))return 2;"
            "if(!strcmp(argv[1],\"unknown-temp\"))"
            "return open(\"/repo/stage/.asset-manifest-write-toolonggg\",O_CREAT|O_WRONLY,0600)<0;"
            "if(!strcmp(argv[1],\"foreign-pid\"))"
            "return open(\"/repo/stage/result.999999999.tmp\",O_CREAT|O_WRONLY,0600)<0;"
            "fd=open(\"/repo/stage/generation.lock\",O_CREAT|O_RDWR|O_NOFOLLOW,0600);"
            "if(fd<0)return 3;if(!strcmp(argv[1],\"write-lock\"))return write(fd,\"bad\",3)!=3;"
            "if(!strcmp(argv[1],\"source-lock\"))return close(fd);"
            "if(!strcmp(argv[1],\"mode-lock\"))return fchmod(fd,0644);"
            "return open(\"/repo/stage/generation.lock\",O_CREAT|O_RDWR|O_TRUNC,0600)<0;}\n"
        ))
        resources = (
            ("directory", "stage"), ("shared-lock", "stage/generation.lock"),
            ("atomic-temporary", "stage/.asset-manifest-write-"), ("pid-temporary", "stage/result"),
        )
        for mode in ("unknown-temp", "foreign-pid", "write-lock", "mode-lock", "truncate-lock", "source-lock"):
            with self.subTest(mode=mode):
                self.add("Makefile", (
                    "-include stage/generation.lock\nstage/generation.lock:\n\t@/native/tool source-lock\nall:\n"
                ) if mode == "source-lock" else "all:\n\t@/native/tool " + mode + "\n")
                session = self.session()
                with session:
                    tool = session.compile_native(("native.c",))
                    class Commands:
                        def __getitem__(self, argv):
                            return Command(argv, native_tool=tool, outputs=("stage/result",), native_resources=resources)
                    with self.assertRaisesRegex(MakeProbeError, "issued output authority|shared lock open|resource role cannot become a generated source"):
                        session._native_make_writable(
                            "all", outputs=("stage/result",), native_resources=resources,
                            native_tool=tool, commands=Commands(), observe_reads=True,
                            observe_runtime_completions=True,
                        )
                self.assert_clean(session)

    def test_native_resource_scope_refuses_pattern_source_collisions_and_ambiguous_roles(self):
        from scripts.validation_ownership.native_resources import (
            resource_plan, resource_operation, validate_resource_scope,
            require_retained_source, validate_terminal_resources,
        )
        for rows in (
            (("directory", "stage"), ("shared-lock", "stage")),
            (("unknown", "stage"),), (("directory", "../stage"),),
            (("atomic-temporary", "stage/prefix"),), ((True, "stage"),),
            (("atomic-temporary", "stage/.asset-manifest-write-"), ("shared-lock", "stage/.asset-manifest-write-abcdefgh")),
            (("shared-lock", "stage/.asset-manifest-write-abcdefgh"), ("atomic-temporary", "stage/.asset-manifest-write-")),
            (("pid-temporary", "stage/result"), ("temporary", "stage/result.123.tmp")),
            (("temporary", "stage/result.123.tmp"), ("pid-temporary", "stage/result")),
            (("temporary", "stage/file"), ("shared-lock", "stage/file/child")),
            (("shared-lock", "stage/file/child"), ("temporary", "stage/file")),
        ):
            with self.subTest(rows=rows):
                with self.assertRaises(MakeProbeError):
                    resource_plan(rows)
        for rows, outputs, sources in (
            ((("atomic-temporary", "stage/.asset-manifest-write-"),), (), ("stage/.asset-manifest-write-abcdefgh",)),
            ((("pid-temporary", "stage/result"),), (), ("stage/result.123.tmp",)),
            ((("temporary", "stage/tmp"),), ("stage/tmp",), ()),
            ((("directory", "stage"),), (), ("stage/immutable",)),
            ((("shared-lock", "stage/lock"),), ("stage/lock",), ()),
            ((("directory", "stage/child"),), ("stage",), ()),
            ((("temporary", "stage/tmp"),), ("stage",), ()),
            ((("shared-lock", "stage/lock"),), ("stage",), ()),
            ((("pid-temporary", "stage/result"),), ("stage",), ()),
            ((("atomic-temporary", "stage/.asset-manifest-write-"),), ("stage",), ()),
        ):
            with self.subTest(rows=rows):
                with self.assertRaises(MakeProbeError):
                    validate_resource_scope(rows, outputs, iter(sources))
        validate_resource_scope(
            (("pid-temporary", "stage/result"), ("atomic-temporary", "stage/.asset-manifest-write-")),
            ("stage/result",), iter(("other/source",)),
        )
        for pattern, concrete in (
            (("pid-temporary", "stage/result"), "stage/result.123.tmp"),
            (("atomic-temporary", "stage/.asset-manifest-write-"), "stage/.asset-manifest-write-abcdefgh"),
        ):
            for kind, suffix in (
                ("directory", "/child"), ("temporary", "/child"), ("shared-lock", "/child"),
                ("pid-temporary", "/child"), ("atomic-temporary", "/.asset-manifest-write-"),
            ):
                child = (kind, concrete + suffix)
                for rows in ((pattern, child), (child, pattern)):
                    with self.subTest(file_ancestor=rows):
                        with self.assertRaises(MakeProbeError):
                            resource_plan(rows)
            for outputs, sources in (((concrete + "/child",), ()), ((), (concrete + "/child",))):
                with self.subTest(pattern=pattern, outputs=outputs, sources=sources):
                    with self.assertRaises(MakeProbeError):
                        validate_resource_scope((pattern,), outputs, iter(sources))
        self.assertEqual(
            resource_plan((("directory", "stage"), ("temporary", "stage/child"))),
            (("directory", "stage"), ("temporary", "stage/child")),
        )
        for kind, name, allowed in (
            ("directory", "stage", {"mkdir", "rmdir"}),
            ("shared-lock", "stage/lock", {"open", "lock"}),
            ("temporary", "stage/tmp", {"open", "write", "mode", "replace", "remove"}),
            ("pid-temporary", "stage/result", {"open", "write", "mode", "replace", "remove"}),
            ("atomic-temporary", "stage/.asset-manifest-write-", {"open", "write", "mode", "replace", "remove"}),
        ):
            path = "/repo/" + name
            if kind == "pid-temporary":
                path += ".123.tmp"
            elif kind == "atomic-temporary":
                path += "abcdefgh"
            for operation in ("open", "write", "mode", "replace", "remove", "mkdir", "rmdir", "lock"):
                self.assertEqual(resource_operation(((kind, name),), path, 123, (), operation), operation in allowed)
            with self.subTest(source_role=kind):
                with self.assertRaises(MakeProbeError):
                    require_retained_source(path.removeprefix("/repo/"), ("stage/result",))
        require_retained_source("stage/result", ("stage/result",))
        validate_terminal_resources(("stage/result",), (), ("/repo/stage/result",))
        validate_terminal_resources(
            ("stage/result",), (("directory", "stage"), ("shared-lock", "stage/lock")),
            ("/repo/stage/result", "/repo/stage/lock"),
        )
        for role, name in (
            ("temporary", "stage/tmp"), ("pid-temporary", "stage/result"),
            ("atomic-temporary", "stage/.asset-manifest-write-"),
        ):
            concrete = name + (".123.tmp" if role == "pid-temporary" else "abcdefgh" if role == "atomic-temporary" else "")
            with self.subTest(terminal_role=role):
                with self.assertRaisesRegex(MakeProbeError, "unretired temporary"):
                    validate_terminal_resources(
                        ("stage/result",), ((role, name),), ("/repo/stage/result", "/repo/" + concrete),
                    )
        with self.assertRaisesRegex(MakeProbeError, "retained"):
            validate_terminal_resources(("stage/result",), (), ())

    def test_native_terminal_roles_refuse_live_temporary_objects(self):
        self.add("native.c", (
            "#define _POSIX_C_SOURCE 200809L\n#include <fcntl.h>\n#include <unistd.h>\n"
            "#include <sys/stat.h>\n#include <stdio.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){int fd;char path[128];if(argc!=2&&argc!=3)return 1;"
            "if(mkdir(\"/repo/stage\",0700))return 2;"
            "fd=open(\"/repo/stage/result\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"final\",5)!=5||close(fd))return 3;"
            "if(!strcmp(argv[1],\"pid\"))snprintf(path,sizeof(path),\"/repo/stage/result.%ld.tmp\",(long)getpid());"
            "else snprintf(path,sizeof(path),\"/repo/stage/%s\","
            "!strcmp(argv[1],\"atomic\")?\".asset-manifest-write-abcdefgh\":\"tmp\");"
            "fd=open(path,O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"VALUE := ignored\\n\",17)!=17||close(fd))return 4;"
            "if(argc==3)printf(\"%s\\n\",path+6);return 0;}\n"
        ))
        resources = (
            ("directory", "stage"), ("temporary", "stage/tmp"),
            ("pid-temporary", "stage/result"), ("atomic-temporary", "stage/.asset-manifest-write-"),
        )
        for mode in ("exact", "pid", "atomic"):
            for source in (False, True):
                with self.subTest(mode=mode, source=source):
                    self.add("Makefile", (
                        "SOURCE := $(shell /native/tool " + mode + " source)\n-include $(SOURCE)\nall:\n\t@:\n"
                        if source else "all:\n\t@/native/tool " + mode + "\n"
                    ))
                    session = self.session()
                    with session:
                        tool = session.compile_native(("native.c",))
                        class Commands:
                            def __getitem__(self, argv):
                                return Command(argv, native_tool=tool, outputs=("stage/result",), native_resources=resources)
                        with self.assertRaisesRegex(
                            MakeProbeError, "resource role cannot become a generated source" if source else "unretired temporary",
                        ):
                            session._native_make_writable(
                                "all", outputs=("stage/result",), native_resources=resources,
                                native_tool=tool, commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                            )
                    self.assert_clean(session)

    def test_native_shared_lock_failed_descriptors_bind_later_actor_and_descendant(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sys/stat.h>\n#include <sys/file.h>\n#include <sys/wait.h>\n"
            "#include <sys/syscall.h>\n#include <fcntl.h>\n#include <unistd.h>\n#include <errno.h>\n#include <string.h>\n"
            "static int failures(int fd){int slots[128],count=0,copy;char *next[]={\"/native/tool\",\"next\",0};"
            "while((copy=open(\"/dev/null\",O_RDONLY))>=0){if(count==128)return 20;slots[count++]=copy;}"
            "if(errno!=EMFILE||dup(fd)!=-1||errno!=EMFILE)return 21;"
            "while(count)if(close(slots[--count]))return 22;"
            "if(dup2(fd,-1)!=-1||errno!=EBADF)return 23;"
            "if(dup3(fd,fd,O_CLOEXEC)!=-1||errno!=EINVAL)return 24;"
            "if(fcntl(fd,F_DUPFD,-1)!=-1||errno!=EINVAL)return 25;"
            "if(fcntl(fd,F_DUPFD_CLOEXEC,-1)!=-1||errno!=EINVAL)return 26;"
            "copy=open(\"/dev/null\",O_RDONLY);if(copy<0)return 27;"
            "if(syscall(SYS_dup3,copy,fd,~O_CLOEXEC)!=-1||errno!=EINVAL||close(copy))return 27;"
            "if(syscall(SYS_execve,next[0],next,(char **)1)!=-1||errno!=EFAULT)return 28;"
            "return fcntl(fd,F_GETFD)==FD_CLOEXEC?0:29;}"
            "int main(int argc,char **argv){int fd,out,status,result;pid_t child;"
            "if(argc!=2)return 1;if(!strcmp(argv[1],\"seed\")){"
            "if(mkdir(\"/repo/stage\",0700))return 2;"
            "fd=open(\"/repo/stage/lock\",O_CREAT|O_RDWR|O_CLOEXEC|O_NOFOLLOW,0600);"
            "out=open(\"/repo/stage/result\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||out<0||write(out,\"final\",5)!=5||close(out)||close(fd))return 3;return 0;}"
            "fd=open(\"/repo/stage/lock\",O_RDWR|O_CLOEXEC|O_NOFOLLOW);"
            "if(fd<0||flock(fd,LOCK_EX))return 4;result=failures(fd);if(result)return result;"
            "child=fork();if(child<0)return 5;if(!child)_exit(failures(fd));"
            "if(waitpid(child,&status,0)!=child||status||close(fd))return 6;return 0;}\n"
        ))
        self.add("Makefile", "all: second\nfirst:\n\t@/native/tool seed\nsecond: first\n\t@/native/tool later\n")
        resources = (("directory", "stage"), ("shared-lock", "stage/lock"))
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool, outputs=("stage/result",), native_resources=resources)
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("stage/result",), native_resources=resources,
                native_tool=tool, commands=Commands(), observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
            self.assertEqual([(row.data, row.mode) for row in generated], [(b"final", 0o644)])
            trace = observed["read_trace"]
            failures = [
                row for row in trace["machine"]["events"]
                if row["kind"] == "native-output" and row["event"]["kind"] == "output-operation-failed"
            ]
            self.assertEqual(len(failures), 14)
            self.assertEqual({row["dispatch"] for row in failures}, {2})
            self.assertEqual({row["event"]["owner"] for row in failures}, {1})
            pids = {row["event"]["pid"] for row in failures}
            self.assertEqual(len(pids), 2)
            for pid in pids:
                effects = [row["event"] for row in failures if row["event"]["pid"] == pid]
                self.assertEqual(
                    [(row["operation"], row.get("duplicate_kind"), row["result"]) for row in effects],
                    [("dup", "dup", -errno.EMFILE), ("dup", "dup2", -errno.EBADF),
                     ("dup", "dup3", -errno.EINVAL), ("dup", "fcntl-dupfd", -errno.EINVAL),
                     ("dup", "fcntl-dupfd-cloexec", -errno.EINVAL),
                     ("duplicate-release", None, -errno.EINVAL), ("exec", None, -errno.EFAULT)],
                )
            for operation in ("dup", "duplicate-release", "exec"):
                invalid = json.loads(json.dumps(trace))
                row = next(
                    row for row in invalid["machine"]["events"]
                    if row["kind"] == "native-output" and row["event"].get("operation") == operation
                )
                row["event"]["owner"] = 2
                row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                with self.subTest(foreign_producer=operation):
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
        self.assert_clean(session)

    def test_native_generated_optional_source_failed_open_retires_its_actual_entry_pin(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _POSIX_C_SOURCE 200809L\n#include <fcntl.h>\n#include <unistd.h>\n#include <sys/stat.h>\n"
            "#include <stdio.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){int fd;if(argc!=2)return 2;"
            "if(!strcmp(argv[1],\"replace\")){"
            "fd=open(\"/repo/stage/tmp\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"VALUE := final\\n\",15)!=15||close(fd))return 3;"
            "return rename(\"/repo/stage/tmp\",\"/repo/stage/generated.mk\");}"
            "if(mkdir(\"/repo/stage\",0700))return 2;"
            "fd=open(\"/repo/stage/generated.mk\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"VALUE := unreachable\\n\",21)!=21||fchmod(fd,0)||close(fd))return 1;"
            "return 0;}\n"
        ))
        self.add("Makefile", "GENERATED := $(shell /native/tool seed)\n-include stage/generated.mk\nall:\n\t@/native/tool replace\n")
        resources = (("directory", "stage"), ("temporary", "stage/tmp"))
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool, outputs=("stage/generated.mk",), native_resources=resources)
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("stage/generated.mk",), native_resources=resources, native_tool=tool, commands=Commands(),
                observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
            self.assertEqual([(row.data, row.mode) for row in generated], [(b"VALUE := final\n", 0o644)])
            trace = observed["read_trace"]
            failed = [
                row for row in trace["events"] if row["kind"] == "source-open"
                and row["result"] == -errno.EACCES
            ]
            self.assertEqual(len(failed), 1)
            self.assertIsNone(failed[0]["source"])
            self.assertEqual(failed[0]["path"], "stage/generated.mk")
            self.assertEqual(failed[0]["custody"]["kind"], "native-output")
            entry = trace["machine"]["events"][failed[0]["custody"]["entry"] - 1]
            retired = [
                row for row in trace["machine"]["events"]
                if row["kind"] == "pin-retired" and row["visit"] == entry["visit"]
            ]
            self.assertEqual(len(retired), 1)
            self.assertIsNone(retired[0]["source"])
            self.assertEqual(retired[0]["identity"], entry["identity"])
            read_epochs.validate_trace(trace, trace["scope"], count_limit=100000, file_limit=10000000)
            malformed_entries = [0, [], {"kind": "other"}]
            for field in ("visit", "trace_seq", "path"):
                malformed = dict(entry)
                del malformed[field]
                malformed_entries.append(malformed)
            for sequence in (None, "1", [], True):
                malformed_entries.append(dict(entry, trace_seq=sequence))
            for malformed in malformed_entries:
                invalid = json.loads(json.dumps(trace))
                invalid["machine"]["events"][failed[0]["custody"]["entry"] - 1] = malformed
                with self.subTest(failed_entry=malformed):
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            temporary_source = json.loads(json.dumps(trace))
            temporary_source["output_authority"]["paths"] = ["stage/required-other"]
            role = ["temporary", "stage/generated.mk"]
            temporary_source["output_authority"]["resources"].append(role)
            for job in temporary_source["output_authority"]["jobs"]:
                job["admission"]["outputs"] = ["stage/required-other"]
                job["admission"]["resources"].append(role)
                admission = job["admission"]
                admission["owner"] = native_command_owner(
                    admission["closure"], admission["outputs"], admission["resources"],
                )
                for image in job["tree"]:
                    if image["kind"] == "exec":
                        image["admission"] = json.loads(json.dumps(admission))
                for event in temporary_source["machine"]["events"]:
                    if event["kind"] == "execute" and not event["make"] and event["dispatch"] == job["sequence"]:
                        event["admission_owner"] = admission["owner"]
                    elif event["kind"] == "native-tree" and event["dispatch"] == job["sequence"] and event["event"]["kind"] == "exec":
                        event["event"]["admission"] = json.loads(json.dumps(admission))
                        event["sha256"] = hashlib.sha256(encoded(event["event"])).hexdigest()
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "resource role cannot become a generated source"):
                read_epochs.validate_native_output_authority(
                    temporary_source, count_limit=100000, file_limit=10000000, reserve=lambda size: None,
                )
            for field, value in (
                ("source", 1), ("identity", entry["identity"][:-1] + [0]),
                ("visit", 999), ("pid", 999),
            ):
                invalid = json.loads(json.dumps(trace))
                row = next(
                    row for row in invalid["machine"]["events"]
                    if row["kind"] == "pin-retired" and row["visit"] == entry["visit"]
                )
                row[field] = value
                with self.subTest(retired_field=field):
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
            for custody in (None, {"kind": "snapshot"}, {"kind": "native-output", "entry": 1}):
                invalid = json.loads(json.dumps(trace))
                row = next(row for row in invalid["events"] if row["seq"] == failed[0]["seq"])
                row["custody"] = custody
                with self.subTest(failed_custody=custody):
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
            invalid = json.loads(json.dumps(trace))
            rows = invalid["machine"]["events"]
            rows[:] = [row for row in rows if not (row["kind"] == "pin-retired" and row["visit"] == entry["visit"])]
            for sequence, row in enumerate(rows, 1):
                row["seq"] = sequence
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "pin retirement"):
                read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
        self.assert_clean(session)

    def test_native_writer_settlement_binds_last_close_exec_duplicate_fork_and_death(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sys/stat.h>\n#include <sys/wait.h>\n"
            "#include <fcntl.h>\n#include <unistd.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){int fd,other,copy,status;pid_t child;"
            "char *next[]={\"/native/tool\",\"finish\",0};"
            "if(argc!=2)return 1;if(!strcmp(argv[1],\"finish\"))return 0;"
            "if(mkdir(\"/repo/stage\",0700))return 2;"
            "fd=open(\"/repo/stage/result\",O_CREAT|O_EXCL|O_WRONLY|O_CLOEXEC,0644);"
            "if(fd<0||write(fd,\"final\",5)!=5)return 3;"
            "other=open(\"/repo/stage/result\",O_RDONLY);if(other<0||close(other))return 4;"
            "if(!strcmp(argv[1],\"close\")){if(close(fd))return 5;}"
            "else if(!strcmp(argv[1],\"dup\")){copy=dup(fd);if(copy<0||close(fd)||close(copy))return 6;}"
            "else if(!strcmp(argv[1],\"fork\")){child=fork();if(child<0)return 7;"
            "if(!child){if(close(fd))_exit(8);_exit(0);}"
            "if(waitpid(child,&status,0)!=child||status||close(fd))return 9;}"
            "else if(!strcmp(argv[1],\"replace\")){copy=open(\"/dev/null\",O_RDONLY);"
            "if(copy<0||dup2(copy,fd)!=fd||close(copy)||close(fd))return 10;}"
            "else if(!strcmp(argv[1],\"exec\")){execv(next[0],next);return 11;}"
            "return 0;}\n"
        ))
        resources = (("directory", "stage"),)
        for mode, closing in (
            ("close", "output-close"), ("dup", "output-close"), ("fork", "output-close"),
            ("replace", "output-duplicate-release"), ("exec", "output-exec-close"), ("death", "output-close"),
        ):
            with self.subTest(mode=mode):
                self.add("Makefile", "all:\n\t@/native/tool " + mode + "\n")
                session = self.session()
                with session:
                    tool = session.compile_native(("native.c",))
                    class Commands:
                        def __getitem__(self, argv):
                            return Command(argv, native_tool=tool, outputs=("stage/result",), native_resources=resources)
                    completed, _, observed, generated = session._native_make_writable(
                        "all", outputs=("stage/result",), native_resources=resources,
                        native_tool=tool, commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                    )
                    self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
                    self.assertEqual([(row.data, row.mode) for row in generated], [(b"final", 0o644)])
                    trace = observed["read_trace"]
                    effects = read_epochs.native_output_effects(trace)
                    settlement, = [row for row in effects if row["kind"] == "output-settled"]
                    self.assertEqual(effects[effects.index(settlement) + 1]["kind"], closing)
                    invalid = json.loads(json.dumps(trace))
                    rows = invalid["machine"]["events"]
                    moved = next(row for row in rows if row["kind"] == "native-output" and row["event"]["kind"] == "output-settled")
                    rows.remove(moved)
                    earlier_close = next(
                        index for index, row in enumerate(rows)
                        if row["kind"] == "native-output" and row["event"]["kind"] == "output-close"
                    )
                    rows.insert(earlier_close, moved)
                    number = 0
                    for sequence, row in enumerate(rows, 1):
                        row["seq"] = sequence
                        if row["kind"] == "native-output":
                            number += 1
                            row["event"]["sequence"] = number
                            row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                    with self.assertRaisesRegex(read_epochs.ReadEpochError, "settlement"):
                        read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
                    if mode == "close":
                        for error in (errno.EINTR, errno.EIO, errno.ENOSPC, errno.EDQUOT):
                            controlled = json.loads(json.dumps(trace))
                            row = next(
                                row for row in controlled["machine"]["events"]
                                if row["kind"] == "native-output" and row["event"]["sequence"] == settlement["sequence"] + 1
                            )
                            row["event"].update(kind="output-close-failed", result=-error)
                            row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                            read_epochs.validate_trace(controlled, controlled["scope"], count_limit=100000, file_limit=10000000)
                self.assert_clean(session)

    def test_native_shared_lock_release_binds_close_exec_duplicate_fork_and_death(self):
        from scripts.validation_ownership import read_epochs
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sys/stat.h>\n#include <sys/file.h>\n#include <sys/wait.h>\n"
            "#include <fcntl.h>\n#include <unistd.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){int fd,out,copy,status;pid_t child;"
            "char *next[]={\"/native/tool\",\"finish\",0};"
            "if(argc!=2)return 1;if(!strcmp(argv[1],\"finish\"))return 0;"
            "if(mkdir(\"/repo/stage\",0700))return 2;"
            "fd=open(\"/repo/stage/lock\",O_CREAT|O_RDWR|O_CLOEXEC|O_NOFOLLOW,0600);"
            "if(fd<0||flock(fd,LOCK_EX))return 3;"
            "out=open(\"/repo/stage/result\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(out<0||write(out,\"final\",5)!=5||close(out))return 4;"
            "if(!strcmp(argv[1],\"unlock\")){if(flock(fd,LOCK_UN)||close(fd))return 5;}"
            "else if(!strcmp(argv[1],\"dup\")){copy=dup(fd);if(copy<0||close(fd)||close(copy))return 6;}"
            "else if(!strcmp(argv[1],\"fork\")){child=fork();if(child<0)return 7;"
            "if(!child){if(close(fd))_exit(8);_exit(0);}"
            "if(waitpid(child,&status,0)!=child||status||close(fd))return 9;}"
            "else if(!strcmp(argv[1],\"replace\")){copy=open(\"/dev/null\",O_RDONLY);"
            "if(copy<0||dup2(copy,fd)!=fd||close(copy)||close(fd))return 10;}"
            "else if(!strcmp(argv[1],\"exec\")){execv(next[0],next);return 11;}"
            "return 0;}\n"
        ))
        resources = (("directory", "stage"), ("shared-lock", "stage/lock"))
        for mode, closing in (
            ("unlock", "output-close"), ("dup", "output-close"), ("fork", "output-close"),
            ("replace", "output-duplicate-release"), ("exec", "output-exec-close"), ("death", "output-close"),
        ):
            with self.subTest(mode=mode):
                self.add("Makefile", "all:\n\t@/native/tool " + mode + "\n")
                session = self.session()
                with session:
                    tool = session.compile_native(("native.c",))
                    class Commands:
                        def __getitem__(self, argv):
                            return Command(argv, native_tool=tool, outputs=("stage/result",), native_resources=resources)
                    completed, _, observed, generated = session._native_make_writable(
                        "all", outputs=("stage/result",), native_resources=resources,
                        native_tool=tool, commands=Commands(), observe_reads=True,
                        observe_runtime_completions=True,
                    )
                    self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
                    self.assertEqual([(row.data, row.mode) for row in generated], [(b"final", 0o644)])
                    trace = observed["read_trace"]
                    effects = read_epochs.native_output_effects(trace)
                    releases = [row for row in effects if row["kind"] == "output-lock-release"]
                    self.assertEqual(len(releases), 0 if mode == "unlock" else 1)
                    if releases:
                        index = effects.index(releases[0])
                        self.assertEqual(effects[index + 1]["kind"], closing)
                        self.assertEqual(effects[index + 1]["description"], releases[0]["description"])
                        invalid = json.loads(json.dumps(trace))
                        rows = invalid["machine"]["events"]
                        rows[:] = [row for row in rows if not (row["kind"] == "native-output" and row["event"]["kind"] == "output-lock-release")]
                        number = 0
                        for sequence, row in enumerate(rows, 1):
                            row["seq"] = sequence
                            if row["kind"] == "native-output":
                                number += 1
                                row["event"]["sequence"] = number
                                row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                        with self.assertRaisesRegex(read_epochs.ReadEpochError, "last close omitted its lock release"):
                            read_epochs.validate_trace(invalid, invalid["scope"], count_limit=100000, file_limit=10000000)
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
