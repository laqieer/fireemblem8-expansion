from dataclasses import dataclass
from pathlib import Path
import re
from typing import FrozenSet, Tuple
import unittest

from scripts.check_docs import DocsCheckError, strip_fenced_blocks


ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    ROOT / ".github" / "skills" / "development-workflow" / "SKILL.md"
)
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
PR_TEMPLATE_PATH = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
CLAUDE_PATH = ROOT / "CLAUDE.md"
COPILOT_INSTRUCTIONS_PATH = ROOT / ".github" / "copilot-instructions.md"
MEANINGFUL_TEST_POLICY_HEADING = "Meaningful test evidence"
MARKDOWN_HEADING = re.compile(r"^(?P<level>#{1,6})\s+(?P<heading>.+)$")
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


def strip_html_comments(text):
    """Blank HTML comments while preserving layout and rejecting ambiguity."""
    visible = []
    comment_line_indexes = set()
    in_comment = False
    opening_line = None
    line_number = 1
    cursor = 0

    while cursor < len(text):
        if text.startswith("<!--", cursor):
            if in_comment:
                raise AssertionError(
                    f"nested HTML comment opener at line {line_number}"
                )
            in_comment = True
            opening_line = line_number
            comment_line_indexes.add(line_number - 1)
            visible.extend(" " * 4)
            cursor += 4
            continue

        if text.startswith("--!>", cursor):
            raise AssertionError(
                f"malformed HTML comment closer at line {line_number}"
            )

        if text.startswith("-->", cursor):
            if not in_comment:
                raise AssertionError(
                    f"stray HTML comment closer at line {line_number}"
                )
            comment_line_indexes.add(line_number - 1)
            visible.extend(" " * 3)
            in_comment = False
            opening_line = None
            cursor += 3
            continue

        char = text[cursor]
        if in_comment:
            comment_line_indexes.add(line_number - 1)
            visible.append(char if char in "\r\n" else " ")
        else:
            visible.append(char)
        if char == "\n":
            line_number += 1
        cursor += 1

    if in_comment:
        raise AssertionError(
            f"unterminated HTML comment opened at line {opening_line}"
        )

    return "".join(visible), frozenset(comment_line_indexes)


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
    unfenced_text = strip_fenced_blocks(text)
    raw_lines = unfenced_text.split("\n")
    visible_text, comment_line_indexes = strip_html_comments(unfenced_text)
    visible_lines = visible_text.split("\n")
    if len(raw_lines) != len(visible_lines):
        raise AssertionError("HTML comment scanner changed Markdown line count")

    heading_lines = []
    for index, raw_line in enumerate(raw_lines):
        visible_line = visible_lines[index]
        raw_match = MARKDOWN_HEADING.match(raw_line)
        visible_match = MARKDOWN_HEADING.match(visible_line)
        if (
            index in comment_line_indexes
            and raw_match
            and raw_match.group("heading").strip() == heading
        ):
            raise AssertionError("policy heading appears inside an HTML comment")

        if index in comment_line_indexes:
            continue
        if (
            visible_match
            and visible_match.group("heading").strip() == heading
        ):
            heading_lines.append(
                (index, len(visible_match.group("level")))
            )
    if len(heading_lines) != 1:
        raise AssertionError(
            f"expected exactly one Markdown section {heading!r}, "
            f"found {len(heading_lines)}"
        )

    end_line = next(
        (
            index
            for index in range(heading_lines[0][0] + 1, len(visible_lines))
            if (
                index not in comment_line_indexes
                and (match := MARKDOWN_HEADING.match(visible_lines[index]))
                and len(match.group("level")) <= heading_lines[0][1]
            )
        ),
        len(visible_lines),
    )
    for index in range(heading_lines[0][0] + 1, end_line):
        if (
            index in comment_line_indexes
            and raw_lines[index].strip()
            and visible_lines[index].strip()
        ):
            raise AssertionError(
                "HTML comments in the policy section must occupy "
                f"standalone lines (line {index + 1})"
            )
    return visible_lines[heading_lines[0][0] + 1:end_line]


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
        if match := MEANINGFUL_TEST_POLICY_CLAUSE.match(line):
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
        if match := MEANINGFUL_TEST_POLICY_ITEM.match(line):
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

        top_level_terminator = policy_text + "\n# Separate document section\n"
        self.assertEqual(
            CANONICAL_POLICY_AST,
            self.assert_meaningful_test_policy(top_level_terminator),
        )
        for policy_heading in (
            "##  Meaningful test evidence",
            "##\tMeaningful test evidence",
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


if __name__ == "__main__":
    unittest.main()
