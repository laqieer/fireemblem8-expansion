"""Orchestrate existing repository gates against the CURRENT TRUSTED WORKTREE
after a maintainer has manually applied a port batch.

WARNING (see docs/upstream-porting.md): this command builds and checks the
repository's *own* current working tree/commit. It never builds, checks out,
or executes the canonical upstream ref/tree. It is a thin, literal mirror of
the four combined workers in `.github/workflows/build.yml`. Before execution,
it parses the selected target checkout's workflow as data and requires exact
semantic equivalence with both the source workflow and this module's reviewed
gate list; target Python is never imported. The event identity, router,
classifier and serial summary jobs, and master-only packaging steps, have no
local gate equivalent. The one DELIBERATE
command-level exception is build.yml's
"Check documentation (issues #7/#17)" step, which remains a required
standalone workflow gate outside this mirror. Run that standalone command pair
directly to reproduce it locally; see docs/upstream-porting.md.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import List

from scripts.workflow_pilot import (
    metadata_adapter_contract,
    summary_continuity_contract,
)

# A leading NAME=VALUE token in a gate command is an inline environment
# assignment (POSIX shell semantics), mirrored verbatim from build.yml so
# the gate list stays an argv-identical copy of the workflow. It is applied
# to the child environment, never exec-ed as a program.
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_TRUSTED_GIT = "/usr/bin/git"
_SOURCE_ROOT = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
_BUILD_WORKFLOW_RELATIVE = os.path.join(".github", "workflows", "build.yml")
_COMBINED_JOBS = ("host-tests", "build", "extended-host-tests", "legacy")
_METADATA_ADAPTER_JOBS = ("host-tests", "build")
_EVENT_IDENTITY_JOB = "event-identity"
_EVENT_ROUTER_JOB = "event-router"
_EVENT_CLASSIFIER_JOB = "event-classifier"
_EXPECTED_JOBS = (
    _EVENT_IDENTITY_JOB,
    _EVENT_ROUTER_JOB,
    _EVENT_CLASSIFIER_JOB,
) + _COMBINED_JOBS + (
    "summary",
)
_CHECKOUT_USES = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
_CHECKOUT_WITH = (
    ("fetch-depth", "0"),
    ("persist-credentials", "false"),
    (
        "ref",
        "${{ (needs.event-classifier.result == 'success' && "
        "needs.event-classifier.outputs.expected_head) || "
        "(needs.event-classifier.result == 'failure' && "
        "needs.event-identity.outputs.fallback_sha) || '' }}",
    ),
    ("submodules", "recursive"),
)
_CLASSIFIER_CHECKOUT_WITH = (
    ("fetch-depth", "1"),
    ("persist-credentials", "false"),
    (
        "ref",
        "${{ needs.event-identity.outputs.classifier_ref }}",
    ),
)
_UPLOAD_USES = (
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
)
_UPLOAD_WITH = (
    ("if-no-files-found", "error"),
    (
        "name",
        "modern-release-all-locales-all-features-aapcs-bps-${{ "
        "needs.event-identity.outputs.fallback_sha }}",
    ),
    ("path", "${{ runner.temp }}/patch-artifact"),
    ("retention-days", "30"),
)
_EXPECTED_BUILD_SHA_EXPRESSION = (
    "${{ (needs.event-classifier.result == 'success' && "
    "needs.event-classifier.outputs.expected_head) || "
    "(needs.event-classifier.result == 'failure' && "
    "needs.event-identity.outputs.fallback_sha) || '' }}"
)
_FULL_WORKER_STEP_CONDITION = (
    "${{ needs.event-classifier.result == 'failure' || "
    "needs.event-classifier.outputs.classification == 'full' }}"
)
_METADATA_ADAPTER_STEP_CONDITION = (
    "${{ needs.event-classifier.result == 'success' && "
    "needs.event-classifier.outputs.classification == 'metadata-only' }}"
)
_CLASSIFIER_REF_EXPRESSION = (
    "${{ needs.event-identity.outputs.classifier_ref }}"
)
_CLASSIFIER_EXPECTED_SHA_EXPRESSION = (
    "${{ needs.event-identity.outputs.classifier_expected_sha }}"
)
_WORKER_CONDITION = (
    "${{ always() && ((needs.event-classifier.result == 'success' && "
    "needs.event-identity.result == 'success' && "
    "needs.event-classifier.outputs.classification == 'full' && "
    "needs.event-classifier.outputs.head_valid == 'true' && "
    "needs.event-classifier.outputs.run_expensive == 'true' && "
    "((github.event_name == 'pull_request' && "
    "needs.event-identity.outputs.fallback_kind == 'pull_request' && "
    "needs.event-identity.outputs.fallback_sha == "
    "needs.event-classifier.outputs.expected_head && "
    "needs.event-classifier.outputs.expected_head == "
    "github.event.pull_request.head.sha && "
    "github.event.pull_request.head.sha != '' && "
    "(needs.event-classifier.outputs.identity_valid == 'true' || "
    "needs.event-classifier.outputs.full_fallback == 'true')) || "
    "(github.event_name == 'push' && "
    "needs.event-identity.outputs.fallback_kind == 'push' && "
    "needs.event-identity.outputs.fallback_sha == "
    "needs.event-classifier.outputs.expected_head && "
    "needs.event-identity.outputs.fallback_sha == github.sha && "
    "needs.event-classifier.outputs.identity_valid == 'true' && "
    "needs.event-classifier.outputs.expected_head == github.event.after && "
    "needs.event-classifier.outputs.expected_base == '' && "
    "github.event.after != ''))) || "
    "(needs.event-classifier.result == 'failure' && "
    "needs.event-identity.result == 'success' && "
    "((github.event_name == 'pull_request' && "
    "needs.event-identity.outputs.fallback_kind == 'pull_request' && "
    "needs.event-identity.outputs.fallback_sha == "
    "github.event.pull_request.head.sha) || "
    "(github.event_name == 'push' && "
    "needs.event-identity.outputs.fallback_kind == 'push' && "
    "needs.event-identity.outputs.fallback_sha == github.event.after && "
    "needs.event-identity.outputs.fallback_sha == github.sha)))) }}"
)
_HOST_BUILD_CONDITION = (
    "${{ always() && ((needs.event-classifier.result == 'success' && "
    "needs.event-identity.result == 'success' && "
    "needs.event-classifier.outputs.classification == 'metadata-only' && "
    "needs.event-classifier.outputs.head_valid == 'true' && "
    "needs.event-classifier.outputs.identity_valid == 'true' && "
    "needs.event-classifier.outputs.full_fallback == 'false' && "
    "needs.event-classifier.outputs.run_expensive == 'false' && "
    "github.event_name == 'pull_request' && "
    "needs.event-identity.outputs.fallback_kind == 'pull_request' && "
    "needs.event-identity.outputs.fallback_sha == "
    "needs.event-classifier.outputs.expected_head && "
    "needs.event-classifier.outputs.expected_head == "
    "github.event.pull_request.head.sha && "
    "needs.event-classifier.outputs.expected_base == "
    "github.event.pull_request.base.sha && "
    "github.event.pull_request.head.sha != '' && "
    "github.event.pull_request.base.sha != '') || "
    "(needs.event-classifier.result == 'success' && "
    "needs.event-identity.result == 'success' && "
    "needs.event-classifier.outputs.classification == 'full' && "
    "needs.event-classifier.outputs.head_valid == 'true' && "
    "needs.event-classifier.outputs.run_expensive == 'true' && "
    "((github.event_name == 'pull_request' && "
    "needs.event-identity.outputs.fallback_kind == 'pull_request' && "
    "needs.event-identity.outputs.fallback_sha == "
    "needs.event-classifier.outputs.expected_head && "
    "needs.event-classifier.outputs.expected_head == "
    "github.event.pull_request.head.sha && "
    "github.event.pull_request.head.sha != '' && "
    "(needs.event-classifier.outputs.identity_valid == 'true' || "
    "needs.event-classifier.outputs.full_fallback == 'true')) || "
    "(github.event_name == 'push' && "
    "needs.event-identity.outputs.fallback_kind == 'push' && "
    "needs.event-identity.outputs.fallback_sha == "
    "needs.event-classifier.outputs.expected_head && "
    "needs.event-identity.outputs.fallback_sha == github.sha && "
    "needs.event-classifier.outputs.identity_valid == 'true' && "
    "needs.event-classifier.outputs.expected_head == github.event.after && "
    "needs.event-classifier.outputs.expected_base == '' && "
    "github.event.after != ''))) || "
    "(needs.event-classifier.result == 'failure' && "
    "needs.event-identity.result == 'success' && "
    "((github.event_name == 'pull_request' && "
    "needs.event-identity.outputs.fallback_kind == 'pull_request' && "
    "needs.event-identity.outputs.fallback_sha == "
    "github.event.pull_request.head.sha) || "
    "(github.event_name == 'push' && "
    "needs.event-identity.outputs.fallback_kind == 'push' && "
    "needs.event-identity.outputs.fallback_sha == github.event.after && "
    "needs.event-identity.outputs.fallback_sha == github.sha)))) }}"
)
_PUBLISHER_CONDITION = (
    "${{ success() && github.event_name == 'push' && "
    "github.repository == 'laqieer/fireemblem8-expansion' && "
    "github.ref == 'refs/heads/master' && needs.event-identity.result == 'success' && "
    "needs.event-identity.outputs.fallback_kind == 'push' && "
    "needs.event-identity.outputs.fallback_sha == github.event.after && "
    "needs.event-identity.outputs.fallback_sha == github.sha }}"
)
_DYNAMIC_JOB_NAMES = {
    "event-classifier": (
        "${{ needs.event-router.result == 'success' && "
        "needs.event-router.outputs.classification == 'metadata-only' && "
        "'metadata-classifier' || 'event-classifier' }}"
    ),
}
_IDENTITY_COMMANDS = (
    ("is_lower_sha()", "{"),
    ("[[", "$1", "=~", "^[0-9a-f]{40}$", "&&", "$2", "=", '"$1"', "]]"),
    ("}",),
    ("is_pr_number()", "{"),
    ("[[", "$1", "=~", "^[1-9][0-9]*$", "&&", "$2", "=", "$1", "]]"),
    ("}",),
    ("classifier_available=false",),
    ("classifier_expected_sha=",),
    ("classifier_ref=",),
    ("fallback_kind=none",),
    ("fallback_sha=",),
    ("if", "[[", "$EVENT_NAME", "=", "pull_request", "]];", "then"),
    (
        "if",
        "is_pr_number",
        "$PR_NUMBER",
        "$PR_NUMBER_JSON",
        "&&",
        "[[",
        "$EVENT_REF",
        "=",
        "refs/pull/$PR_NUMBER/merge",
        "]]",
        "&&",
        "is_lower_sha",
        "$PR_HEAD_SHA",
        "$PR_HEAD_SHA_JSON;",
        "then",
    ),
    ("fallback_kind=pull_request",),
    ("fallback_sha=$PR_HEAD_SHA",),
    ("fi",),
    ("if", "is_lower_sha", "$PR_BASE_SHA", "$PR_BASE_SHA_JSON;", "then"),
    ("classifier_expected_sha=$PR_BASE_SHA",),
    ("classifier_ref=$PR_BASE_SHA",),
    ("elif", "[[", "-n", "$DEFAULT_BRANCH", "]];", "then"),
    ("bootstrap_ref=refs/heads/$DEFAULT_BRANCH",),
    (
        "if",
        "/usr/bin/git",
        "check-ref-format",
        "$bootstrap_ref",
        ">",
        "/dev/null",
        "2>&1;",
        "then",
    ),
    ("classifier_ref=$bootstrap_ref",),
    ("fi",),
    ("fi",),
    (
        "elif",
        "[[",
        "$EVENT_NAME",
        "=",
        "push",
        "&&",
        "$EVENT_REF",
        "=",
        "refs/heads/master",
        "]]",
        "&&",
        "is_lower_sha",
        "$PUSH_SHA",
        "$PUSH_SHA_JSON",
        "&&",
        "is_lower_sha",
        "$RAW_SHA",
        "$RAW_SHA_JSON",
        "&&",
        "[[",
        "$RAW_SHA",
        "=",
        "$PUSH_SHA",
        "]];",
        "then",
    ),
    ("classifier_expected_sha=$PUSH_SHA",),
    ("classifier_ref=$PUSH_SHA",),
    ("fallback_kind=push",),
    ("fallback_sha=$PUSH_SHA",),
    ("fi",),
    ("if", "[[", "-n", "$classifier_ref", "]];", "then"),
    ("classifier_available=true",),
    ("fi",),
    ("{",),
    ("echo", "classifier_available=$classifier_available"),
    ("echo", "classifier_expected_sha=$classifier_expected_sha"),
    ("echo", "classifier_ref=$classifier_ref"),
    ("echo", "fallback_kind=$fallback_kind"),
    ("echo", "fallback_sha=$fallback_sha"),
    ("}", ">>", "$GITHUB_OUTPUT"),
)
_CLASSIFIER_VERIFY_COMMANDS = (
    ("ACTUAL_SHA=$(git rev-parse HEAD)",),
    ("printf", "classifier.sha=%s\\n", "$ACTUAL_SHA"),
    ("if", "[", "-n", "$CLASSIFIER_EXPECTED_SHA", "];", "then"),
    ("test", "$ACTUAL_SHA", "=", "$CLASSIFIER_EXPECTED_SHA"),
    ("else",),
    ("test", "$CLASSIFIER_REF", "=", "refs/heads/$DEFAULT_BRANCH"),
    ("fi",),
)
_CLASSIFIER_COMMANDS = (
    ("if", "test", "-f", "scripts/workflow_pilot/event_classifier.py;", "then"),
    (
        "/usr/bin/python3",
        "-I",
        "scripts/workflow_pilot/isolated_launcher.py",
        "classify-event",
        "--event-name",
        "$GITHUB_EVENT_NAME",
        "--event-path",
        "$GITHUB_EVENT_PATH",
        "--github-ref",
        "$GITHUB_REF",
        "--github-sha",
        "$GITHUB_SHA",
        "--pr-base-sha",
        "$PR_BASE_SHA",
        "--pr-head-sha",
        "$PR_HEAD_SHA",
        "--push-sha",
        "$PUSH_SHA",
        "--output",
        "$GITHUB_OUTPUT",
    ),
    ("else",),
    ("base_ref_valid=false",),
    ("expected_base=",),
    ("expected_head=",),
    ("full_fallback=false",),
    ("head_valid=false",),
    ("identity_valid=false",),
    ("if", "[[", "$GITHUB_EVENT_NAME", "=", "pull_request", "]];", "then"),
    ("LC_ALL=C",),
    ("export", "LC_ALL"),
    (
        "if",
        "[[",
        "$PR_BASE_REF",
        "!=",
        "@",
        "&&",
        "$PR_BASE_REF_JSON",
        "=",
        '"*"',
        "&&",
        "${#PR_BASE_REF}",
        "-le",
        "1024",
        "]]",
        "&&",
        "/usr/bin/git",
        "check-ref-format",
        "refs/heads/$PR_BASE_REF",
        ">",
        "/dev/null",
        "2>&1;",
        "then",
    ),
    ("base_ref_valid=true",),
    ("fi",),
    (
        "if",
        "[[",
        "$PR_BASE_SHA",
        "=~",
        "^[0-9a-f]{40}$",
        "&&",
        "$PR_BASE_SHA_JSON",
        "=",
        '"$PR_BASE_SHA"',
        "]];",
        "then",
    ),
    ("expected_base=$PR_BASE_SHA",),
    ("fi",),
    (
        "if",
        "[[",
        "$VALIDATED_FALLBACK_KIND",
        "=",
        "pull_request",
        "&&",
        "$VALIDATED_FALLBACK_SHA",
        "=",
        "$PR_HEAD_SHA",
        "]];",
        "then",
    ),
    ("expected_head=$VALIDATED_FALLBACK_SHA",),
    ("head_valid=true",),
    ("fi",),
    (
        "if",
        "[[",
        "-n",
        "$expected_base",
        "&&",
        "-n",
        "$expected_head",
        "&&",
        "$base_ref_valid",
        "=",
        "true",
        "]];",
        "then",
    ),
    ("identity_valid=true",),
    ("elif", "[[", "$head_valid", "=", "true", "]];", "then"),
    ("full_fallback=true",),
    ("fi",),
    (
        "elif",
        "[[",
        "$VALIDATED_FALLBACK_KIND",
        "=",
        "push",
        "&&",
        "$VALIDATED_FALLBACK_SHA",
        "=",
        "$PUSH_SHA",
        "]];",
        "then",
    ),
    ("expected_head=$VALIDATED_FALLBACK_SHA",),
    ("head_valid=true",),
    ("identity_valid=true",),
    ("fi",),
    ("{",),
    ("echo", "classification=full"),
    ("echo", "reason=classifier-bootstrap"),
    ("echo", "expected_base=$expected_base"),
    ("echo", "expected_head=$expected_head"),
    ("echo", "full_fallback=$full_fallback"),
    ("echo", "head_valid=$head_valid"),
    ("echo", "identity_valid=$identity_valid"),
    ("echo", "run_expensive=true"),
    ("}", ">>", "$GITHUB_OUTPUT"),
    ("fi",),
)
_MODE_COMMANDS = (
    ("if", "[", "$ROUTER_RESULT", "!=", "success", "];", "then"),
    ("echo", "Build event router did not succeed: $ROUTER_RESULT", ">&2"),
    ("exit", "1"),
    ("fi",),
    ("if", "[", "$EVENT_IDENTITY_RESULT", "!=", "success", "];", "then"),
    (
        "echo",
        "trusted Build event identity did not succeed: $EVENT_IDENTITY_RESULT",
        ">&2",
    ),
    ("exit", "1"),
    ("fi",),
    ("if", "[", "$EVENT_NAME", "=", "pull_request", "];", "then"),
    (
        "if",
        "[",
        "$TRUSTED_EVENT_KIND",
        "!=",
        "pull_request",
        "]",
        "||",
        "[",
        "-z",
        "$TRUSTED_EVENT_SHA",
        "]",
        "||",
        "[",
        "$TRUSTED_EVENT_SHA",
        "!=",
        "$PR_HEAD_SHA",
        "]",
        "||",
        "[",
        "$TRUSTED_EVENT_SHA",
        "!=",
        "$CLASSIFIED_HEAD",
        "];",
        "then",
    ),
    ("echo", "classified PR head lacks coherent trusted event identity", ">&2"),
    ("exit", "1"),
    ("fi",),
    ("elif", "[", "$EVENT_NAME", "=", "push", "];", "then"),
    (
        "if",
        "[",
        "$TRUSTED_EVENT_KIND",
        "!=",
        "push",
        "]",
        "||",
        "[",
        "-z",
        "$TRUSTED_EVENT_SHA",
        "]",
        "||",
        "[",
        "$TRUSTED_EVENT_SHA",
        "!=",
        "$PUSH_SHA",
        "]",
        "||",
        "[",
        "$TRUSTED_EVENT_SHA",
        "!=",
        "$EVENT_SHA",
        "]",
        "||",
        "[",
        "$TRUSTED_EVENT_SHA",
        "!=",
        "$CLASSIFIED_HEAD",
        "];",
        "then",
    ),
    ("echo", "classified push head lacks coherent trusted event identity", ">&2"),
    ("exit", "1"),
    ("fi",),
    ("else",),
    ("echo", "classified event has no trusted identity mode", ">&2"),
    ("exit", "1"),
    ("fi",),
    ("case", "$HEAD_VALID", "in"),
    ("true|false)", ";;"),
    (
        "*)",
        "echo",
        "Build event router returned invalid head validity",
        ">&2;",
        "exit",
        "1",
        ";;",
    ),
    ("esac",),
    ("case", "$FULL_FALLBACK", "in"),
    ("true|false)", ";;"),
    (
        "*)",
        "echo",
        "Build event router returned invalid full fallback",
        ">&2;",
        "exit",
        "1",
        ";;",
    ),
    ("esac",),
    ("case", "$IDENTITY_VALID", "in"),
    ("true|false)", ";;"),
    (
        "*)",
        "echo",
        "Build event router returned invalid identity validity",
        ">&2;",
        "exit",
        "1",
        ";;",
    ),
    ("esac",),
    ("if", "[", "$CLASSIFICATION", "=", "metadata-only", "];", "then"),
    (
        "if",
        "[",
        "$EVENT_NAME",
        "!=",
        "pull_request",
        "]",
        "||",
        "[",
        "$TRUSTED_EVENT_KIND",
        "!=",
        "pull_request",
        "]",
        "||",
        "[",
        "$FULL_FALLBACK",
        "!=",
        "false",
        "]",
        "||",
        "[",
        "$HEAD_VALID",
        "!=",
        "true",
        "]",
        "||",
        "[",
        "$IDENTITY_VALID",
        "!=",
        "true",
        "]",
        "||",
        "[",
        "$RUN_EXPENSIVE",
        "!=",
        "false",
        "];",
        "then",
    ),
    ("echo", "metadata event mode is not authoritative", ">&2"),
    ("exit", "1"),
    ("fi",),
    ("exit", "0"),
    ("fi",),
    (
        "if",
        "[",
        "$CLASSIFICATION",
        "!=",
        "full",
        "]",
        "||",
        "[",
        "$RUN_EXPENSIVE",
        "!=",
        "true",
        "];",
        "then",
    ),
    ("echo", "full Build event mode is not authoritative", ">&2"),
    ("exit", "1"),
    ("fi",),
    (
        "if",
        "[",
        "$FULL_FALLBACK",
        "=",
        "true",
        "]",
        "&&",
        "{",
        "[",
        "$HEAD_VALID",
        "!=",
        "true",
        "]",
        "||",
        "[",
        "$IDENTITY_VALID",
        "!=",
        "false",
        "];",
        "};",
        "then",
    ),
    ("echo", "full fallback mode is not authoritative", ">&2"),
    ("exit", "1"),
    ("fi",),
)
_METADATA_EVENT_PRODUCER_COMMANDS = (
    ("if", "test", "-f", "scripts/workflow_pilot/metadata_event.py;", "then"),
    (
        "if", "!", "/usr/bin/python3", "-I",
        "scripts/workflow_pilot/isolated_launcher.py", "attest-metadata-event",
        "--event-path", "$GITHUB_EVENT_PATH", "--repository", "$GITHUB_REPOSITORY",
        "--run-id", "$GITHUB_RUN_ID", "--run-number", "$GITHUB_RUN_NUMBER",
        "--run-attempt", "$GITHUB_RUN_ATTEMPT", "--output", "$GITHUB_OUTPUT;", "then",
    ),
    ("echo", "Metadata event attribution unavailable; reconciliation must hold.", ">&2"),
    ("fi",),
    ("else",),
    ("echo", "Trusted base lacks metadata event attribution; reconciliation must hold."),
    ("fi",),
)
_METADATA_EVENT_PRODUCER_ENV = (
    ("BASH_ENV", "''"),
    ("ENV", "''"),
    ("PATH", "/usr/bin:/bin"),
    ("PYTHONPATH", "''"),
)
_METADATA_EVENT_DIGEST_EXPRESSION = (
    "${{ needs.event-router.outputs.metadata_event_digest }}"
)
_METADATA_EVENT_MARKER_NAME = (
    "workflow-pilot-metadata-event:v1:" + _METADATA_EVENT_DIGEST_EXPRESSION
)
_METADATA_EVENT_MARKER_CONDITION = (
    "${{ needs.event-router.outputs.classification == 'metadata-only' && "
    "needs.event-router.outputs.metadata_event_digest != '' }}"
)
_METADATA_EVENT_MARKER_COMMANDS = (
    ("[[", "$METADATA_EVENT_DIGEST", "=~", "^[0-9a-f]{64}$", "]]"),
)
_EXPECTED_JOB_OUTPUTS = {
    "event-identity": (
        (
            "classifier_available",
            "${{ steps.identity.outputs.classifier_available }}",
        ),
        (
            "classifier_expected_sha",
            "${{ steps.identity.outputs.classifier_expected_sha }}",
        ),
        ("classifier_ref", "${{ steps.identity.outputs.classifier_ref }}"),
        ("fallback_kind", "${{ steps.identity.outputs.fallback_kind }}"),
        ("fallback_sha", "${{ steps.identity.outputs.fallback_sha }}"),
    ),
    "event-router": (
        ("classification", "${{ steps.classify.outputs.classification }}"),
        ("expected_base", "${{ steps.classify.outputs.expected_base }}"),
        ("expected_head", "${{ steps.classify.outputs.expected_head }}"),
        ("full_fallback", "${{ steps.classify.outputs.full_fallback }}"),
        ("head_valid", "${{ steps.classify.outputs.head_valid }}"),
        ("identity_valid", "${{ steps.classify.outputs.identity_valid }}"),
        ("metadata_event_digest", "${{ steps.metadata-event.outputs.digest }}"),
        ("reason", "${{ steps.classify.outputs.reason }}"),
        ("run_expensive", "${{ steps.classify.outputs.run_expensive }}"),
    ),
    "event-classifier": (
        ("classification", "${{ needs.event-router.outputs.classification }}"),
        ("expected_base", "${{ needs.event-router.outputs.expected_base }}"),
        ("expected_head", "${{ needs.event-router.outputs.expected_head }}"),
        ("full_fallback", "${{ needs.event-router.outputs.full_fallback }}"),
        ("head_valid", "${{ needs.event-router.outputs.head_valid }}"),
        ("identity_valid", "${{ needs.event-router.outputs.identity_valid }}"),
        ("reason", "${{ needs.event-router.outputs.reason }}"),
        ("run_expensive", "${{ needs.event-router.outputs.run_expensive }}"),
    ),
}
_EXPECTED_JOB_ENV = {
    "event-identity": (
        ("BASH_ENV", "''"),
        ("DEFAULT_BRANCH", "${{ github.event.repository.default_branch }}"),
        ("ENV", "''"),
        ("EVENT_NAME", "${{ github.event_name }}"),
        ("EVENT_REF", "${{ github.ref }}"),
        ("PATH", "/usr/bin:/bin"),
        ("PR_BASE_SHA", "${{ github.event.pull_request.base.sha }}"),
        ("PR_BASE_SHA_JSON", "${{ toJSON(github.event.pull_request.base.sha) }}"),
        ("PR_HEAD_SHA", "${{ github.event.pull_request.head.sha }}"),
        ("PR_HEAD_SHA_JSON", "${{ toJSON(github.event.pull_request.head.sha) }}"),
        ("PR_NUMBER", "${{ github.event.number }}"),
        ("PR_NUMBER_JSON", "${{ toJSON(github.event.number) }}"),
        ("PUSH_SHA", "${{ github.event.after }}"),
        ("PUSH_SHA_JSON", "${{ toJSON(github.event.after) }}"),
        ("RAW_SHA", "${{ github.sha }}"),
        ("RAW_SHA_JSON", "${{ toJSON(github.sha) }}"),
    ),
    "event-router": (
        (
            "CLASSIFIER_AVAILABLE",
            "${{ needs.event-identity.outputs.classifier_available }}",
        ),
        ("CLASSIFIER_EXPECTED_SHA", _CLASSIFIER_EXPECTED_SHA_EXPRESSION),
        ("CLASSIFIER_REF", _CLASSIFIER_REF_EXPRESSION),
        ("DEFAULT_BRANCH", "${{ github.event.repository.default_branch }}"),
        ("PR_BASE_REF", "${{ github.event.pull_request.base.ref }}"),
        ("PR_BASE_REF_JSON", "${{ toJSON(github.event.pull_request.base.ref) }}"),
        ("PR_BASE_SHA", "${{ github.event.pull_request.base.sha }}"),
        ("PR_BASE_SHA_JSON", "${{ toJSON(github.event.pull_request.base.sha) }}"),
        ("PR_HEAD_SHA", "${{ github.event.pull_request.head.sha }}"),
        ("PUSH_SHA", "${{ github.event.after }}"),
        (
            "VALIDATED_FALLBACK_KIND",
            "${{ needs.event-identity.outputs.fallback_kind }}",
        ),
        (
            "VALIDATED_FALLBACK_SHA",
            "${{ needs.event-identity.outputs.fallback_sha }}",
        ),
    ),
    "event-classifier": (
        ("CLASSIFICATION", "${{ needs.event-router.outputs.classification }}"),
        ("CLASSIFIED_HEAD", "${{ needs.event-router.outputs.expected_head }}"),
        ("EVENT_IDENTITY_RESULT", "${{ needs.event-identity.result }}"),
        ("EVENT_NAME", "${{ github.event_name }}"),
        ("EVENT_SHA", "${{ github.sha }}"),
        ("FULL_FALLBACK", "${{ needs.event-router.outputs.full_fallback }}"),
        ("HEAD_VALID", "${{ needs.event-router.outputs.head_valid }}"),
        ("IDENTITY_VALID", "${{ needs.event-router.outputs.identity_valid }}"),
        ("PR_HEAD_SHA", "${{ github.event.pull_request.head.sha }}"),
        ("PUSH_SHA", "${{ github.event.after }}"),
        ("ROUTER_RESULT", "${{ needs.event-router.result }}"),
        ("RUN_EXPENSIVE", "${{ needs.event-router.outputs.run_expensive }}"),
        (
            "TRUSTED_EVENT_KIND",
            "${{ needs.event-identity.outputs.fallback_kind }}",
        ),
        (
            "TRUSTED_EVENT_SHA",
            "${{ needs.event-identity.outputs.fallback_sha }}",
        ),
    ),
    "host-tests": (("EXPECTED_BUILD_SHA", _EXPECTED_BUILD_SHA_EXPRESSION),),
    "build": (("EXPECTED_BUILD_SHA", _EXPECTED_BUILD_SHA_EXPRESSION),),
    "extended-host-tests": (
        ("EXPECTED_BUILD_SHA", _EXPECTED_BUILD_SHA_EXPRESSION),
    ),
    "legacy": (
        ("AGBCC_COMMIT", "da598c1d918402c42c0c0d7128ba14567f3175e9"),
        ("EXPECTED_BUILD_SHA", _EXPECTED_BUILD_SHA_EXPRESSION),
        (
            "MGFEMBP_AGBCC_COMMIT",
            "63b22f3eb8a8051af30bd80c4795b355e439e7ef",
        ),
    ),
    "summary": tuple(
        sorted(
            (
                ("BUILD_RESULT", "${{ needs.build.result }}"),
                ("CLASSIFICATION", "${{ needs.event-classifier.outputs.classification }}"),
                (
                    "CLASSIFIED_BASE_SHA",
                    "${{ needs.event-classifier.outputs.expected_base }}",
                ),
                (
                    "CLASSIFIED_BUILD_SHA",
                    "${{ needs.event-classifier.outputs.expected_head }}",
                ),
                ("CLASSIFIER_RESULT", "${{ needs.event-classifier.result }}"),
                ("EXTENDED_HOST_TESTS_RESULT", "${{ needs.extended-host-tests.result }}"),
                ("FALLBACK_IDENTITY_RESULT", "${{ needs.event-identity.result }}"),
                ("FALLBACK_KIND", "${{ needs.event-identity.outputs.fallback_kind }}"),
                ("FALLBACK_SHA", "${{ needs.event-identity.outputs.fallback_sha }}"),
                ("FULL_FALLBACK", "${{ needs.event-classifier.outputs.full_fallback }}"),
                ("GITHUB_API_URL", "${{ github.api_url }}"),
                ("GITHUB_REPOSITORY", "${{ github.repository }}"),
                ("GITHUB_TOKEN", "${{ github.token }}"),
                ("HEAD_VALID", "${{ needs.event-classifier.outputs.head_valid }}"),
                ("HOST_TESTS_RESULT", "${{ needs.host-tests.result }}"),
                ("IDENTITY_VALID", "${{ needs.event-classifier.outputs.identity_valid }}"),
                ("LEGACY_RESULT", "${{ needs.legacy.result }}"),
                ("PR_BASE_SHA", "${{ github.event.pull_request.base.sha }}"),
                ("PR_HEAD_SHA", "${{ github.event.pull_request.head.sha }}"),
                ("PR_NUMBER", "${{ github.event.number }}"),
                ("PUSH_SHA", "${{ github.event.after }}"),
                ("RAW_PUSH_SHA", "${{ github.sha }}"),
                ("RUN_ATTEMPT", "${{ github.run_attempt }}"),
                ("RUN_EXPENSIVE", "${{ needs.event-classifier.outputs.run_expensive }}"),
                ("RUN_ID", "${{ github.run_id }}"),
                ("RUN_NUMBER", "${{ github.run_number }}"),
            )
        )
    ),
}
_METADATA_ADAPTER_STEP_NAME = "Attest metadata-only branch-protection continuity"
_SUMMARY_STEP_NAME = "Render fail-closed combined Build summary"
_METADATA_ADAPTER_ENV = tuple(
    sorted(
        (
            ("BASH_ENV", "''"),
            ("CLASSIFICATION", "${{ needs.event-classifier.outputs.classification }}"),
            ("CLASSIFIED_BASE_SHA", "${{ needs.event-classifier.outputs.expected_base }}"),
            ("CLASSIFIED_BUILD_SHA", "${{ needs.event-classifier.outputs.expected_head }}"),
            ("CLASSIFIER_RESULT", "${{ needs.event-classifier.result }}"),
            ("ENV", "''"),
            ("FALLBACK_IDENTITY_RESULT", "${{ needs.event-identity.result }}"),
            ("FALLBACK_KIND", "${{ needs.event-identity.outputs.fallback_kind }}"),
            ("FALLBACK_SHA", "${{ needs.event-identity.outputs.fallback_sha }}"),
            ("FULL_FALLBACK", "${{ needs.event-classifier.outputs.full_fallback }}"),
            ("HEAD_VALID", "${{ needs.event-classifier.outputs.head_valid }}"),
            ("IDENTITY_VALID", "${{ needs.event-classifier.outputs.identity_valid }}"),
            ("PATH", "/usr/bin:/bin"),
            ("RUN_EXPENSIVE", "${{ needs.event-classifier.outputs.run_expensive }}"),
        )
    )
)
_NON_GATE_STEP_NAMES = {
    _METADATA_ADAPTER_STEP_NAME,
    "Verify checked-out revision",
    "Hydrate workflow-pilot Git authority",
    "Validate ownership with exact PR-base verifier",
    "Install host-only dependencies (no arm-none-eabi toolchain)",
    "Install dependencies",
    "Build tools",
    "Install extended host dependencies",
    "Install archival build dependencies",
    "Preflight archival toolchain executables",
    "Install pinned archival agbcc compilers",
}
_DOCS_GOVERNANCE_STEP_NAME = "Check documentation (issues #7/#17)"
_WORKFLOW_PILOT_TEST_STEP_NAME = (
    "Run workflow-pilot reporter regression suite (issue #176)"
)
_WORKFLOW_PILOT_BASELINE_STEP_NAME = (
    "Validate workflow-pilot baseline against checked-out Git history"
)
_VALIDATION_OWNERSHIP_TEST_STEP_NAME = (
    "Run validation ownership regression suite (issue #180)"
)
_VALIDATION_OWNERSHIP_CHECK_STEP_NAME = (
    "Validate validation ownership graph (issue #180)"
)
_VALIDATION_OWNERSHIP_BASE_STEP_NAME = (
    "Validate ownership with exact PR-base verifier"
)
_FULL_MODE_ONLY_JOB_STEPS = {
    ("host-tests", "Verify checked-out revision"),
    ("host-tests", "Hydrate workflow-pilot Git authority"),
    ("host-tests", "Install host-only dependencies (no arm-none-eabi toolchain)"),
    ("host-tests", "Run gba-playtest host test suite"),
    ("host-tests", "Run upstream-port tooling test suite"),
    ("host-tests", "Run workflow contract test suite"),
    ("host-tests", _WORKFLOW_PILOT_TEST_STEP_NAME),
    ("host-tests", _WORKFLOW_PILOT_BASELINE_STEP_NAME),
    ("host-tests", _VALIDATION_OWNERSHIP_BASE_STEP_NAME),
    ("host-tests", _VALIDATION_OWNERSHIP_TEST_STEP_NAME),
    ("host-tests", _VALIDATION_OWNERSHIP_CHECK_STEP_NAME),
    ("host-tests", "Run localization host test suite (issue #18)"),
    ("host-tests", "Run full-game localization width contract (issue #18)"),
    ("build", "Verify checked-out revision"),
    ("build", "Check tracked artifacts"),
    ("build", _DOCS_GOVERNANCE_STEP_NAME),
    ("build", "Install dependencies"),
    ("build", "Build tools"),
    ("build", "Run CodeQL alert regression suite (issue #84)"),
    ("build", "Check default build lane and quickstart legacy glue (issue #15)"),
    ("build", "Check generated-data tables for drift"),
    ("build", "Build and verify modern target ROMs and linker"),
    ("build", "Boundary/serialization item-ID-expansion + content runtime gate (cap 0xCE)"),
    ("build", "Build and verify all-locales/all-features map menu (issues #49/#168)"),
}
_SCRUBBED_PILOT_ENV = (
    "BASH_ENV: ''",
    "ENV: ''",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES: ''",
    "GIT_CEILING_DIRECTORIES: ''",
    "GIT_COMMON_DIR: ''",
    "GIT_CONFIG_COUNT: '0'",
    "GIT_CONFIG_GLOBAL: /dev/null",
    "GIT_CONFIG_KEY_0: ''",
    "GIT_CONFIG_NOSYSTEM: '1'",
    "GIT_CONFIG_PARAMETERS: ''",
    "GIT_CONFIG_SYSTEM: /dev/null",
    "GIT_CONFIG_VALUE_0: ''",
    "GIT_DIR: ''",
    "GIT_EXEC_PATH: ''",
    "GIT_INDEX_FILE: ''",
    "GIT_NAMESPACE: ''",
    "GIT_NO_LAZY_FETCH: '1'",
    "GIT_NO_REPLACE_OBJECTS: '1'",
    "GIT_OBJECT_DIRECTORY: ''",
    "GIT_REPLACE_REF_BASE: ''",
    "GIT_WORK_TREE: ''",
    "PATH: /usr/bin:/bin",
    "PYTHONPATH: ''",
)
_VALIDATION_OWNERSHIP_ENV = (
    *_SCRUBBED_PILOT_ENV,
    "GNUMAKEFLAGS: ''",
    "MAKEFLAGS: ''",
    "MAKEOVERRIDES: ''",
    "MFLAGS: ''",
)
_BASE_VERIFIER_ENV = (
    *_VALIDATION_OWNERSHIP_ENV,
    "BUILD_EVENT_NAME: ${{ github.event_name }}",
    "EXPECTED_BASE_SHA: ${{ (needs.event-classifier.result == 'success' && "
    "needs.event-classifier.outputs.expected_base) || "
    "(github.event_name == 'pull_request' && github.event.pull_request.base.sha) "
    "|| '' }}",
    "EXPECTED_CANDIDATE_SHA: ${{ (needs.event-classifier.result == 'success' && "
    "needs.event-classifier.outputs.expected_head) || "
    "needs.event-identity.outputs.fallback_sha || '' }}",
    "VALIDATION_OWNERSHIP_TEMP: ${{ runner.temp }}",
)
_EXPECTED_STEP_ROLES = {
    "event-identity": (
        ("setup", "Validate trusted event identities"),
    ),
    "event-router": (
        ("setup", "Require classifier authority"),
        ("setup", None),
        ("setup", "Verify classifier authority revision"),
        ("setup", "Classify Build event"),
        ("setup", "Bind immutable metadata event"),
    ),
    "event-classifier": (
        ("setup", "Verify authoritative Build event mode"),
        ("setup", _METADATA_EVENT_MARKER_NAME),
    ),
    "host-tests": (
        ("setup", _METADATA_ADAPTER_STEP_NAME),
        ("setup", None),
        ("setup", "Verify checked-out revision"),
        ("setup", "Hydrate workflow-pilot Git authority"),
        ("setup", "Install host-only dependencies (no arm-none-eabi toolchain)"),
        ("gate", "Run gba-playtest host test suite"),
        ("gate", "Run upstream-port tooling test suite"),
        ("gate", "Run workflow contract test suite"),
        ("gate", _WORKFLOW_PILOT_TEST_STEP_NAME),
        ("gate", _WORKFLOW_PILOT_BASELINE_STEP_NAME),
        ("setup", _VALIDATION_OWNERSHIP_BASE_STEP_NAME),
        ("gate", _VALIDATION_OWNERSHIP_TEST_STEP_NAME),
        ("gate", _VALIDATION_OWNERSHIP_CHECK_STEP_NAME),
        ("gate", "Run localization host test suite (issue #18)"),
        ("gate", "Run full-game localization width contract (issue #18)"),
    ),
    "build": (
        ("setup", _METADATA_ADAPTER_STEP_NAME),
        ("setup", None),
        ("setup", "Verify checked-out revision"),
        ("gate", "Check tracked artifacts"),
        ("standalone-gate", _DOCS_GOVERNANCE_STEP_NAME),
        ("setup", "Install dependencies"),
        ("setup", "Build tools"),
        ("gate", "Run CodeQL alert regression suite (issue #84)"),
        ("gate", "Check default build lane and quickstart legacy glue (issue #15)"),
        ("gate", "Check generated-data tables for drift"),
        ("gate", "Build and verify modern target ROMs and linker"),
        (
            "gate",
            "Boundary/serialization item-ID-expansion + content runtime "
            "gate (cap 0xCE)",
        ),
        (
            "gate",
            "Build and verify all-locales/all-features map menu "
            "(issues #49/#168)",
        ),
        ("publisher", "Create and verify patch artifact"),
        ("publisher", "Upload patch-only artifact"),
    ),
    "extended-host-tests": (
        ("setup", None),
        ("setup", "Verify checked-out revision"),
        ("setup", "Install extended host dependencies"),
        ("gate", "Run CJK font gates"),
        ("gate", "Run multilang texttools codec gates"),
        ("gate", "Run configuration and linker-budget gates"),
    ),
    "legacy": (
        ("setup", None),
        ("setup", "Verify checked-out revision"),
        ("setup", "Install archival build dependencies"),
        ("setup", "Preflight archival toolchain executables"),
        ("setup", "Install pinned archival agbcc compilers"),
        ("setup", "Build tools"),
        ("gate", "Build archival lane without a copyrighted baserom"),
        ("gate", "Validate pinned archival payload identities"),
    ),
    "summary": (("summary", "Render fail-closed combined Build summary"),),
}


def _split_env_prefix(command):
    """Split any leading ``NAME=VALUE`` env-assignment tokens off the front of
    a gate command, returning ``(env_overrides, argv)``. Only a *leading*
    run is treated as env (matching the shell), so a NAME=VALUE that appears
    after the program (e.g. make variable overrides like ``MODERN_CONFIG=debug``)
    stays part of argv."""
    env_overrides = {}
    argv = list(command)
    while argv and _ENV_ASSIGN_RE.match(argv[0]):
        name, _, value = argv[0].partition("=")
        env_overrides[name] = value
        argv = argv[1:]
    return env_overrides, argv


def _split_stdout_redirect(command):
    """Translate the workflow's trailing ``> /dev/null`` without a shell."""
    argv = list(command)
    if argv[-2:] == [">", "/dev/null"]:
        return argv[:-2], subprocess.DEVNULL
    return argv, subprocess.PIPE


