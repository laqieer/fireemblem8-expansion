import json
import os
from pathlib import Path
import signal
import threading
import time
import unittest
import zlib
from unittest import mock

from scripts.validation_ownership import budget as budget_module, reporter
from scripts.validation_ownership.authority import ENVIRONMENT, encoded
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_regex import CommandPatterns, WORKER_PATH, evaluate, validate_patterns


ROOT = Path(__file__).resolve().parents[3]


class GraphRegexTests(unittest.TestCase):
    def budget(self, **limits):
        value = ProbeBudget(Limits(**limits))
        self.addCleanup(value.close)
        return value

    def observe_reads(self, chunks):
        read = os.read
        def capture(*args):
            result = read(*args)
            chunks.append(result)
            return result
        return mock.patch.object(budget_module.os, "read", side_effect=capture)

    def phases(self, chunks):
        result = []
        for line in b"".join(chunks).splitlines():
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(value, dict) and "phase" in value:
                result.append(value)
        return result

    def test_worker_input_bound_tracks_actual_request_not_aggregate_pending_budget(self):
        budget = self.budget()
        execute = budget.run
        bounds = []

        def observe(argv, **arguments):
            result = execute(argv, **arguments)
            wire = arguments["input_data"]
            decoded = zlib.decompress(wire) if argv[-2] == "zlib" else wire
            bounds.append((int(argv[-4]), len(wire), int(argv[-1]), len(decoded),
                           argv[-2], result.returncode))
            return result

        cases = (
            ("compile", {"patterns": ["^ok$"]}, None),
            ("compile", {"patterns": ["^" + "a" * 1024 + "$"]}, None),
            ("fullmatch", {"patterns": ["^caf\u00e9$"], "command": "caf\u00e9"}, (0,)),
            ("schema", ["ok", {"type": "string"}, {"type": "string"}], None),
        )
        with mock.patch.object(budget, "run", side_effect=observe):
            for operation, payload, expected in cases:
                with self.subTest(operation=operation, payload=payload):
                    self.assertEqual(evaluate(budget, operation, payload), expected)
        self.assertEqual(len(bounds), len(cases))
        for limit, actual, decoded_limit, decoded_size, encoding, status in bounds:
            self.assertEqual(limit, actual)
            self.assertEqual(decoded_limit, decoded_size)
            self.assertLess(limit, budget.limits.pending_bytes)
            self.assertEqual(status, 0)
        self.assertEqual(bounds[0][4], "identity")
        self.assertEqual(bounds[1][4], "zlib")
        self.assertFalse(budget.children)

    def test_lossless_worker_transport_reduces_actual_pattern_request_traffic(self):
        contracts = json.loads((ROOT / reporter.MAKE_DYNAMIC_PATH).read_bytes())["contracts"]
        patterns = tuple(item["command_regex"] for item in contracts)
        identity = self.budget()
        packed = self.budget()
        commands = ("uname", 'find texts -type f -name "*.txt"', "unregistered-command")
        for command in commands:
            payload = {"patterns": patterns, "command": command}
            raw = encoded(payload)
            baseline = identity.run(
                [
                    "/usr/bin/python3", "-I", "-S", "-B", str(WORKER_PATH),
                    "fullmatch", str(identity.limits.address_space_bytes),
                    str(len(raw)), str(ROOT), "identity", str(len(raw)),
                ],
                env=ENVIRONMENT, input_data=raw,
            )
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            expected = json.loads(baseline.stdout.splitlines()[-1])
            self.assertTrue(expected["ok"])
            self.assertEqual(evaluate(packed, "fullmatch", payload), tuple(expected["indices"]))
        self.assertLessEqual(2 * packed.bytes["pending"], identity.bytes["pending"])
        self.assertFalse(identity.children)
        self.assertFalse(packed.children)

    def test_worker_rejects_truncated_trailing_and_oversized_decoded_input(self):
        budget = self.budget()
        raw = encoded({"patterns": ["^ok$"]})
        packed = zlib.compress(raw)
        cases = (
            ("zlib", packed[:-1], len(raw)),
            ("zlib", packed + b"trailing", len(raw)),
            ("zlib", zlib.compress(b"x" * 100000), len(raw)),
            ("identity", raw, len(raw) - 1),
            ("unsupported", raw, len(raw)),
        )
        for encoding, wire, decoded_length in cases:
            with self.subTest(encoding=encoding, wire_length=len(wire)):
                result = budget.run(
                    [
                        "/usr/bin/python3", "-I", "-S", "-B", str(WORKER_PATH),
                        "compile", str(budget.limits.address_space_bytes),
                        str(len(wire)), str(ROOT), encoding, str(decoded_length),
                    ],
                    env=ENVIRONMENT, input_data=wire,
                )
                self.assertNotEqual(result.returncode, 0)
                records = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(len(records), 1)
                self.assertFalse(records[0]["ok"])
                self.assertIn("error", records[0])
                self.assertFalse(budget.children)

    def test_catastrophic_command_match_enters_engine_and_obeys_same_report_deadline(self):
        budget = self.budget(seconds=0.75)
        matcher = CommandPatterns(budget, ("^(a+)+$",))
        chunks = []
        started = time.monotonic()
        with self.observe_reads(chunks), self.assertRaises(MakeProbeError):
            matcher.fullmatch("a" * 36 + "!")
        self.assertIn("match-start", {item["phase"] for item in self.phases(chunks)})
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(budget.failed)
        self.assertFalse(budget.children)
        runs = budget.runs
        with self.assertRaisesRegex(MakeProbeError, "aggregate"):
            matcher.fullmatch("a")
        self.assertEqual(budget.runs, runs)
        self.assertEqual(matcher.matches, {})

    def test_candidate_schema_pattern_is_also_inside_the_report_boundary(self):
        budget = self.budget(seconds=0.75)
        schema = {"type": "string", "pattern": "^(a+)+$"}
        chunks = []
        with self.observe_reads(chunks), self.assertRaises(MakeProbeError):
            reporter.validate_json_schema("a" * 36 + "!", schema, schema, budget=budget)
        self.assertIn("schema-pattern-start", {item["phase"] for item in self.phases(chunks)})
        self.assertTrue(budget.failed)
        self.assertFalse(budget.children)

    def test_real_repository_patterns_preserve_positive_negative_and_multiline_matches(self):
        contracts = json.loads((ROOT / reporter.MAKE_DYNAMIC_PATH).read_bytes())["contracts"]
        budget = self.budget()
        validate_patterns(budget, tuple(item["command_regex"] for item in contracts))
        matcher = CommandPatterns(budget, tuple(item["command_regex"] for item in contracts))
        cases = (
            ("uname", "host-uname"),
            ('tools/scaninc/scaninc -I include -I "" sound/voicegroups/voicegroup038.s', "banim-scaninc-inputs"),
            ("python3 scripts/arm_compressing_linker.py -t linker_script_banim.txt -m", "banim-compressing-linker-inputs"),
            ('mkdir -p "build/generated/assets/"', "asset-include-remake-directory"),
            ('python3 -m scripts.generated_data.chapterobjectives.deps \\\n'
             '\t--source "src/data/chapter_objectives.json" \\\n'
             '\t--depfile "build/generated/data/chapterobjectives.inputs.mk"', "generated-input-dependency-remakes"),
            ("unregistered-command", None),
            ("uname; echo unregistered", None),
        )
        for command, expected in cases:
            with self.subTest(command=command):
                selected = [contracts[index]["id"] for index in matcher.fullmatch(command)]
                self.assertEqual(selected, [] if expected is None else [expected])
        runs = budget.runs
        for command, _ in cases:
            matcher.fullmatch(command)
        self.assertEqual(budget.runs, runs)
        self.assertGreater(budget.bytes["pending"], 0)
        self.assertGreater(budget.bytes["output"], 0)
        self.assertGreater(budget.bytes["cache"], 0)
        self.assertFalse(budget.children)

    def test_standard_engine_dialect_and_observed_hard_memory_bound_are_preserved(self):
        budget = self.budget(address_space_bytes=128 * 1024 * 1024)
        chunks = []
        patterns = (r"^(?P<item>a)\1$", r"^a(?=b)b$", r"^a.*b$", r"^(?i:abc)$")
        with self.observe_reads(chunks):
            matcher = CommandPatterns(budget, patterns)
            self.assertEqual(matcher.fullmatch("aa"), (0,))
            self.assertEqual(matcher.fullmatch("ab"), (1, 2))
            self.assertEqual(matcher.fullmatch("a\nb"), (2,))
            self.assertEqual(matcher.fullmatch("ABC"), (3,))
        self.assertTrue(self.phases(chunks))
        self.assertTrue(all(item["address_space_bytes"] == 128 * 1024 * 1024
                            for item in self.phases(chunks)))

    def test_malformed_unsupported_and_deep_patterns_reject_in_worker(self):
        budget = self.budget()
        for pattern in ("^[", "^(?R)$", "^" + "(" * 1500 + "a" + ")" * 1500 + "$"):
            chunks = []
            with self.subTest(pattern=pattern[:12]), self.observe_reads(chunks):
                before = budget.runs
                with self.assertRaisesRegex(MakeProbeError, "validation failed"):
                    validate_patterns(budget, (pattern,))
                self.assertGreater(budget.runs, before)
                self.assertIn("compile-start", {item["phase"] for item in self.phases(chunks)})
                self.assertFalse(budget.children)

    def test_oversized_inputs_reject_before_launch_and_deadline_cache_does_not_revive(self):
        budget = self.budget()
        before = budget.runs
        with self.assertRaises(MakeProbeError):
            CommandPatterns(budget, ("^" + "a" * 8192 + "$",))
        matcher = CommandPatterns(budget, ("^a$",))
        with self.assertRaises(MakeProbeError):
            matcher.fullmatch("a" * 65537)
        self.assertEqual(budget.runs, before)
        self.assertEqual(matcher.fullmatch("a"), (0,))
        budget.close()
        before = budget.runs
        with self.assertRaises(MakeProbeError):
            matcher.fullmatch("a")
        self.assertEqual(budget.runs, before)

    def test_interrupted_match_reaps_owned_worker_after_matching_has_started(self):
        budget = self.budget(seconds=5)
        matcher = CommandPatterns(budget, ("^(a+)+$",))
        read, chunks, timers = os.read, [], []
        own_pid = os.getpid()
        def capture(*args):
            result = read(*args)
            chunks.append(result)
            if b'"phase":"match-start"' in b"".join(chunks) and not timers:
                timer = threading.Timer(0.05, lambda: os.kill(own_pid, signal.SIGINT))
                timers.append(timer)
                timer.start()
            return result
        try:
            with mock.patch.object(budget_module.os, "read", side_effect=capture), \
                 self.assertRaises(KeyboardInterrupt):
                matcher.fullmatch("a" * 36 + "!")
        finally:
            for timer in timers:
                timer.join()
        self.assertTrue(timers)
        self.assertFalse(budget.children)
        self.assertTrue(budget.failed)

    def test_existing_stream_bound_rejects_worker_output(self):
        budget = self.budget(process_output_bytes=1)
        with self.assertRaisesRegex(MakeProbeError, "output"):
            validate_patterns(budget, ("^a$",))
        self.assertFalse(budget.children)


if __name__ == "__main__":
    unittest.main()
