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

`src/lifecycle.c` is the real optional consumer policy for this reference.
Epoch 256 denotes Grado schema 1; it still uses the framework's existing
format/envelope version 2 and no additional save bytes. Every normal, Suspend
and legacy-compatibility consumer must run `GradoState_PreflightSave` on a
readonly view before calling an original loader. It requires the existing
global classifier to report `CURRENT`, validates every applicable permanent
field and the current Suspend chapter, and returns without changing the view,
metadata or live flags on refusal. Vanilla/legacy metadata, the default epoch
2 and any non-current history are incompatible rather than migrated.

Chapter-clear facts make preceding choices required. Roderich and Lund instead
use separate monotonic completed-resolution facts in the existing allocation;
their CH10/CH14 exposition cannot settle either outcome. The actual CH18
producer calls `GradoState_RecordResolution` only after a valid outcome is
committed. Before that completion, unset remains unset.
Valid stored choices are preserved; multi-hot choices conflict. Supported
missing choices recover to their accepted values, except MED/DATA with recorded
absence markers, which report and preserve the missing state. Post-resolution
Roderich/Lund recover to EXILED/ABANDONED, with no item/unit/resource operation.
Preflight checks the complete history before returning a recovery action;
unrecoverable missing choices and direction reopening take precedence.
Preflight binds its diagnostic to the 25 permanent flag bytes and, for Suspend,
the five chapter flag bytes it inspected. `GradoState_ApplyLoadRecovery` refuses
stale live flags, then completes every recoverable correction without yielding.
Its successful diagnostic lists recovered symbols in `recoveredSymbols` up to
`recoveredCount` and reports guide initialization separately. If a correction
fails or makes no progress, it restores both live flag scopes and reports a
conflict. A normal-load recovery does not bind unrelated chapter flags.
This rollback covers the optional flag consumer only; it does not make the
original loader or its SRAM writes transactional.
A completed final-direction
producer with no direction returns the reopen action instead of choosing an
ending. `GradoState_ResolveEnding` therefore never invents a default ending.
New-game and new-chapter lifecycle calls reset their native scopes and are
separate from Suspend resume and normal loads. New game also restores static
guide ownership, while a chapter reset preserves permanent history.

This remains an unselected optional reference. The project slot is not wired
to the framework by default, and no selected game UI or playable campaign is
claimed. Framework epoch 2 remains the negative/default profile. Do not add a
second save blob or bypass the preflight when adapting this source. The native
compatibility subcase below proves the shared classifier; the whole-path case
proves representative strict Grado preflight followed by original loading.

## TC-GRADO-SAVE-COMPAT-001

This subcase of `TEST-SAVE-COMPAT` executes the original live global
classifier, metadata/checksum builders and SRAM initialization/read routines
in an ARM7TDMI/Thumb AAPCS release-mode module under libmGBA. It builds the
same native code with configured epochs 2 and 256; the existing envelope stays 2.
This is not a Grado game profile or a separate save record.

**Prerequisites:** repository root, Python 3, the supported ARM toolchain and
libmGBA from quickstart. No user ROM, save, savestate or external project is
read. The fixture redirects the native `gSram` pointer to an owned 32 KiB
EWRAM image; it does not claim cartridge-bus or complete game-loader evidence.

1. Run
   `env -u GBA_PLAYTEST_HOST_ONLY python3 -B -m unittest tools.gba-playtest.tests.test_grado_save_compat_native -v`.
   The existing debug ARM-enabled linker invocation runs this module in
   candidate/master Build CI. Explicit host-only discovery skips it.
   Both physical and symlink-discovered test sources must execute the same
   native probe; all compiler source paths use the canonical repository root.
   The linked control imports a fresh module through the symlink before
   running the actual native case; a skipped or missing case is not evidence.
2. Both uniformly zeroed and erased images classify `EMPTY` after the real
   hardware-probe initialization. Erased-looking headers with a surviving
   payload, or inconsistent erased header/metadata patterns, refuse as corrupt.
