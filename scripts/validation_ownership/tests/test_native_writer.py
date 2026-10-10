"""Original native job admission and regular-file writer cases."""

import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import MappingProxyType
import unittest
from unittest.mock import patch

from scripts.validation_ownership import make_probe
from scripts.validation_ownership.authority import ENVIRONMENT, encoded, native_command_owner, parse_json
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.tests import test_foundation as foundation


class NativeReadonlyVariableTests(unittest.TestCase):
    setUp = foundation.FoundationTests.setUp
    tearDown = foundation.FoundationTests.tearDown
    add = foundation.FoundationTests.add
    session = foundation.FoundationTests.session
    assert_clean = foundation.FoundationTests.assert_clean

    def test_native_runtime_compact_spans_preserve_source_and_reduce_real_observation_cost(self):
        self.add("Makefile", "".join(f"unused_{index}: ; @true\n" for index in range(600)) +
                 "SELECTED := value\nall: ; @v=owned; printf '%s' '$(SELECTED)'\n")
        costs = []
        for eager in (False, True):
            body = (
                "before=guard.read_epochs._statement_index\n"
                "def eager(data,**kwargs):\n"
                " kwargs['compact']=False\n"
                " return before(data,**kwargs)\n"
                "guard.read_epochs._statement_index=eager\n"
            ) if eager else ""
            session = self.session()
            with session, foundation.FoundationTests.native_supervisor(self, body):
                completed, semantics, observed = session._native_make_readonly(
                    "all", variables=("SELECTED",), observe_reads=True,
                    observe_runtime_completions=True,
                )
                self.assertEqual((completed.stdout, completed.stderr), (b"value", b""))
                self.assertEqual(semantics["domains"]["SELECTED"]["value"], "value")
                from scripts.validation_ownership import read_epochs
                archive = read_epochs.reconstruct_archive(observed["read_trace"], budget=session.budget)
                self.assertEqual(archive.passes[0].visits[0].source.data, session.snapshot.files["Makefile"])
                self.assertFalse(archive.passes[0].patterns)
                costs.append(observed["observation_bytes"])
            self.assert_clean(session)
        self.assertLess(costs[0] + (self.root / "Makefile").stat().st_size, costs[1])

    def test_statement_index_charges_actual_growth_without_per_row_tables(self):
        from scripts.validation_ownership import read_epochs
        source = b"FIRST := one\\\n two\n" + b"".join(
            ("V%d := value%d\n" % (index, index)).encode() for index in range(1000)
        )
        expected, table_bytes = {}, sys.getsizeof({})
        allocation_bytes = table_bytes
        for logical, first, last, raw in read_epochs.physical_statements(source):
            value = (logical, first, last, hashlib.sha256(raw.encode()).hexdigest())
            allocation_bytes += (
                sys.getsizeof(first) + sys.getsizeof(value)
                + sum(sys.getsizeof(item) for item in value)
            )
            expected[first] = value
            current_bytes = sys.getsizeof(expected)
            if current_bytes != table_bytes:
                allocation_bytes += current_bytes
                table_bytes = current_bytes
        allocation_bytes += sys.getsizeof(MappingProxyType(expected))
        charges = []
        observed = read_epochs._statement_index(source, reserve=charges.append)
        self.assertEqual(dict(observed), expected)
        self.assertEqual(observed[1][1:3], (1, 2))
        self.assertEqual(sum(charges), allocation_bytes)
        with self.assertRaises(TypeError):
            observed[1] = observed[1]
        for limit in (allocation_bytes, allocation_bytes - 1):
            budget = ProbeBudget(make_probe.Limits(control_bytes=limit))
            reserve = lambda size: budget.charge("control", size)
            if limit == allocation_bytes:
                self.assertEqual(
                    dict(read_epochs._statement_index(source, reserve=reserve)), expected,
                )
                self.assertEqual(budget.bytes["control"], allocation_bytes)
            else:
                with self.assertRaisesRegex(MakeProbeError, "control byte budget exhausted"):
                    read_epochs._statement_index(source, reserve=reserve)
                self.assertTrue(budget.failed)
        with self.assertRaisesRegex(read_epochs.ReadEpochError, "observation bound"):
            read_epochs._statement_index(source, count_limit=1)
        with self.assertRaisesRegex(read_epochs.ReadEpochError, "unsupported bytes"):
            read_epochs._statement_index(b"V := \0")

    def test_native_managed_python_ancestor_metadata_is_exact_and_metadata_only(self):
        runtime = "/usr/lib/python3/dist-packages"
        self.add("Makefile", (
            ".PHONY: all\nall:\n"
            "\t@test -d /usr/lib/python3 && printf '%s\\n' directory\n"
        ))
        session = self.session()
        with session:
            completed, _, observed = session._native_make_readonly(
                "all", native_runtime_directories=(runtime,),
            )
            self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, b"directory\n", b""))
            self.assertIn("/usr/lib/python3", observed["accessed"])
        self.assert_clean(session)

        for command, path in (
            ("read -r value < /usr/lib/python3", "/usr/lib/python3"),
            ("printf '%s\\n' /usr/lib/python3/*", "/usr/lib/python3"),
            ("test -d /usr/lib/python3-sibling", "/usr/lib/python3-sibling"),
            ("printf changed > /usr/lib/python3/forbidden", "/usr/lib/python3/forbidden"),
            ("test -d /usr/lib/python3/dist-packages/..", "/usr/lib/python3"),
        ):
            self.add("Makefile", ".PHONY: all\nall:\n\t@" + command + "\n")
            session = self.session()
            with self.subTest(command=command), session:
                with self.assertRaisesRegex(MakeProbeError, "denied|uncaptured") as error:
                    session._native_make_readonly("all", native_runtime_directories=(runtime,))
                self.assertIn(path, str(error.exception))
            self.assert_clean(session)

    def test_runtime_projection_reuses_checked_source_bytes_and_preserves_rejections(self):
        from scripts.validation_ownership import read_epochs
        self.add("Makefile", "# preserved source padding\n" * 5000 + "all:\n\t@v=recipe; printf once\n")
        session = self.session()
        with session:
            completed, _, observed = session._native_make_readonly(
                "all", observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual(completed.stdout, b"once")
            self.assertEqual(completed.returncode, 0)
            trace = observed["read_trace"]
            self.assertGreater(sum(row["bytes"] for row in trace["sources"]), 100 * 1024)
            charges = []
            decoder = read_epochs.base64.b64decode
            with patch.object(read_epochs.base64, "b64decode", wraps=decoder) as decode:
                read_epochs.validate_trace(
                    trace, trace["scope"], count_limit=100000, file_limit=10000000,
                    reserve=charges.append,
                )
            for row in trace["sources"]:
                self.assertEqual(
                    sum(call.args[0] == row["data"] for call in decode.call_args_list), 1,
                )
                self.assertEqual(charges.count(row["bytes"]), 1)
            for field, value in (
                ("mode", 0o1000), ("bytes", trace["sources"][0]["bytes"] - 1),
                ("sha256", "0" * 64), ("data", "!" + trace["sources"][0]["data"][1:]),
            ):
                with self.subTest(source_field=field):
                    invalid = json.loads(json.dumps(trace))
                    invalid["sources"][0][field] = value
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            invalid = json.loads(json.dumps(trace))
            complete = next(row for row in invalid["events"] if row["kind"] == "complete")
            complete["visits"] += 1
            with self.assertRaises(read_epochs.ReadEpochError):
                read_epochs.validate_trace(
                    invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                )
        self.assert_clean(session)


    def test_literal_reference_closure_preserves_gnu_global_and_target_values(self):
        self.add("Makefile", (
            "BASE := global\nB := short\nMID = ${BASE}\n"
            "TOP = $(MID)/$(MID)/$B/$(.missing-name)/$$literal\n"
            "all: BASE := target\n"
            ".PHONY: all\nall:\n\t@v=recipe; printf '%s\\n' '$(TOP)'\n"
        ))
        ordinary = subprocess.run(
            ("/usr/bin/make", "-rR", "--no-print-directory", "-f", "Makefile", "all"),
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        session = self.session()
        with session:
            completed, semantics, _ = session._native_make_readonly(
                "all", variables=("TOP", "MID", "BASE"),
            )
            self.assertEqual(completed.stdout, ordinary.stdout)
            self.assertEqual(completed.stdout, b"target/target/short//$literal\n")
            self.assertEqual(completed.stderr, ordinary.stderr)
            self.assertEqual(semantics["domains"]["TOP"]["value"], "global/global/short//$literal")
            target = next(row for row in semantics["files"] if row["target"] == "all")
            self.assertEqual(target["variables"]["TOP"]["value"], "target/target/short//$literal")
            self.assertEqual(target["variables"]["MID"]["value"], "target")
            self.assertEqual(semantics["domains"]["TOP"]["flavor"], "recursive")
        self.assert_clean(session)

    def test_reference_closure_rejects_hidden_effects_and_cycles_before_expansion(self):
        cases = (
            "TOP = $(SAFE)$(HIDDEN)\nSAFE = $(BASE)\nBASE := safe\n"
            "HIDDEN = $(info observer-only)\n",
            "TOP = $(MIDDLE)\nMIDDLE = $(HIDDEN)\nHIDDEN = $(shell printf observer-only)\n",
            "TOP = $($(SELECTOR))\nSELECTOR := HIDDEN\nHIDDEN = $(info observer-only)\n",
            "TOP = $(BASE:x=y)\nBASE := x\n",
            "TOP = $(TOP)\n",
            "TOP = $(MIDDLE)\nMIDDLE = ${TOP}\n",
        )
        for source in cases:
            with self.subTest(source=source):
                self.add("Makefile", source + ".PHONY: all\nall:\n\t@v=recipe; printf '%s\\n' recipe\n")
                session = self.session()
                with session:
                    original = session._sandbox_run
                    completed = {}
                    def capture(*args, **kwargs):
                        result, observed = original(*args, **kwargs)
                        completed["result"] = result
                        return result, observed
                    with patch.object(session, "_sandbox_run", side_effect=capture):
                        with self.assertRaisesRegex(
                            MakeProbeError, "unsupported readonly native recursive observation",
                        ):
                            session._native_make_readonly("all", variables=("TOP",))
                    self.assertEqual(completed["result"].stdout, b"recipe\n")
                    self.assertNotIn(b"observer-only", completed["result"].stderr)
                self.assert_clean(session)

    def test_reference_closure_checks_inherited_append_at_referenced_binding(self):
        self.add("Makefile", (
            "TOP = $(HIDDEN)\nHIDDEN = $(info observer-only)parent\n"
            "all: HIDDEN += tail\n"
            ".PHONY: all\nall:\n\t@v=recipe; printf '%s\\n' recipe\n"
        ))
        session = self.session()
        with session:
            with self.assertRaisesRegex(
                MakeProbeError, "unsupported readonly native append observation",
            ):
                session._native_make_readonly("all", variables=("TOP",))
        self.assert_clean(session)


class NativeRuntimeMetadataTests(unittest.TestCase):
    setUp = foundation.FoundationTests.setUp
    tearDown = foundation.FoundationTests.tearDown
    add = foundation.FoundationTests.add
    session = foundation.FoundationTests.session
    assert_clean = foundation.FoundationTests.assert_clean
    paths = ("/sys/fs/selinux", "/selinux", "/usr/share/locale")
    git_roots = ("/.git", "/HEAD")

    def test_native_optional_maps_probe_observes_actual_guest_omission(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <errno.h>\n"
            "#include <sys/stat.h>\n#include <stdio.h>\n"
            "int main(void){struct stat info;int rc,error,fd,open_error;"
            "rc=lstat(\"/proc/self/maps\",&info);error=errno;"
            "fd=open(\"/proc/self/maps\",O_RDONLY);open_error=errno;"
            "printf(\"{\\\"stat\\\":%d,\\\"stat_errno\\\":%d,\\\"open\\\":%d,\\\"open_errno\\\":%d}\\n\","
            "rc,error,fd,open_error);return 0;}\n"
        ))
        self.add("Makefile", "all: ; @/native/tool\n")
        for runtime_files in ((), ("/proc/mounts",)):
            with self.subTest(runtime_files=runtime_files):
                session = self.session(runtime_files=runtime_files)
                with session:
                    tool = session.compile_native(("native.c",))
                    completed, _, observed = session._native_make_readonly(
                        "all", native_tool=tool, observe_reads=True, observe_runtime_completions=True,
                    )
                    self.assertEqual(json.loads(completed.stdout), {
                        "stat": -1, "stat_errno": errno.ENOENT,
                        "open": -1, "open_errno": errno.ENOENT,
                    })
                    self.assertEqual(completed.stderr, b"")
                    self.assertFalse(any(path.endswith("/maps") for path in observed["accessed"]))
                self.assert_clean(session)

    def test_native_optional_maps_probe_refuses_writes_and_other_spellings(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){if(argc!=3)return 1;"
            "open(argv[2],!strcmp(argv[1],\"write\")?O_WRONLY|O_CREAT:O_RDONLY,0600);return 0;}\n"
        ))
        operations = [("read", path) for path in (
            "/proc/self", "/proc/self/maps/child", "/proc/self/maps-neighbor",
            "/proc/self/../self/maps", "/proc/1/maps", "/proc/thread-self/maps",
        )] + [("write", "/proc/self/maps")]
        for operation, path in operations:
            with self.subTest(operation=operation, path=path):
                self.add("Makefile", "all: ; @/native/tool " + operation + " " + path + "\n")
                session = self.session(runtime_files=("/proc/mounts",))
                with session:
                    tool = session.compile_native(("native.c",))
                    with self.assertRaises(MakeProbeError):
                        session._native_make_readonly(
                            "all", native_tool=tool, observe_reads=True, observe_runtime_completions=True,
                        )
                self.assert_clean(session)

    def test_native_optional_maps_omission_requires_absent_prepared_leaf_and_native_role(self):
        from scripts.validation_ownership.syscall_guard import Process, Violation
        policy = foundation.FoundationTests.observation_policy(self, mode="make", count=100)
        policy.native_readonly = True
        policy.config["forbidden_paths"] = []
        policy.config["runtime_aliases"] = ["/proc/self"]
        directory = self.directory / "proc/7"
        directory.mkdir(parents=True)
        (self.directory / "proc/self").symlink_to("7")
        state = Process("native", path_context=("/proc/self/maps", -100, None))
        self.assertTrue(policy.native_maps_omission(state))
        path = policy.resolve("/proc/self/maps", native_maps_omission=True)
        self.assertEqual(path, "/proc/7/maps")
        policy.make_runtime_access(state, path, "read")
        leaf = directory / "maps"
        for kind in ("regular", "directory", "symlink"):
            with self.subTest(kind=kind):
                if kind == "regular":
                    leaf.write_bytes(b"must-not-be-read")
                elif kind == "directory":
                    leaf.mkdir()
                else:
                    leaf.symlink_to("/proc/8/maps")
                with patch.object(Path, "read_bytes", side_effect=AssertionError("maps payload read")):
                    with self.assertRaisesRegex(Violation, "genuinely omitted guest leaf"):
                        policy.resolve("/proc/self/maps", native_maps_omission=True)
                leaf.rmdir() if kind == "directory" else leaf.unlink()
        for role in ("make", "compiler", "command", "helper"):
            with self.subTest(role=role):
                actor = Process(role, path_context=state.path_context, observer_ready=True)
                self.assertFalse(policy.native_maps_omission(actor))
                with self.assertRaises(Violation):
                    policy.make_runtime_access(actor, path, "read")
        policy.native_readonly = False
        self.assertFalse(policy.native_maps_omission(state))
        policy.mode = "command"
        policy.native_readonly = True
        self.assertFalse(policy.native_maps_omission(state))

    def test_native_runtime_metadata_preserves_actual_type_and_absence(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <errno.h>\n#include <stdio.h>\n#include <sys/vfs.h>\n"
            "int main(int argc,char **argv){struct statfs info;int rc;if(argc!=2)return 7;"
            "rc=statfs(argv[1],&info);if(rc)printf(\"error:%d\\n\",errno);"
            "else printf(\"type:%lx;size:%ld;readonly:%d\\n\",(unsigned long)info.f_type,"
            "(long)info.f_bsize,!!(info.f_flags&1));return 0;}\n"
        ))
        for path in self.paths:
            self.add("Makefile", "all: ; @/native/tool " + path + "\n")
            session = self.session()
            with self.subTest(path=path), session:
                tool = session.compile_native(("native.c",))
                ordinary = subprocess.run(
                    (str(tool.path), path), cwd=self.root, env=ENVIRONMENT,
                    capture_output=True, timeout=10, check=True,
                )
                completed, _, observed = session._native_make_readonly(
                    "all", native_tool=tool, native_metadata_directories=(path,),
                    observe_reads=True, observe_runtime_completions=True,
                )
                if Path(path).exists():
                    self.assertEqual(completed.stdout.split(b";")[:2], ordinary.stdout.split(b";")[:2])
                    self.assertEqual(completed.stdout.split(b";")[2], b"readonly:1\n")
                    self.assertIn(path, observed["accessed"])
                else:
                    self.assertEqual(completed.stdout, ordinary.stdout)
                    self.assertEqual(completed.stdout, f"error:{errno.ENOENT}\n".encode())
                record, = [row for row in observed["metadata"] if row[0] == 137 and row[1] == path]
                self.assertEqual(record[2:6], (0, 0, 120, 0))
                self.assertEqual(record[6], 0 if Path(path).exists() else -errno.ENOENT)
            self.assert_clean(session)

    def test_native_runtime_metadata_has_no_content_or_descendant_authority(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <dirent.h>\n#include <fcntl.h>\n#include <string.h>\n"
            "#include <sys/vfs.h>\n#include <unistd.h>\n#include <stdio.h>\n"
            "int main(int argc,char **argv){struct statfs info;char path[512];if(argc!=3)return 7;"
            "if(!strcmp(argv[1],\"read\"))open(argv[2],O_RDONLY);"
            "else if(!strcmp(argv[1],\"list\"))opendir(argv[2]);"
            "else if(!strcmp(argv[1],\"write\")){snprintf(path,sizeof(path),\"%s/test\",argv[2]);"
            "open(path,O_WRONLY|O_CREAT,0600);}else statfs(argv[2],&info);return 0;}\n"
        ))
        operations = [
            (operation, path, message) for path in self.paths for operation, message in (
                ("read", "metadata-only runtime operation denied"),
                ("list", "metadata-only runtime operation denied"),
                ("write", "filesystem write denied"),
            )
        ] + [
            ("metadata", path, "uncaptured Make runtime access") for path in (
                "/sys/fs/selinux/child", "/sys/fs/other", "/selinux/child",
                "/usr/share/locale/child", "/usr/share/locales",
            )
        ]
        for operation, path, message in operations:
            self.add("Makefile", "all: ; @/native/tool " + operation + " " + path + "\n")
            session = self.session()
            with self.subTest(operation=operation, path=path):
                with self.assertRaisesRegex(MakeProbeError, message), session:
                    tool = session.compile_native(("native.c",))
                    session._native_make_readonly(
                        "all", native_tool=tool, native_metadata_directories=self.paths,
                    )
                self.assertTrue(session.budget.failed)
                self.assert_clean(session)

    def test_native_runtime_metadata_declarations_keep_exact_trust_and_identity(self):
        from scripts.validation_ownership import make_probe
        self.add("Makefile", "all: ; @:\n")
        for declarations in (
            ["/sys/fs/selinux"], ("/sys",), ("/sys/fs/selinux/child",),
            ("/sys/fs/selinux", "/sys/fs/selinux"), (True,), ("/selinux/../selinux",),
            ("/usr/share",), ("/usr/share/locale/child",), ("/usr/share/locales",),
            ("/usr/share/locale", "/usr/share/locale"), ("/usr/share/locale/../locale",),
        ):
            session = self.session()
            with self.subTest(declarations=declarations):
                with self.assertRaisesRegex(MakeProbeError, "metadata directory"), session:
                    session._native_make_readonly("all", native_metadata_directories=declarations)
                self.assert_clean(session)
        original_stat, original_resolve = Path.lstat, Path.resolve
        fixture = list(self.root.stat())
        fixture[4] = fixture[5] = 0
        fixture[0] = stat.S_IFDIR | 0o555
        capture = make_probe._native_metadata_directory
        for name in self.paths:
            path = Path(name)
            for field, value in ((4, os.getuid() + 1), (0, stat.S_IFDIR | 0o777), (0, stat.S_IFREG | 0o444)):
                def changed(source, *args, **kwargs):
                    if source != path:
                        return original_stat(source, *args, **kwargs)
                    fields = fixture.copy()
                    fields[field] = value
                    return os.stat_result(fields)
                with self.subTest(path=name, field=field), patch.object(Path, "lstat", changed):
                    with self.assertRaisesRegex(MakeProbeError, "mutable/untrusted") as rejected:
                        capture(name, ProbeBudget())
                    self.assertIn(name + " uid=", str(rejected.exception))
            def redirected(source, *args, **kwargs):
                return path.parent if source == path else original_resolve(source, *args, **kwargs)
            with self.subTest(path=name, alias=True), patch.object(Path, "resolve", redirected):
                with self.assertRaisesRegex(MakeProbeError, "canonical"):
                    capture(name, ProbeBudget())
            calls = 0
            def replaced(name, budget):
                nonlocal calls
                row = capture(name, budget)
                calls += 1
                if calls == 2:
                    if row[1] is None:
                        return row[0], (0, 1, stat.S_IFDIR | 0o555, 0, 0)
                    identity = list(row[1])
                    identity[1] += 1
                    return row[0], tuple(identity)
                return row
            session = self.session()
            with self.subTest(path=name, drift=True), patch.object(make_probe, "_native_metadata_directory", replaced):
                with self.assertRaisesRegex(MakeProbeError, "changed before invocation"), session:
                    session._native_make_readonly("all", native_metadata_directories=(name,))
            self.assert_clean(session)

    def test_native_runtime_metadata_supervisor_rejects_malformed_authority(self):
        from scripts.validation_ownership.syscall_guard import Policy, Violation
        identity = [1, 2, stat.S_IFDIR | 0o555, 0, 0]
        row = {"path": self.paths[0], "identity": identity}
        invalid = (
            {}, None, [None], [{**row, "path": []}], [{**row, "extra": 1}],
            [{**row, "path": "/sys"}], [row, row], [row, row, row, row],
            [{**row, "path": "/usr/share"}], [{**row, "path": "/usr/share/locale/child"}],
            [{**row, "identity": True}], [{**row, "identity": identity[:-1]}],
            [{**row, "identity": [True, *identity[1:]]}],
            [{**row, "identity": [-1, *identity[1:]]}],
            [{**row, "identity": [1 << 64, *identity[1:]]}],
            [{**row, "identity": [1, 2, 1 << 32, 0, 0]}],
            [{**row, "identity": [1, 2, stat.S_IFREG | 0o444, 0, 0]}],
            [{**row, "identity": [1, 2, stat.S_IFDIR | 0o777, 0, 0]}],
            [{**row, "identity": [1, 2, stat.S_IFDIR | 0o555, 1000, 0]}],
        )
        for path in self.paths:
            for declaration in invalid:
                selected = json.loads(json.dumps(declaration))
                if isinstance(selected, list):
                    for item in selected:
                        if isinstance(item, dict) and item.get("path") == row["path"]:
                            item["path"] = path
                with self.subTest(path=path, declaration=selected):
                    with self.assertRaisesRegex(Violation, "metadata-only directory authority"):
                        Policy({
                            "mode": "make", "native_readonly": True,
                            "native_metadata_directories": selected,
                        })

    def test_native_runtime_metadata_absence_rejects_actual_replaced_backing(self):
        foundation.FoundationTests.test_native_selinux_metadata_absence_rejects_actual_replaced_backing(self)

    def test_native_git_root_discovery_preserves_actual_absence_and_exact_scope(self):
        for root in self.git_roots:
            with self.assertRaises(FileNotFoundError):
                Path(root).lstat()
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <errno.h>\n#include <fcntl.h>\n"
            "#include <stdio.h>\n#include <string.h>\n#include <sys/stat.h>\n"
            "#include <unistd.h>\nint main(int argc,char **argv){int rc;struct stat info;"
            "if(argc!=3)return 1;errno=0;"
            "if(!strcmp(argv[1],\"metadata\"))rc=lstat(argv[2],&info);"
            "else rc=open(argv[2],!strcmp(argv[1],\"write\")?O_CREAT|O_WRONLY:O_RDONLY,0600);"
            "printf(\"result:%d;errno:%d\\n\",rc,errno);return 0;}\n"
        ))
        cases = [
            (root, operation, root + suffix, denied)
            for root in self.git_roots for operation, suffix, denied in (
                ("metadata", "", None), ("read", "", None),
                ("metadata", "/HEAD", None), ("read", "/HEAD", None),
                ("metadata", "-neighbor", "uncaptured Make runtime access"),
                ("write", "", "filesystem write denied"),
            )
        ]
        for root, operation, path, denied in cases:
            with self.subTest(operation=operation, path=path):
                self.add("Makefile", "all: ; @/native/tool " + operation + " " + path + "\n")
                session = self.session(runtime_files=(root,))
                with session:
                    tool = session.compile_native(("native.c",))
                    def run():
                        return session._native_make_readonly(
                            "all", native_tool=tool,
                            observe_reads=True, observe_runtime_completions=True,
                        )
                    if denied:
                        with self.assertRaisesRegex(MakeProbeError, denied):
                            run()
                    else:
                        ordinary = subprocess.run(
                            (str(tool.path), operation, path), cwd=self.root,
                            env=ENVIRONMENT, capture_output=True, timeout=10, check=True,
                        )
                        completed, _, observed = run()
                        self.assertEqual(completed.stdout, ordinary.stdout)
                        self.assertEqual(completed.stdout, f"result:-1;errno:{errno.ENOENT}\n".encode())
                        self.assertEqual(completed.stderr, b"")
                        self.assertNotIn(path, observed["accessed"])
                        if operation == "metadata":
                            record, = [row for row in observed["metadata"] if row[1] == path]
                            self.assertEqual(record[6], -errno.ENOENT)
                self.assert_clean(session)

    def test_native_git_root_capture_refuses_present_types_without_reading_content(self):
        self.add("root-git-file", b"not runtime content")
        directory = self.root / "root-git-directory"
        directory.mkdir()
        alias = self.root / "root-git-alias"
        alias.symlink_to(self.root / "root-git-file")
        original_lstat = Path.lstat
        cases = [
            (root, backing) for root in self.git_roots
            for backing in (self.root / "root-git-file", directory, alias)
        ]
        for root, backing in cases:
            with self.subTest(root=root, backing=backing):
                budget = ProbeBudget()
                def lstat(path, *args, **kwargs):
                    return original_lstat(backing if path == Path(root) else path, *args, **kwargs)
                with patch.object(make_probe, "_trusted_runtime_path", return_value=Path(root)):
                    with patch.object(Path, "lstat", lstat):
                        with patch.object(budget, "read_bytes", side_effect=AssertionError("present Git input was read")):
                            with self.assertRaisesRegex(MakeProbeError, "Git discovery runtime probe is not an actual absence"):
                                make_probe._capture_runtime_input(root, budget)

    def test_native_git_root_capture_binds_exact_optional_absence_and_replacement(self):
        for root in self.git_roots:
            resource = make_probe._capture_runtime_input(root, ProbeBudget())
            self.assertEqual((resource.path, resource.data, resource.mode), (root, None, None))
        cases = [
            (path, optional) for root in self.git_roots for path, optional in (
                (root, False), (root + "/HEAD", True), (root + "-other", True),
                ("/." + root, True), ("/repo/.." + root, True),
            )
        ]
        for path, optional in cases:
            with self.subTest(path=path, optional=optional):
                with self.assertRaises(MakeProbeError):
                    make_probe._trusted_runtime_path(path, optional=optional)
        self.add("replaced-root-git", b"replacement")
        replacement = (self.root / "replaced-root-git").lstat()
        original_lstat = Path.lstat
        for root in self.git_roots:
            with self.subTest(root=root):
                calls = 0
                def lstat(path, *args, **kwargs):
                    nonlocal calls
                    if path == Path(root):
                        calls += 1
                        if calls == 1:
                            raise FileNotFoundError(errno.ENOENT, "actual absence")
                        return replacement
                    return original_lstat(path, *args, **kwargs)
                with patch.object(make_probe, "_trusted_runtime_path", return_value=Path(root)):
                    with patch.object(Path, "lstat", lstat):
                        with self.assertRaisesRegex(MakeProbeError, "runtime input changed during capture"):
                            make_probe._capture_runtime_input(root, ProbeBudget())


