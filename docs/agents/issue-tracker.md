# Issue tracker: GitHub

Issues and specs for this repo live in GitHub Issues for
`laqieer/fireemblem8-expansion`. Use the `gh` CLI for all operations.
Run commands inside this clone so `gh` selects the repository, or pass
`--repo laqieer/fireemblem8-expansion` explicitly. The `decomp` remote is
an upstream reference, not this project's issue tracker.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a
  heredoc or `--body-file` for multi-line bodies.
- **Read an issue**: `gh issue view <number> --json number,title,body,labels,comments`.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments`
  with appropriate `--label` and `--state` filters.
- **Make an issue a sub-issue of a parent**: with `gh` 2.94+, use
  `gh issue create --parent <parent> ...` or
  `gh issue edit <parent> --add-sub-issue <child>`. With older `gh`, use
  `gh api --method POST repos/laqieer/fireemblem8-expansion/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`.
  Obtain the numeric database ID with
  `gh api repos/laqieer/fireemblem8-expansion/issues/<child> --jq .id`.
  Where sub-issues are unavailable, put `Part of #<parent>` at the top of the
  child body and link the child in the parent's task list.
- **Comment**: `gh issue comment <number> --body "..."`.
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` /
  `--remove-label "..."`. Read `docs/agents/triage-labels.md` for triage roles.
- **Close**: `gh issue close <number> --comment "..."`, only after the applicable
  repository development-workflow completion criteria are satisfied.

## Pull requests as a triage surface

**PRs as a request surface: no.** Set to `yes` if this repo treats external
PRs as feature requests; `/triage` reads this flag.

GitHub shares one number space across issues and PRs. Resolve an ambiguous
number with `gh pr view <number>` and fall back to `gh issue view <number>`.
Normal implementation PRs still follow the repository development workflow.

## Skill operations

When a skill says **publish to the issue tracker**, create a GitHub issue.
When it says **fetch the relevant ticket**, read the issue with its comments.

## Wayfinding operations

For `/wayfinder`, the map is a single issue with child issues as tickets.

- **Map**: an issue labelled `wayfinder:map`, holding the Notes /
  Decisions-so-far / Fog body.
- **Child ticket**: a sub-issue of the map, labelled `wayfinder:<type>`
  (`research`, `prototype`, `grilling`, or `task`). Use the parent/child
  fallback above when sub-issues are unavailable.
- **Blocking**: use GitHub's native issue dependencies:
  `gh api --method POST repos/laqieer/fireemblem8-expansion/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`.
  Where dependencies are unavailable, use a `Blocked by: #<n>, #<n>` line at
  the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children, drop any with an open
  blocker or assignee, and choose the first in map order.
- **Claim**: `gh issue edit <number> --add-assignee @me`, the driving session's
  first write to the ticket.
- **Resolve**: comment with the answer, close after the applicable workflow
  criteria are satisfied, and append a context pointer to the map's
  Decisions-so-far.
