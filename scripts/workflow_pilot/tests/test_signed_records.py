import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from scripts.workflow_pilot import reporter, signed_records as records


class CanonicalTimestampTests(unittest.TestCase):
    def test_parser_and_real_schema_agree_on_clock_and_gregorian_boundaries(self):
        schema = json.loads(Path(records.__file__).with_name("git_broker.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(
            schema["$defs"]["time"], format_checker=jsonschema.FormatChecker(),
        )
        values = [
            "0001-01-01T00:00:00Z", "9999-12-31T23:59:59.999999Z",
            "2000-02-29T00:00:00Z", "2024-02-29T23:59:59.1Z",
            "2026-01-01T24:00:00Z", "2026-01-01T24:00:00.000000Z",
            "2026-01-01T00:60:00Z", "2026-01-01T00:00:60Z",
            "2026-01-01T00:00:00.1234567Z", "2026-01-01T00:00:00.Z",
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00-00:00",
            "2026-01-01T00:00:00+01:00Z", "2026-01-01T00:00:00z",
            "20260101T000000Z", "2026-001T00:00:00Z",
            "2026-01-01 00:00:00Z", "2026-01-01t00:00:00Z",
            "2026-01-01T00:00:00Z\n", " 2026-01-01T00:00:00Z",
            "٢٠٢٦-01-01T00:00:00Z", "0000-01-01T00:00:00Z",
            None, False, 2026,
        ]
        for year in (1, 4, 100, 400, 1600, 1700, 1900, 2000, 2024, 2025, 2026, 2100, 2400, 9999):
            for month in (0, 1, 2, 3, 4, 6, 9, 11, 12, 13):
                for day in (0, 1, 28, 29, 30, 31, 32):
                    values.append(f"{year:04}-{month:02}-{day:02}T23:59:59Z")
        for hour in range(26):
            values.append(f"2026-09-05T{hour:02}:00:00Z")
        for value in values:
            with self.subTest(value=value):
                schema_accepts = validator.is_valid(value)
                if schema_accepts:
                    result = records.parse_utc(value)
                    self.assertEqual(result.tzinfo, timezone.utc)
                    self.assertEqual(reporter.parse_time(value, "record time"), result)
                else:
                    with self.assertRaises(records.RecordError):
                        records.parse_utc(value)
                    with self.assertRaises(reporter.PilotDataError):
                        reporter.parse_time(value, "record time")

    def test_fraction_is_preserved_not_rounded_or_truncated(self):
        for text, micros in (("1", 100000), ("000001", 1), ("123456", 123456)):
            result = records.parse_utc(f"2026-09-05T12:00:00.{text}Z")
            self.assertEqual(result.microsecond, micros)
        # Deterministic pre-fix negative control on all supported Python versions:
        # the old ISO parser silently accepted precision outside the signed schema.
        self.assertEqual(
            datetime.fromisoformat("2026-09-05T12:00:00.1234567+00:00").microsecond, 123456,
        )
        with self.assertRaises(records.RecordError):
            records.parse_utc("2026-09-05T12:00:00.1234567Z")
        self.assertIsNone(reporter.parse_time(None, "optional time", nullable=True))

    def test_midnight_rollover_never_reaches_runtime_iso_parser(self):
        for clock in ("24:00:00", "24:01:00", "25:00:00", "99:99:99"):
            with self.assertRaises(records.RecordError):
                records.parse_utc(f"2026-09-05T{clock}Z")
        self.assertEqual(
            records.parse_utc("2026-09-05T00:00:00Z"),
            datetime(2026, 9, 5, tzinfo=timezone.utc),
        )


class CanonicalJSONTests(unittest.TestCase):
    def test_closed_canonical_bytes(self):
        value = {"operation": "advance", "sequence": 1}
        self.assertEqual(records.strict_json(records.canonical_json(value), 4096), value)
        for raw in (
            b'{"a":1,"a":2}\n', b'{"a":NaN}\n', b'{"a":Infinity}\n',
            b'{"a":1.0}\n', b'{"a":1e999}\n', b'{"a": 1}\n',
            b'{"a":1}', b"\xff", b"[" * 2000 + b"]" * 2000,
        ):
            with self.subTest(raw=raw[:40]), self.assertRaises(records.RecordError):
                records.strict_json(raw, 8192)
        with self.assertRaises(records.RecordError):
            records.strict_json(b'{"a":1}\n', 4)


if __name__ == "__main__":
    unittest.main()