class NativeWriterTests(unittest.TestCase):
    setUp = foundation.FoundationTests.setUp
    tearDown = foundation.FoundationTests.tearDown
    add = foundation.FoundationTests.add
    session = foundation.FoundationTests.session
    assert_clean = foundation.FoundationTests.assert_clean
    native_supervisor = foundation.FoundationTests.native_supervisor

    def test_native_compiler_cohort_binds_each_original_profile_to_its_actual_root(self):
        from scripts.validation_ownership import read_epochs
        self.add("src/query.c", '#include "query.h"\nint value = VALUE;\n')
        self.add("include/query.h", "#define VALUE 7\n")
        setup = self.session()
        with setup:
            setup._sealed_dependency_runtime()
            driver, frontend = setup.dependency_compiler
        self.assert_clean(setup)
        output = "query-$(FE8_ITEM_ID_CAP).d"
        outputs = ("query-0xCD.d", "query-0xCE.d")
        resources = ()
        recipe = (driver +
                  " -E -MM -MG -nostdinc -undef -MT query.o -Iinclude src/query.c > " + output)
        self.add("Makefile", ".PHONY: all\nall:\n\t@" + recipe +
                 "\n\t@v=owned; printf '%s' '$(FE8_ITEM_ID_CAP)'\n")
        class Commands:
            selected = outputs[0]
            def __getitem__(owner, argv):
                if argv[0] == "/bin/sh" and argv[1] == "-c" and argv[2] in {
                    recipe.replace("$(FE8_ITEM_ID_CAP)", cap) for cap in ("0xCD", "0xCE")
                }:
                    owner.selected = argv[2].rsplit(" > ", 1)[1]
                    return Command(argv, outputs=(owner.selected,), native_resources=resources)
                if argv[0] in {driver, frontend}:
                    return Command(argv, sources=("src/query.c",), code=("include/query.h",),
                                   directories=("src", "include"), outputs=(owner.selected,),
                                   native_resources=resources)
                if argv in {( "/bin/sh", "-c", "v=owned; printf '%s' '" + cap + "'")
                            for cap in ("0xCD", "0xCE")}:
                    return Command(argv)
                raise KeyError(argv)
        session = self.session(runtime_files=(
            "/proc/filesystems", "/proc/mounts", "/etc/selinux/config",
        ))
        with session:
            session._sealed_dependency_runtime()
            deadline, limits = session.budget.deadline, session.budget.limits
            results, observed = session._native_make_cohort(tuple(
                ("all", "Makefile", (
                    ("command-line", "PYTHON", "python3"),
                    ("command-line", "FE8_ITEM_ID_CAP", cap),
                    ("command-line", "EXPANSION_CUSTOM_SPELL_EFFECTS", str(index)),
                    ("command-line", "MODERN_BUILD_ROOT", "build/original-" + cap),
                    ("command-line", "ASSET_MANIFEST", "assets/manifest.json" if not index
                     else "assets/manifests/custom-spell-reference.json"),
                ))
                for index, cap in enumerate(("0xCD", "0xCE"))
            ), variables=("FE8_ITEM_ID_CAP",), commands=Commands(),
                writable_outputs=outputs, native_resources=resources,
                native_executables=(driver, frontend))
            self.assertEqual([result.stdout for result, _, _ in results], [b"0xCD", b"0xCE"])
            self.assertEqual([semantics["domains"]["FE8_ITEM_ID_CAP"]["value"]
                              for _, semantics, _ in results], ["0xCD", "0xCE"])
            self.assertEqual([[(file.path, file.data) for file in files] for _, _, files in results],
                             [[(outputs[0], b"query.o: src/query.c include/query.h\n")],
                              [(name, b"query.o: src/query.c include/query.h\n") for name in outputs]])
            archive = read_epochs.reconstruct_archive(observed["read_trace"], budget=session.budget)
            self.assertEqual([dict(actor.environment)["FE8_ITEM_ID_CAP"]
                              for actor in archive.compiler_executions],
                             ["0xCD", "0xCD", "0xCE", "0xCE"])
            roots = observed["read_trace"]["machine"]["roots"]
            self.assertEqual(len(roots), 2)
            self.assertNotEqual(roots[0]["initial"]["pid"], roots[1]["initial"]["pid"])
            self.assertEqual(session.budget.deadline, deadline)
            self.assertIs(session.budget.limits, limits)
            trace = observed["read_trace"]
            for field in ("FE8_ITEM_ID_CAP", "MAKEFLAGS", "MAKEOVERRIDES"):
                changed = json.loads(json.dumps(trace))
                row = next(row for row in changed["machine"]["events"]
                           if row["kind"] == "native-tree" and row["event"]["kind"] == "exec"
                           and row["event"]["path"] == frontend and row["exec"] >= roots[1]["first_exec"])
                row["event"]["admission"]["compiler"]["environment"][field] = "foreign"
                row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                with self.subTest(replay=field), self.assertRaises(MakeProbeError):
                    read_epochs.validate_trace(
                        changed, changed["scope"], count_limit=session.budget.limits.observation_count,
                        file_limit=session.budget.limits.file_bytes,
                        reserve=lambda size: session.budget.charge("control", size),
                    )
        self.assert_clean(session)
        from scripts.validation_ownership.producer_channel import ProducerChannel
        for defect in ("missing-final-output", "foreign-root"):
            session = self.session()
            received = ProducerChannel.receive
            mutated = []
            def receive(channel):
                data = received(channel)
                if data is None or defect != "foreign-root":
                    return data
                value = json.loads(data)
                if value.get("root") == 2 and not mutated:
                    value["root"] = 1
                    mutated.append(True)
                    return encoded(value)
                return data
            with self.subTest(actual=defect), session, patch.object(ProducerChannel, "receive", receive):
                session._sealed_dependency_runtime()
                with self.assertRaisesRegex(MakeProbeError, "retained output|root|environment|admission"):
                    session._native_make_cohort(tuple(
                        ("all", "Makefile", (("command-line", "FE8_ITEM_ID_CAP", cap),))
                        for cap in ("0xCD", "0xCE")
                    ), variables=("FE8_ITEM_ID_CAP",), commands=Commands(),
                        writable_outputs=outputs + (("missing.d",) if defect == "missing-final-output" else ()),
                        native_executables=(driver, frontend))
                self.assertTrue(session.budget.failed)
                self.assertEqual(bool(mutated), defect == "foreign-root")
            self.assert_clean(session)

    def test_native_finite_pattern_roots_remake_generated_reads_and_retire_templates(self):
        from scripts.validation_ownership import read_epochs
        self.add("Makefile", (
            "-include $(PROFILE).mk\n"
            "%.mk: HELPER = pattern\n"
            "$(PROFILE).mk: ; @printf '%s\\n' 'RESULT := $(HELPER)' > $(PROFILE).mk\n"
            ".PHONY: all\nall: ; @v=owned; printf '%s' '$(PROFILE):$(RESULT)'\n"
        ))
        outputs = ("first.mk", "second.mk")
        commands = {}
        for profile in ("first", "second"):
            recipe = "printf '%s\\n' 'RESULT := pattern' > " + profile + ".mk"
            argv = ("/bin/sh", "-c", recipe)
            commands[argv] = Command(argv, outputs=(profile + ".mk",))
            argv = ("/bin/sh", "-c", "v=owned; printf '%s' '" + profile + ":pattern'")
            commands[argv] = Command(argv)
        requests = tuple(
            ("all", "Makefile", (("command-line", "PROFILE", profile),))
            for profile in ("first", "second")
        )
        for patterns in (False, True):
            session = self.session()
            with self.subTest(patterns=patterns), session:
                if not patterns:
                    with self.assertRaisesRegex(
                        MakeProbeError, "runtime post-read effect/eval is not qualified",
                    ):
                        session._native_make_cohort(
                            requests, variables=("RESULT",), writable_outputs=outputs,
                            commands=commands, observe_patterns=False,
                        )
                    self.assertTrue(session.budget.failed)
                else:
                    deadline, limits = session.budget.deadline, session.budget.limits
                    results, observed = session._native_make_cohort(
                        requests, variables=("RESULT",), writable_outputs=outputs,
                        commands=commands, observe_patterns=True,
                    )
                    self.assertEqual([row[0].stdout for row in results],
                                     [b"first:pattern", b"second:pattern"])
                    self.assertEqual([row[0].stderr for row in results], [b"", b""])
                    self.assertEqual(
                        [[(file.path, file.data) for file in row[2]] for row in results],
                        [[(outputs[0], b"RESULT := pattern\n")],
                         [(name, b"RESULT := pattern\n") for name in outputs]],
                    )
                    trace = observed["read_trace"]
                    archive = read_epochs.reconstruct_archive(trace, budget=session.budget)
                    roots = trace["machine"]["roots"]
                    self.assertEqual(len(roots), 2)
                    self.assertEqual(len(archive.passes), 4)
                    for root in roots:
                        parts = archive.passes[root["first_exec"] - 1:root["last_exec"]]
                        self.assertEqual(len(parts), 2)
                        self.assertTrue(all(part.pattern_templates for part in parts))
                        self.assertTrue(any(part.patterns for part in parts))
                        visits = [visit for part in parts for visit in part.visits
                                  if visit.source is not None and visit.name in outputs]
                        self.assertTrue(visits)
                        self.assertTrue(all(visit.source.data == b"RESULT := pattern\n"
                                            for visit in visits))
                    self.assertEqual(session.budget.deadline, deadline)
                    self.assertIs(session.budget.limits, limits)
                    self.assertFalse(session.budget.failed)
            self.assert_clean(session)

    def test_native_finite_cohort_captures_distinct_actual_roots_and_inputs(self):
        self.add("Makefile", (
            ".PHONY: first second\n"
            "first:\n\t@v=one; printf first; printf first-error >&2\n"
            "second:\n\t@v=two; printf second; printf second-error >&2\n"
        ))
        session = self.session()
        with session:
            results, observed = session._native_make_cohort((
                ("first", "Makefile", (("command-line", "VALUE", "one"),)),
                ("second", "Makefile", (("environment", "VALUE", "two"),)),
            ), variables=("MAKECMDGOALS", "VALUE"))
            self.assertEqual(
                [(result.stdout, result.stderr) for result, _, _ in results],
                [(b"first", b"first-error"), (b"second", b"second-error")],
            )
            self.assertEqual(
                [(semantics["domains"]["MAKECMDGOALS"]["value"], semantics["domains"]["VALUE"]["value"])
                 for _, semantics, _ in results],
                [("first", "one"), ("second", "two")],
            )
            roots = observed["read_trace"]["machine"]["roots"]
            self.assertNotEqual(roots[0]["initial"]["pid"], roots[1]["initial"]["pid"])
            self.assertEqual([row["initial"]["wait"] for row in roots], [0, 0])
            self.assertEqual(roots[0]["last"] + 1, roots[1]["first"])
            self.assertEqual([generated for _, _, generated in results], [(), ()])
        self.assert_clean(session)

    def test_native_root_boundary_replay_requires_admitted_outputs_and_no_temporary(self):
        from scripts.validation_ownership import read_epochs
        recipes = {
            "first": "printf one > scratch.tmp; rm -f scratch.tmp; printf one > first.out",
            "second": "printf two > second.out",
        }
        self.add("Makefile", ".PHONY: first second\n" + "".join(
            target + ":\n\t@" + recipe + "\n" for target, recipe in recipes.items()
        ))
        resources = (("temporary", "scratch.tmp"),)
        commands = {
            ("/bin/sh", "-c", recipe): Command(
                ("/bin/sh", "-c", recipe), outputs=(target + ".out",), native_resources=resources,
            ) for target, recipe in recipes.items()
        }
        commands[("rm", "-f", "scratch.tmp")] = Command(
            ("rm", "-f", "scratch.tmp"), native_resources=resources,
        )
        session = self.session()
        with session:
            results, observed = session._native_make_cohort((
                ("first", "Makefile", ()), ("second", "Makefile", ()),
            ), commands=commands, native_executables=("/usr/bin/rm",),
                writable_outputs=("first.out", "second.out"), native_resources=resources)
            self.assertEqual([[(item.path, item.data) for item in files] for _, _, files in results],
                             [[("first.out", b"one")], [("first.out", b"one"), ("second.out", b"two")]])
            trace = observed["read_trace"]
            for defect in ("admitted-later", "retired-later"):
                changed = json.loads(json.dumps(trace))
                machine = changed["machine"]
                if defect == "admitted-later":
                    admission = changed["output_authority"]["jobs"][0]["admission"]
                    admission["outputs"].append("second.out")
                    admission["owner"] = native_command_owner(
                        admission["closure"], admission["outputs"], admission.get("resources", ()),
                    )
                    for row in machine["events"]:
                        if row["kind"] == "native-tree" and row["dispatch"] == 1 and row["event"]["kind"] == "exec":
                            if row["event"]["pid"] == changed["output_authority"]["jobs"][0]["pid"]:
                                row["event"]["admission"] = json.loads(json.dumps(admission))
                        elif row["kind"] == "execute" and not row["make"] and row["dispatch"] == 1:
                            row["admission_owner"] = admission["owner"]
                else:
                    retirement = next(row for row in machine["events"]
                                      if row["kind"] == "native-output" and row["event"]["kind"] == "output-retire")
                    machine["events"].remove(retirement)
                    execution = next(row for row in machine["events"]
                                     if row["kind"] == "native-tree" and row["dispatch"] == 2
                                     and row["event"]["kind"] == "exec")
                    retirement.update({key: execution[key] for key in ("pid", "exec", "pass", "trace_seq", "dispatch")})
                    retirement["event"].update(pid=execution["pid"], operation_owner=2)
                    machine["events"].insert(machine["events"].index(execution) + 1, retirement)
                    machine["roots"][0]["last"] -= 1
                    machine["roots"][1]["first"] -= 1
                output_sequence = 0
                for number, row in enumerate(machine["events"], 1):
                    row["seq"] = number
                    if row["kind"] == "native-output":
                        output_sequence += 1
                        row["event"]["sequence"] = output_sequence
                    if row["kind"] in {"native-output", "native-tree"}:
                        row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                with self.subTest(replay=defect), self.assertRaisesRegex(MakeProbeError, "finite native root"):
                    read_epochs.validate_trace(
                        changed, changed["scope"], count_limit=session.budget.limits.observation_count,
                        file_limit=session.budget.limits.file_bytes,
                        reserve=lambda size: session.budget.charge("control", size),
                    )
        self.assert_clean(session)

    def test_native_finite_cohort_keeps_generated_versions_and_internal_reexec(self):
        self.add("native.c", (
            "#include <stdio.h>\n"
            "int main(int n,char **a){FILE *f;if(n!=2)return 1;"
            "f=fopen(\"generated.mk.tmp\",\"w\");if(!f)return 2;"
            "if(fprintf(f,\"VALUE := %s\\n\",a[1])<0||fclose(f))return 3;"
            "return rename(\"generated.mk.tmp\",\"generated.mk\")!=0;}\n"
        ))
        self.add("Makefile", (
            "GENERATED := $(shell /native/tool $(MAKECMDGOALS))\n"
            "include generated.mk\n"
            ".PHONY: first second\nfirst second: ; @:\n"
        ))
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(
                        argv, native_tool=tool,
                        outputs=("generated.mk",) if argv[0] == "/native/tool" else (),
                        native_resources=(("temporary", "generated.mk.tmp"),),
                    )
            results, observed = session._native_make_cohort((
                ("first", "Makefile", ()), ("second", "Makefile", ()),
            ), variables=("VALUE",), native_tool=tool, commands=Commands(),
                writable_outputs=("generated.mk",),
                native_resources=(("temporary", "generated.mk.tmp"),))
            self.assertEqual(
                [(files[0].path, files[0].data, files[0].mode) for _, _, files in results],
                [("generated.mk", b"VALUE := first\n", 0o644),
                 ("generated.mk", b"VALUE := second\n", 0o644)],
            )
            self.assertEqual(
                [semantics["domains"]["VALUE"]["value"] for _, semantics, _ in results],
                ["first", "second"],
            )
            opened = [
                row for row in observed["read_trace"]["events"]
                if row["kind"] == "source-open" and row["path"] == "generated.mk"
            ]
            self.assertEqual(len(opened), 2)
            self.assertNotEqual(opened[0]["source"], opened[1]["source"])
            from scripts.validation_ownership import read_epochs
            plan = read_epochs.native_request_plan([
                {key: row["initial"][key] for key in ("argv", "cwd", "environment")}
                for row in observed["native_results"]
            ], count_limit=32768, file_limit=16777216)
            for mutation in (
                "rows.pop()", "rows.reverse()", "rows[1]['ordinal']=False",
                "rows[1]['initial']['pid']=rows[0]['initial']['pid']",
                "rows[1]['initial']['argv'].append('FOREIGN=1')",
                "rows[1]['initial']['environment']['FOREIGN']='1'",
                "rows[1]['initial']['cwd']='/'", "rows[1]['initial']['wait']=256",
                "rows[1]['outputs']=[]", "rows[1]['outputs'].append(dict(rows[1]['outputs'][0]))",
                "rows[1]['outputs'][0]['data']=rows[0]['outputs'][0]['data']",
                "rows[1]['outputs'][0]['serial']=True", "rows[1]['outputs'][0]['revision']+=1",
                "rows[1]['outputs'][0]['identity'][2]^=1",
                "rows[1]['outputs'][0]['identity'][3]+=1",
                "rows[1]['outputs'][0]['path']='/repo/foreign'",
                "rows[1]['stdout']='!'", "rows[1]['observation']=None",
                "rows[1]['foreign']=1",
            ):
                invalid = json.loads(json.dumps(observed["native_results"]))
                exec(mutation, {}, {"rows": invalid})
                with self.subTest(mutation=mutation), self.assertRaises(read_epochs.ReadEpochError):
                    read_epochs.validate_native_results(
                        invalid, plan, observed["read_trace"], count_limit=32768,
                        file_limit=16777216, output_limit=1048576, reserve=lambda size: None,
                    )
        self.assert_clean(session)

        self.add("Makefile", (
            "-include generated.mk\n"
            "generated.mk:\n\t@printf 'VALUE := produced\\n' > generated.mk\n"
            "all: ; @:\n"
        ))
        session = self.session()
        with session:
            class ReexecCommands:
                def __getitem__(self, argv):
                    return Command(argv, outputs=("generated.mk",) if "generated.mk" in argv[-1] else ())
            results, observed = session._native_make_cohort(
                (("all", "Makefile", ()),), variables=("VALUE",),
                commands=ReexecCommands(), writable_outputs=("generated.mk",),
            )
            root, = observed["read_trace"]["machine"]["roots"]
            self.assertEqual((root["first_exec"], root["last_exec"]), (1, 2))
            self.assertEqual(results[0][1]["domains"]["VALUE"]["value"], "produced")
            self.assertEqual(results[0][2][0].data, b"VALUE := produced\n")
        self.assert_clean(session)

    def test_native_finite_cohort_failed_first_stops_before_successor(self):
        self.add("Makefile", (
            ".PHONY: failed second\nfailed: ; @printf failed-error >&2; exit 3\nsecond: ; @printf second\n"
        ))
        reports = []
        session = self.session()
        read = session.budget.read_bytes
        def capture(path, category):
            data = read(path, category)
            if category == "control" and Path(path).name.startswith("report-"):
                reports.append(json.loads(data))
            return data
        with session, patch.object(session.budget, "read_bytes", capture):
            with self.assertRaisesRegex(MakeProbeError, "native GNU Make failed: 2.*failed-error"):
                session._native_make_cohort((
                    ("failed", "Makefile", ()), ("second", "Makefile", ()),
                ))
            self.assertEqual(reports[-1]["native_results"], [])
            self.assertEqual(reports[-1]["native_root"]["argv"][-1], "failed")
            self.assertEqual(reports[-1]["native_root"]["wait"], 512)
            self.assertTrue(session.budget.failed)
        self.assert_clean(session)

    def test_native_finite_cohort_refuses_malformed_plans_before_launch(self):
        self.add("Makefile", "all: ; @:\n")
        for requests in (
            (), [], [("all", "Makefile", ())],
            (("all", "Makefile", []),), (("all", "Makefile", (("command-line", 1, "x"),)),),
            (("all", "Makefile", ()), ("foreign", "absent.mk", ())),
            (("all", "Makefile", ()), ("all", "Makefile", (("environment", "SHELL", "foreign"),))),
        ):
            session = self.session()
            with self.subTest(requests=requests), session, patch.object(
                session, "_sandbox_run", side_effect=AssertionError("invalid plan launched a capsule"),
            ):
                with self.assertRaises(MakeProbeError):
                    session._native_make_cohort(requests)
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)

    def test_native_finite_cohort_failed_later_preserves_prefix_without_successor(self):
        from base64 import b64decode
        self.add("Makefile", (
            ".PHONY: first failed third\nfirst: ; @v=once; printf first\n"
            "failed: ; @printf failed-error >&2; exit 3\nthird: ; @printf forbidden\n"
        ))
        session = self.session()
        reports = []
        read = session.budget.read_bytes
        def capture(path, category):
            data = read(path, category)
            if category == "control" and Path(path).name.startswith("report-"):
                reports.append(json.loads(data))
            return data
        with session, patch.object(session.budget, "read_bytes", capture):
            with self.assertRaisesRegex(MakeProbeError, "native GNU Make failed: 2.*failed-error"):
                session._native_make_cohort((
                    ("first", "Makefile", ()), ("failed", "Makefile", ()), ("third", "Makefile", ()),
                ))
            prefix, = reports[-1]["native_results"]
            self.assertEqual(prefix["initial"]["argv"][-1], "first")
            self.assertEqual(b64decode(prefix["stdout"], validate=True), b"first")
            self.assertEqual(reports[-1]["native_root"]["argv"][-1], "failed")
            self.assertNotEqual(prefix["initial"]["pid"], reports[-1]["native_root"]["pid"])
            self.assertEqual(reports[-1]["native_root"]["wait"], 512)
            self.assertTrue(session.budget.failed)
        self.assert_clean(session)

    def test_native_finite_cohort_enforces_one_cumulative_stdio_bound(self):
        first, second = "a" * 1024, "b" * 1024
        self.add("Makefile", (
            ".PHONY: first second\n"
            f"first: ; @i=0; while test $$i -lt 512; do printf '{first}'; i=$$((i+1)); done\n"
            f"second: ; @i=0; while test $$i -lt 512; do printf '{second}' >&2; i=$$((i+1)); done\n"
        ))
        for limit in (1048576, 1048575):
            session = self.session(process_output_bytes=limit)
            with self.subTest(limit=limit), session:
                if limit == 1048576:
                    results, _ = session._native_make_cohort((
                        ("first", "Makefile", ()), ("second", "Makefile", ()),
                    ))
                    self.assertEqual(
                        [(completed.stdout, completed.stderr) for completed, _, _ in results],
                        [(first.encode() * 512, b""), (b"", second.encode() * 512)],
                    )
                else:
                    with self.assertRaisesRegex(MakeProbeError, "cumulative process output bound"):
                        session._native_make_cohort((
                            ("first", "Makefile", ()), ("second", "Makefile", ()),
                        ))
                    self.assertTrue(session.budget.failed)
            self.assert_clean(session)

    def test_native_finite_cohort_host_rejects_returned_root_and_observation_changes(self):
        self.add("Makefile", ".PHONY: first second\nfirst second: ; @:\n")
        for mutation in (
            "row['native_results'].pop()",
            "row['native_results'].reverse()",
            "row['native_results'][1]['initial']['environment']['FOREIGN']='1'",
            "row['native_root']['pid']+=1",
            "row['native_results'][1]['observation']=row['native_results'][0]['observation']",
        ):
            session = self.session()
            read = session.budget.read_bytes
            def changed(path, category):
                data = read(path, category)
                if category == "control" and Path(path).name.startswith("report-"):
                    row = json.loads(data)
                    exec(mutation, {}, {"row": row})
                    return encoded(row)
                return data
            with self.subTest(mutation=mutation), session, patch.object(session.budget, "read_bytes", changed):
                with self.assertRaises(MakeProbeError):
                    session._native_make_cohort((
                        ("first", "Makefile", ()), ("second", "Makefile", ()),
                    ), variables=("MAKECMDGOALS",))
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)

    def test_native_finite_cohort_pending_source_and_deadline_block_successor(self):
        self.add("Makefile", ".PHONY: first second\nfirst second: ; @:\n")
        for pending in (True, False):
            body = (
                "retire=guard.NativeReadTrace.retire_root\n"
                "def held(self):\n"
                + (" self.invocations.append({'kind':'tester-unretired'})\n" if pending else "")
                + " retire(self)\n"
                + ("" if pending else " self.config['deadline']=guard.time.monotonic()-1\n")
                + "guard.NativeReadTrace.retire_root=held\n"
            )
            session = self.session()
            reports = []
            read = session.budget.read_bytes
            def capture(path, category):
                data = read(path, category)
                if category == "control" and Path(path).name.startswith("report-"):
                    reports.append(json.loads(data))
                return data
            with self.subTest(pending=pending), self.native_supervisor(body), session, patch.object(
                session.budget, "read_bytes", capture,
            ):
                with self.assertRaisesRegex(
                    MakeProbeError, "incomplete actual state" if pending else "deadline exhausted before finite",
                ):
                    session._native_make_cohort((
                        ("first", "Makefile", ()), ("second", "Makefile", ()),
                    ))
                self.assertEqual(reports[-1]["processes"], 1)
                self.assertEqual(len(reports[-1]["native_results"]), 0 if pending else 1)
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)

    def test_native_finite_cohort_large_write_bounds_actual_pipe_retention(self):
        self.add("native.c", (
            "#include <unistd.h>\nstatic char data[2*1024*1024];\n"
            "int main(int n,char **a){return write(n==2&&a[1][0]=='2'?2:1,data,sizeof data)<0;}\n"
        ))
        self.add("Makefile", ".PHONY: first second\nfirst: ; @/native/tool $(FD)\nsecond: ; @:\n")
        for descriptor in (1, 2):
            self._check_finite_large_write(descriptor)

    def _check_finite_large_write(self, descriptor):
        receipt = self.directory / f"actual-finite-pipe-capture-{descriptor}.json"
        body = (
            "read=guard.os.read\n"
            "total=0\n"
            "def observed_read(fd,size):\n"
            " global total\n"
            " info=os.fstat(fd)\n"
            " data=read(fd,size)\n"
            " if guard.stat.S_ISFIFO(info.st_mode):\n"
            "  total+=len(data)\n"
            f"  Path({str(receipt)!r}).write_text(json.dumps({{'actual_pipe_read_bytes':total}}))\n"
            " return data\n"
            "guard.os.read=observed_read\n"
        )
        reports = []
        session = self.session()
        read = session.budget.read_bytes
        def report(path, category):
            data = read(path, category)
            if category == "control" and Path(path).name.startswith("report-"):
                reports.append(json.loads(data))
            return data
        with session, self.native_supervisor(body), patch.object(session.budget, "read_bytes", report):
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool)
            with self.assertRaisesRegex(MakeProbeError, "cumulative process output bound"):
                session._native_make_cohort((
                    ("first", "Makefile", (("command-line", "FD", str(descriptor)),)),
                    ("second", "Makefile", ()),
                ), native_tool=tool, commands=Commands())
            actual = json.loads(receipt.read_bytes())
            self.assertEqual(actual["actual_pipe_read_bytes"], 1048577)
            self.assertEqual(reports[-1]["native_results"], [])
            self.assertEqual(reports[-1]["processes"], 2)
            self.assertTrue(session.budget.failed)
        self.assert_clean(session)

    def test_native_finite_cohort_preserves_pipe_syscalls_and_inherited_dup_writes(self):
        self.add("native.c", (
            "#define _POSIX_C_SOURCE 200809L\n"
            "#include <errno.h>\n#include <sys/stat.h>\n#include <sys/wait.h>\n"
            "#include <unistd.h>\n"
            "int main(void){struct stat s;char c;int fd;pid_t p;"
            "if(fstat(1,&s)||!S_ISFIFO(s.st_mode))return 1;"
            "errno=0;if(lseek(1,0,SEEK_SET)!=-1||errno!=ESPIPE)return 2;"
            "errno=0;if(pwrite(1,\"x\",1,0)!=-1||errno!=ESPIPE)return 3;"
            "errno=0;if(read(1,&c,1)!=-1||errno!=EBADF)return 4;"
            "fd=dup(1);if(fd<0)return 5;p=fork();if(p<0)return 6;"
            "if(!p){if(write(fd,\"child\",5)!=5)_exit(7);_exit(0);}"
            "if(waitpid(p,0,0)!=p||write(fd,\"parent\",6)!=6)return 8;"
            "return close(fd)!=0;}\n"
        ))
        self.add("Makefile", ".PHONY: first second\nfirst second: ; @/native/tool\n")
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool)
            results, observed = session._native_make_cohort((
                ("first", "Makefile", ()), ("second", "Makefile", ()),
            ), native_tool=tool, commands=Commands())
            self.assertEqual(
                [(completed.returncode, completed.stdout, completed.stderr) for completed, _, _ in results],
                [(0, b"childparent", b""), (0, b"childparent", b"")],
            )
            self.assertEqual(observed["processes"], 6)
        self.assert_clean(session)

    def test_native_finite_cohort_stdio_cap_does_not_lower_generated_file_allowance(self):
        self.add("native.c", (
            "#include <stdio.h>\nstatic char data[1024*1024+1];\n"
            "int main(void){FILE *f=fopen(\"generated.bin\",\"w\");"
            "if(!f)return 1;if(fwrite(data,1,sizeof data,f)!=sizeof data)return 2;"
            "return fclose(f)!=0;}\n"
        ))
        self.add("Makefile", "all: ; @/native/tool\n")
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool, outputs=("generated.bin",))
            results, observed = session._native_make_cohort(
                (("all", "Makefile", ()),), native_tool=tool, commands=Commands(),
                writable_outputs=("generated.bin",),
            )
            completed, _, generated = results[0]
            self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
            self.assertEqual(
                [(item.path, item.mode, len(item.data)) for item in generated],
                [("generated.bin", 0o644, 1048577)],
            )
            self.assertEqual(generated[0].data, bytes(1048577))
            self.assertEqual(observed["native_results"][0]["outputs"][0]["identity"][3], 1048577)
        self.assert_clean(session)

    def test_native_root_observation_binds_actual_initial_inputs_and_terminal(self):
        self.add("Makefile", ".PHONY: all failed\nall: ; @:\nfailed: ; @exit 3\n")
        for target in ("all", "failed"):
            session = self.session()
            reports = []
            read = session.budget.read_bytes
            def capture(path, category):
                data = read(path, category)
                if category == "control" and Path(path).name.startswith("report-"):
                    reports.append(json.loads(data))
                return data
            with self.subTest(target=target), session, patch.object(session.budget, "read_bytes", capture):
                if target == "failed":
                    with self.assertRaisesRegex(MakeProbeError, "native GNU Make failed: 2"):
                        session._native_make_readonly(
                            target, observe_reads=True, observe_runtime_completions=True, observe_root=True,
                        )
                else:
                    completed, _, observed = session._native_make_readonly(
                        target, observe_reads=True, observe_runtime_completions=True, observe_root=True,
                    )
                    self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, b"", b""))
                    self.assertEqual(observed["native_root"], reports[-1]["native_root"])
                    self.assertEqual({
                        row["pid"] for row in observed["read_trace"]["machine"]["events"]
                        if row["kind"] == "execute" and row["make"]
                    }, {observed["native_root"]["pid"]})
                root = reports[-1]["native_root"]
                self.assertEqual(root["argv"], ["/usr/bin/make", "-f", "Makefile", target])
                self.assertEqual(root["cwd"], "/repo")
                self.assertEqual(root["environment"]["VO_OBSERVE_TARGET"], target)
                self.assertEqual((root["version"], root["exit_stop"], root["wait"]),
                                 (1, 512 if target == "failed" else 0, 512 if target == "failed" else 0))
            self.assert_clean(session)
        session = self.session()
        with session:
            _, _, observed = session._native_make_readonly(
                "all", observe_reads=True, observe_runtime_completions=True,
            )
            self.assertNotIn("native_root", observed)
        self.assert_clean(session)

    def test_native_root_initial_actual_input_changes_refuse(self):
        self.add("Makefile", ".PHONY: all\nall: ; @:\n")
        for mode in ("argv", "environment", "cwd"):
            body = (
                "original=guard.os.execve\n"
                "def execute(path,argv,environment):\n"
                " if path=='/usr/bin/make':\n"
                f"  if {mode!r}=='argv':argv=[*argv,'FOREIGN=1']\n"
                f"  if {mode!r}=='environment':environment={{**environment,'FOREIGN':'1'}}\n"
                f"  if {mode!r}=='cwd':os.chdir('/')\n"
                " return original(path,argv,environment)\n"
                "guard.os.execve=execute\n"
            )
            session = self.session()
            with self.subTest(mode=mode), self.native_supervisor(body), session:
                with self.assertRaisesRegex(MakeProbeError, "native root actual"):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, observe_root=True,
                    )
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)

    def test_native_root_returned_shape_and_bindings_refuse(self):
        self.add("Makefile", ".PHONY: all\nall: ; @:\n")
        for mutation in (
            "del row['native_root']", "row['native_root']=None",
            "root['version']=2", "root['pid']+=1", "root['wait']=256",
            "root['exit_stop']=None", "root['exit_stop']=False",
            "root['argv'].append('FOREIGN=1')", "root['cwd']='/'",
            "root['environment']['FOREIGN']='1'", "root['foreign']=1",
        ):
            session = self.session()
            read = session.budget.read_bytes
            def mutated(path, category):
                data = read(path, category)
                if category == "control" and Path(path).name.startswith("report-"):
                    row = json.loads(data)
                    root = row["native_root"]
                    exec(mutation, {}, {"row": row, "root": root})
                    return encoded(row)
                return data
            with self.subTest(mutation=mutation), session, patch.object(session.budget, "read_bytes", mutated):
                with self.assertRaisesRegex(MakeProbeError, "native root report|malformed supervisor"):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, observe_root=True,
                    )
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)

    def test_native_finite_machine_root_keeps_actual_reexec_and_rejects_range_mutations(self):
        from scripts.validation_ownership import read_epochs
        self.add("Makefile", (
            "-include generated.mk\n"
            "generated.mk:\n\t@printf 'VALUE := produced\\n' > generated.mk\n"
            "all:\n\t@v=once; printf '%s' \"$$v\"\n"
        ))
        class Commands:
            def __getitem__(self, argv):
                return Command(argv, outputs=("generated.mk",) if "generated.mk" in argv[-1] else ())
        body = (
            "finish=guard.NativeReadTrace.finish\n"
            "def finite_finish(self):\n"
            " self.retire_root()\n"
            " return finish(self)\n"
            "guard.NativeReadTrace.finish=finite_finish\n"
        )
        session = self.session()
        with self.native_supervisor(body), session:
            completed, semantics, observed, generated = session._native_make_writable(
                "all", outputs=("generated.mk",), commands=Commands(),
                variables=("VALUE",), observe_reads=True, observe_runtime_completions=True,
                observe_root=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"once", b""))
            self.assertEqual(semantics["domains"]["VALUE"]["value"], "produced")
            self.assertEqual([(row.path, row.data, row.mode) for row in generated], [
                ("generated.mk", b"VALUE := produced\n", 0o644),
            ])
            trace = observed["read_trace"]
            machine = trace["machine"]
            root, = machine["roots"]
            self.assertEqual(machine["version"], 2)
            self.assertEqual((root["first"], root["last"]), (1, len(machine["events"])))
            self.assertEqual((root["first_exec"], root["last_exec"]), (1, 2))
            self.assertEqual(root["initial"], observed["native_root"])
            for mutation in (
                "roots.clear()", "roots.append(roots[0])", "roots[0]['ordinal']=2",
                "roots[0]['first']=2", "roots[0]['last']-=1",
                "roots[0]['first_exec']=2", "roots[0]['last_exec']=1",
                "roots[0]['initial']['pid']=999", "roots[0]['initial']['wait']=512",
                "roots[0]['initial']['exit_stop']=True",
                "roots[0]['initial']['environment']['BAD']='\\x00'",
                "roots[0]['extra']=0",
            ):
                with self.subTest(mutation=mutation):
                    invalid = json.loads(json.dumps(trace))
                    exec(mutation, {}, {"roots": invalid["machine"]["roots"]})
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
        self.assert_clean(session)

    def test_native_finite_machine_root_refuses_reused_or_incomplete_retirement(self):
        self.add("Makefile", ".PHONY: all\nall: ; @:\n")
        for incomplete in (False, True):
            body = (
                "finish=guard.NativeReadTrace.finish\n"
                "def finite_finish(self):\n"
                + (" self.invocations.append({'kind':'tester-unretired'})\n" if incomplete else
                   " self.retire_root()\n")
                + " self.retire_root()\n"
                " return finish(self)\n"
                "guard.NativeReadTrace.finish=finite_finish\n"
            )
            session = self.session()
            with self.subTest(incomplete=incomplete), self.native_supervisor(body), session:
                with self.assertRaisesRegex(
                    MakeProbeError, "incomplete actual state" if incomplete else "reused its completed actual range",
                ):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, observe_root=True,
                    )
            self.assert_clean(session)

    def test_native_finite_machine_root_charges_exact_retained_boundary(self):
        self.add("Makefile", ".PHONY: all\nall: ; @:\n")
        for insufficient in (False, True):
            receipt = self.directory / ("finite-root-charge-" + str(insufficient) + ".json")
            body = (
                "finish=guard.NativeReadTrace.finish\n"
                "retire=guard.NativeReadTrace.retire_root\n"
                "def finite_finish(self):\n"
                " initial=dict(self.policy.native_root,argv=list(self.policy.native_root['argv']),"
                "environment=dict(self.policy.native_root['environment']))\n"
                " row={'ordinal':1,'initial':initial,'first':1,'last':len(self.machine),"
                "'first_exec':1,'last_exec':self.execs}\n"
                " table=[];empty=sys.getsizeof(table);table.append(row)\n"
                " expected=128+len(guard.encoded(row))+sys.getsizeof(initial)"
                "+sys.getsizeof(initial['argv'])+sys.getsizeof(initial['environment'])"
                "+empty+sys.getsizeof(table)\n"
                " before=self.policy.observation_bytes;limit=self.config['observation_limit']\n"
                f" self.config['observation_limit']=before+expected-int({insufficient!r})\n"
                " try:retire(self)\n"
                " finally:\n"
                f"  Path({str(receipt)!r}).write_text(json.dumps({{'expected':expected,'charged':self.policy.observation_bytes-before}}))\n"
                "  self.config['observation_limit']=limit\n"
                " return finish(self)\n"
                "guard.NativeReadTrace.finish=finite_finish\n"
            )
            session = self.session()
            with self.subTest(insufficient=insufficient), self.native_supervisor(body), session:
                if insufficient:
                    with self.assertRaisesRegex(MakeProbeError, "metadata observation byte budget exhausted"):
                        session._native_make_readonly(
                            "all", observe_reads=True, observe_runtime_completions=True, observe_root=True,
                        )
                    self.assertTrue(session.budget.failed)
                else:
                    completed, _, observed = session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, observe_root=True,
                    )
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(observed["read_trace"]["machine"]["version"], 2)
                actual = json.loads(receipt.read_bytes())
                self.assertEqual(actual["charged"], actual["expected"])
            self.assert_clean(session)

    def test_native_finite_machine_root_host_binds_actual_initial_inputs(self):
        self.add("Makefile", ".PHONY: all\nall: ; @:\n")
        body = (
            "finish=guard.NativeReadTrace.finish\n"
            "def finite_finish(self):\n"
            " self.retire_root()\n"
            " return finish(self)\n"
            "guard.NativeReadTrace.finish=finite_finish\n"
        )
        for mutation in (
            "root['argv'].append('FOREIGN=1')",
            "root['environment']['FOREIGN']='1'",
            "root['cwd']='/'",
        ):
            session = self.session()
            read = session.budget.read_bytes
            def mutated(path, category):
                data = read(path, category)
                if category == "control" and Path(path).name.startswith("report-"):
                    row = json.loads(data)
                    root = row["read_trace"]["machine"]["roots"][0]["initial"]
                    exec(mutation, {}, {"root": root})
                    return encoded(row)
                return data
            with self.subTest(mutation=mutation), self.native_supervisor(body), session, patch.object(
                session.budget, "read_bytes", mutated,
            ):
                with self.assertRaisesRegex(MakeProbeError, "machine root inputs"):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, observe_root=True,
                    )
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)

    def test_native_root_input_decoder_and_option_refuse_incomplete_contracts(self):
        from scripts.validation_ownership import read_epochs
        self.assertEqual(read_epochs.native_root_inputs(b"make\0all\0", b"A=one=two\0B=\0"),
                         (["make", "all"], {"A": "one=two", "B": ""}))
        for command, environment in (
            (b"", b"A=1\0"), (b"make", b"A=1\0"), (b"make\0", b"A=1"),
            (b"make\0", b"A=1\0A=2\0"), (b"make\0", b"=1\0"),
            (b"make\0", b"A\0"), (b"\xff\0", b"A=1\0"),
        ):
            with self.subTest(command=command, environment=environment), self.assertRaises(MakeProbeError):
                read_epochs.native_root_inputs(command, environment)
        self.add("Makefile", "all: ; @:\n")
        for options in ({"observe_root": 1}, {"observe_root": True},
                        {"observe_root": True, "observe_reads": True}):
            session = self.session()
            with self.subTest(options=options), session:
                before = session.budget.runs
                with self.assertRaisesRegex(MakeProbeError, "invalid native read observation"):
                    session._native_make_readonly("all", **options)
                self.assertEqual(session.budget.runs, before)
            self.assert_clean(session)

    def test_native_root_capture_keeps_exact_existing_metadata_budget(self):
        self.add("Makefile", ".PHONY: all\nall: ; @:\n")
        for insufficient in (False, True):
            receipt = self.directory / ("root-charge-" + str(insufficient) + ".json")
            body = (
                "original=guard.Policy.observe_native_root\n"
                "def captured(self,pid):\n"
                " command=Path(f'/proc/{pid}/cmdline').read_bytes()\n"
                " environment=Path(f'/proc/{pid}/environ').read_bytes()\n"
                " argv,values=guard.read_epochs.native_root_inputs(command,environment)\n"
                " row={'version':1,'pid':pid,'argv':argv,'cwd':self.config.get('cwd','/repo'),"
                "'environment':values,'exit_stop':None,'wait':None}\n"
                " size=len(command)+len(environment)+256+128+len(guard.encoded(row))\n"
                " before=self.observation_bytes;limit=self.config['observation_limit']\n"
                f" self.config['observation_limit']=before+size-int({insufficient!r})\n"
                " try:return original(self,pid)\n"
                " finally:\n"
                f"  Path({str(receipt)!r}).write_text(json.dumps({{'expected':size,'charged':self.observation_bytes-before,'limit':self.config['observation_limit']-before}}))\n"
                "  self.config['observation_limit']=limit\n"
                "guard.Policy.observe_native_root=captured\n"
            )
            session = self.session()
            with self.subTest(insufficient=insufficient), self.native_supervisor(body), session:
                if insufficient:
                    with self.assertRaisesRegex(MakeProbeError, "metadata observation byte budget exhausted"):
                        session._native_make_readonly(
                            "all", observe_reads=True, observe_runtime_completions=True, observe_root=True,
                        )
                    self.assertTrue(session.budget.failed)
                else:
                    completed, _, _ = session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, observe_root=True,
                    )
                    self.assertEqual(completed.returncode, 0)
                actual = json.loads(receipt.read_bytes())
                self.assertEqual(actual["charged"], actual["expected"])
                self.assertEqual(actual["limit"], actual["expected"] - int(insufficient))
            self.assert_clean(session)

    def test_native_make_root_kernel_terminal_matches_actual_wait(self):
        self.add("Makefile", ".PHONY: all failed\nall: ; @:\nfailed: ; @exit 3\n")
        receipt = self.directory / "make-root-terminal.json"
        body = (
            "original=guard.Policy.native_sigkill_exit\n"
            "waitpid=guard.os.waitpid\nroots={}\n"
            "def outcome(self,pid,state,status):\n"
            " result=original(self,pid,state,status)\n"
            " if state.role=='make':\n"
            "  roots[pid]={'pid':pid,'stop':status,'stored':state.native_exit_status}\n"
            " return result\n"
            "def waited(*args):\n"
            " pid,status=waitpid(*args)\n"
            " if pid in roots and (os.WIFEXITED(status) or os.WIFSIGNALED(status)):\n"
            "  roots[pid]['wait']=status\n"
            f"  Path({str(receipt)!r}).write_text(json.dumps(roots[pid]))\n"
            " return pid,status\n"
            "guard.Policy.native_sigkill_exit=outcome;guard.os.waitpid=waited\n"
        )
        for target, status in (("all", 0), ("failed", 2 << 8)):
            session = self.session(seconds=10)
            with self.subTest(target=target), self.native_supervisor(body), session:
                if target == "failed":
                    with self.assertRaisesRegex(MakeProbeError, "native GNU Make failed: 2"):
                        session._native_make_readonly(
                            target, observe_reads=True, observe_runtime_completions=True,
                        )
                else:
                    completed, _, observed = session._native_make_readonly(
                        target, observe_reads=True, observe_runtime_completions=True,
                    )
                    self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, b"", b""))
                    self.assertTrue(observed["read_trace"]["machine"]["closed"])
                actual = json.loads(receipt.read_bytes())
                self.assertGreater(actual["pid"], 0)
                self.assertEqual(
                    {key: actual[key] for key in ("stop", "stored", "wait")},
                    dict.fromkeys(("stop", "stored", "wait"), status),
                )
            self.assert_clean(session)

    def test_native_make_root_missing_changed_and_reused_terminal_refuse(self):
        self.add("Makefile", ".PHONY: all\nall: ; @:\n")
        receipt = self.directory / "make-root-terminal-mutation.json"
        for mode in ("missing", "changed", "untyped", "reused"):
            body = (
                "original=guard.Policy.native_sigkill_exit\n"
                "entry=guard.Policy.entry\n"
                "def entered(self,pid,state,r):\n"
                " result=entry(self,pid,state,r)\n"
                f" if {mode!r}=='reused' and state.role=='make' and r.orig_rax==231:\n"
                "  state.native_exit_status=0\n"
                f"  Path({str(receipt)!r}).write_text(json.dumps({{'pid':pid,'syscall':r.orig_rax}}))\n"
                " return result\n"
                "def outcome(self,pid,state,status):\n"
                " result=original(self,pid,state,status)\n"
                " if state.role=='make':\n"
                f"  Path({str(receipt)!r}).write_text(json.dumps({{'pid':pid,'stop':status}}))\n"
                f"  state.native_exit_status=None if {mode!r}=='missing' else False if {mode!r}=='untyped' else 1<<16\n"
                " return result\n"
                "guard.Policy.entry=entered;guard.Policy.native_sigkill_exit=outcome\n"
            )
            session = self.session(seconds=10)
            with self.subTest(mode=mode), self.native_supervisor(body), session:
                with self.assertRaisesRegex(
                    MakeProbeError, "reused its terminal kernel exit stop" if mode == "reused"
                    else "terminal status differs from its actual kernel exit stop",
                ):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True,
                    )
                actual = json.loads(receipt.read_bytes())
                self.assertGreater(actual["pid"], 0)
                self.assertEqual(actual.get("syscall") if mode == "reused" else actual["stop"],
                                 231 if mode == "reused" else 0)
                self.assertTrue(session.budget.failed)
            self.assert_clean(session)

    def test_native_register_workspace_reuses_storage_and_preserves_kernel_guards(self):
        self.add("Makefile", (
            "".join("V%d := value%d\n" % (index, index) for index in range(20))
            + "all:\n\t@printf final > result; printf once\n"
        ))
        for negative, expected in (
            ("none", None),
            ("callback", "original read callback changed its register state"),
            ("restoration", "original read register restoration failed kernel readback"),
        ):
            report = self.directory / ("register-captures-" + negative + ".json")
            body = """
import ctypes
held=[]
captures=[]
active=False
register_reads=0
original_ptrace=guard.ptrace
def measured_ptrace(request,pid,address=0,data=0):
 global register_reads
 result=original_ptrace(request,pid,address,data)
 if active and request in {guard.GETREGS,0x4202}:
  value=data._obj
  held.append(value)
  kind='register' if request==guard.GETREGS else 'siginfo'
  captures.append([kind,id(value)])
  if kind=='register':
   register_reads+=1
   if negative=='restoration' and register_reads==2:
    value.rbx^=1
 return result
guard.ptrace=measured_ptrace
original_trap=guard.NativeReadTrace.trap
def measured_trap(self,*args):
 global active,register_reads
 active=True
 register_reads=0
 try:
  return original_trap(self,*args)
 finally:
  active=False
guard.NativeReadTrace.trap=measured_trap
if negative=='callback':
 original_caller=guard.NativeReadTrace.caller
 def changed_caller(self,registers,target):
  result=original_caller(self,registers,target)
  registers.rbx^=1
  return result
 guard.NativeReadTrace.caller=changed_caller
original_supervise=guard.supervise
def measured_supervise(*args,**kwargs):
 try:
  return original_supervise(*args,**kwargs)
 finally:
  Path(report).write_text(json.dumps(captures))
guard.supervise=measured_supervise
"""
            body = "negative=" + repr(negative) + "\nreport=" + repr(str(report)) + "\n" + body
            session = self.session()
            with self.subTest(negative=negative), session, self.native_supervisor(body):
                request = dict(
                    outputs=("result",), observe_reads=True, observe_runtime_completions=True,
                    commands={
                        ("/bin/sh", "-c", "printf final > result; printf once"): Command(
                            ("/bin/sh", "-c", "printf final > result; printf once"),
                            outputs=("result",),
                        ),
                    },
                )
                if expected is not None:
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session._native_make_writable("all", **request)
                else:
                    completed, _, observed, generated = session._native_make_writable("all", **request)
                    self.assertEqual(
                        (completed.returncode, completed.stdout, completed.stderr), (0, b"once", b""),
                    )
                    self.assertEqual(
                        [(row.path, row.data, row.mode) for row in generated],
                        [("result", b"final", 0o644)],
                    )
                    captures = json.loads(report.read_text())
                    siginfo = [row[1] for row in captures if row[0] == "siginfo"]
                    registers = [row[1] for row in captures if row[0] == "register"]
                    traps = [
                        row for row in observed["read_trace"]["machine"]["events"]
                        if row["kind"] == "trap"
                    ]
                    self.assertGreater(len(traps), 20)
                    self.assertEqual(len(siginfo), len(traps))
                    self.assertEqual(len(registers), 2 * len(traps))
                    self.assertEqual(len(set(siginfo)), 1)
                    self.assertEqual(len(set(registers)), 2)
            self.assert_clean(session)
            report.unlink()

    def test_native_read_abi_reuses_exact_image_without_disassembly_replay(self):
        from scripts.validation_ownership import read_epochs
        budget = ProbeBudget()
        self.add("Makefile", "VALUE := original\nall: ; @:\n")
        loader = foundation.FoundationTests.capture_view(self, budget)
        session = make_probe.ProbeSession(loader, scratch_root=self.scratch, budget=budget)
        with session:
            start = session.budget.runs
            plain = session._native_read_abi()
            self.assertEqual(session.budget.runs - start, 2)
            complete = session._native_read_abi(completions=True)
            self.assertEqual(session.budget.runs - start, 5)
            original = json.loads(json.dumps(complete))
            complete["globals"].clear()
            with patch.object(session.budget, "run", side_effect=AssertionError("disassembly replay")):
                self.assertEqual(session._native_read_abi(), plain)
                self.assertEqual(session._native_read_abi(completions=True), original)
                with session.select_view(session.loader):
                    self.assertEqual(session._native_read_abi(completions=True), original)
                self.assertEqual(session._native_read_abi(completions=True), original)
            self.assertEqual(session.budget.runs - start, 5)
            key = next(key for key in session.native_read_abis if key[1])
            invalid = json.loads(session.native_read_abis[key])
            invalid["image_sha256"] = "0" * 64
            session.native_read_abis[key] = encoded(invalid)
            with self.assertRaises(read_epochs.ReadEpochError):
                session._native_read_abi(completions=True)
        self.assertEqual(session.native_read_abis, {})
        self.assert_clean(session)

    def test_native_close_range_preserves_outside_and_failed_bindings(self):
        self.add("input", "data")
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <unistd.h>\n"
            "#include <sys/syscall.h>\n#include <poll.h>\n#include <errno.h>\n"
            "int main(void){int fd;char byte;struct pollfd closed[2]={{80,POLLIN,0},{81,POLLIN,0}};"
            "fd=open(\"/repo/input\",O_RDONLY);if(fd<0)return 1;"
            "if(dup2(fd,80)!=80||dup2(fd,81)!=81||dup2(fd,82)!=82||close(fd))return 2;"
            "if(syscall(SYS_close_range,82U,80U,0U)!=-1||errno!=EINVAL)return 3;"
            "if(read(82,&byte,1)!=1||byte!='d')return 4;"
            "if(syscall(SYS_close_range,80U,81U,0U))return 5;"
            "if(poll(closed,2,0)!=2||closed[0].revents!=POLLNVAL||closed[1].revents!=POLLNVAL)return 6;"
            "if(read(82,&byte,1)!=1||byte!='a'||close(82))return 7;"
            "fd=open(\"/repo/result\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"ok\",2)!=2||close(fd))return 8;return 0;}\n"
        ))
        self.add("Makefile", "all:\n\t@/native/tool\n")
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool, sources=("input",), outputs=("result",))
            completed, _, _, generated = session._native_make_writable(
                "all", outputs=("result",), native_tool=tool, commands=Commands(),
                observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
            self.assertEqual([(row.data, row.mode) for row in generated], [(b"ok", 0o644)])
        self.assert_clean(session)

    def test_native_close_range_refuses_managed_descriptions_and_flags(self):
        self.add("input", "data")
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <unistd.h>\n"
            "#include <sys/syscall.h>\n#include <stdlib.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){int fd;unsigned int flags;"
            "if(argc!=2)return 1;flags=(unsigned int)strtoul(argv[1],0,10);"
            "if(!strcmp(argv[1],\"generated\"))fd=open(\"/repo/result\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "else fd=open(\"/repo/input\",O_RDONLY);"
            "if(fd<0||dup2(fd,80)!=80||close(fd))return 2;"
            "return syscall(SYS_close_range,80U,80U,flags)?3:0;}\n"
        ))
        for mode in ("generated", "2", "4", "8"):
            with self.subTest(mode=mode):
                self.add("Makefile", "all:\n\t@/native/tool " + mode + "\n")
                session = self.session()
                with session:
                    tool = session.compile_native(("native.c",))
                    class Commands:
                        def __getitem__(self, argv):
                            return Command(argv, native_tool=tool, sources=("input",), outputs=("result",))
                    expected = "generated output descriptor" if mode == "generated" else "close_range flags"
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session._native_make_writable(
                            "all", outputs=("result",), native_tool=tool, commands=Commands(),
                            observe_reads=True, observe_runtime_completions=True,
                        )
                self.assert_clean(session)

    def test_native_close_range_process_bindings_retire_only_on_success(self):
        from unittest.mock import Mock
        from scripts.validation_ownership.syscall_guard import Process, Registers, Violation
        policy = foundation.FoundationTests.observation_policy(self, mode="make")
        policy.config.update(syscall_limit=100, write_limit=1)
        policy.native_readonly = True
        for result in (-errno.EINVAL, 0, 1):
            with self.subTest(result=result):
                state = Process("native", fds={2: "<stderr>", 80: "/repo/input", 81: "/repo/input", 82: "/repo/input"})
                policy.read_trace = Mock()
                registers = Registers(
                    orig_rax=436, rdi=(1 << 32) | 80, rsi=(1 << 32) | 81, rdx=1 << 32,
                )
                policy.entry(1, state, registers)
                registers.rax = result
                if result == 1:
                    with self.assertRaisesRegex(Violation, "invalid successful result"):
                        policy.leave(1, state, registers)
                else:
                    policy.leave(1, state, registers)
                self.assertEqual(set(state.fds), {2, 82} if result == 0 else {2, 80, 81, 82})
                self.assertEqual(
                    [call.args for call in policy.read_trace.fd_closed.call_args_list],
                    [(1, 80), (1, 81)] if result == 0 else [],
                )
        for role in ("make", "compiler", "command", "helper"):
            with self.subTest(role=role):
                with self.assertRaisesRegex(Violation, "admitted native image"):
                    policy.entry(1, Process(role), Registers(orig_rax=436, rdi=80, rsi=81))

    def _inherited_lock_range_fixture(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <unistd.h>\n"
            "#include <sys/syscall.h>\n#include <sys/file.h>\n#include <sys/wait.h>\n"
            "#include <poll.h>\n#include <errno.h>\n#include <stdlib.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){int fd,other,status;pid_t child;"
            "struct pollfd closed[2]={{80,POLLIN,0},{82,POLLIN,0}};"
            "if(argc!=2&&argc!=3)return 1;if(argc==3){"
            "if(!strcmp(argv[1],\"independent\")){other=open(\"/repo/shared.lock\",O_RDWR);"
            "if(other<0)return 5;return syscall(SYS_close_range,(unsigned)other,(unsigned)other,0U)?6:0;}"
            "other=open(\"/repo/result\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(other<0||dup2(other,81)!=81||close(other))return 7;"
            "return syscall(SYS_close_range,!strcmp(argv[1],\"writer\")?81U:80U,82U,0U)?8:0;}"
            "fd=open(\"/repo/shared.lock\",O_CREAT|O_RDWR,0600);"
            "if(fd<0||flock(fd,LOCK_EX)||dup2(fd,80)!=80||dup2(fd,82)!=82"
            "||dup2(fd,84)!=84)return 2;"
            "if(!strcmp(argv[1],\"last\"))return syscall(SYS_close_range,80U,82U,0U)?3:0;"
            "child=fork();if(child<0)return 4;if(!child){"
            "if(!strcmp(argv[1],\"independent\")||!strcmp(argv[1],\"mixed\")"
            "||!strcmp(argv[1],\"writer\")){char *next[]={\"/native/tool\",argv[1],\"child\",0};"
            "execv(next[0],next);return 17;}"
            "if(strcmp(argv[1],\"ok\"))return syscall(SYS_close_range,80U,82U,"
            "(unsigned)strtoul(argv[1],0,10))?9:0;"
            "if(syscall(SYS_close_range,82U,80U,0U)!=-1||errno!=EINVAL"
            "||fcntl(80,F_GETFD)<0||fcntl(82,F_GETFD)<0)return 10;"
            "if(syscall(SYS_close_range,80U,82U,0U))return 11;"
            "if(poll(closed,2,0)!=2||closed[0].revents!=POLLNVAL"
            "||closed[1].revents!=POLLNVAL||fcntl(84,F_GETFD)<0"
            "||close(84)||close(fd))return 12;return 0;}"
            "if(waitpid(child,&status,0)!=child||!WIFEXITED(status)||WEXITSTATUS(status))return 13;"
            "other=open(\"/repo/shared.lock\",O_RDWR);"
            "if(other<0||flock(other,LOCK_EX|LOCK_NB)!=-1||errno!=EWOULDBLOCK||close(other))return 14;"
            "if(fcntl(80,F_GETFD)<0||fcntl(82,F_GETFD)<0||fcntl(84,F_GETFD)<0"
            "||flock(fd,LOCK_UN)||close(80)||close(82)||close(84)||close(fd))return 15;"
            "fd=open(\"/repo/result\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"ok\",2)!=2||close(fd))return 16;return 0;}\n"
        ))

    def test_native_close_range_retires_only_inherited_lock_aliases(self):
        from scripts.validation_ownership import read_epochs
        self._inherited_lock_range_fixture()
        self.add("Makefile", "all:\n\t@/native/tool ok\n")
        resources = (("shared-lock", "shared.lock"),)
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    return Command(argv, native_tool=tool, outputs=("result",), native_resources=resources)
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=("result",), native_tool=tool, commands=Commands(),
                native_resources=resources, observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"", b""))
            self.assertEqual([(row.data, row.mode) for row in generated], [(b"ok", 0o644)])
            trace = observed["read_trace"]
            closures = [
                row for row in read_epochs.native_output_effects(trace)
                if row["kind"] == "output-range-close"
            ]
            self.assertEqual([row["fd"] for row in closures], [80, 82])
            self.assertEqual(len({row["description"] for row in closures}), 1)
            for mutation in ("first", "last", "flags", "result", "fd", "serial", "description", "missing"):
                with self.subTest(mutation=mutation):
                    invalid = json.loads(json.dumps(trace))
                    row = next(
                        row for row in invalid["machine"]["events"]
                        if row["kind"] == "native-tree" and row["event"]["kind"] == "close-range"
                    )
                    event = row["event"]
                    if mutation == "missing":
                        event["bindings"].pop()
                    elif mutation in {"fd", "serial", "description"}:
                        event["bindings"][0][mutation] += 1
                    else:
                        event[mutation] = {"first": 81, "last": 81, "flags": 4, "result": 1}[mutation]
                    row["sha256"] = hashlib.sha256(encoded(event)).hexdigest()
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            invalid = json.loads(json.dumps(trace))
            row = next(
                row for row in invalid["machine"]["events"]
                if row["kind"] == "native-output" and row["event"]["kind"] == "output-range-close"
            )
            row["event"]["kind"] = "output-close"
            row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "descriptor retirement"):
                read_epochs.validate_trace(
                    invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                )
            for actor in ([], {}, True, None):
                with self.subTest(retirement_actor=actor):
                    invalid = json.loads(json.dumps(trace))
                    row = next(
                        row for row in invalid["machine"]["events"]
                        if row["kind"] == "native-output" and row["event"]["kind"] == "output-close"
                    )
                    row["event"]["pid"] = actor
                    row["sha256"] = hashlib.sha256(encoded(row["event"])).hexdigest()
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
        self.assert_clean(session)

    def test_native_close_range_refuses_noninherited_and_mixed_output_ranges(self):
        self._inherited_lock_range_fixture()
        resources = (("shared-lock", "shared.lock"),)
        for mode in ("last", "independent", "writer", "mixed", "2", "4", "8"):
            with self.subTest(mode=mode):
                self.add("Makefile", "all:\n\t@/native/tool " + mode + "\n")
                session = self.session()
                with session:
                    tool = session.compile_native(("native.c",))
                    class Commands:
                        def __getitem__(self, argv):
                            return Command(
                                argv, native_tool=tool, outputs=("result",), native_resources=resources,
                            )
                    expected = "close_range flags" if mode.isdigit() else "generated output descriptor"
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session._native_make_writable(
                            "all", outputs=("result",), native_tool=tool, commands=Commands(),
                            native_resources=resources, observe_reads=True, observe_runtime_completions=True,
                        )
                self.assert_clean(session)

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
            "if(!strcmp(mode,\"foreign-untracked-lock\")){other=open(\"/repo/Makefile\",O_RDONLY);"
            "return other<0||flock(other,LOCK_EX|LOCK_NB)||flock(other,LOCK_UN)||close(other)||close(fd)||close(lock);}"
            "if(!strcmp(mode,\"foreign-readonly-lock\")||!strcmp(mode,\"own-readonly-lock\")){"
            "other=open(\"/repo/stage/lock\",O_RDONLY|O_NOFOLLOW);"
            "return other<0||flock(other,LOCK_EX|LOCK_NB)||flock(other,LOCK_UN)||close(other)||close(fd)||close(lock);}"
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

    def test_native_shared_lock_new_readonly_description_requires_current_image_role(self):
        outputs, resources = self._native_image_operand_fixture()
        for mode in ("foreign-readonly-lock", "foreign-untracked-lock", "own-readonly-lock"):
            self.add("Makefile", "all:\n\t@MODE=" + mode + " /native/tool\n")
            receipt = self.directory / ("lock-result-" + mode + ".json")
            body = (
                "original=guard.Policy.leave\n"
                "def returned(self,pid,state,r):\n"
                " if state.role=='native' and state.kernel_call==73 and state.native_execs==1:\n"
                "  inputs=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\\0')\n"
                "  item=self.native_outputs.custody.descriptors.get((pid,guard.ctypes.c_int(r.rdi).value))\n"
                "  description=None if item is None else item.descriptions[(pid,guard.ctypes.c_int(r.rdi).value)]\n"
                "  if (description is None or not any(owner!=pid for owner,fd in description.bindings)) and len(inputs)>1 and inputs[1] in (b'foreign-readonly-lock',b'foreign-untracked-lock',b'own-readonly-lock'):\n"
                f"   Path({str(receipt)!r}).write_text(json.dumps({{'pid':pid,'result':guard.signed(r.rax),'resources':state.native_admission['resources']}}))\n"
                " return original(self,pid,state,r)\n"
                "guard.Policy.leave=returned\n"
            )
            session = self.session()
            with self.subTest(mode=mode), self.native_supervisor(body), session:
                tool = session.compile_native(("native.c",))
                class Commands:
                    def __getitem__(self, argv):
                        helper = len(argv) == 4
                        return Command(
                            argv, native_tool=tool, outputs=() if helper else outputs,
                            native_resources=(("shared-lock", "stage/lock"),) if helper and mode == "own-readonly-lock"
                            else () if helper else resources,
                        )
                def run():
                    return session._native_make_writable(
                        "all", outputs=outputs, native_resources=resources, native_tool=tool,
                        commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                    )
                if mode.startswith("foreign-"):
                    with self.assertRaises(MakeProbeError) as refused:
                        run()
                    self.assertFalse(
                        receipt.exists(), receipt.read_text() if receipt.exists()
                        else "undeclared image reached an actual flock return",
                    )
                    self.assertIn(
                        "admitted or inherited description" if mode == "foreign-untracked-lock"
                        else "issued output authority", str(refused.exception),
                    )
                    self.assertTrue(session.budget.failed)
                else:
                    completed, _, _, generated = run()
                    self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, b"", b""))
                    actual = json.loads(receipt.read_bytes())
                    self.assertEqual(actual["result"], 0)
                    self.assertEqual(actual["resources"], [["shared-lock", "stage/lock"]])
                    self.assertEqual({row.path: row.data for row in generated},
                                     {"stage/first": b"inherited", "stage/second": b""})
            self.assert_clean(session)

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
                        image_record = next((
                            row for row in invalid["machine"]["events"]
                            if row["kind"] == "native-tree" and row["dispatch"] == 1
                            and row["event"]["kind"] == "exec" and len(row["event"]["argv"]) == 4
                        ), None)
                        if image_record is None:
                            continue
                        image = image_record["event"]
                        image["admission"][field] = value
                        image_record["sha256"] = hashlib.sha256(encoded(image)).hexdigest()
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
                "  for row in trace['machine']['events']:\n"
                "   if row['kind']=='native-tree' and row['event']['kind']=='exec'"
                " and row['event']['pid']==image['pid'] and row['event']['generation']==image['generation']:\n"
                "    row['event']['admission']=admission.copy();row['sha256']=guard.hashlib.sha256(guard.encoded(row['event'])).hexdigest()\n"
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
                            "actual pathname dispatch",
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

    def test_native_readonly_consumer_opens_settled_foreign_output_without_write_plan(self):
        self._readonly_consumer_fixture(with_directory=True)

    def test_native_resource_free_readonly_consumer_binds_actual_actor_and_creator(self):
        self._readonly_consumer_fixture(with_directory=False)

    def _readonly_consumer_fixture(self, *, with_directory):
        from scripts.validation_ownership import read_epochs
        path = "stage/result" if with_directory else "result"
        resources = (("directory", "stage"),) if with_directory else ()
        mkdir = 'if(mkdir("/repo/stage",0700))return 2;' if with_directory else ""
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <unistd.h>\n"
            "#include <sys/stat.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){int fd;char data[16];ssize_t count;"
            "if(argc!=2)return 1;if(!strcmp(argv[1],\"create\")){"
            + mkdir +
            "fd=open(\"/repo/" + path + "\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"produced\",8)!=8||close(fd))return 3;return 0;}"
            "fd=open(\"/repo/" + path + "\",O_RDONLY);if(fd<0)return 4;"
            "count=read(fd,data,sizeof(data));if(count!=8||write(1,data,count)!=count||close(fd))return 5;"
            "return 0;}\n"
        ))
        self.add("Makefile", (
            "all: " + path + "\n\t@/native/tool read\n"
            + path + ":\n\t@/native/tool create\n"
        ))
        session = self.session()
        with session:
            tool = session.compile_native(("native.c",))
            class Commands:
                def __getitem__(self, argv):
                    if argv[-1] == "create":
                        return Command(
                            argv, native_tool=tool, outputs=(path,), native_resources=resources,
                        )
                    return Command(argv, native_tool=tool)
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=(path,), native_resources=resources,
                native_tool=tool, commands=Commands(), observe_reads=True,
                observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (b"produced", b""))
            self.assertEqual([(row.path, row.data, row.mode) for row in generated], [
                (path, b"produced", 0o644),
            ])
            trace = observed["read_trace"]
            opened = [row for row in read_epochs.native_output_effects(trace) if row["kind"] == "output-open"]
            writer, reader = opened
            self.assertTrue(writer["writing"])
            self.assertFalse(reader["writing"])
            self.assertEqual((reader["owner"], reader["serial"]), (writer["owner"], writer["serial"]))
            self.assertNotEqual(reader["operation_owner"], reader["owner"])
            self.assertNotEqual(reader["description"], writer["description"])
            for field, value in (
                ("writing", True), ("writing", 0), ("serial", 999), ("path", "/repo/other"),
                ("owner", reader["operation_owner"]), ("revision", reader["revision"] + 1),
                ("identity", [0] * 7),
            ):
                invalid = json.loads(json.dumps(trace))
                event = next(
                    row for row in invalid["machine"]["events"]
                    if row["kind"] == "native-output" and row["event"]["sequence"] == reader["sequence"]
                )
                event["event"][field] = value
                event["sha256"] = hashlib.sha256(encoded(event["event"])).hexdigest()
                with self.subTest(field=field), self.assertRaises(read_epochs.ReadEpochError):
                    read_epochs.validate_trace(
                        invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                    )
            parents = [
                row for row in read_epochs.native_output_effects(trace)
                if row["kind"] == "output-directory-change" and row["sequence"] > reader["sequence"]
            ]
            self.assertEqual(len(parents), int(with_directory))
            for parent in parents:
                changed_identity = list(parent["identity"])
                changed_identity[4] += 1
                for field, value in (
                    ("identity", changed_identity), ("before", changed_identity),
                    ("entries", []), ("source", "/repo/stage/other"),
                    ("operation", "remove"), ("destination", "/repo/stage/other"),
                ):
                    invalid = json.loads(json.dumps(trace))
                    event = next(
                        row for row in invalid["machine"]["events"]
                        if row["kind"] == "native-output" and row["event"]["sequence"] == parent["sequence"]
                    )
                    event["event"][field] = value
                    event["sha256"] = hashlib.sha256(encoded(event["event"])).hexdigest()
                    with self.subTest(parent_field=field), self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
        self.assert_clean(session)

    def test_native_foreign_consumer_write_flags_still_refuse_without_output_plan(self):
        self._foreign_consumer_write_flags(with_directory=True)

    def test_native_resource_free_foreign_consumer_write_flags_still_refuse(self):
        self._foreign_consumer_write_flags(with_directory=False)

    def test_native_foreign_readonly_consumer_handles_actual_eacces_with_and_without_resources(self):
        from scripts.validation_ownership import read_epochs
        for with_directory in (False, True):
            with self.subTest(with_directory=with_directory):
                path = "stage/result" if with_directory else "result"
                resources = (("directory", "stage"),) if with_directory else ()
                mkdir = 'if(mkdir("/repo/stage",0700))return 2;' if with_directory else ""
                self.add("native.c", (
                    "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <unistd.h>\n"
                    "#include <sys/stat.h>\n#include <string.h>\n#include <errno.h>\n"
                    "int main(int argc,char **argv){int fd;if(argc!=2)return 1;"
                    "if(!strcmp(argv[1],\"create\")){" + mkdir +
                    "fd=open(\"/repo/" + path + "\",O_CREAT|O_EXCL|O_WRONLY,0000);"
                    "if(fd<0||write(fd,\"produced\",8)!=8||close(fd))return 3;return 0;}"
                    "fd=open(\"/repo/" + path + "\",O_RDONLY);"
                    "if(fd!=-1||errno!=EACCES)return 4;return write(1,\"denied\",6)!=6;}\n"
                ))
                self.add("Makefile", (
                    "all: " + path + "\n\t@/native/tool read\n"
                    + path + ":\n\t@/native/tool create\n"
                ))
                session = self.session()
                with session:
                    tool = session.compile_native(("native.c",))
                    class Commands:
                        def __getitem__(self, argv):
                            return Command(
                                argv, native_tool=tool,
                                outputs=(path,) if argv[-1] == "create" else (),
                                native_resources=resources if argv[-1] == "create" else (),
                            )
                    original = session._sandbox_run
                    returned = {}
                    def capture(*args, **kwargs):
                        result, observation = original(*args, **kwargs)
                        returned.update(completed=result, observed=observation)
                        return result, observation
                    with patch.object(session, "_sandbox_run", side_effect=capture):
                        with self.assertRaises(PermissionError):
                            session._native_make_writable(
                                "all", outputs=(path,), native_resources=resources,
                                native_tool=tool, commands=Commands(), observe_reads=True,
                                observe_runtime_completions=True,
                            )
                    completed, observed = returned["completed"], returned["observed"]
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual((completed.stdout, completed.stderr), (b"denied", b""))
                    trace = observed["read_trace"]
                    settled = [
                        row for row in read_epochs.native_output_effects(trace)
                        if row["kind"] == "output-settled" and row["path"] == "/repo/" + path
                    ]
                    self.assertTrue(settled)
                    self.assertEqual(settled[-1]["sha256"], hashlib.sha256(b"produced").hexdigest())
                    self.assertEqual(settled[-1]["identity"][2] & 0o777, 0)
                    failed, = [
                        row for row in read_epochs.native_output_effects(trace)
                        if row["kind"] == "output-operation-failed"
                    ]
                    self.assertEqual((failed["result"], failed["flags"]), (-errno.EACCES, os.O_RDONLY))
                    for field, value in (
                        ("flags", os.O_WRONLY), ("flags", os.O_RDWR),
                        ("flags", os.O_CREAT), ("flags", os.O_TRUNC), ("flags", True),
                        ("owner", 1), ("source", "/repo/other"), ("result", 0),
                    ):
                        invalid = json.loads(json.dumps(trace))
                        event = next(
                            row for row in invalid["machine"]["events"]
                            if row["kind"] == "native-output" and row["event"]["sequence"] == failed["sequence"]
                        )
                        event["event"][field] = value
                        event["sha256"] = hashlib.sha256(encoded(event["event"])).hexdigest()
                        with self.subTest(field=field), self.assertRaises(read_epochs.ReadEpochError):
                            read_epochs.validate_trace(
                                invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                            )
                self.assert_clean(session)

    def _foreign_consumer_write_flags(self, *, with_directory):
        path = "stage/result" if with_directory else "result"
        resources = (("directory", "stage"),) if with_directory else ()
        mkdir = 'if(mkdir("/repo/stage",0700))return 2;' if with_directory else ""
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <unistd.h>\n"
            "#include <sys/stat.h>\n#include <string.h>\n#include <stdlib.h>\n"
            "int main(int argc,char **argv){int fd;if(argc!=2)return 1;"
            "if(!strcmp(argv[1],\"create\")){" + mkdir +
            "fd=open(\"/repo/" + path + "\",O_CREAT|O_EXCL|O_WRONLY,0644);"
            "if(fd<0||write(fd,\"produced\",8)!=8||close(fd))return 3;return 0;}"
            "fd=open(\"/repo/" + path + "\",atoi(argv[1]),0644);"
            "if(fd<0)return 4;return write(fd,\"changed!\",8)!=8;}\n"
        ))
        for flags in (os.O_WRONLY, os.O_RDWR, os.O_RDONLY | os.O_TRUNC, os.O_RDONLY | os.O_CREAT):
            with self.subTest(flags=flags):
                self.add("Makefile", (
                    "all: " + path + "\n\t@/native/tool " + str(flags) + "\n"
                    + path + ":\n\t@/native/tool create\n"
                ))
                session = self.session()
                with session:
                    tool = session.compile_native(("native.c",))
                    class Commands:
                        def __getitem__(self, argv):
                            if argv[-1] == "create":
                                return Command(
                                    argv, native_tool=tool, outputs=(path,), native_resources=resources,
                                )
                            return Command(argv, native_tool=tool)
                    with self.assertRaisesRegex(MakeProbeError, "native job write lacks its issued output authority"):
                        session._native_make_writable(
                            "all", outputs=(path,), native_resources=resources,
                            native_tool=tool, commands=Commands(), observe_reads=True,
                            observe_runtime_completions=True,
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


    def test_native_job_report_canonical_success_preserves_failed_and_readonly_packets(self):
        from unittest.mock import patch
        from scripts.validation_ownership import read_epochs

        for writable, status in ((True, 0), (True, 1), (False, 0)):
            with self.subTest(writable=writable, status=status):
                recipe = ("printf final > result; " if writable else "") + (
                    "v=once; printf '%s' \"$$v\"; exit " + str(status)
                )
                self.add("Makefile", "all:\n\t@" + recipe + "\n")
                requests, reports = [], []
                original_parse = make_probe.parse_json
                def capture(raw, label):
                    value = original_parse(raw, label)
                    if label == "supervisor JSON":
                        reports.append(value)
                    return value
                class Commands:
                    def __getitem__(self, argv):
                        requests.append(argv)
                        return Command(argv, outputs=("result",) if writable else ())
                session = self.session()
                with session, patch.object(make_probe, "parse_json", capture):
                    kwargs = dict(
                        observe_reads=True, observe_runtime_completions=True, commands=Commands(),
                    )
                    run = session._native_make_writable if writable else session._native_make_readonly
                    if writable:
                        kwargs["outputs"] = ("result",)
                    if status:
                        with self.assertRaises(MakeProbeError):
                            run("all", **kwargs)
                    else:
                        if writable:
                            completed, _, _, generated = run("all", **kwargs)
                        else:
                            completed, _, _ = run("all", **kwargs)
                            generated = ()
                        self.assertEqual(completed.stdout, b"once")
                        self.assertEqual(completed.stderr, b"")
                        self.assertEqual(
                            [(row.path, row.data, row.mode) for row in generated],
                            [("result", b"final", 0o644)] if writable else [],
                        )
                self.assertEqual(len(requests), 1)
                report, = reports
                packets = [
                    parse_json(value.removeprefix("native-job:").encode(), "native job packet")
                    for value in report["accessed"] if value.startswith("native-job:")
                ]
                if writable and status == 0:
                    self.assertEqual(report["read_trace"]["version"], read_epochs.WRITABLE_VERSION)
                    self.assertFalse(packets)
                    self.assertFalse(any(
                        value.startswith("native-output:") for value in report["accessed"]
                    ))
                    effects = read_epochs.native_output_effects(report["read_trace"])
                    self.assertTrue(any(
                        row["kind"] == "output-write" and row["result"] == 5 for row in effects
                    ))
                    self.assertTrue(any(
                        row["kind"] == "output-settled" and row["path"] == "/repo/result"
                        for row in effects
                    ))
                    job, = report["native_jobs"]
                    self.assertNotIn("tree", job)
                    self.assertTrue(job["waited"])
                    self.assertEqual(job["returncode"], 0)
                else:
                    self.assertNotIn("native_jobs", report)
                    job, = packets
                    self.assertTrue(job["tree"])
                    self.assertEqual(job["returncode"], status)
                    if status:
                        self.assertNotIn("read_trace", report)
                        self.assertTrue(any(
                            value.startswith("native-output:") for value in report["accessed"]
                        ))
                    else:
                        self.assertEqual(report["read_trace"]["version"], read_epochs.RUNTIME_VERSION)
                self.assert_clean(session)

    def test_native_job_report_refuses_malformed_or_mixed_canonical_headers(self):
        self.add("Makefile", "all:\n\t@printf final > result; printf once\n")
        mutations = {
            "missing": "del report['native_jobs']",
            "not-list": "report['native_jobs']={}",
            "non-object": "report['native_jobs']=[None]",
            "duplicate": "report['native_jobs'].append(report['native_jobs'][0].copy())",
            "tree": "report['native_jobs'][0]['tree']=[]",
            "sequence": "report['native_jobs'][0]['sequence']=999",
            "legacy": (
                "job=report['native_jobs'][0].copy();"
                "job['tree']=[row['event'] for row in report['read_trace']['machine']['events']"
                " if row['kind']=='native-tree' and row['dispatch']==job['sequence']];"
                "report['accessed'].append('native-job:'+json.dumps(job))"
            ),
            "legacy-output": (
                "event=next(row['event'] for row in report['read_trace']['machine']['events']"
                " if row['kind']=='native-output');"
                "report['accessed'].append('native-output:'+json.dumps(event))"
            ),
        }
        class Commands:
            def __getitem__(self, argv):
                return Command(argv, outputs=("result",))
        for name, mutation in mutations.items():
            body = (
                "import json\nfrom pathlib import Path\n"
                "original=guard.supervise\n"
                "def altered(config):\n"
                " status=original(config)\n"
                " if status==0:\n"
                "  path=Path(config['report']);report=json.loads(path.read_text())\n"
                "  " + mutation + "\n"
                "  path.write_text(json.dumps(report))\n"
                " return status\n"
                "guard.supervise=altered\n"
            )
            session = self.session()
            with self.subTest(mutation=name), self.native_supervisor(body), session:
                with self.assertRaises(MakeProbeError):
                    session._native_make_writable(
                        "all", outputs=("result",), observe_reads=True,
                        observe_runtime_completions=True, commands=Commands(),
                    )
            self.assert_clean(session)

    def test_original_scaninc_and_writable_producer_share_one_native_make(self):
        from scripts.validation_ownership import read_epochs
        compiler = foundation.FoundationTests.original_scaninc_command(self)
        recipe = "printf '%s' '$(VALUE)' > result; printf once"
        self.add("Makefile", (
            "VALUE := $(shell tools/scaninc/scaninc -I include unit.c)\n"
            ".PHONY: all\nall:\n\t@" + recipe + "\n"
        ))
        requests = []
        session = self.session()
        with session:
            tool = session.compile_native_command(compiler, cwd="tools/scaninc")
            class Commands:
                def __getitem__(self, argv):
                    requests.append(argv)
                    if argv == ("tools/scaninc/scaninc", "-I", "include", "unit.c"):
                        return Command(
                            argv, sources=("unit.c", "include/sample.h"), native_tool=tool,
                        )
                    if argv == ("/bin/sh", "-c", recipe.replace("$(VALUE)", "include/sample.h")):
                        return Command(argv, outputs=("result",))
                    raise KeyError(argv)
            completed, semantics, observed, generated = session._native_make_writable(
                "all", outputs=("result",), variables=("VALUE",),
                native_tool=tool, original_tool=True,
                native_libraries=tuple("/lib/x86_64-linux-gnu/" + name for name in (
                    "libstdc++.so.6", "libgcc_s.so.1", "libm.so.6",
                )),
                observe_reads=True, observe_runtime_completions=True, commands=Commands(),
            )
            self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, b"once", b""))
            self.assertEqual(semantics["domains"]["VALUE"]["value"], "include/sample.h")
            self.assertEqual(
                [(item.path, item.data, item.mode) for item in generated],
                [("result", b"include/sample.h", 0o644)],
            )
            self.assertEqual(requests, [
                ("tools/scaninc/scaninc", "-I", "include", "unit.c"),
                ("/bin/sh", "-c", recipe.replace("$(VALUE)", "include/sample.h")),
            ])
            executed = [
                row["event"] for row in observed["read_trace"]["machine"]["events"]
                if row["kind"] == "native-tree" and row["event"]["kind"] == "exec"
                and row["event"]["path"] == "/repo/tools/scaninc/scaninc"
            ]
            self.assertEqual(len(executed), 1)
            self.assertTrue(read_epochs.native_output_effects(observed["read_trace"]))
            self.assertFalse((session.tree / "tools/scaninc/scaninc").exists())
        self.assert_clean(session)

    def test_writable_original_tool_rejects_output_and_resource_aliases(self):
        for alias in ("output", "resource"):
            with self.subTest(alias=alias):
                self.add("tool.c", '#include <stdio.h>\nint main(void){puts("final");return 0;}\n')
                self.add("Makefile", "all:\n\t@./reader > result\n")
                session = self.session()
                with session:
                    tool = session.compile_native_command(Command(
                        ("gcc", "tool.c", "-o", "reader"),
                        code=("tool.c",), outputs=("reader",),
                    ))
                    class Commands:
                        def __getitem__(self, argv):
                            raise AssertionError("alias must refuse before original dispatch")
                    with self.assertRaisesRegex(MakeProbeError, "conflicts with.*(tool|source)"):
                        session._native_make_writable(
                            "all", outputs=("reader",) if alias == "output" else ("result",),
                            native_resources=(("shared-lock", "reader"),) if alias == "resource" else (),
                            native_tool=tool, original_tool=True,
                            observe_reads=True, observe_runtime_completions=True, commands=Commands(),
                        )
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
            jobs = observed["native_jobs"]
            self.assertFalse(any(row.startswith("native-job:") for row in observed["accessed"]))
            self.assertEqual(requests, [("/bin/sh", "-c", recipe)])
            self.assertEqual(len(jobs), 1)
            job, = jobs
            self.assertNotIn("tree", job)
            self.assertEqual(job["admission"]["outputs"], ["result"])
            self.assertTrue(job["waited"])
            self.assertEqual(job["returncode"], 0)
            effects = read_epochs.native_output_effects(observed["read_trace"])
            self.assertFalse(any(
                row.startswith("native-output:") for row in observed["accessed"]
            ))
            self.assertTrue(effects)
            self.assertTrue(all(row["owner"] == job["sequence"] for row in effects))
            self.assertTrue(any(row["kind"] == "output-write" and row["pid"] == job["pid"] for row in effects))
            trace = observed["read_trace"]
            self.assertEqual(trace["version"], read_epochs.WRITABLE_VERSION)
            self.assertEqual(trace["output_authority"]["paths"], ["result"])
            self.assertEqual(set(trace["output_authority"]), {"paths", "jobs"})
            self.assertEqual(
                set(trace["output_authority"]["jobs"][0]), {"sequence", "pid", "admission"},
            )
            duplicated_tree = json.loads(json.dumps(trace))
            duplicated_tree["output_authority"]["jobs"][0]["tree"] = [
                row["event"] for row in trace["machine"]["events"]
                if row["kind"] == "native-tree" and row["dispatch"] == job["sequence"]
            ]
            self.assertGreater(len(encoded(duplicated_tree)), len(encoded(trace)))
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "closed dispatch"):
                read_epochs.validate_trace(
                    duplicated_tree, duplicated_tree["scope"],
                    count_limit=100000, file_limit=10000000,
                )
            missing_tree = json.loads(json.dumps(trace))
            for row in missing_tree["machine"]["events"]:
                if row["kind"] == "native-tree":
                    row["dispatch"] = 999
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "closed dispatch"):
                read_epochs.validate_native_output_authority(
                    missing_tree, count_limit=100000, file_limit=10000000,
                    reserve=lambda size: None,
                )
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
                observe_root=True,
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
            executions = [
                row for row in trace["machine"]["events"] if row["kind"] == "execute" and row["make"]
            ]
            self.assertEqual(len(executions), 2)
            self.assertEqual({row["pid"] for row in executions}, {observed["native_root"]["pid"]})
            self.assertEqual(observed["native_root"]["argv"], ["/usr/bin/make", "-f", "Makefile", "all"])
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
                trace.patterns = None
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
                        self.assertEqual(
                            trace.machine[-1]["identity"],
                            list(publication_identity(os.fstat(old))),
                        )
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
            self.assertIn(inherited["pid"], [
                row["event"].get("child") for row in trace["machine"]["events"]
                if row["kind"] == "native-tree" and row["dispatch"] == job["sequence"]
            ])
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
                "  for event in trace['machine']['events']:\n"
                "   if event['kind']=='execute' and not event['make'] and event['dispatch']==1:\n"
                "    event['admission_owner']=admission['owner']\n"
                "   if event['kind']=='native-tree' and event['event']['kind']=='exec'"
                " and event['event']['pid']==root['pid'] and event['event']['generation']==1:\n"
                "    event['event']['admission']=admission.copy();event['sha256']=guard.hashlib.sha256(guard.encoded(event['event'])).hexdigest()\n"
                "  for job in report['native_jobs']:\n"
                "   if job['pid']==root['pid']:job['admission']=admission.copy()\n"
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


    def test_native_image_callback_retains_actual_parent_at_fork_and_rejects_changed_context(self):
        from scripts.validation_ownership import read_epochs
        nested = "exec /usr/bin/printf second"
        recipe = "( /usr/bin/printf first & wait ); /bin/sh -c '" + nested + "' > result"
        self.add("Makefile", "all: ; @" + recipe + "\n")
        commands = {
            ("/bin/sh", "-c", recipe): Command(("/bin/sh", "-c", recipe), outputs=("result",)),
            ("/usr/bin/printf", "first"): Command(("/usr/bin/printf", "first")),
            ("/bin/sh", "-c", nested): Command(("/bin/sh", "-c", nested), outputs=("result",)),
            ("/usr/bin/printf", "second"): Command(("/usr/bin/printf", "second"), outputs=("result",)),
        }
        for mutation in (None, 0, 1, 2, "missing", "null", "zero", "boolean", "short", "foreign"):
            session = self.session()
            contexts = []
            with self.subTest(mutation=mutation), session:
                before = session._sandbox_run

                def observe(*args, **kwargs):
                    handler = kwargs["native_admission_handler"]

                    def callback(path, inputs, context=None):
                        if context is not None:
                            contexts.append(json.loads(json.dumps(context)))
                            if mutation is not None and context["fork_parent"] is not None:
                                if type(mutation) is int:
                                    context["fork_parent"][mutation] += 1
                                elif mutation == "missing":
                                    del context["fork_parent"]
                                elif mutation == "null":
                                    context["fork_parent"] = None
                                elif mutation in {"zero", "boolean"}:
                                    context["fork_parent"][0] = 0 if mutation == "zero" else True
                                elif mutation == "short":
                                    context["fork_parent"].pop()
                                else:
                                    context["fork_parent"] = "foreign"
                        return handler(path, inputs, context)

                    kwargs["native_admission_handler"] = callback
                    return before(*args, **kwargs)

                with patch.object(session, "_sandbox_run", observe):
                    def execute():
                        return session._native_make_writable(
                            "all", outputs=("result",), commands=commands,
                            native_executables=("/usr/bin/printf",),
                            observe_reads=True, observe_runtime_completions=True,
                        )
                    if mutation is not None:
                        with self.assertRaisesRegex(MakeProbeError, "issued operand admission"):
                            execute()
                        self.assertTrue(session.budget.failed)
                    else:
                        completed, _, observed, generated = execute()
                        self.assertEqual((completed.stdout, completed.stderr), (b"first", b""))
                        self.assertEqual([(row.path, row.data) for row in generated], [("result", b"second")])
                        job, = observed["native_jobs"]
                        tree = [
                            row["event"] for row in observed["read_trace"]["machine"]["events"]
                            if row["kind"] == "native-tree" and row["dispatch"] == job["sequence"]
                        ]
                        references = read_epochs.native_fork_references(tree)
                        executed, pre_exec_forks = set(), []
                        for row in tree:
                            if row["kind"] == "exec":
                                executed.add(row["pid"])
                            elif row["kind"] == "fork" and row["pid"] not in executed:
                                pre_exec_forks.append(row)
                        self.assertTrue(pre_exec_forks)
                        self.assertTrue(any(reference is not None for reference in references.values()))
                        self.assertTrue(any(context["fork_parent"] is None for context in contexts))
                        self.assertEqual(len(contexts), sum(row["kind"] == "exec" for row in tree) - 1)
                        for context in contexts:
                            reference = references[(context["pid"], context["generation"])]
                            self.assertEqual(context["fork_parent"], None if reference is None else list(reference))
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
            "if(mkdir(\"/repo/stage/later\",0700)||rmdir(\"/repo/stage/later\"))return 5;"
            "return 0;}\n"
        ))
        self.add("Makefile", "all:\n\t@/native/tool\n")
        resources = (("directory", "stage"), ("directory", "stage/empty"), ("directory", "stage/later"))
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
            self.assertEqual(len([row for row in effects if row["kind"] == "output-mkdir"]), 3)
            self.assertEqual(len([row for row in effects if row["kind"] == "output-rmdir"]), 2)
            issued = {
                row["path"]: row["serial"] for row in effects
                if row["kind"] in {"output-mkdir", "output-open"}
            }
            invalid = json.loads(json.dumps(trace))
            for machine in invalid["machine"]["events"]:
                if machine["kind"] == "native-output":
                    row = machine["event"]
                    if row.get("serial") == issued["/repo/stage/generated.mk"] and "revision" in row:
                        row["revision"] += 7
                    machine["sha256"] = hashlib.sha256(encoded(row)).hexdigest()
            with self.assertRaisesRegex(read_epochs.ReadEpochError, "initial revision"):
                read_epochs.validate_trace(
                    invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                )
            for new, prior in (
                ("/repo/stage/generated.mk", "/repo/stage"),
                ("/repo/stage/generated.mk", "/repo/stage/empty"),
                ("/repo/stage/empty", "/repo/stage"),
                ("/repo/stage/later", "/repo/stage"),
                ("/repo/stage/later", "/repo/stage/empty"),
                ("/repo/stage/later", "/repo/stage/generated.mk"),
            ):
                with self.subTest(issuance=new, reused=prior):
                    invalid = json.loads(json.dumps(trace))
                    for machine in invalid["machine"]["events"]:
                        if machine["kind"] == "native-output":
                            row = machine["event"]
                            if row.get("serial") == issued[new]:
                                row["serial"] = issued[prior]
                            machine["sha256"] = hashlib.sha256(encoded(row)).hexdigest()
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
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
                with self.subTest(forged_initial_revision=creation["path"]):
                    invalid = json.loads(json.dumps(trace))
                    for row in invalid["machine"]["events"]:
                        if row["kind"] == "native-output":
                            effect = row["event"]
                            if effect.get("serial") == creation["serial"] and "revision" in effect:
                                effect["revision"] += 7
                            row["sha256"] = hashlib.sha256(encoded(effect)).hexdigest()
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
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
            self.assertEqual(returned[0]["identity"], retired["identity"])
            read_epochs.validate_trace(trace, trace["scope"], count_limit=100000, file_limit=10000000)
            for identity in (
                lease["identity"], replaced["identity"], None, [],
                [True] * 7, [*retired["identity"][:6], True],
            ):
                with self.subTest(retirement_identity=identity):
                    invalid = json.loads(json.dumps(trace))
                    invalid["machine"]["events"][returned[0]["seq"] - 1]["identity"] = identity
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                        )
            immutable_pins = [
                row for row in trace["events"] if row["kind"] == "source-open"
                and row["source"] is not None and row["custody"]["kind"] == "snapshot"
            ]
            self.assertTrue(immutable_pins)
            for immutable in immutable_pins:
                for field in (3, 5, 6):
                    with self.subTest(immutable_visit=immutable["visit"], retirement_field=field):
                        invalid = json.loads(json.dumps(trace))
                        pin, = [
                            row for row in invalid["machine"]["events"]
                            if row["kind"] == "pin-retired" and row["visit"] == immutable["visit"]
                        ]
                        pin["identity"][field] += 1
                        with self.assertRaises(read_epochs.ReadEpochError):
                            read_epochs.validate_trace(
                                invalid, invalid["scope"], count_limit=100000, file_limit=10000000,
                            )
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


    def test_native_original_make_empty_resources_refuse_mode_and_lock_before_kernel(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <fcntl.h>\n#include <sys/file.h>\n"
            "#include <sys/stat.h>\n#include <sys/wait.h>\n#include <unistd.h>\n#include <stdio.h>\n"
            "#include <string.h>\n#include <errno.h>\n"
            "int main(int argc,char **argv){int fd,other,rc,status;pid_t child;struct stat info;"
            "if(argc!=3)return 1;fd=!strcmp(argv[2],\"inherited\")?3:"
            "open(\"/repo/result\",O_CREAT|O_WRONLY,0600);if(fd<0)return 2;"
            "if(!strcmp(argv[2],\"duplicate\")){other=dup(fd);"
            "if(other<0||close(fd))return 7;fd=other;}"
            "if(!strcmp(argv[2],\"fork\")){child=fork();if(child<0)return 8;"
            "if(child){if(waitpid(child,&status,0)!=child)return 9;"
            "return !WIFEXITED(status)||WEXITSTATUS(status)||close(fd);}}"
            "if(!strcmp(argv[2],\"exec\")){argv[2]=\"inherited\";"
            "execv(\"/native/tool\",argv);return 10;}"
            "if(!strcmp(argv[1],\"mode\")){"
            "if(fchmod(fd,0644)||fstat(fd,&info))return 3;"
            "printf(\"mode:%o\\n\",info.st_mode&0777);}"
            "else{if(flock(fd,LOCK_EX))return 4;"
            "printf(\"lock:acquired\\n\");fflush(stdout);"
            "other=open(\"/repo/result\",O_WRONLY);if(other<0)return 5;"
            "rc=flock(other,LOCK_EX|LOCK_NB);"
            "printf(\"lock:%d;errno:%d\\n\",rc,errno);if(close(other))return 6;}"
            "return close(fd);}\n"
        ))
        cases = [
            (mode, binding, expected)
            for mode, expected in (
                ("mode", "issued output authority"),
                ("lock", "shared synchronization role"),
            )
            for binding in ("original", "duplicate", "fork", "exec")
        ]
        for mode, binding, expected in cases:
            with self.subTest(mode=mode, binding=binding):
                self.add("Makefile", "all:\n\t@/native/tool " + mode + " " + binding + "\n")
                session = self.session()
                with session:
                    tool = session.compile_native(("native.c",))
                    class Commands:
                        def __getitem__(self, argv):
                            return Command(argv, native_tool=tool, outputs=("result",))
                    completed = []
                    original_run = session.budget.run
                    def observe_run(*args, **kwargs):
                        result = original_run(*args, **kwargs)
                        if result.stdout.startswith((b"mode:", b"lock:")):
                            completed.append(result.stdout)
                        return result
                    with patch.object(session.budget, "run", side_effect=observe_run):
                        with self.assertRaisesRegex(MakeProbeError, expected):
                            session._native_make_writable(
                                "all", outputs=("result",), native_tool=tool,
                                commands=Commands(), observe_reads=True,
                                observe_runtime_completions=True,
                            )
                    self.assertEqual(completed, [])
                    self.assertTrue(session.budget.failed)
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
                    self.assertEqual([
                        row["event"]["generation"] for row in trace["machine"]["events"]
                        if row["kind"] == "native-tree" and row["dispatch"] == job["sequence"]
                        and row["event"]["kind"] == "exec"
                    ], [1, 2])
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
                def image(path, *, sealed):
                    self.assertTrue(sealed)
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
                def image(path, *, sealed):
                    self.assertTrue(sealed)
                    return changed(captured(path))
                with patch.object(session, "_captured_native_runtime", image):
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


