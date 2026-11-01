# Restoration of Grado native-state reference

This is the unfinished optional reference for
[#315](https://github.com/laqieer/fireemblem8-expansion/issues/315).
It is not a selectable game project or a qualified Grado build yet. Nothing
here is linked by an unselected default framework build.

The current tracer implements `GradoState_ValidateAllocation`: a read-only C
check over typed Boolean, one-hot and bounded-counter descriptors. It rejects
unsupported domains, native capacity excess, engine/debug/sort reservations
and overlapping lifetimes. Different chapters may reuse temporary bits.
Permanent guide flags remain owned by the progressive guide unless the caller
supplies the static-guide policy. Supplying that policy is **not** evidence
that the native guide consumers have been redirected.

There is no complete production allocation, state mutation, save envelope,
epoch change, load hook, recovery, guide replacement or playable content in
this tracer. The complete 227-FLAG/16-STATE inventory, aliases and derived
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
   diagnostics must be empty of failures; both methods must pass.
2. The executed native vectors accept nonoverlapping descriptors and temporary
   bit reuse across different chapters. Same-chapter overlap, incompatible
   one-hot/count domains, unknown scopes, boundary overflow, chapter flags
   1 through 6, debug flag 39 and permanent engine/sort reservations reject.
   This includes inherited postgame flags 0x71 through 0x77, ending flag
   0x7D, boss-BGM flag 0x82, trade-tutorial flag 0x87 and saved world-map
   flag 0x8A, not just names already present in `event-flags.h`.
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
newlib-header fallback. The existing full Build job runs this suite after its
host/ARM dependencies are installed; no new job or tool prerequisite is added.
Runtime guide policy, concrete allocation,
save compatibility and gameplay integrations are intentionally not claimed.
