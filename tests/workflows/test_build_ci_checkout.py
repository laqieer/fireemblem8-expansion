"""Build CI must execute the exact event-derived revision it reports."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.upstream_port import verify

WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
CHECKOUT_PIN = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
EXPECTED_SHA = (
    "${{ (needs.event-classifier.result == 'success' && "
    "needs.event-classifier.outputs.expected_head) || "
    "(needs.event-classifier.result == 'failure' && "
    "needs.event-identity.outputs.fallback_sha) || '' }}"
)
FULL_MODE_STEP_IF = (
    "${{ needs.event-classifier.result == 'failure' || "
    "needs.event-classifier.outputs.classification == 'full' }}"
)
MERGE_SHA_FALLBACK = (
    "github.event_name == 'pull_request' && "
    "github.event.pull_request.head.sha || github.sha"
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
            errors.append(
                f"{name} does not derive EXPECTED_BUILD_SHA from classified exact identity"
            )
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
            + (
                f"      if: {FULL_MODE_STEP_IF}\n"
                if name in {"host-tests", "build"}
                else ""
            )
            + '      run: |\n'
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
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn(MERGE_SHA_FALLBACK, text)
        self.assertNotIn(
            "needs.event-classifier.result == 'failure' && "
            "github.event.pull_request.head.sha",
            EXPECTED_SHA,
        )
        host = _job_block(text, "host-tests")
        changed_host = host.replace(EXPECTED_SHA, "${{ github.sha }}", 1)
        self.assertNotEqual(changed_host, host)
        text = text.replace(host, changed_host, 1)
        self.assertTrue(
            any(
                "classified exact identity" in error
                for error in _contract_errors(text)
            )
        )

    def test_checkout_ref_must_use_event_head(self):
        text = WORKFLOW.read_text(encoding="utf-8").replace(
            f"        ref: {EXPECTED_SHA}\n",
            "        ref: ${{ github.sha }}\n",
            1,
        )
        self.assertTrue(any("checkout must pin" in error for error in _contract_errors(text)))

    def test_fallback_checkouts_never_consume_raw_event_refs(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        _, _, jobs = verify._parse_workflow_structure_text(text)
        checkouts = {
            name: dict(dict(fields)["with"])["ref"]
            for name, _, steps in jobs
            for _, _, fields in steps
            if dict(fields).get("uses") == CHECKOUT_PIN
        }
        self.assertIn("needs.event-identity.outputs.fallback_sha", EXPECTED_SHA)
        self.assertEqual(checkouts, {
            "event-router": "${{ needs.event-identity.outputs.classifier_ref }}",
            **{name: EXPECTED_SHA for name in
               ("host-tests", "build", "extended-host-tests", "legacy")},
        })
        for raw_ref in (
            "${{ github.sha }}",
            "${{ github.event.after }}",
            "${{ github.event.pull_request.head.sha }}",
        ):
            with self.subTest(raw_ref=raw_ref):
                changed = text.replace(f"ref: {EXPECTED_SHA}", f"ref: {raw_ref}", 1)
                self.assertNotEqual(changed, text)
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)

    def test_missing_checkout_verification_is_rejected(self):
        verification = (
            '    - name: Verify checked-out revision\n'
            '      run: |\n'
            '        ACTUAL_SHA="$(git rev-parse HEAD)"\n'
            "        printf 'checkout.sha=%s\\n' \"$ACTUAL_SHA\"\n"
            '        test "$ACTUAL_SHA" = "$EXPECTED_BUILD_SHA"\n\n'
        )
        text = WORKFLOW.read_text(encoding="utf-8").replace(verification, "", 1)
        self.assertTrue(
            any("must immediately verify" in error for error in _contract_errors(text))
        )


if __name__ == "__main__":
    unittest.main()
