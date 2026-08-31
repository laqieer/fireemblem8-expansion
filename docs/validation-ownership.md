# Validation ownership graph

Issue [#180](https://github.com/laqieer/fireemblem8-expansion/issues/180)
is an accepted **framework capability**: a machine-readable, fail-closed map
from admitted repository paths to existing validation evidence. It records
semantic ownership that Git history cannot derive reliably.

The graph is observational. It reports additive owners and review invalidation;
it does not execute a selected gate, skip a gate, or narrow local checks.
Build CI requires the ownership suite and whole-tree check in `host-tests`,
but those checks only validate this contract. Any use for narrower validation
requires a later independently accepted issue with non-inferiority evidence.
Issue #181 is parallel and does not consume or authorize this graph.

## Authoritative files and public commands

- [`.github/validation-ownership-graph.json`](../.github/validation-ownership-graph.json)
  contains typed surfaces, evidence authorities, edges, path rules, named
  exclusions, and authoritative lifecycle events.
- [`scripts/validation_ownership/graph.schema.json`](../scripts/validation_ownership/graph.schema.json)
  is the closed JSON Schema. The stdlib reporter also applies semantic
  invariants that a schema alone cannot express.
- [`scripts/validation_ownership/probe-oracle.json`](../scripts/validation_ownership/probe-oracle.json)
  is the independent sealed probe oracle. Expected surfaces and edge families
  never come from the graph being measured.
- [`.github/validation-ownership-make-dynamics.json`](../.github/validation-ownership-make-dynamics.json)
  is the sealed allowlist for reachable shell-derived Make dependencies. Each
  expression binds tracked tools, input files/variables, automatic inputs,
  optional nonexecuting resolved values, and exact evidence owners.
- [`scripts/validation_ownership/reporter.py`](../scripts/validation_ownership/reporter.py)
  enumerates tracked paths through trusted Git, resolves live authorities,
  emits canonical JSON, and verifies that execution did not change Git state.
- [`scripts/validation_ownership/isolated_launcher.py`](../scripts/validation_ownership/isolated_launcher.py)
  admits only `check`, `resolve`, `tests`, and the closed lifecycle-check mode
  after isolated Python startup and removes ambient `GIT_*` controls.

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

`validation-ownership-check` must be the sole Make goal. A mixed invocation
such as `make validation-ownership-check compare` fails before NODEP or
generated-include suppression can affect `compare`.

## Typed contract

Surface nodes use the closed types `source`, `schema`, `configuration`,
`generated`, and `manual`. Evidence nodes use `host`, `compile`, `link`,
`runtime`, and `manual`. An evidence authority references only:

- a statically discovered Make target;
- a parsed Build workflow job or named step;
- a stable case in `docs/test-cases/registry.json`;
- the typed `scripts.generated_data.registry:REGISTRY`; or
- `.github/manual-testing-handoff.json`.

The graph stores identities, not copied commands. A single root-confined loader
requires every graph, schema, probe, tester-case, manual, generated-data, Make,
and workflow authority to be a Git-tracked regular blob. Recursive literal
Make includes pass through that loader. Workflow jobs and steps remain bound
to the existing strict Build workflow parser. Generated paths and owners come
from registered table schemas. Symlinks, escapes, untracked includes,
non-blob modes, target removal, registry drift, and workflow structural drift
therefore fail without a second command or filename-derived owner registry.

Make authority fingerprints contain normalized target declarations, exact
prerequisite order, ordered recipes, target/global assignment operator and
flavor, ordered repeated assignments, conditional context, and transitively
referenced variable definitions. Comments, nonsemantic spacing, and unrelated
`.mk` targets do not invalidate another target. First-prerequisite swaps,
assignment reordering, operator changes, and false/different conditional
wrapping do. Workflow fingerprints are job/step-specific normalized
structures. Review invalidation reports only edge IDs whose endpoint, type,
owner, target authority, path mapping, or referenced target/job semantics
changed.

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

Coverage uses exact `HEAD` and optional base tree entries, including Git mode,
object type, and identity. `100xxx` regular blobs enter typed exact and prefix selectors
cover stable repository namespaces; the generated-data selector expands from
the real schema registry. Includes and explicit excludes form a partition:
zero matches are unknown, while multiple rule/exclusion matches are ambiguous.
Either condition is an error.

This avoids a hand-maintained list of more than ten thousand files while
keeping overlap and unknown namespaces deterministic. Mode `120000` symlinks
always reject. The `mgfembp` mode `160000` gitlink is
a named fail-closed exclusion because nested ownership and provenance cannot
be inferred from the parent path. Resolving it as a change is an error, not an
empty success; any synthetic gitlink under an owned prefix also rejects.
Changed paths must exist in `HEAD` or the selected base, and a path whose mode
changes between them rejects. Untracked, ignored, and nonexistent paths never
inherit ownership from a matching prefix.

GitHub metadata is partitioned semantically: workflows, governance/schema,
repository host configuration, issue templates, the pull-request template,
and manual handoff are distinct surfaces. `.github/workflows/build.yml`
resolves to the workflow contract step, issue templates to their workflow
tests, and `.github/PULL_REQUEST_TEMPLATE.md` to documentation governance.
`.github/CODEOWNERS` has no deterministic repository consumer, so it is a
named fail-closed external-GitHub-enforcement exclusion rather than a circular
ownership-test claim.

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
pre-graduation triggers each have one later proof bound to the artifact,
dependency edge or decision authority. The public check uses the #176-style
bounded sandbox and isolated launcher: for each trigger it removes the graph,
runs both the declared consumer and consistency identity, requires the fixed
named semantic failure, restores the graph, and reruns both successfully.
Self-declared replacement reasons, fabricated authorities, stale timestamps,
or non-restoring proofs reject.

The independently sealed oracle pins exact `(edge_type, evidence_id)` owner
pairs for runtime, host-only, generated,
localization, configuration, ABI, manual A/V, workflow, governance, templates,
pull-request-template, and repository-config surfaces, plus the exact
CODEOWNERS exclusion. Pair order is normalized before sealing, while duplicate
pairs, unknown families/evidence IDs, same-type wrong owners, target swaps,
stale paths/surfaces, or seal drift reject. Any missing or unexpected owner
pair makes the public check fail rather than emitting a successful report.
The report exposes zero false-negative/false-positive counts plus the bounded
maintenance estimate without modifying issue #176's immutable baseline
fixture or expected report.

Each Make authority also contains a cycle-safe transitive closure of exact
prerequisite targets and matching pattern rules. Nested variables and the
closed nonexecuting prerequisite-function subset (`addprefix`, `addsuffix`,
`patsubst`, `filter`, `filter-out`, `foreach`, `subst`, path transforms,
`sort`, `strip`, and tracked-tree `wildcard`) resolve to concrete prerequisites
before pattern matching. Unsupported dynamic functions, cyclic variables, and
bounded expansion overflow are never discarded: unsupported/cyclic
prerequisites are sealed as explicit unknown records and bounds reject; no
`shell`, `eval`, or recipe is executed. A child recipe, assignment,
prerequisite, or generated-rule change
therefore changes every aggregate authority that consumes it while unrelated
targets remain stable. Target cycles emit a deterministic cycle record rather
than recursing or silently dropping an edge.

Global assignment evaluation preserves one parse sequence. `:=` and `::=`
expand against then-current values; recursive `=` remains deferred; `?=`,
`+=`, target-specific assignments, flavors, and conditionals follow GNU Make
timing. A later `BASE` reassignment therefore cannot change an earlier
`OBJECT := $(BASE)`, while it does affect `OBJECT = $(BASE)`. GNU Make fixtures
pin global, target-specific, append, and conditional variants.

Target-specific recursive `=` and recursive `+=` resolve at target use, so
they observe later global/target values and are inherited by prerequisites;
target-specific `:=`/`::=` and append to a simple value remain immediate.
Target-local bindings participate in recipe fingerprints and escaped
secondary-prerequisite expansion. `?=` follows the visible global/local
definition, while target-specific shell assignments and `override`, `private`,
or `export` modifiers fail closed until explicitly modeled.

Reachable unregistered dynamic prerequisites reject. The live linker authority
has zero unowned dynamics: its `scaninc`, generated item-cap, and
banim-linker shell expressions are represented by the separate registry and
never executed. Contract tool/input/variable changes flow only to Make
evidence that consumes the contract. Variable recursion and expression
nesting have independent limits of 64, with separate word and variant bounds.

Dynamic target declarations are whole-tree authority. No expansion failure is
ignored, even for a target unrelated to the requested root. A registered
dynamic target must have an exact sealed expression, concrete resolved target,
tracked tool/input bindings, and evidence owners; unregistered shell/function,
cycle, malformed, or over-depth declarations reject before closure. Registered
targets resolve without shell execution, and their recipes flow into aggregate
fingerprints.

Domain-separated seals cover the strict schema, probe oracle, complete graph, and resolved
edges plus live evidence-authority fingerprints. Make authority derives from
normalized target semantics; workflow authority derives from normalized parsed
jobs and steps; tester/manual/generated authority derives from real typed
registries. Graph-edge comparison is the review-invalidation source. Filenames,
commit messages, comments, and nonsemantic whitespace are not evidence.

All mutation fixtures use a bounded local clone or synthetic authority root;
tests never overwrite the source checkout's Makefile, workflow, graph, or
oracle and verify their bytes plus Git status even after a simulated exception.
Before any sandbox use, no-follow traversal lstats and opens the repository
root, `build`, `test-artifacts`, and `validation-ownership` components,
rejecting symlinks, non-directories, escapes, and detectable replacement.

## Tester case and compatibility

[`TC-WORKFLOW-GATE-OWNERSHIP-001`](test-cases/workflow-governance.md#tc-workflow-gate-ownership-001-resolve-every-admitted-path-to-complete-validation-ownership)
owns representative resolution, whole-repository coverage, every edge-family
mutation/deletion, stale authority, lifecycle, clean execution, and reporting
controls.

There is no feature flag, save migration, resource allocation, generated game
content change, localization payload change, gameplay/runtime behavior change,
ABI change, or archival-lane behavior change. Reverting the dedicated commit
removes the reporting capability and leaves all broader validation mandatory.
