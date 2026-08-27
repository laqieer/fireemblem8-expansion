from dataclasses import dataclass
import copy
import json
from pathlib import Path
import re
from typing import FrozenSet, Tuple
import unittest

from scripts.check_docs import (
    DocsCheckError,
    is_fence_closing,
    parse_atx_heading,
    parse_fence_opening,
)


ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    ROOT / ".github" / "skills" / "development-workflow" / "SKILL.md"
)
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
PR_TEMPLATE_PATH = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
CLAUDE_PATH = ROOT / "CLAUDE.md"
COPILOT_INSTRUCTIONS_PATH = ROOT / ".github" / "copilot-instructions.md"
WORKFLOW_GOVERNANCE_PATH = ROOT / "docs" / "test-cases" / "workflow-governance.md"
TEST_CASE_REGISTRY_PATH = ROOT / "docs" / "test-cases" / "registry.json"
MANUAL_HANDOFF_CONTRACT_PATH = ROOT / ".github" / "manual-testing-handoff.json"
MANUAL_HANDOFF_CASE_HEADING = (
    "TC-WORKFLOW-MANUAL-HANDOFF-001: "
    "Surface actionable manual testing and resume automatically"
)
MANUAL_HANDOFF_QUERY = (
    'repo:laqieer/fireemblem8-expansion is:open assignee:laqieer '
    'label:"waiting-for-manual-testing"'
)
MANUAL_HANDOFF_QUERY_URL = (
    "https://github.com/laqieer/fireemblem8-expansion/issues?"
    "q=repo%3Alaqieer%2Ffireemblem8-expansion+is%3Aopen+"
    "assignee%3Alaqieer+label%3A%22waiting-for-manual-testing%22"
)
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
    CLAUDE_PATH,
    CONTRIBUTING_PATH,
    FRAMEWORK_SUPPORT_PATH,
    LOCALIZATION_PATH,
)
TRUSTED_PUSH_GUIDANCE_PATHS = (
    SKILL_PATH,
    ROOT / ".github" / "copilot-instructions.md",
    CLAUDE_PATH,
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


EXPECTED_MANUAL_HANDOFF_CONTRACT = {
    "schema": "fe8.manual-testing-handoff.v1",
    "eligibility": {
        "kinds": ["visual", "audio", "ux"],
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
        "mention": "@laqieer",
        "targets": [
            "originating_issue",
            "each_open_implementation_pr",
        ],
        "comment": {
            "required": True,
            "fields": [
                "case_id",
                "commit",
                "positive_artifact_path",
                "positive_artifact_sha256",
                "control_artifact_path",
                "control_artifact_sha256",
                "environment",
                "clean_state",
                "steps",
                "expected",
                "requested_judgment",
                "merge_hold",
                "closure_hold",
            ],
        },
    },
    "hold": {
        "merge": True,
        "issue_closure": True,
    },
    "completion": {
        "post_result": True,
        "post_evidence_link": True,
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
        "resume_exact_candidate_gates": True,
        "resume_merge": True,
    },
    "queue": {
        "query": MANUAL_HANDOFF_QUERY,
        "url": MANUAL_HANDOFF_QUERY_URL,
        "notify_when_empty": False,
        "live_cardinality": "dynamic",
        "relationship_source": "github_linked_open_implementation_prs",
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
    if isinstance(value, list):
        return value[:-1]
    raise TypeError(f"unsupported contract leaf: {type(value).__name__}")


def read_manual_handoff_contract():
    return json.loads(MANUAL_HANDOFF_CONTRACT_PATH.read_text(encoding="utf-8"))


def validate_live_manual_queue(contract, live_items, relationships):
    violations = []
    activation = contract["activation"]
    queue = contract["queue"]
    seen_urls = set()
    pending_open_items = []
    for item in live_items:
        url = item.get("url")
        if not url:
            violations.append("item missing URL")
            continue
        if url in seen_urls:
            violations.append(f"duplicate item: {url}")
        seen_urls.add(url)
        if not item.get("manual_pending", True):
            if item.get("label") == activation["label"]:
                violations.append(f"stale label: {url}")
            if (
                item.get("assignee") == activation["assignee"]
                and not item.get("other_ownership", False)
            ):
                violations.append(f"stale assignee: {url}")
        if item.get("state", "open") != "open":
            continue
        if not item.get("manual_pending", True):
            continue
        pending_open_items.append(item)
        if item.get("label") != activation["label"]:
            violations.append(f"wrong label: {url}")
        if item.get("assignee") != activation["assignee"]:
            violations.append(f"wrong assignee: {url}")

    issues = {
        item["url"]: item
        for item in pending_open_items
        if item.get("kind") == "issue"
    }
    prs = {}
    for item in pending_open_items:
        if item.get("kind") != "pr":
            continue
        if not item.get("origin_url"):
            violations.append(f"pending PR missing origin: {item['url']}")
            continue
        prs.setdefault(item["origin_url"], set()).add(item["url"])
        if item["origin_url"] not in issues:
            violations.append(f"orphan PR: {item['url']}")

    discovered = {}
    for relationship in relationships:
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


def validate_completion_cleanup(contract, item_history, cleanup):
    activation = contract["activation"]
    completion = contract["completion"]
    labeled_urls = {
        item["url"]
        for item in item_history
        if item.get("received_label")
        and item.get("kind") in {"issue", "pr"}
    }
    assignee_urls = {
        item["url"]
        for item in item_history
        if item.get("received_label")
        and item.get("kind") in {"issue", "pr"}
        and not item.get("other_ownership", False)
    }
    violations = []
    for item in item_history:
        if not item.get("received_label"):
            continue
        if item.get("label") == activation["label"]:
            violations.append(f"stale label: {item['url']}")
        if (
            not item.get("other_ownership", False)
            and item.get("assignee") == activation["assignee"]
        ):
            violations.append(f"stale assignee: {item['url']}")
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
        claude_instructions = CLAUDE_PATH.read_text(encoding="utf-8")
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
            ("Claude project instructions", claude_instructions),
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
            "CLAUDE": CLAUDE_PATH.read_text(encoding="utf-8"),
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
            "CLAUDE": CLAUDE_PATH.read_text(encoding="utf-8"),
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
            "Review and merge the stack bottom-up",
            "gh pr edit <child-pr> --base master",
            "Apply candidate-commit Build CI plus Copilot review",
            "consolidated Build verification",
            "Complete the umbrella",
            "initiative only after every accepted",
        )

        for requirement in required_contract:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, text)

        self.assertIn(
            normalize_policy("Complete the umbrella initiative only after every accepted"),
            normalize_policy(text),
        )

    def test_meaningful_test_evidence_policy_is_aligned(self):
        """Validate the fail-closed canonical policy AST on every guidance surface."""
        for path in (
            SKILL_PATH,
            COPILOT_INSTRUCTIONS_PATH,
            CLAUDE_PATH,
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
            "gh pr edit <child-pr-number> --base master",
            "rerun candidate Build CI and",
            "Copilot review if the candidate commit or tree changed",
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

    def test_pull_request_template_records_boundary_stack_and_size(self):
        text = PR_TEMPLATE_PATH.read_text(encoding="utf-8")
        required_contract = (
            "exactly one independent issue",
            "Immediate base branch",
            "Stack position",
            "Depends on",
            "Known dependents",
            "explicit dependent",
            "sub-issues",
            "git diff --name-only <base>...HEAD",
            "Total changed lines",
            "20,000-line hard ceiling",
            "Indivisible-change exception and alternative evidence",
        )

        for requirement in required_contract:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, text)

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
            "Tester-facing cases",
            "Case IDs exercised",
            "Definition/catalog links",
            "Exact configuration/profile or artifact",
            "Positive procedure and actual result",
            "pre-fix negative control and actual result",
            "feature interactions, and save expectations",
            "Automation mapping and result, or precise manual-only reason",
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
            "activation mention": (
                "activation",
                "mention",
                "@other-tester",
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
                mutated["activation"]["comment"]["fields"].remove(field)
                self.assertTrue(compare_contract(
                    mutated,
                    EXPECTED_MANUAL_HANDOFF_CONTRACT,
                ))

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
                "origin_url": origin_url,
            }
            item.update(overrides)
            return item

        issue_url = "https://example.invalid/issues/171"
        pr_one = "https://example.invalid/pulls/172"
        pr_two = "https://example.invalid/pulls/173"
        closed_pr = "https://example.invalid/pulls/174"
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
                    "issue_url": "https://example.invalid/issues/999",
                    "pr_url": "https://example.invalid/pulls/999",
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
                        "https://example.invalid/issues/999",
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
                self.assertTrue(
                    validate_live_manual_queue(
                        contract,
                        items,
                        relationships,
                    )
                )

    def test_manual_handoff_completion_cleans_labeled_history(self):
        contract = read_manual_handoff_contract()
        issue_url = "https://example.invalid/issues/171"
        open_pr = "https://example.invalid/pulls/172"
        closed_pr = "https://example.invalid/pulls/173"
        superseded_pr = "https://example.invalid/pulls/174"
        never_labeled_pr = "https://example.invalid/pulls/175"
        history = (
            {
                "url": issue_url,
                "kind": "issue",
                "state": "open",
                "received_label": True,
                "label": None,
                "assignee": None,
            },
            {
                "url": open_pr,
                "kind": "pr",
                "state": "open",
                "received_label": True,
                "label": None,
                "assignee": None,
            },
            {
                "url": closed_pr,
                "kind": "pr",
                "state": "closed",
                "received_label": True,
                "label": None,
                "assignee": None,
            },
            {
                "url": superseded_pr,
                "kind": "pr",
                "state": "superseded",
                "received_label": True,
                "label": None,
                "assignee": "laqieer",
                "other_ownership": True,
            },
            {
                "url": never_labeled_pr,
                "kind": "pr",
                "state": "closed",
                "received_label": False,
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
        self.assertEqual(
            [],
            validate_completion_cleanup(contract, history, cleanup),
        )
        for omitted_url in (closed_pr, superseded_pr):
            with self.subTest(omitted=omitted_url):
                mutated = copy.deepcopy(cleanup)
                mutated["remove_label_from"].remove(omitted_url)
                self.assertTrue(
                    validate_completion_cleanup(contract, history, mutated)
                )
        missing_closed_assignee = copy.deepcopy(cleanup)
        missing_closed_assignee["remove_temporary_assignee_from"].remove(
            closed_pr
        )
        self.assertTrue(validate_completion_cleanup(
            contract,
            history,
            missing_closed_assignee,
        ))
        independent_owner_cleanup = copy.deepcopy(cleanup)
        independent_owner_cleanup["remove_temporary_assignee_from"].append(
            superseded_pr
        )
        self.assertTrue(validate_completion_cleanup(
            contract,
            history,
            independent_owner_cleanup,
        ))
        extra = copy.deepcopy(cleanup)
        extra["remove_label_from"].append(never_labeled_pr)
        self.assertTrue(validate_completion_cleanup(contract, history, extra))
        duplicate = copy.deepcopy(cleanup)
        duplicate["remove_label_from"].append(issue_url)
        self.assertTrue(validate_completion_cleanup(
            contract,
            history,
            duplicate,
        ))

        stale_closed = copy.deepcopy(history)
        stale_closed[2]["label"] = "waiting-for-manual-testing"
        self.assertTrue(validate_completion_cleanup(
            contract,
            stale_closed,
            cleanup,
        ))
        stale_superseded = copy.deepcopy(history)
        stale_superseded[3]["label"] = "waiting-for-manual-testing"
        self.assertTrue(validate_completion_cleanup(
            contract,
            stale_superseded,
            cleanup,
        ))
        unowned_assignee = copy.deepcopy(history)
        unowned_assignee[3]["other_ownership"] = False
        self.assertTrue(validate_completion_cleanup(
            contract,
            unowned_assignee,
            cleanup,
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
