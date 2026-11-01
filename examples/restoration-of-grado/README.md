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

An unselected static-guide adapter is also available for the existing
`src/bmguide.c` code slot: `src/static_guide.c` includes that native
implementation unchanged and routes only its flag operations through the
shared ownership policy in `src/state.c`. It does not duplicate guide
content, scripts, window, navigation or sort code. An eventual project must
replace that slot and include `src/state.c`; no project preset is activated
by this reference.

There is no complete production allocation, activated state/guide profile,
save envelope, epoch change, load hook, recovery or playable content here.
The complete 227-FLAG/16-STATE inventory, aliases and derived
entries, actual guide consumers, Grado schema 1/epoch 256, strict compatible
normal/Suspend/legacy loading and producer-sensitive lifecycles remain the
unchanged issue contract. Framework epoch 2 and its existing envelope are
untouched.

## TC-GRADO-ALLOCATION-001

This allocation subcase contributes to `TEST-STATE-TOKEN-AUDIT`; it does not
qualify the complete state, guide or save protocols.

Prerequisites: a clean source checkout on Linux, Python 3, host `cc` and the
supported `arm-none-eabi-gcc` toolchain from the normal quickstart. No ROM,
emulator, SRAM file, alternate profile or manual judgment is needed.

1. Run
   `python3 -B -m unittest scripts.modernize.tests.test_grado_state_native -v`.
   The test compiles and executes the real example C, then compiles the same
   source for ARM7TDMI/Thumb with the default AAPCS ABI. Compiler and process
   diagnostics must be empty of failures; all five methods must pass.
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
3. Keep the unselected default as the negative control: this module has no
   default build input or runtime hook. The pre-tracer interface was absent
   and the same host/ARM probes failed compilation. Do not treat either
   compiler success or these metadata vectors as target-ROM state evidence.

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
   All five methods must pass. The guide probe compiles the real native guide
   table, native flag backend and either the original or replacement guide
   source. It does not retype those implementations or ignore unresolved
   linker errors.
2. The original source is the progressive negative control: an empty flag
   buffer locks the guide; native unlock-all exposes entries but leaves them
   unread. The selected adapter exposes all 56 entries as read without
   changing any of the 25 permanent or 5 chapter bytes. Exercise every
   display/read flag through the real guide consumers and policy operations;
   all 112 bits remain byte-identical.
3. Sort flag 179 and ordinary chapter flag 7 still pass through to the real
   native flag backend. The ARM object must route every guide flag call
   through the three public `GradoGuide_*Flag` policy functions; only those
   forwarders may call raw `CheckFlag`, `SetFlag` or `ClearFlag`. This is a
   compiled-symbol/relocation contract, not a source-spelling check.

The host compiler removes only the unsupported ARM register annotation;
native ARM compilation retains it. The unused event-dispatch dependency is
an explicit abort-only test adapter: unexpected execution fails rather than
simulating success. Tests use actual native capacities/content and only
their own memory/temporary directories. The same source still implements
windows/navigation/sort, but no target-ROM UI judgment or selected-profile
save/lifecycle qualification is implied by these host and object checks.
