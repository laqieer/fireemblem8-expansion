## Frozen scope

- Issue: <!-- exact https://github.com/laqieer/fireemblem8-expansion/issues/N -->
- [ ] Scope is itemized and non-goals are explicit.

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

- [ ] Every objective acceptance criterion is validated; or the exact
      non-agent-verifiable criterion preventing merge is named.
