"""Exercise the CodeQL target with real GCC and disposable fixture sources."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
HARNESSES = (
    "sio_protocol_host_test",
    "runtime_bounds_host_test",
    "png_bounds_host_test",
)
SOURCES = {
    HARNESSES[0]: ("src/sio_core.c",),
    HARNESSES[1]: ("src/bmtrick.c", "src/event.c", "src/eventscr.c"),
    HARNESSES[2]: ("tools/gbagfx/convert_png.c",),
}
ANALYZED = ("src/sio_core.c", "src/event.c", "tools/gbagfx/convert_png.c")


def overlay(source, destination, excluded):
    destination.mkdir()
    for path in source.iterdir():
        if path.name not in excluded:
            (destination / path.name).symlink_to(path, target_is_directory=path.is_dir())


def marker(path):
    return "probe_" + path.replace("/", "_").replace(".", "_")


class CodeqlMakeSequencingTests(unittest.TestCase):
    def run_target(self, *, failing="", defective="", selected_make=None, inspect_binary=False,
                   probe_directory_options=False):
        compiler = shutil.which("gcc")
        make = selected_make or shutil.which("make")
        self.assertIsNotNone(compiler, "GCC is required by the CodeQL analyzer lane")
        self.assertIsNotNone(make, "GNU Make is required")
        if not os.path.isabs(make):
            make = os.getcwd() + "/" + make

        with tempfile.TemporaryDirectory(prefix="codeql-sequencing-") as directory:
            root = Path(directory)
            work = root / "work"
            excluded = {"src", "tests", "tools", "build", "GNUmakefile", "config.autotools.mk"}
            overlay(ROOT, work, excluded)
            (work / "build").mkdir()
            overlay(ROOT / "src", work / "src",
                    {"sio_core.c", "bmtrick.c", "event.c", "eventscr.c"})
            overlay(ROOT / "tests", work / "tests", {"codeql"})
            (work / "tests" / "codeql").mkdir()
            (work / "tools").mkdir()
            for path in (ROOT / "tools").iterdir():
                if path.is_dir():
                    tool = work / "tools" / path.name
                    tool.mkdir()
                    (tool / "Makefile").write_text("all:\n\t@:\n%:\n\t@:\n", encoding="utf-8")

            for path in {source for sources in SOURCES.values() for source in sources}:
                body = f"int {marker(path)}(void) {{ return 1; }}\n"
                if path == defective:
                    if path == "src/event.c":
                        body += "int sentinel(int x) { int i; if (x) i = 1; return i; }\n"
                    else:
                        body += ("#include <stdlib.h>\n"
                                 "void sentinel(int x) { void *p = malloc(4); "
                                 "if (x) free(p); if (x > 0) free(p); }\n")
                (work / path).write_text(body, encoding="utf-8")

            for name, sources in SOURCES.items():
                declarations = "".join(f"int {marker(path)}(void);\n" for path in sources)
                calls = " + ".join(f"{marker(path)}()" for path in sources)
                body = (
                    "#include <limits.h>\n#include <stdio.h>\n#include <stdlib.h>\n"
                    "#include <string.h>\n" + declarations +
                    "int main(int argc, char **argv) {\n"
                    "  volatile int i = argc - 1, n = INT_MAX;\n"
                    "  char buf[2] = {0, 0}; volatile int x = n + i, y = buf[i];\n"
                    "  const char *path = getenv(\"PROBE_LOG\");\n"
                    "  const char *failure = getenv(\"PROBE_FAIL\");\n"
                    f"  if ({calls} != {len(sources)}) return 6;\n"
                    "  FILE *out = fopen(path, \"a\"); (void) argv; (void) x; (void) y;\n"
                    "  if (!out) return 3;\n"
                    f"  fprintf(out, \"harness {name} %s %s\\n\", "
                    "getenv(\"ASAN_OPTIONS\"), getenv(\"UBSAN_OPTIONS\"));\n"
                    "  if (fclose(out)) return 4;\n"
                    f"  return failure && strcmp(failure, \"{name}\") == 0 ? 17 : 0;\n"
                    "}\n"
                )
                (work / "tests" / "codeql" / (name + ".c")).write_text(
                    body, encoding="utf-8"
                )

            bindir = root / "bin"
            bindir.mkdir()
            pkg = bindir / "pkg-config"
            pkg.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            pkg.chmod(0o755)
            submake = bindir / "submake"
            submake.write_text(
                "#!/usr/bin/env python3\nimport os, sys\n"
                "if 'codeql-fanalyzer-test' in sys.argv[1:]:\n"
                "    with open(os.environ['PROBE_LOG'], 'a') as f: f.write('analyzer\\n')\n"
                "os.execv(os.environ['PROBE_MAKE'], [os.environ['PROBE_MAKE'], *sys.argv[1:]])\n",
                encoding="utf-8",
            )
            submake.chmod(0o755)
            python = bindir / "python"
            python.write_text(
                "#!/usr/bin/env python3\nimport os, sys\n"
                "if sys.argv[1:3] == ['-m', 'unittest']: raise SystemExit(0)\n"
                f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n",
                encoding="utf-8",
            )
            python.chmod(0o755)

            log = root / "events"
            output = root / "output"
            environment = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"],
                               PROBE_LOG=str(log), PROBE_FAIL=failing, PROBE_MAKE=make)
            for name in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEFILES", "MAKELEVEL"):
                environment.pop(name, None)
            result = subprocess.run(
                [make, "--no-print-directory", "codeql-alerts-test",
                 f"HOST_CC={compiler}", f"CODEQL_TEST_DIR={output}",
                 "CODEQL_REQUIRE_FANALYZER=1",
                 "ASSET_MAINTENANCE_GOALS=codeql-alerts-test codeql-fanalyzer-test",
                 "AUTOTOOLS_CONFIG_MK=config.autotools.mk",
                 "MAKE_RESTARTS=1", f"MAKE={make if selected_make else submake}",
                 f"PYTHON={python}"],
                cwd=work, env=environment, text=True, capture_output=True, timeout=60,
            )
            events = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
            if probe_directory_options and result.returncode == 0:
                probe_log = root / "option-events"
                probe_output = root / "option-output"
                probe_environment = dict(environment, PROBE_LOG=str(probe_log),
                                         PROBE_MAKE_LOG=str(root / "option-make-log"))
                wrapper = make if selected_make else str(submake)
                for args in (
                    ("-C", ".", "codeql-fanalyzer-test"),
                    ("-C", "tools/gbagfx"),
                    ("-Ctools/gbagfx",),
                    ("--directory", "tools/gbagfx"),
                    ("--directory=tools/gbagfx",),
                ):
                    probe = subprocess.run(
                        [wrapper, "--no-print-directory", *args, f"HOST_CC={compiler}",
                         f"CODEQL_TEST_DIR={probe_output}", "CODEQL_REQUIRE_FANALYZER=1",
                         "ASSET_MAINTENANCE_GOALS=codeql-fanalyzer-test",
                         "AUTOTOOLS_CONFIG_MK=config.autotools.mk", "MAKE_RESTARTS=1"],
                        cwd=work, env=probe_environment, text=True, capture_output=True, timeout=60,
                    )
                    self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
                self.assertEqual(probe_log.read_text(encoding="utf-8").splitlines(), ["analyzer"])
                for name in ("sio_core", "event", "convert_png"):
                    self.assertTrue((probe_output / (name + "_analyzer.o")).is_file())
            if inspect_binary and result.returncode == 0:
                nm = shutil.which("nm")
                self.assertIsNotNone(nm, "GNU binutils is required")
                for name in HARNESSES:
                    symbols = subprocess.run(
                        [nm, "-g", str(output / name)], text=True, capture_output=True,
                        check=True, timeout=30,
                    ).stdout
                    for symbol in ("__asan_init", "__asan_report",
                                   "__ubsan_handle_add_overflow", "__ubsan_handle_out_of_bounds"):
                        with self.subTest(harness=name, symbol=symbol):
                            self.assertIn(symbol, symbols)
            return result, events

    def test_all_harnesses_run_before_real_analyzer(self):
        result, events = self.run_target(inspect_binary=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected = {f"harness {name} detect_leaks=0 halt_on_error=1" for name in HARNESSES}
        self.assertEqual(len(events), 4, events)
        self.assertEqual(set(events[:-1]), expected)
        self.assertEqual(events[-1], "analyzer")

    def test_configured_source_uses_fixture_makefile(self):
        with tempfile.TemporaryDirectory(prefix="codeql-configured-source-") as directory:
            source = Path(directory) / "source"
            overlay(ROOT, source, {"GNUmakefile", "config.autotools.mk"})
            (source / "GNUmakefile").write_text(
                "%:\n\t$(MAKE) -C nonexistent $@\n", encoding="utf-8"
            )
            (source / "config.autotools.mk").write_text(
                "$(error configured-source fragment reached the fixture)\n", encoding="utf-8"
            )
            with mock.patch.dict(globals(), ROOT=source):
                result, events = self.run_target()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(events), 4, events)
        self.assertEqual(events[-1], "analyzer")

    def test_each_harness_failure_stops_before_analyzer(self):
        for name in HARNESSES:
            with self.subTest(harness=name):
                result, events = self.run_target(failing=name)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(any(event.startswith(f"harness {name} ") for event in events))
                self.assertNotIn("analyzer", events)

    def test_each_required_analyzer_source_is_really_analyzed(self):
        for path in ANALYZED:
            with self.subTest(source=path):
                result, events = self.run_target(defective=path)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(events[-1:], ["analyzer"])
                diagnostic = "use-of-uninitialized-value" if path == "src/event.c" else "double-free"
                self.assertIn(path, result.stderr)
                self.assertIn(f"analyzer-{diagnostic}", result.stderr)

    def check_selected_make(self, probe_directory_options=False):
        real_make = shutil.which("make")
        self.assertIsNotNone(real_make)
        with tempfile.TemporaryDirectory(prefix="codeql-make-") as directory:
            root = Path(directory)
            actual = root / "actual"
            (actual / "target").mkdir(parents=True)
            (actual / "bin").mkdir()
            (root / "bin").mkdir()
            (root / "link").symlink_to(actual / "target", target_is_directory=True)
            wrapper = actual / "bin" / "wrapper"
            wrapper.write_text(
                "#!/usr/bin/env python3\nimport os, sys\n"
                "if 'codeql-alerts-test' in sys.argv[1:] or 'codeql-fanalyzer-test' in sys.argv[1:]:\n"
                "    with open(os.environ['PROBE_MAKE_LOG'], 'a') as f: "
                "f.write(sys.argv[0] + ' ' + ' '.join(sys.argv[1:]) + '\\n')\n"
                "if 'codeql-fanalyzer-test' in sys.argv[1:]:\n"
                "    with open(os.environ['PROBE_LOG'], 'a') as f: f.write('analyzer\\n')\n"
                f"os.execv({real_make!r}, [{real_make!r}, *sys.argv[1:]])\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            (actual / "bin" / "make").symlink_to(wrapper)
            wrong = root / "bin" / "make"
            wrong.write_text("#!/bin/sh\nexit 87\n", encoding="utf-8")
            wrong.chmod(0o755)
            selected = str(root) + "/link/../bin/make"
            log = root / "make-log"
            with mock.patch.dict(os.environ, {"PROBE_MAKE_LOG": str(log)}):
                result, _ = self.run_target(selected_make=selected,
                                            probe_directory_options=probe_directory_options)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 2, calls)
            self.assertTrue(all(call.startswith(selected + " ") for call in calls), calls)
            self.assertIn("codeql-alerts-test", calls[0])
            self.assertIn("codeql-fanalyzer-test", calls[1])

    def test_recursive_analyzer_uses_selected_make(self):
        self.check_selected_make()

    def test_equivalent_recursive_make_directory_options(self):
        result, events = self.run_target(probe_directory_options=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(events[-1:], ["analyzer"])
        self.check_selected_make(probe_directory_options=True)


if __name__ == "__main__":
    unittest.main()
