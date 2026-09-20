# Issue 180 — local, nondelivery null bootstrap preparation

**UNQUALIFIED AND NOT LAUNCH-READY. Never merge or push this branch as a
delivery change. No namespace, privileged process, helper, or CI job was
executed to prepare it.**

Scope:
<https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5736286567>.
The mounted-root amendment is
<https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5737056729>;
the recovery-continuation lineage is frozen in
<https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5748359622>.
The branch has exactly three normal commits above
`ec1dc8553419c8833a687fd8d4a6521a4e29ff7a`: held preparation
`20478394860b673b98fb32a4dd292fa0fc02a5d4`, interrupted recovery
`3302f790e944e81be4ea0777682f5282e560fd9c`, then this correction.
Each must be the sole direct parent of the next, and the correction must
modify only the same five additive regular files. Its separately selected source is
`c8b365da1be29bc58352cf1edb8b836a2cf18321`. The original ordinary startup census
is closed, not repeated. This preparation does not adopt a FoundationTests
backend, qualify seven modes, fix current CI, or allocate ROOT21, census2,
H1, a compiler/Make/SDK workload, or a native supervisor.

## Historical interface hold and mounted-root correction

The first preparation correctly refused
`original-private-child-identity-unavailable`: R created a host backing child
whose original identity could not reach C through the fixed custody frames.
Namespace destruction releases mounts but does not unlink that host child.
A late lookup cannot distinguish it from a replacement. The historical
refusal is not an installed-kernel failure and is not reclassified as success.

The amended placement avoids that extra host object without expanding c8:

1. C exclusively creates its run container and empty `fixture` child and
   retains their original nofollow identities and CLOEXEC pins. The unchanged
   `_FixtureBinding` names that original host fixture, never a mounted inode.
2. R verifies the bound empty fixture, overmounts it only inside private M0
   with the single 1 MiB tmpfs, and **closes its pre-mount host pin**. A new
   mounted-root pin must prove a distinct tmpfs identity, exact size and
   restrictions before any creation or metadata change.
3. R creates `volume` and all fixture children only inside that filesystem.
   Only fresh tmpfs objects are chowned. Host backing, mounted root and volume
   are distinct identities, even though path names can overlap.
4. After truthful lifecycle/namespace custody closes, C verifies its original
   unchanged host fixture is empty and removes only that object and its own
   empty container. Replacement, retained namespace/child, unknown FD,
   uncertain close/removal or incomplete custody fails and retains unproved
   objects. No recursive deletion, late identity adoption or privileged
   cleanup is provided.

The connected C path now performs original acquisition, the existing-budget
`run_capture`, source rechecks, custody retention and checked cleanup. Its
tests are fully inert, not kernel or restricted-host evidence. Independent
exact-code review and a separate launch freeze are still required.
The original c8 serializer controls remain: extra child fields and a changed
binding inode reject. No frame, callback, channel or production API changes.

## Prepared fixed role code

There is no generic command dispatcher. The only external roles are `plan`,
`run`, and a closed `--reaper readonly U G DEADLINE PARENT DEV INODE OWNER`
argument shape. No PID, FD, mapping range, capability request, source path,
mount source, program fragment, retry count or timeout override is accepted.
The selected source and harness paths are fixed checkout siblings.

| Role | Fixed transition / authority |
| --- | --- |
| C | Actual nonzero independent U/G and exact Git source/harness guards; original host acquisition, concrete c8 outcome binding, exact `NAMESPACE_LAUNCHER` adapter, source rechecks and identity-bound cleanup. |
| L | Unchanged c8 lifecycle code. Its real API is tested with inert process/syscall boundaries, including L7 → cleanup fault → actual main125 → C cleanup fault. |
| R setup | Verify private PID1/net/M0 with unchanged full-map U0 using the fixed launcher ancestry; replace proc with a P0-aligned view. Mount tmpfs on the originally bound fixture, withdraw the old pin and verify the mounted root before creating volume/canaries; bind existing devices without mknod or device metadata changes. |
| N | Empty groups, effective U/G but temporary real/saved root, only initial CAP_SYS_ADMIN E/P, I/A0, dumpable0 and NNP1. One NEWUSER\|NEWNS creation after READY/CREATE. |
| R maps | Owned waitable child, pidfd and proc/start identity; typed namespace FDs, direct parent U0, owner U, and mount owner U1. Empty maps, `deny`, exact `0 U 1` / `0 G 1`, complete writes and readback. |
| N exit | Verify maps, normalize all saved IDs to namespace0, clear caps, exit. R observes status before reaping and closing all N/proc/map handles. N0 is not a mode result. |
| W0 | Rebind FD1 to the private result pipe and discard inherited setup aliases, then send fixed `W0_READY` on that same pipe and block for GO. R checks the live W0 FD inventory itself. |
| R reaper | Close UF/MF and the W proc handle, verify CAP_KILL-only E/P/bounding with I/A0/NNP1 and PID1 parent-death protection, then send GO. Only owned waitable/pidfd children can be signaled. |
| W entry | All real/effective/saved/fs IDs U/G, no groups, five caps0 and NNP1 in U0. USER setns **before** MOUNT setns; verify target maps/identities, fresh root/cwd, and unchanged label. Close all target/setup handles before subject import. |
| W mechanism | In U1/M1 only: real old0x102a must fail EPERM with full identity/flags unchanged; only then the unchanged selected helper clears local NODEV. Final five caps0/NNP1 precede null EOF/tiny write and readonly/other-device denials. |
| R outcome | Parse one bounded private worker record, construct R's own envelope from its owned wait/stage, publish before cleanup, attempt every remaining owned cleanup, and publish bounded after data without hiding failure. |

