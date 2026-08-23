import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    ROOT / ".github" / "skills" / "development-workflow" / "SKILL.md"
)
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
PR_TEMPLATE_PATH = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
CLAUDE_PATH = ROOT / "CLAUDE.md"
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


class DevelopmentWorkflowSkillTests(unittest.TestCase):
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
