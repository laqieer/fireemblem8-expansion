# Text generation and atomic publication

The shared text producer is `scripts/texttools/textprocess.py`. Modern and
archival builds use its existing message-ID and Huffman formats; this is
separate from authoring expansion localization catalogs.

```bash
python3 scripts/texttools/textprocess.py texts/texts.txt texts/textdefs.txt \
  src/msg_data.c include/constants/msg.h utf8
```

The CLI arguments remain text source, control definitions, C output, header
output, and encoding (`utf8` or `cp932`). Input includes remain relative to the
input file. An absolute script path also works from another working directory,
including paths with spaces; the sibling `huffman` import does not require
installing another package.

## Publication contract

[Issue #268](https://github.com/laqieer/fireemblem8-expansion/issues/268) fixes
readers observing shared files while the generator was still writing them.
Separate object-build roots do not isolate the shared `src/msg_data.c` recipe
and `include/constants/msg.h`. The previous producer opened final names with
`w`, so a real compiler could encounter a naturally buffered partial header
with an unterminated `#ifndef`.

Each output is streamed into a unique, exclusively created staging file in
the destination directory. Both renderers and closes must finish before any
publication. Each changed file is then atomically replaced; complete existing
files remain available while staging is in progress. Byte-identical output
keeps the existing inode and modification time. Existing permission bits are
preserved; new files use ordinary `0666` creation subject to the caller's
umask. Existing output symlinks are resolved to their targets before staging.
Only the invocation's own staged paths are cleaned, and failures propagate.
Previously correct inputs retain their decoded content and generated-byte
format. Fixing an unterminated message changes the Huffman frequencies, so
compressed bytes may change even for otherwise unchanged messages.

Named controls in each selected source or included file must be defined;
unmatched or nested brackets are rejected. The error identifies the source
and message ID before either output is published. Comments are removed before
splitting messages: line comments begin with `//` at the start of a source
line, and `/* ... */` comments may span lines. Brackets inside comments are
ignored. Removing a comment cannot create a directive, and comment markers
inside quoted include paths remain literal. This syntax check does not by
itself qualify control ordering, native references or storage capacity.
Message IDs across the main source and its includes must be unique and dense
from zero; generated message definitions must also be unique and cannot use
the header's reserved `MSG_H` or `MSG_COUNT` names. Declarations may appear out
of order: output is ordered by numeric ID so `gMsgTable[id]` matches the
declared message. Gaps, duplicates and reserved names are rejected before
either output is published.
Each encoded message must end with exactly one standalone zero symbol, the
terminator consumed by the native Huffman decoder. A printable byte immediately
before the terminator does not absorb it into a two-byte symbol. Control
boundaries distinguish a UTF-8 `0x80` continuation or CP932 trail byte from an
extended control, including glyph bytes supplied through named definitions.
They also distinguish zero operands from the terminator. A face control
consumes two operand bytes; extended subcodes 0–3 consume one additional byte.
Representable zero operands are retained, but consecutive zero bytes cannot be
encoded by the native decoder and are rejected. Missing, embedded or
control-consumed terminators and truncated controls are rejected before
publication. This byte-level check does not qualify every consumer or establish
that a control is semantically valid in a selected locale.
If every message is empty, the generated Huffman tree still has a branch and
each compressed stream contains a bit for the native decoder to consume.
The default and archival Make recipes both track the producer and its
Huffman module through separate input-freshness stamps. Identical regeneration
keeps output mtimes, while changed output invalidates its Make consumers;
missing outputs or stamps are restored without repeatedly invoking the producer.
Normal builds make a lightweight output probe even on a no-op, so Make sees
changes caused by `-W` as well as filesystem timestamps. `make -q` reports
actual freshness, but `make -n` may conservatively list downstream work that a
real no-op build skips. If both `MAKEFLAGS` and `MFLAGS` are overridden on the
command line, Make hides its actual mode from the recipe graph; the probe stays
enabled, so `make -q` conservatively reports stale rather than risking a stale
consumer.

This is **per-file atomicity**, not a multi-file transaction. A replace failure
after the header was committed can leave a complete new header and complete
old C file. The producer never rolls back another writer's committed file.
Same-input concurrent producers are supported; different configurations that
intentionally share output names do not gain pair-level version isolation.
Publication may replace inodes and does not claim crash durability,
preservation of arbitrary external ACL/ownership metadata, or protection from
concurrent retargeting of filesystem links.

The regression suite has one explicit `extended-host-tests` owner next to the
texttools codec suite:

```bash
python3 -m unittest scripts.texttools.tests.test_textprocess_publication -v
```

It is not hidden in the `test_multilang_codec*.py` pattern and does not
rediscover unrelated text suites. The upstream mirror retains every prior
gate and this gate, with **the same eight jobs**. Job
conditions, timeouts, required contexts, approvals and master-only build-once
patch publication remain unchanged.

## TC-TEXT-ATOMIC-PUBLICATION-001: Publish complete shared text outputs

- **Issue/configuration:** #268; ordinary host text generation for modern and
  archival inputs, without a ROM or ARM runtime.
- **Prerequisites:** Python 3 and a host `cc` compiler/preprocessor. Use an
  owned temporary directory with synthetic text/control inputs, a minimal
  `global.h` defining `u8`/`u32`, and complete prior header/C outputs.
- **Starting state:** do not inject faults into live project outputs. The
  suite creates and removes its own fixture files and producer processes.

1. Run the exact regression command above. At the original renderer's natural
   buffered-write boundaries, read both final paths and run the real header
   preprocessor and C syntax reader. Require complete old output during
   staging and complete old-or-new bytes around every replacement.
2. Use two actual producer processes with identical inputs and shared final
   names. IPC barriers must force both into header and C buffered writes
   concurrently; do not rely on sleeps or a lucky scheduling loop. Require
   distinct same-directory staged names, successful producers, serial-reference
   bytes and no remaining owned staging. Names are recorded at real staging
   opens, not recovered from Linux procfs; the test runs with procfs lookup
   unavailable.
3. Check UTF-8/CP932 serial byte equivalence, unchanged regeneration,
   permissions/umask, output symlinks, spaces, and the actual standalone CLI
   from another directory. Compile the small generated C fixture to an object.
4. Inject parsing, rendering, staging, writing, closing and replacement errors.
   Pre-publication failures retain both prior outputs. A later replacement
   failure preserves complete already-published or competing-writer outputs;
   no cross-file rollback is expected. Cleanup failures are explicit and never
   justify deleting another invocation's staging. For #374, exercise unknown
   and malformed named controls in direct and included inputs under both
   encodings; the actual CLI must reject them with source and message ID while
   preserving the previous pair. Line and multiline block comments must not
   conceal later controls or create message directives. Decode generated CP932
   with the independent decoder and check literal characters plus controls.
   The old producer silently published unknown controls, including after a
   line comment; dropping CP932 literals must fail the decode assertion.
   Also reject missing or duplicate message IDs and duplicate definitions
   across direct and included inputs. Independently decode the generated table
   after reordered declarations and verify each message remains at its ID.
   Decode odd-length UTF-8 literals, named and split UTF-8 glyphs, CP932
   trail bytes, both face-operand byte positions and zero extended-control
   operands; each representable stream must retain its bytes and final
   terminator.
   Missing, embedded, control-consumed and unencodable terminators reject
   without publication. Before the fix, the independent decoder read beyond
   compressed odd-length messages and malformed inputs were published. A
   catalog of empty messages must decode correctly instead of emitting zero-byte
   compressed arrays. The real Make regression uses isolated outputs and
   consumers to check producer/Huffman changes, content-stable no-ops,
   changed inputs, `-W` and `-q`, missing outputs/stamps and failed generation
   for both lanes.
5. The same regression command also runs a deliberately direct-to-final
   buffered writer using the current serial renderers. Its ordinary buffered
   line writes must expose partial header/C bytes and real compiler-reader
   failures. The C reader uses the public message/Huffman globals, rejecting
   a valid prefix that omits those definitions. Complete final files must
   then parse and match the serial bytes.
   Compiler status, incomplete byte count and final restoration establish
   the negative result; GCC-specific diagnostic wording is not required.
   This automates the old publication behavior without committing an old
   source snapshot or depending on the renderers' write-call grouping.
   The exact preimage producer may also be run in a disposable copy; its
   recorded failures remain distinct from the fixed publisher's positives.

**Expected result:** all readers see complete per-file versions; generated
bytes and IDs match the serial reference, and both concurrent same-input
producers succeed. The former direct-to-final writer is the negative control.

**Dependencies/conflicts:** existing text parsing/Huffman/Make interfaces and
host compiler only. There is no gameplay, save, ABI, locale catalog, item-cap
or profile-identity change and no new feature flag. Independent ownership work
in #180 consumes the fix after integration; it is not part of this change.
No manual criterion or broad ROM/archival build is required. Roll back by a
normal fix-forward/revert rather than serializing builds or skipping this gate.
