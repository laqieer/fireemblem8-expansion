"""Actual complete sealed bodies, materialization, admission and cleanup."""

import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.validation_ownership import make_probe, read_epochs
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from scripts.validation_ownership.runtime_image import (
    IMAGE_SEALS, RuntimeImage, close_images, image_digest, materialize_image,
)
from scripts.validation_ownership.tests import test_foundation as foundation


class RuntimeImageTests(unittest.TestCase):
    def test_compiler_make_profile_binds_original_cli_and_rejects_search_overrides(self):
        from scripts.validation_ownership.authority import ENVIRONMENT
        argv = ["/usr/bin/make", "-f", "Makefile", "FE8_ITEM_ID_CAP=0xCD", "ASSET_MANIFEST=assets/manifest.json", "all"]
        expected = read_epochs.compiler_make_environment(ENVIRONMENT, argv)
        self.assertEqual(expected["MAKEFLAGS"], " -- ASSET_MANIFEST=assets/manifest.json FE8_ITEM_ID_CAP=0xCD")
        self.assertEqual(expected["FE8_ITEM_ID_CAP"], "0xCD")
        read_epochs.compiler_environment(expected, expected, frontend=False, driver="/usr/bin/cc")
        for name in ("CPATH", "C_INCLUDE_PATH", "COMPILER_PATH", "GCC_EXEC_PREFIX", "LIBRARY_PATH", "LD_PRELOAD"):
            with self.subTest(name=name), self.assertRaises(MakeProbeError):
                values = read_epochs.compiler_make_environment(ENVIRONMENT, ["/usr/bin/make", name + "=/etc", "all"])
                read_epochs.compiler_environment(values, values, frontend=False, driver="/usr/bin/cc")
            forged = dict(expected, **{name: "/repo/foreign"})
            forged["MAKEFLAGS"] = " -- " + name + "=/repo/foreign " + expected["MAKEFLAGS"][4:]
            with self.subTest(forged=name), self.assertRaises(MakeProbeError):
                read_epochs.compiler_environment(forged, forged, frontend=False, driver="/usr/bin/cc")
        for field in ("MAKEFLAGS", "MAKEOVERRIDES", "FE8_ITEM_ID_CAP"):
            changed = dict(expected)
            changed[field] += "foreign"
            with self.subTest(field=field), self.assertRaises(MakeProbeError):
                read_epochs.compiler_environment(changed, expected, frontend=False, driver="/usr/bin/cc")

    def test_compiler_environment_binds_closed_baseline_and_driver_transformations(self):
        from scripts.validation_ownership.authority import ENVIRONMENT
        baseline = dict(ENVIRONMENT)
        driver = "/usr/bin/cc"
        additions = {
            "COLLECT_GCC": driver, "COLLECT_GCC_OPTIONS": "'-E' '-MM' '-MG'",
            "OFFLOAD_TARGET_NAMES": "nvptx-none:amdgcn-amdhsa", "OFFLOAD_TARGET_DEFAULT": "1",
        }
        for frontend, actual in (
            (False, baseline), (True, {**baseline, "COLLECT_GCC": driver}),
            (True, {**baseline, **additions}),
        ):
            read_epochs.compiler_environment(actual, baseline, frontend=frontend, driver=driver)
        controls = []
        for name in baseline:
            missing, changed = dict(baseline), dict(baseline)
            del missing[name]
            changed[name] += "changed"
            controls.extend(((missing, baseline, False), (changed, baseline, False)))
        for name in ("CPATH", "C_INCLUDE_PATH", "COMPILER_PATH", "GCC_EXEC_PREFIX", "LD_PRELOAD", "LD_LIBRARY_PATH"):
            controls.extend((
                ({**baseline, name: "/repo/foreign"}, baseline, False),
                ({**baseline, name: "/repo/foreign"}, {**baseline, name: "/repo/foreign"}, False),
                ({**baseline, **additions, name: "/repo/foreign"}, baseline, True),
            ))
        for name in additions:
            controls.extend((
                ({**baseline, name: additions[name]}, baseline, False),
                ({**baseline, **additions, name: ""}, baseline, True),
                ({**baseline, **additions, name: None}, baseline, True),
                ({**baseline, **additions, name: "\0"}, baseline, True),
                ({**baseline, **additions, name: "\ud800"}, baseline, True),
                ({**baseline, **additions, name: "x" * 65536}, baseline, True),
            ))
        controls.extend((
            ({**baseline, **additions, "COLLECT_GCC": "/repo/cc"}, baseline, True),
            (baseline, baseline, True), (baseline, baseline, "frontend"),
            ({1: "bad"}, baseline, False), (baseline, None, False),
        ))
        for actual, expected, frontend in controls:
            with self.subTest(actual=tuple(actual) if isinstance(actual, dict) else actual, frontend=frontend):
                with self.assertRaises(read_epochs.ReadEpochError):
                    read_epochs.compiler_environment(actual, expected, frontend=frontend, driver=driver)

class RuntimeImageSessionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests("runTest")
        self.fixture.setUp()
        self.fixture.add("Makefile", "all: ; @value=owned; printf '%s' \"$$value\"\n")

    def tearDown(self):
        self.fixture.tearDown()

    def test_actual_native_dependency_compiler_binds_source_environment_and_output_custody(self):
        from scripts.validation_ownership.make_probe import Command
        self.fixture.add("src/query.c", '#include "query.h"\nint value = VALUE;\n')
        self.fixture.add("include/query.h", "#define VALUE 7\n")
        with self.fixture.session() as setup:
            setup._sealed_dependency_runtime()
            driver, frontend = setup.dependency_compiler
        output = ".dep/query.d"
        recipe = (
            "mkdir -p .dep; " + driver +
            " -E -MM -MG -nostdinc -undef -MT query.o -Iinclude src/query.c > " + output
        )
        self.fixture.add("Makefile", (
            "-include " + output + "\n.PHONY: all\nall: ; @v=done; printf '%s' \"$$v\"\n"
            + output + ": src/query.c include/query.h\n\t@" + recipe + "\n"
        ))
        baseline = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", "all"], cwd=self.fixture.root,
            capture_output=True, check=True, timeout=15,
        )
        expected = (self.fixture.root / output).read_bytes()
        (self.fixture.root / output).unlink()
        resources = (("directory", ".dep"),)
        missing_code = False

        class Commands:
            def __getitem__(owner, argv):
                if argv == ("/bin/sh", "-c", recipe) or argv == ("mkdir", "-p", ".dep"):
                    return Command(argv, outputs=(output,), native_resources=resources)
                if argv == ("/bin/sh", "-c", "v=done; printf '%s' \"$v\""):
                    return Command(argv)
                if argv[0] not in {driver, frontend}:
                    raise KeyError(argv)
                return Command(
                    argv, sources=("src/query.c",), code=() if missing_code else ("include/query.h",),
                    directories=("src", "include"), outputs=(output,), native_resources=resources,
                )

        session = self.fixture.session(runtime_files=(
            "/proc/filesystems", "/proc/mounts", "/etc/selinux/config",
        ))
        with session:
            session._sealed_dependency_runtime()
            completed, _, observed, generated = session._native_make_writable(
                "all", outputs=(output,), commands=Commands(), native_resources=resources,
                assignments=(("command-line", "PYTHON", "python3"),
                             ("command-line", "FE8_ITEM_ID_CAP", ""),
                             ("command-line", "EXPANSION_CUSTOM_SPELL_EFFECTS", "0"),
                             ("command-line", "MODERN_BUILD_ROOT", "build/expansion-modern"),
                             ("command-line", "ASSET_MANIFEST", "assets/manifest.json")),
                native_executables=(driver, frontend, "/usr/bin/mkdir"),
                native_metadata_directories=("/sys/fs/selinux", "/selinux"),
                observe_reads=True, observe_runtime_completions=True,
            )
            self.assertEqual((completed.stdout, completed.stderr), (baseline.stdout, b""))
            self.assertEqual([(item.path, item.data, item.mode) for item in generated],
                             [(output, expected, 0o644)])
            trace = observed["read_trace"]
            profile = read_epochs.native_compiler_profile(
                trace["output_authority"]["compiler"], count_limit=32768,
            )
            job = observed["native_jobs"][0]
            tree = [row["event"] for row in trace["machine"]["events"]
                    if row["kind"] == "native-tree" and row["dispatch"] == 1]
            actors = read_epochs.native_compiler_lineage(
                tree, job, profile, sources=session.snapshot.files, count_limit=32768,
            )
            self.assertEqual([actor.role for actor in actors], ["driver", "frontend"])
            self.assertEqual(actors[1].driver, (actors[0].pid, actors[0].generation, actors[0].admission))
            effects = read_epochs.native_output_effects(trace)
            writes = [row for row in effects if row["kind"] == "output-write"]
            self.assertEqual([(row["pid"], row["fd"], row["result"]) for row in writes],
                             [(actors[1].pid, 1, len(expected))])
            self.assertEqual([row["exec"] for row in trace["events"] if row["kind"] == "entry-image"],
                             [1, 2])
            self.assertEqual(observed["consumed"], ["src/query.c"])
            self.assertEqual(observed["code_consumed"], ["include/query.h"])
            archive = read_epochs.reconstruct_archive(
                json.loads(json.dumps(trace)), budget=session.budget,
            )
            self.assertEqual(archive.compiler_profile, profile)
            self.assertEqual(archive.compiler_executions, actors)
            self.assertEqual([part.exec for part in archive.passes], [1, 2])
            for defect in ("profile", "driver", "source", "environment", "missing-profile"):
                changed = json.loads(json.dumps(trace))
                if defect == "missing-profile":
                    del changed["output_authority"]["compiler"]
                else:
                    row = next(row for row in changed["machine"]["events"]
                               if row["kind"] == "native-tree" and row["event"]["kind"] == "exec"
                               and row["event"]["pid"] == actors[1].pid)
                    binding = row["event"]["admission"]["compiler"]
                    if defect == "profile":
                        binding["profile"] = "0" * 64
                    elif defect == "driver":
                        binding["driver"][0] += 1
                    elif defect == "source":
                        binding["sources"] = ["Makefile"]
                    else:
                        del binding["environment"]
                    row["sha256"] = hashlib.sha256(read_epochs.encoded(row["event"])).hexdigest()
                with self.subTest(replay=defect), self.assertRaises(MakeProbeError):
                    read_epochs.validate_trace(
                        changed, trace["scope"], count_limit=32768,
                        file_limit=session.budget.limits.file_bytes,
                        reserve=lambda size: session.budget.charge("control", size),
                    )
        self.fixture.assert_clean(session)
        for defect in ("environment", "source"):
            missing_code = defect == "source"
            if defect == "environment":
                recipe = "CPATH=/repo/include; export CPATH; " + recipe
            else:
                recipe = recipe.removeprefix("CPATH=/repo/include; export CPATH; ")
            self.fixture.add("Makefile", (
                "-include " + output + "\n.PHONY: all\nall: ;\n"
                + output + ": src/query.c include/query.h\n\t@" + recipe + "\n"
            ))
            session = self.fixture.session(runtime_files=(
                "/proc/filesystems", "/proc/mounts", "/etc/selinux/config",
            ))
            with self.subTest(actual=defect), session:
                session._sealed_dependency_runtime()
                with self.assertRaisesRegex(MakeProbeError, "compiler environment|issued source scope"):
                    session._native_make_writable(
                        "all", outputs=(output,), commands=Commands(), native_resources=resources,
                        native_executables=(driver, frontend, "/usr/bin/mkdir"),
                        native_metadata_directories=("/sys/fs/selinux", "/selinux"),
                        observe_reads=True, observe_runtime_completions=True,
                    )
                self.assertTrue(session.budget.failed)
            self.fixture.assert_clean(session)

    def test_derived_compiler_closure_owns_complete_sealed_images_without_exec_authority(self):
        session = self.fixture.session()
        with session:
            rows = session._sealed_dependency_runtime()
            self.assertEqual(tuple(path for path, _ in rows), tuple(session.dependency_runtime["runtime_files"]))
            self.assertTrue(set(session.dependency_compiler) <= dict(rows).keys())
            for path, image in rows:
                with self.subTest(path=path), Path(path).open("rb") as stream:
                    self.assertEqual(image_digest(image), hashlib.file_digest(stream, "sha256").hexdigest())
                    self.assertEqual(len(image), Path(path).stat().st_size)
                    self.assertEqual(fcntl.fcntl(image.descriptor, fcntl.F_GET_SEALS), IMAGE_SEALS)
            stored = session.budget.bytes["snapshot"]
            repeated = session._sealed_dependency_runtime()
            self.assertTrue(all(a is b for (_, a), (_, b) in zip(rows, repeated)))
            self.assertEqual(session.budget.bytes["snapshot"], stored)
            frontend = session.dependency_compiler[1]
            if frontend.startswith("/usr/libexec/"):
                with self.assertRaisesRegex(MakeProbeError, "outside the trusted system tool/library roots"):
                    session._captured_native_runtime_input(frontend, sealed=True)
            with self.assertRaisesRegex(MakeProbeError, "outside its issued runtime closure"):
                session._captured_native_runtime_input("/usr/bin/make", sealed=True, dependency=True)
            completed, _, _ = session._native_make_readonly("all")
            self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, b"owned", b""))
        self.assertTrue(all(image.descriptor == -1 for _, image in rows))
        self.assertIsNone(session.dependency_compiler)
        self.assertIsNone(session.dependency_runtime)
        self.fixture.assert_clean(session)

    def test_derived_compiler_capture_failure_closes_partial_owned_closure(self):
        session = self.fixture.session()
        images = []
        with self.assertRaisesRegex(MakeProbeError, "read-only|regular|trusted"):
            with session:
                session._ensure_dependency_runtime()
                capture = session._captured_native_runtime_input

                def changed_input(path, **kwargs):
                    if images:
                        with patch.object(make_probe, "_trusted_runtime_path", return_value=self.fixture.root / "Makefile"):
                            return capture(path, **kwargs)
                    image = capture(path, **kwargs)
                    images.append(image)
                    return image

                with patch.object(session, "_captured_native_runtime_input", changed_input):
                    session._sealed_dependency_runtime()
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].descriptor, -1)
        self.assertTrue(session.budget.failed)
        self.fixture.assert_clean(session)

    def test_sealed_compiler_root_preserves_issued_file_alias_identity(self):
        session = self.fixture.session()
        with session:
            root = session._sealed_dependency_root("compiler-alias-layout")
            rows = session._sealed_dependency_runtime()
            for path, image in rows:
                canonical = make_probe._trusted_runtime_path(path, compiler=True)
                source = root / str(canonical).lstrip("/")
                destination = root / path.lstrip("/")
                with self.subTest(path=path), destination.open("rb") as stream:
                    self.assertTrue(destination.samefile(source))
                    self.assertEqual(hashlib.file_digest(stream, "sha256").hexdigest(), image_digest(image))
                    self.assertEqual(destination.stat().st_size, len(image))
            interpreter = session.dependency_runtime["runtime_interpreter"]
            alias = "/lib64/ld-linux-x86-64.so.2"
            old = session._new_root("independent-body-layout", make=True, native_runtime=rows)
            self.assertFalse((old / alias.lstrip("/")).samefile(old / interpreter.lstrip("/")))
            driver, frontend = session.dependency_compiler
            for name, aliases in (
                ("different-body", ((driver, frontend),)),
                ("missing-target", ((alias, "/usr/lib/foreign-runtime.so"),)),
                ("escaping", ((alias, "/usr/../etc/foreign-runtime"),)),
                ("duplicate", ((alias, interpreter), (alias, interpreter))),
                ("cycle", ((alias, interpreter), (interpreter, alias))),
            ):
                with self.subTest(alias_mutation=name), self.assertRaises(MakeProbeError):
                    session._new_root(name, make=True, native_runtime=rows, native_aliases=aliases)
                self.assertFalse((session.base / name).exists())
        self.fixture.assert_clean(session)

    def test_actual_sealed_root_compiler_retains_closed_runtime_and_header_scope(self):
        from scripts.validation_ownership.authority import ENVIRONMENT
        self.fixture.add("src/query.c", '#include "query.h"\n')
        self.fixture.add("include/query.h", "#define VALUE 7\n")
        self.fixture.add("include/foreign.h", "#define FOREIGN 9\n")
        for case in (
            "positive", "separate-loader-copy", "missing-search-parents",
            "CPATH", "GCC_EXEC_PREFIX", "LD_PRELOAD", "foreign-header",
        ):
            if case == "foreign-header":
                self.fixture.add("include/query.h", '#include "foreign.h"\n')
            session = self.fixture.session()
            with self.subTest(case=case), session:
                rows = session._sealed_dependency_runtime()
                root = (
                    session._new_root("compiler-root", make=True, native_runtime=rows)
                    if case == "separate-loader-copy" else session._sealed_dependency_root("compiler-root")
                )
                output = session.base / "compiler-output"
                output.mkdir()
                driver, frontend = session.dependency_compiler
                profile = {
                    **session.dependency_runtime, "executables": [driver, frontend],
                    "include_dirs": ["/repo/include"],
                }
                if case == "missing-search-parents":
                    profile["runtime_directories"] = []

                def execute():
                    return session._sandbox_run(
                        root, mode="compile",
                        argv=[driver, "-E", "-MM", "-nostdinc", "-undef", "-MT",
                              "query.o", "-Iinclude", "src/query.c"],
                        environment={
                            **ENVIRONMENT, "SOURCE_DATE_EPOCH": "0", "TMPDIR": "/work",
                            **({case: "/repo/foreign"} if case in {"CPATH", "GCC_EXEC_PREFIX", "LD_PRELOAD"} else {}),
                        },
                        mounts=[
                            session._mount(session.tree, "/repo"),
                            session._mount(output, "/work", writable=True),
                            session._mount(Path("/dev/null"), "/dev/null", writable=True),
                        ],
                        code=("include/query.h",), sources=("src/query.c",),
                        directories=("src", "include"), executables=(driver, frontend),
                        dependency=profile,
                    )

                if case == "positive":
                    completed, observed = execute()
                    self.assertEqual(
                        (completed.returncode, completed.stdout, completed.stderr),
                        (0, b"query.o: src/query.c include/query.h\n", b""),
                    )
                    self.assertEqual(tuple(observed["executed"]), (driver, frontend))
                    self.assertEqual(observed["consumed"], ["src/query.c"])
                    self.assertEqual(observed["code_consumed"], ["include/query.h"])
                else:
                    with self.assertRaisesRegex(MakeProbeError, "undeclared|unadmitted|compiler environment"):
                        execute()
            self.fixture.assert_clean(session)

    def test_issued_compiler_profile_and_finite_driver_at_fork_lineage(self):
        from scripts.validation_ownership.authority import ENVIRONMENT, encoded, native_command_owner
        self.fixture.add("src/query.c", '#include "query.h"\n')
        self.fixture.add("include/query.h", "#define VALUE 7\n")
        session = self.fixture.session()
        with session:
            value = session._native_compiler_profile(ENVIRONMENT)
            profile = read_epochs.native_compiler_profile(value, count_limit=32768)
            self.assertEqual(profile.environment, tuple(sorted(ENVIRONMENT.items())))
            images = dict(session._sealed_dependency_runtime())
            self.assertEqual(
                profile.files, tuple((path, len(image), image_digest(image)) for path, image in images.items()),
            )
            inputs = ["cc", "-E", "-MM", "-nostdinc", "-undef", "-MT", "query.o", "-Iinclude", "src/query.c"]
            scope = {"profile": profile.identity, "sources": ["src/query.c"],
                     "code": ["include/query.h"], "includes": ["include"]}

            def admission(sequence, argv, compiler):
                closure = hashlib.sha256(encoded([argv, compiler])).hexdigest()
                return {"sequence": sequence, "closure": closure,
                        "owner": native_command_owner(closure, [".dep/query.d"], ()),
                        "input_sha256": hashlib.sha256(encoded({"argv": argv, "cwd": "/repo"})).hexdigest(),
                        "outputs": [".dep/query.d"], "compiler": compiler}

            def execution(pid, parent, generation, path, argv, binding, sequence):
                if binding is not None:
                    binding = dict(binding, environment={
                        **ENVIRONMENT,
                        **({"COLLECT_GCC": profile.driver} if binding["role"] == "frontend" else {}),
                    })
                return {"kind": "exec", "pid": pid, "parent": parent, "generation": generation,
                        "path": path, "argv": argv, "cwd": "/repo",
                        "admission": admission(sequence, argv, binding)}

            events = [
                execution(10, 1, 1, "/bin/sh", ["/bin/sh", "-c", "original recipe"], None, 1),
                {"kind": "fork", "pid": 10, "child": 11}, {"kind": "start", "pid": 11},
                execution(11, 10, 1, profile.driver, inputs, dict(scope, role="driver", driver=None), 2),
                {"kind": "fork", "pid": 11, "child": 12}, {"kind": "start", "pid": 12},
                execution(12, 11, 1, profile.frontend, [profile.frontend, "-E", "src/query.c"],
                          dict(scope, role="frontend", driver=[11, 1, 2]), 3),
                {"kind": "exit", "pid": 12, "status": 0}, {"kind": "exit", "pid": 11, "status": 0},
                {"kind": "exit", "pid": 10, "status": 0},
            ]
            for sequence, event in enumerate(events, 1):
                event["seq"] = sequence
            job = {"pid": 10, "sequence": 1, "terminal_status": 0, "executable": "/bin/sh",
                   "argv": events[0]["argv"], "cwd": "/repo"}
            ordinary = json.loads(json.dumps(events))
            for event in ordinary:
                if event["kind"] == "exec":
                    event["admission"].pop("compiler")
            job["admission"] = ordinary[0]["admission"]
            with self.assertRaisesRegex(MakeProbeError, "Command owner"):
                read_epochs.native_job_tree(
                    events, job, 1, ["/bin/sh", profile.driver, profile.frontend],
                    count_limit=32768, writable=True,
                )
            read_epochs.native_job_tree(
                ordinary, job, 1, ["/bin/sh", profile.driver, profile.frontend],
                count_limit=32768, writable=True,
            )
            validate = lambda rows: read_epochs.native_compiler_lineage(
                rows, job, profile, sources=session.snapshot.files, count_limit=32768,
                reserve=lambda size: session.budget.charge("control", size),
            )
            actors = validate(events)
            self.assertEqual([(actor.role, actor.pid, actor.driver) for actor in actors],
                             [("driver", 11, None), ("frontend", 12, (11, 1, 2))])
            self.assertEqual(actors[0].environment, profile.environment)
            self.assertEqual(dict(actors[1].environment)["COLLECT_GCC"], profile.driver)
            for index in (3, 6):
                baseline = events[index]["admission"]["compiler"]["environment"]
                mutations = (
                    None, [], {**baseline, "CPATH": "/repo/foreign"},
                    {**baseline, "HOME": "/repo"}, {**baseline, "LANG": None},
                    {**baseline, "LANG": "x" * 65536},
                    {name: item for name, item in baseline.items() if name != "HOME"},
                    {**baseline, "COLLECT_GCC": "/repo/cc"},
                )
                for actual in mutations:
                    changed = json.loads(json.dumps(events))
                    changed[index]["admission"]["compiler"]["environment"] = actual
                    with self.subTest(actor=index, environment=actual), self.assertRaises(MakeProbeError):
                        validate(changed)
                changed = json.loads(json.dumps(events))
                del changed[index]["admission"]["compiler"]["environment"]
                with self.assertRaisesRegex(MakeProbeError, "open or foreign"):
                    validate(changed)
            for actual in (
                {**ENVIRONMENT, "CPATH": "/repo/foreign"},
                {**ENVIRONMENT, "COLLECT_GCC": profile.driver},
            ):
                invalid = json.loads(json.dumps(value))
                invalid["environment"] = actual
                invalid["identity"] = hashlib.sha256(encoded({
                    key: item for key, item in invalid.items() if key != "identity"
                })).hexdigest()
                with self.subTest(profile_environment=actual), self.assertRaises(MakeProbeError):
                    read_epochs.native_compiler_profile(invalid, count_limit=32768)
            redirected = json.loads(json.dumps(events))
            redirected.insert(6, execution(
                12, 11, 1, "/bin/sh", ["/bin/sh", "-c", ":"], None, 3,
            ))
            redirected[7]["generation"] = 2
            redirected[7]["admission"]["sequence"] = 4
            for sequence, event in enumerate(redirected, 1):
                event["seq"] = sequence
            ordinary_redirected = json.loads(json.dumps(redirected))
            for event in ordinary_redirected:
                if event["kind"] == "exec":
                    event["admission"].pop("compiler")
            read_epochs.native_job_tree(
                ordinary_redirected, job, 1, ["/bin/sh", profile.driver, profile.frontend],
                count_limit=32768, writable=True,
            )
            with self.assertRaisesRegex(MakeProbeError, "driver-at-fork"):
                validate(redirected)
            charges = []
            self.assertEqual(read_epochs.native_compiler_lineage(
                events, job, profile, sources=session.snapshot.files, count_limit=len(events),
                reserve=charges.append,
            ), actors)
            cost = sum(charges)
            for limit in (cost, cost - 1):
                budget = ProbeBudget(Limits(control_bytes=limit))
                try:
                    if limit == cost:
                        self.assertEqual(read_epochs.native_compiler_lineage(
                            events, job, profile, sources=session.snapshot.files, count_limit=len(events),
                            reserve=lambda size: budget.charge("control", size),
                        ), actors)
                        self.assertEqual(budget.bytes["control"], cost)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "budget exhausted"):
                            read_epochs.native_compiler_lineage(
                                events, job, profile, sources=session.snapshot.files, count_limit=len(events),
                                reserve=lambda size: budget.charge("control", size),
                            )
                        self.assertTrue(budget.failed)
                finally:
                    budget.close()
            for name, mutate in (
                ("driver-options", lambda rows: rows[3]["argv"].append("-fplugin=foreign")),
                ("profile", lambda rows: rows[6]["admission"]["compiler"].update(profile="0" * 64)),
                ("parent", lambda rows: rows[6].update(parent=10)),
                ("generation", lambda rows: rows[6]["admission"]["compiler"].update(driver=[11, 2, 2])),
                ("admission", lambda rows: rows[6]["admission"]["compiler"].update(driver=[11, 1, 1])),
                ("boolean-reference", lambda rows: rows[6]["admission"]["compiler"].update(driver=[11, True, 2])),
                ("scope", lambda rows: rows[6]["admission"]["compiler"]["code"].append("src/query.c")),
                ("direct-frontend", lambda rows: rows[3].update(path=profile.frontend)),
                ("unbound-frontend", lambda rows: rows[6]["admission"].update(compiler=None)),
                ("missing-terminal", lambda rows: rows.pop()),
                ("missing-frontend", lambda rows: rows[6].update(path="/bin/sh", admission=rows[0]["admission"])),
                ("retired-driver", lambda rows: rows.insert(6, {"kind": "exit", "pid": 11, "status": 0})),
                ("unrelated-driver-exec", lambda rows: rows.insert(
                    6, execution(11, 10, 2, "/bin/sh", ["/bin/sh", "-c", ":"], None, 4),
                )),
            ):
                changed = json.loads(json.dumps(events))
                mutate(changed)
                with self.subTest(lineage_mutation=name), self.assertRaises(MakeProbeError):
                    validate(changed)
            invalid = json.loads(json.dumps(value))
            invalid["files"][0][2] = "0" * 64
            with self.assertRaisesRegex(MakeProbeError, "issued identity"):
                read_epochs.native_compiler_profile(invalid, count_limit=32768)
            with self.assertRaisesRegex(MakeProbeError, "tree extent"):
                read_epochs.native_compiler_lineage(
                    events, job, profile, sources=session.snapshot.files, count_limit=len(events) - 1,
                )
        self.fixture.assert_clean(session)

    def test_sealed_capture_has_one_owned_body_and_preserves_legacy_cold_capture(self):
        session = self.fixture.session()
        images = []
        with session:
            legacy = session._captured_native_runtime_input("/usr/bin/find")
            self.assertIsInstance(legacy, bytes)
            before = session.budget.bytes.get("snapshot", 0)
            image = session._captured_native_runtime_input("/usr/bin/python3", sealed=True)
            images.append(image)
            self.assertIsInstance(image, RuntimeImage)
            self.assertEqual(
                session.budget.bytes["snapshot"] - before,
                Path("/usr/bin/python3").resolve().stat().st_size,
            )
            self.assertLess(session.budget.bytes.get("cache", 0), len(legacy) + 4096)
            self.assertIs(
                session._captured_native_runtime_input("/usr/bin/python3", sealed=True), image,
            )
            completed, _, _ = session._native_make_readonly("all")
            self.assertEqual((completed.stdout, completed.stderr), (b"owned", b""))
        self.assertTrue(all(image.descriptor == -1 for image in images))
        self.fixture.assert_clean(session)

    def test_sealed_cache_failure_retires_complete_body(self):
        session = self.fixture.session(cache_bytes=64)
        with session:
            before = set(os.listdir("/proc/self/fd"))
            with self.assertRaises(MakeProbeError):
                session._captured_native_runtime_input("/usr/bin/python3", sealed=True)
            self.assertFalse(session.native_runtime_inputs)
            self.assertEqual(set(os.listdir("/proc/self/fd")), before)
        self.fixture.assert_clean(session)

    def test_capture_mode_binds_both_input_and_closure_cache_call_orders(self):
        for closure in (False, True):
            for first in (False, True):
                session = self.fixture.session()
                images = []
                with self.subTest(closure=closure, first=first), session:
                    capture = (
                        session._captured_native_runtime if closure
                        else session._captured_native_runtime_input
                    )
                    captures = {}
                    for sealed in (first, not first, first, not first):
                        value = capture("/usr/bin/find", sealed=sealed)
                        rows = value if closure else (("/usr/bin/find", value),)
                        for path, body in rows:
                            self.assertTrue(isinstance(body, RuntimeImage if sealed else bytes))
                            if sealed:
                                images.append(body)
                        if sealed in captures:
                            self.assertIs(value, captures[sealed])
                        captures[sealed] = value
                    ordinary = dict(captures[False]) if closure else {"/usr/bin/find": captures[False]}
                    protected = dict(captures[True]) if closure else {"/usr/bin/find": captures[True]}
                    self.assertEqual(set(ordinary), set(protected))
                    for path in ordinary:
                        self.assertEqual(image_digest(ordinary[path]), image_digest(protected[path]))
                self.assertTrue(images)
                self.assertTrue(all(image.descriptor == -1 for image in images))
                self.fixture.assert_clean(session)

    def test_sealed_warm_capture_does_not_bypass_legacy_file_quota(self):
        original_path = make_probe._trusted_runtime_path
        for name in ("/usr/bin/python3", "/usr/bin/make"):
            source = Path(name).resolve()
            session = self.fixture.session()
            with self.subTest(source=name), session, patch.object(
                make_probe, "_trusted_runtime_path",
                lambda path, **kwargs: source if path == "/usr/bin/python3"
                else original_path(path, **kwargs),
            ):
                image = session._captured_native_runtime_input("/usr/bin/python3", sealed=True)
                limit = min(len(image) - 1, session.budget.limits.file_bytes)
                # The cold provider's quota must not constrain unrelated session-bootstrap files.
                cold_budget = ProbeBudget(Limits(file_bytes=limit))
                try:
                    def cold_source(path, budget):
                        self.assertIs(budget, session.budget)
                        return cold_budget.read_bytes(make_probe._trusted_runtime_path(path), "control")

                    with patch.object(make_probe, "_trusted_runtime_bytes", cold_source):
                        with self.assertRaisesRegex(MakeProbeError, "file.*byte|file exceeds"):
                            session._captured_native_runtime_input("/usr/bin/python3")
                    self.assertTrue(cold_budget.failed)
                    self.assertEqual(cold_budget.bytes.get("control", 0), 0)
                finally:
                    cold_budget.close()
            self.assertEqual(image.descriptor, -1)
            self.fixture.assert_clean(session)

    def test_alternate_capture_refuses_changed_complete_body_and_retires_descriptors(self):
        original_bytes = make_probe._trusted_runtime_bytes
        original_path = make_probe._trusted_runtime_path
        for first in (False, True):
            session = self.fixture.session()
            with self.subTest(first=first), session:
                session._captured_native_runtime_input("/usr/bin/find", sealed=first)
                descriptors = set(os.listdir("/proc/self/fd"))
                if first:
                    context = patch.object(
                        make_probe, "_trusted_runtime_bytes",
                        lambda path, budget: original_bytes("/usr/bin/printf", budget),
                    )
                else:
                    context = patch.object(
                        make_probe, "_trusted_runtime_path",
                        lambda path: original_path("/usr/bin/printf"),
                    )
                with context, self.assertRaisesRegex(MakeProbeError, "earlier captured runtime"):
                    session._captured_native_runtime_input("/usr/bin/find", sealed=not first)
                self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)
            self.fixture.assert_clean(session)

    def test_nested_view_shutdown_and_misnesting_close_actual_owned_descriptors(self):
        for shutdown in (True, False):
            budget = ProbeBudget()
            loader = self.fixture.capture_view(budget)
            session = foundation.ProbeSession(
                loader, scratch_root=self.fixture.scratch, budget=budget,
            )
            with self.subTest(shutdown=shutdown), session:
                session._native_make_readonly("all")
                maps = [session.native_runtime_inputs]
                outer, inner = session.select_view(loader), session.select_view(loader)
                outer.__enter__()
                session._native_make_readonly("all")
                maps.append(session.native_runtime_inputs)
                inner.__enter__()
                session._native_make_readonly("all")
                maps.append(session.native_runtime_inputs)
                images = [
                    image for bodies in maps for image in bodies.values()
                    if isinstance(image, RuntimeImage)
                ]
                self.assertGreaterEqual(len(images), 3)
                descriptors = [image.descriptor for image in images]
                try:
                    if shutdown:
                        session.__exit__(None, None, None)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "nesting order"):
                            outer.__exit__(None, None, None)
                    self.assertTrue(all(not bodies for bodies in maps))
                    self.assertTrue(all(image.descriptor == -1 for image in images))
                    for descriptor in descriptors:
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)
                    self.fixture.assert_clean(session)
                finally:
                    inner.__exit__(None, None, None)
                    if shutdown:
                        outer.__exit__(None, None, None)

    def test_changed_source_and_failed_sealing_retire_created_backing(self):
        path = Path("/usr/bin/python3").resolve()
        original_lstat = Path.lstat

        def changed(source, *args, **kwargs):
            info = original_lstat(source, *args, **kwargs)
            if source == path:
                fields = list(info)
                fields[6] += 1
                return os.stat_result(fields)
            return info

        for failure in ("source", "seal", "interrupt"):
            budget = ProbeBudget()
            before = set(os.listdir("/proc/self/fd"))
            try:
                if failure == "source":
                    context = patch.object(Path, "lstat", changed)
                elif failure == "seal":
                    context = patch("scripts.validation_ownership.runtime_image.fcntl.fcntl",
                                    side_effect=OSError(errno.EIO, "modeled seal failure"))
                else:
                    context = patch("scripts.validation_ownership.runtime_image.os.write",
                                    side_effect=KeyboardInterrupt)
                with self.subTest(failure=failure), context:
                    with self.assertRaises((MakeProbeError, OSError, KeyboardInterrupt)):
                        RuntimeImage(path, budget)
                self.assertEqual(set(os.listdir("/proc/self/fd")), before)
            finally:
                budget.close()

    def test_generated_file_limit_remains_independent_and_bounded(self):
        for name in ("/usr/bin/python3", "/usr/bin/make"):
            with self.subTest(source=name):
                self.assert_generated_file_limit(Path(name).resolve())

    def assert_generated_file_limit(self, path):
        budget = ProbeBudget(Limits(file_bytes=min(path.stat().st_size - 1, Limits().file_bytes)))
        image = RuntimeImage(path, budget)
        try:
            self.assertGreater(len(image), budget.limits.file_bytes)
            with tempfile.TemporaryDirectory() as directory:
                generated = Path(directory) / "generated"
                with generated.open("wb") as stream:
                    stream.truncate(budget.limits.file_bytes + 1)
                with self.assertRaisesRegex(MakeProbeError, "exceeds byte bound"):
                    budget.read_bytes(generated, "output")
        finally:
            image.close()
            budget.close()