3. Current native metadata classifies `CURRENT` only for its configured
   epoch. The other profile's epoch refuses as incompatible. Valid legacy
   headers without metadata, older/newer formats, bad header/metadata checksum
   and a false hardware-ready flag retain the named results below.
   An older format wins over a simultaneously foreign epoch, preserving the
   framework's existing classification order.
4. ABI/version/fingerprint/build diagnostics with a valid recomputed checksum
   remain compatible. Compare every byte of the complete owned image after
   every classification; both positive and refusal paths must leave it intact.
   Capture the actual 13 semantic records per epoch and measured ROM/EWRAM/IWRAM.

The results use the existing `SaveCompatState` categories; they are not a
unique error code for every input cause.

| Input group | Native result |
| --- | --- |
| Uniform all-zero or erased image | `EMPTY` |
| Valid header without expansion metadata | `VALID_LEGACY_OR_VANILLA` |
| Current format and configured epoch | `CURRENT` |
| Current format with the other epoch | `SAVE_CONFIG_INCOMPATIBLE` |
| Older format, including a foreign epoch | `MIGRATABLE_OLDER` |
| Newer format | `NEWER_UNSUPPORTED` |
| Bad metadata checksum | `METADATA_CORRUPT` |
| Bad header, false hardware readiness, surviving payload or mixed erased regions | `HEADER_CORRUPT` |
| Diagnostic-only changes with a valid checksum | `CURRENT` |

**Controls and limits:** wrong epoch, missing/corrupt metadata and surviving
payload are actual native inputs, not fabricated success labels or source-text
checks. The default epoch 2 is the unselected control. The ordinary supported
native layout is required; forcing an unrelated packed-struct ABI is not this
profile. The module uses the canonical IWRAM selectors for the native SRAM
workspaces before collecting remaining BSS in EWRAM, including the named
sound-table owner. Compiler objects retain their original source-relative
paths so those selectors match. Check the actual ELF workspace bounds and
live SRAM function pointers as well as native classification; merely
including the linker script is insufficient. This proves these copied SRAM
routines' placement, not the complete production game's globals or overlays.
No successful substitute functions are used.

No production code, defaults, format, epoch, flags or persistent RAM change.
Actual normal/Suspend loading, producer-sensitive recovery/checkpoints,
resources/retirement, selected guide/game profiles and complete target
qualification remain open in #315. Temporary compiler/ROM/capture files are
removed only from the fixture's owned build directory.

## TC-GRADO-SAVE-FLAGS-001

This bounded `TEST-SAVELOAD` / `TEST-SUSPEND` subcase uses the same native
module, prerequisites, source command and owned image as the compatibility
case. It links the original `eventinfo.c` getters and native flag serializers;
the canonical linker supplies the actual IWRAM flag arrays.

1. Run
   `env -u GBA_PLAYTEST_HOST_ONLY python3 -B -m unittest tools.gba-playtest.tests.test_grado_save_compat_native -v`.
   Both epochs and physical/fresh-linked imports must execute.
2. Exercise all three normal slots and both Suspend slots. Virtual slots 0..2
   denote normal fields; 3..4 denote Suspend fields. For each, use seeds
   `0`, `0xFF`, `0xAA`, `0x55` with byte vectors
   `permanent[i] = seed ^ (29*i) ^ (3*slot)` and
   `chapter[i] = ~seed ^ (17*i) ^ (3*slot)`, reduced to eight bits.
3. Execute the real write functions. All 25 permanent bytes and, for Suspend,
   five chapter bytes must match. Compare the entire 32 KiB image against an
   independent rolling snapshot updated only at those fields; the two unused
   bytes in each Suspend struct's seven-byte chapter field are seeded with
   `0xA5` / `0x5A` before the snapshot and must remain unchanged. A seven-byte
   native write must fail this boundary, not hide in zero-filled padding.
