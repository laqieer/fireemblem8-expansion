"""
Issue #6 Sprint 2 host tests -- the bundled generated-data CONTENT example.

Where possible these compile and *execute* the real, unmodified project
sources (include/expansion_starter_content.h, src/expansion_starter_content.c
and the public registry in src/expansion_mechanics.c) with a native host
compiler rather than pattern-matching their logic, matching
test_expansion_mechanics.py's approach. The small driver sources live in
tools/gba-playtest/tests/c/ and are test-only (never referenced by
modern.mk/Makefile).

They also pin the two structural properties the rest of the evidence chain
depends on:

  * the disabled translation unit emits NO data at all, so a default build's
    EWRAM/BSS layout -- and therefore every committed scenario probe address
    -- is untouched by adding this feature;
  * the enabled content registers once through the public mechanics API and
    applies its typed, capped avoid effect only to a bearer; and
  * the ORIGINAL authored display text is config-gated end to end: the
    generator writes nothing at EXPANSION_STARTER_CONTENT=0, the default
    objects contain no such string and no call into the content seam, and the
    content objects contain exactly the authored bytes and do read them
    through the production name path.
"""

import importlib
import os
import re
import runpy
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INCLUDE_DIRS = [REPO_ROOT / "include", REPO_ROOT / "include" / "generated"]

CONTENT_SRC = REPO_ROOT / "src" / "expansion_starter_content.c"
CONTENT_HEADER = REPO_ROOT / "include" / "expansion_starter_content.h"
MECHANICS_SRC = REPO_ROOT / "src" / "expansion_mechanics.c"
ITEMTEST_SRC = REPO_ROOT / "src" / "expansion_itemtest.c"
ITEMTEST_HEADER = REPO_ROOT / "include" / "expansion_itemtest.h"
RUNNER = REPO_ROOT / "tools" / "gba-playtest" / "run_item_expansion_checks.py"

BMITEM_SRC = REPO_ROOT / "src" / "bmitem.c"
BMBATTLE_SRC = REPO_ROOT / "src" / "bmbattle.c"
ITEMS_EXPANSION_JSON = REPO_ROOT / "src" / "data" / "items_expansion.json"
CONTENT_TEXT_HEADER_NAME = "items_expansion_content_text.h"
CONTENT_TEXT_CATALOG_NAME = "items_expansion_content_text.json"
TEST_ARTIFACTS_DIR = REPO_ROOT / "build" / "test-artifacts"

CC = shutil.which("gcc") or shutil.which("cc")
ARM_CC = shutil.which("arm-none-eabi-gcc")
SIZE = shutil.which("arm-none-eabi-size")
NM = shutil.which("arm-none-eabi-nm")

CONTENT_DEFINES = (
    "FE8_EXPANSION_STARTER_CONTENT=1",
    "FE8_EXPANSION_MECHANICS_HOOKS=1",
    "FE8_EXPANSION_MECHANICS_SAMPLE=1",
    "FE8_ITEM_ID_CAP=0xCE",
)

