from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    ROOT / ".github" / "skills" / "development-workflow" / "SKILL.md"
)
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
PR_TEMPLATE_PATH = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
CLAUDE_PATH = ROOT / "CLAUDE.md"


def normalize_markdown_whitespace(text):
    return " ".join(text.split())


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

        markdown_contract = (
            "Validate and merge independent PRs in parallel.",
            "Another independent merge advancing `master` does not by itself "
            "invalidate a candidate head's evidence.",
            "Monitor Copilot review concurrently with candidate Build CI. Use a "
            "separate bounded direct watcher for the exact candidate's Copilot "
            "review check; when it finishes, inspect and triage its threads "
            "immediately instead of waiting for Build or Full Matrix.",
            "Dispatch Full Matrix only after both Build CI and Copilot review "
            "are terminal and clean for the same candidate.",
        )
        normalized_skill = normalize_markdown_whitespace(text)
        for requirement in markdown_contract:
            with self.subTest(surface="skill", requirement=requirement):
                self.assertIn(requirement, normalized_skill)

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
        project_markdown_contract = (
            "Monitor Copilot review concurrently with candidate Build CI using "
            "its own bounded direct watcher.",
            "Inspect review threads as soon as that review is terminal; do not "
            "wait for Build or Full Matrix.",
            "Start Full Matrix only after Build and Copilot review are both clean.",
            "Independent PRs may validate and merge in parallel.",
            "Do not queue them by age, issue number, shared initiative, or "
            "another independent PR's post-merge CI.",
        )
        for surface, instructions in (
            ("project instructions", project_instructions),
            ("Claude project instructions", claude_instructions),
        ):
            normalized_instructions = normalize_markdown_whitespace(instructions)
            for requirement in project_markdown_contract:
                with self.subTest(surface=surface, requirement=requirement):
                    self.assertIn(requirement, normalized_instructions)

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
