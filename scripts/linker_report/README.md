# Linker Memory-Budget Report

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

# Check mode (exits 1 on drift from committed report):
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
