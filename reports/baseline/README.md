# FE8 matching-build baseline

`baseline.json` is the machine-readable evidence contract for the final
agbcc-derived, byte-matching build before expansion-only development diverges
from it. It intentionally contains no timestamp, hostname, or path.

The manifest records:

- the latest source commit outside `scripts/baseline/` and `reports/baseline/`;
- ROM SHA-1, size, and GBA title/game/maker/revision header fields;
- ELF SHA-1, entry point, allocatable section addresses/sizes, and symbol counts;
- map SHA-1 and GNU ld output-section addresses/sizes;
- ROM, EWRAM, and IWRAM capacities, occupied bytes, and high-water marks; and
- normalized agbcc, old_agbcc, Python, Make, and ARM binutils versions.

EWRAM overlays share addresses. Therefore `occupied_bytes` is the union of
section ranges, while `section_bytes_including_overlays` deliberately counts
every overlay. `high_watermark_bytes` is the highest allocated address relative
to the region origin. For ROM, `image_size_bytes` is the padded `.gba` size and
the other usage fields come from ELF allocatable sections.

Tool versions are normalized to semantic version components, excluding distro,
host, and absolute-path text. Project-built tools without version interfaces
are tied to the recorded source commit.

## Regenerate

From the repository root, with the normal build dependencies installed:

```sh
python3 scripts/baseline/capture.py --build \
  --output reports/baseline/baseline.json
```

`--build` runs `make clean` followed by `make compare -j<N>` before capture; it
does not alter the Makefile. To capture already-built artifacts:

```sh
python3 scripts/baseline/capture.py \
  --output reports/baseline/baseline.json
```

Verify deterministic regeneration and run focused tests:

```sh
cp reports/baseline/baseline.json /tmp/fe8-baseline.json
python3 scripts/baseline/capture.py \
  --output reports/baseline/baseline.json
cmp /tmp/fe8-baseline.json reports/baseline/baseline.json
python3 -m unittest discover -s scripts/baseline/tests -v
```

Any ELF/map disagreement is rejected rather than silently reported. Missing,
truncated, non-ELF, and malformed map inputs also fail with a concise error.