def _trusted_git_executable():
    git = os.path.realpath(_TRUSTED_GIT)
    if not os.path.isfile(git) or not os.access(git, os.X_OK):
        raise ValueError(f"trusted Git executable {git!r} is unavailable")
    return git


def _git_environment():
    return {
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


def _git_top_level(path):
    git = _trusted_git_executable()
    try:
        return os.path.realpath(
            subprocess.check_output(
                [
                    git,
                    "--no-replace-objects",
                    "-C",
                    path,
                    "rev-parse",
                    "--show-toplevel",
                ],
                env=_git_environment(),
                stderr=subprocess.PIPE,
                text=True,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(
            f"{path!r} is not inside a checked-out Git repository"
        ) from error


def _resolve_repository_root(repository_root):
    requested_root = os.path.realpath(os.path.abspath(repository_root))
    target_root = _git_top_level(requested_root)
    if requested_root != target_root:
        raise ValueError(
            f"gate repository must be the exact Git top level {target_root!r}"
        )
    return target_root


def _expand_workspace(argv, repository_root):
    substitutions = {
        "$GITHUB_WORKSPACE": repository_root,
        "$GITHUB_WORKSPACE/build/host-python/bin/python3": os.path.join(
            repository_root, "build", "host-python", "bin", "python3"
        ),
    }
    return [
        substitutions.get(argument, argument)
        for argument in argv
    ]


def _workflow_job_entries(text):
    lines = text.splitlines(keepends=True)
    try:
        jobs_index = next(
            index
            for index, line in enumerate(lines)
            if line.rstrip("\r\n") == "jobs:"
        )
    except StopIteration as error:
        raise ValueError("workflow lacks jobs mapping") from error

    starts = []
    for index in range(jobs_index + 1, len(lines)):
        line = lines[index].rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            raise ValueError("workflow has content after the jobs mapping")
        if indent != 2:
            continue
        match = re.fullmatch(r"  ([A-Za-z_][A-Za-z0-9_-]*):", line)
        if match is None:
            raise ValueError("workflow uses unsupported job-key syntax")
        starts.append((match.group(1), index))
    names = [name for name, _ in starts]
    if len(names) != len(set(names)):
        raise ValueError("workflow contains duplicate job names")
    return tuple(
        (
            name,
            "".join(
                lines[
                    index + 1 :
                    starts[position + 1][1]
                    if position + 1 < len(starts)
                    else len(lines)
                ]
            ),
        )
        for position, (name, index) in enumerate(starts)
    )


def _parse_workflow_context(text):
    lines = text.splitlines()
    direct = []
    for index, line in enumerate(lines):
        if line == "jobs:":
            direct.append(("jobs", "", index))
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            match = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
                line,
            )
            if match is None:
                raise ValueError(
                    "workflow uses unsupported top-level key syntax"
                )
            direct.append((match.group(1), match.group(2), index))
    names = [name for name, _, _ in direct]
    if len(names) != len(set(names)):
        raise ValueError("workflow contains duplicate top-level keys")
    if names != ["name", "on", "permissions", "jobs"]:
        raise ValueError(
            "workflow top-level execution context must be exactly "
            "name, on, permissions, and jobs"
        )

    values = {}
    for field_index, (name, raw_value, line_index) in enumerate(direct):
        end = (
            direct[field_index + 1][2]
            if field_index + 1 < len(direct)
            else len(lines)
        )
        value = raw_value.strip()
        nested = [
            line
            for line in lines[line_index + 1 : end]
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if name == "name":
            if value != "Build CI" or nested:
                raise ValueError(
                    "workflow name must be exactly 'Build CI'"
                )
            values[name] = value
        elif name == "on":
            if value or not nested:
                raise ValueError(
                    "workflow on must use the reviewed block mapping"
                )
            values[name] = tuple(
                (
                    len(line) - len(line.lstrip(" ")),
                    line.strip(),
                )
                for line in nested
            )
        elif name == "permissions":
            if value:
                raise ValueError(
                    "workflow permissions must use a block mapping"
                )
            entries = {}
            for line in nested:
                if len(line) - len(line.lstrip(" ")) != 2:
                    raise ValueError(
                        "workflow permissions uses unsupported indentation"
                    )
                match = re.fullmatch(
                    r"  ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
                    line,
                )
                if match is None:
                    raise ValueError(
                        "workflow permissions uses unsupported key syntax"
                    )
                key = match.group(1)
                if key in entries:
                    raise ValueError(
                        f"workflow permissions repeats key {key!r}"
                    )
                entries[key] = match.group(2).strip()
            permissions = tuple(sorted(entries.items()))
            if permissions != (("actions", "read"), ("contents", "read")):
                raise ValueError(
                    "workflow permissions must be exactly actions: read and contents: read"
                )
            values[name] = permissions
        else:
            if value:
                raise ValueError("workflow jobs must use a block mapping")
            values[name] = None
    return tuple((name, values[name]) for name in names)


def _parse_job_mapping(lines, start, end, job_name, field):
    entries = {}
    for line in lines[start:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip(" ")) != 6:
            raise ValueError(
                f"job {job_name!r} {field} uses unsupported indentation"
            )
        match = re.fullmatch(
            r"      ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
            line,
        )
        if match is None:
            raise ValueError(
                f"job {job_name!r} {field} uses unsupported key syntax"
            )
        key = match.group(1)
        if key in entries:
            raise ValueError(f"job {job_name!r} {field} repeats key {key!r}")
        value = match.group(2).strip()
        if not value:
            raise ValueError(f"job {job_name!r} {field}.{key} is empty")
        entries[key] = value
    return tuple(sorted(entries.items()))


def _parse_job_context(job_name, body):
    lines = body.splitlines()
    direct = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent != 4 or line.startswith("    -"):
            continue
        match = re.fullmatch(
            r"    ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
            line,
        )
        if match is None:
            raise ValueError(
                f"job {job_name!r} uses unsupported direct key syntax"
            )
        direct.append((match.group(1), match.group(2), index))
    names = [name for name, _, _ in direct]
    if len(names) != len(set(names)):
        raise ValueError(f"job {job_name!r} contains duplicate direct keys")
    expected_names = {
        "event-identity": [
            "name",
            "runs-on",
            "timeout-minutes",
            "outputs",
            "env",
            "steps",
        ],
        "event-router": [
            "name",
            "if",
            "needs",
            "runs-on",
            "timeout-minutes",
            "outputs",
            "env",
            "steps",
        ],
        "event-classifier": [
            "name",
            "if",
            "needs",
            "runs-on",
            "timeout-minutes",
            "outputs",
            "env",
            "steps",
        ],
        **{
            name: [
                "needs",
                "if",
                "runs-on",
                "timeout-minutes",
                "env",
                "steps",
            ]
            for name in _COMBINED_JOBS
        },
        "summary": [
            "name",
            "if",
            "needs",
            "runs-on",
            "timeout-minutes",
            "env",
            "steps",
        ],
    }[job_name]
    if names != expected_names:
        raise ValueError(
            f"job {job_name!r} direct mapping differs from reviewed keys"
        )

    values = {}
    for field_index, (name, raw_value, line_index) in enumerate(direct):
        end = (
            direct[field_index + 1][2]
            if field_index + 1 < len(direct)
            else len(lines)
        )
        value = raw_value.strip()
        nested = [
            line
            for line in lines[line_index + 1 : end]
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if name == "name":
            expected = (
                job_name
                if job_name in {"event-identity", "event-router"}
                else "summary"
                if job_name == "summary"
                else _DYNAMIC_JOB_NAMES[job_name]
            )
            if value != expected or nested:
                raise ValueError(f"job {job_name!r} name differs")
            values[name] = value
        elif name == "if":
            expected = (
                (_HOST_BUILD_CONDITION if job_name in _METADATA_ADAPTER_JOBS else _WORKER_CONDITION)
                if job_name in _COMBINED_JOBS
                else {
                    "event-router": (
                        "${{ always() && "
                        "needs.event-identity.result == 'success' }}"
                    ),
                    "event-classifier": "always()",
                    "summary": "always()",
                }[job_name]
            )
            if value != expected or nested:
                raise ValueError(f"job {job_name!r} if condition differs")
            values[name] = value
        elif name == "needs":
            expected = (
                "[event-identity]"
                if job_name in {"event-router"}
                else "[event-identity, event-router]"
                if job_name == "event-classifier"
                else "[event-identity, event-classifier]"
                if job_name in _COMBINED_JOBS
                else "[event-identity, event-classifier, host-tests, build, "
                "extended-host-tests, legacy]"
            )
            if value != expected or nested:
                raise ValueError(f"job {job_name!r} needs differs")
            values[name] = value
        elif name == "runs-on":
            if value != "ubuntu-latest" or nested:
                raise ValueError(
                    f"job {job_name!r} runs-on must be ubuntu-latest"
                )
            values[name] = value
        elif name == "timeout-minutes":
            expected = (
                "5"
                if job_name
                in {"event-identity", "event-router", "event-classifier", "summary"}
                else "90"
                if job_name == "build"
                else "60"
            )
            if value != expected or nested:
                raise ValueError(
                    f"job {job_name!r} timeout-minutes must be {expected}"
                )
            values[name] = value
        elif name in {"env", "outputs"}:
            if value:
                raise ValueError(
                    f"job {job_name!r} {name} must use a block mapping"
                )
            mapping = _parse_job_mapping(
                lines,
                line_index + 1,
                end,
                job_name,
                name,
            )
            expected = (
                _EXPECTED_JOB_ENV[job_name]
                if name == "env"
                else _EXPECTED_JOB_OUTPUTS[job_name]
            )
            if mapping != expected:
                raise ValueError(
                    f"job {job_name!r} {name} differs from its reviewed mapping"
                )
            values[name] = mapping
        else:
            if value:
                raise ValueError(
                    f"job {job_name!r} steps must use a block sequence"
                )
            values[name] = None
    return tuple((name, values[name]) for name in names)


def _parse_nested_mapping(lines, start, end, field, step_label):
    entries = {}
    for line in lines[start:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip(" ")) != 8:
            raise ValueError(
                f"{step_label} {field} uses unsupported nested indentation"
            )
        match = re.fullmatch(
            r"        ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
            line,
        )
        if match is None:
            raise ValueError(
                f"{step_label} {field} uses unsupported mapping-key syntax"
            )
        key = match.group(1)
        if key in entries:
            raise ValueError(f"{step_label} {field} repeats key {key!r}")
        value = match.group(2).strip()
        if not value:
            raise ValueError(f"{step_label} {field}.{key} is empty")
        entries[key] = value
    if not entries:
        raise ValueError(f"{step_label} {field} mapping is empty")
    return tuple(sorted(entries.items()))


def _parse_run_value(lines, start, end, value, step_label):
    if value and value != "|":
        commands = (tuple(shlex.split(value)),)
    elif value == "|":
        commands = _parse_bash_run_script_commands(
            _literal_run_script(lines, start, end, value, step_label),
            step_label,
        )
    else:
        raise ValueError(f"{step_label} run field is empty")
    if not commands or any(not command for command in commands):
        raise ValueError(f"{step_label} run command is empty")
    return commands


def _literal_run_script(lines, start, end, value, step_label):
    if value != "|":
        raise ValueError(f"{step_label} must use a literal run block")
    script = []
    for line in lines[start:end]:
        if line and not line.startswith("        "):
            break
        script.append(line[8:] if line else "")
    return "\n".join(script) + "\n"


def _bash_line_state(line, state):
    index = 0
    word_start = state == "normal"
    while index < len(line):
        character = line[index]
        if state == "normal":
            if character in " \t":
                word_start = True
            elif character == "#" and word_start:
                break
            elif character == "'":
                state = "single"
                word_start = False
            elif character == '"':
                state = "double"
                word_start = False
            elif character == "\\":
                if index == len(line) - 1:
                    return state, True
                index += 2
                word_start = False
                continue
            elif character in "&|;":
                if character in "&|" and index + 1 < len(line) and line[index + 1] == character:
                    index += 1
                word_start = True
            else:
                word_start = False
        elif state == "single":
            if character == "'":
                state = "normal"
        else:
            if character == '"':
                state = "normal"
            elif character == "\\":
                if index == len(line) - 1:
                    return state, True
                if line[index + 1] in '$`"\\':
                    index += 2
                    continue
        index += 1
    return state, False


def _parse_bash_run_script_commands(script, step_label):
    state = "normal"
    current = ""
    parsed = []
    for line in script.splitlines():
        current += line
        state, continued = _bash_line_state(line, state)
        if continued:
            current = current[:-1]
            continue
        if state != "normal":
            current += "\n"
            continue
        if current.strip() and not current.lstrip().startswith("#"):
            command = tuple(shlex.split(current))
            if not command:
                raise ValueError(f"{step_label} run command is empty")
            parsed.append(command)
        current = ""
    if current:
        raise ValueError(f"{step_label} has unterminated quoting or continuation")
    if not parsed:
        raise ValueError(f"{step_label} run command is empty")
    return tuple(parsed)


def _parse_step(block, job_name, index):
    lines = block.split("\n")
    first_index = next(
        (
            line_index
            for line_index, line in enumerate(lines)
            if line.strip() and not line.lstrip().startswith("#")
        ),
        None,
    )
    step_label = f"job {job_name!r} step {index}"
    if first_index is None:
        raise ValueError(f"{step_label} is empty")
    first = re.fullmatch(
        r"    -[ \t]+([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
        lines[first_index],
    )
    if first is None:
        raise ValueError(f"{step_label} uses unsupported sequence-key syntax")

    direct = [(first.group(1), first.group(2), first_index)]
    for line_index in range(first_index + 1, len(lines)):
        line = lines[line_index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 6:
            match = re.fullmatch(
                r"      ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
                line,
            )
            if match is None:
                raise ValueError(
                    f"{step_label} uses unsupported direct mapping-key syntax"
                )
            direct.append((match.group(1), match.group(2), line_index))
        elif indent < 8:
            raise ValueError(f"{step_label} uses unsupported step indentation")

    direct_names = [name for name, _, _ in direct]
    if len(direct_names) != len(set(direct_names)):
        raise ValueError(f"{step_label} contains duplicate direct fields")
    unknown = sorted(
        set(direct_names)
        - {"id", "if", "name", "uses", "run", "shell", "env", "with"}
    )
    if unknown:
        raise ValueError(
            f"{step_label} has unsupported direct fields: {', '.join(unknown)}"
        )

    values = {}
    literal_run_script = None
    for field_index, (name, raw_value, line_index) in enumerate(direct):
        end = (
            direct[field_index + 1][2]
            if field_index + 1 < len(direct)
            else len(lines)
        )
        value = raw_value.strip()
        if name in {"env", "with"}:
            if value:
                raise ValueError(
                    f"{step_label} {name} must use a block mapping"
                )
            values[name] = _parse_nested_mapping(
                lines,
                line_index + 1,
                end,
                name,
                step_label,
            )
        elif name == "run":
            values[name] = _parse_run_value(
                lines,
                line_index + 1,
                end,
                value,
                step_label,
            )
            if value == "|":
                literal_run_script = _literal_run_script(
                    lines,
                    line_index + 1,
                    end,
                    value,
                    step_label,
                )
        else:
            scalar = value.strip()
            if name == "uses":
                scalar = scalar.split(" #", 1)[0].strip()
            if not scalar:
                raise ValueError(f"{step_label} {name} is empty")
            if any(
                line.strip() and not line.lstrip().startswith("#")
                for line in lines[line_index + 1 : end]
            ):
                raise ValueError(
                    f"{step_label} {name} cannot contain nested values"
                )
            values[name] = scalar

    name = values.get("name")
    if job_name == "event-identity":
        if (
            index != 0
            or name != "Validate trusted event identities"
            or set(values) != {"id", "name", "run"}
            or values["id"] != "identity"
            or values["run"] != _IDENTITY_COMMANDS
        ):
            raise ValueError(f"{step_label} trusted identity setup differs")
        role = "setup"
    elif job_name == "event-router":
        if index == 0:
            if (
                name != "Require classifier authority"
                or set(values) != {"if", "name", "run"}
                or values["if"]
                != "${{ needs.event-identity.outputs.classifier_available "
                "!= 'true' }}"
                or values["run"]
                != (
                    (
                        "echo",
                        "Build classifier authority is unavailable",
                        ">&2",
                    ),
                    ("exit", "1"),
                )
            ):
                raise ValueError(
                    f"{step_label} unavailable classifier guard differs"
                )
        elif index == 1:
            if (
                name is not None
                or set(values) != {"if", "uses", "with"}
                or values["uses"] != _CHECKOUT_USES
                or values["with"] != _CLASSIFIER_CHECKOUT_WITH
                or values["if"]
                != "${{ needs.event-identity.outputs.classifier_available "
                "== 'true' }}"
            ):
                raise ValueError(f"{step_label} authority checkout differs")
        elif index == 2:
            if (
                name != "Verify classifier authority revision"
                or set(values) != {"if", "name", "run"}
                or values["if"]
                != "${{ needs.event-identity.outputs.classifier_available "
                "== 'true' }}"
                or values["run"] != _CLASSIFIER_VERIFY_COMMANDS
            ):
                raise ValueError(f"{step_label} authority verification differs")
        elif index == 3:
            if (
                name != "Classify Build event"
                or set(values) != {"env", "id", "if", "name", "run"}
                or values["id"] != "classify"
                or values["if"]
                != "${{ needs.event-identity.outputs.classifier_available "
                "== 'true' }}"
                or values["run"] != _CLASSIFIER_COMMANDS
                or values["env"]
                != tuple(
                    sorted(
                        tuple(
                            entry.split(": ", 1)
                            if ": " in entry
                            else (entry[:-1], "")
                        )
                        for entry in _SCRUBBED_PILOT_ENV
                    )
                )
            ):
                raise ValueError(f"{step_label} classifier mapping differs")
        elif index == 4:
            if (
                name != "Bind immutable metadata event"
                or set(values) != {"env", "id", "if", "name", "run"}
                or values["id"] != "metadata-event"
                or values["if"]
                != "${{ steps.classify.outputs.classification == 'metadata-only' }}"
                or values["run"] != _METADATA_EVENT_PRODUCER_COMMANDS
                or values["env"] != _METADATA_EVENT_PRODUCER_ENV
            ):
                raise ValueError(f"{step_label} metadata event producer differs")
        else:
            raise ValueError(f"{step_label} unexpected classifier setup step")
        role = "setup"
    elif job_name == "event-classifier":
        if index == 0:
            if (
                name != "Verify authoritative Build event mode"
                or set(values) != {"name", "run"}
                or values["run"] != _MODE_COMMANDS
            ):
                raise ValueError(f"{step_label} mode verification differs")
        elif index == 1:
            if (
                name != _METADATA_EVENT_MARKER_NAME
                or set(values) != {"env", "if", "name", "run"}
                or values["if"] != _METADATA_EVENT_MARKER_CONDITION
                or values["run"] != _METADATA_EVENT_MARKER_COMMANDS
                or values["env"]
                != (("METADATA_EVENT_DIGEST", _METADATA_EVENT_DIGEST_EXPRESSION),)
            ):
                raise ValueError(f"{step_label} metadata event marker differs")
        else:
            raise ValueError(f"{step_label} unexpected mode setup step")
        role = "setup"
    elif job_name == "build" and name in {"Create and verify patch artifact", "Upload patch-only artifact"}:
        if values.get("if") != _PUBLISHER_CONDITION:
            raise ValueError(f"{step_label} publisher condition differs")
        if name == "Create and verify patch artifact":
            if (
                set(values) != {"name", "if", "env", "run"}
                or values["env"] != (
                    ("BASEROM_URL", "${{ secrets.BASEROM_URL }}"),
                    ("PATCH_ARTIFACT_DIR", "${{ runner.temp }}/patch-artifact"),
                    ("PATCH_COMMIT", "${{ needs.event-identity.outputs.fallback_sha }}"),
                )
                or values["run"] != (("bash", "scripts/modernize/package_ci_patch.sh"),)
            ):
                raise ValueError(f"{step_label} packaging invocation differs")
        elif (
            set(values) != {"name", "if", "uses", "with"}
            or values["uses"] != _UPLOAD_USES or values["with"] != _UPLOAD_WITH
        ):
            raise ValueError(f"{step_label} patch-only upload differs")
        role = "publisher"
    elif name is None:
        expected_index = 1 if job_name in _METADATA_ADAPTER_JOBS else 0
        expected_fields = (
            {"uses", "if", "with"}
            if job_name in _METADATA_ADAPTER_JOBS
            else {"uses", "with"}
        )
        if (
            set(values) != expected_fields
            or values["uses"] != _CHECKOUT_USES
            or values["with"] != _CHECKOUT_WITH
            or index != expected_index
            or (
                job_name in _METADATA_ADAPTER_JOBS
                and values["if"] != _FULL_WORKER_STEP_CONDITION
            )
        ):
            raise ValueError(
                f"{step_label} is an unreviewed unnamed step"
            )
        role = "setup"
    else:
        if name == _METADATA_ADAPTER_STEP_NAME:
            if set(values) != {"name", "if", "env", "run"}:
                raise ValueError(
                    f"{step_label} must contain exactly env, if, name, run"
                )
            if values["if"] != _METADATA_ADAPTER_STEP_CONDITION:
                raise ValueError(f"{step_label} metadata adapter if differs")
            if values["env"] != _METADATA_ADAPTER_ENV:
                raise ValueError(f"{step_label} metadata adapter env differs")
            if literal_run_script is None:
                raise ValueError(f"{step_label} metadata adapter script differs")
            try:
                metadata_adapter_contract.validate_metadata_adapter_script(
                    literal_run_script
                )
            except ValueError as error:
                raise ValueError(f"{step_label} metadata adapter script differs") from error
        elif job_name == "summary" and name == _SUMMARY_STEP_NAME:
            if set(values) != {"name", "run"}:
                raise ValueError(f"{step_label} must contain exactly name, run")
            if literal_run_script is None:
                raise ValueError(f"{step_label} summary script differs")
            try:
                summary_continuity_contract.validate_summary_continuity_script(
                    literal_run_script
                )
            except ValueError as error:
                raise ValueError(f"{step_label} summary script differs") from error
        else:
            expected_fields = (
                {"name", "env", "run"}
                if name
                in {
                    _WORKFLOW_PILOT_TEST_STEP_NAME,
                    _WORKFLOW_PILOT_BASELINE_STEP_NAME,
                    _VALIDATION_OWNERSHIP_TEST_STEP_NAME,
                    _VALIDATION_OWNERSHIP_CHECK_STEP_NAME,
                    _VALIDATION_OWNERSHIP_BASE_STEP_NAME,
                    "Hydrate workflow-pilot Git authority",
                }
                else {"name", "run"}
            )
            if (job_name, name) in _FULL_MODE_ONLY_JOB_STEPS:
                expected_fields.add("if")
            if set(values) != expected_fields:
                raise ValueError(
                    f"{step_label} must contain exactly "
                    f"{', '.join(sorted(expected_fields))}"
                )
            if (job_name, name) in _FULL_MODE_ONLY_JOB_STEPS and values["if"] != _FULL_WORKER_STEP_CONDITION:
                raise ValueError(f"{step_label} full-mode if differs")
            expected_environment = (
                _BASE_VERIFIER_ENV
                if name == _VALIDATION_OWNERSHIP_BASE_STEP_NAME
                else _VALIDATION_OWNERSHIP_ENV
                if name
                in {
                    _VALIDATION_OWNERSHIP_TEST_STEP_NAME,
                    _VALIDATION_OWNERSHIP_CHECK_STEP_NAME,
                }
                else _SCRUBBED_PILOT_ENV
            )
            if "env" in values and values["env"] != tuple(
                sorted(
                    tuple(
                        entry.split(": ", 1)
                        if ": " in entry
                        else (entry[:-1], "")
                    )
                    for entry in expected_environment
                )
            ):
                raise ValueError(
                    f"{step_label} changes its reviewed scrubbed environment"
                )
        if job_name == "summary" and name == _SUMMARY_STEP_NAME:
            role = "summary"
        elif name in _NON_GATE_STEP_NAMES:
            role = "setup"
        elif name == _DOCS_GOVERNANCE_STEP_NAME:
            role = "standalone-gate"
        else:
            role = "gate"
    return (
        role,
        name,
        tuple(sorted(values.items())),
    )


def _parse_job_steps(job_name, body):
    lines = body.splitlines(keepends=True)
    step_headers = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^    -(?:[ \t]|\r?\n?\Z)", line)
    ]
    steps_lines = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n") == "    steps:"
    ]
    if len(steps_lines) != 1:
        raise ValueError(f"job {job_name!r} must have exactly one steps sequence")
    if not step_headers or step_headers[0] <= steps_lines[0]:
        raise ValueError(f"job {job_name!r} steps sequence is empty")
    for line in lines[steps_lines[0] + 1 : step_headers[0]]:
        if line.strip() and not line.lstrip().startswith("#"):
            raise ValueError(
                f"job {job_name!r} has content before its first step"
            )
    blocks = [
        "".join(
            lines[
                start :
                step_headers[index + 1]
                if index + 1 < len(step_headers)
                else len(lines)
            ]
        )
        for index, start in enumerate(step_headers)
    ]
    steps = tuple(
        _parse_step(block, job_name, index)
        for index, block in enumerate(blocks)
    )
    names = [step[1] for step in steps if step[1] is not None]
    if len(names) != len(set(names)):
        raise ValueError(f"job {job_name!r} contains duplicate step names")
    expected_unnamed = (
        0
        if job_name in {"event-identity", "event-classifier", "summary"}
        else 1
    )
    if sum(step[1] is None for step in steps) != expected_unnamed:
        raise ValueError(
            f"job {job_name!r} unnamed step count differs"
        )
    roles = tuple((role, name) for role, name, _ in steps)
    if roles != _EXPECTED_STEP_ROLES[job_name]:
        raise ValueError(
            f"job {job_name!r} step roles and order differ from reviewed setup"
        )
    return steps


def _parse_workflow_structure_text(text):
    workflow_context = _parse_workflow_context(text)
    jobs = _workflow_job_entries(text)
    names = tuple(name for name, _ in jobs)
    if names != _EXPECTED_JOBS:
        raise ValueError(
            "workflow job order must exactly match the reviewed Build jobs"
        )
    blocks = dict(jobs)
    for required in _EXPECTED_JOBS:
        if required not in blocks:
            raise ValueError(f"missing candidate Build job {required!r}")
    structures = tuple(
        (
            name,
            _parse_job_context(name, blocks[name]),
            _parse_job_steps(name, blocks[name]),
        )
        for name in _EXPECTED_JOBS
    )
    return workflow_context, names, structures


def _workflow_gate_contract(structure):
    commands = []
    _, _, jobs = structure
    for job_name, _, steps in jobs:
        for role, step_name, fields in steps:
            if role not in {"gate", "standalone-gate"}:
                continue
            values = dict(fields)
            run_commands = values["run"]
            if step_name == "Build archival lane without a copyrighted baserom":
                run_commands = tuple(
                    command
                    for command in run_commands
                    if command and command[0] == "make"
                )
            for command in run_commands:
                commands.append((job_name, step_name, command))
    return tuple(commands)


def _parse_workflow_gate_contract_text(text):
    return _workflow_gate_contract(_parse_workflow_structure_text(text))


def _read_workflow_gate_contract(repository_root):
    path = os.path.join(repository_root, _BUILD_WORKFLOW_RELATIVE)
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            raise ValueError(
                f"target Build workflow {path!r} must be a regular file"
            )
        if os.path.commonpath((repository_root, os.path.realpath(path))) != (
            repository_root
        ):
            raise ValueError(
                f"target Build workflow {path!r} escapes the checkout"
            )
        if os.path.getsize(path) > 1024 * 1024:
            raise ValueError(f"Build workflow {path!r} exceeds 1 MiB")
        with open(path, "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"cannot read target Build workflow {path!r}: {error}") from error
    return _parse_workflow_structure_text(text)


def _mirrored_workflow_commands(contract):
    return [
        list(argv)
        for _, step_name, argv in contract
        if step_name != _DOCS_GOVERNANCE_STEP_NAME
    ]


def _require_target_gate_equivalence(repository_root):
    source = _read_workflow_gate_contract(_SOURCE_ROOT)
    target = _read_workflow_gate_contract(repository_root)
    if target != source:
        raise ValueError(
            "target Build workflow gate contract differs from the reviewed "
            "source checkout"
        )
    source_commands = _mirrored_workflow_commands(
        _workflow_gate_contract(source)
    )
    reviewed_commands = [gate.command for gate in gates(jobs=2)]
    if source_commands != reviewed_commands:
        raise ValueError(
            "source Build workflow gate contract differs from reviewed gates"
        )


@dataclass
class Gate:
    name: str
    command: List[str]
    applicable_note: str


def gates(jobs: int = 2) -> List[Gate]:
    """Return the ordered gate list, mirroring build.yml's CI steps.

    Kept as data (not hardcoded shell text) so tests can assert on the exact
    command list without actually executing a multi-minute native build.
    """
    return [
        Gate(
            name="gba-playtest-host-suite",
            command=[
                "GBA_PLAYTEST_HOST_ONLY=1",
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tools/gba-playtest/tests",
                "-v",
            ],
            applicable_note=(
                "issue #13 host lane (build.yml `host-tests` job, textually "
                "first): every tools/gba-playtest host test -- scenario/schema "
                "parsing, generators, config, save/migration fixtures, "
                "timeouts, retry policy, deterministic sorted-JSON output, "
                "provenance/diagnostics. Host-only (build-essential + "
                "libmgba-dev, no arm-none-eabi toolchain); never builds/links "
                "the ROM, so it does not overlap the modern-linker gates below. "
                "GBA_PLAYTEST_HOST_ONLY=1 (mirrored verbatim from build.yml, "
                "and applied to THIS child process only) makes that host-only "
                "scope explicit: the ROM-dependent live-integration tests skip "
                "by mode instead of by whether a git-ignored build artifact "
                "happens to exist, so this gate cannot be perturbed by the "
                "modern-linker/item-expansion gates below rewriting those "
                "artifacts. Live coverage stays with those ROM gates"
            ),
        ),
        Gate(
            name="upstream-port-tests",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/upstream_port",
                "-v",
            ],
            applicable_note=(
                "issue #12/#15 host lane (same `host-tests` job): pure-stdlib "
                "upstream-port review tooling tests; re-run this suite for "
                "the current count (classify/scan/drift/state/ref-binding/"
                "output-safety/merge-commit determinism and this "
                "verify.gates() <-> build.yml mirror, which excludes only "
                "the standalone documentation-governance step). Python/stdlib "
                "only, links no C and never rebuilds the ROM"
            ),
        ),
        Gate(
            name="workflow-contract-tests",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/workflows",
                "-p",
                "test_*.py",
                "-v",
            ],
            applicable_note=(
                "fast host lane (same `host-tests` job): stdlib-only parsed "
                "contracts for the consolidated Build CI job graph and "
                "synthetic-input behavior tests of the target checkout's "
                "packaging helper. No game build, compiler, linker, private "
                "base download, or network is invoked"
            ),
        ),
        Gate(
            name="workflow-pilot-reporter-tests",
            command=[
                "$GITHUB_WORKSPACE/build/host-python/bin/python3",
                "-I",
                "scripts/workflow_pilot/isolated_launcher.py",
                "reporter-tests",
            ],
            applicable_note=(
                "issues #176/#216 host lane: workflow-pilot reporter regression "
                "suite, including immutable baseline validation. First create "
                "the pinned owned environment with /usr/bin/python3 -I "
                "scripts/host_python.py create; verification never installs "
                "packages or relies on system/user-site jsonschema"
            ),
        ),
        Gate(
            name="workflow-pilot-baseline",
            command=[
                "/usr/bin/python3",
                "-I",
                "scripts/workflow_pilot/isolated_launcher.py",
                "baseline",
                "--repository-root",
                "$GITHUB_WORKSPACE",
                "--fixture",
                "scripts/workflow_pilot/tests/fixtures/baseline.json",
                "--decisions",
                ".github/workflow-pilot-decisions.json",
                "--expected",
                "scripts/workflow_pilot/tests/fixtures/baseline_expected.json",
                ">",
                "/dev/null",
            ],
            applicable_note=(
                "issue #176 host lane: validates the frozen workflow-pilot "
                "baseline against checked-out Git history"
            ),
        ),
        Gate(
            name="validation-ownership-tests",
            command=[
                "/usr/bin/python3",
                "-I",
                "scripts/validation_ownership/isolated_launcher.py",
                "tests",
            ],
            applicable_note=(
                "issue #180 host lane: isolated fail-closed ownership graph "
                "schema, path-mode, authority, mutation, and lifecycle tests"
            ),
        ),
        Gate(
            name="validation-ownership-check",
            command=[
                "MAKEFLAGS=",
                "MFLAGS=",
                "MAKEOVERRIDES=",
                "GNUMAKEFLAGS=",
                "make",
                "validation-ownership-check",
            ],
            applicable_note=(
                "issue #180 host lane: validates exact Git-tree coverage, "
                "independent probes, and executable lifecycle with Make "
                "execution controls scrubbed before invocation, without "
                "narrowing or executing graph-selected gates"
            ),
        ),
        Gate(
            name="localization-host-suite",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/localization/tests",
                "-p",
                "test_*.py",
            ],
            applicable_note=(
                "issue #18 host lane addition (same `host-tests` job, "
                "textually after the workflow-pilot gates): the "
                "scripts/localization package's own pure-stdlib unit test "
                "suite (schema/pseudo/catalog/generate/CLI/determinism plus "
                "the host-native resolver-behavior and vanilla-isolation "
                "source-audit tests, which self-skip without a host `cc`). "
                "Python/stdlib only; never builds/links the ROM, so it does "
                "not overlap the localization-runtime-*-check scenarios "
                "reached through the modern-linker gates below"
            ),
        ),
        Gate(
            name="game-localization-width-contract",
            command=["make", "game-localization-test"],
            applicable_note=(
                "issue #18 full-game host lane addition: validates the 3,414 "
                "entry JA/ZH catalog, typed UI/scene width coverage, "
                "metrics-aware generated line breaks, and native text "
                "consumer behavior before the target-ROM gates"
            ),
        ),
        Gate(
            name="game-localization-catalog-check",
            command=["python3", "-m", "scripts.localization.game_locales", "check"],
            applicable_note="Build host lane closure check for the committed full-game locale catalog",
        ),
        Gate(
            name="game-localization-crosswalk-check",
            command=[
                "python3",
                "-m",
                "scripts.localization.game_locales",
                "check-crosswalk",
            ],
            applicable_note="Build host lane closure check for full-game source/catalog crosswalk coverage",
        ),
        Gate(
            name="game-localization-raw-closure-check",
            command=[
                "python3",
                "-m",
                "scripts.localization.game_locales",
                "check-raw-closure",
            ],
            applicable_note="Build host lane closure check for unresolved raw full-game locale content",
        ),
        Gate(
            name="artifact-guard-tests",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/artifact_guard_tests",
                "-p",
                "test_*.py",
                "-v",
            ],
            applicable_note="Build ROM lane host tests for immutable candidate artifact hygiene",
        ),
        Gate(
            name="artifact-guard",
            command=["python3", "scripts/artifact_guard.py", "--revision", "HEAD"],
            applicable_note="always applicable: rejects prohibited tracked build artifacts",
        ),
        # build.yml's "Check documentation (issues #7/#17)" step
        # (scripts/docs_check_tests followed by scripts/check_docs.py --check
        # --check-examples) intentionally has no Gate(...) entry here. It is
        # independently required immediately after the artifact guard.
        Gate(
            name="codeql-alerts-test",
            command=["make", "codeql-alerts-test", "CODEQL_REQUIRE_FANALYZER=1"],
            applicable_note=(
                "issue #84 host/static-analysis gate: runs the sanitizer-backed "
                "SIO, runtime-bound, and PNG harnesses, required GCC analyzer "
                "checks in this CI-equivalent mirror, and affected host-tool builds"
            ),
        ),
        Gate(
            name="default-lane-check",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_build_default_lane.py",
                "-v",
            ],
            applicable_note=(
                "issue #15 closure: asserts a bare `make`/`make all` always "
                "resolves to the modern release AAPCS lane"
            ),
        ),
        Gate(
            name="quickstart-legacy-check",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_quickstart.py",
                "-v",
            ],
            applicable_note=(
                "issue #15 closure: asserts quickstart.sh only reaches the "
                "archival agbcc lane via explicit `make legacy`/`make "
                "fireemblem8.gba`, never via env/CLI variable overrides"
            ),
        ),
        Gate(
            name="generated-data-test",
            command=["make", "generated-data-test"],
            applicable_note="applicable when generated-data schema and cross-reference tests exist",
        ),
        Gate(
            name="generated-data-check",
            command=["make", "generated-data-check"],
            applicable_note="applicable when generated_data.mk-tracked tables exist",
        ),
        Gate(
            name="modern-linker-check-debug",
            command=[
                "make",
                "expansion-modern-linker-check",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                f"-j{jobs}",
            ],
            applicable_note=(
                "aggregates the full modern DEBUG ROM/ELF runtime + linker "
                "suite off a single reused object/ELF build -- the runtime "
                "scenarios are covered here and are NOT re-run individually by "
                "verify, so no gate triggers a second/redundant ROM build. "
                "expansion-modern-linker-check depends on -budget-check, "
                "-overlay-audit (-> -relocs), -boot-check, -title-check, "
                "-debugtools-check/-timer-check/-map-check/-tools-check, "
                "-debugtools-prep-check, -debugtools-ch4prep-check, "
                "-newgame-check, -combat-check, -saveload-check (incl. the "
                "suspend/resume save scenario), -savefmt-check (save-format "
                "migration) and -shifted-check, then runs the shift/offset "
                "address scan and the raw-pointer cast audit. Net coverage: "
                "boot, title, new-game, map, prep, combat, save-load, "
                "suspend/resume, debugtools-tools, save migration, budget, "
                "shift/offset, raw-pointer, relocation and cross-overlay"
            ),
        ),
        Gate(
            name="modern-linker-check-release",
            command=[
                "make",
                "expansion-modern-linker-check",
                "MODERN_CONFIG=release",
                "MODERN_ABI=aapcs",
                f"-j{jobs}",
            ],
            applicable_note=(
                "release-config counterpart of the debug gate above: the same "
                "aggregated runtime + linker suite off the reused RELEASE "
                "object/ELF build, additionally exercising the release "
                "debugtools-disabled negative scenarios. Runtime scenarios are "
                "covered here, not re-run individually by verify"
            ),
        ),
        Gate(
            name="modern-itemexpansion-check-debug",
            command=[
                "FE8_ITEM_ID_CAP=0xCE",
                "FE8_EXPANSION_ITEMTEST=1",
                "make",
                "expansion-modern-itemexpansion-check",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "EXPANSION_STARTER_CONTENT=1",
                "EXPANSION_MECHANICS_HOOKS=1",
                "EXPANSION_MECHANICS_SAMPLE=1",
                f"-j{jobs}",
            ],
            applicable_note=(
                "issue #10 acceptance (build.yml ROM `build` job, after the two "
                "default-cap modern-linker gates above -- never the host lane): "
                "boots the real modern debug ROM at an expanded item cap (0xCE, "
                "FE8_EXPANSION_ITEMTEST=1) and runs the item-ID-expansion runtime "
                "probe (expansion-modern-itemexpansion-check). The same single "
                "ROM build also carries the issue #6 bundled-content profile "
                "(EXPANSION_STARTER_CONTENT=1 + hooks + sample), so the authored "
                "content record and its public-registry mechanic are asserted by "
                "this same probe run -- no extra gate and no extra ROM build"
            ),
        ),
        Gate(
            name="modern-itemexpansion-check-release",
            command=[
                "FE8_ITEM_ID_CAP=0xCE",
                "FE8_EXPANSION_ITEMTEST=1",
                "make",
                "expansion-modern-itemexpansion-check",
                "MODERN_CONFIG=release",
                "MODERN_ABI=aapcs",
                "EXPANSION_STARTER_CONTENT=1",
                "EXPANSION_MECHANICS_HOOKS=1",
                "EXPANSION_MECHANICS_SAMPLE=1",
                f"-j{jobs}",
            ],
            applicable_note=(
                "release-config counterpart of the item-expansion debug gate above, "
                "and the final step of build.yml ROM `build` job"
            ),
        ),
        Gate(
            name="modern-all-locales-all-features-profile",
            command=["make", "expansion-modern-map-menu-presentation-check", "-j1"],
            applicable_note=(
                "issue #49 trusted-patch preflight: builds and validates the "
                "isolated release/AAPCS 32 MiB all-production-locales and "
                "maximal-supported-features profile, then runs issue #168's "
                "deterministic map-menu presentation scenario without reading "
                "a base image, creating a patch, or publishing an artifact"
            ),
        ),
        Gate(
            name="cjk-font-gates",
            command=["make", "-f", "cjk_fonts.mk", "cjk-fonts-check", "cjk-fonts-test"],
            applicable_note="combined-gate unique CJK font inventory and codec coverage",
        ),
        Gate(
            name="multilang-codec-gates",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/texttools/tests",
                "-p",
                "test_multilang_codec*.py",
                "-v",
            ],
            applicable_note="combined-gate unique multilang texttools codec coverage",
        ),
        Gate(
            name="expansion-config-gates",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_expansion_config.py",
                "-v",
            ],
            applicable_note="combined-gate unique expansion configuration coverage",
        ),
        Gate(
            name="linker-budget-gates",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/linker_report/tests",
                "-p",
                "test_*.py",
                "-v",
            ],
            applicable_note="combined-gate unique linker-budget coverage",
        ),
        Gate(
            name="legacy-build",
            command=["make", "legacy", "-j2"],
            applicable_note="combined-gate archival no-baserom build",
        ),
        Gate(
            name="legacy-payload-identity",
            command=["make", "-C", "mgfembp", "compare"],
            applicable_note="combined-gate archival payload identity comparison",
        ),
    ]


