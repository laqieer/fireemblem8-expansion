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
  admits only `check`, `resolve`, `tests`, and the closed `authority-check` mode
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
runs metadata integrity plus the declared consumer/consistency identities
against the two executable artifact states. Removal must produce the fixed
named semantic failure, restoration must pass, and those bounded behavioral
results are attached to every trigger-specific proof record.
Self-declared replacement reasons, fabricated authorities, stale timestamps,
or non-restoring proofs reject.
The lifecycle consumer now calls the same nonrecursive complete
`validate_graph` path as the public checker, then performs the independent
oracle measurement. Make/workflow authorities and exact resolved owner pairs
are therefore checked on every restored proof without invoking the lifecycle
driver again. Broken/stale edges, owners, Make contracts, workflows, or oracle
pairs fail the proof rather than passing an artifact-byte-only shortcut.

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

Make authority comes from `/usr/bin/make`, not a repository implementation of
Make syntax. The reporter creates a new scratch root from the selected exact
Git tree, materializes the exact `mgfembp` gitlink commit from the shared object
database, and starts GNU Make through user, mount, PID, and network namespaces.
Every in-repository scratch component is created and reopened relative to a
trusted directory descriptor with `O_NOFOLLOW`; an existing file or symlink at
`build`, `test-artifacts`, or `validation-ownership` rejects before a probe,
temporary directory, deletion, or external write.
The superproject, gitlink trees, and final `/probe/probe.mk` program are
read-only mounts. Only separate ignored Make scratch, registered-command
scratch, and generated `build` output mounts are writable. Domain observations
and supervisor control use bounded FIFOs outside all candidate-visible mounts;
candidate parse-time `$(file ...)`, include, shell, symlink, and eager-command
attempts cannot replace either the probe program or its results.
Missing
namespace support, the fixed absolute tools, the exact gitlink object, or the
statically built command interceptor fails before Make starts.
The launcher first probes unprivileged user namespaces. On runners that block
them, it permits only exact passwordless `/usr/bin/sudo -n /usr/bin/unshare`;
after mounting and chrooting, the trusted launcher clears supplementary
groups, the capability bounding/permitted/effective sets, and keep-caps, drops
to the frozen original runner UID/GID, sets no-new-privileges, and only then
executes candidate Make. Candidate code never runs as host root. The selected
launcher mode, absolute binaries, versions, and executable digests are part of
authority; if both paths fail, the probe fails closed.

The probe binds the SHA-256 and version of `/usr/bin/make`, its normalized
argv, empty/scrubbed environment, `C` locale, UTC timezone, fixed source-date
epoch, every tracked Make input path and mode, the interceptor source/compiler/
binary identity, and the source-built `tools/gbagfx/gbagfx` authority. GNU Make
runs with `-n -B --trace --debug=v`; built-in implicit rules remain active, but
tracked inputs have explicit no-remake rules so `-B` cannot invent an unrelated
makefile-remake chain. The resulting concrete considered-target graph, pattern
stems, terminal tracked/gitlink/generated inputs, expanded recipes, includes,
errors, and normalized trace records form the authority fingerprint. Scratch
prefixes and source line numbers are normalized; comments and unrelated target
changes remain stable when GNU Make's observed semantics remain stable. GNU
Make database `Last modified` diagnostics from fresh copied trees are excluded
as nonsemantic execution timestamps; rule, prerequisite, recipe, and status
records remain bound.
The in-process optimization cache first keys the selected live tree by every
tracked path, mode, Git/content identity, and stable device/inode/size/time
descriptor. Each execution namespace then comes from the completed copied
snapshot's raw bytes and current modes, not a pre-copy hash. Revision blobs are
verified against their Git object IDs and copied gitlinks against their exact
commit trees. Source descriptors must remain equal before and after probing,
and every parallel target probe must report one identical completed-snapshot
digest, or validation rejects. Wildcard-visible ordinary file
addition/removal, regular-to-symlink or executable-mode changes, and same-size
mutations therefore cannot reuse an earlier result. The same Git/content state
decides whether prior Make authority can be reused for invalidation.
Each probe uses a new random child and ignores incomplete scratch children left
by an interrupted process.

