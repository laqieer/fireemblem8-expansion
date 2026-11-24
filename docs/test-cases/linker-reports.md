# Linker report feature cases

## TC-LINKER-REAL-ARTIFACT-001: Validate linked overlays and CJK headroom

- **Feature / originating issue:** `localization-profile-validation` /
  [#478](https://github.com/laqieer/fireemblem8-expansion/issues/478).
- **Supported configuration or artifact:** modern debug/AAPCS default build
  and 32 MiB `en,ja,zh-Hans` profile.
- **Prerequisites and clean starting state:** run from a source checkout with
  initialized dependencies, Python, and the supported ARM GCC/binutils.
  Use the default isolated build roots, without edited generated artifacts.

### Actions

1. Run `make expansion-modern-elf MODERN_CONFIG=debug MODERN_ABI=aapcs`.
2. Run `make expansion-modern-localization-profile-en-ja-zh-hans MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. Run `python3 -m unittest scripts.linker_report.tests.test_budget scripts.linker_report.tests.test_overlay_audit -v`.
   Confirm the two real modern-map cases, the CJK map/ELF case, and the
   persistent-EWRAM partition case report `ok`, not `skipped`.

### Expected result

The real maps contain the required named EWRAM overlays within hardware
bounds. The CJK map and ELF contain a matching nonempty allocatable locale
section and the linker-defined IWRAM user-stack margin is at least `0x1000`.
The reported stack boundary symbols match the linked ELF.
The existing real partition check retains persistent EWRAM after all overlays.
Exact free-byte totals are not fixed across toolchains.

### Negative control

The focused host controls reject a missing required overlay, an oversized
overlay, a missing, empty, nonallocatable or mismatched locale section,
insufficient IWRAM stack margin, and an incorrect linked stack boundary.
Before #478, the real CJK case and
both overlay count checks failed on valid artifacts due to stale values.

### Interactions and save compatibility

The existing linker, map/ELF validation, budget and overlay audits remain
authoritative; no CI gate is removed. No save, generated-game-data,
configuration, runtime or archival behavior changes.

### Automation

The unittest command above runs the real-artifact cases and focused malformed
report controls. Existing `budget.py --validate-elf` and
`--require-positive-headroom iwram` checks retain their independent failure
paths. No manual-only criterion applies.

### Cleanup and limitations

Build artifacts are ignored; use `make clean_fast` when cleanup is needed.
Map-only runs without the specified built artifacts skip the integration
cases and are not evidence for this procedure. The case does not claim
runtime behavior or CI timing improvement.
