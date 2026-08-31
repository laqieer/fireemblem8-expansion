# Validation ownership graph

Issue [#180](https://github.com/laqieer/fireemblem8-expansion/issues/180)
is an accepted **framework capability**: a machine-readable, fail-closed map
from admitted repository paths to existing validation evidence. It records
semantic ownership that Git history cannot derive reliably.

The graph is observational. It reports additive owners and review invalidation;
it does not execute a gate, skip a gate, narrow local checks, or change Build
CI. Any use for narrower validation requires a later independently accepted
issue with non-inferiority evidence. Issue #181 is parallel and does not
consume or authorize this graph.

## Authoritative files and public commands

- [`.github/validation-ownership-graph.json`](../.github/validation-ownership-graph.json)
  contains typed surfaces, evidence authorities, edges, path rules, named
  exclusions, lifecycle records, and representative measurement probes.
- [`scripts/validation_ownership/graph.schema.json`](../scripts/validation_ownership/graph.schema.json)
  is the closed JSON Schema. The stdlib reporter also applies semantic
  invariants that a schema alone cannot express.
- [`scripts/validation_ownership/reporter.py`](../scripts/validation_ownership/reporter.py)
  enumerates tracked paths through trusted Git, resolves live authorities,
  emits canonical JSON, and verifies that execution did not change Git state.
- [`scripts/validation_ownership/isolated_launcher.py`](../scripts/validation_ownership/isolated_launcher.py)
  admits only `check` and `resolve` after isolated Python startup and removes
  ambient `GIT_*` controls.

Validate whole-repository coverage without selecting or running any owner:

```bash
make validation-ownership-check
```

Explain one or more changed or deleted paths:

```bash
/usr/bin/python3 -I scripts/validation_ownership/isolated_launcher.py \
  resolve --repository-root "$PWD" \
  --changed src/bm.c \
  --changed src/data/items.json
```

Add `--base-revision <revision>` to derive whether authoritative graph-edge
changes invalidate review evidence. Output is recursively sorted canonical
ASCII JSON with one trailing newline.

## Typed contract

Surface nodes use the closed types `source`, `schema`, `configuration`,
`generated`, and `manual`. Evidence nodes use `host`, `compile`, `link`,
`runtime`, and `manual`. An evidence authority references only:

- a statically discovered Make target;
- a parsed Build workflow job or named step;
- a stable case in `docs/test-cases/registry.json`;
- the typed `scripts.generated_data.registry:REGISTRY`; or
- `.github/manual-testing-handoff.json`.

The graph stores identities, not copied commands. Make targets come from the
same static parser used by documentation governance. Workflow jobs and steps
come from the existing strict Build workflow parser. Generated source paths
and owners come from registered table schemas. Target removal, registry drift,
or workflow structural drift therefore fails without a second command or
filename-derived ownership registry.

The closed edge families are:

| Edge | Required meaning |
| --- | --- |
| `owns-test` | Changed path to its existing positive host evidence. |
| `adversarial-control` | Malformed/boundary fixture owner. |
| `compile-owner` / `link-owner` | Existing compile and link properties. |
| `target-scenario` | Runtime or ABI surface to a real target scenario. |
| `generated-by` | Existing generator target. |
| `drift-check` | Existing generation/schema/round-trip drift target. |
| `generated-consumer` | Existing compiled or linked consumer. |
| `dependent-profile` | Shared contract to dependent supported profiles. |
| `negative-control` | Shared contract to default/disabled control. |
| `manual-handoff` | Supplementary visual/audio/UX judgment route. |
| `depends-on` | Typed surface dependency; cycles fail closed. |

Each surface declares applicable requirements. The reporter converts those
requirements to exact required edge families, rejects missing or inapplicable
edges, and rejects more than one owner for a family. Every evidence identity
is unique. Unknown nodes, keys, enum values, selectors, edge types, paths, and
targets fail closed.

## Maintainable path coverage

Coverage uses Git's tracked path enumeration. Typed exact and prefix selectors
cover stable repository namespaces; the generated-data selector expands from
the real schema registry. Includes and explicit excludes form a partition:
zero matches are unknown, while multiple rule/exclusion matches are ambiguous.
Either condition is an error.

This avoids a hand-maintained list of more than ten thousand files while
keeping overlap and unknown namespaces deterministic. The `mgfembp` gitlink is
a named fail-closed exclusion because nested ownership and provenance cannot
be inferred from the parent path. Resolving it as a change is an error, not an
empty success.

## Manual evidence boundary

Visual, audio, and UX source maps to the existing
[manual-testing handoff](../.github/manual-testing-handoff.json). The reporter
requires that contract to keep deterministic criteria false and semantic
assertions primary. A manual surface must also retain positive, adversarial,
compile, link, and deterministic runtime edges. Manual evidence cannot replace
those owners, and this implementation has no manual-only acceptance criterion.

## Artifact lifecycle, measurements, and seals

The graph uses issue #176's admission fields: one owner, executable consumer,
unique decision, consistency check, bounded maintenance estimate, deletion
criterion, expiry, and disposition history. Checkpoint, dependency-change, and
pre-graduation deletion proofs must agree with the current disposition, share
one semantic reason, precede the disposition, fail while this graduated
artifact is absent, and pass after restoration.

Representative runtime, host-only, generated, localization, configuration,
ABI, and manual A/V probes measure missing and unexpected edge-family
selections. The report exposes those false-negative/false-positive counts plus
the bounded maintenance estimate without modifying issue #176's immutable
baseline fixture or expected report.

Domain-separated seals cover the strict schema, complete graph, and resolved
edges plus live evidence-authority fingerprints. Make authority derives from
the actual tracked Makefiles; workflow authority derives from parsed jobs and
steps; tester/manual/generated authority derives from their real registries.
Graph-edge comparison is the review-invalidation source. Filenames, commit
messages, comments, and ordering are not evidence.

## Tester case and compatibility

[`TC-WORKFLOW-GATE-OWNERSHIP-001`](test-cases/workflow-governance.md#tc-workflow-gate-ownership-001-resolve-every-admitted-path-to-complete-validation-ownership)
owns representative resolution, whole-repository coverage, every edge-family
mutation/deletion, stale authority, lifecycle, clean execution, and reporting
controls.

There is no feature flag, save migration, resource allocation, generated game
content change, localization payload change, gameplay/runtime behavior change,
ABI change, or archival-lane behavior change. Reverting the dedicated commit
removes the reporting capability and leaves all broader validation mandatory.
