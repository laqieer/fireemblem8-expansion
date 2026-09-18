# Issue 180 — local, nondelivery null bootstrap preparation

**UNQUALIFIED AND NOT LAUNCH-READY. Never merge or push this branch as a
delivery change. No namespace, privileged process, helper, or CI job was
executed to prepare it.**

Scope:
<https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5736286567>.
The branch is a normal additive child of
`ec1dc8553419c8833a687fd8d4a6521a4e29ff7a`; its separately selected source is
`c8b365da1be29bc58352cf1edb8b836a2cf18321`. The original ordinary startup census
is closed, not repeated. This preparation does not adopt a FoundationTests
backend, qualify seven modes, fix current CI, or allocate ROOT21, census2,
H1, a compiler/Make/SDK workload, or a native supervisor.

## Exact frozen-interface hold

`execution_contract()` unconditionally refuses
`original-private-child-identity-unavailable`. Neither environment variables
nor a valid-looking result can enable execution. The prospective coordinator
records unavailable custody and exits **125 without reserving an outcome,
creating a fixture, launching sudo, or executing R**. The reaper command-line
entry independently has the same refusal.

This is a concrete ownership-custody contradiction, not an installed-kernel
failure or another abstract bootstrap proposal:

1. The frozen sequence gives C an originally pinned **parent**, then requires
   R to exclusively create a fresh private child and mount its 1 MiB tmpfs
   there. The child backing directory is a distinct host-filesystem object.
2. R must close every parent/setup handle and retire before GO. Namespace
   teardown, not a newly added privileged unmount/cleanup operation, releases
   the mounts. Namespace destruction does not unlink that backing directory.
3. C must delete only originally owned objects and retain replaced/foreign
   objects. Looking up `volume` for the first time after R exits does not
   identify the original child: a substituted empty directory can have the
   same owner, mode and name. The original parent identity alone does not
   distinguish these cases.
4. c8's concrete `_FixtureBinding` carries the already-bound parent, not a
   second object. Its R frames reject an extra `fixture_child` or
   `backing_identity`; replacing the parent inode is a binding mismatch.
   The fixed worker mount-state and operation fields describe the null
   mechanism, not this separate backing object.
5. C's opted-in run admits neither arbitrary callbacks nor a producer
   channel; this preparation adds no receipt file, channel, broker, frame
   field or production API.

The benign controls execute c8's real encoder/decoder to demonstrate (4), and
exercise `remove_empty_fixture` against a retained child and substituted
parent. That cleanup function will remove a genuinely empty pinned parent,
but **will not recursively delete an unretained R-created child**. No child
inode is fabricated from a late `stat`, and no unrelated field is overloaded.

Consequently, the final C fixture-creation/launch/cleanup orchestration is
intentionally **not connected**. The existing-budget `run_capture` adapter
and the fixed role code are implemented and exercised with inert boundaries;
they are not advertised as a complete runnable witness. Main must resolve
the precise frozen ownership contract before a separately reviewed launch
freeze. This branch changes no shipping API to resolve it.

## Prepared fixed role code

There is no generic command dispatcher. The only external roles are `plan`,
`run`, and a closed `--reaper readonly U G DEADLINE PARENT DEV INODE OWNER`
argument shape. No PID, FD, mapping range, capability request, source path,
mount source, program fragment, retry count or timeout override is accepted.
The selected source and harness paths are fixed checkout siblings.

| Role | Fixed transition / authority |
| --- | --- |
| C | Actual nonzero independent U/G and exact Git source/harness guards; concrete c8 outcome binding and exact `NAMESPACE_LAUNCHER` adapter. The interface hold currently prevents live acquisition. |
| L | Unchanged c8 lifecycle code. Its real API is tested with inert process/syscall boundaries, including L7 → cleanup fault → actual main125 → C cleanup fault. |
| R setup | Verify private PID1/net/M0 with unchanged full-map U0 using the fixed launcher ancestry; replace proc with a P0-aligned view. Create only the fixed fresh volume and canaries; bind existing devices without mknod or device metadata changes. |
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

* C/L retain only their existing budget/lifecycle capture and lifetime owners.
  No setup FD crosses sudo.
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
  records with zero statuses cannot overcome the explicit interface hold,
  an outer125, missing EOF, failed cleanup or retained ownership.

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
Files live at a fixed workspace-relative output directory, not a temporary
directory. Under the hold the four post-scope records explicitly report
**no launch**, unavailable custody/mode and no acquired launch resources.

Readonly binds of the fixed selected/harness views and fresh source/runtime
canaries are prepared inside the volume. The original checkouts are not
edited. This is a fixed-function diagnostic, not a general hostile-code
filesystem sandbox. The live C before/after source/fixture integration remains
unconnected under the hold; no source-preservation-after-workload claim is
made from a mock.

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
identity is not authority. The real c8 serializer rejects extra child custody;
the actual coordinator's hold produces no attempted launch.

Two in-memory restorations remove the pre-entry and old-operation checks:
each must break its behavioral oracle, with the corrected code passing again.
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

Main owns the exact independent review and any separate revised interface/
one-shot launch freeze. No launch is authorized by this commit or these
tests. R/N/W are prepared code, not an accepted installed-platform mechanism.
Creation and entry can each be denied by the unchanged LSM, with no fallback,
profile borrowing, sysctl change, root-worker substitution or retry.

Even a later representative readonly mechanism would not qualify the four
Foundation methods/seven modes or repair CI; that requires its own coherent
integration and real restricted-host acceptance. ROM/RAM/save/localization/
generated-data/build-profile contracts are unaffected. The only dependency
is immutable c8's existing custody/helper interface; the explicit backing
object contradiction is the current conflict. Discarding this never-merged
branch is the rollback; no production feature flag or migration is added.