Schema version 5 seals each external selector as either a finite exact domain,
an exact tracked fallback, or symbolic recipe/environment-only authority.
Every one of the 112 prerequisite domains is run through a real command-line
GNU Make invocation; environment-sensitive domains are also run through a
clean process-environment origin. The baseline plus all variant traces are
unioned per evidence target. An unclassified external input, stale symbolic
classification, oversized domain, missing target, alternate active error, or
failed variant rejects. Symbolic recipe inputs are retained in the fingerprint
but are admitted only when the closed reference-position census proves that
every transitive use is confined to a recipe/environment payload. A use in a
target, prerequisite or secondary expansion, include, conditional,
target-specific assignment, or `eval` expansion requires a finite domain even
when the registry mislabeled it as symbolic.

The named external-selector syntax census is the one irreplaceable lexical
Make boundary: a closed comment/recipe/`define`-aware parser extracts static
`?=` names, direct and secondary-expansion references, definition
dependencies, and graph-versus-recipe positions from every GNU Make-loaded
authoritative input. It rejects dynamic left-hand sides and conservatively
classifies computed names. It decides no target or recipe semantics;
behavioral mutation tests prove each classified graph position through real
GNU Make. GNU Make remains the behavioral authority, runs with
undefined-variable diagnostics, and rejects every evaluated undefined name
not covered by a finite/symbolic registry entry or a typed
builtin/automatic/scoped contract. This covers defaults and references reached
through `define`, `eval`, `call`, `foreach`, and computed names without relying
on identifier spelling as behavior evidence. The probe emits domains, symbolic
names, generated paths, and typed-variable census fields only from loaded
source and baseline/variant observations. Registry entries are expected
inputs, never synthesized observations, so an unused domain or stale generated
path rejects. Variant discovery is a deterministic fixed point: every concrete
fallback/domain/origin state contributes its loaded includes and trace sources,
defaults, use positions, closure, and generated prerequisites. A newly loaded
source or newly observed finite domain is evaluated under the exact parent
assignment context, so nested `MODE` -> `DEP` -> include selectors cannot hide
behind fallback-only parsing. Branch-only undeclared or graph-shaping symbolic
selectors reject immediately; branch-only recipe symbolic inputs contribute
authentic recipe-only census evidence, and recipe-only finite domains remain in
the observed census without spawning closure-expansion variants. Definition-dependency expansion is
scoped to the authoritative sources GNU Make actually loaded, so an alternate
branch cannot backfill an unobserved selector into another branch's census.
Process-environment variants are likewise driven by authentic observations:
only names that the loaded sources treat as ambient defaults or actually
undefined authority spawn environment-origin graph variants; explicit
Makefile-assigned graph selectors do not.
State, combination, depth, source,
domain, subprocess, and one-hour per concrete standalone-target or
combined-root fallback/domain/origin probe-state bounds all fail closed on
exhaustion. For bounded performance, the reference graph conservatively
narrows each solo target's candidate domains;
an actual GNU Make database comparison over the combined roots establishes
whether any requested target is `MAKECMDGOALS`-sensitive. Standalone checks
remain authoritative for every concrete fallback/domain/origin state. Any
changed database that can alter loaded sources, closure, or recipe payload is
re-evaluated with the standalone target; a graph-only external name with an
unchanged discovery signature keeps the changed database fingerprint while
reusing the fallback recipe semantics.

This means GNU Make itself owns conditionals, `eval`, pattern/static-pattern
resolution, `define`/`call`, target-specific and inherited values, `${NAME}`,
one-character `$C`, automatic variables, secondary expansion, assignment
flavors/modifiers, and include rebuilding. Tests compare positive behavior to
direct GNU Make and freeze alternate `MODE=two`, `$(eval $(RULE))`, concrete
`%.out: %.in` stems, target-local prerequisites, and expanded `$@`/`$<`
recipes. A literal missing prerequisite and active `$(error)` surface the real
Make failure rather than becoming metadata.
Each evidence target is invoked as the sole requested goal for fallback and
every domain/origin variant. No combined `MAKECMDGOALS` result is attributed
to another target; target order and set iteration therefore cannot change a
record.