4. Overwrite live arrays with `0xC3` / `0x3C`; they must now differ from the
   saved vectors. The original non-destructive permanent view returns the
   saved bytes without changing either live array. Original reads restore
   every saved byte, preserve the other scope and never mutate the image.
   Guard the native live-array ends with `0xD6` / `0x6D` in the module's
   verified IWRAM padding and the view output with `0xE2` / `0x2E` before/after
   its 25 bytes. Check guards immediately after each original read. Oversized
   permanent/view 26-byte and chapter seven-byte reads must all fail.
5. Inspect all 20 distinct slot/pattern records per epoch, actual flag-storage
   bounds, copied SRAM function pointers and measured native resources.
   The existing thirteen classification records still pass. Explicit
   host-only skipping is not proof; a no-op read fails the clobber control.

**Boundary:** this proves existing flag-transfer primitives and typed slot
fields, not complete normal/Suspend entrypoints, block checksums/metadata,
cartridge-bus timing, legal semantic state combinations, producer recovery,
guide consumers or a supported Grado profile. No production source, save
layout, defaults, epoch, feature flag or persistent allocation changes.
The existing ARM-enabled module invocation covers this case; no new job,
timeout, framework service or registry is introduced.

## TC-GRADO-SAVE-PATHS-001

This `TEST-SAVELOAD` / `TEST-SUSPEND` subcase extends the same ARM/libmGBA
module builder to the original whole writer/loader paths. It uses owned
valid generic unit/class/item providers, not the unfinished Grado roster.
All save, flag, unit, world-map, preference, SRAM and handwritten ARM checksum
functions come from their original source owners; no function is stubbed.
The existing `gbagfx` helper builds only the original data required for linking.

1. With the compatibility case's toolchain/emulator prerequisites, run
   `env -u GBA_PLAYTEST_HOST_ONLY python3 -B -m unittest tools.gba-playtest.tests.test_grado_save_compat_native -v`.
   The additional whole-save case must execute at epochs 2 and 256, not skip.
2. In one current owned image, save valid native unit/play state in every
   normal slot 0..2. Use `permanent[i] = 0xA5 ^ (29*i) ^ (7*input)` and
   `chapter[i] = 0x5A ^ (17*i) ^ (7*input)`, reduced to eight bits. Give
   each backing slot a distinct saved bonus-claim value. Call original
   `WriteGameSave` and require `ReadSaveBlockInfo` acceptance. Clobber live
   permanent flags, gold/chapter, backing-slot identity, bonus flags and the
   first complete unit, then call `ReadGameSave`. All 25 permanent bytes,
   saved gold/chapter, slot and bonus flags, and unit identity, class, level
   7, experience 33 and position (2,3) return; chapter flags stay intact.
3. Keep all three normal blocks valid and call original `WriteSuspendSave`
   twice for each referenced backing slot through its real alternating-slot
   selection. Check the stored backing identity, normal block checksum and
   its distinct bonus value, and validate each Suspend through the original
   API. Clobber both flag scopes, slot identity, bonus flags and live state,
   then call `ReadSuspendSave`. Both physical slots 3 and 4 must execute
   against all three normal slots, restoring the saved observations and the
   bonus flags from the referenced normal block rather than another slot.
4. After each valid path, flip one stored permanent byte. The original block
   checksum validator must now refuse without changing any of the owned
   32 KiB input image. Restore the byte. This checks native recognition of
   corruption, not refusal by the unchecked raw loader or a new load policy.
5. Inspect all nine distinct records per epoch and measured ROM/EWRAM/IWRAM.
   Omit actual permanent restores from the normal/Suspend call chains: all
   nine paths fail. Omit only the chapter restore: all six Suspend paths fail.
   Force `IsValidSuspendSave` to refuse while retaining the correct resolved
   slot: all six Suspend validity records must fail, not merely load that slot.
   Restore the original functions before running the complete module.
