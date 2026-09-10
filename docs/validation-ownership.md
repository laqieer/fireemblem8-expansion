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

Graph test discovery uses unittest's package `load_tests` protocol to exclude
the foundation, producer, dependency and metadata codec modules. They run once in
the required `ownership-probe-test` owner in `extended-host-tests`; they are
not repeated by the graph gate or workflow discovery. The parsed discovery
partition requires every test ID to retain exactly one execution owner.

Complete integration of the shared
[issue #206 / PR #212 foundation](https://github.com/laqieer/fireemblem8-expansion/pull/212)
is a required dependency, not optional hardening. Its small real consumer and
the asset-specific host tests do not prove the full 112-domain graph, oracle,
lifecycle or public command. Registered native-tool results and generated Make
include outputs must reach that shared observer through admitted APIs before
this acceptance can pass; an older duplicate sandbox or fabricated empty
command output is not a substitute.
The primary-source discovery API from
[#234 / #235](https://github.com/laqieer/fireemblem8-expansion/pull/235)
is also a genuine prerequisite. It must be available in the actual selected
BASE before directory-backed registry comparison is qualified. No CURRENT
schema implementation is substituted into a historical BASE, and introducing
the API does not claim that older trees already supplied it.
The graph reads generated include bytes from the completed
`MakeObservation.generated` result corresponding to the actual
`MAKEFILE_LIST`. Resolving a registration does not execute its producer
in advance. Native dispatch, source receipts, replacement and cleanup remain
owned by P; no preparatory sample supplies the graph's source census.
The graph also checks actual repository file-open attempts, not only the
successfully loaded `MAKEFILE_LIST`. An unknown optional include cannot acquire
authority merely because Make ignored its absence. Attempts must resolve to
captured regular sources or actual completed producer outputs; known generated
include/remake behavior remains valid. Syscall spellings and real metadata
remain intact, rather than being inferred from a new Make parser.

Asset discovery uses the [captured-source API](asset_manifest.md#captured-source-discovery)
with immutable path/mode/content identities, not candidate claims or validation
bypasses. Its digest is stable across equal Git materializations; ordinary CLI
stamping and full metadata/cache validation remain unchanged. Exact declared/
consumed inputs and private outputs stay enforced.
Issue [#238](https://github.com/laqieer/fireemblem8-expansion/issues/238)
now owns the reusable Python command closure, registry-source selection and
generated-dependency publication API in
`scripts/validation_ownership/python_commands.py`. The graph consumes that
shared seam rather than carrying its own duplicate importer/depfile adapters.
Shell continuation normalization is still graph-local integration because it
binds the sealed Make-command spellings to the shared typed registrations.
Python command registrations explicitly declare the repository root, the
adapted command's actual repository import closure, and the import directories
needed for that closure through `Command.directories`. Enumeration uses the
complete active immutable view, never a sparse code-only tree; seeing a member
name does not grant permission to read its content. Registry and asset controls
exercise both properties. Root enumeration admits gitlink namespaces only by
capturing their actual recorded pins from the repository's common-Git module
databases.
Registry declaration execution now has an explicit shared-session entry:
`graph_registry.observe_declarations(loader, session)`. The selected loader
must be the active public view and own the same budget. It invokes the real
candidate `REGISTRY` through a confined `Command`; the reporter then validates
the declaration fields and their captured source paths. A foreign view, stale
source or invalid schema identity rejects rather than becoming an empty registry.
Directory-backed primary inputs use the schema's metadata-only `source_paths`
selector, shared with its ordinary loader. File-backed primary inputs still run
the existing strict `load_records` consumer, so declared, reported and consumed
paths must agree there too. Discovery grants no directory-wide member contents,
and matching bundle additions/deletions follow the selected CURRENT/BASE view;
a nonmatching member is not classified as generated merely because it shares
the directory.
Modern link-library directory shells preserve the selected compiler's complete
optional `-B`, architecture and metadata-query argv. The root-owned system
`arm-none-eabi-gcc` image and required library aliases are captured, revalidated
and executed through the existing compiler confinement; the resulting stdout
is reduced with ordinary shell `dirname` semantics before Make consumes it.
The runtime receipt participates in the dynamic-command observation. A failed
required query rejects with its real nonzero status instead of becoming a
successful empty producer. Unsupported compiler modes, compiler names or
binutils roots reject before execution.

The build framework still supports checkout-local toolchain roots for ordinary
modern builds. The ownership reporter does not execute a checkout-local
filename match: those roots currently have no trusted installed-tool identity
contract equivalent to the root-owned system package capture. Selecting one
for an ownership-observed directory query therefore returns that precise
compatibility error rather than granting candidate execution authority.
The system compiler capture itself is optional at session setup: a host owner
without the ARM package retains the genuine absent `/bin`/`/usr/bin` alias
observation and can run non-toolchain graph controls. A directory query still
requires the captured compiler and fails rather than fabricating coverage.

Deleted-path resolution requires the selected BASE ownership model, not just
BASE's filename inventory. It uses that model's graph rules, generated-source
classification and evidence owners. CURRENT still enforces semantic admission;
providing BASE context cannot admit a newly tracked CURRENT file by prefix.
The report-level orchestration must supply this model from its grouped BASE
view before deletion explanations can be accepted.
The asset integration control captures the real three-record manifest as BASE,
then removes the battle record and one of its tracked inputs from CURRENT.
Both generated includes reach actual GNU Make and each restarts once. A single
public `select_view` block restores BASE's real deleted source and battle
consumer ID, without resetting the report budget or changing source paths.
That source/output test is not a substitute for the reporter's BASE ownership
model, full112 domains, oracle, lifecycle or independently captured public gate.

The scanner adapter similarly reuses `SourceFile::GetIncludes` and
`ScanIncDependencies` from `tools/scaninc`. The ordinary CLI still uses real
file availability and its original include-search order. The graph's native
driver discovers direct includes with the actual parser, resolves availability
from the same immutable capture, then runs that same dependency traversal with
the exact admitted source closure. This avoids asking a confined process to
open undeclared absent candidates such as
`include/asm/macros/music_voice.inc`; it does not copy the scanner parser or
invent dependency output. Missing initial inputs, escaping includes and
symlink/gitlink matches fail. Native command consumption must equal its
declaration, and the resulting dependency text must reach actual GNU Make.
The four scanner C++ sources, four headers, native wrapper and scanner Makefile are trusted code,
not candidate dependency data. Before creating `ProbeSession`, the verifier
requires their candidate regular Git blobs and modes to match the independently
selected `source_sha`. Compilation admits only that explicit closure, never
extra files selected by a candidate directory listing. Intentional scanner
evolution requires a reviewed source revision containing the approved change;
ordinary candidate source data and the independently approved BASE view remain
separate. This is a narrow compiler-code authority boundary, not a whole-tree
identity gate or a committed content-hash ledger.
The named scanner-build contract admits literal `=`/`:=` assignments, the
four-source/four-header `g++` profile and the existing scaninc/clean recipes.
Executable text permits only ASCII space, tab and LF grammar; NUL, CR,
non-ASCII separators and non-Make controls reject, while printable Unicode is
inert only after an ASCII `#`. Parsing precedes Make evaluation, so extra
flags, sources, functions or recipes cannot execute. Assignment order,
equivalent braces and safe comments retain ordinary behavior.
Include names are resolved only after joining each original search directory,
so repository-contained parent components in real banim sources remain valid.
The planner checks every intermediate component against the captured namespace
before collapsing `..`; an absent or non-directory prefix cannot become a
different existing file. Canonical paths bind source admission, while the
original accepted search spellings are passed to the native traversal and
retained in its ordinary output. Alias traversal remains bounded, and escaping
or nonregular namespaces still reject. The line-framed include helper rejects
embedded line breaks or NUL rather than splitting one pathname into claims.
Ordinary-versus-adapted comparisons use equivalent captured input metadata, not
an ambient worktree containing untracked files. Native adapter unit tests and
standalone producer successes are not whole-root Make acceptance.
Registered `find ... -type f -name ...` discovery keeps the real depth-first
directory traversal and captured source equality, but issues bounded Linux
`getdents64` requests directly instead of inheriting Python `scandir`'s 32 KiB
readdir buffer. Each request is 4096 bytes; the supervisor still records and
accounts for the complete requested before/after buffers, offsets, results and
EOF calls. Regular-file filtering does not follow symlinks, and `DT_UNKNOWN`
entries use an exact no-follow metadata fallback. Nested, Unicode,
nonmatching, multi-batch, missing and nonregular controls compare against the
ordinary `find` behavior. The small-directory traffic proof requires at least
a 50 percent real control/metadata reduction under unchanged production
limits; it is not a claim that the complete ownership report now fits.
The host C dependency adapter preserves the original ordered `cc -E -MM`
include/define arguments and the declared `.dep` destination through
`Command(dependency_only=True)`. An explicit captured header-code pool discovers
the real compiler closure; unused headers do not become dependency provenance.
The actual dependency file must reach GNU Make, with its original prerequisite
order and genuine restart. This uses neither an ARM/agbcc executable nor a
copied preprocessor, fake `.d` file or re-exec from a Python command capsule.
The root Make dependency query invokes the existing `$(PYTHON)` interpreter
explicitly rather than relying on an executable script's shebang. This keeps
ordinary output unchanged and lets the registered command reach Make without
granting execution on the captured source mount. The full linker itself is
not executed by the dependency adapter; its real `-m` CLI reads the linker
script and returns the prerequisite list.

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
  optional nonexecuting resolved values, and exact evidence owners. Its
  expressions and command patterns are confined execution-admission contracts,
  not a second inventory of validation gates. Authoritative GNU Make emits the
  actual expanded command; exactly one sealed pattern must select a typed
  adapter, which then validates its argv and source closure before execution.
  Merely observing an arbitrary candidate command cannot grant it execution
  authority, and this registry never selects, skips, or narrows a validation
  gate.
- The `scripts/assets/` implementation package retains asset generation, drift,
  compilation and linked-consumer owners, separately from authored A/V
  judgments. Its `tests/` namespace remains host-test code, matching the
  existing `ASSET_TOOL_INPUTS` exclusion. `assets.mk` additionally preserves all
  prior configuration/default-disabled/profile/boot owners while retaining
  the asset pipeline relationships; this is not a weaker generic host mapping.
- [`scripts/validation_ownership/reporter.py`](../scripts/validation_ownership/reporter.py)
  enumerates tracked paths through trusted Git, resolves live authorities,
  emits canonical JSON, and verifies that execution did not change Git state.
- [`scripts/validation_ownership/isolated_launcher.py`](../scripts/validation_ownership/isolated_launcher.py)
  retains the foundation consumer's no-mode/options entry and admits `check`,
  `resolve`, `tests`, and the closed lifecycle-check graph modes after isolated
  no-site Python startup. It removes ambient `GIT_*`, Make preload/flag/override,
  and shell-startup controls before entering its payload.
  Report options are parsed once. The controlled non-symlink repository root
  is resolved before changing directory, and the same normalized namespace is
  passed to the reporter. Relative roots, equals forms and accepted
  abbreviations cannot acquire a different meaning through a second parse.
  The BASE-staging guard uses the existing parsed workflow structure to select
  one verifier in `host-tests` and compare its full step and job context.
  An inert or duplicate textual copy cannot stand in for that executed step;
  independent coordinator invocation remains a separate mandatory boundary.

Validate whole-repository coverage without selecting or running any owner:

```bash
/usr/bin/python3 -I -S -B scripts/validation_ownership/isolated_launcher.py \
  check --repository-root "$PWD"
```

Explain one or more changed or deleted paths:

```bash
/usr/bin/python3 -I -S -B scripts/validation_ownership/isolated_launcher.py \
  resolve --repository-root "$PWD" \
  --changed src/bm.c \
  --changed src/data/items.json
```

Add `--base-revision <revision>` to derive whether authoritative graph-edge
changes invalidate review evidence. Output is recursively sorted canonical
ASCII JSON with one trailing newline.

For a trusted Make invocation, `make validation-ownership-check` remains a
convenience alias and must be the sole goal. A mixed invocation
such as `make validation-ownership-check compare` fails before NODEP or
generated-include suppression can affect `compare`.
It is not a pre-evaluation boundary: GNU Make processes ambient `MAKEFILES`
and command-line `--eval` before reading the root Makefile. Use the standalone
entry above for untrusted evaluation; do not prepend a Make invocation.

The host Build setup installs `build-essential`, `libmgba-dev`, `libpng-dev`,
`python3-venv` and `pkg-config`, plus `binutils-arm-none-eabi`,
`gcc-arm-none-eabi` and `libnewlib-arm-none-eabi` for the real sealed modern
library-directory queries. The ownership consumer also compiles native
`gbagfx` against `png.h`, libpng and zlib, so the ARM compiler alone is
insufficient; `libpng-dev` supplies its development dependency closure on the
supported Ubuntu host, and its existing Makefile queries libpng through
`pkg-config`. Installing the query compiler does not opt the explicit
`GBA_PLAYTEST_HOST_ONLY=1` suite into full project ROM builds. The concurrent
custom-spell profile build uses the existing live-artifact class guard and
registry; its configuration/host checks still run, and normal-mode build
behavior is unchanged. Use the existing
[pinned host Python environment](workflow-pilot.md) for local host tests.
Both `user-namespace` and the supported `sudo-drop` launcher are valid
observations. Missing native dependencies or unavailable confinement remain
errors; a platform-specific mode assertion must not turn a supported fallback
into a failure.

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
changed. A semantic change to the schema or the independently sealed oracle
invalidates every current edge, even when the graph declarations and owner
fingerprints remain unchanged. Adding, removing, or semantically changing a
fail-closed exclusion likewise invalidates every existing edge: exclusion
authority is part of whole-tree admission even when no oracle probe names the
new exclusion. Exclusion-list, selector-list, JSON whitespace, and object-key
reordering with equal parsed semantics do not invalidate review.
The artifact's ownership/consumer/consistency/disposition record and lifecycle
event authority are also part of this comparison. A valid change to either
invalidates all existing owner edges, even if edge declarations themselves
are unchanged. Event-set and object-key ordering are nonsemantic; artifact
history ordering and every authoritative record field remain significant.

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
object type, and identity. Prefixes describe the initially admitted cohort,
not permission to classify every future filename. That cohort is derived from
the unique graph-introduction tree in the selected Git history; no path list,
commit pin, blob list or content-hash ledger is stored beside Git. Missing or
ambiguous introduction history fails closed.

A later tracked path needs an exact selector in the existing ownership graph,
actual generated-source registry membership, or the canonical verifier-runtime
source registry. Thus a newly tracked `src/foo.c` fails despite `src/`, and
renaming or rearranging the prefix rule does not admit it. An exact selector
records the semantic owner choice and still requires the surface's complete
positive/adversarial/build/runtime roles. Known post-introduction framework
components have explicit entries in their existing semantic rules, not a
second inventory registry.

Includes and explicit excludes still form a partition: zero matches are
unknown, multiple rule/exclusion matches are ambiguous, and a prefix-only
new path lacks semantic admission. All three are errors. Admission is checked
for whole-tree coverage before Make-authority execution and again when
resolving a current path; successful resolutions report the admission kind.
Every exact path-rule selector, in either the include or exclude role, and
every exact top-level exclusion selector must
name a current captured-tree member or an explicitly admitted generated-source
member. Deleting that member without removing the selector is a stale ownership
target and rejects even when no oracle probe names the path. Prefix selectors
are namespaces and need not currently match. Removing the stale exact selector
is the repair; deleted-path reporting still resolves through the separately
validated BASE graph/model and reports `selected-base-tree`.

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

The eight-job Build retains both ownership host gates and the complete mirrored
local gate inventory. Patch packaging is a master-only step in `build`, not a separate
publisher job or local gate. The existing `surface.host` mapping covers
`scripts/modernize/package_ci_patch.sh` and
`tests/workflows/test_patch_release_workflow.py`: the host job runs the
workflow suite's real packaging/producer checks, including invalid-input,
wrong-context and cleanup-failure controls. Both paths have independent
oracle probes; no new routing surface or copied command list is introduced.
The removed `scripts/workflow_pilot/publisher_shell_contract.py` cannot resolve
as a current path. With an explicitly selected base that contained it, a
deletion still selects the complete host owner pair rather than an empty
successful result. A `patch-release` workflow-job authority is stale and
rejects; historical skipped-job compatibility is not a live graph owner.
This is the [trusted build-once packaging contract](patch_release.md), not
the withdrawn malicious-build namespace or supervisor proposal.

## Manual evidence boundary

Visual, audio, and UX source maps to the existing
[manual-testing handoff](../.github/manual-testing-handoff.json). The reporter
requires that contract to keep deterministic criteria false and semantic
assertions primary. A manual surface retains positive and adversarial host
roles plus its **applicable** build, generated and observing runtime roles.
An unrelated runtime scenario is not evidence merely because it boots a ROM.
The independent oracle rejects missing or substituted owner pairs, including
removing both an applicable requirement and its edge. Manual handoff cannot
replace reliable deterministic automation; this source-only graph correction
has no manual-only acceptance criterion.

The A/V partition follows actual consumers, not directory-name similarity:

| Inputs | Deterministic owner and observation |
| --- | --- |
| The eleven exact main-title inputs in `paths.title-visual` | `Title_SetupMainGraphics` in [`src/titlescreen.c`](../src/titlescreen.c) consumes these graphics, palettes and maps. `expansion-modern-title-check` compares four framebuffer checkpoints in the default debug/release title-progression profiles. Skipped intro artwork does not inherit this edge. |
| Three `LORM_SP1_PROOF` package inputs from [`assets/manifest.json`](../assets/manifest.json) | `assets-test`, `assets-generate`, `assets-check` and the existing compiled consumers retain generated evidence. `expansion-modern-banim-package-runtime-check` observes alias selection, script/palette/OAM consumption and one battle entry/completion against a zero-state control. This is **not** an image or audible-sound oracle. |
| Eirika's three formatted-package inputs and four existing component aliases | The same asset generation/build roles apply. `expansion-modern-portrait-package-runtime-check` observes the face/minimug ID, render count, palette/VRAM words and mouth activity. It does not verify every portrait or an entire framebuffer. |
| Remaining `assets/` authoring inputs | Existing host, selected-manifest generation/drift and compile/link consumer roles remain. Arbitrary manifests have no single observing runtime scenario in this graph. |
| Remaining `banim/`, `graphics/` and `sound/` inputs | Existing host and compile/link roles remain, with the canonical manual handoff for material A/V judgment. No runtime pixels or audio are claimed. |
| `preview/` | Review-only TSA pairing/coherence material, as documented in [`preview/README.md`](../preview/README.md). Host tooling/documentation roles and manual handoff remain, but these files have no ROM compile/link/runtime consumer. |
| `.github/manual-testing-handoff.json` | Governance host evidence, not a rendered/audio asset or a ROM scenario. |

[`run_banim_presentation_checks.py`](../tools/gba-playtest/run_banim_presentation_checks.py)
records HP transitions and presentation-policy counters with framebuffer
capture disabled and no audio assertions. Its existing authority remains
registered, but no asset path selects it. New tracked paths in the existing
generic A/V namespaces require semantic admission and cannot inherit an exact observed-consumer rule;
untracked paths, unknown namespaces and named exclusions still fail closed.
The graph remains report-only and does not reduce any broader validation.

## Artifact lifecycle, measurements, and seals

The graph uses issue #176's admission fields: one owner, executable consumer,
unique decision, consistency check, bounded maintenance estimate, deletion
criterion, expiry, and disposition history. Checkpoint, dependency-change, and
pre-graduation triggers each have one later proof bound to the artifact,
dependency edge or decision authority. The public check uses its bounded
session under the isolated launcher: for each trigger, both the declared
executable consumer and consistency check run before removal, while the graph
is absent, and after restoration. Each route must produce the fixed named
semantic failure on removal and pass on restoration; those bounded behavioral
results are attached to every trigger-specific proof record.
Self-declared replacement reasons, fabricated authorities, stale timestamps,
or non-restoring proofs reject.
Each proof performs its own actual removal and restoration under the same
report budget. One successful cycle is never copied into several trigger
records; skipping a later removal must fail even if an earlier proof passed.
Both routes validate the artifact schema, equality with the already measured
graph, and independent oracle owner pairs using that same complete model.
The consistency route also checks its actual captured tester-case registry.
The allowed identities come only from that validated artifact's two declared
roles. A valid reviewed Make-consumer change retains its consumer role rather
than being mistaken for a tester-case ID; undeclared routes still reject.
Neither route reruns Make or recursively invokes the lifecycle driver.
Allowing removal or rejecting restoration in either route must fail the proof;
the other route's success cannot substitute for it.

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
Make syntax. `graph_report.check` uses one caller-supplied `ProbeBudget`,
capture chain and public `ProbeSession` for all CURRENT targets/states, grouped BASE
queries and lifecycle checks. The caller supplies the budget; no target or
registry helper creates another lifetime. `graph_probe` owns only domain
planning and the reference-position census. Execution, immutable snapshots,
stock runtime inputs, native tools, generated publication and cleanup use the
[shared foundation](ownership-probe-foundation.md).

The native observer records actual target/prerequisite order, recipe text,
target-local variable values/origins/flavors, includes and successful dynamic
provenance. It does not scrape diagnostic output or synthesize a dry-run
Makefile. Ordinary recipes are metadata only; genuine expansion and include
remakes use the declared real command/output adapters. The graph planner
retains actual generated include bytes for its census. It does not create a
second sandbox, interceptor, source loader or process executor.

The census preserves complete recipe text, including quoted shell `#`
characters. Make comments are stripped only from non-recipe statements;
inline recipes are separated without treating semicolons inside Make
references as lexical separators. Native Make still determines the actual
recipe and prerequisites after expansion.
Computed `value`, `origin` or `flavor` selectors in graph expressions or
consumed recipes reject rather than silently omit a possible prerequisite
domain. Literal selectors remain supported. An unconsumed debug recipe does
not become a graph input or force its symbolic values to expand.

New paths brought in by the delivered producer/dependency/adaptive/cleanup
contracts have explicit selectors in the existing host and documentation
rules. The original introduction cohort is unchanged; another new path under
those prefixes, including a new changelog fragment, still requires its own
semantic admission.
The pure `scripts/workflow_pilot/tests/coordinator_support.py` helper is
explicitly classified with the existing host owners. An unclassified adjacent
helper still rejects; neither its directory nor the introduction cohort is
expanded to admit it.

All report observations share the existing deadline and cumulative resource
accounting. BASE uses one public `select_view` block, the same original source
paths and independently captured BASE registry/model. Global graph/target
caches and per-target executors are removed. The already validated model is
reused for the same artifact's nonrecursive lifecycle removal/restoration;
that reuse is not another graph evaluation or a success label.
Within each selected view, the existing Make registry is parsed and validated
once for its authority pass. Its ambient, finite, typed and generated-input
sections reuse that validated data; repeated tool/input references reuse their
captured bytes with explicit cache-byte accounting. The metadata object is
bound to its exact loader and budget, cannot cross CURRENT/BASE views, and is
not a process-global cache or a switch that skips validation.

Schema version 5 seals each external selector as either a finite exact domain,
an exact tracked fallback, or symbolic recipe/environment-only authority.
The parsed model admits 112 prerequisite domains: 111 tracked fallbacks and
the explicit `NODEP` domain (`""`, `"0"`, `"1"`). Acceptance requires every
domain to be observed through real command-line GNU Make, with
environment-sensitive domains also observed through a clean process-environment
origin. A count derived by `load_make_prerequisite_domains` proves the admitted
scope, not live execution; a smaller synthetic fixture is not a substitute.
Full-domain graph/oracle/lifecycle/public-gate adoption of the independent
[issue #206 foundation](https://github.com/laqieer/fireemblem8-expansion/issues/206)
is a separate acceptance requirement, not evidence supplied by the focused
A/V correction. The baseline plus all variant traces are
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
State, context, source, domain, subprocess and byte bounds fail closed across
the entire report. Each target remains a standalone native Make goal.
One-variable domain observations extend their actual parent context when a
branch reveals a new domain or fallback. No combined goal is attributed to a
different target, no per-target deadline is restarted, and no registry entry
is backfilled as an observed domain.

Candidate command regexes are compiled and matched in a fixed isolated Python
operation launched through the existing `ProbeBudget.run` watchdog, never by
the trusted report thread's backtracking engine. Candidate JSON-schema pattern
validation uses the same bounded operation, including recursive schema checks.
The operation retains Python `re` syntax and the original search/fullmatch and
DOTALL semantics. It applies the existing address-space bound before parsing
inputs or compiling patterns and retains the report's original deadline,
launch/input/output budgets and owned cleanup. No engine, dependency, dialect,
service or numeric allowance is added.
The caller first validates the encoded JSON against the existing file and
pending bounds, then gives the worker the actual wire length as its input
ceiling. A cumulative pending allowance is not an allocation request: small
messages must not reserve the report-wide traffic budget before parsing.
Repeated pattern batches use lossless zlib transport only when it is smaller
than the identity representation. The original decoded JSON still satisfies
the same per-request bounds, the actual wire length bounds the stdin read,
and decoding is limited to the declared original length plus one byte before
requiring exact length, complete stream and no trailing data. Regex/schema
execution sees every original byte; the existing pending ledger charges the
bytes actually transmitted, with no refunds or omitted observations.
The worker executes from this same verified module file rather than repeating
its program text in every argv; isolated startup and the pre-parse memory
limit remain in force.

All command patterns are evaluated as one batch for a concrete command; exact
completed match results are reused only within that matcher/report lifetime.
Repeating a cached match after deadline/failure/closure still fails. Metadata
sections keep the existing selected-view reuse, with compilation validation
performed once for that metadata object. Invalid patterns, excessive input,
resource exhaustion and interrupted work fail closed. The tester case observes
matching start for its benign catastrophic-backtracking negative rather than
accepting a missing fixture or pre-match error as timeout evidence.

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

GNU Make can execute parse-time `$(shell)`, `!=` and makefile-remake recipes.
The foundation's native dispatch intercepts those operations without replacing
Make-visible `SHELL`/argv semantics. A graph command must match exactly one
sealed domain and execute through its public `Command`/native/dependency action.
Only actual returned bytes and declared outputs can reach replay. Unknown
commands, source/consumption mismatches, nonconvergence or unavailable
confinement reject. The graph has no control-channel implementation of its own.

Generated-data path classification likewise does not import the base
`scripts.generated_data.registry` into the trusted reporter. A small trusted
probe executes the exact candidate registry in a separate credential-free,
networkless, read-only-tree process with only isolated command scratch. The
reporter accepts only bounded UTF-8 JSON with the closed typed record schema,
sorted unique names/dependencies, valid versions, and confined tracked paths.
It binds the candidate registry AST/blob set, exact typed output, launcher, and
Python identity. Newly declared candidate generated paths therefore classify
from candidate authority, while candidate code cannot mutate or enter the
trusted gate process.

Every Make include observed by GNU Make must be the primary `Makefile`, a
tracked regular `.mk`, an exact gitlink-contained file, the trusted probe
control file, or a canonical regular descendant of the generated `build/`
overlay. Include spelling is normalized against conceptual `/repo` before
classification; dot/dot-dot, repeated or encoded separators, absolute/dynamic
aliases, missing paths, and any symlink component reject. Generated asset
discovery and manifest includes begin from an empty overlay and are rebuilt
only through registered confined commands; no live source file is written.

The public boundary is the existing standalone Python entry, started with
`-I -S -B` before any Make process. It strips `MAKEFILES`, `MAKEFLAGS`,
`GNUMAKEFLAGS`, `MAKEOVERRIDES`, `MFLAGS`, `BASH_ENV`, `ENV` and `GIT_*`;
GNU Make options such as `--eval` are not reporter arguments. The root
Makefile retains only a convenience path for a trusted invocation. Its sole-goal,
override and dry-run checks operate after Make startup and bypass normal build
includes; they cannot undo a preload or an evaluation option already executed
by GNU Make. The tests demonstrate that difference with real file/preload and
dry-run effects, not a source-spelling assertion.

Candidate CI does not use these candidate-authored modules as its own trust
root. On pull requests, `host-tests` first checks the exact GitHub PR-base
commit for only the stable bootstrap sentinels needed to distinguish
no-authority, foundation-only, and verifier-owned BASE states. When the BASE
already carries `scripts/validation_ownership/ci_verifier.py`, the hosted step
archives that exact BASE and lets its own verifier package validate the
complete authority set. Newer candidate-only runtime helpers therefore do not
become preflight requirements for older exact bases. When the verifier is present, it creates an
unpredictable mode-`0700` directory under the lstat-checked GitHub runner
temporary root, records its device/inode identity, and archives the complete
clean base tree there. It never removes or creates a verifier staging path
through the candidate checkout; base and candidate Make/registry probes use a
mode-`0700` runtime child under the same external trusted root, and cleanup
removes only that unchanged external identity. CI starts the extracted
`ci_verifier.py` directly with `-I -S -B`, before any Make invocation.
The root Make target remains a trusted-invocation convenience only.
The standalone base verifier verifies every
staged verifier package file and every loaded transitive `scripts.*` module
against independently selected immutable source, excludes the candidate
checkout from `sys.path`, and reads CURRENT and BASE through separate real
public views. There is no hybrid loader overwriting candidate entry identities
with BASE bytes. Every managed mode compares the union of candidate and
selected-source verifier namespaces, including new files, types and modes.
Actual transitive modules are bound before and after execution. Candidate
modifications to `reporter.py`, `make_probe.py`, the interceptor, or their tests
must match the independently selected source rather than merely being reported.
After trusted validation of both exact-base and
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
### Coordinator-owned review and capture

Candidate YAML is redundant drift protection, not the authority to decide
whether verification ran. `coordinator_capture.validate_handoff` requires the
existing #178 `coordinator-check` assignment and always calls the real
standalone verifier through `capture_check`/`trusted_executor` before managed
admission. The coordinator owns the trusted source/root, actual PR BASE,
candidate/worktree and expected mode outside candidate control. For a reviewed
graph evolution, `qualify_reviewed_evolution` first consumes the coordinator's
actual `ReviewSession` task result and immutable `ReviewTools` tester-case
binding. The completed read-only reviewer must be independent of the
coordinator/implementer, and its exact scope binds repository/PR/BASE/head,
worktree identity, reviewed checker revision and required paths, changed tracked
paths, invalidated relationships, and every affected consumer. The review
session uses exactly four subjects under the unchanged bound: the exact
checker revision plus domain-separated SHA-256 identities of the canonical
complete path, edge-ID, and affected-consumer sets. The full sorted arrays
remain explicit in the qualification and verifier selection; the digests only
give the bounded review scope an identity, not source-content ledgers,
truncation, sampling, or independent authority. Paths are capped at the single
review's200-file capacity before launch; edge/consumer arrays remain256 and
the subject cap remains40. Every path needs actual read coverage from the
same immutable root/BASE/head pair through the same `ReviewTools.model`
that created the session. Qualification invokes the shared
`require_candidate_path_coverage` over `candidate_changes`: added/modified
paths need head reads, deleted paths need base blobs, and mode-only changes
need both sides. Empty or unrelated read sets, wrong roots and runtime file
counts cannot substitute. The same builder validates the
live session/report/lease/ownership scope and the persisted record. That
scope is delivered before review, not reconstructed from hashes afterward:
`reviewed_evolution_context` supplies the complete explicit selection and
repository/PR/BASE/head/worktree/checker identity to `ReviewSession.begin`.
The existing request-byte bound applies, and the native task observation must
carry the same dispatched context before it can become the immutable lease/
report binding. A missing or different context cannot qualify any scope. That
qualification is stored with `candidate.local_validation`, joined back to the
actual candidate record and capture assignment, and included in the generated
`ownership-reviewed-*` evidence ID. A candidate field, copied result, mode
claim, matching SHA, or freshly archived candidate checker cannot construct
it. Changing the candidate, checker, scope, task observation, or worktree
invalidates the old local capture. The actual
bounded process result, PID, exit and RSS are retained by the existing handoff
contract. Missing, stale, failed or mismatched captures and candidate pass
labels cannot grant acceptance.

A base with no authority remains `bootstrap-not-authoritative`/`authority:none`;
exit0 is not managed exact-base acceptance. A complete foundation-only BASE is
the explicit `foundation-introduction` transition and needs independently
selected immutable graph-verifier source. It reports `explicit-introduction`,
not a fabricated BASE verifier or exact-base proof. Partial graph authority
fails instead of downgrading. A complete graph BASE uses `exact-base-pinned`
and exact BASE verifier source. A coordinator-qualified graph update may use
`reviewed-evolution`: the verifier comes from the independently reviewed
revision selected by the qualification, not from candidate/revision equality.
Code-only verifier evolution is also explicit: `trusted_source_changes`
records the actual selected-source/BASE difference. Exact changed-path reads
remain mandatory; edge and consumer scopes are both empty only when graph
authority is unchanged. An evolution with neither a graph-authority change nor
a trusted-source change rejects. No dummy graph edit or unexecuted future
verifier can manufacture acceptance.
BASE and candidate each validate against their own immutable schema, oracle,
graph and model. The shared document-aware authority comparison supplies
schema/oracle/authority invalidation; dependency edges are covered only when
both endpoint surfaces have complete oracle probes. The independently
qualified path, edge and affected-consumer scopes must match the actual Git
diff and invalidation exactly. Unreviewed exact-base retargets still fail, and
the PR-only hosted invocation remains strict: it accepts no candidate-supplied
review mode or scope, and its failed exact-base evolution observation is not
rewritten. The managed input-free workflow dispatch keeps that PR-only step
not-applicable; neither the dispatch event nor N/A status is authority. Final
review-first assessment, reservation, dispatch and full-run admission require
the same live qualification that produced the local capture. Production
refresh passes that object through `assess_observed(..., local_qualification=...)`;
the persisted record is only the bound comparison target and cannot recreate qualification.
If a live qualification is supplied but the candidate has no coordinator-owned
`local_validation`, delegated handoff readiness cannot substitute for the
qualified ownership capture. Ordinary delegated candidates with no reviewed
qualification retain their existing readiness path.
Reviewed capture assignments must contain the exact qualification record;
the ordinary delegation schema cannot strip it into unqualified acceptance.
Legacy delegated ownership checks bearing a reviewed evidence identity are
held from delegated-only readiness even if the live argument is omitted.
That identity can block a stale record, never grant review authority.

The exact-base verifier owns one private `.validation-ownership-runtime`
workspace inside its immutable trusted source root. Its captured parent,
device, inode, owner and mode identity is removed only after the shared budget,
owned processes and `ProbeSession` have terminated. Successful and failed
captures therefore leave the trusted tree reusable. A pre-existing workspace,
swapped directory or symlink, changed parent identity, or nonempty residual
workspace rejects without deleting the unknown path or trusted source tree.
Cleanup failure rejects success; when validation already failed, the original
failure remains primary and the cleanup failure is reported alongside it.

Git remains the identity authority; no source ledger, new service, signer,
privileged PR event or human approval is introduced.

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