TEST_QUALITY_CASE_ID = "TC-TEST-QUALITY-001"
ISSUE_158_AUDIT_FILE = "tools/gba-playtest/tests/test_expansion_starter_content.py"
ISSUE_158_AUDIT_MIGRATIONS = {
    TEST_QUALITY_CASE_ID: {
    ISSUE_158_AUDIT_FILE + "::SourceHygieneTests.test_no_raw_numeric_content_item_id":
        ("StarterContentRegistrationHostTests",
         "test_enabled_content_registers_once_and_applies_bounded_avoid"),
    ISSUE_158_AUDIT_FILE + "::SourceHygieneTests.test_cap_dependency_error_stays_actionable":
        ("CompileTimeDependencyTests", "test_content_at_default_cap_fails"),
    ISSUE_158_AUDIT_FILE + "::SourceHygieneTests.test_no_double_slash_comments":
        ("StarterContentC89ContractTests",
         "test_enabled_content_translation_units_and_header_compile_as_c89"),
    ISSUE_158_AUDIT_FILE + "::SourceHygieneTests.test_content_registers_only_through_the_public_api":
        ("StarterContentRegistrationHostTests",
         "test_enabled_content_registers_once_and_applies_bounded_avoid"),
    ISSUE_158_AUDIT_FILE + "::SourceHygieneTests.test_single_builtin_install_point":
        ("StarterContentRegistrationHostTests",
         "test_enabled_content_registers_once_and_applies_bounded_avoid"),
    ISSUE_158_AUDIT_FILE + "::SourceHygieneTests.test_bmbattle_seam_is_not_content_aware":
        ("StarterContentBattleSeamTests",
         "test_generic_hook_is_only_content_path_and_poisoned_special_case_fails"),
    ISSUE_158_AUDIT_FILE + "::SourceHygieneTests.test_content_effect_is_bounded":
        ("StarterContentRegistrationHostTests",
         "test_enabled_content_registers_once_and_applies_bounded_avoid"),
    ISSUE_158_AUDIT_FILE + "::SourceHygieneTests.test_content_stat_differs_from_the_existing_sample":
        ("StarterContentRegistrationHostTests",
         "test_enabled_content_registers_once_and_applies_bounded_avoid"),
    ISSUE_158_AUDIT_FILE + "::SourceHygieneTests.test_probe_field_order_matches_the_c_struct":
        ("ItemExpansionProbeAbiTests",
         "test_probe_matches_every_runner_field_offset_and_width"),
    ISSUE_158_AUDIT_FILE + "::SourceHygieneTests.test_every_probe_field_is_a_u32_scalar":
        ("ItemExpansionProbeAbiTests",
         "test_probe_matches_every_runner_field_offset_and_width"),
    ISSUE_158_AUDIT_FILE + "::ContentTextGenerationTests.test_content_profile_emits_the_exact_authored_name":
        ("StarterContentRegistrationHostTests",
         "test_enabled_content_registers_once_and_applies_bounded_avoid"),
    ISSUE_158_AUDIT_FILE + "::ContentTextGenerationTests.test_generated_output_is_deterministic_and_path_independent":
        ("ContentTextGenerationTests",
         "test_generated_output_is_deterministic_and_path_independent"),
    ISSUE_158_AUDIT_FILE + "::ContentTextGenerationTests.test_no_committed_source_hand_holds_the_authored_text":
        ("ContentTextGenerationTests", "test_default_profile_generates_nothing"),
    ISSUE_158_AUDIT_FILE + "::ContentTextGenerationTests.test_texts_table_carries_no_content_message":
        ("scripts.generated_data.tests.test_items_expansion",
         "AuthoredContentRecordTests", "test_record_consumes_no_shared_message_slot"),
    ISSUE_158_AUDIT_FILE + "::ProductionNamePathTests.test_default_bmitem_has_no_content_seam":
        ("ProductionNamePathTests", "test_default_bmitem_has_no_content_seam"),
    ISSUE_158_AUDIT_FILE + "::ProductionNamePathTests.test_content_module_carries_exactly_the_generated_text":
        ("StarterContentRegistrationHostTests",
         "test_enabled_content_registers_once_and_applies_bounded_avoid"),
    ISSUE_158_AUDIT_FILE + "::ProductionNamePathTests.test_default_content_module_has_no_authored_text":
        ("ProductionNamePathTests", "test_default_content_module_has_no_authored_text"),
    ISSUE_158_AUDIT_FILE + "::ProductionNamePathTests.test_over_long_authoring_text_fails_the_build":
        ("ProductionNamePathTests", "test_over_long_authoring_text_fails_the_build"),
    },
}


def authored_name():
    """The ONE authoring source of truth for the bundled content text."""
    import json

    record = json.loads(ITEMS_EXPANSION_JSON.read_text(encoding="utf-8"))["items"][0]
    return record["authoringName"]


def generate_content_text(out_dir, content):
    """Run the real generator exactly as generated_data.mk does."""
    env = dict(os.environ)
    env["EXPANSION_STARTER_CONTENT"] = str(content)
    env["FE8_ITEM_ID_CAP"] = "0xCE"
    return subprocess.run(
        [sys.executable, "-m", "scripts.generated_data", "content-text",
         "--out-dir", str(out_dir)],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)


