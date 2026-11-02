# Restoration of Grado native-state reference

This is the unfinished optional reference for
[#315](https://github.com/laqieer/fireemblem8-expansion/issues/315).
It is not a selectable game project or a qualified Grado build yet. Nothing
here is linked by an unselected default framework build.

The allocation tracer implements `GradoState_ValidateAllocation`: a read-only C
check over typed Boolean, one-hot and bounded-counter descriptors. It rejects
unsupported domains, native capacity excess, engine/debug/sort reservations
and overlapping lifetimes. Different chapters may reuse temporary bits.
Permanent guide flags remain owned by the progressive guide unless the caller
supplies the static-guide policy. Supplying that policy is **not** evidence
that the native guide consumers have been redirected.

The unselected `src/values.c` codec reads and writes these descriptors through
the original native flag backend. Use only fields from a complete allocation
already validated as a whole; each access additionally checks its own layout,
guide policy, value domain and explicit current project-chapter ordinal.
It distinguishes unset one-hot fields and corrupt stored values without
changing output or backing on refusal. Writes are explicit requests, not
automatic load recovery. Generic Boolean writes do not implement retirement
monotonicity. The explicit permanent-only `GRADO_STATE_MONOTONIC` kind refuses
clearing a set fact and allows idempotent writes; full retirement producers,
load/recovery and selected-profile lifecycles remain separate requirements.

`GradoState_ReadValueFrom` accepts a readonly, sized view of the existing
native chapter/permanent buffers. It uses the same descriptor validation and
decoder as the live reader and the original packed-bit reader; it neither
loads temporary bytes into live game flags nor writes its input. Only the
requested scope needs a full native buffer, so a normal-save permanent view
does not need chapter bytes. Null/short input, wrong chapter, reserved/owned
fields, unset and corrupt values retain their explicit results and output
guards. The view is an ephemeral pair of pointers/sizes, not a save record.
An owning loader must establish compatible envelope/epoch/checksum and native
block validity before constructing it. Field decoding alone never authorizes
loading, migration, recovery or a resource operation.

An unselected static-guide adapter is also available for the existing
`src/bmguide.c` code slot: `src/static_guide.c` includes that native
implementation unchanged and routes only its flag operations through the
shared ownership policy in `src/state.c`. It does not duplicate guide
content, scripts, window, navigation or sort code. Every display/read query
uses the one truth flag assigned by the campaign definition; uninitialized
truth keeps the guide locked, regardless of unrelated reclaimed bits.
`src/native_flags.c` supplies the existing `src/eventinfo.c` replacement
slot, includes the original implementation and initializes truth after its
real permanent-flag reset. New-game callers keep that same native reset
interface. The header derives the internal native ID from the same
allocation definition, not a second numeric owner.

An eventual project must select both replacements and include `src/state.c`;
neither replacement is activated by this reference. A permanent reset clears
the original permanent buffer then sets only truth, leaving chapter bits
unchanged. This is new-game/reset behavior, not a load-recovery operation.

The candidate campaign allocation now names all 227 FLAG and 16 STATE entries
in `include/grado_campaign_state.def`. That single C definition owns explicit
native slots, scope, complete value domains, aliases and derived counts.
`src/campaign_state.c` validates its complete physical allocation through the
existing validator and exposes typed symbol reads/writes through the original
flag backend. Logical enum ordinals are build-local C selectors, not serialized
identities; changing definition order does not change the explicit native slots.
The physical map is not yet a qualified, released save ABI.

Choice backing flags are read-only views of their semantic one-hot state.
The final ending is a read-only direction projection. CH04 and CH05 each use
four separate monotonic facts; their counts are read-only cardinalities, not
mutable totals. `RecordRescue` records an explicit fact, not evidence that an
evacuation/category producer's conditions were satisfied. The eventual native
event adapter must establish those conditions and a coherent checkpoint.
Count declarations select only their typed collection; they have no unused
field target that could contradict the reader. Ending declarations instead
name the field actually read by that projection.
An unset semantic one-hot reports `UNSET`; its Boolean backing views are false.
Corrupt one-hot storage refuses both views rather than exposing partial truth.
Call `GradoState_ValidateCampaign` before using this immutable allocation.

Retained narrative/epilogue outcomes, absence-at-event markers, omen continuity
and choice backing use permanent storage even when the old register called
their immediate use local. True local flags reuse chapter bits with explicit
project ordinals: CH00..CH05 are 0..5, CH05X is 6, CH06..CH18 are 7..19,
CH18X is 20 and FINAL is 21. Local access does not reset storage or implement
new-chapter/Suspend behavior. The allocation validator itself initializes
neither guide truth nor transition backing. The optional native reset supplies
guide initialization; the transition producer and selected save/profile
qualification remain open.

There is still no activated state/guide profile, epoch change, load hook,
recovery or playable content here. Selected guide/save integration,
Grado schema 1/epoch 256, strict compatible normal/Suspend/legacy loading,
producer-sensitive lifecycles and target-ROM/RAM proof remain required.
Framework epoch 2 and its existing envelope are untouched. Do not select or
save this unfinished allocation as a supported game.

## TC-GRADO-ALLOCATION-001

### Complete campaign bindings

This subcase of `TEST-STATE-TOKEN-AUDIT` uses the same Linux compilers and
owned temporary memory as the native cases below. It requires no ROM, real
save, credentials or manual judgment.

1. Run
   `python3 -B -m unittest scripts.modernize.tests.test_grado_state_native -v`.
   The campaign probe links the real allocation, codec and native flag
   implementation, executes them, then compiles the campaign source for
   ARM7TDMI/Thumb AAPCS. The previous absent campaign interface failed host
   compilation; that structural negative does not claim target-ROM evidence.
2. The complete physical allocation must pass the existing reservation,
   capacity, scope and overlap validator. An independent C consumer names
   every required public symbol from the owning 16-STATE/227-FLAG register;
   it is not generated from the definition being tested. Missing names fail
   compilation, extras/duplicates reject, and every required symbol resolves
   from its cleared starting state. Exercise
   a semantic choice and both true/false backing views, all four-direction
   ending values, permanent wyvern and guard fields, the bounded CH13
   local count and a monotonic retirement fact.
3. Execute every subset of each four-member rescue collection in a permuted
   input order, recording each present fact twice. Each exact identity must
   remain queryable, cardinalities must be 0..4, and the two collections must
   remain independent. Completed-zero validity is a value-codec result here;
   producer-sensitive missing-state and checkpoint proof remain separate.
   Record the retained political, letter, persuasion, recruitment and
   epilogue facts in their real producer chapters, call the original native
   chapter reset, then read them from their declared later chapters and FINAL.
   Their values must survive; a new chapter does not erase narrative history.
4. Read-only alias/count/ending writes, out-of-range values, wrong local
   chapter, retirement clearing and invalid symbols/facts must refuse
   without changing either complete native backing array. Invalid reads
   preserve the output guard. Keep the unselected default as the negative
   profile: this example still supplies no default build source or hook.

The same native corpus exercises all14 public one-hot fields and all8 stored
counter bindings using independent source-domain bounds, not descriptor
metadata. Every legal value round-trips; zero is `UNSET` for one-hot and
valid for counters. All31 choice/direction backing views agree across their
full domains, and all4 retirement facts refuse clearing. An upper-bound
write preserves both complete native arrays.

The owning source inventory is the accepted state register in
[Design main](https://github.com/laqieer/Restoration_of_Grado-Design/blob/578bcc136581c52c537f0cacb0591cb840626e89/docs/06_technical/EVENT_FLAGS_AND_STATE_FLOW.md).
Name coverage is structural inventory evidence, not state/save/runtime proof.
The C definition is the implementation's sole allocation authority, not a
second mutable storage ledger. Original engine/debug/sort reservations remain.
The named static contract is the **public Grado logical C-symbol inventory**:
an otherwise unused deleted interface cannot be exercised without an independent
C consumer retaining its name. Compiler-checked public identifiers are therefore
necessary here; raw tracked-text matches, Git content or private implementation
spelling are not behavioral evidence. Reordering definition rows preserves the
named consumer and explicit native positions.

### Generic descriptor controls

This allocation subcase contributes to `TEST-STATE-TOKEN-AUDIT`; it does not
qualify the complete state, guide or save protocols.

Prerequisites: a clean source checkout on Linux, Python 3, host `cc` and the
supported `arm-none-eabi-gcc` toolchain from the normal quickstart. No ROM,
emulator, SRAM file, alternate profile or manual judgment is needed.

1. Run
   `python3 -B -m unittest scripts.modernize.tests.test_grado_state_native -v`.
   The test compiles and executes the real example C, then compiles the same
   source for ARM7TDMI/Thumb with the default AAPCS ABI. Compiler and process
   diagnostics must be empty of failures; the complete module must pass.
2. The executed native vectors accept nonoverlapping descriptors and temporary
   bit reuse across different chapters. Same-chapter overlap, incompatible
   one-hot/count domains, unknown scopes, boundary overflow, chapter flags
   1 through 6, debug flag 39 and permanent engine/sort reservations reject.
   This includes inherited postgame flags 0x71 through 0x77, ending flag
   0x7D, boss-BGM flag 0x82, trade-tutorial flag 0x87, inherited world-map
   route flag 0x89 and saved world-map flag 0x8A, not just names already
   present in `event-flags.h`.
   Both complete guide display/read ranges reject under the progressive
   policy and are available only under the explicit static policy; their
   unused gap remains available. Native last-bit, chapter-index, empty-input
   and permanent-overlap boundaries are exercised without writing game flags.
   One-hot/counter fields whose later bits reach engine, debug or sort
   reservations must reject even when the first bit is free; a field crossing
   the guide-read boundary rejects only under the progressive policy.
   The standalone C corpus replaces the former mutable inline two-field
   script. It checks every ordering of three-field chapter/permanent,
   interval-edge, overlapping and distinct-lifetime examples under both
   guide policies, plus every colliding pair among eight descriptor positions.
   Input bytes must remain unchanged after every result, including refusals.
3. Keep the unselected default as the negative control: this module has no
   default build input or runtime hook. The pre-tracer interface was absent
   and the same host/ARM probes failed compilation. Do not treat either
   compiler success or these metadata vectors as target-ROM state evidence.
   The former adjacent-only overlap control survived compilation/execution;
   the complete ordering/position corpus now rejects it with a real native
   assertion, not a compiler or loader failure.

The tests use unique temporary directories and remove only those directories.
They never change the real Git index, existing save files or framework flag
buffers. The host probe uses only an ABI-type adapter; it compiles the real
validator rather than a retyped implementation. The ARM compile uses actual
framework headers and constants, including the supported Debian/Ubuntu
newlib-header fallback and the normal `MODERN_CC`,
`MODERN_TOOLCHAIN_ROOT`/`PREFIX`, and `MODERN_BINUTILS_DIR` overrides. The
existing full Build job and upstream verifier mirror run this suite after
their host/ARM dependencies are installed; no new job or tool prerequisite
is added. Concrete allocation, selected runtime/profile qualification, save
compatibility and gameplay integrations are intentionally not claimed.

## TC-GRADO-GUIDE-001

This deterministic subcase checks the static-guide adapter before any guide
bits may be reclaimed by a real project. It needs the same compilers as the
allocation case, plus the normal ARM binutils; no ROM or save is required.

1. Run
   `python3 -B -m unittest scripts.modernize.tests.test_grado_state_native -v`.
   The complete module must pass. The guide probe compiles the real native guide
   table, native flag backend and either the original or replacement guide
   source. It does not retype those implementations or ignore unresolved
   linker errors.
2. The original source is the progressive negative control: an empty flag
   buffer locks the guide; native unlock-all exposes entries but leaves them
   unread. The adapter also locks with truth unset, even if unrelated
   reclaimed display/read bits are set. Initializing truth exposes all56
   as read. Exercise both explicit truth initialization and the optional
   actual native permanent-reset producer; the latter clears the original
   permanent bytes then sets exactly the allocated truth bit, preserving
   the complete chapter buffer. Subsequent guide reads/writes change none
   of the 25 permanent or 5 chapter bytes. Exercise every
   display/read flag through the real guide consumers and policy operations;
   all 112 bits remain byte-identical.
3. Check all 240 valid native flag IDs: chapter 1 through 40 and permanent
   101 through 300. Guide ownership must match the actual native guide table,
   not a second copy of the range predicate. The unused gap 236 through 239
   and upper tail 296 through 300 remain ordinary flags, as do sort flag 179
   and chapter flag 7. From both cleared and set starting states, reads must
   preserve every backing byte; guide-owned reads equal the actual truth
   bit rather than an unconditional success. Set/clear operations must either
   preserve guide-owned storage or match the real backend's complete byte changes.
   Capture the complete initial backing once and restore it before each
   operation. In particular, clear-from-zero cannot inherit a prior set.
   The ARM object must route every guide flag call
   through the three public `GradoGuide_*Flag` policy functions; only those
   forwarders may reference the raw flag operations, chapter/permanent
   operations, reset/read-through/storage-pointer interfaces or backing
   arrays. Size-only queries are not storage access. This is the finite
   native flag-interface compiled-symbol/relocation contract, not a
   source-spelling check or arbitrary-C memory-isolation claim.

The pre-fix probes accepted a guide predicate incorrectly extended through
flag 299 and a rendered guide entry reading `CheckPermanentFlag` directly.
The complete flag-domain probe now rejects the former through a native
assertion; the ARM relocation check rejects the latter after successful
compilation. A compiler failure is neither negative control.
A toggle-instead-of-clear implementation compiled and survived the old
shared-state probe. With each operation isolated, it still compiles but the
real native probe aborts on clear-from-zero.

The host compiler removes only the unsupported ARM register annotation;
native ARM compilation retains it, and compiles the original event module
through its reset adapter too. The unused event-dispatch dependency is
an explicit abort-only test adapter: unexpected execution fails rather than
simulating success. Tests use actual native capacities/content and only
their own memory/temporary directories. The same source still implements
windows/navigation/sort, but no target-ROM UI judgment or selected-profile
save/lifecycle qualification is implied by these host and object checks.

## TC-GRADO-STATE-VALUES-001

This subcase exercises native field values, not the complete token allocation,
save identity, producer recovery or retirement rules. It uses the same Linux
source checkout, host `cc` and supported ARM GCC/headers as the other subcases.
No ROM, emulator, save file, credentials or manual judgment is needed.

1. Run
   `python3 -B -m unittest scripts.modernize.tests.test_grado_state_native -v`.
   The field probe links the real example codec/validator and original
   `src/eventinfo.c` flag routines against its own 25 permanent/5 chapter
   bytes, then compiles the value module for ARM7TDMI/Thumb AAPCS.
2. Observe explicit Boolean, bounded-counter and one-hot reads/writes. The
   counter encoding is checked through real native flag reads and complete
   expected backing bytes, not a second codec. The last permanent Boolean
   remains in bounds. A chapter-local access with the wrong project ordinal,
   an engine-reserved descriptor and an out-of-domain write refuse without
   changing backing. Invalid input preserves the output guard.
   Unknown guide-policy arguments report `INPUT`, not descriptor `LAYOUT`,
   for reads and writes of every kind, without changing either backing array.
   A monotonic permanent field accepts 0-to-0, 0-to-1 and 1-to-1 writes,
   refuses 1-to-0 without changing any backing, and cannot be allocated in
   chapter-local storage. This write primitive does not prove that the four
   production retirement states or every save/load consumer are wired.
   Guide-owned descriptors of all four kinds refuse both reads and writes
   under the progressive policy, preserving the output guard and every
   backing byte from cleared and set starting states. Under the static
   policy they round-trip through the actual native backend, with complete
   expected-byte checks across both guide ranges and their upper boundaries.
3. Zero one-hot backing reports `UNSET`, not a default choice. Multiple set
   bits or an encoded counter above its bound report `CORRUPT`, preserving
   every byte and the output guard. A later explicit valid write can replace
   those bytes; this is not permission for an automatic load-repair path.
   Valid accesses do not alter unrelated permanent/chapter storage.
   Explicit one-hot zero writes clear an already selected value in ordinary
   permanent, chapter-local and static-guide backing. Compare both complete
   arrays with direct native clearing, then read `UNSET` without changing the
   output guard or any backing byte.
4. Produce native packed bytes through actual value writes, then make the
   live buffers deliberately disagree. Read those independent saved-buffer
   views through `GradoState_ReadValueFrom`: permanent/chapter counters,
   one-hot and the last permanent bit must reflect the supplied bytes, not
   live state. A permanent-only view works without chapter storage.
   Null/short input, wrong chapter, reservations/guide ownership, unset and
   corrupt one-hot/counter values refuse without changing the output guard,
   either input or either complete live array. This is native-format field
   preflight, not real SRAM/envelope compatibility or load/recovery proof.

The absent pre-feature interface is a structural negative control, not
target execution evidence. The actual host vectors supply the positive and
adversarial value evidence; ARM compilation alone does not. As in the guide
probe, an unrelated retained event-dispatch dependency is an abort-only
adapter: executing it fails rather than simulating success.
Removing the actual monotonic write guard still compiles, but the native
probe aborts on the attempted clear; compiler failure is not that control.
Hard-coding either guide policy also compiles, but the same native probe
aborts on the opposite policy's read/write contract.
Returning success without clearing a valid one-hot zero write compiles and
survived the former vectors; the current native probe rejects that mutation.
Alternative fixture descriptors are not a production physical assignment.
The suites remove only their own temporary files. Default linking, the
native buffers/envelope and framework epoch remain unchanged; complete
Grado normal/Suspend/legacy loading, schema1/epoch256, atomic checkpoints,
producer-sensitive recovery, retirement, profiles and ROM/RAM remain open.
