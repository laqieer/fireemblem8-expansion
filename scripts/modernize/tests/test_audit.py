import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "audit.py"
FIXTURE = HERE / "fixtures" / "repo"
SPEC = importlib.util.spec_from_file_location("modernize_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def copy_fixture(self, destination: Path) -> Path:
        root = destination / "repo"
        shutil.copytree(FIXTURE, root)
        (root / "scripts" / "modernize").mkdir(parents=True)
        (root / "scripts" / "modernize" / "decisions.json").write_text(
            '{"schema_version": 1, "decisions": []}\n', encoding="utf-8"
        )
        return root

    def test_all_scanner_classes_and_narrow_suppressions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            inventory = audit.scan_repository(
                root, root / "scripts" / "modernize" / "decisions.json"
            )

        categories = set(inventory["summary"]["by_category"])
        self.assertEqual(categories, set(audit.CATEGORY_META))
        evidence = [finding["evidence"] for finding in inventory["findings"]]
        self.assertNotIn("Documentation only: 0x08ABCDEF", evidence)
        naked = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "naked-function"
        ]
        self.assertEqual(len(naked), 2)
        self.assertTrue(any("__attribute__((naked))" in item["evidence"] for item in naked))
        mismatched = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "mismatched-signature"
        ]
        self.assertEqual(len(mismatched), 1)
        inline_asm = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "inline-asm"
        ]
        self.assertEqual(len(inline_asm), 1)
        fixed_bases = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "fixed-rom-base"
        ]
        self.assertTrue(
            any("arm_compressing_linker" in item["evidence"] for item in fixed_bases)
        )
        self.assertFalse(any(item["file"] == "scripts/tool.py" for item in fixed_bases))
        self.assertFalse(
            any(
                item["file"] == "asm/helper.s"
                for item in inventory["findings"]
                if item["category"] == "raw-rom-address"
            )
        )
        raw_addresses = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "raw-rom-address"
        ]
        self.assertEqual(len(raw_addresses), 1)
        self.assertIn("raw_pointer", raw_addresses[0]["evidence"])
        forced = [
            finding["evidence"]
            for finding in inventory["findings"]
            if finding["category"] == "forced-data-placement"
        ]
        self.assertFalse(any("SHOULD_BE_CONST" in item for item in forced))
        compiler_debt = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "legacy-compiler-pipeline"
        ]
        self.assertFalse(any(item["file"] == "scripts/tool.py" for item in compiler_debt))
        layout = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "layout-sensitive-struct"
        ]
        self.assertTrue(any("value : 3" in item["evidence"] for item in layout))
        self.assertFalse(any("?" in item["evidence"] for item in layout))
        barrier = next(
            finding
            for finding in inventory["findings"]
            if finding["category"] == "inline-asm-barrier"
        )
        self.assertEqual(barrier["root_construct"], "BarrierOwner")
        self.assertNotEqual(barrier["root_construct"], "LaterDefinition")
        inline_owner = next(
            finding
            for finding in inventory["findings"]
            if finding["category"] == "inline-asm"
        )
        self.assertEqual(inline_owner["root_construct"], "InlineOwner")

    def test_old_style_definition_lexical_forms_and_negatives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            inventory = audit.scan_repository(
                root, root / "scripts" / "modernize" / "decisions.json"
            )

        findings = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "old-style-function-definition"
        ]
        expected = [
            ("src/old_style_definitions.c", 1, "SameLine", "void SameLine()"),
            ("src/old_style_definitions.c", 3, "MultiLine", "int MultiLine()"),
            ("src/old_style_definitions.c", 9, "SplitParens", "static int SplitParens()"),
            (
                "src/old_style_definitions.c",
                16,
                "KnrDefinition",
                "int KnrDefinition(first, second)",
            ),
            (
                "src/old_style_definitions.c",
                24,
                "MacroGenerated",
                "DEFINE_OLD(MacroGenerated)",
            ),
            (
                "src/old_style_definitions.c",
                27,
                "ConditionalDefinition",
                "void ConditionalDefinition()",
            ),
            (
                "src/old_style_definitions.c",
                33,
                "MacroReturnType",
                "OLD_RETURN_TYPE MacroReturnType()",
            ),
            (
                "src/old_style_definitions.c",
                39,
                "MacroFunctionReturn",
                "OLD_RETURN_FUNCTION() MacroFunctionReturn()",
            ),
            (
                "src/old_style_definitions.c",
                65,
                "LeadingAttribute",
                "__attribute__((noreturn)) void LeadingAttribute()",
            ),
            (
                "src/old_style_definitions.c",
                66,
                "MiddleAttribute",
                "void __attribute__((noreturn)) MiddleAttribute()",
            ),
            (
                "src/old_style_definitions.c",
                70,
                "MultilineAttribute",
                "__attribute__((noreturn)) void MultilineAttribute()",
            ),
            (
                "src/old_style_definitions.c",
                75,
                "ComplexReturn",
                "int(*ComplexReturn())(void)",
            ),
            (
                "src/old_style_definitions.c",
                80,
                "KnrFunctionPointer",
                "int KnrFunctionPointer(cb)",
            ),
            (
                "src/old_style_definitions.c",
                87,
                "SplitConditional",
                "void SplitConditional()",
            ),
            (
                "src/old_style_definitions.c",
                101,
                "RealBeforeLiteral",
                "REAL_BEFORE_LITERAL(RealBeforeLiteral)",
            ),
            (
                "src/old_style_definitions.c",
                122,
                "RealAfterLiteral",
                "REAL_AFTER_LITERAL(RealAfterLiteral)",
            ),
            (
                "src/old_style_definitions.c",
                128,
                "ReviewerStringReal",
                "REVIEWER_REAL_DEFINITION(ReviewerStringReal)",
            ),
            (
                "src/old_style_definitions.c",
                134,
                "SlashStringReal",
                "SLASH_STRING_REAL_DEFINITION(SlashStringReal)",
            ),
            (
                "src/old_style_definitions.c",
                141,
                "LineCommentReal",
                "LINE_COMMENT_REAL_DEFINITION(LineCommentReal)",
            ),
            (
                "src/old_style_definitions.c",
                151,
                "BlockCommentReal",
                "BLOCK_COMMENT_REAL_DEFINITION(BlockCommentReal)",
            ),
            (
                "src/old_style_definitions.c",
                160,
                "SplitBlockOpenReal",
                "SPLIT_BLOCK_OPEN_REAL(SplitBlockOpenReal)",
            ),
            (
                "src/old_style_definitions.c",
                169,
                "SplitBlockCloseReal",
                "SPLIT_BLOCK_CLOSE_REAL(SplitBlockCloseReal)",
            ),
            (
                "src/old_style_definitions.c",
                177,
                "SplitLineReal",
                "SPLIT_LINE_REAL(SplitLineReal)",
            ),
        ]
        actual = [
            (
                finding["file"],
                finding["line"],
                finding["symbol"],
                finding["evidence"],
            )
            for finding in findings
        ]
        self.assertEqual(actual, expected)
        self.assertTrue(all(finding["priority"] == "P0" for finding in findings))
        self.assertTrue(all(finding["severity"] == "high" for finding in findings))
        self.assertTrue(all(finding["disposition"] == "rewrite-c" for finding in findings))
        self.assertTrue(
            all(finding["root_construct"] == finding["symbol"] for finding in findings)
        )
        rejected = {
            "PrototypeOnly",
            "TypedVoid",
            "TypedParameter",
            "FunctionType",
            "FunctionPointerType",
            "FunctionPointerObject",
            "Factory",
            "StringDefinition",
            "CommentDefinition",
            "BlockCommentDefinition",
            "SectionObject",
            "ObjectInitializer",
            "NegativeBody",
            "AsmDefinition",
            "NOT_A_DEFINITION",
            "UNINVOKED_DEFINITION",
            "CommentedMacroInvocation",
            "FalsePositive",
            "EscapedFalsePositive",
            "CharacterFalsePositive",
            "LineCommentFalse",
            "BlockCommentFalse",
            "SplitBlockOpenFalse",
            "SplitBlockCloseFalse",
            "SplitLineFalse",
        }
        self.assertFalse(rejected & {finding["symbol"] for finding in findings})
        fixture_lines = (
            FIXTURE / "src" / "old_style_definitions.c"
        ).read_text(encoding="utf-8").splitlines()
        macros = audit.definition_macros(fixture_lines)
        self.assertIn("REAL_BEFORE_LITERAL", macros)
        self.assertIn("REAL_AFTER_LITERAL", macros)
        self.assertIn("REVIEWER_REAL_DEFINITION", macros)
        self.assertIn("SLASH_STRING_REAL_DEFINITION", macros)
        self.assertIn("LINE_COMMENT_REAL_DEFINITION", macros)
        self.assertIn("BLOCK_COMMENT_REAL_DEFINITION", macros)
        self.assertIn("SPLIT_BLOCK_OPEN_REAL", macros)
        self.assertIn("SPLIT_BLOCK_CLOSE_REAL", macros)
        self.assertIn("SPLIT_LINE_REAL", macros)
        self.assertNotIn("LITERAL_DEFINITION", macros)
        self.assertNotIn("ESCAPED_LITERAL_DEFINITION", macros)
        self.assertNotIn("CHARACTER_LITERAL_DEFINITION", macros)
        self.assertNotIn("LINE_COMMENT_FAKE_DEFINITION", macros)
        self.assertNotIn("BLOCK_COMMENT_FAKE_DEFINITION", macros)
        self.assertNotIn("SPLIT_BLOCK_OPEN_FAKE", macros)
        self.assertNotIn("SPLIT_BLOCK_CLOSE_FAKE", macros)
        self.assertNotIn("SPLIT_LINE_FAKE", macros)
        inline_asm_fixture = (
            HERE / "fixtures" / "old_style_negative_inline_asm.c"
        ).read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            audit.scan_old_style_definitions(
                "src/old_style_negative_inline_asm.c", inline_asm_fixture
            ),
            [],
        )

    def test_phase2_splice_table_matches_arm_gcc(self):
        cases = [
            (
                "block-open",
                "/\\\n"
                "*\n"
                "#define HIDDEN(n) void n() {}\n"
                "*/\n"
                "#define REAL(n) void n() {}\n"
                "REAL(BlockOpenTable)\n",
                "\n",
                6,
                "BlockOpenTable",
                "REAL(BlockOpenTable)",
                "/*",
                (1, 2),
            ),
            (
                "block-close",
                "/*\n"
                "#define HIDDEN(n) void n() {}\n"
                "*\\\n"
                "/\n"
                "#define REAL(n) void n() {}\n"
                "REAL(BlockCloseTable)\n",
                "\n",
                6,
                "BlockCloseTable",
                "REAL(BlockCloseTable)",
                "*/",
                (3, 4),
            ),
            (
                "line-open",
                "/\\\n"
                "/ fake directive follows \\\n"
                "#define HIDDEN(n) void n() {}\n"
                "#define REAL(n) void n() {}\n"
                "REAL(LineOpenTable)\n",
                "\n",
                5,
                "LineOpenTable",
                "REAL(LineOpenTable)",
                "//",
                (1, 2),
            ),
            (
                "lf-string",
                'const char * text = "continued \\\n'
                '/*";\n'
                "#define REAL(n) void n() {}\n"
                "REAL(LfStringTable)\n",
                "\n",
                4,
                "LfStringTable",
                "REAL(LfStringTable)",
                "/*",
                (2, 2),
            ),
            (
                "crlf-string",
                'const char * text = "continued \\\n'
                '/*";\n'
                "#define REAL(n) void n() {}\n"
                "REAL(CrlfStringTable)\n",
                "\r\n",
                4,
                "CrlfStringTable",
                "REAL(CrlfStringTable)",
                "/*",
                (2, 2),
            ),
        ]
        rendered: list[tuple[str, bytes, int]] = []
        for (
            name,
            source_lf,
            newline,
            line,
            symbol,
            evidence,
            delimiter,
            delimiter_lines,
        ) in cases:
            source = source_lf.replace("\n", newline)
            physical_lines = source.split("\n")
            finding = audit.scan_old_style_definitions(
                f"src/{name}.c", physical_lines
            )
            self.assertEqual(
                [
                    (
                        item["file"],
                        item["line"],
                        item["symbol"],
                        item["evidence"],
                    )
                    for item in finding
                ],
                [(f"src/{name}.c", line, symbol, evidence)],
            )
            spliced = audit.splice_c_phase2(physical_lines)
            delimiter_offset = spliced.text.index(delimiter)
            self.assertEqual(
                (
                    spliced.points[delimiter_offset].line,
                    spliced.points[delimiter_offset + 1].line,
                ),
                delimiter_lines,
            )
            rendered.append((name, source.encode("utf-8"), line))

        cc = os.environ.get("MODERN_CC") or shutil.which("arm-none-eabi-gcc")
        if not cc:
            self.skipTest("arm-none-eabi GCC is not available")
        with tempfile.TemporaryDirectory() as temporary:
            for name, source, expected_line in rendered:
                path = Path(temporary) / f"{name}.c"
                path.write_bytes(source)
                result = subprocess.run(
                    [
                        cc,
                        "-mcpu=arm7tdmi",
                        "-mthumb",
                        "-std=gnu11",
                        "-ffreestanding",
                        "-Wold-style-definition",
                        "-fsyntax-only",
                        "-fdiagnostics-color=never",
                        str(path),
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                warning_lines = {
                    int(value)
                    for value in re.findall(
                        r":(\d+):\d+: warning: old-style function definition",
                        result.stdout,
                    )
                }
                self.assertEqual(warning_lines, {expected_line}, result.stdout)

    def test_root_aggregation_retains_every_drill_down_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            inventory = audit.scan_repository(
                root, root / "scripts" / "modernize" / "decisions.json"
            )

        drill_down = [
            finding_id
            for aggregate in inventory["aggregates"]
            for finding_id in aggregate["finding_ids"]
        ]
        self.assertEqual(sorted(drill_down), sorted(item["id"] for item in inventory["findings"]))
        self.assertEqual(len(drill_down), len(set(drill_down)))
        packed = next(
            item
            for item in inventory["aggregates"]
            if item["category"] == "layout-sensitive-struct"
            and item["root_construct"] == "struct Packed"
        )
        self.assertGreaterEqual(packed["finding_count"], 2)
        manifest = next(
            item
            for item in inventory["aggregates"]
            if item["category"] == "linker-placement-coupling"
            and item["file"] == "ldscript.txt"
        )
        self.assertGreaterEqual(manifest["finding_count"], 2)
        construct = next(
            item
            for item in inventory["aggregates"]
            if item["category"] == "forced-data-placement"
            and item["root_construct"] == "CONST_DATA → .data"
        )
        self.assertEqual(construct["root_kind"], "placement")
        self.assertGreaterEqual(construct["finding_count"], 2)
        self.assertIn("forced_data", construct["symbols"])
        self.assertIn("forced_data_2", construct["symbols"])
        placement_findings = [
            item
            for item in inventory["findings"]
            if item["root_kind"] == "placement"
            and item["root_construct"] == "CONST_DATA → .data"
        ]
        self.assertEqual(
            set(construct["symbols"]),
            {item["symbol"] for item in placement_findings},
        )

    def test_output_is_deterministic_relative_and_sorted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            decisions = root / "scripts" / "modernize" / "decisions.json"
            first = audit.scan_repository(root, decisions)
            second = audit.scan_repository(root, decisions)

        self.assertEqual(audit.json_bytes(first), audit.json_bytes(second))
        self.assertEqual(audit.markdown_text(first), audit.markdown_text(second))
        rendered = audit.json_bytes(first).decode("utf-8")
        self.assertNotIn(str(root), rendered)
        self.assertNotIn("generated_at", rendered)
        keys = [
            (item["detected_category"], item["file"], item["line"], item["evidence"])
            for item in first["findings"]
        ]
        self.assertEqual(keys, sorted(keys))

    def test_check_passes_and_detects_report_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            common = ["--root", str(root)]
            self.assertEqual(audit.main(common), 0)
            self.assertEqual(audit.main([*common, "--check"]), 0)
            report = root / "reports" / "modernize" / "inventory.json"
            report.write_bytes(report.read_bytes() + b" ")
            self.assertEqual(audit.main([*common, "--check"]), 1)

    def test_decisions_remain_visible_and_can_reclassify(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            decision_path = root / "scripts" / "modernize" / "decisions.json"
            initial = audit.scan_repository(root, decision_path)
            target = next(
                item
                for item in initial["findings"]
                if item["category"] == "inline-asm"
            )
            decision_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "decisions": [
                            {
                                "id": target["id"],
                                "status": "reclassified",
                                "category": "platform-call",
                                "disposition": "typed-asm-boundary",
                                "reason": "Reviewed BIOS boundary",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            inventory = audit.scan_repository(root, decision_path)

        updated = next(item for item in inventory["findings"] if item["id"] == target["id"])
        self.assertEqual(updated["detected_category"], "inline-asm")
        self.assertEqual(updated["category"], "platform-call")
        self.assertEqual(updated["status"], "reclassified")
        self.assertTrue(inventory["decision_ledger"][0]["active"])

    def test_declaration_asm_labels_are_not_inline_asm(self):
        """Declaration-level asm symbol labels must not be classified as inline-asm."""
        lines = [
            'extern u8 F(int x) __asm__("OrigSym");\n',
            'extern int var __asm__("orig_var");\n',
            'static int sVar asm("_sVar");\n',
        ]
        for line in lines:
            findings = audit.scan_c_file("test.c", [line])
            categories = [f["detected_category"] for f in findings]
            self.assertNotIn(
                "inline-asm",
                categories,
                f"declaration asm label incorrectly classified: {line.strip()}",
            )
            self.assertNotIn(
                "inline-asm-barrier",
                categories,
                f"declaration asm label incorrectly classified: {line.strip()}",
            )

    def test_control_flow_asm_not_suppressed(self):
        """Asm after control-flow keywords must remain detected as inline-asm."""
        lines_and_expected = [
            ('if (x) asm("nop");\n', "inline-asm"),
            ('while (x) __asm__("nop");\n', "inline-asm"),
            ('for (;;) asm("nop");\n', "inline-asm"),
            ('void f(void) { asm("swi 3"); }\n', "inline-asm"),
            ('if (c) asm("" ::: "memory");\n', "inline-asm-barrier"),
        ]
        for line, expected_cat in lines_and_expected:
            findings = audit.scan_c_file("test.c", [line])
            categories = [f["detected_category"] for f in findings]
            self.assertIn(
                expected_cat,
                categories,
                f"expected {expected_cat} for: {line.strip()}",
            )

    def test_real_inline_asm_still_detected(self):
        """Executable inline asm must still be reported even with the label exclusion."""
        lines_and_expected = [
            ('void f(void) { asm("swi 3"); }\n', "inline-asm"),
            ('void g(void) { asm("" ::: "memory"); }\n', "inline-asm-barrier"),
            ('    asm("nop");\n', "inline-asm"),
            ('    asm("add r0, r0, #1");\n', "inline-asm"),
        ]
        for line, expected_cat in lines_and_expected:
            findings = audit.scan_c_file("test.c", [line])
            categories = [f["detected_category"] for f in findings]
            self.assertIn(
                expected_cat,
                categories,
                f"expected {expected_cat} for: {line.strip()}",
            )

    def test_multiline_declaration_asm_label_excluded(self):
        """A wrapped declaration asm label split across lines must be suppressed."""
        lines = [
            'extern u8 Alias(\n',
            '    struct A * a, struct B * b) __asm__("Target");\n',
        ]
        findings = audit.scan_c_file("test.c", lines)
        categories = [f["detected_category"] for f in findings]
        self.assertNotIn("inline-asm", categories)
        self.assertNotIn("inline-asm-barrier", categories)

    def test_multiline_control_flow_asm_not_suppressed(self):
        """Asm on a continuation line after a control keyword must still be detected."""
        cases = [
            (
                ['if (x)\n', '    asm("nop");\n'],
                "inline-asm",
            ),
            (
                ['while (x)\n', '    __asm__("nop");\n'],
                "inline-asm",
            ),
            (
                ['void f(void) {\n', '    asm("swi 3");\n', '}\n'],
                "inline-asm",
            ),
        ]
        for lines, expected_cat in cases:
            findings = audit.scan_c_file("test.c", lines)
            categories = [f["detected_category"] for f in findings]
            self.assertIn(
                expected_cat,
                categories,
                f"expected {expected_cat} for multiline: {lines}",
            )

    def test_register_pinned_still_detected_with_label_exclusion(self):
        """Register-pinned locals must keep their own category, not be suppressed."""
        line = 'register int r4 asm("r4");\n'
        findings = audit.scan_c_file("test.c", [line])
        categories = [f["detected_category"] for f in findings]
        self.assertIn("register-pinned-local", categories)
        self.assertNotIn("inline-asm", categories)

    def test_fixture_declaration_asm_labels_excluded(self):
        """Fixture sample.c has declaration asm labels that must not appear as findings."""
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            inventory = audit.scan_repository(
                root, root / "scripts" / "modernize" / "decisions.json"
            )

        inline_asm = [
            f for f in inventory["findings"] if f["category"] == "inline-asm"
        ]
        self.assertEqual(len(inline_asm), 1, "only real inline asm should be reported")
        self.assertEqual(inline_asm[0]["root_construct"], "InlineOwner")
        asm_labels = [
            f
            for f in inventory["findings"]
            if "AliasedFunc" in f.get("evidence", "")
            or "aliased_var" in f.get("evidence", "")
        ]
        self.assertEqual(asm_labels, [], "declaration asm labels should not produce findings")


if __name__ == "__main__":
    unittest.main()