The additional `W0_READY` is a fixed token on the already required W→R pipe,
not another channel or top-level outcome frame. Its bytes count within the
existing F-byte private-result subdivision. No arbitrary request is accepted
on any pipe.

### FD ownership

* C retains its own original workspace/container/fixture CLOEXEC pins in
  addition to the existing C/L budget/lifecycle capture and lifetime owners.
  No setup FD crosses sudo; C withdraws all pins during checked cleanup.
* N inherits only fixed control endpoints and trusted stdio; it imports no
  subject. It is reaped before worker handoff.
* R holds N pidfd/proc/maps and temporary typed namespace relation handles
  only while setting up. After N cleanup, only UF/MF survive.
* W0 receives UF/MF and the GO reader by fork. Its FD1 is a distinct private
  FIFO writer; the enclosing capture writer and every duplicate alias are
  excluded. R observes this through the owned live W0 proc binding.
* After retirement R retains only the W pidfd, result reader and briefly the
  GO writer, plus its stdio pipes.
* W before entry has UF/MF and pipe stdio. Before helper import it has only
  EOF-reader FD0, private-result writer FD1 and distinct diagnostic writer
  FD2. No device, ancestor, setup or enclosing-capture handle survives.

Cleanup withdraws FD authority before a potentially ambiguous close. An
uncertain close fails, is not retried by recycled integer, and cannot skip
other owned closes. First ordinary observations are distinct from cleanup
waits. Existing c8 `finish_cleanup` and cumulative concrete reports preserve
precedence rather than using a return-losing `finally`. Signal boundaries in
all preparation tests, including separately loaded c8 modules, are mocked.

## Assertions versus exported observations

All facts below are **future code conditions tested only with mocks here**,
not runtime evidence:

* R/N/W credentials, caps, maps, PID/proc alignment, namespace type/parent/
  owner, root/cwd, mount restrictions, live-child identity, labels, and FD
  barriers are actual check sites in the prepared role code.
* N checks dumpable0 using its own PR_GET_DUMPABLE before its fixed READY.
  R separately observes N's proc credentials/caps/namespace/label. The
  current proc status format does not expose dumpability, so R does **not**
  pretend to have independently read or serialized that integer.
* Old0x102a's actual errno and unchanged-state comparison are an in-worker
  prerequisite. They are **not separately exported**: c8 allows only one
  operation slot, which contains the selective helper's observed call.
* Namespace inode relations and the intermediate credential snapshots are
  not present in c8's frames. Passing fixed code gates must not be described
  as a separately serialized intermediate record.
* The six-element before/after tuples retain device, inode, mode, rdev, mount
  ID and full flags. They are not repurposed for namespace or backing-child
  identities. The binding retains actual U/G/deadline/parent identity.
* R carries its own normal N/W wait status or earlier finite error, setup
  status, validated mode data, and bounded cleanup. L carries its actual
  WNOWAIT observation and cleanup. C retains actual outer return/error and
  capture/EOF/reap/API-return availability.
* `snapshot.qualified` remains false. `capture_complete` is only a necessary
  set of semantic capture checks, **not fixture acceptance**. Even four valid
  records with zero statuses cannot overcome an outer125, missing EOF,
  failed cleanup, changed backing identity, retained namespace or ownership.

No snapshot, mode tuple, magic prefix or token is authentication by itself.
The intended trust boundary is the fixed program plus actual exclusive
writer/namespace/FD checks. W bytes are never forwarded as R/L envelopes.
The selected helper source is unchanged; its one syscall is observed through
the same kind of fixed forwarding adapter used by the existing fixture, not
replaced with a new implementation. The preparation tests use a stub rather
than importing or executing that actual helper.

## Resource and artifact boundaries

The prepared adapter uses the existing one bare ProbeBudget/outcome:
30-second original deadline, no fresh budget/reset/refund, at most 35-second
coordinator-wait ceiling and 45-second outer timeout. The fixture is 1 MiB.
Combined capture is at most 256 KiB; four c8 frames are at most 4096 bytes
each. The existing 52-entry / 552960-byte cache reservation and F/E
subdivisions are unchanged. Capture counts actual admitted bytes.

