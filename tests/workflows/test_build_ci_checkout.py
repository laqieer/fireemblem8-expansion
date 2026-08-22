"""Build CI must execute the exact event-derived revision it reports."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
CHECKOUT_PIN = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
EXPECTED_SHA = (
    "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}"
)


def _job_block(text, name):
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [A-Za-z][A-Za-z0-9_-]*:\n|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return "" if match is None else match.group("body")


def _contract_errors(text):
    errors = []
    for name in ("host-tests", "build", "extended-host-tests", "legacy"):
        job = _job_block(text, name)
        if not job:
            errors.append(f"missing {name} job")
            continue
        if f"EXPECTED_BUILD_SHA: {EXPECTED_SHA}" not in job:
            errors.append(f"{name} does not derive EXPECTED_BUILD_SHA from pull-request head or push SHA")
        checkout = f"actions/checkout@{CHECKOUT_PIN.split('@', 1)[1]}"
        checkout_fields = (
            f"ref: {EXPECTED_SHA}",
            "fetch-depth: 0",
            "submodules: recursive",
            "persist-credentials: false",
        )
        if checkout not in job or not all(field in job for field in checkout_fields):
            errors.append(f"{name} checkout must pin ref, fetch-depth, submodules, and credentials")
            continue
        verification = (
            '    - name: Verify checked-out revision\n'
            '      run: |\n'
            '        ACTUAL_SHA="$(git rev-parse HEAD)"\n'
            "        printf 'checkout.sha=%s\\n' \"$ACTUAL_SHA\"\n"
            '        test "$ACTUAL_SHA" = "$EXPECTED_BUILD_SHA"'
        )
        if verification not in job:
            errors.append(f"{name} must immediately verify the checkout")
            continue
        if job.index(verification) < job.index(checkout):
            errors.append(f"{name} must verify checkout immediately after actions/checkout")
    return errors


class BuildCiCheckoutContractTests(unittest.TestCase):
    def test_real_workflow_binds_all_combined_workers_to_the_event_head(self):
        self.assertEqual(_contract_errors(WORKFLOW.read_text(encoding="utf-8")), [])

    def test_missing_fetch_depth_is_rejected(self):
        text = WORKFLOW.read_text(encoding="utf-8").replace("        fetch-depth: 0\n", "", 1)
        self.assertTrue(any("fetch-depth" in error for error in _contract_errors(text)))

    def test_merge_ref_fallback_is_rejected(self):
        text = WORKFLOW.read_text(encoding="utf-8").replace(
            EXPECTED_SHA, "${{ github.sha }}", 1
        )
        self.assertTrue(any("pull-request head" in error for error in _contract_errors(text)))


if __name__ == "__main__":
    unittest.main()