@dataclass
class GateResult:
    gate: Gate
    ran: bool
    returncode: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.ran and self.returncode == 0


def run_gates(
    repository_root: str,
    jobs: int = 2,
    dry_run: bool = False,
) -> List[GateResult]:
    """Execute (or, if dry_run, just describe) every gate at the exact target
    repository root, in the fixed order returned by `gates()`.

    Stops at the first failing gate (fail-fast, matching CI). Never
    weakens, reorders, or skips a gate. There is intentionally no gate
    *selection* capability here (no `selected`/subset parameter): closure
    evidence for this tool is only ever the full, ordered gate set --
    partial/unknown/zero-gate "success" is a forged closure signal, not a
    real one. (See docs/upstream-porting.md and cli.py -- the public
    `verify` subcommand has no `--gate` flag for the same reason; this
    function has no internal escape hatch a caller could use to bypass
    that either.)
    """
    results: List[GateResult] = []
    repository_root = _resolve_repository_root(repository_root)
    _require_target_gate_equivalence(repository_root)
    for gate in gates(jobs=jobs):
        if dry_run:
            results.append(GateResult(gate=gate, ran=False, returncode=0, stdout="", stderr=""))
            continue
        env_overrides, argv = _split_env_prefix(gate.command)
        argv, stdout = _split_stdout_redirect(argv)
        argv = _expand_workspace(argv, repository_root)
        child_env = None
        if env_overrides:
            child_env = dict(os.environ)
            child_env.update(env_overrides)
        proc = subprocess.run(
            argv,
            cwd=repository_root,
            env=child_env,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
        )
        result = GateResult(
            gate=gate,
            ran=True,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr,
        )
        results.append(result)
        if not result.passed:
            break
    return results
