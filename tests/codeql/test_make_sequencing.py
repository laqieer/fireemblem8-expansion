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
    def run_target(self, *, failing="", defective="", selected_make=None, inspect_binary=False):
        compiler = shutil.which("gcc")
        make = selected_make or shutil.which("make")
        self.assertIsNotNone(compiler, "GCC is required by the CodeQL analyzer lane")
        self.assertIsNotNone(make, "GNU Make is required")
        if not os.path.isabs(make):
            make = os.getcwd() + "/" + make

        with tempfile.TemporaryDirectory(prefix="codeql-sequencing-") as directory:
            root = Path(directory)
            work = root / "work"
            overlay(ROOT, work, {"src", "tests", "tools", "build"})
            (work / "build").mkdir()
            overlay(ROOT / "src", work / "src",
                    {"sio_core.c", "bmtrick.c", "event.c", "eventscr.c"})
            overlay(ROOT / "tests", work / "tests", {"codeql"})
            (work / "tests" / "codeql").mkdir()
            overlay(ROOT / "tools", work / "tools", {"gbagfx"})
            overlay(ROOT / "tools" / "gbagfx", work / "tools" / "gbagfx",
                    {"convert_png.c"})

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
                "    os.execv(os.environ['PROBE_MAKE'], [os.environ['PROBE_MAKE'], *sys.argv[1:]])\n"
                "if '-C' in sys.argv[1:]: raise SystemExit(0)\n"
                "raise SystemExit('unexpected recursive make')\n",
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
                 "MAKE_RESTARTS=1", f"MAKE={submake}", f"PYTHON={python}"],
                cwd=work, env=environment, text=True, capture_output=True, timeout=60,
            )
            events = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
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

    def test_recursive_analyzer_uses_selected_make(self):
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
                "with open(os.environ['PROBE_MAKE_LOG'], 'a') as f: "
                "f.write(sys.argv[0] + ' ' + ' '.join(sys.argv[1:]) + '\\n')\n"
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
                result, _ = self.run_target(selected_make=selected)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 2, calls)
            self.assertTrue(all(call.startswith(selected + " ") for call in calls), calls)
            self.assertIn("codeql-alerts-test", calls[0])
            self.assertIn("codeql-fanalyzer-test", calls[1])


if __name__ == "__main__":
    unittest.main()
