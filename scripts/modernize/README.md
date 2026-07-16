# Modern-Compiler Blocker Audit

This stdlib-only Python audit turns compiler and layout debt into a deterministic
migration inventory.

## Run it

From the repository root:

```sh
python3 scripts/modernize/audit.py
python3 scripts/modernize/audit.py --check
python3 -m unittest discover -s scripts/modernize/tests -v
```

The normal run writes `reports/modernize/inventory.json` and
`reports/modernize/inventory.md`. `--check` writes nothing and exits nonzero if
either committed report differs byte-for-byte.

The scanner reports these stable categories:

- `register-pinned-local`
- `inline-asm` and `inline-asm-barrier`
- `naked-function`
- `old-style-function-declaration`
- `mismatched-signature`
- `layout-sensitive-struct`
- `forced-data-placement`
- `raw-rom-address`
- `modern-conditional`
- `legacy-compiler-pipeline`
- `linker-placement-coupling`
- `fixed-rom-base`
- `assembly-boundary`

Each JSON finding has an ID, detected/current category, severity, priority,
repository-relative file and line, evidence, subsystem, status, and recommended
disposition. It also names a `root_kind`, `root_construct`, and `root_line`.
Top-level `aggregates` group findings by actionable source construct, struct, or
manifest and retain every member in `finding_ids`; aggregation never hides or
replaces the drill-down list. No timestamp or absolute path is emitted.

## Resolve, retain, or reclassify

Do not delete or broadly ignore a finding to make the dashboard smaller. Add an
entry to `scripts/modernize/decisions.json` using the finding's JSON `id`:

```json
{
  "id": "inline-asm:0123456789ab",
  "status": "retained",
  "reason": "BIOS SWI remains behind the reviewed platform boundary"
}
```

Allowed statuses:

- `resolved`: migration work is complete. The active finding remains visible
  until the triggering construct is removed; after removal, the decision remains
  as an inactive ledger record.
- `retained`: the boundary is intentional. A concrete reason is mandatory.
- `reclassified`: add `"category": "new-category"` and optionally a new
  `"disposition"`. The original `detected_category` remains in JSON.

Run the audit again and commit the decision and reports together. IDs derive
from category, relative file, normalized evidence, and duplicate ordinal, so
unrelated line movement does not churn them.

## False-positive policy

Suppressions are narrow and are also recorded in every JSON/Markdown report:

1. The scanner and generated reports are excluded to prevent self-matches.
2. Target-C ABI checks cover `src/` and `include/`, not vendored host tools.
3. C comments and GNU ARM `@` comments containing ROM addresses are ignored.
   Source literals require pointer/address syntax, so palette fills and integer
   layout data do not masquerade as pointers.
4. Marker macro definitions are not counted as marker use sites; direct
   `__attribute__((naked))` function sites are counted.
5. Empty `SHOULD_BE_CONST` annotations are not placement constraints.
6. CP932 is toolchain debt only in a compiler-coupled build recipe. Generic
   text/asset decoding remains a data-format concern.
7. Assembly is represented once per source boundary, not once per directive.
8. Empty-parameter declarations are reported only for declaration-shaped target
   C lines.

If a new false-positive class appears, add a precise syntactic rule and a
fixture proving both the suppression and the nearby true positive.
