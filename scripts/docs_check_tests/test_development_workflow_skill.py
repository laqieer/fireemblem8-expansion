from dataclasses import dataclass
import copy
import json
import os
import posixpath
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
from typing import FrozenSet, Tuple
import unittest

from scripts.check_docs import (
    DocsCheckError,
    is_fence_closing,
    parse_atx_heading,
    parse_fence_opening,
)
from scripts.workflow_pilot import candidate_evidence


ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    ROOT / ".github" / "skills" / "development-workflow" / "SKILL.md"
)
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
PR_TEMPLATE_PATH = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
ISSUE_RESOLUTION_POLICY_PATH = ROOT / "docs" / "issue-resolution-policy.md"
WORKFLOW_PILOT_PATH = ROOT / "docs" / "workflow-pilot.md"
FRAMEWORK_SUPPORT_PATH = ROOT / "docs" / "framework-support.md"
COPILOT_INSTRUCTIONS_PATH = ROOT / ".github" / "copilot-instructions.md"
WORKFLOW_GOVERNANCE_PATH = ROOT / "docs" / "test-cases" / "workflow-governance.md"
BUILD_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build.yml"
PRE_FIX_BUILD_WORKFLOW_PATH = (
    ROOT
    / "scripts"
    / "workflow_pilot"
    / "tests"
    / "fixtures"
    / "pre_fix_build.yml"
)
TEST_CASE_REGISTRY_PATH = ROOT / "docs" / "test-cases" / "registry.json"
MANUAL_HANDOFF_CONTRACT_PATH = ROOT / ".github" / "manual-testing-handoff.json"
MANUAL_HANDOFF_CASE_HEADING = (
    "TC-WORKFLOW-MANUAL-HANDOFF-001: "
    "Surface actionable manual testing and resume automatically"
)
STACKED_CI_CASE_HEADING = (
    "TC-WORKFLOW-STACKED-CI-001: "
    "Run exact Build CI on a genuine stacked PR base"
)
BODY_EDIT_CASE_HEADING = (
    "TC-WORKFLOW-BODY-EDIT-001: Suppress metadata-only Build workers"
)
METADATA_EDIT_RACE_CASE_HEADING = (
    "TC-WORKFLOW-METADATA-EDIT-RACE-001: "
    "Defer metadata edits and reconcile continuity"
)
CANDIDATE_EVIDENCE_MARKER = "<!-- workflow-pilot-candidate-evidence -->"
EVOLVING_PR_BODY_FIELDS = (
    "## Validation commands",
    "Validation results:",
    "Tester actual results:",
    "actual result:",
    "## Review-size preflight",
    "Changed files:",
    "Additions:",
    "Deletions:",
    "Total changed lines:",
    "current SHA:",
    "Candidate SHA:",
    "run ID:",
    "Candidate Build CI",
    "Copilot review ran",
    "security checks passed",
)
MANUAL_HANDOFF_POLICY_HEADING = "Actionable manual-testing handoff"
MANUAL_HANDOFF_SUMMARY_HEADING = "Lifecycle summary"
MANUAL_HANDOFF_QUERY = (
    'repo:laqieer/fireemblem8-expansion is:open assignee:laqieer '
    'label:"waiting-for-manual-testing"'
)
MANUAL_HANDOFF_QUERY_URL = (
    "https://github.com/laqieer/fireemblem8-expansion/issues?"
    "q=repo%3Alaqieer%2Ffireemblem8-expansion+is%3Aopen+"
    "assignee%3Alaqieer+label%3A%22waiting-for-manual-testing%22"
)
_MISSING = object()
MEANINGFUL_TEST_POLICY_HEADING = "Meaningful test evidence"
POLICY_ATOM = re.compile(r"^[A-Za-z]+(?:[ /-][A-Za-z]+)*$")
MEANINGFUL_TEST_POLICY_CLAUSE = re.compile(
    r"^- \*\*(?P<name>[^*:]+):\*\* (?P<status>[a-z-]+)"
    r"(?:\. (?P<detail>.+))?$"
)
MEANINGFUL_TEST_POLICY_ITEM = re.compile(
    r"^  - \*\*(?P<name>[^*:]+):\*\* "
    r"(?P<status>[a-z-]+)(?:\. (?P<detail>.+))?$"
)
PROHIBITED_EVIDENCE_CATEGORIES = (
    "arbitrary strings",
    "comments",
    "helper names",
    "line numbers",
    "ordering",
    "implementation spelling",
)
GIT_TEXT_RATIONALE = "Git-text rationale"
CANONICAL_POLICY_SOURCE = (
    (
        "Evidence standard",
        "required",
        (
            ("behavior", "required", ""),
            ("parsed structural contract", "required", ""),
            ("generated output", "required", ""),
            ("compile/link properties", "required", ""),
            ("runtime state", "required", ""),
        ),
    ),
    (
        "Prohibited evidence",
        "prohibited",
        (
            ("sole-evidence rule", "prohibited", ""),
            *[(category, "prohibited", "") for category in PROHIBITED_EVIDENCE_CATEGORIES],
            (
                GIT_TEXT_RATIONALE,
                "required",
                "git-tracks=source,review,history; "
                "raw-tracked-text=not-behavior-evidence",
            ),
        ),
    ),
    (
        "Static-contract exception",
        "conditional",
        (
            ("source-text assertion", "permitted-only", ""),
            ("exact syntax/spelling/absence", "required", ""),
            ("documented public format", "one-of", ""),
            ("security boundary", "one-of", ""),
            ("generated-file contract", "one-of", ""),
            ("ABI/layout constraint", "one-of", ""),
            ("externally consumed protocol", "one-of", ""),
            ("named contract", "required", ""),
            ("irreplaceable evidence explanation", "required", ""),
        ),
    ),
    (
        "Evidence preference",
        "ordered",
        (
            ("real function positive/adversarial inputs", "first", ""),
            ("parsed JSON/YAML/Make/AST/binary/schema", "second", ""),
            (
                "compile/link typed symbols/sections/resources/generated output",
                "third",
                "",
            ),
            ("deterministic target-ROM/libmGBA behavior", "fourth", ""),
            ("narrowly justified source-text assertion", "last", ""),
        ),
    ),
    (
        "Replacement and mutation controls",
        "required",
        (
            ("accepted requirement", "preserve", ""),
            ("stronger evidence", "required-or-duplicate", ""),
            ("duplicate gate", "no-independent-contract", ""),
            ("phrase-preserving behavior change", "fails", ""),
            ("semantics-preserving spelling/order refactor", "green", ""),
        ),
    ),
)


@dataclass(frozen=True)
class PolicyItem:
    status: str
    detail: Tuple[Tuple[str, FrozenSet[str]], ...] = ()


@dataclass(frozen=True)
class PolicyClause:
    status: str
    items: FrozenSet[Tuple[str, PolicyItem]]


@dataclass(frozen=True)
class MeaningfulTestPolicy:
    clauses: FrozenSet[Tuple[str, PolicyClause]]


@dataclass(frozen=True)
class MarkdownScan:
    raw_lines: Tuple[str, ...]
    visible_lines: Tuple[str, ...]
    comment_line_indexes: FrozenSet[int]


FRAMEWORK_SUPPORT_PATH = ROOT / "docs" / "framework-support.md"
LOCALIZATION_PATH = ROOT / "docs" / "localization.md"
WATCHER_DOC_PATHS = (
    SKILL_PATH,
    CONTRIBUTING_PATH,
    FRAMEWORK_SUPPORT_PATH,
    LOCALIZATION_PATH,
)
COMBINED_BUILD_GUIDANCE_PATHS = (
    SKILL_PATH,
    ROOT / ".github" / "copilot-instructions.md",
    CONTRIBUTING_PATH,
    FRAMEWORK_SUPPORT_PATH,
    LOCALIZATION_PATH,
)
TRUSTED_PUSH_GUIDANCE_PATHS = (
    SKILL_PATH,
    ROOT / ".github" / "copilot-instructions.md",
)
FLEET_COORDINATOR_GUIDANCE_PATHS = TRUSTED_PUSH_GUIDANCE_PATHS
FOCUSED_LOCAL_VALIDATION_PATHS = TRUSTED_PUSH_GUIDANCE_PATHS
BACKGROUND_AGENT_GUIDANCE_PATHS = TRUSTED_PUSH_GUIDANCE_PATHS
RETIRED_MATRIX_SPELLINGS = (
    "full" + " matrix",
    "full" + "-matrix",
    "full" + "_matrix",
)
CANONICAL_WATCHER_COMMAND = (
    "timeout 90m gh run watch <run-id> --interval 30 --exit-status"
)
FENCED_COMMAND_BLOCK = re.compile(
    r"```(?:bash|sh|shell|text)?\n(?P<commands>.*?)```",
    re.DOTALL,
)
RAW_HTML_TEXT_TAGS = frozenset(("pre", "script", "style", "textarea"))
RAW_HTML_BLOCK_TAGS = frozenset(
    """
    address article aside base basefont blockquote body caption center col
    colgroup dd details dialog dir div dl dt fieldset figcaption figure footer
    form frame frameset h1 h2 h3 h4 h5 h6 head header hr html iframe legend li
    link main menu menuitem nav noframes ol optgroup option p param search
    section summary table tbody td tfoot th thead title tr track ul
    """.split()
)
HTML_BLOCK_WHITESPACE = " \t\r\f"


def is_ascii_letter(char):
    return "A" <= char <= "Z" or "a" <= char <= "z"


def raw_html_block_kind(line):
    """Classify a CommonMark raw HTML block start, or return None."""
    indent = 0
    while indent < len(line) and line[indent] == " ":
        indent += 1
    if indent > 3:
        return None

    source = line[indent:]
    if not source.startswith("<"):
        return None
    if source.startswith("<?"):
        return "processing instruction"
    if source.startswith("<![CDATA["):
        return "CDATA section"
    if (
        source.startswith("<!")
        and len(source) > 2
        and "A" <= source[2] <= "Z"
    ):
        return "declaration"

    cursor = 1
    closing = False
    if cursor < len(source) and source[cursor] == "/":
        closing = True
        cursor += 1
    if cursor >= len(source) or not is_ascii_letter(source[cursor]):
        return None

    name_start = cursor
    cursor += 1
    while cursor < len(source):
        char = source[cursor]
        if not (
            is_ascii_letter(char)
            or "0" <= char <= "9"
            or char == "-"
        ):
            break
        cursor += 1

    tag_name = source[name_start:cursor].casefold()
    remainder = source[cursor:]
    has_boundary = (
        not remainder
        or remainder[0] in HTML_BLOCK_WHITESPACE
        or remainder[0] == ">"
        or remainder.startswith("/>")
    )
    if not has_boundary:
        return None

    if not closing and tag_name in RAW_HTML_TEXT_TAGS:
        return f"raw-text tag <{tag_name}>"
    if tag_name in RAW_HTML_BLOCK_TAGS:
        return f"block tag <{tag_name}>"
    if source.rstrip(HTML_BLOCK_WHITESPACE).endswith(">"):
        return f"complete tag <{tag_name}>"
    return None


def markdown_indent(line):
    columns = 0
    cursor = 0
    while cursor < len(line) and line[cursor] in " \t":
        if line[cursor] == " ":
            columns += 1
        else:
            columns += 4 - (columns % 4)
        cursor += 1
    return columns, cursor


def find_html_comment_end(line, cursor, line_number):
    delimiters = (
        (line.find("<!--", cursor), "nested opener"),
        (line.find("--!>", cursor), "malformed closer"),
        (line.find("-->", cursor), "closer"),
    )
    matches = [
        (position, delimiter)
        for position, delimiter in delimiters
        if position >= 0
    ]
    if not matches:
        return None

    position, delimiter = min(matches)
    if delimiter == "closer":
        return position
    if delimiter == "nested opener":
        raise AssertionError(
            f"nested HTML comment opener at line {line_number}"
        )
    raise AssertionError(
        f"malformed HTML comment closer at line {line_number}"
    )


def backtick_run_length(line, cursor):
    end = cursor
    while end < len(line) and line[end] == "`":
        end += 1
    return end - cursor


def find_matching_backtick_run(line, cursor, run_length):
    while cursor < len(line):
        match = line.find("`", cursor)
        if match < 0:
            return None
        candidate_length = backtick_run_length(line, match)
        if candidate_length == run_length:
            return match
        cursor = match + candidate_length
    return None


def find_inline_raw_text_opener(line):
    """Return an inline raw-text tag outside complete backtick code spans."""
    cursor = 0
    while cursor < len(line):
        if line[cursor] == "`":
            run_length = backtick_run_length(line, cursor)
            closing = find_matching_backtick_run(
                line,
                cursor + run_length,
                run_length,
            )
            if closing is None:
                cursor += run_length
            else:
                cursor = closing + run_length
            continue

        if line[cursor] != "<" or cursor + 1 >= len(line):
            cursor += 1
            continue
        if line[cursor + 1] == "/":
            cursor += 2
            continue

        name_start = cursor + 1
        if not is_ascii_letter(line[name_start]):
            cursor += 1
            continue
        name_end = name_start + 1
        while name_end < len(line):
            char = line[name_end]
            if not (
                is_ascii_letter(char)
                or "0" <= char <= "9"
                or char == "-"
            ):
                break
            name_end += 1

        tag_name = line[name_start:name_end].casefold()
        if tag_name not in RAW_HTML_TEXT_TAGS:
            cursor = name_end
            continue
        if (
            name_end < len(line)
            and line[name_end] not in HTML_BLOCK_WHITESPACE + ">/"
        ):
            cursor = name_end
            continue
        if ">" not in line[name_end:]:
            cursor = name_end
            continue
        return tag_name

    return None


def scan_policy_markdown(text):
    """Scan policy Markdown with mutually exclusive block contexts."""
    raw_lines = tuple(text.split("\n"))
    visible_lines = []
    comment_line_indexes = set()
    in_fence = False
    fence_marker = None
    fence_length = 0
    fence_line = None
    in_comment = False
    comment_line = None

    for index, line in enumerate(raw_lines):
        line_number = index + 1

        if in_fence:
            if is_fence_closing(line, fence_marker, fence_length):
                in_fence = False
            visible_lines.append("")
            continue

        if in_comment:
            comment_line_indexes.add(line_number - 1)
            end = find_html_comment_end(line, 0, line_number)
            if end is None:
                visible_lines.append("")
                continue
            if line[end + 3:].strip(" \t\r"):
                raise AssertionError(
                    "HTML comments must occupy standalone lines "
                    f"(line {line_number})"
                )
            in_comment = False
            comment_line = None
            visible_lines.append("")
            continue

        indent, content_start = markdown_indent(line)
        if indent >= 4:
            visible_lines.append(line)
            continue

        opening = parse_fence_opening(line, line_number)
        if opening is not None:
            fence_marker, fence_length = opening
            fence_line = line_number
            in_fence = True
            visible_lines.append("")
            continue

        content = line[content_start:]
        if content.startswith("<!--"):
            comment_line_indexes.add(index)
            end = find_html_comment_end(
                line,
                content_start + 4,
                line_number,
            )
            if end is None:
                in_comment = True
                comment_line = line_number
            elif line[end + 3:].strip(" \t\r"):
                raise AssertionError(
                    "HTML comments must occupy standalone lines "
                    f"(line {line_number})"
                )
            visible_lines.append("")
            continue

        if "<!--" in line:
            raise AssertionError(
                "HTML comments must occupy standalone lines "
                f"(line {line_number})"
            )
        if "--!>" in line:
            raise AssertionError(
                f"malformed HTML comment closer at line {line_number}"
            )
        if "-->" in line:
            raise AssertionError(
                f"stray HTML comment closer at line {line_number}"
            )

        kind = raw_html_block_kind(line)
        if kind is not None:
            raise AssertionError(
                f"raw HTML block ({kind}) starts at line {line_number}"
            )
        inline_raw_text_tag = find_inline_raw_text_opener(line)
        if inline_raw_text_tag is not None:
            raise AssertionError(
                "inline raw-text tag "
                f"<{inline_raw_text_tag}> starts at line {line_number}"
            )

        visible_lines.append(line)

    if in_fence:
        raise DocsCheckError(
            "unterminated fenced code block opened at line "
            f"{fence_line} with {fence_marker * fence_length}"
        )

    if in_comment:
        raise AssertionError(
            f"unterminated HTML comment opened at line {comment_line}"
        )

    return MarkdownScan(
        raw_lines=raw_lines,
        visible_lines=tuple(visible_lines),
        comment_line_indexes=frozenset(comment_line_indexes),
    )