The proposed workflow is public/owner/first-created-push/run1/attempt1 only,
`contents: read`, pinned checkout/artifact actions, and no persisted
credentials. It declares the same `ubuntu-latest` and relevant native-CI
packages as the consumed census; that is **not a claim about an observed
future image, kernel or LSM**. First job creation would consume the one-shot
allocation even if setup fails. There is no dispatch trigger.

Only `scope.json`, `launch.json`, `custody.json`, `mode.json`, `cleanup.json`
are uploadable, each at most 16 KiB and together at most 64 KiB. No raw
stdout/stderr/frame/environment/source/ROM/SDK/report artifact is allowed.
Files live at a fixed workspace-relative `-records` directory, separate from
C's removable run container. Preflight failures explicitly report no launch;
later records distinguish adapter invocation, actual budget admissions,
original identities, available observations, source checks and cleanup.
`qualified` stays false. Cleanup records describe completed checks before
artifact publication, not a prediction that later writes/closes will succeed;
any artifact failure forces exit125.

Readonly binds of the fixed selected/harness views and fresh source/runtime
canaries are prepared inside the volume. The original checkouts are not
edited. This is a fixed-function diagnostic, not a general hostile-code
filesystem sandbox. C records each before/after source check only if performed;
unavailable checks are not reported as true. The connected source/fixture
integration is exercised with mocks only; no after-workload source-preservation
claim is made from that model.

## Benign local controls

Existing case context:
`TC-WORKFLOW-OWNERSHIP-MODERN-TOOLCHAIN-001`, issue #180. No production
selector, registry or test is changed.

Prerequisites: Python with the already present PyYAML, actionlint 1.7.7, and
a clean separate checkout of exact c8. Do **not** run the program's roles,
the workflow, original Foundation kernel fixtures, or any namespace helper.

```sh
# Read-only source identity check, then the wholly inert new tests only:
test "$(git -C "$C8_CHECKOUT" rev-parse HEAD)" = c8b365da1be29bc58352cf1edb8b836a2cf18321
git -C "$C8_CHECKOUT" diff --quiet c8b365da1be29bc58352cf1edb8b836a2cf18321 -- scripts/validation_ownership
ISSUE180_SELECTED_SOURCE="$C8_CHECKOUT" \
  python3 -B -m unittest scripts.ci_null_bootstrap.test_bootstrap -v
actionlint .github/workflows/issue180-null-bootstrap-1.yml
```

Expected results: mocked ordinary-owner path reaches the fixed helper boundary;
foreign/root/cap/NNP/map/namespace/root-cwd/FD states fail before it. Mocked
old success, altered flags and missing/foreign custody fail. Every remaining
owned close is attempted on errors, first cause survives, and recycled
identity is not authority. The real c8 serializer rejects extra child custody.
The connected coordinator model uses actual c8 admission/capture/retirement
APIs and a two-view directory model: pre-mount FDs continue to name host
objects while newly opened mounted-root FDs name the tmpfs. Fault injection
covers acquisition, mount, admission, capture, source checks, each close and
removal, incomplete custody, deadline and artifact publication.

In-memory restorations remove the pre-entry and old-operation checks or
create `volume` through the old host pin. Each must break its behavioral
oracle, with the corrected code passing again; the host-pin restoration also
proves the unowned host child is retained rather than recursively deleted.
A neutral local-variable rename and JSON-key/global-frame interleaving retain
behavior. No source-text phrase is used as evidence of namespace correctness.
Only the workflow's public identities/permissions/action/allowlist syntax is
checked as a parsed externally consumed security contract.

No test forks, launches a Popen payload/watchdog, invokes prctl/capset/setuid,
signals a real process, mounts, enters/creates namespaces, imports the actual
helper, or runs sudo. Positive pipe controls use only owned ordinary pipes.
Tests install explicit traps/mock boundaries in both this program and the
separately loaded c8 modules; mocks are not kernel, LSM, custody-authentication,
RSS or runtime-cleanup qualification.

## Remaining authority and unsupported claims

Main owns the exact independent review and any separate
one-shot launch freeze. No launch is authorized by this commit or these
tests. R/N/W are prepared code, not an accepted installed-platform mechanism.
Creation and entry can each be denied by the unchanged LSM, with no fallback,
profile borrowing, sysctl change, root-worker substitution or retry.

Even a later representative readonly mechanism would not qualify the four
Foundation methods/seven modes or repair CI; that requires its own coherent
integration and real restricted-host acceptance. ROM/RAM/save/localization/
generated-data/build-profile contracts are unaffected. The only dependency
is immutable c8's existing custody/helper interface; no interface expansion
or conflicting production owner is introduced. Discarding this never-merged
branch is the rollback; no production feature flag or migration is added.
