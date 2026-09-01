## Frozen scope

- Issue:
<!-- exact https://github.com/laqieer/fireemblem8-expansion/issues/N -->
- Closure:
<!-- Closes #N; exactly one independent issue -->
- Itemized scope:
- Explicit non-goals:

## Frozen classification and relationships

- Classification and rationale:
- Dependencies:
<!-- None, or exact issue/PR links -->
- Dependents:
<!-- None, or exact issue/PR links -->
- Conflicts:
<!-- None, or explicit conflicts and enforced boundaries -->
- Security and authority boundaries:

## PR boundary and stack

- This PR implements and closes exactly one independent issue.
- Immediate base branch:
<!-- master, or the genuine parent branch -->
- Stack position:
<!-- root, or child N of M -->
- Depends on:
<!-- None, or issue and PR links using Depends on #... -->
- Known dependents:
<!-- None, or issue and PR links -->
- Umbrella issue/discussion:
<!-- None, or tracking link; never an umbrella implementation PR -->
- Implementation, tests, documentation, generated outputs, migrations, and
  provenance for this issue remain together.
- Any separately deliverable contracts were created as explicit dependent
  sub-issues before being split into separate PRs.

## Frozen acceptance criteria

1. Observable criterion:
2. Positive criterion:
3. Negative or fail-closed criterion:

## Tester-facing procedure

- Stable case IDs:
- Definition/catalog links:
<!-- originating issue/docs; canonical catalog tracked by #54 -->
- Supported configuration/profile or artifact:
- Prerequisites and clean starting state:
- Exact actions or inputs:
- Observable expected result:
- Default/disabled or pre-fix negative control:
- Dependencies, conflicts, feature interactions, and save expectations:
- Automation mapping, or precise manual-only reason:
- Reset/cleanup, known limitations, and unsupported configurations:
- Any manual-only criterion is a precisely named visual, audio, or UX judgment
  that cannot be asserted reliably.

## Compatibility impact

- Save format and migration:
- Generated data and committed inventories:
- Debug configuration:
- Release configuration:
- Localization:
- ROM/RAM/resource budget:
- Archival lane:
- Baseline/fingerprint plan:
- Artifact and legal-source boundary:

## Canonical candidate evidence

Keep the scope, classification, relationships, acceptance criteria, tester
procedure, and compatibility decisions above stable. Put all evolving
candidate validation, tester observations, review-size measurements, workflow
and review identities, and completion state in exactly one PR comment that
follows the
[canonical marked-comment protocol](../docs/workflow-pilot.md#build-event-classification-and-candidate-evidence).
Update that comment in place; do not copy its marker or evolving fields into
this pull-request body.

## Merge boundary

Feature and bug-fix pull requests require no human review or approval. Passing
one checker alone is not delivery evidence. Follow
[the development workflow skill](skills/development-workflow/SKILL.md);
CODEOWNERS requests are advisory unless an external ruleset enforces them.
Every objective criterion must be validated, or the exact non-agent-verifiable
criterion preventing merge must be named in the canonical candidate-evidence
comment.
