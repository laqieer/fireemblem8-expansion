"""Behavior-baseline metadata census for issue #29."""

from __future__ import annotations

import json
import re
import shlex
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
FINGERPRINTS_DIR = PLAYTEST_DIR / "fingerprints"

sys.path.insert(0, str(PLAYTEST_DIR))
import gba_playtest  # noqa: E402

_MAKE_ASSIGNMENT_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?::|\?|\+)?=\s*(.*)$"
)
_MAKE_VARIABLE_RE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")


def _make_variables(source: str) -> dict[str, str]:
    variables = {}
    for raw_line in source.splitlines():
        match = _MAKE_ASSIGNMENT_RE.match(raw_line.strip())
        if match is not None:
            variables[match.group(1)] = match.group(2)
    return variables


def _expand_make_variables(value: str, variables: dict[str, str]) -> str:
    for _ in range(len(variables) + 1):
        expanded = _MAKE_VARIABLE_RE.sub(
            lambda match: variables.get(match.group(1), match.group(0)),
            value,
        )
        if expanded == value:
            return expanded
        value = expanded
    return value


def _make_command_blocks(source: str):
    block = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            if block:
                yield " ".join(block)
                block = []
            continue
        if line.endswith("\\"):
            block.append(line[:-1].rstrip())
            continue
        block.append(line)
        yield " ".join(block)
        block = []
    if block:
        yield " ".join(block)


def _option_value(tokens: list[str], option: str) -> str | None:
    for index, token in enumerate(tokens):
        if token == option and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith(option + "="):
            return token.removeprefix(option + "=")
    return None


def _is_gba_playtest_command(token: str, variables: dict[str, str]) -> bool:
    return _expand_make_variables(token.lstrip("@+-"), variables).endswith(
        "tools/gba-playtest/gba_playtest.py"
    )


def _committed_make_verifiers(source: str):
    variables = _make_variables(source)
    for block in _make_command_blocks(source):
        if "verify" not in block:
            continue
        try:
            tokens = shlex.split(block)
        except ValueError as exc:
            raise AssertionError(f"cannot parse Make command block: {block!r}") from exc
        for index, token in enumerate(tokens[:-1]):
            if not _is_gba_playtest_command(token, variables) or tokens[index + 1] != "verify":
                continue
            expected = _option_value(tokens[index + 2:], "--expected")
            if expected is None:
                continue
            expected = _expand_make_variables(expected, variables)
            if "tools/gba-playtest/fingerprints/" in expected:
                yield block, expected, _option_value(tokens[index + 2:], "--policy")


def _assert_behavior_policy_for_committed_verifiers(source: str):
    verifiers = list(_committed_make_verifiers(source))
    if not verifiers:
        raise AssertionError("no committed fingerprint verifier was discovered")
    for block, expected, policy in verifiers:
        if policy != "behavior":
            raise AssertionError(
                f"committed fingerprint {expected!r} must use --policy behavior: {block}"
            )
    return verifiers


def _project_make_source() -> str:
    paths = [REPO_ROOT / "Makefile", *sorted(REPO_ROOT.glob("*.mk"))]
    return "\n\n".join(path.read_text(encoding="utf-8") for path in paths)


class FingerprintIdentityPolicyCensusTests(unittest.TestCase):
    def test_committed_behavior_baselines_omit_rom_identity(self):
        paths = sorted(FINGERPRINTS_DIR.glob("*.json"))
        self.assertTrue(paths, "expected a nonempty committed baseline corpus")
        for path in paths:
            with self.subTest(path=path.name):
                fingerprint = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("rom", fingerprint)
                self.assertEqual(
                    gba_playtest.validate_fingerprint(
                        fingerprint, str(path), policy="behavior"
                    ),
                    fingerprint,
                )

    def test_makefiles_committed_baseline_consumers_select_behavior_policy(self):
        _assert_behavior_policy_for_committed_verifiers(_project_make_source())

    def test_makefile_audit_accepts_wrapped_variable_behavior_command(self):
        source = """
PLAYTEST := tools/gba-playtest/gba_playtest.py
FINGERPRINT_DIR := tools/gba-playtest/fingerprints
EXPECTED := $(FINGERPRINT_DIR)/boot.json

	"$(PLAYTEST)" \\
		verify --expected="$(EXPECTED)" \\
		--policy=behavior
"""
        _assert_behavior_policy_for_committed_verifiers(source)

    def test_makefile_audit_rejects_exact_or_missing_policy_for_romless_baseline(self):
        baseline_path = FINGERPRINTS_DIR / "boot.json"
        baseline = str(baseline_path.relative_to(REPO_ROOT))
        self.assertNotIn("rom", json.loads(baseline_path.read_text(encoding="utf-8")))
        for policy in ("exact-rom", None):
            with self.subTest(policy=policy):
                command = (
                    "python3 tools/gba-playtest/gba_playtest.py verify "
                    f"--expected {baseline}"
                )
                if policy is not None:
                    command += f" --policy {policy}"
                with self.assertRaisesRegex(AssertionError, "--policy behavior"):
                    _assert_behavior_policy_for_committed_verifiers(command)

    def test_script_committed_baseline_consumers_select_behavior_policy(self):
        shifted_boot = (
            REPO_ROOT / "scripts" / "shiftcheck" / "modern_shifted_boot.sh"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            shifted_boot,
            re.compile(
                r"gba_playtest\.py verify.*?--policy\s+behavior",
                re.DOTALL,
            ),
        )

        save_compat = (
            PLAYTEST_DIR / "run_save_compat_checks.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"--policy", "behavior"', save_compat)


if __name__ == "__main__":
    unittest.main()
