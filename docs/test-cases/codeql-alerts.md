# CodeQL alert regression procedures

These cases cover issue #84's bounded Link Arena protocol and the confirmed
host/runtime correctness findings from the same CodeQL snapshot. They are bug
fixes, not optional features: valid protocol bytes and supported build
profiles remain compatible.

## TC-SIO-084: bounded Link Arena transfer

**Issue:** #84.

**Profiles and artifact:** Use a clean modern AAPCS debug or release build.
The deterministic packet harness is host-native and compiles the production
`src/sio_core.c`; the modern builds compile the same implementation for
ARM7TDMI. No save file is required.

**Prerequisites:** Start from the repository root with a host C compiler,
libpng development files, and the supported `arm-none-eabi` toolchain.
Reset the link-session state between manual packet cases. Two real consoles or
emulator link peers may supplement the deterministic harness, but are not
required evidence.

**Actions:**

1. Run `make codeql-alerts-test`.
2. Run the modern debug and release linker/runtime gates documented in the PR.
3. In the packet harness, send canonical payloads of 4 bytes, 0x28 bytes, and
   `SIO_MAX_DATA` bytes. Run the XMAP layout/copy case for exactly 0xC00 bytes.
4. Submit a truncated physical frame, a claim of `SIO_MAX_DATA + 1`, a payload
   larger than its destination, sender IDs 4 and 0xFF, a sender that does not
   match the physical lane, and one extra record to each full pending ring.
5. Submit a verifier-rejected payload and an invalid big-transfer header.

**Expected result:** Valid payloads arrive byte-for-byte. XMAP uses 26 payload
blocks (25 full blocks and a 22-byte final block) and changes exactly 0xC00
destination bytes. Every invalid case returns or reaches an error before
destination/verifier access, preserves destination canaries, sequence state,
unread queue entries, and rejected send entries, and emits no success ACK.
Failed sends do not advance caller state.

**Pre-fix negative control:** At base commit `cc1e14c`, the preserved native
reproducer queues a 16-byte message and calls the old capacity-free receive
API with a guarded four-byte destination; the guard is overwritten. The same
tree sends a 0x28-byte saved-unit record into `u8 buf[0x24]`. Its XMAP
arithmetic receives 26 full 122-byte blocks plus 22 bytes, or 3194 bytes, into
a 3072-byte object.

**Interactions and compatibility:** This applies to every locale and feature
profile. It changes no protocol representation for valid packets, no
configuration identity, and no save bytes. A malformed or spoofing peer is
unsupported and fails closed. The archival lane receives source-compatible
API changes but has no byte-identity requirement.

**Cleanup:** Use `make clean_fast` only if build artifacts need removal.

**Limitations:** The harness proves protocol semantics, queue state, canaries,
and exact XMAP size. It does not claim radio/link timing quality between
physical consoles; timing is outside this memory-safety bug contract.

**Automation:** `make codeql-alerts-test` runs
`tests/codeql/sio_protocol_host_test.c` under AddressSanitizer and
UndefinedBehaviorSanitizer. Modern debug/release/default builds provide target
compile, link, boot, and existing runtime integration evidence.

## TC-CODEQL-084: remaining confirmed alert boundaries

**Issue:** #84.

**Profiles and artifact:** Run from a clean checkout on the native host, then
build the modern debug and release AAPCS ROMs. No optional feature profile or
save fixture is required.

**Prerequisites:** Install the normal host-tool and modern build dependencies.

**Actions:**

1. Run `make codeql-alerts-test`.
2. Run the complete modernization audit suite and documentation checker.
3. Build `gbagfx` and `mid2agb`.
4. Run the modern debug, release, and default release gates.

**Expected result:** The bitfield matcher accepts valid qualified
declarations and rejects 5000 repeated qualifiers within its five-second
process-CPU budget. PNG buffer arithmetic accepts a normal image and rejects
dimensions or products that cannot fit before allocation/libpng writes. The
format-checked MIDI tool build accepts the fixed-width pattern label argument.
Valid map changes are in bounds; overflowing extents are rejected. Empty event
dequeue, a 31st queue push, and negative or out-of-range event-slot queue
operands fail without state corruption. Six scripted hits plus the required end
marker fit in seven entries; a seventh payload cannot overwrite the guard.

**Negative control:** These cases correspond to the pre-fix ambiguous nested
regex, narrowed PNG products, mismatched `%lu` argument, wrapping `u8` map
indices, unchecked event-slot indexing, empty-queue decrement, and
seven-payload-plus-sentinel overwrite. Sanitizer guards and explicit state
comparisons detect partial writes.

**Interactions and compatibility:** There are no dependencies or conflicts
with starter features, localization, generated data, item caps, configuration
identity, or saves. Valid authored maps/events and normal tool inputs retain
their prior output.

**Cleanup:** Use `make clean_fast` for ROM artifacts. Host test binaries live
under ignored `build/tests/codeql`.

**Limitations:** Code-scanning state for source fixes changes only after
GitHub analyzes the delivered commit. Evidence-backed false positives are
dismissed in GitHub rather than suppressed in source.

**Automation:** `tests/codeql/runtime_bounds_host_test.c`,
`tests/codeql/png_bounds_host_test.c`, and
`scripts/modernize/tests/test_audit.py` are all invoked by
`make codeql-alerts-test`.