GNU Make can execute parse-time `$(shell)`, `!=`, and makefile-remake recipes
even in otherwise nonexecuting modes. The sandbox therefore exposes no general
shell to Make: `SHELL` is the statically compiled interceptor, and the root
contains only GNU Make, its loader/libc, and that interceptor. Every attempted
command is logged. A command must match exactly one sealed registry regex.
Fixed commands receive their registered output; source-dependent commands run
once in a second networkless read-only `/usr` + exact-tree command sandbox,
with only the scratch build overlay writable, and their concrete output digest
is fingerprinted. Normal `-n` recipes produce no interceptor event. Any
include-remake, recursive, eager, or direct expansion that GNU Make does
attempt is authorized only when the concrete command matches exactly one
sealed contract; the same text appearing in trace output, a recipe, or
normalized multiline output grants no authority. Unknown direct recipe
expansion, unregistered shell/eager assignment,
ambiguous command registration, nonconvergence, or sandbox failure rejects.
The interceptor receives fixed event/domain FIFO descriptors and a read-only
mapping-directory descriptor opened by the trusted launcher before chroot.
Neither descriptor has a pathname through `/proc` or `/dev/fd`, and no event
or mapping path appears in candidate environment or scratch. The supervisor
stream-parses events while Make runs and terminates the process group on byte,
record, argument, pending-command, mapping-count, mapping-byte, mapped-output,
or future-batch overflow. Every record's format, mapping count, command hash,
match identity, and strict UTF-8 text boundary is revalidated. Registered
command output remains raw bytes through capture, cache, hashing, mapping, and
replay; only consumers that require Make/parser text decode it, strictly.
Registered commands run in a different scratch namespace with both control
descriptors closed. Candidate `$(file ...)` writes and registered scripts can
only create decoys in their own scratch; they cannot read, overwrite, or forge
supervisor events or mappings.

Generated-data path classification likewise does not import the base
`scripts.generated_data.registry` into the trusted reporter. A small trusted
probe executes each exact candidate schema separately in a credential-free,
networkless, read-only-tree process with only isolated command scratch and the
Python executable, standard library, loader, and platform libraries mounted;
other host tools and `/usr/share` are absent.
Directory sources require a finite declarative basename glob. A whole-tree
supervisor inotify observer independently records every opened candidate file
and directory. List and metadata operations may open only the bounded registry
implementation set; each loader must use that same observed program set, may
list only its declared directory source, and must make its remaining regular
tracked reads equal both the declared file/glob expansion and the
loader-reported paths exactly. Omitted, extra, duplicate, dynamic, symlink,
escaping, and undeclared outside-root-set reads or directory scans reject. The reporter accepts only bounded strict
UTF-8 JSON with the closed typed record schema, sorted unique names and
dependencies, valid versions, and confined tracked paths. It binds the
candidate registry AST/blob set, exact typed output, supervisor-observed
program path hashes, launcher, and Python identity. Newly declared candidate
generated paths therefore classify from candidate authority, while candidate
code cannot mutate or enter the trusted gate process.

Every Make include observed by GNU Make must be the primary `Makefile`, a
tracked regular `.mk`, an exact gitlink-contained file, the trusted probe
control file, or a canonical regular descendant of the generated `build/`
overlay. Include spelling is normalized against conceptual `/repo` before
classification; dot/dot-dot, repeated or encoded separators, absolute/dynamic
aliases, missing paths, and any symlink component reject. Generated asset
discovery and manifest includes begin from an empty overlay and are rebuilt
only through registered confined commands; no live source file is written.

