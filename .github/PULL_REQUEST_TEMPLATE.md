## Frozen scope

- Issue: <!-- exact https://github.com/laqieer/fireemblem8-expansion/issues/N -->
- Closure: <!-- Closes #N; exactly one independent issue -->
- [ ] Scope is itemized and non-goals are explicit.

## PR boundary and stack

- [ ] This PR implements and closes exactly one independent issue.
- Immediate base branch: <!-- master, or the genuine parent branch -->
- Stack position: <!-- root, or child N of M -->
- Depends on: <!-- None, or issue and PR links using Depends on #... -->
- Known dependents: <!-- None, or issue and PR links -->
- Umbrella issue/discussion: <!-- None, or tracking link; never an umbrella implementation PR -->
- [ ] Implementation, tests, documentation, generated outputs, migrations,
      and provenance for this issue remain together.
- [ ] Any separately deliverable contracts were created as explicit dependent
      sub-issues before being split into separate PRs.

## Validation commands

List every command exactly as run from the repository root (no prose, no chaining):

```
python3 scripts/artifact_guard.py --revision HEAD
python3 -m unittest discover -s scripts/artifact_guard_tests -p 'test_*.py'
make generated-data-check
make expansion-modern-linker-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-linker-check MODERN_CONFIG=release MODERN_ABI=aapcs
```

- [ ] All commands above (or the subset relevant to this change) pass.
- Runtime/playtest evidence (scenario, environment, command, result): <!-- required only when behavior changes -->

## Tester-facing cases

- Case IDs exercised: <!-- e.g. TC-SAVE-001 -->
- Definition/catalog links: <!-- originating issue/docs; canonical catalog tracked by #54 -->
- Exact configuration/profile or artifact:
- Environment:
- Positive procedure and actual result:
- Default/disabled or pre-fix negative control and actual result:
- Dependencies, conflicts, feature interactions, and save expectations:
- Automation mapping and result, or precise manual-only reason:
- Reset/cleanup, known limitations, and unsupported configurations:
- [ ] Required cases shipped or were updated with the behavior; none were
      deferred to post-merge cleanup.
- [ ] Every deterministic assertion is automated. Any manual-only evidence is
      a precisely named visual, audio, or UX judgment that cannot be asserted
      reliably.

## Review-size preflight

Record the immediate-base comparison before requesting review:

```
git diff --name-only <base>...HEAD
git diff --numstat <base>...HEAD
git diff --shortstat <base>...HEAD
```

- Changed files:
- Additions:
- Deletions:
- Total changed lines:
- [ ] The PR remains below GitHub Copilot review's 20,000-line hard ceiling;
      or this is one genuinely indivisible issue whose reason and alternative
      automated/per-area review evidence are recorded below.
- Indivisible-change exception and alternative evidence: <!-- None for normal PRs -->

## Compatibility impact

- [ ] Save format / migration
- [ ] Generated data and committed inventories
- [ ] Debug configuration
- [ ] Release configuration

## Baseline/fingerprint review

- [ ] No `reports/baseline/`, `tools/gba-playtest/fingerprints/`, or
      `scripts/shiftcheck/tas/fingerprint.lua` path changed; **or**
- [ ] Such a change is intentional, investigated, and explained above (see
      [`docs/issue-resolution-policy.md`](../docs/issue-resolution-policy.md)).

## Prohibited artifacts

- [ ] No ROM, save, savestate, ROM patch, or other build/runtime artifact is
      newly tracked. `python3 scripts/artifact_guard.py --revision HEAD` was run.
- [ ] I am not claiming any tracked source asset (e.g. `graphics/`, `sound/`)
      is legally cleared; see [`docs/issue-resolution-policy.md`](../docs/issue-resolution-policy.md).

## Merge boundary

> Feature and bug-fix PRs require no human review or approval. Passing CI or
> the artifact checker alone is still not complete delivery evidence. Follow
> [the development workflow skill](skills/development-workflow/SKILL.md);
> CODEOWNERS requests are advisory unless an external ruleset enforces them.

- [ ] Candidate Build CI and Copilot review ran concurrently.
- [ ] After merge, automatic Build CI reruns the same combined jobs and
      fail-closed summary before issue closure or remote completion.
- [ ] Every objective acceptance criterion is validated; or the exact
      non-agent-verifiable criterion preventing merge is named.
