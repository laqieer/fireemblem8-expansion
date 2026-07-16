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
disposition. No timestamp or absolute path is emitted.

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
3. C comments and GNU ARM `@` comments containing ROM addresses are ignored;
   executable/build/linker literals are retained.
4. Marker macro definitions are not counted as marker use sites.
5. Assembly is represented once per source boundary, not once per directive.
6. Empty-parameter declarations are reported only for declaration-shaped target
   C lines.

If a new false-positive class appears, add a precise syntactic rule and a
fixture proving both the suppression and the nearby true positive.
