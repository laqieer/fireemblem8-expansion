from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    ROOT / ".github" / "skills" / "development-workflow" / "SKILL.md"
)
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
PR_TEMPLATE_PATH = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
CLAUDE_PATH = ROOT / "CLAUDE.md"
COPILOT_INSTRUCTIONS_PATH = ROOT / ".github" / "copilot-instructions.md"
MEANINGFUL_TEST_POLICY_HEADING = "Meaningful test evidence"
MEANINGFUL_TEST_POLICY_CLAUSES = (
    "Evidence standard",
    "Prohibited evidence",
    "Static-contract exception",
    "Evidence preference",
    "Replacement and mutation controls",
)
MARKDOWN_HEADING = re.compile(r"^#{2,6} (?P<heading>.+)$")
MEANINGFUL_TEST_POLICY_CLAUSE = re.compile(
    r"^- \*\*(?P<name>[^*:]+):\*\* (?P<value>.+)$"
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
MEANINGFUL_TEST_POLICY_ITEM_SCHEMA = {
    "Evidence standard": {},
    "Prohibited evidence": {
        **{category: "prohibited" for category in PROHIBITED_EVIDENCE_CATEGORIES},
        GIT_TEXT_RATIONALE: "required",
    },
    "Static-contract exception": {},
    "Evidence preference": {},
    "Replacement and mutation controls": {},
}
MEANINGFUL_TEST_POLICY_SEMANTIC_PATTERNS = {
    "Evidence standard": {
        "tests-must-prove": (r"\btests? must prove\b",),
        "behavior": (r"\bbehavior\b",),
        "parsed-structural-contract": (r"\bparsed structural contract\b",),
        "generated-output": (r"\bgenerated output\b",),
        "compile-link-properties": (r"\bcompile link properties\b",),
        "runtime-state": (r"\bruntime state\b",),
    },
    "Prohibited evidence": {
        "sole-evidence-rule": (
            r"\b(?:do not|must not) add (?:a )?test whose only evidence "
            r"comes from a listed category\b",
        ),
        "listed-categories-prohibited": (
            r"\beach listed category is prohibited\b",
        ),
    },
    "Static-contract exception": {
        "source-text-only": (
            r"\bsource text assertion is permitted only\b",
        ),
        "syntax-spelling-absence": (
            r"\bexact syntax spelling or absence\b",
        ),
        "public-format": (r"\bdocumented public format\b",),
        "security-boundary": (r"\bsecurity boundary\b",),
        "generated-file-contract": (r"\bgenerated file contract\b",),
        "abi-layout-constraint": (r"\babi layout constraint\b",),
        "externally-consumed-protocol": (
            r"\bexternally consumed protocol\b",
        ),
        "named-contract": (r"\btest must name that contract\b",),
        "irreplaceable-evidence": (
            r"\bfunctional parsed compiled or runtime evidence cannot replace it\b",
        ),
    },
    "Evidence preference": {
        "prefer-real-function-inputs": (
            r"\bprefer calling the real function with positive and adversarial inputs\b",
        ),
        "parse-real-structure": (
            r"\bparsing the real json yaml make database ast binary or schema\b",
        ),
        "avoid-serialized-grep": (
            r"\brather than grepping its serialization\b",
        ),
        "compile-link-inspection": (
            r"\bcompile link inspection of typed symbols sections resources or generated output\b",
        ),
        "deterministic-runtime": (
            r"\bdeterministic target rom libmgba behavior\b",
        ),
        "narrow-static-last": (
            r"\bbefore a narrowly justified source text assertion\b",
        ),
    },
    "Replacement and mutation controls": {
        "preserve-requirement": (
            r"\bpreserve its accepted requirement with stronger evidence\b",
        ),
        "duplicate-gate": (
            r"\bduplicated another gate and had no independent contract\b",
        ),
        "behavior-change-fails": (
            r"\bbehavior change which preserves the old phrase fails the replacement test\b",
        ),
        "semantics-preserving-refactor-green": (
            r"\bsemantics preserving spelling or ordering refactor remains green\b",
        ),
    },
}
MEANINGFUL_TEST_POLICY_SEMANTICS = {
    clause_name: frozenset(patterns)
    for clause_name, patterns in MEANINGFUL_TEST_POLICY_SEMANTIC_PATTERNS.items()
}
GIT_TEXT_RATIONALE_PATTERNS = {
    "git-tracks-source": (r"\bgit tracks source\b",),
    "git-tracks-review": (r"\bgit tracks(?: source)? review\b",),
    "git-tracks-history": (
        r"\bgit tracks(?: source)?(?: review)?(?: and)? history\b",
    ),
    "tracked-text-is-not-behavior-evidence": (
        r"\braw tracked text presence is not behavior evidence\b",
    ),
}
GIT_TEXT_RATIONALE_SEMANTICS = frozenset(GIT_TEXT_RATIONALE_PATTERNS)
PROHIBITED_EVIDENCE_PERMISSION = re.compile(
    r"\b(?:permitted|allowed|acceptable)\b"
)


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
    lines = text.splitlines()
    heading_line = next(
        (
            index
            for index, line in enumerate(lines)
            if (
                MARKDOWN_HEADING.match(line)
                and MARKDOWN_HEADING.match(line).group("heading").strip() == heading
            )
        ),
        None,
    )
    if heading_line is None:
        raise AssertionError(f"missing Markdown section: {heading}")

    end_line = next(
        (
            index
            for index in range(heading_line + 1, len(lines))
            if MARKDOWN_HEADING.match(lines[index])
        ),
        len(lines),
    )
    return lines[heading_line + 1:end_line]


def parse_meaningful_test_policy(text):
    clauses = {}
    clause_name = None
    item = None
    for line in read_markdown_section(text, MEANINGFUL_TEST_POLICY_HEADING):
        match = MEANINGFUL_TEST_POLICY_CLAUSE.match(line)
        if match:
            clause_name = match.group("name")
            item = None
            if clause_name in clauses:
                raise AssertionError(f"duplicate policy clause: {clause_name}")
            clauses[clause_name] = {
                "text": [match.group("value")],
                "items": {},
            }
        elif match := MEANINGFUL_TEST_POLICY_ITEM.match(line):
            if clause_name is None:
                raise AssertionError(f"orphaned policy item: {match.group('name')}")
            items = clauses[clause_name]["items"]
            item_name = match.group("name")
            if item_name in items:
                raise AssertionError(f"duplicate policy item: {item_name}")
            item = {
                "status": match.group("status"),
                "detail": match.group("detail") or "",
            }
            items[item_name] = item
        elif clause_name is not None and re.match(r"^\s+- ", line):
            raise AssertionError(f"invalid policy item: {line.strip()}")
        elif item is not None and line.startswith("    ") and line.strip():
            item["detail"] = " ".join((item["detail"], line.strip())).strip()
        elif clause_name is not None and line.startswith("  ") and line.strip():
            clauses[clause_name]["text"].append(line.strip())

    expected = set(MEANINGFUL_TEST_POLICY_CLAUSES)
    actual = set(clauses)
    if actual != expected:
        raise AssertionError(
            f"policy clauses differ: missing={expected - actual}, extra={actual - expected}"
        )
    return {
        name: {
            "text": " ".join(clauses[name]["text"]),
            "items": clauses[name]["items"],
        }
        for name in MEANINGFUL_TEST_POLICY_CLAUSES
    }


def render_meaningful_test_policy(clauses, clause_order):
    lines = [f"## {MEANINGFUL_TEST_POLICY_HEADING}", ""]
    for name in clause_order:
        clause = clauses[name]
        lines.append(f"- **{name}:** {clause['text']}")
        for item_name, item in clause["items"].items():
            detail = f". {item['detail']}" if item["detail"] else ""
            lines.append(f"  - **{item_name}:** {item['status']}{detail}")
    return "\n".join([*lines, ""])


def clone_meaningful_test_policy(clauses):
    return {
        name: {
            "text": clause["text"],
            "items": {
                item_name: dict(item)
                for item_name, item in clause["items"].items()
            },
        }
        for name, clause in clauses.items()
    }


def normalize_policy_text(text):
    return " ".join(
        re.sub(
            r"[^a-z0-9]+",
            " ",
            text.casefold().replace("behaviour", "behavior"),
        ).split()
    )


def policy_semantics(text, patterns):
    normalized = normalize_policy_text(text)
    return frozenset(
        name
        for name, alternatives in patterns.items()
        if any(re.search(pattern, normalized) for pattern in alternatives)
    )


class DevelopmentWorkflowSkillTests(unittest.TestCase):
    def assert_meaningful_test_policy(self, text):
        clauses = parse_meaningful_test_policy(text)
        normalized_policy = {}

        for clause_name in MEANINGFUL_TEST_POLICY_CLAUSES:
            clause = clauses[clause_name]
            expected_items = MEANINGFUL_TEST_POLICY_ITEM_SCHEMA[clause_name]
            actual_items = {
                item_name: item["status"]
                for item_name, item in clause["items"].items()
            }
            self.assertEqual(
                expected_items,
                actual_items,
                f"{clause_name}: policy item schema differs",
            )

            actual_semantics = policy_semantics(
                clause["text"],
                MEANINGFUL_TEST_POLICY_SEMANTIC_PATTERNS[clause_name],
            )
            self.assertEqual(
                MEANINGFUL_TEST_POLICY_SEMANTICS[clause_name],
                actual_semantics,
                f"{clause_name}: semantic contract differs",
            )
            normalized_policy[clause_name] = {
                "items": actual_items,
                "semantics": actual_semantics,
            }

        prohibited_text = normalize_policy_text(
            clauses["Prohibited evidence"]["text"]
        )
        if PROHIBITED_EVIDENCE_PERMISSION.search(prohibited_text):
            self.fail(
                "Prohibited evidence cannot authorize a text-only evidence category"
            )

        rationale_semantics = policy_semantics(
            clauses["Prohibited evidence"]["items"][GIT_TEXT_RATIONALE]["detail"],
            GIT_TEXT_RATIONALE_PATTERNS,
        )
        self.assertEqual(
            GIT_TEXT_RATIONALE_SEMANTICS,
            rationale_semantics,
            "Git-text rationale semantic contract differs",
        )
        normalized_policy["Prohibited evidence"]["git-text-rationale"] = (
            rationale_semantics
        )
        return normalized_policy

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
            "Merge the PR autonomously when all five conditions hold.",
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
        required_contract = (
            "Reasoning subagents must not remain alive merely to wait",
            "records the exact candidate",
            "SHA and run ID",
            "returns immediately",
            "exactly one direct shell watcher",
            "timeout 90m gh run watch <run-id> --interval 30 --exit-status",
            "process-completion",
            "never create duplicate watchers",
            "Only after the workflow reaches a terminal state",
            "gh run cancel <run-id>",
            "Never repeatedly wake the same subagent merely",
            "never accept a stale run",
            "Post-merge `master` Build CI monitoring is always nonblocking.",
            "attached asynchronous mode",
            "immediately continue scheduling every dependency-ready",
            "Do not stop orchestration or",
            "send a waiting-only response",
            "Only issue closure, remote completion, and other true dependents",
            "exact merged `master` SHA",
            "fix forward or revert immediately",
        )

        for requirement in required_contract:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, text)

        project_instructions = (
            ROOT / ".github" / "copilot-instructions.md"
        ).read_text(encoding="utf-8")
        project_contract = (
            "CI waiting must not occupy a reasoning subagent.",
            "records its exact SHA and run ID, then returns immediately",
            "exactly one bounded direct shell watcher",
            "timeout 90m gh run watch <run-id> --interval 30 --exit-status",
            "invoke a reasoning agent only",
            "after the run is terminal",
            "Do not repeatedly wake an",
            "agent to poll",
            "cancel superseded",
            "candidate runs before dispatching replacement checks",
            "After a PR merge, monitor the exact-`master` Build CI",
            "nonblocking asynchronous shell watcher",
            "continue every unrelated dependency-ready task",
            "stopping to wait or sending a waiting-only response",
            "Only closure, remote",
            "fix forward or revert the broken default",
        )

        for requirement in project_contract:
            with self.subTest(
                surface="project instructions", requirement=requirement
            ):
                self.assertIn(requirement, project_instructions)

        claude_instructions = CLAUDE_PATH.read_text(encoding="utf-8")
        for requirement in project_contract:
            with self.subTest(
                surface="Claude project instructions", requirement=requirement
            ):
                self.assertIn(requirement, claude_instructions)

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
            "Apply candidate-commit Build CI, Full Matrix, post-merge Build verification",
            "Complete the umbrella initiative only after every accepted",
        )

        for requirement in required_contract:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, text)

    def test_meaningful_test_evidence_policy_is_aligned(self):
        """Validate the normalized policy schema and its semantic contracts."""
        canonical = None
        for path in (
            SKILL_PATH,
            COPILOT_INSTRUCTIONS_PATH,
            CLAUDE_PATH,
        ):
            with self.subTest(surface=str(path.relative_to(ROOT))):
                normalized_policy = self.assert_meaningful_test_policy(
                    path.read_text(encoding="utf-8")
                )
                if canonical is None:
                    canonical = normalized_policy
                else:
                    self.assertEqual(canonical, normalized_policy)

        _, skill = read_skill()
        clauses = parse_meaningful_test_policy(skill)
        reordered = render_meaningful_test_policy(
            clauses, reversed(MEANINGFUL_TEST_POLICY_CLAUSES)
        ).replace(
            "Tests must prove behavior, a parsed structural contract",
            "Tests must prove behavior, a parsed\n"
            "    structural contract",
        )

        self.assert_meaningful_test_policy(
            reordered
        )
        self.assert_meaningful_test_policy(
            render_meaningful_test_policy(
                clauses, MEANINGFUL_TEST_POLICY_CLAUSES
            ).replace("behavior", "behaviour")
        )

        for mutation_name, clause_name, replacement in (
            (
                "evidence standard",
                "Evidence standard",
                clauses["Evidence standard"]["text"].replace(
                    "Tests must prove", "Tests may merely mention"
                ),
            ),
            (
                "static-contract exception",
                "Static-contract exception",
                clauses["Static-contract exception"]["text"].replace(
                    "permitted only", "permitted sometimes"
                ),
            ),
            (
                "evidence preference",
                "Evidence preference",
                clauses["Evidence preference"]["text"].replace(
                    "Prefer calling", "Avoid calling"
                ),
            ),
            (
                "replacement mutation control",
                "Replacement and mutation controls",
                clauses["Replacement and mutation controls"]["text"].replace(
                    "fails the replacement test", "passes the replacement test"
                ),
            ),
        ):
            with self.subTest(mutation=mutation_name):
                mutation = clone_meaningful_test_policy(clauses)
                mutation[clause_name]["text"] = replacement
                with self.assertRaises(AssertionError):
                    self.assert_meaningful_test_policy(
                        render_meaningful_test_policy(
                            mutation, MEANINGFUL_TEST_POLICY_CLAUSES
                        )
                    )

        mutation = clone_meaningful_test_policy(clauses)
        mutation["Prohibited evidence"]["text"] += (
            " Source-text-only tests are permitted."
        )
        with self.assertRaises(AssertionError):
            self.assert_meaningful_test_policy(
                render_meaningful_test_policy(
                    mutation, MEANINGFUL_TEST_POLICY_CLAUSES
                )
            )

        for mutation_name, detail in (
            (
                "Git-text rationale source",
                "Git tracks review and history, so raw tracked-text presence "
                "is not behavior evidence.",
            ),
            (
                "Git-text rationale review",
                "Git tracks source and history, so raw tracked-text presence "
                "is not behavior evidence.",
            ),
            (
                "Git-text rationale history",
                "Git tracks source and review, so raw tracked-text presence "
                "is not behavior evidence.",
            ),
            (
                "Git-text rationale behavior evidence",
                "Git tracks source, review, and history, so raw tracked-text "
                "presence is behavior evidence.",
            ),
        ):
            with self.subTest(mutation=mutation_name):
                mutation = clone_meaningful_test_policy(clauses)
                mutation["Prohibited evidence"]["items"][GIT_TEXT_RATIONALE][
                    "detail"
                ] = detail
                with self.assertRaises(AssertionError):
                    self.assert_meaningful_test_policy(
                        render_meaningful_test_policy(
                            mutation, MEANINGFUL_TEST_POLICY_CLAUSES
                        )
                    )

        for clause_name in MEANINGFUL_TEST_POLICY_CLAUSES:
            with self.subTest(mutation=f"unexpected item under {clause_name}"):
                mutation = clone_meaningful_test_policy(clauses)
                item_name = (
                    "unexpected category"
                    if clause_name == "Prohibited evidence"
                    else "arbitrary strings"
                )
                mutation[clause_name]["items"][item_name] = {
                    "status": "permitted",
                    "detail": "",
                }
                with self.assertRaises(AssertionError):
                    self.assert_meaningful_test_policy(
                        render_meaningful_test_policy(
                            mutation, MEANINGFUL_TEST_POLICY_CLAUSES
                        )
                    )

        malformed_item = render_meaningful_test_policy(
            clauses, MEANINGFUL_TEST_POLICY_CLAUSES
        ).replace(
            "  - **comments:** prohibited",
            "   - **comments:** prohibited",
        )
        with self.assertRaises(AssertionError):
            parse_meaningful_test_policy(malformed_item)

        for mutation_name, mutate_items in (
            (
                "missing prohibited category",
                lambda items: items.pop("helper names"),
            ),
            (
                "permitted prohibited category",
                lambda items: items["comments"].update(status="permitted"),
            ),
        ):
            with self.subTest(mutation=mutation_name):
                mutation = clone_meaningful_test_policy(clauses)
                mutate_items(mutation["Prohibited evidence"]["items"])
                with self.assertRaises(AssertionError):
                    self.assert_meaningful_test_policy(
                        render_meaningful_test_policy(
                            mutation, MEANINGFUL_TEST_POLICY_CLAUSES
                        )
                    )

    def test_meaningful_test_policy_requires_a_valid_markdown_heading(self):
        with self.assertRaisesRegex(AssertionError, "missing Markdown section"):
            read_markdown_section(
                "##Meaningful test evidence\n\n- **Evidence standard:** ignored\n",
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
            "exact-candidate Build CI",
            "Full Matrix if the candidate commit or tree changed",
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
