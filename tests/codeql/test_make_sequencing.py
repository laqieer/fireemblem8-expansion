"""Exercise the CodeQL target with real GCC and disposable fixture sources."""

from __future__ import annotations

import os
import re
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
    def run_target(self, *, failing="", defective="", diagnose_all=False,
                   inspect_binary=False, analyzer_alias=False):
        compiler = shutil.which("gcc")
        make = shutil.which("make")
        self.assertIsNotNone(compiler, "GCC is required by the CodeQL analyzer lane")
        self.assertIsNotNone(make, "GNU Make is required")
        if not os.path.isabs(make):
            make = os.getcwd() + "/" + make

        with tempfile.TemporaryDirectory(prefix="codeql-sequencing-") as directory:
            root = Path(directory)
            work = root / "work"
            excluded = {"src", "tests", "tools", "build", "GNUmakefile", "config.autotools.mk"}
            if analyzer_alias:
                excluded.add("Makefile")
            overlay(ROOT, work, excluded)
            if analyzer_alias:
                (work / "Makefile").write_text(
                    (ROOT / "Makefile").read_text(encoding="utf-8")
                    + "\n.PHONY: analyzer-alias\nanalyzer-alias: codeql-fanalyzer-test\n",
                    encoding="utf-8",
                )
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
                if diagnose_all and path in ANALYZED:
                    body += ('#pragma GCC diagnostic warning "-Wanalyzer-double-free"\n'
                             '#pragma GCC diagnostic warning "-Wanalyzer-use-of-uninitialized-value"\n'
                             '#pragma GCC diagnostic warning "-Wanalyzer-malloc-leak"\n')
                if path == defective or (diagnose_all and path in ANALYZED):
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
            observed_cc = bindir / "gcc-observer"
            observed_cc.write_text(
                "#!/usr/bin/env python3\nimport os, sys\n"
                "if '-fanalyzer' in sys.argv[1:]:\n"
                "    with open(os.environ['PROBE_LOG'], 'a') as f: f.write('analyzer\\n')\n"
                "os.execv(os.environ['PROBE_CC'], [os.environ['PROBE_CC'], *sys.argv[1:]])\n",
                encoding="utf-8",
            )
            observed_cc.chmod(0o755)
            submake = make
            if analyzer_alias:
                submake = bindir / "submake"
                submake.write_text(
                    "#!/usr/bin/env python3\nimport os, sys\n"
                    "args = ['analyzer-alias' if a == 'codeql-fanalyzer-test' else a "
                    "for a in sys.argv[1:]]\n"
                    "if args != sys.argv[1:]:\n"
                    "    with open(os.environ['PROBE_LOG'], 'a') as f: f.write('alias\\n')\n"
                    "os.execv(os.environ['PROBE_MAKE'], [os.environ['PROBE_MAKE'], *args])\n",
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
                               PROBE_LOG=str(log), PROBE_FAIL=failing, PROBE_MAKE=make,
                               PROBE_CC=compiler, LC_ALL="C")
            for name in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEFILES", "MAKELEVEL"):
                environment.pop(name, None)
            result = subprocess.run(
                [make, "--no-print-directory", "codeql-alerts-test",
                 f"HOST_CC={observed_cc}", f"CODEQL_TEST_DIR={output}",
                 "CODEQL_REQUIRE_FANALYZER=1",
                 "ASSET_MAINTENANCE_GOALS=codeql-alerts-test codeql-fanalyzer-test",
                 "AUTOTOOLS_CONFIG_MK=config.autotools.mk",
                 "MAKE_RESTARTS=1", f"MAKE={submake}",
                 f"PYTHON={python}"],
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
        result, events = self.run_target(inspect_binary=True, diagnose_all=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_harnesses_before_analyzer(events)
        for path in ANALYZED:
            with self.subTest(source=path):
                diagnostic = "use-of-uninitialized-value" if path == "src/event.c" else "double-free"
                self.assertRegex(
                    result.stderr,
                    rf"(?m)^(?:.*/)?{re.escape(path)}:\d+:\d+: warning: .* "
                    rf"\[-Wanalyzer-{diagnostic}\]$",
                )

    def assert_harnesses_before_analyzer(self, events):
        expected = {f"harness {name} detect_leaks=0 halt_on_error=1" for name in HARNESSES}
        self.assertIn("analyzer", events)
        first = events.index("analyzer")
        self.assertEqual(first, len(HARNESSES), events)
        self.assertEqual(set(events[:first]), expected)
        self.assertTrue(all(event == "analyzer" for event in events[first:]), events)

    def test_equivalent_analyzer_alias_runs_after_harnesses(self):
        result, events = self.run_target(analyzer_alias=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_harnesses_before_analyzer([event for event in events if event != "alias"])
        if "alias" not in events:
            self.skipTest("the recipe does not invoke the recursive analyzer goal")
        self.assertEqual(events.count("alias"), 1, events)
        self.assertLess(events.index("alias"), events.index("analyzer"))

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
        self.assert_harnesses_before_analyzer(events)

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


if __name__ == "__main__":
    unittest.main()
