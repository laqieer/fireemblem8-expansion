"""Build CI must execute the exact event-derived revision it reports."""

import re
import unittest
from pathlib import Path

from scripts.workflow_pilot import publisher_command_signatures

ROOT = Path(__file__).resolve().parents[2]

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


def _workflow_text():
    return publisher_command_signatures.authority_file_bytes(
        ".github/workflows/build.yml"
    ).decode("utf-8")


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
        verification = '    - name: Verify checked-out revision\n'
        if name in {"host-tests", "build"}:
            verification += f"      if: {FULL_MODE_STEP_IF}\n"
        if name == "host-tests":
            verification += (
                "      env:\n"
                "        AUTHORITY_SUITE: check\n"
                "        BASH_ENV: ''\n"
                "        ENV: ''\n"
                f"        EXPECTED_AUTHORITY_SHA: {EXPECTED_SHA}\n"
                "        GIT_CONFIG_COUNT: '0'\n"
                "        GIT_CONFIG_GLOBAL: /dev/null\n"
                "        GIT_CONFIG_NOSYSTEM: '1'\n"
                "        GIT_NO_LAZY_FETCH: '1'\n"
                "        GIT_NO_REPLACE_OBJECTS: '1'\n"
                "        HOME: /\n"
                "        LD_AUDIT: ''\n"
                "        LD_LIBRARY_PATH: ''\n"
                "        LD_PRELOAD: ''\n"
                "        PATH: /usr/bin:/bin\n"
                "        PYTHONHOME: ''\n"
                "        PYTHONPATH: ''\n"
            )
        expected_variable = (
            "EXPECTED_AUTHORITY_SHA"
            if name == "host-tests"
            else "EXPECTED_BUILD_SHA"
        )
        actual_sha_command = (
            '/usr/bin/git rev-parse HEAD'
            if name == "host-tests"
            else "git rev-parse HEAD"
        )
        verification += (
            '      run: |\n'
            f'        ACTUAL_SHA="$({actual_sha_command})"\n'
            "        printf 'checkout.sha=%s\\n' \"$ACTUAL_SHA\"\n"
            f'        test "$ACTUAL_SHA" = "${expected_variable}"'
        )
        if verification not in job:
            errors.append(f"{name} must immediately verify the checkout")
            continue
        if job.index(verification) < job.index(checkout):
            errors.append(f"{name} must verify checkout immediately after actions/checkout")
    return errors


class BuildCiCheckoutContractTests(unittest.TestCase):
    def test_real_workflow_binds_all_combined_workers_to_the_event_head(self):
        self.assertEqual(_contract_errors(_workflow_text()), [])

    def test_missing_fetch_depth_is_rejected(self):
        text = _workflow_text().replace("        fetch-depth: 0\n", "", 1)
        self.assertTrue(any("fetch-depth" in error for error in _contract_errors(text)))

    def test_merge_ref_fallback_is_rejected(self):
        text = _workflow_text()
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
        text = _workflow_text().replace(
            f"        ref: {EXPECTED_SHA}\n",
            "        ref: ${{ github.sha }}\n",
            1,
        )
        self.assertTrue(any("checkout must pin" in error for error in _contract_errors(text)))

    def test_fallback_checkouts_never_consume_raw_event_refs(self):
        text = _workflow_text()
        self.assertIn(
            "ref: ${{ needs.event-identity.outputs.classifier_ref }}",
            text,
        )
        self.assertIn("needs.event-identity.outputs.fallback_sha", EXPECTED_SHA)
        self.assertIn(
            "ref: ${{ needs.event-identity.outputs.fallback_sha }}",
            text,
        )
        for raw_ref in (
            "ref: ${{ github.sha }}",
            "ref: ${{ github.event.after }}",
            "ref: ${{ github.event.pull_request.head.sha }}",
        ):
            with self.subTest(raw_ref=raw_ref):
                self.assertNotIn(raw_ref, text)

    def test_missing_checkout_verification_is_rejected(self):
        verification = (
            '    - name: Verify checked-out revision\n'
            '      run: |\n'
            '        ACTUAL_SHA="$(git rev-parse HEAD)"\n'
            "        printf 'checkout.sha=%s\\n' \"$ACTUAL_SHA\"\n"
            '        test "$ACTUAL_SHA" = "$EXPECTED_BUILD_SHA"\n\n'
        )
        text = _workflow_text().replace(verification, "", 1)
        self.assertTrue(
            any("must immediately verify" in error for error in _contract_errors(text))
        )


if __name__ == "__main__":
    unittest.main()
