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
No parser, message ID, Huffman table, encoding or generated-byte format is
changed.

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
rediscover unrelated text suites. The upstream mirror retains all prior 30
gates and adds this gate, for **31 gates and the same eight jobs**. Job
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
   bytes and no remaining owned staging.
3. Check UTF-8/CP932 serial byte equivalence, unchanged regeneration,
   permissions/umask, output symlinks, spaces, and the actual standalone CLI
   from another directory. Compile the small generated C fixture to an object.
4. Inject parsing, rendering, staging, writing, closing and replacement errors.
   Pre-publication failures retain both prior outputs. A later replacement
   failure preserves complete already-published or competing-writer outputs;
   no cross-file rollback is expected. Cleanup failures are explicit and never
   justify deleting another invocation's staging.
5. The same regression command also runs a deliberately direct-to-final
   buffered writer using the current serial renderers. Its ordinary buffered
   line writes must expose partial header/C bytes and real compiler-reader
   failures. The C reader uses the public message/Huffman globals, rejecting
   a valid prefix that omits those definitions. Complete final files must
   then parse and match the serial bytes.
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