def normalize_policy(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def workflow_job_ids(path):
    text = path.read_text(encoding="utf-8")
    jobs = text.split("\njobs:\n", 1)
    if len(jobs) != 2:
        raise AssertionError(f"{path} lacks a jobs mapping")
    names = re.findall(r"^  ([A-Za-z][A-Za-z0-9_-]*):\s*$", jobs[1], re.MULTILINE)
    if not names or len(names) != len(set(names)):
        raise AssertionError(f"{path} has missing or duplicate job IDs")
    return frozenset(names)


def documented_job_set(text, label):
    pattern = re.compile(
        rf"- \*\*{re.escape(label)}:\*\*\s+\{{(?P<body>.*?)\}}\.",
        re.DOTALL,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one documented job set {label!r}"
        )
    entries = [entry.strip() for entry in matches[0].group("body").split(",")]
    names = []
    for entry in entries:
        match = re.fullmatch(r"`([^`]+)`", entry)
        if match is None:
            raise AssertionError(
                f"documented job set {label!r} has malformed entry {entry!r}"
            )
        names.append(match.group(1))
    if len(names) != len(set(names)):
        raise AssertionError(f"documented job set {label!r} has duplicates")
    return frozenset(names)


def replace_documented_job_set(text, label, names):
    pattern = re.compile(
        rf"- \*\*{re.escape(label)}:\*\*\s+\{{(?P<body>.*?)\}}\.",
        re.DOTALL,
    )
    replacement = (
        f"- **{label}:** "
        + "{"
        + ", ".join(f"`{name}`" for name in names)
        + "}."
    )
    changed, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise AssertionError(f"cannot replace documented job set {label!r}")
    return changed


def raw_markdown_section(text, heading):
    lines = text.splitlines()
    marker = f"## {heading}"
    starts = [index for index, line in enumerate(lines) if line == marker]
    if len(starts) != 1:
        raise AssertionError(f"expected exactly one raw Markdown section {heading!r}")
    start = starts[0]
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def workflow_tester_topology_violations(text):
    scan_policy_markdown(text)
    stacked_case = "\n".join(
        read_markdown_section(text, STACKED_CI_CASE_HEADING)
    )
    body_case = "\n".join(
        read_markdown_section(text, BODY_EDIT_CASE_HEADING)
    )
    current_jobs = workflow_job_ids(BUILD_WORKFLOW_PATH)
    selected_full_pr_jobs = current_jobs - {"patch-release"}
    expected = {
        "stacked-full-pr": selected_full_pr_jobs,
        "current-metadata": frozenset(
            {
                "host-tests",
                "build",
                "extended-host-tests",
                "legacy",
                "event-identity",
                "event-router",
                "metadata-classifier",
                "patch-release",
                "summary",
            }
        ),
        "preserved-pre-fix": workflow_job_ids(PRE_FIX_BUILD_WORKFLOW_PATH),
        "live-opened-full": current_jobs,
        "live-title-metadata": frozenset(
            {
                "host-tests",
                "build",
                "extended-host-tests",
                "legacy",
                "event-identity",
                "event-router",
                "metadata-classifier",
                "patch-release",
                "summary",
            }
        ),
        "live-restore-metadata": frozenset(
            {
                "host-tests",
                "build",
                "extended-host-tests",
                "legacy",
                "event-identity",
                "event-router",
                "metadata-classifier",
                "patch-release",
                "summary",
            }
        ),
    }
    documented = {
        "stacked-full-pr": documented_job_set(
            stacked_case,
            "Parsed full-PR job set",
        ),
        "current-metadata": documented_job_set(
            body_case,
            "Parsed current metadata-only job/check set",
        ),
        "preserved-pre-fix": documented_job_set(
            body_case,
            "Parsed preserved pre-fix body-only job set",
        ),
        "live-opened-full": documented_job_set(
            body_case,
            "Parsed live opened-run job set",
        ),
        "live-title-metadata": documented_job_set(
            body_case,
            "Parsed live title-edit job/check set",
        ),
        "live-restore-metadata": documented_job_set(
            body_case,
            "Parsed live title-restore job/check set",
        ),
    }
    violations = [
        f"{name}-job-set"
        for name in expected
        if documented[name] != expected[name]
    ]
    skipped_names_contract = normalize_policy(
        "live branch protection remains unchanged and therefore still requires canonical host-tests build summary and the independent gitguardian context"
    )
    if skipped_names_contract not in normalize_policy(body_case):
        violations.append("skipped-worker-names-are-semantic")
    forbidden_claims = (
        "same canonical skipped worker names",
        "canonical skipped worker contexts",
        "each skipped with no runner",
        "all four workers are exactly `skipped`",
    )
    normalized_body = normalize_policy(body_case)
    for claim in forbidden_claims:
        if normalize_policy(claim) in normalized_body:
            violations.append("stale-metadata-worker-claim")
    return violations


def live_title_probe_violations(text):
    scan_policy_markdown(text)
    body_case = raw_markdown_section(text, BODY_EDIT_CASE_HEADING)
    commands = " ".join(body_case.replace("\\\n", " ").split())
    normalized = normalize_policy(body_case)
    required_text = {
        "candidate-containing-base": (
            'candidate_branch="${candidate_branch:-agent/issue-177}"',
            'gh pr create --head "$probe_branch" --base "$candidate_branch"',
            'test "$base_ref" = "$candidate_branch"',
            'test "$base_sha" = "$candidate_sha"',
        ),
        "strict-nonempty-descendant": (
            'git diff --cached --quiet && { echo "probe change is empty" >&2; exit 1; }',
            'test "$(git rev-parse "$head_sha^")" = "$candidate_sha"',
            'git diff-tree --no-commit-id --name-only -r "$head_sha"',
            ".github/workflow-probes/issue-177-title-only.json",
        ),
        "title-edit-and-restore": (
            'gh api --method PATCH "repos/{owner}/{repo}/pulls/$pr" '
            '-f title="$probe_title" > /dev/null',
            'gh api --method PATCH "repos/{owner}/{repo}/pulls/$pr" '
            '-f title="$original_title" > /dev/null',
            'test "$title_run_id" != "$opened_run_id"',
            'test "$restore_run_id" != "$title_run_id"',
            "Normalize all three real runs",
        ),
        "three-exact-live-runs": (
            'watch_build_run "$opened_run_id"',
            'watch_build_run "$title_run_id"',
            'watch_build_run "$restore_run_id"',
            'gh run view "$opened_run_id" --json event,headSha,conclusion,url',
            'gh run view "$title_run_id" --json event,headSha,conclusion,url',
            'gh run view "$restore_run_id" --json event,headSha,conclusion,url',
            '"repos/$repo/actions/runs/$opened_run_id/jobs"',
            '"repos/$repo/actions/runs/$title_run_id/jobs"',
            '"repos/$repo/actions/runs/$restore_run_id/jobs"',
        ),
        "required-checks-continuity": (
            'gh pr checks "$pr" --required > "$evidence_dir/title-required-checks.txt"',
            'gh pr checks "$pr" --required > "$evidence_dir/restore-required-checks.txt"',
        ),
        "bounded-exact-run-watcher": (
            "watch_build_run()",
            'timeout 90m gh run watch "$run_id" --interval 30 --exit-status',
            'if [ "$watch_status" -ne 124 ]',
            'gh run view "$run_id" --json status,conclusion',
            "queued|in_progress|waiting)",
            'if [ "$watch_status" -eq 124 ]',
            "second watcher timed out for exact Build run",
            'return "$watch_status"',
            'if [ "$run_conclusion" = success ]',
            "exact Build run completed unsuccessfully",
        ),
        "bounded-unseen-run-discovery": (
            'while [ "$attempt" -lt 60 ]',
            "sleep 5",
            "gh api --method GET --paginate",
            "| jq -s '.'",
            'for page in json.loads(os.environ["RUNS_JSON"])',
            'str(record["id"]) not in prior',
            'record["name"] == "Build CI"',
            'record["event"] == "pull_request"',
            'record["head_branch"] == os.environ["EXPECTED_BRANCH"]',
            'record["head_sha"] == os.environ["EXPECTED_HEAD"]',
            'record["created_at"] >= os.environ["EXPECTED_CREATED_AFTER"]',
            "if len(matches) != 1",
            'opened_run_id="$(discover_build_run',
            'title_run_id="$(discover_build_run',
            'restore_run_id="$(discover_build_run',
        ),
        "supported-gh-pagination": (
            "--jq '.workflow_runs[].id'",
            "| jq -s '.' > \"$evidence_dir/opened-jobs.json\"",
            "| jq -s '.' > \"$evidence_dir/title-jobs.json\"",
            "| jq -s '.' > \"$evidence_dir/restore-jobs.json\"",
        ),
        "fail-fast-trapped-cleanup": (
            "set -euo pipefail",
            "trap finish_probe EXIT",
            "trap 'exit 130' INT",
            "trap 'exit 143' TERM",
            'primary_status="$?"',
            'cleanup_status="$?"',
            'exit "$primary_status"',
            'exit "$cleanup_status"',
            "exit 0",
        ),
        "complete-probe-cleanup": (
            "evidence_dir_created=false",
            "local_ownership_intent=false",
            "push_ownership_intent=false",
            "pr_ownership_intent=false",
            'gh api --method PATCH "repos/{owner}/{repo}/pulls/$pr" '
            '-f title="$original_title"',
            'gh api --method PATCH "repos/$repo/pulls/$cleanup_pr" '
            '-f title="$original_title"',
            'gh pr close "$cleanup_pr"',
            'git push --force-with-lease="refs/heads/$probe_branch:$probe_head_sha"',
            'origin ":refs/heads/$probe_branch"',
            'git -C "$source_root" worktree remove "$probe_worktree"',
            'git -C "$source_root" update-ref -d '
            '"refs/heads/$probe_branch" "$probe_head_sha"',
            '[ "$evidence_dir" = '
            '"$source_root/build/test-artifacts/issue-177-live-probe"',
        ),
        "cleanup-ownership-cas": (
            'remote_sha" != "$probe_head_sha"',
            "remote probe ref changed; preserving it for inspection",
            'local_head" != "$probe_head_sha"',
            'local_ref" != "refs/heads/$probe_branch"',
            'local_dirty"',
            "local probe worktree changed or dirty; preserving it",
            "ambiguous exact validation PRs; preserving all",
            'record["head"]["user"]["login"] == os.environ["EXPECTED_OWNER"]',
            'record["head"]["ref"] == os.environ["EXPECTED_BRANCH"]',
            'record["head"]["sha"] == os.environ["EXPECTED_HEAD_SHA"]',
            'record["base"]["ref"] == os.environ["EXPECTED_BASE"]',
            'record["base"]["sha"] == os.environ["EXPECTED_BASE_SHA"]',
            "validation PR contract changed; preserving it",
            'cleanup_pr_body" != '
            '"Validation-only disposable PR. Never merge."',
        ),
        "raw-job-scan": (
            "for job in raw_jobs",
            "assert api_id not in seen_api_ids",
            "assert name not in seen_names",
            "assert name in stable_by_name",
            "assert job_id not in seen_stable_ids",
            "contexts.append(",
            "assert required_names <= seen_names",
        ),
        "metadata-adapter-runs": (
            'metadata_adapter_ids = {"host-tests", "build"}',
            'assert job["conclusion"] == "success"',
            'assert isinstance(job["runner_name"], str) and job["runner_name"]',
            "assert isinstance(started_at, str)",
        ),
        "metadata-worker-no-start": (
            'metadata_skipped_ids = {"extended-host-tests", "legacy"}',
            'started_at = job["started_at"]',
            'assert job["conclusion"] == "skipped"',
            "assert started_at is None or isinstance(started_at, str)",
        ),
    }
    violations = []
    for violation, fragments in required_text.items():
        if any(fragment not in commands for fragment in fragments):
            violations.append(violation)
    watcher = 'timeout 90m gh run watch "$run_id" --interval 30 --exit-status'
    if (
        commands.count(watcher) != 2
        or commands.count(
            'gh run view "$run_id" --json status,conclusion'
        )
        != 1
        or any(
            commands.count(f'watch_build_run "${variable}"') != 1
            for variable in (
                "opened_run_id",
                "title_run_id",
                "restore_run_id",
            )
        )
    ):
        violations.append("bounded-exact-run-watcher")
    required_policy = {
        "implementation-pr-bootstrap-negative": (
            "Do not edit the implementation PR",
            "base predates event_classifier.py",
            "classifier-bootstrap",
            "not a valid metadata-suppression probe",
            "until the classifier is merged into that base",
        ),
        "validation-only-never-merged": (
            "validation-only",
            "never merged",
            "does not implement an independent issue",
        ),
        "summary-expected-regression-closed": (
            "protected async merge attempt",
            'Required status check "summary" is expected.',
        ),
        "no-empty-or-merge-commit": (
            "Never use git commit --allow-empty",
            "empty commit",
            "merge commit",
        ),
    }
    for violation, fragments in required_policy.items():
        if any(normalize_policy(fragment) not in normalized for fragment in fragments):
            violations.append(violation)
    if "git commit --allow-empty -m" in body_case:
        violations.append("no-empty-or-merge-commit")
    head_assertion = (
        'test "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" '
        '= "$head_sha"'
    )
    base_assertion = (
        'test "$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.sha)" '
        '= "$base_sha"'
    )
    event_assertions = tuple(
        f'test "$(gh run view "${variable}" --json event --jq .event)" '
        '= "pull_request"'
        for variable in ("opened_run_id", "title_run_id", "restore_run_id")
    )
    run_head_assertions = tuple(
        f'test "$(gh run view "${variable}" --json headSha --jq .headSha)" '
        '= "$head_sha"'
        for variable in ("opened_run_id", "title_run_id", "restore_run_id")
    )
    if (
        commands.count(head_assertion) != 3
        or commands.count(base_assertion) != 3
        or any(assertion not in commands for assertion in event_assertions)
        or any(assertion not in commands for assertion in run_head_assertions)
    ):
        violations.append("three-run-head-base-identity")
    restore_title = (
        'gh api --method PATCH "repos/{owner}/{repo}/pulls/$pr" '
        '-f title="$original_title"'
    )
    cleanup_restore_title = (
        'gh api --method PATCH "repos/$repo/pulls/$cleanup_pr" '
        '-f title="$original_title"'
    )
    if (
        commands.count(restore_title) != 1
        or commands.count(cleanup_restore_title) != 1
    ):
        violations.append("title-edit-and-restore")
    if commands.count("candidate_evidence.evaluate_candidate_runs") != 7:
        violations.append("actual-evaluator-assertions")
    if (
        commands.count(
            'assert isinstance(job["runner_name"], str) and job["runner_name"]'
        )
        != 1
    ):
        violations.append("metadata-adapter-runs")
    if commands.count('assert job["runner_name"] is None') != 2:
        violations.append("metadata-worker-no-start")
    evaluator_assertions = (
        "assert opened_result.eligible and opened_result.run_id == opened[\"run_id\"]",
        "assert not title_result.eligible and title_result.mode == \"metadata-only\"",
        "assert full_title_result.eligible",
        "assert full_title_result.run_id == opened[\"run_id\"]",
        "assert not failed_result.eligible",
        "assert all_runs_result.eligible",
        "assert all_runs_result.run_id == opened[\"run_id\"]",
        "assert not restore_result.eligible and restore_result.mode == \"metadata-only\"",
        "assert not failed_restore_result.eligible",
    )
    if any(assertion not in commands for assertion in evaluator_assertions):
        violations.append("actual-evaluator-assertions")
    if re.search(r"--limit\s+1(?:\s|$)", commands):
        violations.append("bounded-unseen-run-discovery")
    if "--slurp" in commands:
        violations.append("unsupported-gh-api-slurp")
    if "git push origin --delete" in commands or "git branch -D" in commands:
        violations.append("cleanup-ownership-cas")
    probe_start = body_case.find("1. From the issue worktree")
    if probe_start < 0:
        violations.append("live-procedure-sequence")
    else:
        numbers = re.findall(
            r"^([0-9]+)\. ",
            body_case[probe_start:],
            re.MULTILINE,
        )
        if numbers[:6] != ["1", "2", "3", "4", "5", "6"]:
            violations.append("live-procedure-sequence")
    trap_index = commands.find("trap finish_probe EXIT")
    push_index = commands.find('git push -u origin "$probe_branch"')
    if trap_index < 0 or push_index < 0 or trap_index > push_index:
        violations.append("fail-fast-trapped-cleanup")
    for variable in ("opened_run_id", "title_run_id", "restore_run_id"):
        discovery = commands.find(f'{variable}="$(discover_build_run')
        watch = commands.find(f'watch_build_run "${variable}"')
        if discovery < 0 or watch < 0 or discovery > watch:
            violations.append("bounded-unseen-run-discovery")
            break
    ownership_pairs = (
        ("local_ownership_intent=true", 'git worktree add -b "$probe_branch"'),
        ("push_ownership_intent=true", 'git push -u origin "$probe_branch"'),
        ("pr_ownership_intent=true", 'gh pr create --head "$probe_branch"'),
    )
    for intent, side_effect in ownership_pairs:
        intent_index = commands.find(intent)
        effect_index = commands.find(side_effect)
        if intent_index < 0 or effect_index < 0 or intent_index > effect_index:
            violations.append("cleanup-ownership-cas")
            break
    return violations


def candidate_evidence_violations(body, comments):
    scan_policy_markdown(body)
    violations = []
    if CANDIDATE_EVIDENCE_MARKER in body:
        violations.append("body-marker")
    folded = body.casefold()
    for field in EVOLVING_PR_BODY_FIELDS:
        if field.casefold() in folded:
            violations.append(f"evolving-body-field:{field}")

    marker_comments = 0
    marker_count = 0
    for comment in comments:
        lines = comment.splitlines()
        exact = sum(
            line.strip() == CANDIDATE_EVIDENCE_MARKER
            for line in lines
        )
        occurrences = comment.count(CANDIDATE_EVIDENCE_MARKER)
        if occurrences != exact:
            violations.append("non-standalone-comment-marker")
        if exact:
            marker_comments += 1
            marker_count += exact
    if marker_comments != 1 or marker_count != 1:
        violations.append("canonical-comment-marker-count")
    return violations


def oracle_evidence_location_violations(text):
    scan_policy_markdown(text)
    normalized = normalize_policy(text)
    stale = (
        "in the PR description",
        "in your PR description",
        "in the pull request description",
    )
    return [
        phrase
        for phrase in stale
        if normalize_policy(phrase) in normalized
    ]


def classifier_bootstrap_contract_violations(text):
    scan_policy_markdown(text)
    normalized = normalize_policy(text)
    violations = []
    bootstrap = normalize_policy(
        "classifier bootstrap may use the trusted default branch when PR "
        "base identity is missing or unusable"
    )
    incomplete_base = (
        r"missing (?:empty )?malformed or "
        r"(?:incoherent|event mismatched) base ref sha with a valid exact "
        r"pr head"
    )
    exact_head_workers = re.compile(
        incomplete_base
        + r" .*?(?:all four workers|the four workers) .*?"
        r"(?:that exact head|that head)"
    )
    failed_summary = re.compile(
        incomplete_base
        + r" .*?(?:fails normal summary|normal summary audits them and fails)"
    )
    no_worker_fallback = normalize_policy(
        "worker checkouts never use a merge/default fallback"
    )
    fallback_requirements = {
        "fallback-lowercase-sha": ("exact lowercase 40-hex SHA",),
        "fallback-pr-coherence": ("refs/pull/<number>/merge",),
        "fallback-pr-number": ("numeric event number",),
        "fallback-push-coherence": (
            "refs/heads/master",
            "event after/github.sha",
        ),
        "validated-worker-fallback": ("Workers consume only that validated",),
        "malformed-fallback-rejection": (
            "Missing",
            "uppercase",
            "short",
            "nonhex",
            "ref-name",
            "ref-number-mismatched",
            "malformed",
            "cross-event",
        ),
        "publisher-revision-verification": (
            "verifies /usr/bin/git rev-parse HEAD immediately after checkout",
        ),
        "publisher-secret-boundary": (
            "BASEROM_URL",
            "All repository/candidate-controlled commands finish before "
            "private download",
            "exact validated after commit",
            "no whole-file source hash pins",
            "No complete target ROM enters an Actions artifact, cache, "
            "release, or log",
            "dedicated unprivileged UID",
            "mount, PID, and network namespaces",
            "no network",
            "BASH_ENV",
            "regular, nonsymlink, single-link",
            "unexpected",
            "exact process group",
            "builder-UID process remains",
            "private mount propagation",
            "recursively read-only",
            "D-Bus",
            "cgroup v2",
            "cgroup.procs",
            "Unavailable mount/cgroup features fail closed",
            "no UID-wide signal",
            "closes inherited file descriptors above 2",
            "stdin/stdout/stderr permanently to private /dev/null",
            "no GitHub workflow command-file paths",
            "Candidate output is never replayed, logged, or uploaded",
            "fixed status text with a numeric exit classification",
            "Arbitrary output volume cannot",
            "tmpfs/ulimit bounds",
            "no output sink exists",
            "root-only 0700 /mnt/supervisor",
            "candidate cannot read, write, execute, or traverse",
            "exact cgroup child",
            "read-only",
            "after /sys is masked",
            "sole member",
            "builder user, tree, wheelhouse, and candidate checkout",
            "unpredictable",
            "mode-restricted",
            "absolute isolated Python",
            "runtime CWD/environment",
            "No candidate command runs while the base exists",
            "success/failure",
            "Cleanup is verified before upload",
            "BPS/manifest/README",
            "immediately before upload",
            "fresh hosted publisher",
            "no candidate-written GITHUB_ENV",
            "background process",
        ),
        "candidate-common-identity": (
            "canonical successful event-identity context",
            "canonical successful event-router context",
            "missing, failed, skipped, renamed, duplicate, or unknown",
        ),
        "classifier-failure-canonical-workers": (
            "validated authoritative pr head",
            "canonical worker names",
            "summary still fails",
        ),
        "base-ref-git-grammar": (
            "1024 UTF-8 bytes",
            "git check-ref-format refs/heads/<base.ref>",
            "--branch shorthand",
            "lone @",
        ),
    }
    if bootstrap not in normalized:
        violations.append("trusted-default-bootstrap")
    if exact_head_workers.search(normalized) is None:
        violations.append("incomplete-base-exact-head-workers")
    if failed_summary.search(normalized) is None:
        violations.append("incomplete-base-summary-failure")
    if no_worker_fallback not in normalized:
        violations.append("worker-merge-default-fallback")
    for violation, phrases in fallback_requirements.items():
        if any(normalize_policy(phrase) not in normalized for phrase in phrases):
            violations.append(violation)
    successful_identity_phrases = (
        "successful full and metadata classifications",
        "successful full/metadata classification",
        "successful full/metadata classifications",
    )
    if not any(
        normalize_policy(phrase) in normalized
        for phrase in successful_identity_phrases
    ):
        violations.append("successful-classification-event-identity")
    return violations


def assert_normalized_policy(test_case, surface, text, concepts, forbidden=()):
    normalized = normalize_policy(text)

    for concept, terms in concepts:
        for term in terms:
            with test_case.subTest(surface=surface, concept=concept, term=term):
                test_case.assertIn(normalize_policy(term), normalized)

    for clause in forbidden:
        with test_case.subTest(surface=surface, forbidden=clause):
            test_case.assertNotIn(normalize_policy(clause), normalized)


def watcher_example_violations(text):
    violations = []
    for block in FENCED_COMMAND_BLOCK.finditer(text):
        for command in block.group("commands").splitlines():
            normalized = " ".join(command.split())
            if re.search(r"\bgh run watch\b", normalized) and normalized != CANONICAL_WATCHER_COMMAND:
                violations.append(normalized)
    return violations


HUMAN_LIFECYCLE_ACTIONS = {
    "Eligibility": (
        ("require material criterion", ("require",), {
            "material", "visual", "audio", "ux", "criterion",
        }),
        ("require unreliable automation", ("require",), {
            "automation", "unreliable", "criterion",
        }),
    ),
    "Activation": (
        ("apply label", ("apply", "add"), {
            "waiting", "for", "manual", "testing", "originating", "issue",
            "open", "implementation", "pr",
        }),
        ("assign tester", ("assign",), {"laqieer", "targets"}),
        ("ping tester", ("ping", "mention", "notify"), {
            "laqieer", "comment",
        }),
    ),
    "Hold": (
        ("block merge", ("block", "prevent"), {
            "merge", "manual", "criterion",
        }),
        ("block closure", ("block", "prevent"), {
            "issue", "closure", "manual", "criterion",
        }),
    ),
    "Completion": (
        ("remove label", ("remove", "clear"), {
            "waiting", "for", "manual", "testing", "originating", "issue",
            "labeled", "implementation", "pr",
        }),
        ("remove assignment", ("remove", "clear"), {
            "temporary", "laqieer", "assignment",
        }),
        ("resume delivery", ("resume", "continue"), {
            "exact", "candidate", "gates", "merge", "automatically",
        }),
    ),
}
HUMAN_POLICY_SOFTENERS = {
    "can",
    "cannot",
    "could",
    "may",
    "might",
    "never",
    "not",
    "optional",
    "optionally",
    "prohibited",
}
NEGATIVE_CONTRACTIONS = {
    "aren't": "are not",
    "can't": "can not",
    "couldn't": "could not",
    "didn't": "did not",
    "doesn't": "does not",
    "don't": "do not",
    "hadn't": "had not",
    "hasn't": "has not",
    "haven't": "have not",
    "isn't": "is not",
    "mustn't": "must not",
    "needn't": "need not",
    "shan't": "shall not",
    "shouldn't": "should not",
    "wasn't": "was not",
    "weren't": "were not",
    "won't": "will not",
    "wouldn't": "would not",
}


def normalize_negative_contractions(text):
    normalized = text.translate(str.maketrans({
        "\u2018": "'",
        "\u2019": "'",
        "\u02bc": "'",
        "\uff07": "'",
    }))
    for contraction, expansion in NEGATIVE_CONTRACTIONS.items():
        normalized = re.sub(
            rf"\b{re.escape(contraction)}\b",
            expansion,
            normalized,
            flags=re.IGNORECASE,
        )
    return re.sub(r"\bcannot\b", "can not", normalized, flags=re.IGNORECASE)


def parse_labeled_summary(text, heading):
    section = "\n".join(read_markdown_section(text, heading))
    fields = {}
    pattern = re.compile(
        r"(?ms)^- \*\*(?P<name>[^*:]+):\*\* "
        r"(?P<value>.*?)(?=^- \*\*|\Z)"
    )
    for match in pattern.finditer(section):
        name = match.group("name")
        if name in fields:
            raise AssertionError(f"duplicate lifecycle field {name!r}")
        fields[name] = " ".join(match.group("value").split())
    return fields


def human_handoff_summary(text, governance=False):
    if governance:
        case = "\n".join(
            read_markdown_section(text, MANUAL_HANDOFF_CASE_HEADING)
        )
        return parse_labeled_summary(case, MANUAL_HANDOFF_SUMMARY_HEADING)
    return parse_labeled_summary(text, MANUAL_HANDOFF_POLICY_HEADING)


def human_handoff_violations(text, governance=False):
    fields = human_handoff_summary(text, governance)
    violations = []
    if set(fields) != set(HUMAN_LIFECYCLE_ACTIONS):
        violations.append("lifecycle fields are incomplete")
    for field, actions in HUMAN_LIFECYCLE_ACTIONS.items():
        clauses = [
            set(normalize_policy(
                normalize_negative_contractions(clause)
            ).split())
            for clause in re.split(r"[.;]+", fields.get(field, ""))
            if clause.strip()
        ]
        for name, verbs, required_words in actions:
            matches = [
                clause
                for clause in clauses
                if required_words <= clause
                and any(verb in clause for verb in verbs)
            ]
            if not matches:
                violations.append(f"{field}: missing {name}")
                continue
            if any(clause & HUMAN_POLICY_SOFTENERS for clause in matches):
                violations.append(f"{field}: reversed or softened {name}")

    activation = fields.get("Activation", "")
    completion = fields.get("Completion", "")
    for exact in (
        "`waiting-for-manual-testing`",
        "`laqieer`",
        "`@laqieer`",
    ):
        if exact not in activation:
            violations.append(f"Activation: missing {exact}")
    for exact in ("`waiting-for-manual-testing`", "`laqieer`"):
        if exact not in completion:
            violations.append(f"Completion: missing {exact}")

    completion_words = normalize_policy(
        normalize_negative_contractions(completion)
    ).split()
    try:
        after = completion_words.index("after")
        accepted = completion_words.index("accepted")
        evidence = completion_words.index("evidence")
        resume = completion_words.index("resume")
    except ValueError:
        violations.append("Completion: missing accepted-evidence gate")
    else:
        if not (after < accepted < resume and after < evidence < resume):
            violations.append("Completion: resume precedes accepted evidence")
    return violations


def replace_whitespace_phrase(text, phrase, replacement):
    pattern = re.compile(
        r"\s+".join(re.escape(part) for part in phrase.split())
    )
    mutated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise AssertionError(f"mutation phrase not found: {phrase}")
    return mutated


EXPECTED_MANUAL_HANDOFF_CONTRACT = {
    "schema": "fe8.manual-testing-handoff.v1",
    "eligibility": {
        "kinds": ["visual", "audio", "ux"],
        "material": True,
        "automation_unreliable": True,
        "deterministic_criteria": False,
    },
    "pre_handoff": {
        "artifact": "non-instrumented",
        "required_roles": ["positive", "control"],
        "required_identity_fields": ["path", "sha256"],
        "render_each": True,
        "inspect_each": True,
        "static_ui": {
            "evidence": "screenshot",
            "source": "emulator",
            "deterministic": True,
        },
        "time_dependent_or_av": {
            "evidence": "av_clip",
            "source": "emulator",
            "synchronized": True,
        },
        "semantic_assertions_primary": True,
    },
    "activation": {
        "required": True,
        "label": "waiting-for-manual-testing",
        "label_description": (
            "Blocked until @laqieer records a specific manual tester result"
        ),
        "assignee": "laqieer",
        "targets": [
            "originating_issue",
            "each_open_implementation_pr",
        ],
        "comment": {
            "required": True,
            "required_per_target": True,
            "mention": "@laqieer",
            "fields": {
                "case_id": {
                    "type": "string",
                    "pattern": "^TC-[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}$",
                },
                "commit": {
                    "type": "string",
                    "format": "git_sha40_lowercase",
                },
                "positive_artifact_path": {
                    "type": "string",
                    "format": "nonempty_path",
                },
                "positive_artifact_sha256": {
                    "type": "string",
                    "format": "sha256_lowercase",
                },
                "control_artifact_path": {
                    "type": "string",
                    "format": "nonempty_path",
                },
                "control_artifact_sha256": {
                    "type": "string",
                    "format": "sha256_lowercase",
                },
                "environment": {
                    "type": "string",
                    "min_length": 1,
                },
                "clean_state": {
                    "type": "string",
                    "min_length": 1,
                },
                "steps": {
                    "type": "array",
                    "format": "numbered_list",
                    "min_items": 1,
                    "items": "nonempty_string",
                },
                "expected": {
                    "type": "string",
                    "min_length": 1,
                },
                "requested_judgment": {
                    "type": "string",
                    "min_length": 1,
                },
                "merge_hold": {
                    "type": "boolean",
                    "const": True,
                },
                "closure_hold": {
                    "type": "boolean",
                    "const": True,
                },
            },
        },
    },
    "hold": {
        "merge": True,
        "issue_closure": True,
    },
    "completion": {
        "post_result": True,
        "post_evidence_link": True,
        "comment": {
            "required": True,
            "required_per_cleanup_target": True,
            "bind_to_activation_fields": ["case_id", "commit"],
            "fields": {
                "case_id": {
                    "type": "string",
                    "pattern": "^TC-[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}$",
                },
                "commit": {
                    "type": "string",
                    "format": "git_sha40_lowercase",
                },
                "actual_result": {
                    "type": "string",
                    "min_length": 1,
                },
                "evidence_url": {
                    "type": "string",
                    "format": "github_evidence_url",
                    "accepted_shapes": [
                        "repository_issue_comment",
                        "repository_pull_comment",
                        "repository_pull_review",
                        "repository_actions_run",
                        "repository_actions_artifact",
                        "repository_blob_at_commit",
                        "github_user_attachment",
                    ],
                },
                "outcome": {
                    "type": "string",
                    "enum": ["accepted", "rejected"],
                },
            },
            "cleanup_allowed_outcome": "accepted",
            "resume_allowed_outcome": "accepted",
            "rejected_outcome": {
                "value": "rejected",
                "retain_waiting_label": True,
                "retain_temporary_assignee": True,
                "retain_merge_hold": True,
                "retain_closure_hold": True,
                "remain_actionable": True,
            },
        },
        "open_pr_head_validation": {
            "source": "github_current_head_sha",
            "field": "current_head_sha",
            "type": "git_sha40_lowercase",
            "must_equal_activation_commit": True,
            "changed_head_requires_fresh_handoff": True,
        },
        "remove_label": "waiting-for-manual-testing",
        "remove_label_from": [
            "originating_issue",
            "each_labeled_implementation_pr",
        ],
        "remove_temporary_assignee": "laqieer",
        "remove_temporary_assignee_from": [
            "originating_issue",
            "each_labeled_implementation_pr",
        ],
        "unless_other_ownership": True,
        "ownership_exception": {
            "flag": "other_ownership",
            "reason_field": "ownership_reason",
            "reason_type": "nonempty_string",
            "required_when_true": True,
            "forbidden_when_false": True,
        },
        "resume_exact_candidate_gates": True,
        "resume_merge": True,
    },
    "queue": {
        "query": MANUAL_HANDOFF_QUERY,
        "url": MANUAL_HANDOFF_QUERY_URL,
        "notify_when_empty": False,
        "live_cardinality": "dynamic",
        "relationship_source": "github_linked_open_implementation_prs",
        "item_schema": {
            "required_fields": [
                "kind",
                "url",
                "state",
                "manual_pending",
            ],
            "kind_enum": ["issue", "pr"],
            "state_enum": [
                "open",
                "closed",
                "superseded",
                "completed",
            ],
            "optional_boolean_fields": [
                "received_label",
                "other_ownership",
                "label_removed",
                "temporary_assignee_removed",
            ],
            "optional_string_fields": [
                "current_head_sha",
                "ownership_reason",
            ],
            "issue_url_pattern": (
                "^https://github\\.com/laqieer/fireemblem8-expansion/"
                "issues/[1-9][0-9]*$"
            ),
            "pr_url_pattern": (
                "^https://github\\.com/laqieer/fireemblem8-expansion/"
                "pull/[1-9][0-9]*$"
            ),
            "pr_origin_url_pattern": (
                "^https://github\\.com/laqieer/fireemblem8-expansion/"
                "issues/[1-9][0-9]*$"
            ),
        },
        "relationship_schema": {
            "required_fields": ["state", "issue_url", "pr_url"],
            "state_enum": ["open", "closed"],
            "issue_url_pattern": (
                "^https://github\\.com/laqieer/fireemblem8-expansion/"
                "issues/[1-9][0-9]*$"
            ),
            "pr_url_pattern": (
                "^https://github\\.com/laqieer/fireemblem8-expansion/"
                "pull/[1-9][0-9]*$"
            ),
        },
        "issue_only_when_no_open_implementation_pr": True,
        "require_every_linked_open_implementation_pr": True,
        "exclude_closed_implementation_prs": True,
    },
}


def compare_contract(actual, expected, path=()):
    location = ".".join(path) or "<root>"
    if type(actual) is not type(expected):
        return [
            f"{location}: expected {type(expected).__name__}, "
            f"got {type(actual).__name__}"
        ]
    if isinstance(expected, dict):
        violations = []
        actual_keys = set(actual)
        expected_keys = set(expected)
        for missing in sorted(expected_keys - actual_keys):
            violations.append(f"{location}: missing {missing}")
        for extra in sorted(actual_keys - expected_keys):
            violations.append(f"{location}: unexpected {extra}")
        for key in sorted(actual_keys & expected_keys):
            violations.extend(
                compare_contract(actual[key], expected[key], path + (key,))
            )
        return violations
    if isinstance(expected, list):
        return compare_string_membership(actual, expected, location)
    if actual != expected:
        return [f"{location}: expected {expected!r}, got {actual!r}"]
    return []


def compare_string_membership(actual, expected, location):
    violations = []
    if any(not isinstance(item, str) for item in actual):
        violations.append(f"{location}: entries must be strings")
        return violations
    if len(actual) != len(set(actual)):
        violations.append(f"{location}: duplicate entries")
    actual_set = set(actual)
    expected_set = set(expected)
    for missing in sorted(expected_set - actual_set):
        violations.append(f"{location}: missing {missing}")
    for extra in sorted(actual_set - expected_set):
        violations.append(f"{location}: unexpected {extra}")
    return violations


def contract_paths(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (key,)
            yield child_path
            yield from contract_paths(child, child_path)


def contract_parent(value, path):
    parent = value
    for key in path[:-1]:
        parent = parent[key]
    return parent, path[-1]


def wrong_contract_value(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return value + "-wrong"
    if isinstance(value, int):
        return value + 1
    if isinstance(value, list):
        return value[:-1]
    raise TypeError(f"unsupported contract leaf: {type(value).__name__}")


def read_manual_handoff_contract():
    return json.loads(MANUAL_HANDOFF_CONTRACT_PATH.read_text(encoding="utf-8"))


GITHUB_EVIDENCE_PATTERNS = {
    "repository_issue_comment": (
        r"https://github\.com/laqieer/fireemblem8-expansion/issues/"
        r"[1-9][0-9]*#issuecomment-[1-9][0-9]*"
    ),
    "repository_pull_comment": (
        r"https://github\.com/laqieer/fireemblem8-expansion/pull/"
        r"[1-9][0-9]*#issuecomment-[1-9][0-9]*"
    ),
    "repository_pull_review": (
        r"https://github\.com/laqieer/fireemblem8-expansion/pull/"
        r"[1-9][0-9]*#discussion_r[1-9][0-9]*"
    ),
    "repository_actions_run": (
        r"https://github\.com/laqieer/fireemblem8-expansion/actions/runs/"
        r"[1-9][0-9]*"
    ),
    "repository_actions_artifact": (
        r"https://github\.com/laqieer/fireemblem8-expansion/actions/runs/"
        r"[1-9][0-9]*/artifacts/[1-9][0-9]*"
    ),
    "repository_blob_at_commit": (
        r"https://github\.com/laqieer/fireemblem8-expansion/blob/"
        r"[0-9a-f]{40}/[^?#\s]+"
    ),
    "github_user_attachment": (
        r"https://github\.com/user-attachments/assets/[0-9A-Za-z-]+"
    ),
}


def github_evidence_shape(value, accepted_shapes):
    if not isinstance(value, str):
        return None
    for shape in accepted_shapes:
        pattern = GITHUB_EVIDENCE_PATTERNS.get(shape)
        if pattern and re.fullmatch(pattern, value):
            return shape
    return None


def validate_comment_field(name, value, specification):
    violations = []
    expected_type = specification["type"]
    if expected_type == "string":
        if not isinstance(value, str):
            return [f"{name}: expected string"]
        if specification.get("min_length") and not value.strip():
            violations.append(f"{name}: expected nonempty text")
        pattern = specification.get("pattern")
        if pattern and re.fullmatch(pattern, value) is None:
            violations.append(f"{name}: invalid pattern")
        if "enum" in specification and value not in specification["enum"]:
            violations.append(f"{name}: invalid enum value")
        format_name = specification.get("format")
        if format_name == "git_sha40_lowercase" and re.fullmatch(
            r"[0-9a-f]{40}",
            value,
        ) is None:
            violations.append(f"{name}: invalid Git SHA")
        if format_name == "sha256_lowercase" and re.fullmatch(
            r"[0-9a-f]{64}",
            value,
        ) is None:
            violations.append(f"{name}: invalid SHA-256")
        if format_name == "nonempty_path" and not value.strip():
            violations.append(f"{name}: expected nonempty path")
        if (
            format_name == "github_evidence_url"
            and github_evidence_shape(
                value,
                specification["accepted_shapes"],
            )
            is None
        ):
            violations.append(f"{name}: invalid GitHub evidence URL")
    elif expected_type == "array":
        if not isinstance(value, list):
            return [f"{name}: expected array"]
        if len(value) < specification.get("min_items", 0):
            violations.append(f"{name}: too few items")
        if specification.get("items") == "nonempty_string" and any(
            not isinstance(item, str) or not item.strip()
            for item in value
        ):
            violations.append(f"{name}: invalid item")
    elif expected_type == "boolean":
        if type(value) is not bool:
            return [f"{name}: expected boolean"]
        if "const" in specification and value is not specification["const"]:
            violations.append(f"{name}: wrong constant")
    else:
        violations.append(f"{name}: unsupported field type")
    return violations


def validate_handoff_comment(contract, comment):
    specification = contract["activation"]["comment"]
    if not isinstance(comment, dict):
        return ["comment must be an object"]
    violations = []
    field_specs = specification["fields"]
    expected_keys = set(field_specs) | {"mention"}
    for extra in sorted(set(comment) - expected_keys):
        violations.append(f"unexpected comment field: {extra}")
    if comment.get("mention") != specification["mention"]:
        violations.append("comment missing exact mention")
    for field, field_spec in field_specs.items():
        if field not in comment:
            violations.append(f"comment missing {field}")
            continue
        violations.extend(
            validate_comment_field(field, comment[field], field_spec)
        )
    positive_path = comment.get("positive_artifact_path")
    control_path = comment.get("control_artifact_path")
    positive_hash = comment.get("positive_artifact_sha256")
    control_hash = comment.get("control_artifact_sha256")
    if all(
        isinstance(value, str)
        for value in (
            positive_path,
            control_path,
            positive_hash,
            control_hash,
        )
    ):
        normalized_positive = posixpath.normpath(
            positive_path.strip().replace("\\", "/")
        )
        normalized_control = posixpath.normpath(
            control_path.strip().replace("\\", "/")
        )
        if (
            normalized_positive,
            positive_hash,
        ) == (
            normalized_control,
            control_hash,
        ):
            violations.append("positive and control artifact identities match")
    return violations


def valid_handoff_comment(contract, steps=None):
    comment = {
        "case_id": "TC-WORKFLOW-MANUAL-HANDOFF-001",
        "commit": "a" * 40,
        "positive_artifact_path": "build/enabled/fireemblem8.gba",
        "positive_artifact_sha256": "b" * 64,
        "control_artifact_path": "build\\control\\fireemblem8.gba",
        "control_artifact_sha256": "c" * 64,
        "environment": "mGBA 0.10.2",
        "clean_state": "Clean boot with default emulator settings",
        "steps": steps or ["Open the artifact."],
        "expected": "The documented presentation is correct.",
        "requested_judgment": "Compare the one named visual criterion.",
        "merge_hold": True,
        "closure_hold": True,
        "mention": contract["activation"]["comment"]["mention"],
    }
    return comment


def validate_completion_comment(contract, comment, activation_comment):
    specification = contract["completion"]["comment"]
    if not isinstance(comment, dict):
        return ["completion comment must be an object"]
    violations = []
    field_specs = specification["fields"]
    for extra in sorted(set(comment) - set(field_specs)):
        violations.append(f"unexpected completion comment field: {extra}")
    for field, field_spec in field_specs.items():
        if field not in comment:
            violations.append(f"completion comment missing {field}")
            continue
        violations.extend(
            validate_comment_field(field, comment[field], field_spec)
        )
    if not isinstance(activation_comment, dict):
        violations.append("missing activation comment for completion binding")
        return violations
    for field in specification["bind_to_activation_fields"]:
        if comment.get(field) != activation_comment.get(field):
            violations.append(f"completion comment mismatches {field}")
    if (
        github_evidence_shape(
            comment.get("evidence_url"),
            field_specs["evidence_url"]["accepted_shapes"],
        )
        == "repository_blob_at_commit"
        and f"/blob/{comment.get('commit')}/" not in comment["evidence_url"]
    ):
        violations.append("completion blob evidence mismatches commit")
    return violations


def valid_completion_comment(contract, activation_comment):
    return {
        "case_id": activation_comment["case_id"],
        "commit": activation_comment["commit"],
        "actual_result": "The requested manual judgment passed.",
        "evidence_url": (
            "https://github.com/user-attachments/assets/"
            "11111111-2222-3333-4444-555555555555"
        ),
        "outcome": "accepted",
    }


def validate_manual_item_shape(contract, item, *, require_pr_origin=False):
    if not isinstance(item, dict):
        return ["item must be an object"]
    schema = contract["queue"]["item_schema"]
    violations = []
    for field in schema["required_fields"]:
        if field not in item:
            violations.append(f"item missing {field}")
    kind = item.get("kind")
    state = item.get("state")
    url = item.get("url")
    if not isinstance(kind, str) or kind not in schema["kind_enum"]:
        violations.append("item has invalid kind")
    if not isinstance(state, str) or state not in schema["state_enum"]:
        violations.append("item has invalid state")
    if type(item.get("manual_pending")) is not bool:
        violations.append("item has invalid manual_pending")
    for field in schema["optional_boolean_fields"]:
        if field in item and type(item[field]) is not bool:
            violations.append(f"item has invalid {field}")
    for field in schema["optional_string_fields"]:
        if field in item and not isinstance(item[field], str):
            violations.append(f"item has invalid {field}")
    ownership = item.get("other_ownership")
    ownership_reason = item.get("ownership_reason")
    if ownership is True:
        if not isinstance(ownership_reason, str) or not ownership_reason.strip():
            violations.append("item is missing ownership_reason")
    elif type(ownership) is bool or ownership is None:
        if "ownership_reason" in item:
            violations.append("item has unexpected ownership_reason")
    if not isinstance(url, str):
        violations.append("item has invalid URL type")
    elif kind in schema["kind_enum"]:
        pattern_key = "issue_url_pattern" if kind == "issue" else "pr_url_pattern"
        if re.fullmatch(schema[pattern_key], url) is None:
            violations.append(f"item has invalid {kind} URL")
    if kind == "pr" and (require_pr_origin or "origin_url" in item):
        if "origin_url" not in item:
            violations.append("PR item missing origin_url")
        elif not isinstance(item["origin_url"], str):
            violations.append("PR item has invalid origin_url type")
        elif re.fullmatch(
            schema["pr_origin_url_pattern"],
            item["origin_url"],
        ) is None:
            violations.append("PR item has malformed origin_url")
    return violations


def validate_relationship_records(contract, relationships):
    schema = contract["queue"]["relationship_schema"]
    required_fields = set(schema["required_fields"])
    violations = []
    valid = []
    seen = set()
    pr_relationships = {}
    for index, relationship in enumerate(relationships):
        label = f"relationship[{index}]"
        if not isinstance(relationship, dict):
            violations.append(f"{label}: expected object")
            continue
        actual_fields = set(relationship)
        for missing in sorted(required_fields - actual_fields):
            violations.append(f"{label}: missing {missing}")
        for extra in sorted(actual_fields - required_fields):
            violations.append(f"{label}: unexpected {extra}")
        if actual_fields != required_fields:
            continue
        state = relationship["state"]
        issue_url = relationship["issue_url"]
        pr_url = relationship["pr_url"]
        if not isinstance(state, str) or state not in schema["state_enum"]:
            violations.append(f"{label}: invalid state")
            continue
        if (
            not isinstance(issue_url, str)
            or re.fullmatch(schema["issue_url_pattern"], issue_url) is None
        ):
            violations.append(f"{label}: invalid issue URL")
            continue
        if (
            not isinstance(pr_url, str)
            or re.fullmatch(schema["pr_url_pattern"], pr_url) is None
        ):
            violations.append(f"{label}: invalid PR URL")
            continue
        identity = (issue_url, pr_url, state)
        if identity in seen:
            violations.append(f"{label}: duplicate relationship")
            continue
        seen.add(identity)
        previous = pr_relationships.get(pr_url)
        if previous is not None and previous != (issue_url, state):
            violations.append(f"{label}: conflicting relationship")
            continue
        pr_relationships[pr_url] = (issue_url, state)
        valid.append(relationship)
    return violations, tuple(valid)


def completed_item_cleanup_violations(contract, item):
    activation = contract["activation"]
    violations = []
    received_label = item.get("received_label")
    other_ownership = item.get("other_ownership", False)
    if (
        "other_ownership" in item
        and type(item["other_ownership"]) is not bool
    ):
        violations.append(f"invalid other_ownership history: {item['url']}")
        other_ownership = False
    if item.get("label") == activation["label"]:
        violations.append(f"stale label: {item['url']}")
    if (
        item.get("assignee") == activation["assignee"]
        and not other_ownership
    ):
        violations.append(f"stale assignee: {item['url']}")
    if type(received_label) is not bool:
        violations.append(f"invalid received_label history: {item['url']}")
        return violations
    if not received_label:
        if item.get("label_removed") is True:
            violations.append(f"impossible label removal: {item['url']}")
        if item.get("temporary_assignee_removed") is True:
            violations.append(
                f"impossible temporary assignee removal: {item['url']}"
            )
        return violations
    if item.get("label_removed") is not True:
        violations.append(f"label removal not recorded: {item['url']}")
    if not other_ownership:
        if item.get("temporary_assignee_removed") is not True:
            violations.append(
                f"temporary assignee removal not recorded: {item['url']}"
            )
    return violations


def validate_open_pr_head(contract, item):
    if item.get("kind") != "pr" or item.get("state") != "open":
        return []
    specification = contract["completion"]["open_pr_head_validation"]
    field = specification["field"]
    current_head = item.get(field)
    if not isinstance(current_head, str):
        return [f"open PR missing typed {field}: {item['url']}"]
    if re.fullmatch(r"[0-9a-f]{40}", current_head) is None:
        return [f"open PR has malformed {field}: {item['url']}"]
    activation_comment = item.get("comment")
    if not isinstance(activation_comment, dict):
        return [f"open PR lacks activation commit: {item['url']}"]
    if (
        specification["must_equal_activation_commit"]
        and current_head != activation_comment.get("commit")
    ):
        return [f"open PR head changed after handoff: {item['url']}"]
    return []


def validate_live_manual_queue(contract, live_items, relationships):
    violations = []
    activation = contract["activation"]
    queue = contract["queue"]
    relationship_violations, valid_relationships = (
        validate_relationship_records(contract, relationships)
    )
    violations.extend(relationship_violations)
    seen_urls = set()
    pending_open_items = []
    for index, item in enumerate(live_items):
        shape_violations = validate_manual_item_shape(
            contract,
            item,
            require_pr_origin=True,
        )
        violations.extend(
            f"item[{index}]: {finding}"
            for finding in shape_violations
        )
        if shape_violations:
            continue
        url = item.get("url")
        if url in seen_urls:
            violations.append(f"duplicate item: {url}")
        seen_urls.add(url)
        state = item["state"]
        pending = item["manual_pending"]
        if state != "open" and pending:
            violations.append(f"non-open item remains pending: {url}")
        if not pending:
            if item.get("label") == activation["label"]:
                violations.append(f"stale label: {url}")
            if (
                item.get("assignee") == activation["assignee"]
                and not item.get("other_ownership", False)
            ):
                violations.append(f"stale assignee: {url}")
            violations.extend(completed_item_cleanup_violations(contract, item))
        if state != "open":
            continue
        if not pending:
            continue
        pending_open_items.append(item)
        if item.get("label") != activation["label"]:
            violations.append(f"wrong label: {url}")
        if item.get("assignee") != activation["assignee"]:
            violations.append(f"wrong assignee: {url}")
        if activation["comment"]["required_per_target"]:
            violations.extend(
                f"{url}: {finding}"
                for finding in validate_handoff_comment(
                    contract,
                    item.get("comment"),
                )
            )

    issues = {
        item["url"]: item
        for item in pending_open_items
        if item.get("kind") == "issue"
    }
    prs = {}
    for item in pending_open_items:
        if item.get("kind") != "pr":
            continue
        origin_url = item["origin_url"]
        prs.setdefault(origin_url, set()).add(item["url"])
        if origin_url not in issues:
            violations.append(f"orphan PR: {item['url']}")

    discovered = {}
    for relationship in valid_relationships:
        if relationship.get("state") != "open":
            continue
        issue_url = relationship.get("issue_url")
        pr_url = relationship.get("pr_url")
        if issue_url in issues and pr_url:
            discovered.setdefault(issue_url, set()).add(pr_url)

    for issue_url in issues:
        expected = discovered.get(issue_url, set())
        actual = prs.get(issue_url, set())
        if (
            not expected
            and actual
            and queue["issue_only_when_no_open_implementation_pr"]
        ):
            violations.append(f"unexpected open PR for {issue_url}")
        if queue["require_every_linked_open_implementation_pr"]:
            for missing in sorted(expected - actual):
                violations.append(f"missing open PR: {missing}")
            for extra in sorted(actual - expected):
                violations.append(f"unlinked open PR: {extra}")
    return violations


def validate_completion_cleanup(
    contract,
    item_history,
    cleanup,
    completion_comments,
):
    completion = contract["completion"]
    violations = []
    if not isinstance(completion_comments, dict):
        return ["completion comments must be an object"]
    valid_history = []
    history_by_url = {}
    for index, item in enumerate(item_history):
        shape_violations = validate_manual_item_shape(contract, item)
        violations.extend(
            f"history[{index}]: {finding}"
            for finding in shape_violations
        )
        if shape_violations:
            continue
        url = item["url"]
        if url in history_by_url:
            duplicate_kind = (
                "duplicate"
                if item == history_by_url[url]
                else "contradictory duplicate"
            )
            violations.append(f"{duplicate_kind} history item: {url}")
            continue
        history_by_url[url] = item
        valid_history.append(item)
    labeled_urls = {
        item["url"]
        for item in valid_history
        if item.get("received_label")
        and item.get("kind") in {"issue", "pr"}
    }
    assignee_urls = {
        item["url"]
        for item in valid_history
        if item.get("received_label")
        and item.get("kind") in {"issue", "pr"}
        and not item.get("other_ownership", False)
    }
    for item in valid_history:
        violations.extend(completed_item_cleanup_violations(contract, item))
        if item.get("received_label") and item.get("manual_pending") is not False:
            violations.append(f"cleanup item remains pending: {item['url']}")
        if item.get("received_label"):
            violations.extend(validate_open_pr_head(contract, item))
            violations.extend(
                f"{item['url']}: activation {finding}"
                for finding in validate_handoff_comment(
                    contract,
                    item.get("comment"),
                )
            )
    expected_comment_urls = labeled_urls
    actual_comment_urls = set(completion_comments)
    for missing in sorted(expected_comment_urls - actual_comment_urls):
        violations.append(f"missing completion comment: {missing}")
    for extra in sorted(actual_comment_urls - expected_comment_urls):
        violations.append(f"unrelated completion comment: {extra}")
    if completion["comment"]["required_per_cleanup_target"]:
        for url in sorted(expected_comment_urls & actual_comment_urls):
            item = history_by_url[url]
            completion_comment = completion_comments[url]
            violations.extend(
                f"{url}: {finding}"
                for finding in validate_completion_comment(
                    contract,
                    completion_comment,
                    item.get("comment"),
                )
            )
            if (
                completion_comment.get("outcome")
                != completion["comment"]["cleanup_allowed_outcome"]
            ):
                violations.append(
                    f"{url}: completion outcome does not permit cleanup"
                )
    if cleanup.get("label") != completion["remove_label"]:
        violations.append("wrong cleanup label")
    if cleanup.get("assignee") != completion["remove_temporary_assignee"]:
        violations.append("wrong cleanup assignee")
    expected_by_field = {
        "remove_label_from": labeled_urls,
        "remove_temporary_assignee_from": assignee_urls,
    }
    for field, expected_urls in expected_by_field.items():
        values = cleanup.get(field)
        if not isinstance(values, list):
            violations.append(f"{field}: expected list")
            continue
        violations.extend(
            compare_string_membership(
                values,
                sorted(expected_urls),
                field,
            )
        )
    return violations


def validate_rejected_manual_state(
    contract,
    item_history,
    rejection_comments,
):
    rejected = contract["completion"]["comment"]["rejected_outcome"]
    activation = contract["activation"]
    violations = []
    seen_urls = set()
    targets = []
    for index, item in enumerate(item_history):
        shape_violations = validate_manual_item_shape(contract, item)
        violations.extend(
            f"rejected history[{index}]: {finding}"
            for finding in shape_violations
        )
        if shape_violations:
            continue
        url = item["url"]
        if url in seen_urls:
            violations.append(f"duplicate rejected history item: {url}")
            continue
        seen_urls.add(url)
        if not item.get("received_label"):
            continue
        targets.append(item)
        if item["state"] != "open":
            violations.append(f"rejected item is not open: {url}")
        if item["manual_pending"] is not True:
            violations.append(f"rejected item is not actionable: {url}")
        if item.get("label") != activation["label"]:
            violations.append(f"rejected item lost waiting label: {url}")
        if item.get("assignee") != activation["assignee"]:
            violations.append(f"rejected item lost tester assignee: {url}")
        if item.get("merge_hold") is not rejected["retain_merge_hold"]:
            violations.append(f"rejected item lost merge hold: {url}")
        if item.get("closure_hold") is not rejected["retain_closure_hold"]:
            violations.append(f"rejected item lost closure hold: {url}")
        if item.get("actionable") is not rejected["remain_actionable"]:
            violations.append(f"rejected item is not actionable: {url}")
        if item.get("label_removed") is True:
            violations.append(f"rejected item removed label early: {url}")
        if item.get("temporary_assignee_removed") is True:
            violations.append(f"rejected item removed assignee early: {url}")

    target_urls = {item["url"] for item in targets}
    comment_urls = set(rejection_comments)
    for missing in sorted(target_urls - comment_urls):
        violations.append(f"missing rejected result: {missing}")
    for extra in sorted(comment_urls - target_urls):
        violations.append(f"unrelated rejected result: {extra}")
    targets_by_url = {item["url"]: item for item in targets}
    for url in sorted(target_urls & comment_urls):
        item = targets_by_url[url]
        comment = rejection_comments[url]
        violations.extend(
            f"{url}: {finding}"
            for finding in validate_completion_comment(
                contract,
                comment,
                item.get("comment"),
            )
        )
        if comment.get("outcome") != rejected["value"]:
            violations.append(f"{url}: result is not rejected")
    return violations


def read_skill():
    text = SKILL_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError("SKILL.md must start with YAML frontmatter")

    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise AssertionError("SKILL.md frontmatter is not terminated") from exc

    metadata = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"invalid frontmatter line: {line}")
        metadata[key.strip()] = value.strip()

    return metadata, text


def read_markdown_section(text, heading):
    scan = scan_policy_markdown(text)

    heading_lines = []
    for index, raw_line in enumerate(scan.raw_lines):
        raw_heading = parse_atx_heading(raw_line)
        visible_heading = parse_atx_heading(scan.visible_lines[index])
        if (
            index in scan.comment_line_indexes
            and raw_heading is not None
            and raw_heading[1] == heading
        ):
            raise AssertionError("policy heading appears inside an HTML comment")

        if index in scan.comment_line_indexes:
            continue
        if (
            visible_heading is not None
            and visible_heading[1] == heading
        ):
            heading_lines.append(
                (index, visible_heading[0])
            )
    if len(heading_lines) != 1:
        raise AssertionError(
            f"expected exactly one Markdown section {heading!r}, "
            f"found {len(heading_lines)}"
        )

    end_line = next(
        (
            index
            for index in range(
                heading_lines[0][0] + 1,
                len(scan.visible_lines),
            )
            if (
                index not in scan.comment_line_indexes
                and (
                    boundary := parse_atx_heading(
                        scan.visible_lines[index]
                    )
                )
                is not None
                and boundary[0] <= heading_lines[0][1]
            )
        ),
        len(scan.visible_lines),
    )
    return list(scan.visible_lines[heading_lines[0][0] + 1:end_line])


def normalize_policy_atom(text):
    """Normalize only accepted policy whitespace and behavior spelling."""
    normalized = " ".join(text.strip().split())
    if not POLICY_ATOM.fullmatch(normalized):
        raise AssertionError(f"invalid policy atom: {text}")
    return re.sub(r"\bbehaviour\b", "behavior", normalized.casefold())


def parse_git_text_rationale_detail(detail):
    assignments = []
    assignment_names = set()
    for assignment in detail.split(";"):
        name, separator, values = assignment.partition("=")
        normalized_name = normalize_policy_atom(name)
        normalized_value_sequence = tuple(
            normalize_policy_atom(value) for value in values.split(",")
        )
        normalized_values = frozenset(normalized_value_sequence)
        if (
            not separator
            or not normalized_name
            or not normalized_values
            or "" in normalized_values
            or len(normalized_value_sequence) != len(normalized_values)
            or normalized_name in assignment_names
        ):
            raise AssertionError(f"invalid Git-text rationale detail: {detail}")
        assignment_names.add(normalized_name)
        assignments.append((normalized_name, normalized_values))
    return tuple(sorted(assignments))


def parse_policy_item_detail(item_name, detail):
    if not detail:
        return ()
    if item_name != normalize_policy_atom(GIT_TEXT_RATIONALE):
        raise AssertionError(f"unexpected detail for policy item: {item_name}")
    return parse_git_text_rationale_detail(detail)


def build_policy_ast(raw_clauses):
    clauses = []
    for clause_name, clause in raw_clauses.items():
        if clause["detail"]:
            raise AssertionError(f"unexpected detail for policy clause: {clause_name}")
        items = []
        for item_name, item in clause["items"].items():
            items.append(
                (
                    item_name,
                    PolicyItem(
                        status=item["status"],
                        detail=parse_policy_item_detail(item_name, item["detail"]),
                    ),
                )
            )
        clauses.append(
            (
                clause_name,
                PolicyClause(
                    status=clause["status"],
                    items=frozenset(items),
                ),
            )
        )
    return MeaningfulTestPolicy(frozenset(clauses))


def build_canonical_policy_ast():
    raw_clauses = {}
    for clause_name, status, source_items in CANONICAL_POLICY_SOURCE:
        normalized_clause_name = normalize_policy_atom(clause_name)
        if normalized_clause_name in raw_clauses:
            raise AssertionError(f"duplicate canonical policy clause: {clause_name}")
        raw_clauses[normalized_clause_name] = {
            "status": normalize_policy_atom(status),
            "detail": "",
            "items": {},
        }
        for item_name, item_status, item_detail in source_items:
            normalized_item_name = normalize_policy_atom(item_name)
            if normalized_item_name in raw_clauses[normalized_clause_name]["items"]:
                raise AssertionError(
                    f"duplicate canonical policy item: {item_name}"
                )
            raw_clauses[normalized_clause_name]["items"][normalized_item_name] = {
                "status": normalize_policy_atom(item_status),
                "detail": item_detail,
            }
    return build_policy_ast(raw_clauses)


CANONICAL_POLICY_AST = build_canonical_policy_ast()


def parse_meaningful_test_policy(text):
    raw_clauses = {}
    current_clause = None
    current_item = None
    for line in read_markdown_section(text, MEANINGFUL_TEST_POLICY_HEADING):
        if not line.strip():
            continue
        if match := MEANINGFUL_TEST_POLICY_CLAUSE.fullmatch(line):
            clause_name = normalize_policy_atom(match.group("name"))
            if clause_name in raw_clauses:
                raise AssertionError(f"duplicate policy clause: {match.group('name')}")
            raw_clauses[clause_name] = {
                "status": normalize_policy_atom(match.group("status")),
                "detail": match.group("detail") or "",
                "items": {},
            }
            current_clause = clause_name
            current_item = None
            continue
        if match := MEANINGFUL_TEST_POLICY_ITEM.fullmatch(line):
            if current_clause is None:
                raise AssertionError(f"orphaned policy item: {match.group('name')}")
            item_name = normalize_policy_atom(match.group("name"))
            items = raw_clauses[current_clause]["items"]
            if item_name in items:
                raise AssertionError(f"duplicate policy item: {match.group('name')}")
            items[item_name] = {
                "status": normalize_policy_atom(match.group("status")),
                "detail": match.group("detail") or "",
            }
            current_item = items[item_name]
            continue
        if current_item is not None and line.startswith("    "):
            if re.match(r"^\s*(?:[-*+] |\d+\. )", line):
                raise AssertionError(f"unexpected policy content: {line.strip()}")
            current_item["detail"] = " ".join(
                (current_item["detail"], line.strip())
            ).strip()
            continue
        raise AssertionError(f"unexpected policy content: {line.strip()}")

    policy = build_policy_ast(raw_clauses)
    if policy != CANONICAL_POLICY_AST:
        raise AssertionError("policy AST differs from the canonical policy schema")
    return policy


def render_meaningful_test_policy(clause_order=None, item_orders=None):
    records = {
        clause_name: (status, items)
        for clause_name, status, items in CANONICAL_POLICY_SOURCE
    }
    lines = [f"## {MEANINGFUL_TEST_POLICY_HEADING}", ""]
    for clause_name in clause_order or records:
        status, items = records[clause_name]
        lines.append(f"- **{clause_name}:** {status}")
        item_records = {
            item_name: (item_status, detail)
            for item_name, item_status, detail in items
        }
        for item_name in (item_orders or {}).get(clause_name, item_records):
            item_status, detail = item_records[item_name]
            suffix = f". {detail}" if detail else ""
            lines.append(f"  - **{item_name}:** {item_status}{suffix}")
    return "\n".join([*lines, ""])


class DevelopmentWorkflowSkillTests(unittest.TestCase):
    def assert_meaningful_test_policy(self, text):
        return parse_meaningful_test_policy(text)

    def test_frontmatter_matches_project_skill_directory(self):
        metadata, _ = read_skill()

        self.assertEqual(metadata["name"], SKILL_PATH.parent.name)
        self.assertIn("feature requests", metadata["description"])
        self.assertIn("bug fixes", metadata["description"])
        self.assertIn("merge", metadata["description"])
        self.assertIn("validate", metadata["description"])

    def test_workflow_contract_is_present(self):
        _, text = read_skill()
        required_contract = (
            "Framework capability",
            "Optional reusable module or reference implementation",
            "Project-specific content or ruleset",
            "Needs design",
            "this skill implements Discussion #30",
            "Bug fixes do not need a feature gate by default.",
            "add a regression test that demonstrates the original failure",
            "Verify an ARM debugger is available",
            "Use ARM GDB when register, stack, symbol, memory, or control-flow state",
            "Identify dependencies and conflicts between the request",
            "Final docs must name all dependencies and conflicts",
            "IDA Pro/IDALib CLI or MCP as the preferred primary",
            "Ghidra/PyGhidra CLI or MCP as a cross-check",
            "../GBA-FE-ROMS",
            "Record every tool installed for the task, its version",
            "make expansion-modern-gdb-smoke",
            "symbolic `AgbMain` breakpoint",
            "Do not add or restore a whole-source/object/ROM SHA-256 identity gate, or",
            "committed source/blob/object/commit snapshots that duplicate Git's immutable",
            "Human provenance metadata may identify exact paths and facts",
            "Build CI for the **exact candidate commit**",
            "make remote-completion-check",
        )

        for requirement in required_contract:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, text)

    def test_autonomous_merge_has_no_human_review_gate(self):
        _, text = read_skill()

        self.assertIn(
            "Merge the PR autonomously when all four conditions hold.",
            text,
        )
        self.assertIn(
            "No human code review or approval is required.",
            text,
        )
        self.assertIn(
            "leave the work open and state the exact unresolved criterion",
            text,
        )
        self.assertIn(
            "not stop for review or approval after all objective evidence is complete.",
            text,
        )

    def test_ci_waiting_does_not_hold_reasoning_subagents(self):
        _, text = read_skill()
        project_instructions = (
            ROOT / ".github" / "copilot-instructions.md"
        ).read_text(encoding="utf-8")
        contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")
        required_policy = (
            (
                "candidate gate",
                ("Build CI", "Copilot review"),
            ),
        )
        forbidden_policy = RETIRED_MATRIX_SPELLINGS

        for surface, instructions in (
            ("development workflow skill", text),
            ("project instructions", project_instructions),
            ("contributor guidance", contributing),
        ):
            assert_normalized_policy(
                self,
                surface,
                instructions,
                required_policy,
                forbidden_policy,
            )

        assert_normalized_policy(
            self,
            "development workflow skill",
            text,
            (
                (
                    "direct watcher lifecycle",
                    (
                        "Reasoning subagents must not remain alive merely to wait",
                        "exactly one direct shell watcher",
                        "timeout 90m gh run watch <run-id> --interval 30 --exit-status",
                        "never create duplicate watchers",
                        "Only after the workflow reaches a terminal state",
                    ),
                ),
            ),
        )

    def test_trusted_push_ownership_is_mirrored_and_rejects_stale_roles(self):
        required_policy = (
            (
                "local implementation ownership",
                (
                    "Implementation subagents validate and commit locally but do not push",
                    "orchestrator pushes the exact commit under repository-owner context",
                    "Build does not become `action_required`",
                    "already-pushed run for that same SHA is `action_required`",
                    "gh run rerun <run-id>",
                    "under owner context",
                    "Never create empty commits",
                    "weaken Actions approvals",
                    "privileged `pull_request_target`",
                ),
            ),
        )
        stale_policy = (
            "Implementation subagents may push",
            "The subagent that pushes or dispatches a workflow",
            "The subagent that dispatches a workflow",
            "Create an empty commit to retrigger Build",
            "May weaken Actions approvals",
            "May use privileged pull_request_target",
        )
        for path in TRUSTED_PUSH_GUIDANCE_PATHS:
            assert_normalized_policy(
                self,
                str(path),
                path.read_text(encoding="utf-8"),
                required_policy,
                stale_policy,
            )

    def test_fleet_delivery_coordinator_is_mirrored(self):
        required_policy = (
            (
                "single fleet coordinator",
                (
                    "designate one delivery coordinator",
                    "run/PR ledger",
                    "exactly one direct shell watcher per active run",
                    "receives terminal watcher notifications",
                    "triages CI and review failures",
                    "routes local-only fixes to one owner",
                    "final merge gate and autonomous merge",
                    "post-merge conflict sweep",
                    "must not poll, sleep",
                    "must not duplicate watchers, fix ownership, or merge decisions",
                    "trusted owner-context push",
                ),
            ),
        )
        stale_policy = (
            "designate one delivery coordinator per pull request",
            "the delivery coordinator polls CI",
            "other agents may duplicate watchers",
            "implementation agents push their own replacement commits",
        )
        for path in FLEET_COORDINATOR_GUIDANCE_PATHS:
            assert_normalized_policy(
                self,
                str(path),
                path.read_text(encoding="utf-8"),
                required_policy,
                stale_policy,
            )

    def test_local_validation_stays_focused_and_ci_owns_broad_gate(self):
        required_policy = (
            (
                "focused local validation",
                (
                    "Local validation is change-focused by default",
                    "smallest tests that directly cover the changed behavior",
                    "one necessary compile or runtime scenario",
                    "Do not run broad catalog validation",
                    "full repository test suites",
                    "all-locale/all-feature profiles",
                    "broad archival builds",
                    "unless the changed surface directly owns that gate",
                    "Combined Build CI is the comprehensive final integration gate",
                    "Stop after focused checks pass",
                    "commit the candidate, and hand it off",
                ),
            ),
        )
        stale_policy = (
            "Before delivery, expand validation",
            "Run the full catalog locally",
            "Run every supported profile locally",
            "Local validation is the comprehensive final integration gate",
        )
        for path in FOCUSED_LOCAL_VALIDATION_PATHS:
            assert_normalized_policy(
                self,
                str(path),
                path.read_text(encoding="utf-8"),
                required_policy,
                stale_policy,
            )

    def test_delegated_agents_are_background_only(self):
        required_policy = (
            (
                "background-only delegation",
                (
                    "Every delegated reasoning agent must be launched in background mode",
                    "Never use a synchronous subagent invocation",
                    "continue every independent dependency-ready task immediately",
                    "rely on the automatic completion notification",
                    "instead of waiting synchronously or polling",
                    "two to five direct tool calls in the main orchestrator",
                ),
            ),
        )
        stale_policy = (
            "launch the subagent synchronously",
            "wait synchronously for the subagent",
            "poll the background agent",
        )
        for path in BACKGROUND_AGENT_GUIDANCE_PATHS:
            assert_normalized_policy(
                self,
                str(path),
                path.read_text(encoding="utf-8"),
                required_policy,
                stale_policy,
            )

    def test_combined_build_replaces_matrix_everywhere(self):
        for path in COMBINED_BUILD_GUIDANCE_PATHS:
            text = path.read_text(encoding="utf-8")
            with self.subTest(surface=path):
                self.assertIn("Build", text)
                for spelling in RETIRED_MATRIX_SPELLINGS:
                    self.assertNotIn(spelling, text.casefold())

    def test_readme_exposes_only_the_combined_build_badge(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("actions/workflows/build.yml/badge.svg", text)
        for retired_badge in (*RETIRED_MATRIX_SPELLINGS, "prs welcome", "makeapullrequest.com"):
            with self.subTest(retired_badge=retired_badge):
                self.assertNotIn(retired_badge, text.casefold())

    def test_contributor_watcher_examples_are_bounded(self):
        watcher_examples = []
        for path in WATCHER_DOC_PATHS:
            text = path.read_text(encoding="utf-8")
            watcher_examples.extend(
                " ".join(command.split())
                for block in FENCED_COMMAND_BLOCK.finditer(text)
                for command in block.group("commands").splitlines()
                if re.search(r"\bgh run watch\b", command)
            )
            self.assertEqual(watcher_example_violations(text), [], path)

        self.assertIn(CANONICAL_WATCHER_COMMAND, watcher_examples)
        self.assertEqual(
            watcher_example_violations("```bash\ngh run watch <run-id> --exit-status\n```"),
            ["gh run watch <run-id> --exit-status"],
        )

    def test_post_merge_reconciliation_policy_is_mirrored(self):
        _, skill = read_skill()
        surfaces = {
            "skill": skill,
            "Copilot instructions": (ROOT / ".github" / "copilot-instructions.md").read_text(
                encoding="utf-8"
            ),
        }
        required_contract = (
            "After each merge, immediately inspect every open PR.",
            "real conflicts or shared-contract changes",
            "refresh",
            "independent conflicts concurrently",
            "rerun only conflict-affected checks",
            "Never pause or cancel unaffected PR CI",
            "priority or unrelated `master` movement",
            "only when its",
            "candidate actually changes",
        )
        for surface, text in surfaces.items():
            assert_normalized_policy(
                self,
                surface,
                text,
                (("continuous monitoring lifecycle", required_contract),),
            )

    def test_continuous_pr_monitoring_policy_is_mirrored(self):
        _, skill = read_skill()
        surfaces = {
            "skill": skill,
            "Copilot instructions": (ROOT / ".github" / "copilot-instructions.md").read_text(
                encoding="utf-8"
            ),
        }
        required_contract = (
            "After each PR opens or updates",
            "exact-head Build CI",
            "Copilot comments/threads",
            "mergeability",
            "triage review findings",
            "immediately",
            "normal `master` merge",
            "Monitor master-branch CI after every merge",
            "exact-master combined Build CI",
            "open-PR conflict rescan",
            "Fix forward or revert a broken `master`",
            "unrelated PRs do not wait",
            "on healthy master runs",
            "attached asynchronous shell watchers",
            "nonblocking",
            "Continue unrelated dependency-ready work",
            "never occupy a reasoning agent",
            "waiting-only response",
            "Cancel only a superseded candidate run",
            "candidate actually changes",
            "blocks that issue's closure and remote completion",
            "not unrelated independent PRs",
        )
        for surface, text in surfaces.items():
            assert_normalized_policy(
                self,
                surface,
                text,
                (("continuous monitoring lifecycle", required_contract),),
            )

    def test_issue_specific_pull_request_and_stack_contract(self):
        _, text = read_skill()
        required_contract = (
            "Every independent issue must have one dedicated pull request.",
            "must not implement or close several independent issues",
            "create explicit dependent sub-issues before implementation",
            "Base every independent issue branch directly on `master`.",
            "when one issue genuinely depends",
            "`Depends on #...` links",
            "run exact-head Build CI and Copilot review against that genuine base",
            "Never temporarily retarget a child to `master`",
            "otherwise misrepresent the stack solely to trigger CI",
            "Review and merge the stack bottom-up",
            "gh pr edit <child-pr> --base master",
            "retarget the child once",
            "`pull_request` `edited` event",
            "fresh exact-head",
            "base/tree evidence changed",
            "The `edited` event alone is not delivery evidence",
            "pull_request.head.sha",
            "Apply candidate-commit Build CI plus Copilot review",
            "consolidated Build verification",
            "Complete the umbrella",
            "initiative only after every accepted",
        )

        for requirement in required_contract:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, text)

        for requirement in (
            "Whenever the parent head changes while both PRs remain open",
            "merge the updated parent branch into the child with a normal merge commit",
            "A parent-only push does not emit a child `pull_request` event",
            "the required `synchronize` event",
            "Never accept the child's earlier green run against an older parent head",
        ):
            with self.subTest(normalized_requirement=requirement):
                self.assertIn(normalize_policy(requirement), normalize_policy(text))

        self.assertIn(
            normalize_policy("Complete the umbrella initiative only after every accepted"),
            normalize_policy(text),
        )

    def test_meaningful_test_evidence_policy_is_aligned(self):
        """Validate the fail-closed canonical policy AST on every guidance surface."""
        for path in (
            SKILL_PATH,
            COPILOT_INSTRUCTIONS_PATH,
        ):
            with self.subTest(surface=str(path.relative_to(ROOT))):
                self.assertEqual(
                    CANONICAL_POLICY_AST,
                    self.assert_meaningful_test_policy(
                        path.read_text(encoding="utf-8")
                    ),
                )

        reverse_clause_order = tuple(
            reversed([clause_name for clause_name, _, _ in CANONICAL_POLICY_SOURCE])
        )
        reverse_item_orders = {
            clause_name: tuple(
                reversed([item_name for item_name, _, _ in items])
            )
            for clause_name, _, items in CANONICAL_POLICY_SOURCE
        }
        harmless_variation = render_meaningful_test_policy(
            clause_order=reverse_clause_order,
            item_orders=reverse_item_orders,
        ).replace("behavior", "behaviour").replace(
            "source,review,history", "history,source,review"
        )
        self.assertEqual(
            CANONICAL_POLICY_AST,
            self.assert_meaningful_test_policy(harmless_variation),
        )

        policy_text = render_meaningful_test_policy()
        hidden_policy_section = "<!--\n" + policy_text + "\n-->\n"
        with self.assertRaisesRegex(
            AssertionError,
            "policy heading appears inside an HTML comment",
        ):
            self.assert_meaningful_test_policy(hidden_policy_section)
        for mutation_name, mutation in (
            (
                "comment inserted in heading syntax",
                policy_text.replace(
                    "## Meaningful test evidence",
                    "##<!-- --> Meaningful test evidence",
                ),
            ),
            (
                "comment inserted in list-marker syntax",
                policy_text.replace(
                    "- **Evidence standard:** required",
                    "-<!-- --> **Evidence standard:** required",
                ),
            ),
        ):
            with self.subTest(mutation=mutation_name):
                with self.assertRaises(AssertionError):
                    self.assert_meaningful_test_policy(mutation)
        harmless_single_line_comment = policy_text.replace(
            "  - **behavior:** required",
            "  - **behavior:** required\n  <!-- explanatory note -->",
        )
        self.assertEqual(
            CANONICAL_POLICY_AST,
            self.assert_meaningful_test_policy(harmless_single_line_comment),
        )
        harmless_multiline_comment = policy_text.replace(
            "  - **behavior:** required",
            "  - **behavior:** required\n"
            "  <!-- explanatory note\n"
            "       continued on another line -->",
        )
        self.assertEqual(
            CANONICAL_POLICY_AST,
            self.assert_meaningful_test_policy(harmless_multiline_comment),
        )
        for marker in ("```", "~~~"):
            with self.subTest(commented_fence=marker):
                self.assertEqual(
                    CANONICAL_POLICY_AST,
                    self.assert_meaningful_test_policy(
                        "<!--\n" + marker + "\n-->\n" + policy_text
                    ),
                )

        invalid_backtick_fence = (
            policy_text
            + "\n```markdown `policy`\n"
            + "Text-only tests are permitted.\n"
            + "```\n"
        )
        with self.assertRaisesRegex(
            DocsCheckError,
            r"invalid backtick fenced code opener at line",
        ):
            self.assert_meaningful_test_policy(invalid_backtick_fence)
        self.assertEqual(
            CANONICAL_POLICY_AST,
            self.assert_meaningful_test_policy(
                policy_text
                + "\n~~~markdown `policy`\n"
                + "Text-only tests are permitted.\n"
                + "~~~\n"
            ),
        )

        for indentation in ("    ", "\t"):
            with self.subTest(indented_comment=repr(indentation)):
                literal_scan = scan_policy_markdown(
                    indentation
                    + "<!--\n"
                    + "Text-only tests are permitted.\n"
                )
                self.assertEqual(
                    (
                        indentation + "<!--",
                        "Text-only tests are permitted.",
                        "",
                    ),
                    literal_scan.visible_lines,
                )
                self.assertEqual(frozenset(), literal_scan.comment_line_indexes)

                indented_comment_attack = policy_text.replace(
                    f"## {MEANINGFUL_TEST_POLICY_HEADING}\n\n",
                    f"## {MEANINGFUL_TEST_POLICY_HEADING}\n\n"
                    + indentation
                    + "<!--\n"
                    + "Text-only tests are permitted.\n"
                    + "-->\n",
                )
                with self.assertRaisesRegex(
                    AssertionError,
                    r"stray HTML comment closer at line",
                ):
                    self.assert_meaningful_test_policy(
                        indented_comment_attack
                    )

        compact_policy_text = policy_text.replace(
            f"## {MEANINGFUL_TEST_POLICY_HEADING}\n\n",
            f"## {MEANINGFUL_TEST_POLICY_HEADING}\n",
        )
        raw_html_mutations = (
            ("unclosed script", "<script>\n" + policy_text),
            (
                "closed script",
                "<script>\n"
                + policy_text
                + "\n# hidden boundary\n</script>\n",
            ),
            ("unclosed pre", "<pre>\n" + policy_text),
            (
                "closed pre",
                "<pre>\n"
                + policy_text
                + "\n# hidden boundary\n</pre>\n",
            ),
            ("style", "<style>\n" + policy_text),
            ("textarea", "<textarea>\n" + policy_text),
            ("processing instruction", "<?policy\n" + policy_text),
            ("CDATA section", "<![CDATA[\n" + policy_text),
            ("declaration", "<!POLICY\n" + policy_text),
            ("block tag", "<div>\n" + compact_policy_text),
            (
                "closing block tag",
                "</section>\n" + compact_policy_text,
            ),
            (
                "complete tag",
                "<policy-wrapper>\n" + compact_policy_text,
            ),
            (
                "indented mixed-case raw-text tag",
                "   <ScRiPt>\n" + policy_text,
            ),
        )
        for mutation_name, mutation in raw_html_mutations:
            with self.subTest(raw_html_block=mutation_name):
                with self.assertRaisesRegex(
                    AssertionError,
                    r"raw HTML block .* starts at line 1",
                ):
                    self.assert_meaningful_test_policy(mutation)
        with self.assertRaisesRegex(
            AssertionError,
            r"HTML comments must occupy standalone lines",
        ):
            self.assert_meaningful_test_policy(
                "  <!-- note -->  <script>\n" + policy_text
            )

        safe_html_contexts = (
            "```html\n<script>\n<pre>\n</pre>\n</script>\n```\n",
            "<!--\n<script>\n<pre>\n</pre>\n</script>\n-->\n",
            "    <script>\n",
            "Use `<script>` and ``<StYle>`` as literal examples.\n",
        )
        for prefix in safe_html_contexts:
            with self.subTest(safe_html_context=prefix.splitlines()[0]):
                self.assertEqual(
                    CANONICAL_POLICY_AST,
                    self.assert_meaningful_test_policy(prefix + policy_text),
                )
        with self.assertRaisesRegex(
            AssertionError,
            r"inline raw-text tag <script> starts at line 1",
        ):
            self.assert_meaningful_test_policy(
                "Visible prose before <ScRiPt type=\"text/javascript\">\n"
                + policy_text
            )


        hidden_boundary_mutation = (
            policy_text
            + "\n<!--\n"
            + "# hidden boundary\n"
            + "-->\n"
            + "Text-only tests are permitted.\n"
        )
        with self.assertRaisesRegex(
            AssertionError,
            "unexpected policy content: Text-only tests are permitted",
        ):
            self.assert_meaningful_test_policy(hidden_boundary_mutation)

        for mutation_name, mutation, error in (
            (
                "unterminated comment",
                policy_text + "\n<!-- explanatory note\n",
                r"unterminated HTML comment opened at line",
            ),
            (
                "nested comment",
                policy_text + "\n<!-- outer <!-- nested -->\n",
                r"nested HTML comment opener at line",
            ),
            (
                "stray comment closer",
                policy_text + "\n-->\n",
                r"stray HTML comment closer at line",
            ),
            (
                "HTML end-bang comment closer",
                policy_text + "\n<!-- explanatory note --!>\n",
                r"malformed HTML comment closer at line",
            ),
        ):
            with self.subTest(mutation=mutation_name):
                with self.assertRaisesRegex(AssertionError, error):
                    self.assert_meaningful_test_policy(mutation)

        for leading in ("\u00A0", "\u2003"):
            with self.subTest(unicode_fence_leading=repr(leading)):
                with self.assertRaises(AssertionError):
                    self.assert_meaningful_test_policy(
                        policy_text
                        + f"\n{leading}```\n"
                        + "Text-only tests are permitted.\n"
                        + f"{leading}```\n"
                    )

        extracted_section = read_markdown_section(
            "## Target\n"
            "visible before\n"
            "<!--\n"
            "# hidden comment boundary\n"
            "comment-only text\n"
            "-->\n"
            "```markdown\n"
            "# fenced boundary\n"
            "hidden fenced prose\n"
            "```\n"
            "visible after\n"
            "## Next\n"
            "outside\n",
            "Target",
        )
        self.assertEqual(
            ["visible before", "visible after"],
            [line for line in extracted_section if line.strip()],
        )

        for terminator in (
            "# Separate document section",
            "  # Separate document section ###",
        ):
            with self.subTest(terminator=terminator):
                self.assertEqual(
                    CANONICAL_POLICY_AST,
                    self.assert_meaningful_test_policy(
                        policy_text + "\n" + terminator + "\n"
                    ),
                )
        for policy_heading in (
            "##  Meaningful test evidence",
            "##\tMeaningful test evidence",
            " ## Meaningful test evidence #",
            "  ## Meaningful test evidence ##",
            "   ## Meaningful test evidence ###\t",
            " ## Meaningful test evidence ##\r",
        ):
            with self.subTest(policy_heading=policy_heading):
                self.assertEqual(
                    CANONICAL_POLICY_AST,
                    self.assert_meaningful_test_policy(
                        policy_text.replace(
                            "## Meaningful test evidence",
                            policy_heading,
                        )
                    ),
                )

        fenced_policy_example = (
            "```markdown\n"
            "## Meaningful test evidence\n"
            "- **Prohibited evidence:** permitted\n"
            "```\n\n"
            + policy_text
        )
        self.assertEqual(
            CANONICAL_POLICY_AST,
            self.assert_meaningful_test_policy(fenced_policy_example),
        )
        fenced_terminator_mutation = (
            policy_text
            + "\n```\n# Separate document section\n```\n"
            + "Text-only tests are permitted.\n"
        )
        with self.assertRaises(AssertionError):
            self.assert_meaningful_test_policy(fenced_terminator_mutation)
        unterminated_fence_mutation = (
            policy_text + "\n```\nText-only tests are permitted.\n"
        )
        with self.assertRaisesRegex(
            DocsCheckError,
            r"unterminated fenced code block opened at line",
        ):
            self.assert_meaningful_test_policy(unterminated_fence_mutation)

        wrapped_git_rationale = policy_text.replace(
            "  - **Git-text rationale:** required. "
            "git-tracks=source,review,history; "
            "raw-tracked-text=not-behavior-evidence",
            "  - **Git-text rationale:** required. "
            "git-tracks=source,review,history;\n"
            "    raw-tracked-text=not-behavior-evidence",
        )
        self.assertEqual(
            CANONICAL_POLICY_AST,
            self.assert_meaningful_test_policy(wrapped_git_rationale),
        )
        whitespace_variation = policy_text.replace(
            "parsed structural contract",
            "parsed   structural\tcontract",
        ).replace(
            "git-tracks=source,review,history; "
            "raw-tracked-text=not-behavior-evidence",
            "git-tracks = source, review, history ; "
            "raw-tracked-text = not-behavior-evidence",
        )
        self.assertEqual(
            CANONICAL_POLICY_AST,
            self.assert_meaningful_test_policy(whitespace_variation),
        )

        mutations = {
            "unexpected paragraph": policy_text
            + "\nText-only tests are permitted.\n",
            "duplicate heading": policy_text
            + "\n## Meaningful test evidence\n\n",
            "lower-level heading": policy_text
            + "\n### Text-only tests are permitted\n"
            "Text-only tests are permitted.\n",
            "negated requirement": policy_text
            + "\nTests must not prove runtime state.\n",
            "top-level prohibited polarity": policy_text.replace(
                "- **Prohibited evidence:** prohibited",
                "- **Prohibited evidence:** permitted",
            ),
            "prohibited category polarity": policy_text.replace(
                "  - **comments:** prohibited",
                "  - **comments:** permitted",
            ),
            "strikethrough label": policy_text.replace(
                "  - **comments:** prohibited",
                "  - **~~comments~~:** prohibited",
            ),
            "altered emphasis label": policy_text.replace(
                "  - **comments:** prohibited",
                "  - **_comments_:** prohibited",
            ),
            "marked status": policy_text.replace(
                "- **Prohibited evidence:** prohibited",
                "- **Prohibited evidence:** ~~prohibited~~",
            ),
            "contradictory Git rationale polarity": policy_text.replace(
                "raw-tracked-text=not-behavior-evidence",
                "raw-tracked-text=behavior-evidence",
            ),
            "duplicate Git-tracks value": policy_text.replace(
                "source,review,history",
                "source,review,history,source",
            ),
            "duplicate raw-tracked-text value": policy_text.replace(
                "raw-tracked-text=not-behavior-evidence",
                "raw-tracked-text=not-behavior-evidence,not-behavior-evidence",
            ),
            "punctuated Git rationale polarity": policy_text.replace(
                "raw-tracked-text=not-behavior-evidence",
                "raw-tracked-text=not!behavior-evidence",
            ),
            "strikethrough Git rationale value": policy_text.replace(
                "not-behavior-evidence",
                "~~not-behavior-evidence~~",
            ),
            "linked Git rationale value": policy_text.replace(
                "not-behavior-evidence",
                "[not-behavior-evidence](https://example.invalid/policy)",
            ),
            "code-span Git rationale value": policy_text.replace(
                "not-behavior-evidence",
                "`not-behavior-evidence`",
            ),
            "contradictory Git rationale detail": policy_text.replace(
                "raw-tracked-text=not-behavior-evidence",
                "raw-tracked-text=not-behavior-evidence; "
                "text-only-tests=permitted",
            ),
            "extra item": policy_text.replace(
                "  - **behavior:** required",
                "  - **behavior:** required\n"
                "  - **arbitrary strings:** permitted",
            ),
            "clause trailing contradictory prose": policy_text.replace(
                "- **Evidence standard:** required",
                "- **Evidence standard:** required "
                "Text-only tests are permitted.",
            ),
            "item trailing contradictory prose": policy_text.replace(
                "  - **behavior:** required",
                "  - **behavior:** required "
                "Text-only tests are permitted.",
            ),
        }
        for category in PROHIBITED_EVIDENCE_CATEGORIES:
            mutations[f"{category} removal"] = policy_text.replace(
                f"  - **{category}:** prohibited\n",
                "",
            )
            mutations[f"{category} detail"] = policy_text.replace(
                f"  - **{category}:** prohibited",
                f"  - **{category}:** prohibited. text-only tests are permitted",
            )

        for mutation_name, mutation in mutations.items():
            with self.subTest(mutation=mutation_name):
                with self.assertRaises(AssertionError):
                    self.assert_meaningful_test_policy(mutation)

    def test_meaningful_test_policy_requires_exactly_one_valid_markdown_heading(self):
        for policy_text in (
            "##Meaningful test evidence\n\n- **Evidence standard:** ignored\n",
            "    ## Meaningful test evidence\n\n"
            "- **Evidence standard:** ignored\n",
            "## Meaningful test evidence\n\n"
            "## Meaningful test evidence\n",
        ):
            with self.subTest(policy_text=policy_text):
                with self.assertRaisesRegex(
                    AssertionError,
                    "expected exactly one Markdown section",
                ):
                    read_markdown_section(
                        policy_text,
                        MEANINGFUL_TEST_POLICY_HEADING,
                    )

    def test_review_size_preflight_and_exception_contract(self):
        _, text = read_skill()
        required_contract = (
            "git diff --name-only <base>...HEAD",
            "git diff --numstat <base>...HEAD",
            "git diff --shortstat <base>...HEAD",
            "20,000-line limit as a hard ceiling, not a",
            "genuinely indivisible single-issue change",
            "alternative automated and per-area review evidence",
            "never combine independent issues",
            "do not require Graphite, Git Town",
        )

        for requirement in required_contract:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, text)

    def test_contributor_guidance_has_worked_stack_example_and_gates(self):
        text = CONTRIBUTING_PATH.read_text(encoding="utf-8")
        required_contract = (
            "Every independent issue gets one dedicated branch and PR.",
            "Worked umbrella and stack example",
            "`feat/101-doc-search`, base `master`",
            "`feat/103-selector`, base `feat/102-registry`",
            "run exact-head Build CI and Copilot review there",
            "Never temporarily",
            "misrepresent",
            "the stack solely to trigger CI",
            "gh pr edit <child-pr-number> --base master",
            "retarget once",
            "`pull_request`",
            "`edited` event to start fresh exact-head Build CI",
            "candidate base/tree evidence changed",
            "bound to the unchanged child `pull_request.head.sha`",
            "does not",
            "replace diff verification or successful gates",
            "automatic master Build rerun",
            "same consolidated evidence",
            "make remote-completion-check",
            "git diff --name-only \"$base_ref\"...HEAD",
            "20,000-line limit is a hard ceiling",
            "Graphite, Git Town",
        )

        for requirement in required_contract:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, text)

        for requirement in (
            "Whenever the parent head changes",
            "merge the updated parent into the child with a normal merge commit",
            "A parent-only push does not emit a child `pull_request` event",
            "the required `synchronize` event",
            "merge that updated parent into `feat/103-selector`",
            "the resulting child `synchronize`",
        ):
            with self.subTest(normalized_requirement=requirement):
                self.assertIn(normalize_policy(requirement), normalize_policy(text))

    def test_pull_request_template_keeps_only_frozen_contract(self):
        text = PR_TEMPLATE_PATH.read_text(encoding="utf-8")
        required_contract = (
            "exactly one independent issue",
            "Frozen classification and relationships",
            "Immediate base branch",
            "Stack position",
            "Depends on",
            "Known dependents",
            "explicit dependent",
            "sub-issues",
            "Frozen acceptance criteria",
            "Tester-facing procedure",
            "Compatibility impact",
            "Canonical candidate evidence",
            "canonical marked-comment protocol",
            "do not copy its marker or evolving fields",
        )

        for requirement in required_contract:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, text)
        self.assertNotIn(CANDIDATE_EVIDENCE_MARKER, text)
        self.assertNotIn("- [ ]", text)
        self.assertEqual(
            candidate_evidence_violations(
                text,
                [CANDIDATE_EVIDENCE_MARKER],
            ),
            [],
        )

    def test_candidate_evidence_requires_one_canonical_comment(self):
        body = PR_TEMPLATE_PATH.read_text(encoding="utf-8")
        canonical_comment = (
            f"{CANDIDATE_EVIDENCE_MARKER}\n"
            "Candidate evidence is maintained here.\n"
        )
        architecture_comment = (
            "Architecture hold: metadata-only contexts cannot replace "
            "candidate evidence.\n"
        )
        self.assertEqual(
            candidate_evidence_violations(
                body,
                [canonical_comment, architecture_comment],
            ),
            [],
        )
        self.assertNotIn(CANDIDATE_EVIDENCE_MARKER, architecture_comment)

        for field in EVOLVING_PR_BODY_FIELDS:
            with self.subTest(field=field):
                self.assertTrue(
                    candidate_evidence_violations(
                        body + f"\n{field} evolving\n",
                        [canonical_comment],
                    )
                )
        for changed_body, comments in (
            (body + f"\n{CANDIDATE_EVIDENCE_MARKER}\n", [canonical_comment]),
            (body, []),
            (body, [canonical_comment, canonical_comment]),
            (
                body,
                [
                    CANDIDATE_EVIDENCE_MARKER
                    + " inline text\n",
                ],
            ),
            (
                body,
                [
                    f"{CANDIDATE_EVIDENCE_MARKER}\n"
                    f"{CANDIDATE_EVIDENCE_MARKER}\n",
                ],
            ),
        ):
            with self.subTest(body=changed_body[-80:], comments=comments):
                self.assertTrue(
                    candidate_evidence_violations(changed_body, comments)
                )

    def test_oracle_actuals_use_canonical_comment_across_guidance(self):
        surfaces = {
            PR_TEMPLATE_PATH: PR_TEMPLATE_PATH.read_text(encoding="utf-8"),
            SKILL_PATH: SKILL_PATH.read_text(encoding="utf-8"),
            CONTRIBUTING_PATH: CONTRIBUTING_PATH.read_text(encoding="utf-8"),
            ISSUE_RESOLUTION_POLICY_PATH: (
                ISSUE_RESOLUTION_POLICY_PATH.read_text(encoding="utf-8")
            ),
            WORKFLOW_PILOT_PATH: WORKFLOW_PILOT_PATH.read_text(encoding="utf-8"),
        }
        for path, text in surfaces.items():
            with self.subTest(path=path):
                self.assertEqual(oracle_evidence_location_violations(text), [])

        for path in (CONTRIBUTING_PATH, ISSUE_RESOLUTION_POLICY_PATH):
            normalized = normalize_policy(surfaces[path])
            with self.subTest(path=path, contract="frozen-plan"):
                self.assertIn(
                    normalize_policy("frozen baseline/fingerprint plan"),
                    normalized,
                )
                self.assertIn(
                    normalize_policy("canonical marked comment"),
                    normalized,
                )
                self.assertIn(normalize_policy("rationale"), normalized)
                self.assertIn(
                    normalize_policy("independent verification"),
                    normalized,
                )

        stale_instructions = (
            "Explain the oracle change in the PR description.",
            "Record actual fingerprint rationale in your PR description.",
            "Put baseline verification in the pull request description.",
        )
        for path, text in surfaces.items():
            for stale in stale_instructions:
                with self.subTest(path=path, stale=stale):
                    self.assertTrue(
                        oracle_evidence_location_violations(
                            text + "\n" + stale
                        )
                    )

    def test_classifier_bootstrap_and_worker_fallback_are_distinct_in_docs(self):
        mutations = (
            (
                "reverse-bootstrap-authority",
                "trusted-default-bootstrap",
                r"classifier bootstrap may use the trusted\s+default branch "
                r"when PR base\s+identity is missing or unusable",
                "classifier bootstrap must fail when PR base identity is unusable",
            ),
            (
                "remove-incomplete-base-path",
                "incomplete-base-exact-head-workers",
                r"missing,\s+(?:empty,\s+)?malformed,\s+or\s+"
                r"(?:incoherent|event-mismatched)\s+base ref/SHA",
                "complete and coherent base ref/SHA",
            ),
            (
                "replace-exact-head",
                "incomplete-base-exact-head-workers",
                r"valid exact\s+PR head",
                "pull-request merge ref",
            ),
            (
                "reverse-summary-failure",
                "incomplete-base-summary-failure",
                r"(?:fails normal\s+summary|normal\s+`summary`\s+audits them "
                r"and fails)",
                "normal summary succeeds",
            ),
            (
                "allow-worker-fallback",
                "worker-merge-default-fallback",
                r"worker checkouts\s+never use a merge/default\s+fallback",
                "worker checkouts may use a merge/default fallback",
            ),
            (
                "accept-non-sha-fallback",
                "fallback-lowercase-sha",
                r"exact lowercase\s+40-hex SHA",
                "any nonempty ref or identity",
            ),
            (
                "remove-publisher-revision-check",
                "publisher-revision-verification",
                r"verifies\s+`/usr/bin/git rev-parse HEAD`\s+"
                r"immediately after checkout",
                "trusts the checkout action",
            ),
            (
                "run-candidate-with-private-base",
                "publisher-secret-boundary",
                r"(?i:No\s+candidate\s+command\s+runs\s+while\s+the\s+"
                r"base\s+exists)",
                "A candidate command runs while the base exists",
            ),
            (
                "download-before-candidate-work",
                "publisher-secret-boundary",
                r"All repository/candidate-controlled commands finish before "
                r"private download",
                "Private download happens before candidate-controlled commands",
            ),
            (
                "lag-producer-one-revision",
                "publisher-secret-boundary",
                r"exact\s+validated\s+after\s+commit",
                "previous protected-branch commit",
            ),
            (
                "transfer-complete-rom",
                "publisher-secret-boundary",
                r"No\s+complete\s+target\s+ROM\s+enters\s+an\s+Actions\s+"
                r"artifact,\s+cache,\s+release,\s+or\s+log",
                "The complete target ROM enters an Actions artifact",
            ),
            (
                "reuse-runner-user",
                "publisher-secret-boundary",
                r"dedicated\s+unprivileged\s+UID",
                "runner account",
            ),
            (
                "drop-builder-network-isolation",
                "publisher-secret-boundary",
                r"mount,\s+PID,\s+and\s+network\s+namespaces",
                "mount and PID namespaces",
            ),
            (
                "drop-builder-process-teardown",
                "publisher-secret-boundary",
                r"exact\s+process\s+group",
                "best-effort process cleanup",
            ),
            (
                "share-builder-mount-propagation",
                "publisher-secret-boundary",
                r"(?i:private\s+mount\s+propagation)",
                "shared mount propagation",
            ),
            (
                "make-host-paths-writable",
                "publisher-secret-boundary",
                r"recursively\s+read-only",
                "writable",
            ),
            (
                "allow-uid-wide-kill",
                "publisher-secret-boundary",
                r"no\s+UID-wide\s+signal",
                "a UID-wide signal",
            ),
            (
                "retain-candidate-log-fds",
                "publisher-secret-boundary",
                r"closes\s+inherited\s+file\s+descriptors\s+above\s+2",
                "inherits workflow log descriptors",
            ),
            (
                "replay-candidate-output",
                "publisher-secret-boundary",
                r"(?i:Candidate\s+output\s+is\s+never\s+replayed,\s+"
                r"logged,\s+or\s+uploaded)",
                "Candidate output is replayed to the workflow log",
            ),
            (
                "restore-volume-dependent-output-file",
                "publisher-secret-boundary",
                r"stdin/stdout/stderr\s+permanently\s+to\s+private\s+"
                r"`/dev/null`",
                "stdin/stdout/stderr to a bounded regular sink",
            ),
            (
                "drop-supervisor-cgroup-view",
                "publisher-secret-boundary",
                r"root-only\s+`0700`\s+`/mnt/supervisor`",
                "candidate-visible supervisor path",
            ),
            (
                "allow-unexpected-handoff",
                "publisher-secret-boundary",
                r"regular,\s+nonsymlink,\s+single-link",
                "ordinary outputs",
            ),
            (
                "add-source-hash-ledger",
                "publisher-secret-boundary",
                r"(?i:No\s+whole-file\s+source\s+hash\s+pins)",
                "whole-file source hash pins",
            ),
            (
                "reuse-candidate-runner",
                "publisher-secret-boundary",
                r"fresh\s+hosted\s+publisher",
                "reused candidate runner",
            ),
            (
                "drop-pr-number-coherence",
                "fallback-pr-number",
                r"numeric event number",
                "unvalidated event label",
            ),
            (
                "make-common-identity-optional",
                "candidate-common-identity",
                r"canonical successful\s+`event-identity`\s+context",
                "optional event-identity context",
            ),
            (
                "make-common-router-optional",
                "candidate-common-identity",
                r"canonical\s+successful\s+`event-router`\s+context",
                "optional event-router context",
            ),
            (
                "remove-base-ref-bound",
                "base-ref-git-grammar",
                r"bounded to 1024 UTF-8 bytes",
                "accepted at any size",
            ),
            (
                "replace-full-ref-oracle",
                "base-ref-git-grammar",
                r"`git check-ref-format refs/heads/<base\.ref>`",
                "`git check-ref-format --branch <base.ref>`",
            ),
        )
        for path in (
            FRAMEWORK_SUPPORT_PATH,
            WORKFLOW_PILOT_PATH,
            WORKFLOW_GOVERNANCE_PATH,
        ):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertEqual(
                    classifier_bootstrap_contract_violations(text),
                    [],
                )
            for name, expected_violation, pattern, replacement in mutations:
                with self.subTest(path=path, mutation=name):
                    changed, count = re.subn(pattern, replacement, text, count=1)
                    self.assertEqual(count, 1)
                    self.assertNotEqual(changed, text)
                    self.assertIn(
                        expected_violation,
                        classifier_bootstrap_contract_violations(changed),
                    )

    def test_workflow_tester_topologies_match_parsed_job_sets(self):
        governance = WORKFLOW_GOVERNANCE_PATH.read_text(encoding="utf-8")
        self.assertEqual(workflow_tester_topology_violations(governance), [])

        required_setup_sets = (
            (
                "Parsed full-PR job set",
                "stacked-full-pr-job-set",
            ),
            (
                "Parsed current metadata-only job/check set",
                "current-metadata-job-set",
            ),
            (
                "Parsed live opened-run job set",
                "live-opened-full-job-set",
            ),
            (
                "Parsed live title-edit job/check set",
                "live-title-metadata-job-set",
            ),
            (
                "Parsed live title-restore job/check set",
                "live-restore-metadata-job-set",
            ),
        )
        for label, expected_violation in required_setup_sets:
            documented = documented_job_set(governance, label)
            for job_id in ("event-identity", "event-router"):
                with self.subTest(label=label, omitted=job_id):
                    changed = replace_documented_job_set(
                        governance,
                        label,
                        sorted(documented - {job_id}),
                    )
                    self.assertNotEqual(changed, governance)
                    self.assertIn(
                        expected_violation,
                        workflow_tester_topology_violations(changed),
                    )

        pre_fix_label = "Parsed preserved pre-fix body-only job set"
        pre_fix = documented_job_set(governance, pre_fix_label)
        changed = replace_documented_job_set(
            governance,
            pre_fix_label,
            sorted(pre_fix - {"host-tests"}),
        )
        self.assertIn(
            "preserved-pre-fix-job-set",
            workflow_tester_topology_violations(changed),
        )

        reordered = governance
        for label in (
            "Parsed full-PR job set",
            "Parsed current metadata-only job/check set",
            pre_fix_label,
            "Parsed live opened-run job set",
            "Parsed live title-edit job/check set",
            "Parsed live title-restore job/check set",
        ):
            reordered = replace_documented_job_set(
                reordered,
                label,
                sorted(documented_job_set(reordered, label), reverse=True),
            )
        self.assertEqual(workflow_tester_topology_violations(reordered), [])

        semantic_names, count = re.subn(
            r"Live branch protection\s+remains unchanged and therefore still\s+"
            r"requires canonical `host-tests`,\s+`build`,\s+`summary`, and "
            r"the independent\s+GitGuardian context",
            "branch protection may skip full summary continuity",
            governance,
            1,
        )
        self.assertEqual(count, 1)
        self.assertNotEqual(semantic_names, governance)
        self.assertIn(
            "skipped-worker-names-are-semantic",
            workflow_tester_topology_violations(semantic_names),
        )

    def test_metadata_adapter_docs_require_two_adapter_two_skipped_contract(self):
        documents = {
            "workflow-pilot": WORKFLOW_PILOT_PATH.read_text(encoding="utf-8"),
            "framework-support": FRAMEWORK_SUPPORT_PATH.read_text(encoding="utf-8"),
            "workflow-governance": WORKFLOW_GOVERNANCE_PATH.read_text(encoding="utf-8"),
        }
        required_fragments = (
            "host-tests/build",
            "continuity adapters",
            "runner-backed",
            "body/title-only",
            "GITHUB_EVENT_PATH",
            "extended-host-tests",
            "legacy",
            "platform-skipped",
            "canonical `summary`",
            "newest conclusively full Build CI run",
            "rejects redirects",
            "run_number",
            "current-run",
        )
        forbidden_fragments = (
            "distinct metadata-only names",
            "same canonical skipped worker names",
            "canonical skipped worker contexts",
            "each skipped with no runner",
            "all four workers are exactly `skipped`",
            "skip the four expensive workers",
        )
        for name, text in documents.items():
            normalized = normalize_policy(text)
            with self.subTest(document=name):
                for fragment in required_fragments:
                    self.assertIn(normalize_policy(fragment), normalized)
                for fragment in forbidden_fragments:
                    self.assertNotIn(normalize_policy(fragment), normalized)

    def test_live_title_probe_contract_is_complete_and_fail_closed(self):
        governance = WORKFLOW_GOVERNANCE_PATH.read_text(encoding="utf-8")
        self.assertEqual(live_title_probe_violations(governance), [])
        body_case = raw_markdown_section(governance, BODY_EDIT_CASE_HEADING)
        bash_blocks = [
            textwrap.dedent(match.group("body"))
            for match in re.finditer(
                r"^[ ]*```bash\n(?P<body>.*?)^[ ]*```",
                body_case,
                re.DOTALL | re.MULTILINE,
            )
        ]
        self.assertTrue(bash_blocks)
        parsed = subprocess.run(
            ["/bin/bash", "-n"],
            input="\n".join(bash_blocks),
            text=True,
            check=False,
            capture_output=True,
        )
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        embedded_python = re.findall(
            r"<<'PY'\n(?P<body>.*?)\nPY",
            "\n".join(bash_blocks),
            re.DOTALL,
        )
        self.assertEqual(len(embedded_python), 3)
        for index, source in enumerate(embedded_python):
            with self.subTest(embedded_python=index):
                compile(source, f"<live-title-probe-{index}>", "exec")
        evaluator_source = textwrap.dedent(embedded_python[-1])
        head_sha = "1" * 40
        base_sha = "2" * 40

        def run_record():
            return {
                "conclusion": "success",
                "event": "pull_request",
                "headSha": head_sha,
                "url": "https://example.invalid/run",
            }

        def job_record(
            job_id,
            name,
            conclusion="success",
            runner_name="GitHub Actions 1",
            started_at="2026-08-31T00:00:00Z",
        ):
            return {
                "conclusion": conclusion,
                "id": job_id,
                "name": name,
                "runner_name": runner_name,
                "started_at": started_at,
            }

        full_names = (
            "event-identity",
            "event-router",
            "event-classifier",
            "host-tests",
            "build",
            "extended-host-tests",
            "legacy",
            "summary",
        )
        metadata_running = (
            "event-identity",
            "event-router",
            "metadata-classifier",
            "host-tests",
            "build",
            "summary",
        )
        metadata_skipped_names = (
            "extended-host-tests",
            "legacy",
            "patch-release",
        )
        full_jobs = [
            job_record(index, name)
            for index, name in enumerate(full_names, start=100)
        ]
        full_jobs.append(
            job_record(
                199,
                "patch-release",
                conclusion="skipped",
                runner_name=None,
                started_at=None,
            )
        )
        metadata_jobs = [
            job_record(index, name)
            for index, name in enumerate(metadata_running, start=200)
        ]
        metadata_jobs.extend(
            job_record(
                index,
                name,
                conclusion="skipped",
                runner_name=None,
                # GitHub may stamp this even when no runner executes the job.
                started_at="2026-08-31T00:00:00Z",
            )
            for index, name in enumerate(metadata_skipped_names, start=300)
        )

        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="live-title-probe-json-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            paths = {}
            for stem, run_id, jobs in (
                ("opened", 1, full_jobs),
                ("title", 2, metadata_jobs),
                ("restore", 3, metadata_jobs),
            ):
                run_path = sandbox / f"{stem}.json"
                jobs_path = sandbox / f"{stem}-jobs.json"
                run_path.write_text(
                    json.dumps(run_record()),
                    encoding="utf-8",
                )
                jobs_path.write_text(
                    json.dumps([{"jobs": jobs}]),
                    encoding="utf-8",
                )
                paths[stem] = (run_id, run_path, jobs_path)

            evaluator_command = [
                "/usr/bin/python3",
                "-",
                head_sha,
                base_sha,
                *[
                    value
                    for stem in ("opened", "title", "restore")
                    for value in (
                        str(paths[stem][0]),
                        str(paths[stem][1]),
                        str(paths[stem][2]),
                    )
                ],
            ]
            accepted = subprocess.run(
                evaluator_command,
                cwd=ROOT,
                input=evaluator_source,
                text=True,
                check=False,
                capture_output=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

            for worker_name in metadata_skipped_names[:-1]:
                with self.subTest(started_worker=worker_name):
                    adversarial_jobs = copy.deepcopy(metadata_jobs)
                    started_worker = next(
                        job
                        for job in adversarial_jobs
                        if job["name"] == worker_name
                    )
                    started_worker["conclusion"] = "success"
                    started_worker["runner_name"] = "GitHub Actions attacker"
                    started_worker["started_at"] = "2026-08-31T00:00:01Z"
                    paths["title"][2].write_text(
                        json.dumps([{"jobs": adversarial_jobs}]),
                        encoding="utf-8",
                    )
                    rejected = subprocess.run(
                        evaluator_command,
                        cwd=ROOT,
                        input=evaluator_source,
                        text=True,
                        check=False,
                        capture_output=True,
                    )
                    self.assertNotEqual(rejected.returncode, 0)

        mutations = (
            (
                "candidate-containing-base",
                'gh pr create --head "$probe_branch" --base "$candidate_branch"',
                'gh pr create --head "$probe_branch" --base master',
            ),
            (
                "strict-nonempty-descendant",
                'test "$(git rev-parse "$head_sha^")" = "$candidate_sha"',
                'test "$(git rev-parse "$head_sha^")" != "$candidate_sha"',
            ),
            (
                "no-empty-or-merge-commit",
                'git commit -m "test(ci): add title-only validation probe"',
                'git commit --allow-empty -m "test(ci): add title-only validation probe"',
            ),
            (
                "title-edit-and-restore",
                '-f title="$original_title" > /dev/null\n'
                '   restore_run_id="$(discover_build_run',
                '-f title="$probe_title" > /dev/null\n'
                '   restore_run_id="$(discover_build_run',
            ),
            (
                "three-exact-live-runs",
                'gh run view "$restore_run_id" \\\n'
                '     '
                "--json event,headSha,conclusion,url",
                'printf "restore run not inspected\\n"',
            ),
            (
                "live-procedure-sequence",
                "5. Normalize all three real runs",
                "10. Normalize all three real runs",
            ),
            (
                "bounded-exact-run-watcher",
                'gh run view "$run_id" --json status,conclusion \\\n'
                "       --jq '[.status, (.conclusion // \"\")] | @tsv'",
                'printf "status query omitted\\n"',
            ),
            (
                "bounded-exact-run-watcher",
                '         timeout 90m gh run watch "$run_id" '
                "--interval 30 --exit-status",
                "         return 124",
            ),
            (
                "bounded-exact-run-watcher",
                'timeout 90m gh run watch "$run_id" --interval 30 --exit-status',
                'timeout 89m gh run watch "$run_id" --interval 30 --exit-status',
            ),
            (
                "actual-evaluator-assertions",
                "candidate_evidence.evaluate_candidate_runs(",
                "candidate_evidence.CandidateEvidence(",
            ),
            (
                "bounded-unseen-run-discovery",
                'str(record["id"]) not in prior',
                "True",
            ),
            (
                "fail-fast-trapped-cleanup",
                "set -euo pipefail",
                "set -u",
            ),
            (
                "fail-fast-trapped-cleanup",
                "trap finish_probe EXIT",
                "true # cleanup trap omitted",
            ),
            (
                "complete-probe-cleanup",
                'git push --force-with-lease='
                '"refs/heads/$probe_branch:$probe_head_sha"',
                'printf "remote branch retained\\n"',
            ),
            (
                "cleanup-ownership-cas",
                "push_ownership_intent=true",
                "push_ownership_intent=false",
            ),
            (
                "raw-job-scan",
                "for job in raw_jobs:",
                "for job in []:",
            ),
            (
                "metadata-adapter-runs",
                'assert isinstance(job["runner_name"], str) and job["runner_name"]',
                "assert True",
            ),
            (
                "metadata-worker-no-start",
                'assert job["runner_name"] is None',
                "assert True",
            ),
            (
                "implementation-pr-bootstrap-negative",
                "not a valid\nmetadata-suppression probe",
                "a valid\nmetadata-suppression probe",
            ),
            (
                "validation-only-never-merged",
                "The disposable PR is never merged",
                "The disposable PR may be merged",
            ),
        )
        for expected, old, new in mutations:
            with self.subTest(mutation=expected):
                changed = governance.replace(old, new, 1)
                self.assertNotEqual(changed, governance)
                self.assertIn(expected, live_title_probe_violations(changed))

        identity_assertions = (
            'test "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" '
            '= "$head_sha"',
            'test "$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.sha)" '
            '= "$base_sha"',
        )
        for assertion in identity_assertions:
            positions = [
                match.start()
                for match in re.finditer(re.escape(assertion), governance)
            ]
            self.assertEqual(len(positions), 3)
            for run_index, position in enumerate(positions):
                with self.subTest(assertion=assertion, run=run_index):
                    changed = (
                        governance[:position]
                        + "true # run identity assertion omitted"
                        + governance[position + len(assertion):]
                    )
                    self.assertIn(
                        "three-run-head-base-identity",
                        live_title_probe_violations(changed),
                    )
        for variable in ("opened_run_id", "title_run_id", "restore_run_id"):
            run_assertions = (
                f'test "$(gh run view "${variable}" --json event --jq .event)" '
                '= "pull_request"',
                f'test "$(gh run view "${variable}" --json headSha --jq .headSha)" '
                '= "$head_sha"',
            )
            for assertion in run_assertions:
                with self.subTest(assertion=assertion):
                    self.assertIn(assertion, governance)
                    changed = governance.replace(
                        assertion,
                        "true # exact run identity assertion omitted",
                        1,
                    )
                    self.assertIn(
                        "three-run-head-base-identity",
                        live_title_probe_violations(changed),
                    )

    def test_live_watcher_queries_once_and_rearms_once(self):
        governance = WORKFLOW_GOVERNANCE_PATH.read_text(encoding="utf-8")
        body_case = raw_markdown_section(governance, BODY_EDIT_CASE_HEADING)
        bash_source = "\n".join(
            textwrap.dedent(match.group("body"))
            for match in re.finditer(
                r"^[ ]*```bash\n(?P<body>.*?)^[ ]*```",
                body_case,
                re.DOTALL | re.MULTILINE,
            )
        )
        match = re.search(
            r"(?ms)^watch_build_run\(\) \{\n.*?^\}",
            bash_source,
        )
        self.assertIsNotNone(match)
        watcher = match.group(0)
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)

        cases = (
            ("first-success", 0, 0, "completed", "success", 0, 1, 0),
            ("first-failure", 1, 0, "completed", "failure", 1, 1, 0),
            ("queued-rearm-success", 124, 0, "queued", "", 0, 2, 1),
            ("active-rearm-failure", 124, 1, "in_progress", "", 1, 2, 1),
            ("waiting-second-timeout", 124, 124, "waiting", "", 124, 2, 1),
            ("terminal-after-timeout", 124, 0, "completed", "success", 0, 1, 1),
            ("failed-after-timeout", 124, 0, "completed", "failure", 1, 1, 1),
        )
        with tempfile.TemporaryDirectory(
            prefix="live-watcher-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            for (
                name,
                first,
                second,
                status,
                conclusion,
                expected_result,
                expected_watches,
                expected_queries,
            ) in cases:
                with self.subTest(name=name):
                    watch_count = sandbox / f"{name}-watch"
                    query_count = sandbox / f"{name}-query"
                    watch_count.write_text("", encoding="ascii")
                    query_count.write_text("", encoding="ascii")
                    harness = (
                        watcher
                        + "\n"
                        + r'''
timeout() {
  printf 'watch\n' >> "$WATCH_COUNT"
  count="$(wc -l < "$WATCH_COUNT")"
  if [ "$count" -eq 1 ]; then
    return "$FIRST_STATUS"
  fi
  return "$SECOND_STATUS"
}
gh() {
  test "$1" = run
  test "$2" = view
  test "$3" = 123
  printf 'query\n' >> "$QUERY_COUNT"
  printf '%s\t%s\n' "$RUN_STATUS" "$RUN_CONCLUSION"
}
if watch_build_run 123; then
  result=0
else
  result="$?"
fi
printf '%s\t%s\t%s\n' "$result" \
  "$(wc -l < "$WATCH_COUNT")" "$(wc -l < "$QUERY_COUNT")"
'''
                    )
                    completed = subprocess.run(
                        ["/bin/bash", "-c", harness],
                        env={
                            **os.environ,
                            "FIRST_STATUS": str(first),
                            "QUERY_COUNT": str(query_count),
                            "RUN_CONCLUSION": conclusion,
                            "RUN_STATUS": status,
                            "SECOND_STATUS": str(second),
                            "WATCH_COUNT": str(watch_count),
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(
                        completed.stdout.strip(),
                        f"{expected_result}\t{expected_watches}\t"
                        f"{expected_queries}",
                    )

    def test_tester_facing_case_contract_is_integrated(self):
        _, skill = read_skill()
        skill_contract = (
            "Do not accept a feature request without at least one proposed tester-facing",
            "deterministic reproduction plus at least one",
            "stable case ID and linked feature or bug issue",
            "supported configuration/profile or downloadable artifact",
            "prerequisites, clean starting state",
            "exact actions or inputs",
            "observable expected result",
            "default/disabled or pre-fix negative control",
            "dependencies, conflicts, feature interactions, and save-compatibility",
            "mapping to deterministic host/ROM automation",
            "known limitations and unsupported configurations",
            "issue #54",
            "Manual-only evidence is",
            "visual, audio, or UX judgment",
            "tester-facing case IDs exercised",
            "Close the feature or bug issue only when every required tester-facing case",
        )
        for requirement in skill_contract:
            with self.subTest(surface="skill", requirement=requirement):
                self.assertIn(requirement, skill)

        contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")
        contributing_contract = (
            "Write tester-facing cases",
            "Every accepted feature request and confirmed bug fix",
            "TC-SAVE-001",
            "issue #54",
            "Tester instructions and automated evidence are complementary.",
            "Manual-only judgment is legitimate only",
            "Cases must remain runnable from documented source profiles.",
            "positive and negative actual results",
        )
        for requirement in contributing_contract:
            with self.subTest(surface="contributing", requirement=requirement):
                self.assertIn(requirement, contributing)

        template = PR_TEMPLATE_PATH.read_text(encoding="utf-8")
        template_contract = (
            "Tester-facing procedure",
            "Stable case IDs",
            "Definition/catalog links",
            "Supported configuration/profile or artifact",
            "Exact actions or inputs",
            "Observable expected result",
            "pre-fix negative control",
            "feature interactions, and save expectations",
            "Automation mapping, or precise manual-only reason",
            "Reset/cleanup, known limitations, and unsupported configurations",
            "visual, audio, or UX judgment",
        )
        for requirement in template_contract:
            with self.subTest(surface="template", requirement=requirement):
                self.assertIn(requirement, template)

    def test_manual_handoff_json_contract_and_human_links(self):
        contract = read_manual_handoff_contract()
        self.assertEqual([], compare_contract(
            contract,
            EXPECTED_MANUAL_HANDOFF_CONTRACT,
        ))

        links = {
            SKILL_PATH: "../../manual-testing-handoff.json",
            CONTRIBUTING_PATH: ".github/manual-testing-handoff.json",
            WORKFLOW_GOVERNANCE_PATH: (
                "../../.github/manual-testing-handoff.json"
            ),
        }
        for path, link in links.items():
            with self.subTest(path=str(path)):
                self.assertIn(link, path.read_text(encoding="utf-8"))

        governance = WORKFLOW_GOVERNANCE_PATH.read_text(encoding="utf-8")
        for path in (SKILL_PATH, CONTRIBUTING_PATH):
            with self.subTest(surface=str(path), contract="human lifecycle"):
                self.assertEqual(
                    [],
                    human_handoff_violations(
                        path.read_text(encoding="utf-8")
                    ),
                )
        self.assertEqual(
            [],
            human_handoff_violations(governance, governance=True),
        )
        case = "\n".join(
            read_markdown_section(governance, MANUAL_HANDOFF_CASE_HEADING)
        )
        for heading in (
            "Actions",
            "Expected result",
            "Negative control",
            "Interactions and save compatibility",
            "Automation",
            "Cleanup and limitations",
        ):
            with self.subTest(heading=heading):
                self.assertTrue(read_markdown_section(case, heading))
        self.assertIn(
            f"[`{MANUAL_HANDOFF_QUERY}`]({MANUAL_HANDOFF_QUERY_URL})",
            case,
        )

        registry = json.loads(
            TEST_CASE_REGISTRY_PATH.read_text(encoding="utf-8")
        )
        feature = next(
            item
            for item in registry["features"]
            if item["id"] == "workflow-governance"
        )
        expected_cases = [
            "TC-WORKFLOW-CI-WAIT-001",
            "TC-WORKFLOW-MANUAL-HANDOFF-001",
            "TC-WORKFLOW-STACKED-CI-001",
            "TC-WORKFLOW-BODY-EDIT-001",
            "TC-WORKFLOW-METADATA-EDIT-RACE-001",
            "TC-WORKFLOW-PILOT-BASELINE-001",
        ]
        self.assertEqual(
            [],
            compare_string_membership(
                feature["required_cases"],
                expected_cases,
                "workflow-governance.required_cases",
            ),
        )
        self.assertEqual(
            [],
            compare_string_membership(
                list(reversed(feature["required_cases"])),
                expected_cases,
                "workflow-governance.required_cases",
            ),
        )
        required_case_mutations = {
            "missing": feature["required_cases"][:-1],
            "extra": feature["required_cases"] + ["TC-WORKFLOW-OTHER-001"],
            "duplicate": feature["required_cases"] + [
                feature["required_cases"][0]
            ],
        }
        for mutation, required_cases in required_case_mutations.items():
            with self.subTest(required_cases=mutation):
                self.assertTrue(
                    compare_string_membership(
                        required_cases,
                        expected_cases,
                        "workflow-governance.required_cases",
                    )
                )
        indexed_case = next(
            item
            for item in registry["cases"]
            if item["id"] == "TC-WORKFLOW-MANUAL-HANDOFF-001"
        )
        self.assertEqual(
            indexed_case["document"],
            "docs/test-cases/workflow-governance.md",
        )
        self.assertEqual(indexed_case["feature_id"], "workflow-governance")

    def test_metadata_edit_race_guidance_and_registry(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")
        pilot = WORKFLOW_PILOT_PATH.read_text(encoding="utf-8")
        governance = WORKFLOW_GOVERNANCE_PATH.read_text(encoding="utf-8")
        case = "\n".join(
            read_markdown_section(governance, METADATA_EDIT_RACE_CASE_HEADING)
        )

        for text in (skill, contributing, pilot):
            normalized = normalize_policy(text)
            for requirement in (
                "pr-metadata edit",
                "canonical evidence comment",
                "same-SHA full Build",
            ):
                with self.subTest(requirement=requirement):
                    self.assertIn(normalize_policy(requirement), normalized)
        for requirement in (
            "pr-metadata reconcile",
            "performs no PR metadata mutation",
            "two complete run/job snapshots",
            "completed failed metadata run",
            "repository-owner comment",
            "No ARM runtime test is needed",
            "There is no manual-only criterion",
        ):
            with self.subTest(case_requirement=requirement):
                self.assertIn(normalize_policy(requirement), normalize_policy(case))

        registry = json.loads(
            TEST_CASE_REGISTRY_PATH.read_text(encoding="utf-8")
        )
        feature = next(
            item
            for item in registry["features"]
            if item["id"] == "workflow-governance"
        )
        self.assertIn(
            "https://github.com/laqieer/fireemblem8-expansion/issues/199",
            feature["issue_urls"],
        )
        self.assertIn(
            "TC-WORKFLOW-METADATA-EDIT-RACE-001",
            feature["required_cases"],
        )
        indexed = next(
            item
            for item in registry["cases"]
            if item["id"] == "TC-WORKFLOW-METADATA-EDIT-RACE-001"
        )
        self.assertEqual(indexed["feature_id"], "workflow-governance")
        self.assertEqual(
            indexed["document"],
            "docs/test-cases/workflow-governance.md",
        )

    def test_manual_handoff_human_lifecycle_mutations_fail_closed(self):
        surfaces = (
            (SKILL_PATH.read_text(encoding="utf-8"), False),
            (CONTRIBUTING_PATH.read_text(encoding="utf-8"), False),
            (
                WORKFLOW_GOVERNANCE_PATH.read_text(encoding="utf-8"),
                True,
            ),
        )
        actions = (
            "Require a material visual, audio, or UX criterion",
            "Require automation to be unreliable for that criterion",
            "Apply `waiting-for-manual-testing` to the originating issue and "
            "each open implementation PR",
            "Assign `laqieer` to those targets",
            "Ping `@laqieer` in each comment",
            "Block merge for the manual criterion",
            "Block issue closure for the manual criterion",
            "remove `waiting-for-manual-testing` from the originating issue "
            "and every labeled implementation PR",
            "Remove the temporary `laqieer` assignment",
            "Resume exact-candidate gates and merge automatically",
        )
        contracted_reversals = {
            actions[2]: (
                "Don't apply `waiting-for-manual-testing` to the originating "
                "issue and each open implementation PR"
            ),
            actions[3]: "Won't assign `laqieer` to those targets",
            actions[4]: "Won't ping `@laqieer` in each comment",
            actions[5]: "Can't block merge for the manual criterion",
            actions[6]: "Can't block issue closure for the manual criterion",
            actions[7]: (
                "Can't remove `waiting-for-manual-testing` from the "
                "originating issue and every labeled implementation PR"
            ),
            actions[8]: (
                "Can't remove the temporary `laqieer` assignment"
            ),
            actions[9]: (
                "Can't resume exact-candidate gates and merge automatically"
            ),
        }
        for text, governance in surfaces:
            for action in actions:
                first, remainder = action.split(" ", 1)
                mutations = {
                    "removed": "",
                    "negated": f"Do not {first.lower()} {remainder}",
                    "softened": f"May {first.lower()} {remainder}",
                }
                for mutation, replacement in mutations.items():
                    with self.subTest(
                        governance=governance,
                        action=action,
                        mutation=mutation,
                    ):
                        mutated = replace_whitespace_phrase(
                            text,
                            action,
                            replacement,
                        )
                        self.assertTrue(
                            human_handoff_violations(
                                mutated,
                                governance=governance,
                            )
                        )
            for action, contracted in contracted_reversals.items():
                for apostrophe in ("'", "\u2019"):
                    with self.subTest(
                        governance=governance,
                        action=action,
                        contraction=contracted,
                        apostrophe=apostrophe,
                    ):
                        mutated = replace_whitespace_phrase(
                            text,
                            action,
                            contracted.replace("'", apostrophe),
                        )
                        self.assertTrue(
                            human_handoff_violations(
                                mutated,
                                governance=governance,
                            )
                        )
            for replacement in ("Before accepted evidence", "After rejected evidence"):
                with self.subTest(
                    governance=governance,
                    completion_gate=replacement,
                ):
                    mutated = replace_whitespace_phrase(
                        text,
                        "After accepted evidence",
                        replacement,
                    )
                    self.assertTrue(
                        human_handoff_violations(
                            mutated,
                            governance=governance,
                        )
                    )
            eligibility_reversals = (
                "Allow an immaterial visual, audio, or UX criterion to trigger "
                "the handoff.",
                "Materiality may be optional for handoff activation.",
            )
            for replacement in eligibility_reversals:
                with self.subTest(
                    governance=governance,
                    eligibility=replacement,
                ):
                    mutated = replace_whitespace_phrase(
                        text,
                        "Require a material visual, audio, or UX criterion",
                        replacement,
                    )
                    self.assertTrue(
                        human_handoff_violations(
                            mutated,
                            governance=governance,
                        )
                    )

        for contraction in (
            "don't",
            "doesn't",
            "isn't",
            "aren't",
            "won't",
            "can't",
            "cannot",
            "couldn't",
            "shouldn't",
            "wouldn't",
            "mustn't",
            "hasn't",
            "haven't",
            "hadn't",
        ):
            spellings = (contraction,)
            if "'" in contraction:
                spellings = tuple(
                    contraction.replace("'", apostrophe)
                    for apostrophe in ("'", "\u2018", "\u2019", "\u02bc", "\uff07")
                )
            for spelling in spellings:
                with self.subTest(normalized_contraction=spelling):
                    normalized = normalize_policy(
                        normalize_negative_contractions(spelling)
                    ).split()
                    self.assertIn("not", normalized)
        affirmative = "It's required, we're ready, and they'll proceed."
        self.assertEqual(
            affirmative,
            normalize_negative_contractions(affirmative),
        )

    def test_manual_handoff_contract_mutations_fail_closed(self):
        contract = read_manual_handoff_contract()
        for path in contract_paths(EXPECTED_MANUAL_HANDOFF_CONTRACT):
            with self.subTest(path=".".join(path), mutation="missing"):
                mutated = copy.deepcopy(contract)
                parent, key = contract_parent(mutated, path)
                del parent[key]
                self.assertTrue(compare_contract(
                    mutated,
                    EXPECTED_MANUAL_HANDOFF_CONTRACT,
                ))
        for path in contract_paths(EXPECTED_MANUAL_HANDOFF_CONTRACT):
            parent, key = contract_parent(
                EXPECTED_MANUAL_HANDOFF_CONTRACT,
                path,
            )
            if isinstance(parent[key], dict):
                continue
            with self.subTest(path=".".join(path), mutation="wrong"):
                mutated = copy.deepcopy(contract)
                mutated_parent, mutated_key = contract_parent(mutated, path)
                mutated_parent[mutated_key] = wrong_contract_value(
                    mutated_parent[mutated_key]
                )
                self.assertTrue(compare_contract(
                    mutated,
                    EXPECTED_MANUAL_HANDOFF_CONTRACT,
                ))

        blocker_mutations = {
            "instrumented artifact": (
                "pre_handoff",
                "artifact",
                "instrumented",
            ),
            "missing positive role": (
                "pre_handoff",
                "required_roles",
                ["control"],
            ),
            "missing control role": (
                "pre_handoff",
                "required_roles",
                ["positive"],
            ),
            "missing path identity": (
                "pre_handoff",
                "required_identity_fields",
                ["sha256"],
            ),
            "missing hash identity": (
                "pre_handoff",
                "required_identity_fields",
                ["path"],
            ),
            "skip rendering": ("pre_handoff", "render_each", False),
            "skip inspection": ("pre_handoff", "inspect_each", False),
            "static UI evidence": (
                "pre_handoff",
                "static_ui",
                {
                    "evidence": "arbitrary_image",
                    "source": "emulator",
                    "deterministic": True,
                },
            ),
            "static UI source": (
                "pre_handoff",
                "static_ui",
                {
                    "evidence": "screenshot",
                    "source": "desktop",
                    "deterministic": True,
                },
            ),
            "static UI determinism": (
                "pre_handoff",
                "static_ui",
                {
                    "evidence": "screenshot",
                    "source": "emulator",
                    "deterministic": False,
                },
            ),
            "A/V evidence": (
                "pre_handoff",
                "time_dependent_or_av",
                {
                    "evidence": "audio_only",
                    "source": "emulator",
                    "synchronized": True,
                },
            ),
            "A/V source": (
                "pre_handoff",
                "time_dependent_or_av",
                {
                    "evidence": "av_clip",
                    "source": "desktop",
                    "synchronized": True,
                },
            ),
            "A/V synchronization": (
                "pre_handoff",
                "time_dependent_or_av",
                {
                    "evidence": "av_clip",
                    "source": "emulator",
                    "synchronized": False,
                },
            ),
            "permissive activation": ("activation", "required", False),
            "activation label": (
                "activation",
                "label",
                "other-label",
            ),
            "activation assignee": (
                "activation",
                "assignee",
                "other-tester",
            ),
            "merge hold": ("hold", "merge", False),
            "closure hold": ("hold", "issue_closure", False),
            "cleanup label": (
                "completion",
                "remove_label",
                "other-label",
            ),
            "cleanup target": (
                "completion",
                "remove_label_from",
                ["originating_issue"],
            ),
            "cleanup assignee": (
                "completion",
                "remove_temporary_assignee",
                "other-tester",
            ),
            "cleanup assignee target": (
                "completion",
                "remove_temporary_assignee_from",
                ["originating_issue"],
            ),
            "resume gates": (
                "completion",
                "resume_exact_candidate_gates",
                False,
            ),
            "resume merge": ("completion", "resume_merge", False),
            "empty queue notification": (
                "queue",
                "notify_when_empty",
                True,
            ),
            "static queue": ("queue", "live_cardinality", "empty"),
            "relationship source": (
                "queue",
                "relationship_source",
                "issue_declared_prs",
            ),
        }
        for name, (section, key, value) in blocker_mutations.items():
            with self.subTest(blocker=name):
                mutated = copy.deepcopy(contract)
                mutated[section][key] = value
                self.assertTrue(compare_contract(
                    mutated,
                    EXPECTED_MANUAL_HANDOFF_CONTRACT,
                ))
        for value in ("true", 1, [], {}):
            with self.subTest(materiality_type=type(value).__name__):
                mutated = copy.deepcopy(contract)
                mutated["eligibility"]["material"] = value
                self.assertTrue(compare_contract(
                    mutated,
                    EXPECTED_MANUAL_HANDOFF_CONTRACT,
                ))
        comment_mutations = {
            "wrong comment mention": ("mention", "@other-tester"),
            "paragraph steps format": ("steps_format", "paragraph"),
            "zero minimum steps": ("minimum_steps", 0),
        }
        for name, (key, value) in comment_mutations.items():
            with self.subTest(blocker=name):
                mutated = copy.deepcopy(contract)
                mutated["activation"]["comment"][key] = value
                self.assertTrue(compare_contract(
                    mutated,
                    EXPECTED_MANUAL_HANDOFF_CONTRACT,
                ))
        misplaced_mention = copy.deepcopy(contract)
        misplaced_mention["activation"]["mention"] = (
            misplaced_mention["activation"]["comment"].pop("mention")
        )
        self.assertTrue(compare_contract(
            misplaced_mention,
            EXPECTED_MANUAL_HANDOFF_CONTRACT,
        ))
        completion_outcome_mutations = {
            "cleanup accepts rejected": ("cleanup_allowed_outcome", "rejected"),
            "resume accepts rejected": ("resume_allowed_outcome", "rejected"),
        }
        for name, (key, value) in completion_outcome_mutations.items():
            with self.subTest(blocker=name):
                mutated = copy.deepcopy(contract)
                mutated["completion"]["comment"][key] = value
                self.assertTrue(compare_contract(
                    mutated,
                    EXPECTED_MANUAL_HANDOFF_CONTRACT,
                ))
        for field in (
            "retain_merge_hold",
            "retain_closure_hold",
            "remain_actionable",
        ):
            with self.subTest(rejected_outcome=field):
                mutated = copy.deepcopy(contract)
                mutated["completion"]["comment"]["rejected_outcome"][
                    field
                ] = False
                self.assertTrue(compare_contract(
                    mutated,
                    EXPECTED_MANUAL_HANDOFF_CONTRACT,
                ))
        old_relationship_key = copy.deepcopy(contract)
        old_relationship_key["queue"].pop(
            "require_every_linked_open_implementation_pr"
        )
        old_relationship_key["queue"][
            "require_every_declared_open_implementation_pr"
        ] = True
        old_key_failures = compare_contract(
            old_relationship_key,
            EXPECTED_MANUAL_HANDOFF_CONTRACT,
        )
        self.assertTrue(any(
            "missing require_every_linked_open_implementation_pr" in failure
            for failure in old_key_failures
        ))
        self.assertTrue(any(
            "unexpected require_every_declared_open_implementation_pr"
            in failure
            for failure in old_key_failures
        ))

        list_paths = [
            path
            for path in contract_paths(EXPECTED_MANUAL_HANDOFF_CONTRACT)
            if isinstance(contract_parent(contract, path)[0][path[-1]], list)
        ]
        for path in list_paths:
            expected_parent, expected_key = contract_parent(contract, path)
            expected_values = expected_parent[expected_key]
            with self.subTest(path=".".join(path), mutation="permutation"):
                permuted = copy.deepcopy(contract)
                parent, key = contract_parent(permuted, path)
                parent[key] = list(reversed(parent[key]))
                self.assertEqual([], compare_contract(
                    permuted,
                    EXPECTED_MANUAL_HANDOFF_CONTRACT,
                ))
            list_mutations = {
                "missing member": expected_values[:-1],
                "extra member": expected_values + ["unexpected_member"],
                "duplicate member": expected_values + [expected_values[0]],
            }
            for mutation, values in list_mutations.items():
                with self.subTest(path=".".join(path), mutation=mutation):
                    mutated = copy.deepcopy(contract)
                    parent, key = contract_parent(mutated, path)
                    parent[key] = values
                    failures = compare_contract(
                        mutated,
                        EXPECTED_MANUAL_HANDOFF_CONTRACT,
                    )
                    self.assertTrue(failures)
                    self.assertTrue(
                        all(".".join(path) in failure for failure in failures)
                    )
        required_artifact_fields = {
            "positive_artifact_path",
            "positive_artifact_sha256",
            "control_artifact_path",
            "control_artifact_sha256",
        }
        for field in required_artifact_fields:
            with self.subTest(comment_field=field):
                mutated = copy.deepcopy(contract)
                del mutated["activation"]["comment"]["fields"][field]
                self.assertTrue(compare_contract(
                    mutated,
                    EXPECTED_MANUAL_HANDOFF_CONTRACT,
                ))

    def test_manual_handoff_comment_payload_is_structured(self):
        contract = read_manual_handoff_contract()
        comment = valid_handoff_comment(contract)
        self.assertEqual([], validate_handoff_comment(contract, comment))

        multiple_steps = copy.deepcopy(comment)
        multiple_steps["steps"] = [
            "Open the artifact.",
            "Perform the documented comparison.",
        ]
        self.assertEqual(
            [],
            validate_handoff_comment(contract, multiple_steps),
        )
        same_hash_distinct_paths = copy.deepcopy(comment)
        same_hash_distinct_paths["control_artifact_sha256"] = (
            same_hash_distinct_paths["positive_artifact_sha256"]
        )
        self.assertEqual(
            [],
            validate_handoff_comment(contract, same_hash_distinct_paths),
        )
        same_path_distinct_hashes = copy.deepcopy(comment)
        same_path_distinct_hashes["control_artifact_path"] = (
            "build\\enabled\\fireemblem8.gba"
        )
        self.assertEqual(
            [],
            validate_handoff_comment(contract, same_path_distinct_hashes),
        )
        same_identity = copy.deepcopy(comment)
        same_identity["control_artifact_path"] = (
            "build\\enabled\\.\\fireemblem8.gba"
        )
        same_identity["control_artifact_sha256"] = (
            same_identity["positive_artifact_sha256"]
        )
        self.assertTrue(validate_handoff_comment(contract, same_identity))

        invalid_comments = {
            "missing mention": {
                key: value for key, value in comment.items()
                if key != "mention"
            },
            "wrong mention": dict(comment, mention="@other-tester"),
            "mention only elsewhere": {
                **{
                    key: value for key, value in comment.items()
                    if key != "mention"
                },
                "activation_mention": "@laqieer",
            },
            "paragraph steps": dict(
                comment,
                steps="Open the artifact and perform the comparison.",
            ),
            "empty steps": dict(comment, steps=[]),
            "blank step": dict(comment, steps=["  "]),
            "non-string step": dict(comment, steps=[1]),
        }
        for hold in ("merge_hold", "closure_hold"):
            for value in (False, None, "true", 0):
                invalid_comments[f"{hold}={value!r}"] = dict(
                    comment,
                    **{hold: value},
                )
        for scenario, invalid in invalid_comments.items():
            with self.subTest(scenario=scenario):
                self.assertTrue(
                    validate_handoff_comment(contract, invalid)
                )

        for field in contract["activation"]["comment"]["fields"]:
            with self.subTest(field=field, mutation="missing"):
                missing = copy.deepcopy(comment)
                del missing[field]
                self.assertTrue(
                    validate_handoff_comment(contract, missing)
                )

        wrong_types = (False, [], {}, 7)
        for field, specification in contract["activation"]["comment"][
            "fields"
        ].items():
            for value in wrong_types:
                expected_type = specification["type"]
                if (
                    (expected_type == "boolean" and type(value) is bool)
                    or (expected_type == "array" and isinstance(value, list))
                ):
                    continue
                with self.subTest(
                    field=field,
                    mutation="wrong type",
                    value=repr(value),
                ):
                    invalid = copy.deepcopy(comment)
                    invalid[field] = value
                    self.assertTrue(
                        validate_handoff_comment(contract, invalid)
                    )

        formatted_mutations = {
            "case ID missing TC prefix": ("case_id", "WORKFLOW-001"),
            "case ID unstable suffix": ("case_id", "TC-WORKFLOW-MANUAL"),
            "commit short": ("commit", "a" * 39),
            "commit nonhex": ("commit", "g" * 40),
            "commit uppercase": ("commit", "A" * 40),
            "positive hash short": (
                "positive_artifact_sha256",
                "b" * 63,
            ),
            "positive hash nonhex": (
                "positive_artifact_sha256",
                "g" * 64,
            ),
            "positive hash uppercase": (
                "positive_artifact_sha256",
                "B" * 64,
            ),
            "control hash short": (
                "control_artifact_sha256",
                "c" * 63,
            ),
            "control hash nonhex": (
                "control_artifact_sha256",
                "z" * 64,
            ),
            "control hash uppercase": (
                "control_artifact_sha256",
                "C" * 64,
            ),
        }
        for scenario, (field, value) in formatted_mutations.items():
            with self.subTest(scenario=scenario):
                invalid = copy.deepcopy(comment)
                invalid[field] = value
                self.assertTrue(
                    validate_handoff_comment(contract, invalid)
                )

        for field in (
            "positive_artifact_path",
            "control_artifact_path",
            "environment",
            "clean_state",
            "expected",
            "requested_judgment",
        ):
            with self.subTest(field=field, mutation="whitespace"):
                invalid = copy.deepcopy(comment)
                invalid[field] = " \t "
                self.assertTrue(
                    validate_handoff_comment(contract, invalid)
                )

    def test_manual_handoff_live_queue_relationships(self):
        contract = read_manual_handoff_contract()

        def issue(url, **overrides):
            item = {
                "url": url,
                "kind": "issue",
                "state": "open",
                "manual_pending": True,
                "label": contract["activation"]["label"],
                "assignee": contract["activation"]["assignee"],
                "comment": valid_handoff_comment(contract),
            }
            item.update(overrides)
            return item

        def pull(url, origin_url, **overrides):
            item = {
                "url": url,
                "kind": "pr",
                "state": "open",
                "manual_pending": True,
                "label": contract["activation"]["label"],
                "assignee": contract["activation"]["assignee"],
                "comment": valid_handoff_comment(contract),
                "origin_url": origin_url,
            }
            item.update(overrides)
            return item

        issue_url = (
            "https://github.com/laqieer/fireemblem8-expansion/issues/171"
        )
        pr_one = "https://github.com/laqieer/fireemblem8-expansion/pull/172"
        pr_two = "https://github.com/laqieer/fireemblem8-expansion/pull/173"
        closed_pr = (
            "https://github.com/laqieer/fireemblem8-expansion/pull/174"
        )
        for kind, url in (("issue", issue_url), ("pr", pr_one)):
            for state in contract["queue"]["item_schema"]["state_enum"]:
                with self.subTest(kind=kind, supported_state=state):
                    self.assertEqual(
                        [],
                        validate_manual_item_shape(
                            contract,
                            {
                                "kind": kind,
                                "url": url,
                                "state": state,
                                "manual_pending": state == "open",
                            },
                        ),
                    )
        missing_origin_pr = pull(pr_one, issue_url)
        del missing_origin_pr["origin_url"]
        positive = {
            "issue only": (
                (issue(issue_url),),
                (),
            ),
            "one open PR": (
                (
                    issue(issue_url),
                    pull(pr_one, issue_url),
                ),
                ({
                    "issue_url": issue_url,
                    "pr_url": pr_one,
                    "state": "open",
                },),
            ),
            "multiple open PRs and closed PR excluded": (
                (
                    issue(issue_url),
                    pull(pr_one, issue_url),
                    pull(pr_two, issue_url),
                    pull(
                        closed_pr,
                        issue_url,
                        state="closed",
                        manual_pending=False,
                        received_label=True,
                        label=None,
                        assignee=None,
                        label_removed=True,
                        temporary_assignee_removed=True,
                    ),
                ),
                (
                    {
                        "issue_url": issue_url,
                        "pr_url": pr_one,
                        "state": "open",
                    },
                    {
                        "issue_url": issue_url,
                        "pr_url": pr_two,
                        "state": "open",
                    },
                    {
                        "issue_url": issue_url,
                        "pr_url": closed_pr,
                        "state": "closed",
                    },
                ),
            ),
            "unrelated linked PR ignored": (
                (issue(issue_url),),
                ({
                    "issue_url": (
                        "https://github.com/laqieer/"
                        "fireemblem8-expansion/issues/999"
                    ),
                    "pr_url": (
                        "https://github.com/laqieer/"
                        "fireemblem8-expansion/pull/999"
                    ),
                    "state": "open",
                },),
            ),
            "only closed linked PR": (
                (issue(issue_url),),
                ({
                    "issue_url": issue_url,
                    "pr_url": closed_pr,
                    "state": "closed",
                },),
            ),
        }
        for scenario, (items, relationships) in positive.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(
                    [],
                    validate_live_manual_queue(
                        contract,
                        items,
                        relationships,
                    ),
                )

        negative = {
            "orphan PR": (
                (pull(pr_one, issue_url),),
                (),
            ),
            "wrong-origin orphan": (
                (
                    issue(issue_url),
                    pull(
                        pr_one,
                        "https://github.com/laqieer/"
                        "fireemblem8-expansion/issues/999",
                    ),
                ),
                ({
                    "issue_url": issue_url,
                    "pr_url": pr_one,
                    "state": "open",
                },),
            ),
            "missing PR origin": (
                (
                    issue(issue_url),
                    missing_origin_pr,
                ),
                (),
            ),
            "null PR origin": (
                (
                    issue(issue_url),
                    pull(pr_one, None),
                ),
                (),
            ),
            "empty PR origin": (
                (
                    issue(issue_url),
                    pull(pr_one, ""),
                ),
                (),
            ),
            "list PR origin": (
                (
                    issue(issue_url),
                    pull(pr_one, []),
                ),
                (),
            ),
            "object PR origin": (
                (
                    issue(issue_url),
                    pull(pr_one, {}),
                ),
                (),
            ),
            "arbitrary PR origin": (
                (
                    issue(issue_url),
                    pull(pr_one, "not-a-url"),
                ),
                (),
            ),
            "PR URL used as origin": (
                (
                    issue(issue_url),
                    pull(pr_one, pr_two),
                ),
                (),
            ),
            "cross-repository PR origin": (
                (
                    issue(issue_url),
                    pull(
                        pr_one,
                        "https://github.com/other/repository/issues/171",
                    ),
                ),
                (),
            ),
            "missing independently discovered open PR": (
                (issue(issue_url),),
                ({
                    "issue_url": issue_url,
                    "pr_url": pr_one,
                    "state": "open",
                },),
            ),
            "unlabeled independently discovered open PR": (
                (
                    issue(issue_url),
                    pull(pr_one, issue_url, label="other-label"),
                ),
                ({
                    "issue_url": issue_url,
                    "pr_url": pr_one,
                    "state": "open",
                },),
            ),
            "linked PR missing its own comment": (
                (
                    issue(issue_url),
                    pull(pr_one, issue_url, comment=None),
                    pull(pr_two, issue_url),
                ),
                (
                    {
                        "issue_url": issue_url,
                        "pr_url": pr_one,
                        "state": "open",
                    },
                    {
                        "issue_url": issue_url,
                        "pr_url": pr_two,
                        "state": "open",
                    },
                ),
            ),
            "linked PR has false hold": (
                (
                    issue(issue_url),
                    pull(
                        pr_one,
                        issue_url,
                        comment=dict(
                            valid_handoff_comment(contract),
                            merge_hold=False,
                        ),
                    ),
                ),
                ({
                    "issue_url": issue_url,
                    "pr_url": pr_one,
                    "state": "open",
                },),
            ),
            "self-declared list cannot hide linked PR": (
                (issue(issue_url, open_pr_urls=[]),),
                ({
                    "issue_url": issue_url,
                    "pr_url": pr_one,
                    "state": "open",
                },),
            ),
            "unlinked open PR": (
                (
                    issue(issue_url),
                    pull(pr_one, issue_url),
                ),
                (),
            ),
            "duplicate": (
                (issue(issue_url), issue(issue_url)),
                (),
            ),
            "wrong label": (
                (issue(issue_url, label="other-label"),),
                (),
            ),
            "wrong assignee": (
                (issue(issue_url, assignee="other-tester"),),
                (),
            ),
            "stale label": (
                (issue(
                    issue_url,
                    manual_pending=False,
                    assignee=None,
                ),),
                (),
            ),
            "stale assignee": (
                (issue(
                    issue_url,
                    manual_pending=False,
                    label=None,
                ),),
                (),
            ),
            "string other ownership": (
                (issue(issue_url, other_ownership="false"),),
                (),
            ),
            "integer other ownership": (
                (issue(issue_url, other_ownership=1),),
                (),
            ),
            "integer received label": (
                (issue(issue_url, received_label=0),),
                (),
            ),
            "string received label": (
                (issue(issue_url, received_label="false"),),
                (),
            ),
            "completed issue cannot legitimize pending PR": (
                (
                    issue(
                        issue_url,
                        manual_pending=False,
                        label=None,
                        assignee=None,
                    ),
                    pull(pr_one, issue_url),
                ),
                ({
                    "issue_url": issue_url,
                    "pr_url": pr_one,
                    "state": "open",
                },),
            ),
            "completed PR cannot satisfy pending issue": (
                (
                    issue(issue_url),
                    pull(
                        pr_one,
                        issue_url,
                        manual_pending=False,
                        label=None,
                        assignee=None,
                    ),
                ),
                ({
                    "issue_url": issue_url,
                    "pr_url": pr_one,
                    "state": "open",
                },),
            ),
            "closed labeled PR has stale cleanup state": (
                (
                    issue(issue_url),
                    pull(
                        closed_pr,
                        issue_url,
                        state="closed",
                        manual_pending=False,
                        received_label=True,
                    ),
                ),
                ({
                    "issue_url": issue_url,
                    "pr_url": closed_pr,
                    "state": "closed",
                },),
            ),
            "closed PR remains pending": (
                (
                    issue(issue_url),
                    pull(
                        closed_pr,
                        issue_url,
                        state="closed",
                        manual_pending=True,
                        label_removed=True,
                        temporary_assignee_removed=True,
                    ),
                ),
                ({
                    "issue_url": issue_url,
                    "pr_url": closed_pr,
                    "state": "closed",
                },),
            ),
            "superseded PR has stale temporary assignee": (
                (
                    issue(issue_url),
                    pull(
                        pr_two,
                        issue_url,
                        state="superseded",
                        manual_pending=False,
                        received_label=True,
                        label=None,
                    ),
                ),
                ({
                    "issue_url": issue_url,
                    "pr_url": pr_two,
                    "state": "closed",
                },),
            ),
        }
        for scenario, (items, relationships) in negative.items():
            with self.subTest(scenario=scenario):
                failures = validate_live_manual_queue(
                    contract,
                    items,
                    relationships,
                )
                self.assertTrue(failures)
                if "PR origin" in scenario:
                    self.assertTrue(any(
                        "PR item" in failure for failure in failures
                    ))
                if scenario == "wrong-origin orphan":
                    self.assertTrue(any(
                        "orphan PR" in failure for failure in failures
                    ))

        def mutate_item(item, field, value):
            mutated = copy.deepcopy(item)
            if value is _MISSING:
                mutated.pop(field)
            else:
                mutated[field] = value
            return mutated

        base_issue = issue(issue_url)
        item_shape_mutations = {
            "missing kind": mutate_item(base_issue, "kind", _MISSING),
            "null kind": mutate_item(base_issue, "kind", None),
            "misspelled kind": mutate_item(base_issue, "kind", "pull_request"),
            "non-string kind": mutate_item(base_issue, "kind", 1),
            "missing URL": mutate_item(base_issue, "url", _MISSING),
            "null URL": mutate_item(base_issue, "url", None),
            "malformed URL": mutate_item(
                base_issue,
                "url",
                "https://example.invalid/issues/171",
            ),
            "issue with PR URL": mutate_item(base_issue, "url", pr_one),
            "missing state": mutate_item(base_issue, "state", _MISSING),
            "null state": mutate_item(base_issue, "state", None),
            "misspelled state": mutate_item(base_issue, "state", "pending"),
            "non-string state": mutate_item(base_issue, "state", 1),
            "missing pending flag": mutate_item(
                base_issue,
                "manual_pending",
                _MISSING,
            ),
            "non-boolean pending flag": mutate_item(
                base_issue,
                "manual_pending",
                "true",
            ),
            "PR with issue URL": {
                **pull(pr_one, issue_url),
                "url": issue_url,
            },
        }
        for scenario, invalid_item in item_shape_mutations.items():
            with self.subTest(item_shape=scenario):
                failures = validate_live_manual_queue(
                    contract,
                    (invalid_item,),
                    (),
                )
                self.assertTrue(
                    any("item[0]" in failure for failure in failures)
                )

        relationship = {
            "issue_url": issue_url,
            "pr_url": pr_one,
            "state": "open",
        }
        relationship_mutations = {}
        for field in ("issue_url", "pr_url", "state"):
            missing = dict(relationship)
            missing.pop(field)
            relationship_mutations[f"missing {field}"] = (missing,)
            relationship_mutations[f"null {field}"] = ({
                **relationship,
                field: None,
            },)
        relationship_mutations.update({
            "extra field": ({
                **relationship,
                "kind": "pr",
            },),
            "malformed issue URL": ({
                **relationship,
                "issue_url": "https://example.invalid/issues/171",
            },),
            "malformed PR URL": ({
                **relationship,
                "pr_url": "https://example.invalid/pull/172",
            },),
            "swapped URL kinds": ({
                "issue_url": pr_one,
                "pr_url": issue_url,
                "state": "open",
            },),
            "misspelled state": ({
                **relationship,
                "state": "superseded",
            },),
            "non-string state": ({
                **relationship,
                "state": 1,
            },),
            "malformed closed relationship": ({
                **relationship,
                "state": "closed",
                "pr_url": "not-a-url",
            },),
            "duplicate relationship": (
                relationship,
                dict(relationship),
            ),
            "conflicting issue relationship": (
                relationship,
                {
                    **relationship,
                    "issue_url": (
                        "https://github.com/laqieer/"
                        "fireemblem8-expansion/issues/999"
                    ),
                },
            ),
            "conflicting state relationship": (
                relationship,
                {
                    **relationship,
                    "state": "closed",
                },
            ),
        })
        for scenario, invalid_relationships in relationship_mutations.items():
            with self.subTest(relationship_shape=scenario):
                failures = validate_live_manual_queue(
                    contract,
                    (issue(issue_url), pull(pr_one, issue_url)),
                    invalid_relationships,
                )
                self.assertTrue(
                    any(
                        "relationship[" in failure
                        for failure in failures
                    )
                )

    def test_manual_handoff_completion_cleans_labeled_history(self):
        contract = read_manual_handoff_contract()
        issue_url = (
            "https://github.com/laqieer/fireemblem8-expansion/issues/171"
        )
        open_pr = "https://github.com/laqieer/fireemblem8-expansion/pull/172"
        closed_pr = "https://github.com/laqieer/fireemblem8-expansion/pull/173"
        superseded_pr = (
            "https://github.com/laqieer/fireemblem8-expansion/pull/174"
        )
        never_labeled_pr = (
            "https://github.com/laqieer/fireemblem8-expansion/pull/175"
        )
        activation_comment = valid_handoff_comment(contract)
        history = (
            {
                "url": issue_url,
                "kind": "issue",
                "state": "open",
                "manual_pending": False,
                "received_label": True,
                "label": None,
                "assignee": None,
                "label_removed": True,
                "temporary_assignee_removed": True,
                "comment": activation_comment,
            },
            {
                "url": open_pr,
                "kind": "pr",
                "state": "open",
                "manual_pending": False,
                "received_label": True,
                "label": None,
                "assignee": None,
                "label_removed": True,
                "temporary_assignee_removed": True,
                "comment": activation_comment,
                "current_head_sha": "a" * 40,
            },
            {
                "url": closed_pr,
                "kind": "pr",
                "state": "closed",
                "manual_pending": False,
                "received_label": True,
                "label": None,
                "assignee": None,
                "label_removed": True,
                "temporary_assignee_removed": True,
                "comment": activation_comment,
            },
            {
                "url": superseded_pr,
                "kind": "pr",
                "state": "superseded",
                "manual_pending": False,
                "received_label": True,
                "label": None,
                "assignee": "laqieer",
                "other_ownership": True,
                "ownership_reason": "Maintainer owns the superseded PR.",
                "label_removed": True,
                "temporary_assignee_removed": False,
                "comment": activation_comment,
            },
            {
                "url": never_labeled_pr,
                "kind": "pr",
                "state": "closed",
                "manual_pending": False,
                "received_label": False,
                "label": None,
                "assignee": "laqieer",
                "other_ownership": True,
                "ownership_reason": "Maintainer owns this historical PR.",
                "label_removed": False,
                "temporary_assignee_removed": False,
            },
        )
        label_cleanup_urls = [
            issue_url,
            open_pr,
            closed_pr,
            superseded_pr,
        ]
        assignee_cleanup_urls = [
            issue_url,
            open_pr,
            closed_pr,
        ]
        cleanup = {
            "label": "waiting-for-manual-testing",
            "assignee": "laqieer",
            "remove_label_from": label_cleanup_urls,
            "remove_temporary_assignee_from": assignee_cleanup_urls,
        }
        completion_comments = {
            url: valid_completion_comment(contract, activation_comment)
            for url in label_cleanup_urls
        }

        def cleanup_violations(
            history_value=history,
            cleanup_value=cleanup,
            comments=completion_comments,
        ):
            return validate_completion_cleanup(
                contract,
                history_value,
                cleanup_value,
                comments,
            )

        self.assertEqual(
            [],
            cleanup_violations(),
        )
        issue_only_cleanup = {
            "label": "waiting-for-manual-testing",
            "assignee": "laqieer",
            "remove_label_from": [issue_url],
            "remove_temporary_assignee_from": [issue_url],
        }
        self.assertEqual(
            [],
            validate_completion_cleanup(
                contract,
                (history[0],),
                issue_only_cleanup,
                {issue_url: completion_comments[issue_url]},
            ),
        )

        fresh_head_history = list(copy.deepcopy(history))
        fresh_head_comment = valid_handoff_comment(contract)
        fresh_head_comment["commit"] = "d" * 40
        fresh_head_history[1]["comment"] = fresh_head_comment
        fresh_head_history[1]["current_head_sha"] = "d" * 40
        fresh_head_comments = copy.deepcopy(completion_comments)
        fresh_head_comments[open_pr] = valid_completion_comment(
            contract,
            fresh_head_comment,
        )
        self.assertEqual(
            [],
            cleanup_violations(
                history_value=fresh_head_history,
                comments=fresh_head_comments,
            ),
        )

        for scenario, current_head in {
            "missing": _MISSING,
            "null": None,
            "short": "a" * 39,
            "nonhex": "g" * 40,
            "uppercase": "A" * 40,
            "changed": "d" * 40,
        }.items():
            with self.subTest(open_pr_head=scenario):
                invalid_history = list(copy.deepcopy(history))
                if current_head is _MISSING:
                    invalid_history[1].pop("current_head_sha")
                else:
                    invalid_history[1]["current_head_sha"] = current_head
                self.assertTrue(
                    cleanup_violations(history_value=invalid_history)
                )
        identical_history = history + (copy.deepcopy(history[1]),)
        identical_failures = cleanup_violations(
            history_value=identical_history,
        )
        self.assertTrue(any(
            "duplicate history item" in failure
            for failure in identical_failures
        ))
        contradictory_record = copy.deepcopy(history[1])
        contradictory_record["received_label"] = False
        contradictory_history = history + (contradictory_record,)
        contradictory_failures = cleanup_violations(
            history_value=contradictory_history,
        )
        self.assertTrue(any(
            "contradictory duplicate history item" in failure
            for failure in contradictory_failures
        ))
        accepted_evidence_urls = {
            "issue comment": issue_url + "#issuecomment-123456",
            "pull comment": open_pr + "#issuecomment-123456",
            "pull review": open_pr + "#discussion_r123456",
            "Actions run": (
                "https://github.com/laqieer/fireemblem8-expansion/"
                "actions/runs/123456"
            ),
            "Actions artifact": (
                "https://github.com/laqieer/fireemblem8-expansion/"
                "actions/runs/123456/artifacts/789"
            ),
            "commit-pinned blob": (
                "https://github.com/laqieer/fireemblem8-expansion/blob/"
                + "a" * 40
                + "/reports/manual-result.json"
            ),
            "user attachment": (
                "https://github.com/user-attachments/assets/"
                "11111111-2222-3333-4444-555555555555"
            ),
        }
        for shape, evidence_url in accepted_evidence_urls.items():
            with self.subTest(evidence_shape=shape):
                valid_evidence = copy.deepcopy(completion_comments)
                valid_evidence[issue_url]["evidence_url"] = evidence_url
                self.assertEqual(
                    [],
                    cleanup_violations(comments=valid_evidence),
                )

        missing_completion_comment = copy.deepcopy(completion_comments)
        del missing_completion_comment[closed_pr]
        self.assertTrue(cleanup_violations(
            comments=missing_completion_comment,
        ))
        unrelated_completion_comment = copy.deepcopy(completion_comments)
        unrelated_completion_comment[never_labeled_pr] = (
            valid_completion_comment(contract, activation_comment)
        )
        self.assertTrue(cleanup_violations(
            comments=unrelated_completion_comment,
        ))

        completion_payload_mutations = {
            "blank result": ("actual_result", " "),
            "result boolean": ("actual_result", False),
            "result list": ("actual_result", []),
            "result object": ("actual_result", {}),
            "result integer": ("actual_result", 1),
            "evidence null": ("evidence_url", None),
            "evidence list": ("evidence_url", []),
            "evidence object": ("evidence_url", {}),
            "evidence integer": ("evidence_url", 1),
            "evidence malformed": ("evidence_url", "not-a-url"),
            "bare issue page": ("evidence_url", issue_url),
            "bare PR page": ("evidence_url", open_pr),
            "evidence unrelated": (
                "evidence_url",
                "https://github.com/other/repository/issues/1",
            ),
            "unsupported issue anchor": (
                "evidence_url",
                issue_url + "#discussion_r123456",
            ),
            "unsupported PR anchor": (
                "evidence_url",
                open_pr + "#files",
            ),
            "blob commit mismatch": (
                "evidence_url",
                "https://github.com/laqieer/fireemblem8-expansion/blob/"
                + "d" * 40
                + "/reports/manual-result.json",
            ),
            "case mismatch": ("case_id", "TC-WORKFLOW-OTHER-001"),
            "commit mismatch": ("commit", "d" * 40),
            "rejected outcome": ("outcome", "rejected"),
            "unsupported outcome": ("outcome", "failed"),
            "outcome null": ("outcome", None),
            "outcome boolean": ("outcome", False),
            "outcome list": ("outcome", []),
            "outcome object": ("outcome", {}),
            "outcome integer": ("outcome", 1),
        }
        for scenario, (field, value) in completion_payload_mutations.items():
            with self.subTest(completion_payload=scenario):
                mutated_comments = copy.deepcopy(completion_comments)
                mutated_comments[closed_pr][field] = value
                self.assertTrue(cleanup_violations(
                    comments=mutated_comments,
                ))
        for field in contract["completion"]["comment"]["fields"]:
            with self.subTest(completion_payload=f"missing {field}"):
                mutated_comments = copy.deepcopy(completion_comments)
                del mutated_comments[closed_pr][field]
                self.assertTrue(cleanup_violations(
                    comments=mutated_comments,
                ))
        missing_activation_binding = copy.deepcopy(history)
        del missing_activation_binding[2]["comment"]
        self.assertTrue(cleanup_violations(
            history_value=missing_activation_binding,
        ))
        for omitted_url in (closed_pr, superseded_pr):
            with self.subTest(omitted=omitted_url):
                mutated = copy.deepcopy(cleanup)
                mutated["remove_label_from"].remove(omitted_url)
                self.assertTrue(cleanup_violations(cleanup_value=mutated))
        missing_closed_assignee = copy.deepcopy(cleanup)
        missing_closed_assignee["remove_temporary_assignee_from"].remove(
            closed_pr
        )
        self.assertTrue(cleanup_violations(
            cleanup_value=missing_closed_assignee,
        ))
        independent_owner_cleanup = copy.deepcopy(cleanup)
        independent_owner_cleanup["remove_temporary_assignee_from"].append(
            superseded_pr
        )
        self.assertTrue(cleanup_violations(
            cleanup_value=independent_owner_cleanup,
        ))
        extra = copy.deepcopy(cleanup)
        extra["remove_label_from"].append(never_labeled_pr)
        self.assertTrue(cleanup_violations(cleanup_value=extra))
        duplicate = copy.deepcopy(cleanup)
        duplicate["remove_label_from"].append(issue_url)
        self.assertTrue(cleanup_violations(cleanup_value=duplicate))

        stale_closed = copy.deepcopy(history)
        stale_closed[2]["label"] = "waiting-for-manual-testing"
        self.assertTrue(cleanup_violations(history_value=stale_closed))
        stale_superseded = copy.deepcopy(history)
        stale_superseded[3]["label"] = "waiting-for-manual-testing"
        self.assertTrue(cleanup_violations(history_value=stale_superseded))
        missing_label_marker = copy.deepcopy(history)
        del missing_label_marker[2]["label_removed"]
        self.assertTrue(cleanup_violations(history_value=missing_label_marker))
        missing_assignee_marker = copy.deepcopy(history)
        del missing_assignee_marker[2]["temporary_assignee_removed"]
        self.assertTrue(cleanup_violations(
            history_value=missing_assignee_marker,
        ))
        missing_completed_state = copy.deepcopy(history)
        del missing_completed_state[2]["manual_pending"]
        self.assertTrue(cleanup_violations(
            history_value=missing_completed_state,
        ))
        unowned_assignee = copy.deepcopy(history)
        unowned_assignee[3]["other_ownership"] = False
        unowned_assignee[3].pop("ownership_reason")
        self.assertTrue(cleanup_violations(history_value=unowned_assignee))
        for scenario, ownership_reason in {
            "missing": _MISSING,
            "blank": " ",
            "boolean": False,
            "list": [],
            "object": {},
            "integer": 1,
        }.items():
            with self.subTest(ownership_reason=scenario):
                invalid_history = list(copy.deepcopy(history))
                if ownership_reason is _MISSING:
                    invalid_history[3].pop("ownership_reason")
                else:
                    invalid_history[3]["ownership_reason"] = ownership_reason
                self.assertTrue(
                    cleanup_violations(history_value=invalid_history)
                )
        unexpected_reason = list(copy.deepcopy(history))
        unexpected_reason[1]["ownership_reason"] = "Unexpected owner"
        self.assertTrue(cleanup_violations(
            history_value=unexpected_reason,
        ))
        false_history_with_current_label = copy.deepcopy(history)
        false_history_with_current_label[4]["label"] = (
            "waiting-for-manual-testing"
        )
        self.assertTrue(cleanup_violations(
            history_value=false_history_with_current_label,
        ))
        false_history_with_label_removal = copy.deepcopy(history)
        false_history_with_label_removal[4]["label_removed"] = True
        self.assertTrue(cleanup_violations(
            history_value=false_history_with_label_removal,
        ))
        false_history_with_assignee_removal = copy.deepcopy(history)
        false_history_with_assignee_removal[4][
            "temporary_assignee_removed"
        ] = True
        self.assertTrue(cleanup_violations(
            history_value=false_history_with_assignee_removal,
        ))
        false_history_with_stale_assignee = copy.deepcopy(history)
        false_history_with_stale_assignee[4]["other_ownership"] = False
        self.assertTrue(cleanup_violations(
            history_value=false_history_with_stale_assignee,
        ))
        missing_received_history = copy.deepcopy(history)
        del missing_received_history[4]["received_label"]
        self.assertTrue(cleanup_violations(
            history_value=missing_received_history,
        ))

        history_shape_mutations = {}
        for field, values in {
            "kind": (_MISSING, None, "pull_request", 1),
            "url": (
                _MISSING,
                None,
                "https://example.invalid/issues/171",
                open_pr,
            ),
            "state": (_MISSING, None, "pending", 1),
            "manual_pending": (_MISSING, None, "false", 0),
            "received_label": ("false", 0),
            "other_ownership": ("false", 1),
            "label_removed": ("true", 1),
            "temporary_assignee_removed": ("true", 1),
        }.items():
            for value in values:
                mutated = copy.deepcopy(history[0])
                if value is _MISSING:
                    mutated.pop(field)
                    label = f"missing {field}"
                else:
                    mutated[field] = value
                    label = f"{field}={value!r}"
                history_shape_mutations[label] = mutated

        for scenario, invalid_item in history_shape_mutations.items():
            with self.subTest(history_shape=scenario):
                invalid_history = list(copy.deepcopy(history))
                invalid_history[0] = invalid_item
                failures = cleanup_violations(
                    history_value=invalid_history,
                )
                self.assertTrue(
                    any("history[0]" in failure for failure in failures)
                )

    def test_manual_handoff_rejected_state_remains_actionable(self):
        contract = read_manual_handoff_contract()
        issue_url = (
            "https://github.com/laqieer/fireemblem8-expansion/issues/171"
        )
        pr_url = "https://github.com/laqieer/fireemblem8-expansion/pull/172"
        activation_comment = valid_handoff_comment(contract)

        def rejected_item(url, kind, **overrides):
            item = {
                "url": url,
                "kind": kind,
                "state": "open",
                "manual_pending": True,
                "received_label": True,
                "label": "waiting-for-manual-testing",
                "assignee": "laqieer",
                "merge_hold": True,
                "closure_hold": True,
                "actionable": True,
                "label_removed": False,
                "temporary_assignee_removed": False,
                "comment": activation_comment,
            }
            if kind == "pr":
                item["origin_url"] = issue_url
                item["current_head_sha"] = activation_comment["commit"]
            item.update(overrides)
            return item

        history = (
            rejected_item(issue_url, "issue"),
            rejected_item(pr_url, "pr"),
        )
        rejection_comments = {
            item["url"]: {
                **valid_completion_comment(contract, activation_comment),
                "actual_result": "The requested judgment failed.",
                "outcome": "rejected",
            }
            for item in history
        }
        self.assertEqual(
            [],
            validate_rejected_manual_state(
                contract,
                history,
                rejection_comments,
            ),
        )

        state_mutations = {
            "label removed": ("label", None),
            "label cleanup recorded": ("label_removed", True),
            "assignee removed": ("assignee", None),
            "assignee cleanup recorded": (
                "temporary_assignee_removed",
                True,
            ),
            "merge hold missing": ("merge_hold", False),
            "closure hold missing": ("closure_hold", False),
            "not actionable": ("actionable", False),
            "not pending": ("manual_pending", False),
        }
        for scenario, (field, value) in state_mutations.items():
            with self.subTest(rejected_state=scenario):
                invalid_history = list(copy.deepcopy(history))
                invalid_history[1][field] = value
                self.assertTrue(
                    validate_rejected_manual_state(
                        contract,
                        invalid_history,
                        rejection_comments,
                    )
                )

        missing_result = dict(rejection_comments)
        del missing_result[pr_url]
        self.assertTrue(validate_rejected_manual_state(
            contract,
            history,
            missing_result,
        ))
        accepted_result = copy.deepcopy(rejection_comments)
        accepted_result[pr_url]["outcome"] = "accepted"
        self.assertTrue(validate_rejected_manual_state(
            contract,
            history,
            accepted_result,
        ))
        malformed_result = copy.deepcopy(rejection_comments)
        malformed_result[pr_url]["actual_result"] = " "
        self.assertTrue(validate_rejected_manual_state(
            contract,
            history,
            malformed_result,
        ))

    def test_manual_handoff_case_subsections_do_not_leak(self):
        governance = WORKFLOW_GOVERNANCE_PATH.read_text(encoding="utf-8")
        case = "\n".join(
            read_markdown_section(governance, MANUAL_HANDOFF_CASE_HEADING)
        )
        self.assertIn("### Actions", governance)
        mutated_case = case.replace(
            "### Actions",
            "### Missing actions",
            1,
        )
        with self.assertRaisesRegex(
            AssertionError,
            "expected exactly one Markdown section",
        ):
            read_markdown_section(mutated_case, "Actions")


if __name__ == "__main__":
    unittest.main()