class NativeGitlinkResourceTests(unittest.TestCase):
    setUp = foundation.FoundationTests.setUp
    tearDown = foundation.FoundationTests.tearDown
    add = foundation.FoundationTests.add
    assert_clean = foundation.FoundationTests.assert_clean
    gitlink_git = foundation.FoundationTests.gitlink_git
    native_supervisor = foundation.FoundationTests.native_supervisor

    def _gitlink_scope_fixture(self):
        self.add("Makefile", "all:\n\t@printf final > result\n")
        self.gitlink_git(self.root, "init", "--quiet")
        self.gitlink_git(self.root, "config", "user.name", "Owned Fixture")
        self.gitlink_git(self.root, "config", "user.email", "fixture@example.invalid")
        self.gitlink_git(self.root, "add", "Makefile")
        roots = ("vendor/module", "vendor/stamp.1.tmp", "vendor/.asset-manifest-write-abcd1234")
        for path in roots:
            self.gitlink_git(
                self.root, "update-index", "--add", "--cacheinfo", "160000," + "1" * 40 + "," + path,
            )
        self.gitlink_git(self.root, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "native gitlink scope")
        return roots

    def test_actual_gitlink_resources_refuse_equal_ancestor_descendant_and_patterns(self):
        roots = self._gitlink_scope_fixture()
        plans = [
            (kind, path) for kind in ("directory", "temporary", "shared-lock", "pid-temporary")
            for path in ("vendor/module", "vendor", "vendor/module/tmp")
        ] + [
            ("pid-temporary", "vendor/stamp"),
            ("atomic-temporary", "vendor/.asset-manifest-write-"),
        ]
        class Commands:
            def __getitem__(self, argv):
                return Command(argv, outputs=("result",))
        for present in (False, True):
            if present:
                for path in roots:
                    (self.root / path).mkdir(parents=True)
            for resource in plans:
                with self.subTest(present=present, resource=resource):
                    budget = ProbeBudget()
                    loader = foundation.AuthorityLoader(
                        self.root, foundation.git_tree_entries(self.root, None, budget=budget),
                        budget=budget,
                    )
                    session = foundation.ProbeSession(loader, scratch_root=self.scratch, budget=budget)
                    with session, patch.object(
                        session, "_sandbox_run", side_effect=AssertionError("gitlink collision reached dispatch"),
                    ) as dispatch:
                        with self.assertRaisesRegex(MakeProbeError, "conflicts with immutable source"):
                            session._native_make_writable(
                                "all", outputs=("result",), native_resources=(resource,),
                                commands=Commands(), observe_reads=True, observe_runtime_completions=True,
                            )
                        dispatch.assert_not_called()
                        self.assertFalse((self.root / "result").exists())
                        if present:
                            self.assertTrue(all(list((self.root / path).iterdir()) == [] for path in roots))
                    self.assert_clean(session)

    def test_gitlink_scope_replay_and_host_binding_preserve_disjoint_outputs(self):
        from scripts.validation_ownership import read_epochs
        roots = self._gitlink_scope_fixture()
        resource = ("directory", "stage")
        class Commands:
            def __getitem__(self, argv):
                return Command(argv, outputs=("result",), native_resources=(resource,))
        def session_for_tree():
            budget = ProbeBudget()
            loader = foundation.AuthorityLoader(
                self.root, foundation.git_tree_entries(self.root, None, budget=budget),
                budget=budget,
            )
            return foundation.ProbeSession(loader, scratch_root=self.scratch, budget=budget)
        def run(session):
            return session._native_make_writable(
                "all", outputs=("result",), native_resources=(resource,),
                commands=Commands(), observe_reads=True, observe_runtime_completions=True,
            )
        for present in (False, True):
            if present:
                for path in roots:
                    (self.root / path).mkdir(parents=True)
            session = session_for_tree()
            with self.subTest(present=present), session:
                completed, _, observed, generated = run(session)
                self.assertEqual((completed.returncode, completed.stderr), (0, b""))
                self.assertEqual([(row.path, row.data, row.mode) for row in generated], [("result", b"final", 0o644)])
                trace = observed["read_trace"]
                self.assertEqual(trace["output_authority"]["source_roots"], sorted(roots))
                read_epochs.validate_trace(trace, trace["scope"], count_limit=100000, file_limit=10000000)
                for collision in (
                    ("directory", "vendor"), ("temporary", roots[0]),
                    ("shared-lock", roots[0] + "/child"), ("pid-temporary", "vendor/stamp"),
                    ("atomic-temporary", "vendor/.asset-manifest-write-"),
                ):
                    invalid = json.loads(json.dumps(trace))
                    invalid["output_authority"]["resources"] = [list(collision)]
                    with self.subTest(replay_collision=collision):
                        with self.assertRaisesRegex(read_epochs.ReadEpochError, "conflicts with immutable source"):
                            read_epochs.validate_native_output_authority(
                                invalid, count_limit=100000, file_limit=10000000, reserve=lambda size: None,
                            )
                for invalid_roots in (None, [], [1], ["../escape"], [roots[0], roots[0]], ["result"]):
                    invalid = json.loads(json.dumps(trace))
                    invalid["output_authority"]["source_roots"] = invalid_roots
                    with self.subTest(source_roots=invalid_roots):
                        with self.assertRaises(read_epochs.ReadEpochError):
                            read_epochs.validate_native_output_authority(
                                invalid, count_limit=100000, file_limit=10000000, reserve=lambda size: None,
                            )
            self.assert_clean(session)
        for mutation in ("omit", "remove", "add", "reorder"):
            body = (
                "original=guard.supervise\n"
                "def altered(config,drop):\n"
                " status=original(config,drop)\n"
                " if config.get('native_output_paths') and status==0:\n"
                "  path=Path(config['report']);report=json.loads(path.read_text())\n"
                "  authority=report['read_trace']['output_authority']\n"
                + ("  del authority['source_roots']\n" if mutation == "omit" else
                   "  authority['source_roots'].pop()\n" if mutation == "remove" else
                   "  authority['source_roots'].append('spare')\n" if mutation == "add" else
                   "  authority['source_roots'].reverse()\n")
                + "  path.write_text(json.dumps(report))\n"
                " return status\n"
                "guard.supervise=altered\n"
            )
            session = session_for_tree()
            with self.subTest(returned_roots=mutation), self.native_supervisor(body), session:
                if mutation == "reorder":
                    completed, _, _, generated = run(session)
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(generated[0].data, b"final")
                else:
                    with self.assertRaisesRegex(MakeProbeError, "returned archive differs"):
                        run(session)
            self.assert_clean(session)