6. In a separate reset of the same target module, hold synthetic A throughout
   the capture to select the semantic fixture. It uses three real,
   independently valid backing normal saves with distinct bonus values.
   Cover each normal slot and both physical Suspend
   slots for every backing slot. At epoch 2, preflight must refuse Grado schema
   identity without touching the image or live state. At epoch 256, preflight
   valid typed histories, clear the live typed state, require successful public
   Suspend validation, and allow the original normal/Suspend loaders to restore
   the saved ending and referenced bonus value. Suspend also restores the set
   final-witness chapter flag and the complete chapter bitmap. Omitting either
   loader or its bonus restore must fail that route's records; earlier unrelated
   load cases cannot mask it.
   In another reset, hold synthetic A+B to select the epoch-256 refusal and
   recovery cases. The legacy case retains a valid native header but removes
   expansion metadata: the real classifier must report legacy and preflight
   must refuse. The recorded-absence/missing-state, one-hot conflict, legacy
   refusal, Roderich EXILED recovery and missing-direction reopen preflights
   preserve their input image and live state. Recovery changes the intended
   Roderich state without resource or unit mutation. These refusal/recovery
   cases use a live flag view; they do not claim a physical save was loaded.
7. In that same epoch-256 capture, write two missing choices and their
   producers into valid normal and Suspend blocks backed by normal slot 0.
   Make the live choices valid, and make live Suspend chapter flags differ
   from the saved flags. Preflight must still diagnose the saved view without
   changing either view. Clobber live state and call the corresponding original
   loader. A single recovery must report both symbols and match the complete
   expected permanent bitmap, preserving every other valid choice, the post-load
   image, chapter flags, unit, play state and bonus. Repeat both routes after
   changing an unrelated permanent flag for normal or a chapter flag for
   Suspend: recovery must refuse and preserve both live flag scopes and the
   post-load image. The test-owned preflight is not production caller integration.

Each capture retains the existing 120-frame, 1 MiB ROM, EWRAM and IWRAM-stack bounds.
Native normal loading intentionally invalidates Suspend metadata; the case
does not falsely require the whole image to remain unchanged on a valid load.
All original compatibility/transfer cases still execute. Cleanup removes only
owned temporary modules; no user save or clean ROM is used.

**Boundary:** this qualifies representative original whole-function
save/load, block-checksum and strict Grado semantic preflight paths with
controlled native data, including producer/missing/conflict/recovery and
normal/Suspend/legacy boundaries. It is not cartridge-bus, complete game,
selected UI, locale, or playable-profile qualification. The module is
release-mode; the host campaign case separately compiles the lifecycle C for
both debug and release settings. No core engine source, default, format, epoch,
allocation or framework gate changes.

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
5. Populate a complete valid schema-1/epoch-256 history and preflight it on
   normal, Suspend and legacy-compatible routes. At each call, compare both
   complete native buffers and the caller's metadata/view. Refuse default
   epoch 2 and legacy classifier state without mutation. Advance the actual
   producers to prove missing choices, multi-hot conflicts, all nine accepted
   recovery values, recorded MED/DATA absence refusal, and missing-direction
   reopen. CH10 clearance must leave Roderich unset; record actual valid CH18
   resolutions before simulating a missing Roderich/Lund outcome. Recover
   EXILED/ABANDONED without resource writes. Combine missing guide truth and
   all nine recoverable choices plus Roderich/Lund on normal, Suspend and
   legacy routes; one successful call must report all eleven symbols and guide
   initialization while preserving every other permanent flag. Preflight saved
   views that differ from valid live flags, then load and recover those saved
   views. Inject a failed or no-op later write on every route, including after
   guide initialization, and compare both complete flag scopes after refusal.
   A changed permanent snapshot must refuse on every route; a changed chapter
   snapshot must refuse Suspend but must not alone refuse normal recovery.
   Missing direction still reopens.
   Valid choices and the derived ending must survive. New game resets
   both scopes and initializes guide truth, while new chapter resets only
   chapter flags. Compile the same lifecycle module for ARM with debug values
   0 and 1 in addition to the release-mode target save cases.

