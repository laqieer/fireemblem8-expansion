"""Orchestrate existing repository gates against the CURRENT TRUSTED WORKTREE
after a maintainer has manually applied a port batch.

WARNING (see docs/upstream-porting.md): this command builds and checks the
repository's *own* current working tree/commit. It never builds, checks out,
or executes the canonical upstream ref/tree. It is a thin, literal mirror of
the four combined workers in `.github/workflows/build.yml`. Before execution,
it parses the selected target checkout's workflow as data and requires exact
semantic equivalence with both the source workflow and this module's reviewed
gate list; target Python is never imported. The event identity, router,
classifier, master-only publisher, and serial summary jobs have no local gate
equivalent. The one DELIBERATE
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
    publisher_command_signatures,
    summary_continuity_contract,
)

publisher_shell_contract = publisher_command_signatures.publisher_shell_contract

# A leading NAME=VALUE token in a gate command is an inline environment
# assignment (POSIX shell semantics), mirrored verbatim from build.yml so
# the gate list stays an argv-identical copy of the workflow. It is applied
# to the child environment, never exec-ed as a program.
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_TRUSTED_GIT = "/usr/bin/git"
_PUBLISHER_AUTHORITY_DECODER = (
    "import base64,sys,zlib;"
    "exec(compile(zlib.decompress(base64.urlsafe_b64decode(sys.argv.pop(1))),"
    "'<publisher-authority-bootstrap>','exec'))"
)
_PUBLISHER_AUTHORITY_PAYLOAD = (
    'eNqVV21z2jgQ_u5fodMn-'
    'wqGpGkmx4TOpJS2uaG4k3AvPY7zGCwHtcb2SDaE4_jvtyvZsklommYmibVaPfu-W'
    'lFKP_KEr4KYcJnGQc5CwpJcbEkm0jxdpDGJUkHyJSNZMY-'
    '5XDJBgiJfpoLnW5dSall8laUiJ8tALmM-'
    'r5aprL4Eq75kMQfYBZNmT26lBQgRSVKFEOS5sFPZItTzx947bzTy_qAOARViltjA'
    '7Qbibu2QS3LWswj8iIBLRm63Mmer4T3P7ZPTV45liRTw-qCEmwX50g3mEv8bgOnJ'
    'zLHYfcYWaHCfGPrpzFqlIWuSXmqSD4tiBb6Rzc2z3kzJ8qNQi0sz0JN2aAsXnn_z'
    '1huPPpP_9Ort9c1wMPFuDGEw8oZ_DgeOhU5epOCTBCQQnhAEdWUWg0XAKVnmTE96'
    'M21zwu4fCFRk_DEYLUP6AT2qZeX5GiTkAiT2S1M13bFK-'
    'EWcSmaXe5pa-6RU1rJYsuYiTdCFQN4pNvr-euIPvPG76_fw77fxhPYI7dLWo833I'
    '-_N1Qh3OyFbd5Iijo9wjb3bz7eT4UfkO2nujz1_dPXXZ__dcDL4cGz3ZvhpdDUY-'
    't6bX8EztwcsH7yPQyW5IowG_tVIKTOoSJ-uJgq3U0jRmfOkh39gcw_ZHbKI3EEcf'
    'zYZ5Og4YrBiVmagKQ1XFI2ATg1kBzAgrWqUWR2exSbsR7SDCB3J4qgThZ1dGYM9r'
    'fkgBv1GHOqNLJASeGW_CmPLaaAv2eJr_10QS9YgBlleCOanRZ4VeX8iCtZMCyhpY'
    '54rGLAmCyysn_qk2zMgx6tX7agjDQyZhyAK3AmlsgpyPwlWWKboWCrYup0FQjLwD'
    '2235TLdtNP5F6jutubuyzwVwR2jjhsy1MN2AFDwzHZKPKzqHZXL4ATCaJ91W-'
    'S06wAaUE5fnSPt_KxFXp46e-'
    'xWTR2wc0HBljBPdaUlu_ehjd3lyxZwbMpvEFyenTZgZ2XicOlrS3we2usgLliZPK'
    'WDBHMjKAY4tljaIqLTbvuXoB3NdrtdLW6_hyQg-jQgKpXH0CZKGaWA-TZn0jbSWh'
    'U932aV0LJRHyhlvlSbbpypPLOj8zidY2ggmiuVxDQXjNH9cxJB8n9NnBcBBJTHOs'
    'ySGg1B9uO4NvRFDJfLkCOKUpMnuY1Uh7wmFy8vLs67F89RJgzy4IgyDaObOmkVQi'
    'g0uCvhWHlDugnb1AXeiHldXBHdNTD3ZIe3Hwp39n93qcsSbSt5oTQq686FgIOJTO'
    'a19eYcFl5tNHqg0gs2jMo_UJmIWuYPBtPHqYFD_ihpVYrKIlbNfq-viiiSDNddtd'
    'wswXsV8bJWtVYCrr1ABFC6cAa33IgnoT2nBH2uzjmGN2dixZMjzOCwVgPpBTlxGv'
    '0QRh1QHTtwAwB44LcuUcMPHq2RLvuV8jgh1acvSRcpNfbrY7Y97eRauXIcwdNTLa'
    '5nVJiZrDeHyrao2A-'
    'MJr2GjkcOmhyoTj_wR682aIaZ1jiKVYZiVa1jbbrqj0v36Ae4N3FHccCy4tTJ8SP'
    '-0CemqjuCknbtn0dFd5BtRu_DvolgkMDwkcZrdQdjWevrvHGhaEK7vWaCR9tyDfV'
    'ZTY_7f3Y73dewyVpHGtGSBeE3bqoKFIaM4dXbfyqgY9cUOFkr96gBVxaU85ggSiC'
    'UdbVR0W0z8cKmkkhJAPnZJNdgzlNXWWN2NlK08qR_eJ9UnHX7hyuXC5n7MU8wVTW'
    '1nHWhXGFsghKddmfVs6DmBmcEIpcbDqP8XN0hhD6pZjOpGzCCrdI1ywSL-'
    'H0NVNcEvhXgBJUL8H0uO5tUfI3idONnPE7zjnkJ-ag7eNCX_A4qBcYheG9sKZwXa'
    'qBQbw9tGFSBnvKhKbH7FkEWLAOWwDAHRcpsdai0Rj_B-oed9VvXtL5MHce9Y7lCM'
    'd1fw8CFj5f9c3r70ZKqSqhCVRaonqCbdlPxpmDduswQcNaFH0z1rv7aP7f2WXyIi'
    'Ml70u2en53R79lkybQQC_YwJ5vOU7OJY8VpEEJAm4-'
    '8KYZPlakagKlKSKWBsqfIoDRZsGrjQxbtqrJEQt_DskLLD1-'
    'OWt2Holx4IUF3sqcoKU0kJkRbFjzHJoEA-FaNK9l9sN6ILvvH93EfnoB3xKFqBzL'
    'Qw9pmbBvHbDju7upZDM57qIyFbRse4SoY5ePPV1OP7-'
    'PDyfdXAU_gu1Xt4Wyl9qpH_JeUJ-p50lKF5RhOQP0Kw70GMgBvPG9yO7m5-'
    'gSPu9-vb6-9MWybbgQvM3bPFjY-MECQrRNFI0MscY_C_G-0dqz_ARTlVeY='
)


def publisher_authority_command(
    repository_root: str,
    revision: str,
    mode: str,
    *arguments: str,
) -> List[str]:
    if mode not in {
        "$AUTHORITY_SUITE",
        "check",
        "upstream-port",
        "workflows",
        "upstream-verify",
    }:
        raise ValueError(f"unsupported publisher authority mode: {mode}")
    return [
        "/usr/bin/python3",
        "-I",
        "-S",
        "-c",
        _PUBLISHER_AUTHORITY_DECODER,
        _PUBLISHER_AUTHORITY_PAYLOAD,
        repository_root,
        revision,
        mode,
        *arguments,
    ]


_CLOSED_ENVIRONMENT = {
    "BASH_ENV": "",
    "DYLD_INSERT_LIBRARIES": "",
    "DYLD_LIBRARY_PATH": "",
    "ENV": "",
    "GIT_CONFIG_COUNT": "0",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "LD_AUDIT": "",
    "LD_LIBRARY_PATH": "",
    "LD_PRELOAD": "",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "PYTHONHOME": "",
    "PYTHONPATH": "",
}


def closed_gate_environment(
    repository_root: str,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        **_CLOSED_ENVIRONMENT,
        "HOME": os.path.abspath(repository_root),
    }
    for name, value in (overrides or {}).items():
        if (
            name.startswith(("BASH_FUNC_", "DYLD_", "GIT_", "LD_", "PYTHON"))
            or name in {"BASH_ENV", "ENV", "HOME", "IFS", "PATH"}
            or _ENV_ASSIGN_RE.fullmatch(f"{name}=") is None
        ):
            raise ValueError(f"unsafe gate environment override: {name}")
        environment[name] = value
    return environment


def publisher_authority_invocation(
    repository_root: str,
    revision: str,
    mode: str,
    *arguments: str,
) -> tuple[List[str], dict[str, str]]:
    return (
        publisher_authority_command(
            repository_root,
            revision,
            mode,
            *arguments,
        ),
        closed_gate_environment(repository_root),
    )


def publisher_authority_ci_launcher_script() -> str:
    prefix = publisher_authority_command("", "", "$AUTHORITY_SUITE")[:6]
    return (
        "import os\n"
        "import resource\n\n"
        f"argv = {prefix!r} + [\n"
        '    os.environ["GITHUB_WORKSPACE"],\n'
        '    os.environ["EXPECTED_AUTHORITY_SHA"],\n'
        '    os.environ["AUTHORITY_SUITE"],\n'
        "]\n"
        f"environment = {_CLOSED_ENVIRONMENT!r}\n"
        'environment["HOME"] = os.environ["GITHUB_WORKSPACE"]\n'
        "descriptor_limit = min(\n"
        "    resource.getrlimit(resource.RLIMIT_NOFILE)[0],\n"
        "    1048576,\n"
        ")\n"
        "os.closerange(3, descriptor_limit)\n"
        "os.execve(argv[0], argv, environment)\n"
    )


def run_publisher_authority(
    repository_root: str,
    revision: str,
    mode: str,
    *arguments: str,
) -> subprocess.CompletedProcess:
    argv, environment = publisher_authority_invocation(
        repository_root,
        revision,
        mode,
        *arguments,
    )
    return subprocess.run(
        argv,
        cwd=repository_root,
        env=environment,
        shell=False,
        close_fds=True,
        capture_output=True,
        text=True,
    )
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
    "patch-release",
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
_PATCH_CHECKOUT_WITH = (
    ("fetch-depth", "0"),
    ("persist-credentials", "false"),
    ("ref", "${{ needs.event-identity.outputs.fallback_sha }}"),
    ("submodules", "recursive"),
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
    "${{ always() && needs.event-identity.result == 'success' && "
    "github.event_name == 'push' && "
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
    "patch-release": (
        ("PATCH_COMMIT", "${{ needs.event-identity.outputs.fallback_sha }}"),
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
                ("PATCH_RELEASE_RESULT", "${{ needs.patch-release.result }}"),
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
_PUBLISHER_AUTHORITY_SUITES = {
    "Verify checked-out revision": "check",
    "Run upstream-port tooling test suite": "upstream-port",
    "Run workflow contract test suite": "workflows",
}
_PUBLISHER_AUTHORITY_ENV = {
    "BASH_ENV": "''",
    "DYLD_INSERT_LIBRARIES": "''",
    "DYLD_LIBRARY_PATH": "''",
    "ENV": "''",
    "EXPECTED_AUTHORITY_SHA": (
        "${{ (needs.event-classifier.result == 'success' && "
        "needs.event-classifier.outputs.expected_head) || "
        "(needs.event-classifier.result == 'failure' && "
        "needs.event-identity.outputs.fallback_sha) || '' }}"
    ),
    "GIT_CONFIG_COUNT": "'0'",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "'1'",
    "GIT_NO_LAZY_FETCH": "'1'",
    "GIT_NO_REPLACE_OBJECTS": "'1'",
    "HOME": "/",
    "LD_AUDIT": "''",
    "LD_LIBRARY_PATH": "''",
    "LD_PRELOAD": "''",
    "PATH": "/usr/bin:/bin",
    "PYTHONHOME": "''",
    "PYTHONPATH": "''",
}
_FULL_MODE_ONLY_JOB_STEPS = {
    ("host-tests", "Verify checked-out revision"),
    ("host-tests", "Hydrate workflow-pilot Git authority"),
    ("host-tests", "Install host-only dependencies (no arm-none-eabi toolchain)"),
    ("host-tests", "Run gba-playtest host test suite"),
    ("host-tests", "Run upstream-port tooling test suite"),
    ("host-tests", "Run workflow contract test suite"),
    ("host-tests", _WORKFLOW_PILOT_TEST_STEP_NAME),
    ("host-tests", _WORKFLOW_PILOT_BASELINE_STEP_NAME),
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
_PRIVATE_STEP_ENV = (
    ("BASH_ENV", "''"),
    ("CDPATH", "''"),
    ("ENV", "''"),
    ("GLOBIGNORE", "''"),
    ("GIT_ALTERNATE_OBJECT_DIRECTORIES", "''"),
    ("GIT_CEILING_DIRECTORIES", "''"),
    ("GIT_COMMON_DIR", "''"),
    ("GIT_CONFIG_COUNT", "'0'"),
    ("GIT_CONFIG_GLOBAL", "/dev/null"),
    ("GIT_CONFIG_KEY_0", "''"),
    ("GIT_CONFIG_NOSYSTEM", "'1'"),
    ("GIT_CONFIG_PARAMETERS", "''"),
    ("GIT_CONFIG_SYSTEM", "/dev/null"),
    ("GIT_CONFIG_VALUE_0", "''"),
    ("GIT_DIR", "''"),
    ("GIT_EXEC_PATH", "''"),
    ("GIT_INDEX_FILE", "''"),
    ("GIT_NAMESPACE", "''"),
    ("GIT_NO_LAZY_FETCH", "'1'"),
    ("GIT_NO_REPLACE_OBJECTS", "'1'"),
    ("GIT_OBJECT_DIRECTORY", "''"),
    ("GIT_REPLACE_REF_BASE", "''"),
    ("GIT_WORK_TREE", "''"),
    ("HOME", "${{ runner.temp }}/patch-runtime"),
    ("LD_LIBRARY_PATH", "''"),
    ("LD_PRELOAD", "''"),
    ("PATH", "/usr/bin:/bin"),
    ("PYTHONPATH", "''"),
    ("SHELLOPTS", "''"),
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
    ),
    "event-classifier": (
        ("setup", "Verify authoritative Build event mode"),
    ),
    "host-tests": (
        ("setup", _METADATA_ADAPTER_STEP_NAME),
        ("setup", None),
        ("setup", "Verify checked-out revision"),
        ("gate", "Run upstream-port tooling test suite"),
        ("gate", "Run workflow contract test suite"),
        ("setup", "Hydrate workflow-pilot Git authority"),
        ("setup", "Install host-only dependencies (no arm-none-eabi toolchain)"),
        ("gate", "Run gba-playtest host test suite"),
        ("gate", _WORKFLOW_PILOT_TEST_STEP_NAME),
        ("gate", _WORKFLOW_PILOT_BASELINE_STEP_NAME),
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
    "patch-release": (
        ("publisher", None),
        ("publisher", "Verify exact candidate and stage trusted producer"),
        ("publisher", "Install trusted isolated-build dependencies"),
        (
            "publisher",
            "Build candidate in isolated namespace and stage public inputs",
        ),
        ("publisher", "Download private base image"),
        ("publisher", "Create and verify patch artifact"),
        ("publisher", "Cleanup and verify private base"),
        ("publisher", "Revalidate patch-only upload"),
        ("publisher", None),
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
    return [
        repository_root if argument == "$GITHUB_WORKSPACE" else argument
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
        "patch-release": [
            "needs",
            "if",
            "runs-on",
            "timeout-minutes",
            "env",
            "steps",
        ],
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
                    "patch-release": _PUBLISHER_CONDITION,
                    "summary": "always()",
                }[job_name]
            )
            if value != expected or nested:
                raise ValueError(f"job {job_name!r} if condition differs")
            values[name] = value
        elif name == "needs":
            expected = (
                "[event-identity]"
                if job_name in {"event-router", "patch-release"}
                else "[event-identity, event-router]"
                if job_name == "event-classifier"
                else "[event-identity, event-classifier]"
                if job_name in _COMBINED_JOBS
                else "[event-identity, event-classifier, host-tests, build, "
                "extended-host-tests, legacy, patch-release]"
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


def _parse_bash_run_script_commands(script, step_label):
    parsed = []
    for logical in publisher_shell_contract.bash_logical_lines(
        script,
        label=step_label,
    ):
        if not logical.strip() or logical.lstrip().startswith("#"):
            continue
        command = tuple(shlex.split(logical))
        if not command:
            raise ValueError(f"{step_label} run command is empty")
        parsed.append(command)
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
    preparsed_name = next(
        (
            raw_value.strip()
            for name, raw_value, _line_index in direct
            if name == "name"
        ),
        None,
    )
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
            if (
                job_name == "host-tests"
                and preparsed_name in _PUBLISHER_AUTHORITY_SUITES
                and value == "|"
            ):
                literal_run_script = _literal_run_script(
                    lines,
                    line_index + 1,
                    end,
                    value,
                    step_label,
                )
                values[name] = ()
            else:
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
    authority_suite = (
        _PUBLISHER_AUTHORITY_SUITES.get(name)
        if job_name == "host-tests"
        else None
    )
    if authority_suite is not None:
        if (
            (literal_run_script or "").rstrip() + "\n"
            != publisher_authority_ci_launcher_script()
            or values.get("shell") != "/usr/bin/python3 -I -S {0}"
            or values.get("env")
            != tuple(
                sorted(
                    {
                        **_PUBLISHER_AUTHORITY_ENV,
                        "AUTHORITY_SUITE": authority_suite,
                    }.items()
                )
            )
        ):
            raise ValueError(
                f"{step_label} publisher authority bootstrap differs"
            )
        if authority_suite != "check":
            values["run"] = (
                tuple(publisher_authority_command(".", "HEAD", authority_suite)),
            )
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
        role = "setup"
    elif job_name == "event-classifier":
        if (
            index != 0
            or name != "Verify authoritative Build event mode"
            or set(values) != {"name", "run"}
            or values["run"] != _MODE_COMMANDS
        ):
            raise ValueError(f"{step_label} mode verification differs")
        role = "setup"
    elif job_name == "patch-release":
        expected_fields = (
            {"uses", "with"}
            if index in {0, 8}
            else {"id", "name", "shell", "env", "run"}
            if index == 4
            else {"name", "shell", "env", "run"}
            if index in {2, 3, 5, 7}
            else {"if", "name", "shell", "env", "run"}
            if index == 6
            else {"name", "env", "run"}
            if index == 1
            else set()
        )
        expected_name = {
            1: "Verify exact candidate and stage trusted producer",
            2: "Install trusted isolated-build dependencies",
            3: "Build candidate in isolated namespace and stage public inputs",
            4: "Download private base image",
            5: "Create and verify patch artifact",
            6: "Cleanup and verify private base",
            7: "Revalidate patch-only upload",
        }.get(index)
        if name != expected_name or set(values) != expected_fields:
            raise ValueError(f"{step_label} publisher mapping differs")
        if index == 0 and (
            values["uses"] != _CHECKOUT_USES
            or values["with"] != _PATCH_CHECKOUT_WITH
        ):
            raise ValueError(f"{step_label} checkout action differs")
        if index == 1 and (
            ("test", "$ACTUAL_SHA", "=", "$PATCH_COMMIT")
            not in values["run"]
            or "/usr/bin/git cat-file -t $PATCH_COMMIT"
            not in " ".join(token for command in values["run"] for token in command)
            or "PREVIOUS_MASTER_SHA"
            in " ".join(token for command in values["run"] for token in command)
            or "sha256sum"
            in " ".join(token for command in values["run"] for token in command)
        ):
            raise ValueError(f"{step_label} trusted producer verification differs")
        if index == 2 and (
            values["shell"]
            != "/bin/bash --noprofile --norc -euo pipefail {0}"
            or values["env"]
            != tuple(
                sorted(
                    _PRIVATE_STEP_ENV
                    + (
                        ("PATCH_RUNTIME_ROOT", "${{ runner.temp }}/patch-runtime"),
                        (
                            "PATCH_WHEELHOUSE",
                            "${{ runner.temp }}/patch-wheelhouse",
                        ),
                    )
                )
            )
            or "/usr/bin/env"
            not in {token for command in values["run"] for token in command}
            or "/usr/bin/python3"
            not in {token for command in values["run"] for token in command}
            or "PIP_CONFIG_FILE=/dev/null"
            not in {token for command in values["run"] for token in command}
        ):
            raise ValueError(f"{step_label} isolated dependency setup differs")
        if index == 3 and (
            values["shell"]
            != "/bin/bash --noprofile --norc -euo pipefail {0}"
            or values["env"]
            != tuple(
                sorted(
                    _PRIVATE_STEP_ENV
                    + (
                        ("BUILDER_ROOT", "${{ runner.temp }}/patch-builder"),
                        ("GITHUB_WORKSPACE_PATH", "${{ github.workspace }}"),
                        ("PATCH_INPUT_ROOT", "${{ runner.temp }}/patch-input"),
                        ("PATCH_RUNTIME_ROOT", "${{ runner.temp }}/patch-runtime"),
                        (
                            "PATCH_WHEELHOUSE",
                            "${{ runner.temp }}/patch-wheelhouse",
                        ),
                    )
                )
            )
            or "/usr/bin/unshare"
            not in {token for command in values["run"] for token in command}
            or "--net"
            not in {token for command in values["run"] for token in command}
            or "--kill-child=KILL"
            not in {token for command in values["run"] for token in command}
            or "/usr/bin/mount"
            not in {token for command in values["run"] for token in command}
            or "--make-rprivate"
            not in {token for command in values["run"] for token in command}
            or "hidepid=2"
            not in " ".join(token for command in values["run"] for token in command)
            or ("/usr/bin/mkdir", "-m", "0700", "/mnt/supervisor")
            not in values["run"]
            or (
                "/usr/bin/mount",
                "--bind",
                "$cgroup_path",
                "/mnt/supervisor/cgroup",
            )
            not in values["run"]
            or "supervisor_cgroup=/mnt/supervisor/cgroup"
            not in {token for command in values["run"] for token in command}
            or ("test", "!", "-r", "/mnt/supervisor") not in values["run"]
            or publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER
            not in block
            or any(
                command
                and command[0].startswith("cgroup_members=")
                and "$cgroup_path/cgroup.procs" in command[0]
                for command in values["run"]
            )
            or "/sys/fs/cgroup/cgroup.controllers"
            not in {token for command in values["run"] for token in command}
            or "$builder_cgroup/cgroup.kill"
            not in {token for command in values["run"] for token in command}
            or "close_inherited_fds()"
            in {token for command in values["run"] for token in command}
            or "/proc/$$/fd"
            in " ".join(token for command in values["run"] for token in command)
            or "candidate-launcher.py"
            not in " ".join(token for command in values["run"] for token in command)
            or "supervisor-launcher.py"
            not in " ".join(token for command in values["run"] for token in command)
            or "os.closerange(3,"
            not in " ".join(token for command in values["run"] for token in command)
            or "os.execve(candidate_argv[0],"
            not in " ".join(token for command in values["run"] for token in command)
            or "MAX_FD = 1_048_576"
            not in " ".join(token for command in values["run"] for token in command)
            or "fcntl.F_GETFD"
            not in " ".join(token for command in values["run"] for token in command)
            or values["run"].count(
                ("exec", "<", "/dev/null", ">", "/dev/null", "2>&1")
            )
            != 2
            or "candidate-output.log"
            in " ".join(token for command in values["run"] for token in command)
            or ("ulimit", "-f", "131072") not in values["run"]
            or publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER
            not in block
            or 'MEMBERSHIP_PATH = "/mnt/supervisor/cgroup/cgroup.procs"'
            not in block
            or "candidate build failed: stage=launch detail=%s exit=%d"
            not in " ".join(token for command in values["run"] for token in command)
            or "candidate build failed: stage=isolated exit=%d"
            not in " ".join(token for command in values["run"] for token in command)
            or "candidate build cleanup failed: process=%d cgroup=%d state=%d primary=%d"
            not in " ".join(token for command in values["run"] for token in command)
            or "< /dev/null > /dev/null 2>&1 &"
            not in " ".join(token for command in values["run"] for token in command)
            or "GITHUB_STEP_SUMMARY-"
            not in " ".join(token for command in values["run"] for token in command)
            or ("builder_cgroup_is_empty",)
            not in values["run"]
            or ("builder_group_is_empty", "$builder_session_id")
            not in values["run"]
            or "builder_session_authenticated=1"
            not in {token for command in values["run"] for token in command}
            or "builder_launch_detail=session-ready"
            not in {token for command in values["run"] for token in command}
            or "os.setsid()"
            not in " ".join(token for command in values["run"] for token in command)
            or "signal.SIGSTOP"
            not in " ".join(token for command in values["run"] for token in command)
            or "libc.prctl(1,"
            not in " ".join(token for command in values["run"] for token in command)
            or '/bin/kill -TERM "$builder_supervisor_pid"'
            in " ".join(token for command in values["run"] for token in command)
            or '/bin/kill -KILL "$builder_supervisor_pid"'
            in " ".join(token for command in values["run"] for token in command)
            or "/usr/bin/setsid --wait /usr/bin/timeout"
            in " ".join(token for command in values["run"] for token in command)
            or ("builder_uid_is_empty", "$builder_uid")
            not in values["run"]
            or ("builder_passwd_entry_absent", "$builder_user")
            not in values["run"]
            or "builder_user_created=0"
            not in {token for command in values["run"] for token in command}
            or "builder_root_owned=0"
            not in {token for command in values["run"] for token in command}
            or ("test", "!", "-e", "$BUILDER_ROOT") not in values["run"]
            or ("test", "!", "-e", "$PATCH_WHEELHOUSE") not in values["run"]
            or "pkill"
            in " ".join(token for command in values["run"] for token in command)
            or "killall"
            in " ".join(token for command in values["run"] for token in command)
            or any(
                token == "$pid"
                for command in values["run"]
                if "/bin/kill" in command
                for token in command
            )
        ):
            raise ValueError(f"{step_label} isolated candidate build differs")
        if index == 3:
            if literal_run_script is None:
                raise ValueError(f"{step_label} patch-release parser script differs")
            try:
                publisher_run_script = publisher_shell_contract.literal_run_script_from_step_block(
                    block,
                    label=step_label,
                )
                publisher_shell_contract.assert_reviewed_patch_release_run_script_identity(
                    publisher_run_script,
                    label=step_label,
                )
                builder_shell = publisher_shell_contract.builder_isolation_shell_source(
                    publisher_run_script,
                    label=step_label,
                )
                publisher_shell_contract.assert_reviewed_builder_isolation_shell_identity(
                    builder_shell,
                    label=step_label,
                )
                publisher_shell_contract.validate_patch_release_parser_heredocs(
                    builder_shell,
                    label=step_label,
                )
                publisher_command_signatures.assert_command_inventory(
                    publisher_run_script
                )
            except ValueError as error:
                raise ValueError(f"{step_label} patch-release parser script differs") from error
            if publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                builder_shell,
                label=step_label,
            ):
                raise ValueError(f"{step_label} isolated candidate build differs")
        if index == 4 and (
            values["id"] != "private-base"
            or values["shell"]
            != "/bin/bash --noprofile --norc -euo pipefail {0}"
            or values["env"]
            != tuple(
                sorted(
                    _PRIVATE_STEP_ENV
                    + (("BASEROM_URL", "${{ secrets.BASEROM_URL }}"),)
                )
            )
            or "/usr/bin/curl"
            not in {token for command in values["run"] for token in command}
            or "/usr/bin/mktemp"
            not in " ".join(token for command in values["run"] for token in command)
            or "scripts."
            in " ".join(token for command in values["run"] for token in command)
        ):
            raise ValueError(f"{step_label} secret download boundary differs")
        if index == 5 and (
            values["shell"]
            != "/bin/bash --noprofile --norc -euo pipefail {0}"
            or values["env"]
            != tuple(
                sorted(
                    _PRIVATE_STEP_ENV
                    + (
                        (
                            "BASE_IMAGE",
                            "${{ steps.private-base.outputs.base_path }}",
                        ),
                        (
                            "PATCH_ARTIFACT_DIR",
                            "${{ runner.temp }}/patch-artifact",
                        ),
                        ("PATCH_INPUT_ROOT", "${{ runner.temp }}/patch-input"),
                        ("PATCH_RUNTIME_ROOT", "${{ runner.temp }}/patch-runtime"),
                        ("PATCH_TOOL_ROOT", "${{ runner.temp }}/patch-tool"),
                    )
                )
            )
            or "/usr/bin/python3"
            not in {token for command in values["run"] for token in command}
            or "-S" not in {token for command in values["run"] for token in command}
            or "/usr/bin/env"
            not in {token for command in values["run"] for token in command}
            or "cleanup_private_base()"
            not in {token for command in values["run"] for token in command}
        ):
            raise ValueError(f"{step_label} audited patch boundary differs")
        if index == 6 and (
            values["if"] != "always()"
            or values["shell"]
            != "/bin/bash --noprofile --norc -euo pipefail {0}"
            or values["env"]
            != tuple(
                sorted(
                    _PRIVATE_STEP_ENV
                    + (
                        (
                            "BASE_IMAGE",
                            "${{ steps.private-base.outputs.base_path }}",
                        ),
                    )
                )
            )
            or "/bin/rm"
            not in {token for command in values["run"] for token in command}
            or ("test", "!", "-e", "$BASE_IMAGE") not in values["run"]
            or ("test", "!", "-e", "$private_dir") not in values["run"]
        ):
            raise ValueError(f"{step_label} private cleanup verification differs")
        if index == 7 and (
            values["shell"]
            != "/bin/bash --noprofile --norc -euo pipefail {0}"
            or values["env"]
            != tuple(
                sorted(
                    _PRIVATE_STEP_ENV
                    + (
                        (
                            "PATCH_ARTIFACT_DIR",
                            "${{ runner.temp }}/patch-artifact",
                        ),
                    )
                )
            )
            or "artifact_names="
            not in " ".join(token for command in values["run"] for token in command)
            or ("test", "!", "-L", "$artifact") not in values["run"]
            or (
                "test",
                "$(/usr/bin/stat -c %F $artifact)",
                "=",
                "regular file",
            )
            not in values["run"]
        ):
            raise ValueError(f"{step_label} upload revalidation differs")
        if index == 8 and (
            values["uses"] != _UPLOAD_USES
            or values["with"] != _UPLOAD_WITH
        ):
            raise ValueError(f"{step_label} upload action differs")
        role = "publisher"
    elif job_name == "summary":
        if (
            name != "Render fail-closed combined Build summary"
            or set(values) != {"name", "run"}
        ):
            raise ValueError(f"{step_label} summary mapping differs")
        role = "summary"
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
            if authority_suite is not None:
                expected_fields = {"name", "env", "run", "shell"}
            elif name in {
                    _WORKFLOW_PILOT_TEST_STEP_NAME,
                    _WORKFLOW_PILOT_BASELINE_STEP_NAME,
                    "Hydrate workflow-pilot Git authority",
            }:
                expected_fields = {"name", "env", "run"}
            else:
                expected_fields = {"name", "run"}
            if (job_name, name) in _FULL_MODE_ONLY_JOB_STEPS:
                expected_fields.add("if")
            if set(values) != expected_fields:
                raise ValueError(
                    f"{step_label} must contain exactly "
                    f"{', '.join(sorted(expected_fields))}"
                )
            if (
                (job_name, name) in _FULL_MODE_ONLY_JOB_STEPS
                and values["if"] != _FULL_WORKER_STEP_CONDITION
            ):
                raise ValueError(f"{step_label} full-mode if differs")
            if (
                "env" in values
                and authority_suite is None
                and values["env"] != tuple(
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
                raise ValueError(
                    f"{step_label} changes its reviewed scrubbed environment"
                )
        if name in _NON_GATE_STEP_NAMES:
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
        2
        if job_name == "patch-release"
        else 0
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
    if os.path.abspath(repository_root) == os.path.abspath(_SOURCE_ROOT):
        text = publisher_command_signatures.authority_file_bytes(
            _BUILD_WORKFLOW_RELATIVE
        ).decode("utf-8")
        return _parse_workflow_structure_text(text)
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
            name="upstream-port-tests",
            command=publisher_authority_command(".", "HEAD", "upstream-port"),
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
            command=publisher_authority_command(".", "HEAD", "workflows"),
            applicable_note=(
                "fast host lane (same `host-tests` job): stdlib-only static "
                "contracts for the consolidated Build CI job graph. No "
                "compiler, ROM, linker, network, or subordinate runtime gate "
                "is invoked"
            ),
        ),
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
                "issue #13 host lane (build.yml `host-tests` job): every "
                "tools/gba-playtest host test -- scenario/schema "
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
            name="workflow-pilot-reporter-tests",
            command=[
                "/usr/bin/python3",
                "-I",
                "scripts/workflow_pilot/isolated_launcher.py",
                "reporter-tests",
            ],
            applicable_note=(
                "issue #176 host lane: pure-stdlib workflow-pilot reporter "
                "regression suite, including immutable baseline validation"
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
        child_env = closed_gate_environment(
            repository_root,
            env_overrides,
        )
        proc = subprocess.run(
            argv,
            cwd=repository_root,
            env=child_env,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            close_fds=True,
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
