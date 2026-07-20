# Linker Reports

Deterministic memory-budget report from a GNU ld `.map` file.

## Usage

```bash
# Map-only (no ELF cross-validation):
python3 scripts/linker_report/budget.py \
    --map fireemblem8.map \
    --output reports/linker-budget.json

# With ELF cross-validation (graceful degradation if readelf unavailable):
python3 scripts/linker_report/budget.py \
    --map fireemblem8.map \
    --elf fireemblem8.elf \
    --output reports/linker-budget.json

# Check mode (exits 1 on map-derived memory-budget drift):
python3 scripts/linker_report/budget.py \
    --map fireemblem8.map \
    --output reports/linker-budget.json \
    --check
```

## Output

A JSON report with:

- **regions** — per-region capacity, occupied bytes, free bytes, utilization %.
- **sections** — every output section with address, size, overlay flag, region.
- **overlays** — EWRAM overlays grouped by base address with per-group peak.
- **pinned_assignments** — linker symbol assignments at GBA memory addresses.
- **elf** — optional ELF section cross-validation (when `--elf` is supplied).

The report is deterministic: no timestamps, no absolute host paths, stable
ordering (by address then name).

Check mode compares the map-derived regions, mapped sections, overlays, pinned
assignments, and overflow state. Optional ELF diagnostics remain in the report
for inspection but are not baseline fields because toolchain/host metadata may
vary without changing the linked memory layout.

## Exit codes

| Code | Meaning |
|------|---------|
| 0    | Success, no region overflow |
| 1    | Region overflow detected, or `--check` found drift |
| 2    | Malformed input or missing file |

## Tests

```bash
python3 -m unittest discover -s scripts/linker_report/tests -v
```

## Overlay audit

`overlay_audit.py` checks every overlay against EWRAM capacity and maps retained
`.relROM` relocations back to their owning object. If an object that owns data
in one overlay references a symbol owned by another overlay, the audit fails
with both object and symbol names.

```bash
python3 scripts/linker_report/overlay_audit.py \
    --map build/expansion-modern/debug/aapcs/fireemblem8.map \
    --elf build/expansion-modern/debug/aapcs/shiftcheck/fireemblem8.relocs.elf \
    --output build/expansion-modern/debug/aapcs/shiftcheck/overlay-audit.json \
    --require-relocations
```

`--require-relocations` is the CI mode: missing tools, a missing retained-reloc
ELF, timeout, or an ELF without relocations is an error rather than a skip.