The public gate has a smaller independent bootstrap at the first byte of the
root Makefile. Command-line ownership of `MAKECMDGOALS` rejects before goal
selection. A trusted exact sole goal selects `validation-ownership-check`;
mixed goals and `-n`/`-t`/`-q`/`-s`/`-i`, aliases, hostile control variables,
or any command-line override reject before `AUTOTOOLS_CONFIG_MK`, generated
includes, config fragments, shell expansions, or dependency remakes. The sole
goal parses no normal build includes. Consequently `MAKECMDGOALS= -n` and
`AUTOTOOLS_CONFIG_MK=/dev/stdin SHELL=...` cannot hide or replace the checker,
while `make validation-ownership-check` either executes the isolated checker
or fails during trusted bootstrap.

Candidate CI does not use these candidate-authored modules as its own trust
root. On pull requests, `host-tests` first checks the exact GitHub PR-base
commit for `ci_verifier.py` and `ci_gate.mk`. When present, it creates an
unpredictable mode-`0700` directory under the lstat-checked GitHub runner
temporary root, records its device/inode identity, and archives the complete
clean base tree there. It never removes or creates a verifier staging path
through the candidate checkout; base and candidate Make/registry probes use a
mode-`0700` runtime child under the same external trusted root, and cleanup
removes only that unchanged external identity. The base gate verifies every
staged verifier package file and every loaded transitive `scripts.*` module against base Git objects,
excludes the candidate checkout from `sys.path`, and overlays the base
validation package/schema/oracle while reading all other graph and Make
authority from the exact candidate commit. Candidate modifications to
`reporter.py`, `make_probe.py`, the interceptor, or their tests therefore
cannot authorize themselves. After trusted validation of both exact-base and
candidate graphs, the verifier resolves every independent-oracle probe,
requires byte-identical `(edge_type, evidence_id)` selections, compares the
resolved base/candidate authority fingerprints, and intersects trusted
graph-edge invalidation with the oracle-backed edge set. Every graph surface
and every non-dependency owned edge must be represented by a real tracked-path
probe; changed dependency or other unrepresented edge authority fails rather
than bypassing comparison. Any authority target,
gate, Make target/command/probe, workflow step, or fingerprint redirect on an
oracle-backed edge therefore rejects even when stable IDs and pairs remain.
Normalized unrelated workflow or Make semantics do not invalidate those
edges.
Before its first direct Git command, the hosted step unsets the exact ten
path-bearing Git redirects while retaining the explicit no-config,
no-replacement, and no-lazy-fetch settings. Empty or hostile inherited
`GIT_DIR`, work-tree, common-dir, index, namespace, object, replace-ref,
ceiling, exec-path, and alternate-object variables therefore cannot redirect
or break candidate/base identity checks.
This introducing PR's base lacks that package,
so both hosted CI and the candidate-staged local verifier emit
`bootstrap-not-authoritative` with `authority` set to `none`; candidate tests
and the public gate still run, but direct adversarial review is the
introduction evidence. The exact-base capability check iterates the same
complete runtime/schema/graph/oracle path list mirrored from the verifier.
Zero present paths means introduction mode; any present path with any missing
peer—including a lone Make-dynamics registry—rejects rather than downgrading.
After merge, every ordinary PR
enters `exact-base-pinned` mode. Git remains the identity authority; no
source-hash ledger is committed.

Domain-separated seals continue to cover the strict schema, probe oracle,
complete graph, resolved edges, and live evidence-authority fingerprints.
Graph-edge comparison remains the review-invalidation source; the graph still
does not execute or narrow any selected validation gate.

## Tester case and compatibility

[`TC-WORKFLOW-GATE-OWNERSHIP-001`](test-cases/workflow-governance.md#tc-workflow-gate-ownership-001-resolve-every-admitted-path-to-complete-validation-ownership)
owns representative resolution, whole-repository coverage, every edge-family
mutation/deletion, stale authority, lifecycle, clean execution, and reporting
controls.

There is no feature flag, save migration, resource allocation, generated game
content change, localization payload change, gameplay/runtime behavior change,
ABI change, or archival-lane behavior change. Reverting the dedicated commit
removes the reporting capability and leaves all broader validation mandatory.