def _include_flags():
    flags = []
    for directory in INCLUDE_DIRS:
        flags += ["-I", str(directory)]
    return flags


def _test_tempdir():
    import tempfile

    TEST_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=str(TEST_ARTIFACTS_DIR))


def _arm_compile(work_dir, src, obj_name, defines=(), extra_includes=()):
    obj = Path(work_dir) / obj_name
    cmd = [ARM_CC, "-c", "-w", "-std=gnu89", "-mthumb"] + _include_flags()
    cmd += ["-I", str(REPO_ROOT)]
    for directory in extra_includes:
        cmd += ["-I", str(directory)]
    for define in defines:
        cmd += ["-D", define]
    cmd += [str(src), "-o", str(obj)]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr, obj


def _host_compile(work_dir, src, obj_name, defines=(), extra_includes=()):
    obj = Path(work_dir) / obj_name
    cmd = [CC, "-c", "-w", "-std=gnu89"] + _include_flags()
    cmd += ["-I", str(REPO_ROOT)]
    for directory in extra_includes:
        cmd += ["-I", str(directory)]
    for define in defines:
        cmd += ["-D", define]
    cmd += [str(src), "-o", str(obj)]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr, obj


def _host_link(work_dir, objects, executable_name):
    executable = Path(work_dir) / executable_name
    proc = subprocess.run(
        [CC, *map(str, objects), "-o", str(executable)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr, executable


def _referenced_symbols(obj):
    output = subprocess.run([NM, str(obj)], capture_output=True, text=True, check=True).stdout
    return {line.split()[-1] for line in output.splitlines() if line.split()}


class TestQualityAuditMappingTests(unittest.TestCase):
    """TC-TEST-QUALITY-001 requires every #158 audit record to retain a
    stronger executable, generated-output, or compiled evidence target."""

    def test_all_audited_rewrites_map_to_executable_evidence(self):
        migrations = ISSUE_158_AUDIT_MIGRATIONS[TEST_QUALITY_CASE_ID]

        self.assertEqual(len(migrations), 18)
        for audit_id, evidence in migrations.items():
            with self.subTest(audit_id=audit_id):
                self.assertTrue(audit_id.startswith(ISSUE_158_AUDIT_FILE + "::"))
                if len(evidence) == 2:
                    module = sys.modules[__name__]
                    class_name, method_name = evidence
                else:
                    module_name, class_name, method_name = evidence
                    module = importlib.import_module(module_name)
                test_case = getattr(module, class_name, None)
                self.assertIsNotNone(test_case)
                self.assertTrue(issubclass(test_case, unittest.TestCase))
                self.assertTrue(callable(getattr(test_case, method_name, None)))


@unittest.skipIf(CC is None, "no host C compiler")
class ItemExpansionProbeAbiTests(unittest.TestCase):
    """Compile a separate consumer against the public probe ABI."""

    def test_probe_matches_every_runner_field_offset_and_width(self):
        runner = runpy.run_path(str(RUNNER))
        fields = tuple(runner["PROBE_FIELDS"])
        expected_layout = {
            field: (4 * index, 4)
            for index, field in enumerate(fields)
        }
        base = 0x02000000
        runner_probes = runner["build_scenario"](base, 1)["checkpoints"][0]["probes"]
        self.assertEqual(len(runner_probes), len(fields))
        runner_layout = {
            field: (int(probe["address"], 16) - base, probe["size"])
            for field, probe in zip(fields, runner_probes)
        }
        self.assertEqual(runner_layout, expected_layout)

        with _test_tempdir() as tmp:
            work = Path(tmp)
            source = work / "item_probe_abi.c"
            field_rows = "\n".join(
                "    {{ \"{0}\", offsetof(struct ItemExpansionProbe, {0}), "
                "sizeof(((struct ItemExpansionProbe *)0)->{0}) }},".format(field)
                for field in fields
            )
            type_rows = "\n".join(
                "typedef char ItemExpansionProbe_{0}_is_u32["
                "__builtin_types_compatible_p(__typeof__(((struct "
                "ItemExpansionProbe *)0)->{0}), u32) ? 1 : -1];".format(field)
                for field in fields
            )
            source.write_text(
                "#define FE8_EXPANSION_ITEMTEST_ENABLED 1\n"
                "#define FE8_ITEM_ID_CAP 0xCE\n"
                "#include <stddef.h>\n"
                "#include <stdio.h>\n"
                "#include \"expansion_itemtest.h\"\n"
                "struct ProbeFieldLayout\n"
                "{\n"
                "    const char *name;\n"
                "    size_t offset;\n"
                "    size_t width;\n"
                "};\n"
                "static const struct ProbeFieldLayout sProbeFields[] =\n"
                "{\n"
                + field_rows
                + "\n};\n"
                + type_rows
                + "\n"
                "int main(void)\n"
                "{\n"
                "    size_t index;\n"
                "    printf(\"size %zu\\n\", sizeof(struct ItemExpansionProbe));\n"
                "    for (index = 0; index < sizeof(sProbeFields) / sizeof(sProbeFields[0]); index++)\n"
                "        printf(\"%s %zu %zu\\n\", sProbeFields[index].name,\n"
                "            sProbeFields[index].offset, sProbeFields[index].width);\n"
                "    return 0;\n"
                "}\n",
                encoding="utf-8",
            )
            executable = work / "item_probe_abi"
            completed = subprocess.run(
                [CC, "-std=gnu89", "-w", *_include_flags(), str(source), "-o", str(executable)],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            completed = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        lines = completed.stdout.splitlines()
        self.assertEqual(lines[0], "size {}".format(4 * len(fields)))
        consumer_layout = {}
        for line in lines[1:]:
            field, offset, width = line.split()
            self.assertNotIn(field, consumer_layout)
            consumer_layout[field] = (int(offset), int(width))
        self.assertEqual(consumer_layout, runner_layout)


@unittest.skipIf(ARM_CC is None, "no arm-none-eabi toolchain")
class StarterContentC89ContractTests(unittest.TestCase):
    """The enabled production translation units must reject non-C89 syntax."""

    def test_enabled_content_translation_units_and_header_compile_as_c89(self):
        with _test_tempdir() as tmp:
            work = Path(tmp)
            generated = work / "generated"
            self.assertEqual(generate_content_text(generated, 1).returncode, 0)
            headers = work / "headers"
            shutil.copytree(REPO_ROOT / "include", headers / "include")

            for path in headers.rglob("*.h"):
                path.write_text(
                    re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )

            def compile_source(source, obj_name):
                obj = work / obj_name
                command = [
                    ARM_CC,
                    "-c",
                    "-w",
                    "-std=c89",
                    "-pedantic-errors",
                    "-mthumb",
                    "-I",
                    str(headers / "include"),
                    "-I",
                    str(generated),
                    "-I",
                    str(headers),
                ]
                command.extend("-D" + define for define in CONTENT_DEFINES)
                command.extend([str(source), "-o", str(obj)])
                return subprocess.run(
                    command,
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                )

            for source in (CONTENT_SRC, MECHANICS_SRC, ITEMTEST_SRC):
                completed = compile_source(source, source.stem + ".o")
                self.assertEqual(
                    completed.returncode,
                    0,
                    "{} must compile as strict C89:\n{}{}".format(
                        source.name, completed.stdout, completed.stderr),
                )

            itemtest_header = headers / "include" / "expansion_itemtest.h"
            itemtest_header.write_text(
                "// strict-C89 mutation\n" + itemtest_header.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            completed = compile_source(ITEMTEST_SRC, "itemtest_mutated.o")
            self.assertNotEqual(
                completed.returncode,
                0,
                "a C++-style comment in expansion_itemtest.h must fail strict C89",
            )


@unittest.skipIf(ARM_CC is None or NM is None, "no arm-none-eabi toolchain")
class StarterContentBattleSeamTests(unittest.TestCase):
    """The compiled production battle object reaches content only through the
    generic mechanics hook."""

    def test_generic_hook_is_only_content_path_and_poisoned_special_case_fails(self):
        with _test_tempdir() as tmp:
            code, output, obj = _arm_compile(
                tmp, BMBATTLE_SRC, "bmbattle_content.o", CONTENT_DEFINES)
            self.assertEqual(code, 0, output)
            references = _referenced_symbols(obj)
            self.assertIn("ExpansionMechanicsApplyBattleStats", references)
            self.assertFalse(
                any("ExpansionStarterContent" in symbol for symbol in references),
                references,
            )

            poison = Path(tmp) / "starter_content_poison.h"
            poison.write_text(
                "#define ITEM_EXPANSION_CE ITEM_EXPANSION_CE_FORBIDDEN_IN_BMBATTLE\n",
                encoding="utf-8",
            )

            def compile_battle(source, obj_name):
                obj = Path(tmp) / obj_name
                command = [
                    ARM_CC,
                    "-c",
                    "-w",
                    "-std=gnu89",
                    "-mthumb",
                    *_include_flags(),
                    "-I",
                    str(REPO_ROOT),
                    "-include",
                    str(poison),
                ]
                command.extend("-D" + define for define in CONTENT_DEFINES)
                command.extend([str(source), "-o", str(obj)])
                return subprocess.run(
                    command,
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                )

            completed = compile_battle(BMBATTLE_SRC, "bmbattle_poisoned.o")
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            mutated = Path(tmp) / "bmbattle_special_case_mutation.c"
            mutated.write_text(
                BMBATTLE_SRC.read_text(encoding="utf-8")
                + "\nint ItemExpansionSpecialCaseMutation(int item)\n"
                + "{\n"
                + "    if (item == ITEM_EXPANSION_CE)\n"
                + "        return 1;\n"
                + "    return 0;\n"
                + "}\n",
                encoding="utf-8",
            )
            completed = compile_battle(mutated, "bmbattle_mutated.o")
            self.assertNotEqual(
                completed.returncode,
                0,
                "a direct starter-content item special case must fail the generic seam contract",
            )


@unittest.skipIf(CC is None, "no host C compiler")
class StarterContentRegistrationHostTests(unittest.TestCase):
    """Execute the real enabled registry and content implementation through
    their public APIs with generated authored text."""

    def test_enabled_content_registers_once_and_applies_bounded_avoid(self):
        import json

        with _test_tempdir() as tmp:
            work = Path(tmp)
            generated = work / "generated"
            self.assertEqual(generate_content_text(generated, 1).returncode, 0)
            driver = work / "starter_content_driver.c"
            driver.write_text(
                "#include <stdio.h>\n"
                "#include <string.h>\n"
                "#include \"global.h\"\n"
                "#include \"bmbattle.h\"\n"
                "#include \"bmitem.h\"\n"
                "#include \"bmlib.h\"\n"
                "#include \"expansion_mechanics.h\"\n"
                "#include \"expansion_starter_content.h\"\n"
                "#include \"constants/items_expansion.h\"\n"
                "#define CHECK(condition) do { if (!(condition)) return 1; } while (0)\n"
                "int GetUnitItemSlot(struct Unit *unit, int itemIndex)\n"
                "{\n"
                "    int index;\n"
                "    for (index = 0; index < UNIT_ITEM_COUNT; index++)\n"
                "        if (ITEM_INDEX(unit->items[index]) == itemIndex)\n"
                "            return index;\n"
                "    return -1;\n"
                "}\n"
                "void CopyString(char *dst, const char *src)\n"
                "{\n"
                "    strcpy(dst, src);\n"
                "}\n"
                "int main(void)\n"
                "{\n"
                "    struct BattleUnit subject;\n"
                "    int index;\n"
                "    int contentCount = 0;\n"
                "    char *name;\n"
                "    ExpansionMechanicsReset();\n"
                "    memset(&gExpansionMechanicsProbe, 0, sizeof(gExpansionMechanicsProbe));\n"
                "    ExpansionMechanicsInstallBuiltins();\n"
                "    ExpansionMechanicsInstallBuiltins();\n"
                "    for (index = 0; index < ExpansionMechanicsCount(); index++)\n"
                "        if (strcmp(ExpansionMechanicsKeyAt(index), EXPANSION_STARTER_CONTENT_KEY) == 0)\n"
                "            contentCount++;\n"
                "    CHECK(ExpansionMechanicsCount() == 2);\n"
                "    CHECK(contentCount == 1);\n"
                "    CHECK(gExpansionMechanicsProbe.registerOkCount == 2);\n"
                "    CHECK(gExpansionMechanicsProbe.registerErrCount == 0);\n"
                "    CHECK(ExpansionStarterContentItemId() == ITEM_EXPANSION_CE);\n"
                "    name = ExpansionStarterContentItemName(ITEM_EXPANSION_CE);\n"
                "    CHECK(name != NULL);\n"
                "    CHECK(strcmp(name, " + json.dumps(authored_name()) + ") == 0);\n"
                "    CHECK(ExpansionStarterContentItemName(ITEM_ID_SENTINEL) == NULL);\n"
                "    memset(&subject, 0, sizeof(subject));\n"
                "    subject.unit.items[0] = ITEM_EXPANSION_CE;\n"
                "    subject.unit.maxHP = 20;\n"
                "    subject.unit.curHP = 20;\n"
                "    subject.battleDefense = 5;\n"
                "    subject.battleAvoidRate = 50;\n"
                "    ExpansionMechanicsApplyBattleStats(&subject, NULL, 0);\n"
                "    CHECK(subject.battleDefense == 6);\n"
                "    CHECK(subject.battleAvoidRate == 50 + EXPANSION_STARTER_CONTENT_AVOID_BONUS);\n"
                "    memset(&subject, 0, sizeof(subject));\n"
                "    subject.unit.items[0] = ITEM_EXPANSION_CE;\n"
                "    subject.battleAvoidRate = 118;\n"
                "    ExpansionMechanicsApplyBattleStats(&subject, NULL, 0);\n"
                "    CHECK(subject.battleAvoidRate == EXPANSION_STARTER_CONTENT_AVOID_CAP);\n"
                "    memset(&subject, 0, sizeof(subject));\n"
                "    subject.battleAvoidRate = 50;\n"
                "    ExpansionMechanicsApplyBattleStats(&subject, NULL, 0);\n"
                "    CHECK(subject.battleAvoidRate == 50);\n"
                "    return 0;\n"
                "}\n",
                encoding="utf-8",
            )
            code, output, mechanics = _host_compile(
                work, MECHANICS_SRC, "mechanics.o", CONTENT_DEFINES, [generated])
            self.assertEqual(code, 0, output)
            code, output, content = _host_compile(
                work, CONTENT_SRC, "content.o", CONTENT_DEFINES, [generated])
            self.assertEqual(code, 0, output)
            code, output, driver_obj = _host_compile(
                work, driver, "driver.o", CONTENT_DEFINES, [generated])
            self.assertEqual(code, 0, output)
            code, output, executable = _host_link(
                work, [mechanics, content, driver_obj], "starter_content_host")
            self.assertEqual(code, 0, output)
            completed = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


@unittest.skipIf(ARM_CC is None or SIZE is None, "no arm-none-eabi toolchain")
class DisabledBuildLayoutTests(unittest.TestCase):
    """A default (content-off) build must add no RAM at all: every committed
    runtime scenario pins absolute EWRAM probe addresses, so a new
    always-linked data object would silently invalidate them."""

    def test_disabled_object_has_no_data_or_bss(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            code, output, obj = _arm_compile(tmp, CONTENT_SRC, "content_off.o")
            self.assertEqual(code, 0, output)
            sizes = subprocess.run([SIZE, "-A", str(obj)], capture_output=True,
                                   text=True, check=True).stdout
            for section in (".data", ".bss", "ewram_data"):
                for line in sizes.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == section:
                        self.assertEqual(
                            int(parts[1]), 0,
                            "disabled content TU emits {} bytes of {}".format(
                                parts[1], section))


@unittest.skipIf(ARM_CC is None, "no arm-none-eabi toolchain")
class CompileTimeDependencyTests(unittest.TestCase):
    """Both content dependencies are hard compile errors, not warnings."""

    def _compile(self, defines):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # The content profile also needs the build-local generated text
            # header on the include path (exactly as modern.mk arranges it),
            # so a *dependency* failure below is the dependency's own #error
            # and never a missing-file artefact of the test setup.
            generated = Path(tmp) / "generated"
            self.assertEqual(generate_content_text(generated, 1).returncode, 0)
            return _arm_compile(tmp, CONTENT_SRC, "probe.o", defines, [generated])[:2]

    def _assert_actionable_cap_diagnostic(self, output):
        self.assertIn("expanded item cap", output)
        self.assertIn("FE8_ITEM_ID_CAP=0xCE", output)

    def test_content_without_hooks_fails(self):
        code, output = self._compile(
            ["FE8_EXPANSION_STARTER_CONTENT=1", "FE8_ITEM_ID_CAP=0xCE"])
        self.assertNotEqual(code, 0)
        self.assertIn("FE8_EXPANSION_MECHANICS_HOOKS=1", output)

    def test_content_at_default_cap_fails(self):
        code, output = self._compile(
            ["FE8_EXPANSION_STARTER_CONTENT=1", "FE8_EXPANSION_MECHANICS_HOOKS=1"])
        self.assertNotEqual(code, 0)
        self._assert_actionable_cap_diagnostic(output)

    def test_generic_only_cap_diagnostic_fixture_is_rejected(self):
        with self.assertRaises(AssertionError):
            self._assert_actionable_cap_diagnostic("error: expanded item cap")

    def test_full_content_profile_compiles(self):
        code, output = self._compile([
            "FE8_EXPANSION_STARTER_CONTENT=1",
            "FE8_EXPANSION_MECHANICS_HOOKS=1",
            "FE8_EXPANSION_MECHANICS_SAMPLE=1",
            "FE8_ITEM_ID_CAP=0xCE",
        ])
        self.assertEqual(code, 0, output)

    def test_default_build_compiles(self):
        code, output = self._compile([])
        self.assertEqual(code, 0, output)


class ContentTextGenerationTests(unittest.TestCase):
    """The ORIGINAL authored display text is generated, config-gated and
    build-local -- never a message appended to the shared, Huffman-compressed
    table (which would re-encode a DEFAULT build's text blob)."""

    def test_default_profile_generates_nothing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = generate_content_text(tmp, 0)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            for name in (CONTENT_TEXT_HEADER_NAME, CONTENT_TEXT_CATALOG_NAME):
                self.assertFalse(
                    (Path(tmp) / name).exists(),
                    "a default build must generate no content text artifact")

    def test_default_profile_removes_a_stale_artifact(self):
        """A previous content build must never leave a string table behind
        for a later default build to pick up."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(generate_content_text(tmp, 1).returncode, 0)
            self.assertTrue((Path(tmp) / CONTENT_TEXT_HEADER_NAME).exists())
            self.assertEqual(generate_content_text(tmp, 0).returncode, 0)
            self.assertFalse((Path(tmp) / CONTENT_TEXT_HEADER_NAME).exists())
            self.assertFalse((Path(tmp) / CONTENT_TEXT_CATALOG_NAME).exists())

    def test_content_profile_emits_the_exact_authored_name(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = generate_content_text(tmp, 1)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            header = (Path(tmp) / CONTENT_TEXT_HEADER_NAME).read_text(encoding="utf-8")
            name = authored_name()
            self.assertIn('"{}"'.format(name), header)
            self.assertIn("ITEM_EXPANSION_CE", header)
            self.assertIn("AUTO-GENERATED", header)

            capacity = int(re.search(
                r"#define EXPANSION_CONTENT_TEXT_NAME_CAPACITY (\d+)", header).group(1))
            self.assertEqual(capacity, len(name) + 1)

            buffer_size = int(re.search(
                r"#define EXPANSION_STARTER_CONTENT_NAME_BUFFER\s+(\d+)",
                CONTENT_HEADER.read_text(encoding="utf-8")).group(1))
            self.assertLessEqual(capacity, buffer_size)

            catalog = json.loads((Path(tmp) / CONTENT_TEXT_CATALOG_NAME).read_text(
                encoding="utf-8"))
            entry = catalog["items"][0]
            self.assertEqual(entry["authoringName"], name)
            self.assertTrue(entry["authoringDescription"])
            self.assertIn("not shown in game", entry["runtimeText"]["description"])

    def test_generated_output_is_deterministic_and_path_independent(self):
        import tempfile

        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            self.assertEqual(generate_content_text(one, 1).returncode, 0)
            self.assertEqual(generate_content_text(two, 1).returncode, 0)
            for name in (CONTENT_TEXT_HEADER_NAME, CONTENT_TEXT_CATALOG_NAME):
                first = (Path(one) / name).read_text(encoding="utf-8")
                second = (Path(two) / name).read_text(encoding="utf-8")
                self.assertEqual(first, second)
                self.assertNotIn(str(REPO_ROOT), first)

@unittest.skipIf(ARM_CC is None or NM is None, "no arm-none-eabi toolchain")
class ProductionNamePathTests(unittest.TestCase):
    """src/bmitem.c's GetItemName() -- the ONE function every production item
    name consumer goes through -- reads the authored text in the content
    profile and is preprocessed back to vanilla in a default build."""

    def _symbols(self, obj):
        out = subprocess.run([NM, str(obj)], capture_output=True, text=True,
                             check=True).stdout
        return out

    def test_default_bmitem_has_no_content_seam(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            code, output, obj = _arm_compile(tmp, BMITEM_SRC, "bmitem_off.o")
            self.assertEqual(code, 0, output)
            self.assertNotIn("ExpansionStarterContent", self._symbols(obj))
            self.assertNotIn(authored_name().encode("ascii"), obj.read_bytes())

    def test_content_bmitem_reads_the_typed_accessor(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            generated = Path(tmp) / "generated"
            self.assertEqual(generate_content_text(generated, 1).returncode, 0)
            code, output, obj = _arm_compile(
                tmp, BMITEM_SRC, "bmitem_on.o", CONTENT_DEFINES, [generated])
            self.assertEqual(code, 0, output)
            symbols = self._symbols(obj)
            self.assertIn("U ExpansionStarterContentItemName", symbols)

    def test_content_module_carries_exactly_the_generated_text(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            generated = Path(tmp) / "generated"
            self.assertEqual(generate_content_text(generated, 1).returncode, 0)
            code, output, obj = _arm_compile(
                tmp, CONTENT_SRC, "content_on.o", CONTENT_DEFINES, [generated])
            self.assertEqual(code, 0, output)
            self.assertIn(authored_name().encode("ascii"), obj.read_bytes())
            self.assertIn("T ExpansionStarterContentItemName", self._symbols(obj))

    def test_default_content_module_has_no_authored_text(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            code, output, obj = _arm_compile(tmp, CONTENT_SRC, "content_off.o")
            self.assertEqual(code, 0, output)
            self.assertNotIn(authored_name().encode("ascii"), obj.read_bytes())
            self.assertNotIn("ExpansionStarterContentItemName", self._symbols(obj))

    def test_over_long_authoring_text_fails_the_build(self):
        """The generated capacity is statically asserted against the module's
        buffer, so authoring text that cannot fit is a compile error rather
        than silent truncation on screen."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            generated = Path(tmp) / "generated"
            self.assertEqual(generate_content_text(generated, 1).returncode, 0)
            header = generated / CONTENT_TEXT_HEADER_NAME
            text = header.read_text(encoding="utf-8")
            text = re.sub(r"(#define EXPANSION_CONTENT_TEXT_NAME_CAPACITY )\d+",
                          r"\g<1>999", text)
            header.write_text(text, encoding="utf-8")
            code, output, _ = _arm_compile(
                tmp, CONTENT_SRC, "content_overlong.o", CONTENT_DEFINES, [generated])
            self.assertNotEqual(code, 0)
            self.assertIn("expansion_starter_content_name_fits_buffer", output)


if __name__ == "__main__":
    unittest.main()
