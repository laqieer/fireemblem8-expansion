# FE8 matching-build baseline

`baseline.json` is the machine-readable evidence contract for the final
agbcc-derived, byte-matching build before expansion-only development diverges
from it. It intentionally contains no timestamp, hostname, or path.

The manifest records:

- the latest commit outside recursive `reports/baseline/` output, whether that
  source tree is dirty, and the exact baseline-generator SHA-256;
- ROM SHA-1, size, and GBA title/game/maker/revision header fields;
- checksum-file verification and ROM-to-ELF content/padding consistency;
- a canonical ELF identity, entry point, allocatable section addresses/sizes,
  and symbol counts;
- a canonical GNU ld identity and memory output-section addresses/sizes;
- ROM, EWRAM, and IWRAM capacities, occupied bytes, and high-water marks; and
- normalized Python, Make, and ARM binutils versions plus distinct SHA-256
  fingerprints for the agbcc and old_agbcc binaries.

The ELF identity hashes only named allocatable section metadata and loaded
bytes. Debug sections are deliberately excluded because they embed build paths;
raw ELF size or hash is therefore not canonical evidence. Likewise, map
identity covers only output sections assigned to GBA memory regions, not debug
rows or the separate linker `Memory Configuration` table.

EWRAM overlays share addresses. Therefore `occupied_bytes` is the union of
section ranges, while `section_bytes_including_overlays` deliberately counts
every overlay. `high_watermark_bytes` is the highest allocated address relative
to the region origin. For ROM, `image_size_bytes` is the padded `.gba` size and
the other usage fields come from ELF allocatable sections.

Tool versions are normalized to semantic version components, excluding distro,
host, and absolute-path text. A failed version command is fatal even if it
prints version-looking text. Project-built tools without version interfaces are
tied to the recorded source commit.

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

Capture, including no-build capture, rejects checksum mismatches, ROM/ELF byte
or padding mismatches, non-ARM and malformed ELF files, ELF/map disagreement,
compiler fingerprint collisions, failed tool version commands, and malformed
map input rather than silently reporting untrusted evidence.