The same native corpus exercises all 14 public one-hot fields and all 8 stored
counter bindings using independent source-domain bounds, not descriptor
metadata. Every legal value round-trips; zero is `UNSET` for one-hot and
valid for counters. All 34 choice/direction/transition backing views agree
with their native values, refuse corrupt input and remain read-only.
Exercise all 16 native direction/ending bit patterns and all 8 private
transition patterns: unset semantic direction/ending preserves the output
guard, unset Boolean views are false, valid one-hot values project exactly,
and every multi-bit pattern refuses without exposing a default ending or
partial alias truth. After every read/refusal, compare both complete native
arrays immediately; do not let later fixture preparation hide a write.
All 4 retirement facts refuse clearing. An upper-bound write preserves both
complete native arrays.

The four one-variable wrong-transition-target, unset-ending-default,
corrupt-ending-default and corrupt-transition-default controls compile and
survive the earlier corpus but abort in this native probe. These are actual
projection/guard controls, not source-spelling assertions or whole-suite
results relabelled as target-ROM evidence.

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
is added. These generic descriptor controls do not independently qualify
the concrete candidate campaign allocation exercised above. Selected
runtime/profile qualification, save compatibility and gameplay integrations
remain unclaimed by either subcase.

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
   reclaimed display/read bits are set. Initializing truth exposes all 56
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

For the separate project-selection and ARM runtime subcase:

1. Run
   `env -u GBA_PLAYTEST_HOST_ONLY python3 -B -m unittest tools.gba-playtest.tests.test_grado_guide_selected_native -v`
   with the supported ARM toolchain and libmGBA. It creates and removes its
   own temporary project and build directories; no save or existing project
   is changed. The existing debug ARM-enabled linker suite also runs it.
2. The real Make project selector compiles the guide and event-info logical
   slots with both adapters and `state.c`, requiring epoch 256 for the
   temporary selected configuration. The default control uses the original
   slots and epoch 2. Run each as a debug and release AAPCS module.
3. With guide truth unset but reclaimed display/read bits set, the selected
   guide remains locked; the default control exposes an unread entry. Seed
   every permanent byte before reset. The selected permanent reset clears
   all 25 bytes except truth and preserves the five chapter bytes; the default
   reset clears permanent flags without setting truth. After the native
   unlock-all and category consumers run, both expose all 56 entries in
   ordered topic categories 1 through 11 and chapter categories 0 through 8.
   The selected entries are all read without changing their backing bits;
   the default entries remain unread with their native display bits set.
   Inject two SELECT key events into the real guide main loop, from topic to
   chapter and back. Both transitions must reset the four navigation indices,
   set then clear the native sort flag, request the actual blocking redraw
   script and preserve all other permanent and chapter bytes.
4. The linked policy symbols and compiled reset slot distinguish the selected
   module from the default control. The native flag buffers are checked for their
   25/5-byte extents and nonoverlapping IWRAM addresses. Module ROM/EWRAM/IWRAM
   measurements are printed for each configuration. The fixture isolates
   inherited feature settings, preserves toolchain overrides and links inside
   a non-ASCII build directory.

The test selects real source slots but deliberately splits functions and
`CONST_DATA` into per-symbol writable sections. A test-owned proc adapter checks
the requested script and parent but does not execute or schedule the redraw;
unrelated UI/audio adapters abort if reached, with sound effects disabled.
The synthetic key events do not test physical key sampling. Test-owned
buffers are not the complete game linker layout. These observations do not
establish whole-game ROM/RAM, rendered guide UI, reopening the guide, a
supported project profile, or save/load behavior.

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
   either input or either complete live array. Snapshot and compare each
   call's input/view metadata and live buffers immediately, before any later
   fixture preparation or restoration can hide an invalid call's writes.
   This is native-format field
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
Null/short-view controls that clear live permanent flags also compiled and
survived the former end-of-sequence checks, because later fixture preparation
restored those bytes. Immediate per-call preservation now rejects both through
native assertions. The short-buffer admission control was already detected;
its original bounds-rejection evidence is retained.
Alternative fixture descriptors are not a production physical assignment.
The suites remove only their own temporary files. Default linking, the
native buffers/envelope and framework epoch remain unchanged; complete
Grado normal/Suspend/legacy loading, schema 1/epoch 256, atomic checkpoints,
producer-sensitive recovery, retirement, profiles and ROM/RAM remain open.
