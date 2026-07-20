#!/usr/bin/env python3
"""Host-side expansion save-format inspection/validation/migration tool
(issue #2 slice 1).

This is a byte-exact Python mirror of the raw-byte compatibility classifier
implemented in C (include/save_format.h's enum SaveCompatState, and its
real logic in src/bmsave-lib.c's ClassifySaveCompatRaw()/
ClassifySramSaveCompat()). Any change to the classifier's precedence order
or field layout MUST be applied to both places -- see docs/save_format.md's
"Known infrastructure gaps" section, which explains why this mirror exists
instead of executing the shipped GBA C code directly (no emulator/host
execution path is available for that code in this repository).

Deliberately dependency-free (Python stdlib only), matching this
repository's existing scripts/modernize/*.py tools.

Commands
--------

  inspect <path>            Print the classification and every diagnostic
                             field of a raw 0x8000-byte SRAM image. Never
                             fails on classification itself, only on I/O.

  validate <path> [--expect STATE ...]
                             Exit non-zero if the image's classification is
                             not one of the given --expect states (default:
                             SAVE_COMPAT_CURRENT).

  migrate <source> <dest>   Out-of-place migration only. Refuses
                             source == destination -- by resolved canonical
                             path (catches symlink and relative-path
                             aliases) and, separately, by device+inode
                             identity via os.path.samefile() (catches hard
                             link aliases, which are a distinct path that
                             resolve() alone cannot detect) -- regardless
                             of --force in both cases, since --force only
                             governs overwriting an unrelated existing
                             destination, never migrating a source onto
                             itself. Refuses an existing (unrelated)
                             destination unless --force is given. Reads the
                             full source image into host memory, classifies
                             it, and only migrates a
                             SAVE_COMPAT_VALID_LEGACY_OR_VANILLA or already-
                             SAVE_COMPAT_CURRENT source (the only transition
                             this slice supports is v0 -> v1: no metadata
                             record at all -> a current, checksummed one).
                             Every other source classification is a
                             precondition failure: neither the source nor any
                             destination file is touched. Before publishing,
                             the in-memory migrated image is re-classified
                             and must come back SAVE_COMPAT_CURRENT, or the
                             migration is aborted with nothing written. The
                             destination is published atomically: the full
                             image is written to a temporary file in the
                             destination's directory, flushed and fsync'd,
                             then re-read and re-verified byte-for-byte and
                             by classification. Publication itself is then
                             OS-enforced, not merely precondition-checked:
                             without --force, the temp file is published via
                             os.link(temp, dest), an atomic fail-if-exists
                             operation, so even a destination created by a
                             concurrent writer strictly after the early
                             existence precheck above still causes the
                             publish itself to fail (exit code 7) rather
                             than clobbering it; with --force, the temp file
                             is published via os.replace(temp, dest), which
                             unconditionally and atomically overwrites any
                             existing destination. Either way, the temporary
                             file is always removed afterwards (whether
                             publish succeeded, lost the no-force race, or
                             failed outright), destination is never opened
                             for writing directly, and any I/O failure --
                             including tempfile.mkstemp() itself failing --
                             is handled deterministically rather than
                             propagating an uncaught exception; the
                             published destination is re-read and
                             re-verified once more after a successful
                             publish.

Exit codes
----------

  0   success
  1   I/O error (file not found, unreadable, unwritable, wrong size,
      temp-file creation/write/publish failure, ...)
  2   argparse usage error (reserved by Python's argparse; not raised here)
  3   validate: classification not in the accepted --expect set
  4   migrate: source classification is not migratable
  5   migrate: post-migration re-verification did not come back CURRENT
  6   migrate: source and destination are the same file (refused),
      either by resolved canonical path (symlink/relative-path alias)
      or by device+inode identity (hard-link alias); enforced
      regardless of --force
  7   migrate: destination already exists (refused; pass --force to
      overwrite). Raised either by the early precondition check or by the
      OS-enforced os.link() publish itself losing a race against a
      concurrently created destination -- either way the destination is
      left completely untouched.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import expansion_config as ec  # noqa: E402  (see sys.path tweak above)


# --- Constants mirroring include/agb_sram.h, include/bmsave.h, and
#     include/save_format.h byte-for-byte ------------------------------------

SRAM_SIZE = 0x8000

HEADER_OFFSET = 0x0000
HEADER_SIZE = 0x64  # sizeof(struct GlobalSaveInfo)
HEADER_CHECKSUM_DOMAIN = 0x50  # GLOBALSIZEINFO_SIZE_FOR_CHECKSUM
HEADER_NAME_MARKER = b"AGB-FE9\x00"  # sSaveMarker, NUL-padded to char[8]
SAVEMAGIC32 = 0x40624
SAVEMAGIC16 = 0x200A

META_OFFSET = 0x73A4  # SRAM_OFFSET_EXPANSION_SAVE_META
META_SIZE = 0x5C  # SRAM_SIZE_EXPANSION_SAVE_META / sizeof(struct ExpansionSaveMeta)
META_CHECKSUM_DOMAIN = 0x2E  # EXPANSION_SAVE_META_SIZE_FOR_CHECKSUM
META_MAGIC = b"FSAV"  # EXPANSION_SAVE_META_MAGIC

SAVE_FORMAT_VERSION_CURRENT = 1

# struct.Struct layout for struct ExpansionSaveMeta. '<' = no alignment/
# padding, matching the C side's explicit STRUCT_PAD()-filled layout.
_META_STRUCT = struct.Struct("<4sBxHB3xI17s3x9sxH44s")
assert _META_STRUCT.size == META_SIZE, _META_STRUCT.size

SAVE_ABI_ID_APCS_GNU = 0
SAVE_ABI_ID_AAPCS = 1


# --- SaveCompatState (mirrors include/save_format.h's enum SaveCompatState) --

SAVE_COMPAT_EMPTY = "SAVE_COMPAT_EMPTY"
SAVE_COMPAT_VALID_LEGACY_OR_VANILLA = "SAVE_COMPAT_VALID_LEGACY_OR_VANILLA"
SAVE_COMPAT_HEADER_CORRUPT = "SAVE_COMPAT_HEADER_CORRUPT"
SAVE_COMPAT_METADATA_CORRUPT = "SAVE_COMPAT_METADATA_CORRUPT"
SAVE_COMPAT_CURRENT = "SAVE_COMPAT_CURRENT"
SAVE_COMPAT_MIGRATABLE_OLDER = "SAVE_COMPAT_MIGRATABLE_OLDER"
SAVE_COMPAT_NEWER_UNSUPPORTED = "SAVE_COMPAT_NEWER_UNSUPPORTED"
SAVE_COMPAT_SAVE_CONFIG_INCOMPATIBLE = "SAVE_COMPAT_SAVE_CONFIG_INCOMPATIBLE"

ALL_SAVE_COMPAT_STATES = (
    SAVE_COMPAT_EMPTY,
    SAVE_COMPAT_VALID_LEGACY_OR_VANILLA,
    SAVE_COMPAT_HEADER_CORRUPT,
    SAVE_COMPAT_METADATA_CORRUPT,
    SAVE_COMPAT_CURRENT,
    SAVE_COMPAT_MIGRATABLE_OLDER,
    SAVE_COMPAT_NEWER_UNSUPPORTED,
    SAVE_COMPAT_SAVE_CONFIG_INCOMPATIBLE,
)

# Migratable in this slice: only the v0 (no metadata at all) -> v1 (current)
# transition. SAVE_COMPAT_CURRENT is accepted too (a no-op re-migration).
MIGRATABLE_SOURCE_STATES = (SAVE_COMPAT_VALID_LEGACY_OR_VANILLA, SAVE_COMPAT_CURRENT)


class SaveFormatError(Exception):
    """Raised for I/O-level failures (bad size, unreadable/unwritable path)."""


# --- Checksum16 mirror (src/bmsave-lib.c) ------------------------------------


def checksum16(data: bytes) -> int:
    """Byte-exact mirror of Checksum16() (src/bmsave-lib.c): iterates
    little-endian u16 words, accumulating both a wrapping u32 sum and a u16
    xor, then returns the low 16 bits of their sum."""
    add_acc = 0
    xor_acc = 0
    word_count = len(data) // 2
    for i in range(word_count):
        word = data[2 * i] | (data[2 * i + 1] << 8)
        add_acc = (add_acc + word) & 0xFFFFFFFF
        xor_acc ^= word
    return (add_acc + xor_acc) & 0xFFFF


def is_region_blank(data: bytes) -> bool:
    """Mirrors IsRegionBlank() (src/bmsave-lib.c): true iff every byte is
    0xFF, matching WipeSram()'s 0xFFFFFFFF fill pattern."""
    return all(byte == 0xFF for byte in data)


# --- Metadata record (struct ExpansionSaveMeta mirror) -----------------------


@dataclass
class ExpansionSaveMeta:
    magic: bytes
    format_version: int
    compat_epoch: int
    abi_id: int
    framework_version_packed: int
    config_fingerprint: bytes
    build_commit_short: bytes
    checksum: int
    reserved: bytes

    @classmethod
    def unpack(cls, raw: bytes) -> "ExpansionSaveMeta":
        if len(raw) != META_SIZE:
            raise SaveFormatError(
                f"metadata region must be exactly {META_SIZE} bytes, got {len(raw)}"
            )
        (magic, format_version, compat_epoch, abi_id, framework_version_packed,
         config_fingerprint, build_commit_short, checksum, reserved) = _META_STRUCT.unpack(raw)
        return cls(
            magic=magic,
            format_version=format_version,
            compat_epoch=compat_epoch,
            abi_id=abi_id,
            framework_version_packed=framework_version_packed,
            config_fingerprint=config_fingerprint,
            build_commit_short=build_commit_short,
            checksum=checksum,
            reserved=reserved,
        )

    def pack(self) -> bytes:
        return _META_STRUCT.pack(
            self.magic,
            self.format_version,
            self.compat_epoch,
            self.abi_id,
            self.framework_version_packed,
            self.config_fingerprint,
            self.build_commit_short,
            self.checksum,
            self.reserved,
        )

    def computed_checksum(self) -> int:
        """Mirrors ExpansionSaveMetaChecksum(): Checksum16 over bytes
        [0, META_CHECKSUM_DOMAIN) of the packed record."""
        return checksum16(self.pack()[:META_CHECKSUM_DOMAIN])

    def config_fingerprint_str(self) -> str:
        return self.config_fingerprint.split(b"\x00", 1)[0].decode("ascii", "replace")

    def build_commit_short_str(self) -> str:
        return self.build_commit_short.split(b"\x00", 1)[0].decode("ascii", "replace")


def build_current_expansion_save_meta(repo_root: Path) -> ExpansionSaveMeta:
    """Host-side mirror of BuildCurrentExpansionSaveMeta() (src/bmsave-lib.c).

    Reads config.mk's real EXPANSION_SAVE_COMPAT_EPOCH/EXPANSION_VERSION_*
    (via expansion_config.py, the same #8 parser used to generate the C
    build's own -D flags) so a bump to either value is honored by this tool
    without a code change. abiId/configFingerprint/buildCommitShort are
    diagnostic-only fields (see docs/save_format.md) with no live build
    context available to a host-side tool, so they use documented,
    honestly-labeled placeholders that can never affect compatibility.
    """
    config_mk_path = repo_root / "config.mk"
    values = ec.parse_config_mk(config_mk_path)

    version_major = int(values["EXPANSION_VERSION_MAJOR"])
    version_minor = int(values["EXPANSION_VERSION_MINOR"])
    version_patch = int(values["EXPANSION_VERSION_PATCH"])
    save_compat_epoch = ec.validate_save_compat_epoch(values["EXPANSION_SAVE_COMPAT_EPOCH"])

    framework_version_packed = ec.compute_version_packed(version_major, version_minor, version_patch)
    build_commit = ec.resolve_build_commit(None, repo_root)

    meta = ExpansionSaveMeta(
        magic=META_MAGIC,
        format_version=SAVE_FORMAT_VERSION_CURRENT,
        compat_epoch=save_compat_epoch,
        abi_id=SAVE_ABI_ID_APCS_GNU,  # diagnostic-only; host tool has no ABI context
        framework_version_packed=framework_version_packed,
        config_fingerprint=b"0000000000000000\x00",  # diagnostic-only placeholder
        build_commit_short=(build_commit[:8].encode("ascii") + b"\x00" * 9)[:9],
        checksum=0,
        reserved=b"\x00" * (META_SIZE - META_CHECKSUM_DOMAIN - 2),
    )
    meta.checksum = meta.computed_checksum()
    return meta


# --- Classifier (mirrors ClassifySaveCompatRaw()/ClassifySramSaveCompat()) ---


def _header_valid(header_bytes: bytes) -> bool:
    name = header_bytes[0:8]
    magic32 = int.from_bytes(header_bytes[8:12], "little")
    magic16 = int.from_bytes(header_bytes[12:14], "little")
    checksum = int.from_bytes(header_bytes[0x60:0x62], "little")
    return (
        name == HEADER_NAME_MARKER
        and magic32 == SAVEMAGIC32
        and magic16 == SAVEMAGIC16
        and checksum == checksum16(header_bytes[:HEADER_CHECKSUM_DOMAIN])
    )


def classify_save_compat_raw(
    header_bytes: bytes,
    meta_bytes: bytes,
    save_compat_epoch: int,
) -> str:
    """Pure classifier: byte-exact mirror of ClassifySaveCompatRaw()
    (src/bmsave-lib.c). Never touches a file; takes already-read regions."""
    if len(header_bytes) != HEADER_SIZE:
        raise SaveFormatError(f"header region must be exactly {HEADER_SIZE} bytes")
    if len(meta_bytes) != META_SIZE:
        raise SaveFormatError(f"metadata region must be exactly {META_SIZE} bytes")

    header_blank = is_region_blank(header_bytes)
    meta_blank = is_region_blank(meta_bytes)

    if header_blank and meta_blank:
        return SAVE_COMPAT_EMPTY

    if not _header_valid(header_bytes):
        return SAVE_COMPAT_HEADER_CORRUPT

    meta = ExpansionSaveMeta.unpack(meta_bytes)

    if meta.magic != META_MAGIC:
        return SAVE_COMPAT_VALID_LEGACY_OR_VANILLA

    if meta.checksum != meta.computed_checksum():
        return SAVE_COMPAT_METADATA_CORRUPT

    if meta.format_version > SAVE_FORMAT_VERSION_CURRENT:
        return SAVE_COMPAT_NEWER_UNSUPPORTED

    if meta.format_version < SAVE_FORMAT_VERSION_CURRENT:
        return SAVE_COMPAT_MIGRATABLE_OLDER

    if meta.compat_epoch != save_compat_epoch:
        return SAVE_COMPAT_SAVE_CONFIG_INCOMPATIBLE

    return SAVE_COMPAT_CURRENT


def classify_image(image: bytes, save_compat_epoch: int) -> str:
    if len(image) != SRAM_SIZE:
        raise SaveFormatError(f"SRAM image must be exactly {SRAM_SIZE} bytes, got {len(image)}")
    header_bytes = image[HEADER_OFFSET:HEADER_OFFSET + HEADER_SIZE]
    meta_bytes = image[META_OFFSET:META_OFFSET + META_SIZE]
    return classify_save_compat_raw(header_bytes, meta_bytes, save_compat_epoch)


# --- File I/O helpers ---------------------------------------------------------


def read_image(path: Path) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SaveFormatError(f"failed to read {path}: {error}") from error
    if len(data) != SRAM_SIZE:
        raise SaveFormatError(
            f"{path}: expected exactly {SRAM_SIZE} (0x{SRAM_SIZE:X}) bytes, got {len(data)}"
        )
    return data


def resolve_save_compat_epoch(repo_root: Path) -> int:
    values = ec.parse_config_mk(repo_root / "config.mk")
    return ec.validate_save_compat_epoch(values["EXPANSION_SAVE_COMPAT_EPOCH"])


# --- CLI commands --------------------------------------------------------------


def cmd_inspect(args: argparse.Namespace) -> int:
    repo_root = args.repo_root
    try:
        save_compat_epoch = resolve_save_compat_epoch(repo_root)
        image = read_image(args.path)
    except (SaveFormatError, ec.ConfigError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    header_bytes = image[HEADER_OFFSET:HEADER_OFFSET + HEADER_SIZE]
    meta_bytes = image[META_OFFSET:META_OFFSET + META_SIZE]
    state = classify_save_compat_raw(header_bytes, meta_bytes, save_compat_epoch)

    print(f"path: {args.path}")
    print(f"classification: {state}")
    print(f"header_blank: {is_region_blank(header_bytes)}")
    print(f"meta_blank: {is_region_blank(meta_bytes)}")

    if state not in (SAVE_COMPAT_EMPTY, SAVE_COMPAT_HEADER_CORRUPT):
        meta = ExpansionSaveMeta.unpack(meta_bytes)
        if meta.magic == META_MAGIC:
            print(f"meta.formatVersion: {meta.format_version}")
            print(f"meta.compatEpoch: {meta.compat_epoch} (expected {save_compat_epoch})")
            print(f"meta.abiId: {meta.abi_id}")
            print(f"meta.frameworkVersionPacked: 0x{meta.framework_version_packed:06X}")
            print(f"meta.configFingerprint: {meta.config_fingerprint_str()!r} (diagnostic only)")
            print(f"meta.buildCommitShort: {meta.build_commit_short_str()!r} (diagnostic only)")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    repo_root = args.repo_root
    expected = tuple(args.expect) if args.expect else (SAVE_COMPAT_CURRENT,)
    try:
        save_compat_epoch = resolve_save_compat_epoch(repo_root)
        image = read_image(args.path)
        state = classify_image(image, save_compat_epoch)
    except (SaveFormatError, ec.ConfigError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if state not in expected:
        print(
            f"error: {args.path} classified as {state}, expected one of {', '.join(expected)}",
            file=sys.stderr,
        )
        return 3

    print(f"{args.path}: {state} (accepted)")
    return 0


def _publish_atomically(
    dest: Path, payload: bytes, save_compat_epoch: int, force: bool
) -> Optional[Tuple[int, str]]:
    """Writes `payload` to `dest` atomically and non-destructively.

    Writes to a temporary file in dest's own directory (so the publish
    step below is on the same filesystem and therefore atomic), flushes
    and fsyncs it, re-reads and re-verifies it byte-for-byte and by
    classification, then publishes it onto `dest`:

    * Without `force`: via os.link(tmp, dest), an OS-enforced, atomic
      fail-if-exists operation. If `dest` is created by anyone else at
      any point up to and including this exact syscall -- including
      strictly *after* an earlier Python-level dest.exists() precheck
      already observed it as absent -- os.link() itself raises
      FileExistsError and `dest` (whatever the concurrent writer put
      there) is left completely untouched; only this function's own
      temporary file is ever removed afterwards. This is deliberately
      not an exists()-then-write() sequence (which has a
      time-of-check-to-time-of-use race window); the no-clobber
      guarantee lives at the actual publish syscall, not merely in an
      earlier precondition check.
    * With `force`: via os.replace(tmp, dest), which atomically
      overwrites any existing destination unconditionally.

    The temporary file is always removed on every path (rename success,
    hard-link success, lost EEXIST race, or any other failure), and
    `dest` is never opened for writing directly -- so an interrupted,
    failed, or raced publish can never leave a truncated/partial
    destination.

    Returns None on success, or (exit_code, message) on failure: exit
    code 7 specifically for a lost no-force race against a concurrently
    created destination, exit code 1 for any other I/O failure (this
    includes tempfile.mkstemp() itself failing, which is handled here
    rather than left to propagate as an uncaught exception).
    """
    dest_dir = dest.parent
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return 1, f"failed to prepare destination directory {dest_dir}: {error}"

    try:
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{dest.name}.", suffix=".tmp", dir=str(dest_dir)
        )
    except OSError as error:
        return 1, f"failed to create temporary migration output in {dest_dir}: {error}"

    tmp_path = Path(tmp_name)
    try:
        try:
            with os.fdopen(tmp_fd, "wb") as tmp_file:
                tmp_file.write(payload)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
        except OSError as error:
            return 1, f"failed to write temporary migration output {tmp_path}: {error}"

        try:
            written = tmp_path.read_bytes()
        except OSError as error:
            return 1, f"failed to re-read temporary migration output {tmp_path}: {error}"
        if written != payload or classify_image(written, save_compat_epoch) != SAVE_COMPAT_CURRENT:
            return 1, f"temporary migration output {tmp_path} failed pre-publish verification"

        if force:
            try:
                os.replace(str(tmp_path), str(dest))
            except OSError as error:
                return 1, f"failed to publish {dest}: {error}"
        else:
            try:
                os.link(str(tmp_path), str(dest))
            except FileExistsError:
                return (
                    7,
                    f"destination {dest} already exists; refusing to overwrite "
                    "without --force (source left untouched, nothing written)",
                )
            except OSError as error:
                return 1, f"failed to publish {dest}: {error}"
    finally:
        # On the --force path, a successful os.replace() already moved
        # the temp file onto `dest`. On the default path, a successful
        # os.link() leaves the temp file behind as a second, now-
        # redundant hard link to the same data (dest is the first).
        # Either way -- success, a lost EEXIST race, or any other
        # failure -- always explicitly remove this function's own temp
        # path so it is never left behind.
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    # Defense in depth: re-read/re-validate what actually landed at the
    # published destination path, not just the temp file we wrote.
    try:
        published = dest.read_bytes()
    except OSError as error:
        return 1, f"failed to re-read published {dest}: {error}"
    if published != payload or classify_image(published, save_compat_epoch) != SAVE_COMPAT_CURRENT:
        return 1, f"published {dest} failed post-publish verification"

    return None


def cmd_migrate(args: argparse.Namespace) -> int:
    repo_root = args.repo_root
    source: Path = args.source
    dest: Path = args.dest

    try:
        source_resolved = source.resolve()
        # dest may not exist yet; resolve() still normalizes the existing
        # parent directory portion so alias spellings (e.g. "./same.bin")
        # are still correctly detected below.
        dest_resolved = dest.resolve()
    except OSError as error:
        print(f"error: failed to resolve path: {error}", file=sys.stderr)
        return 1

    if source_resolved == dest_resolved:
        print("error: refusing to migrate: source and destination are the same file", file=sys.stderr)
        return 6

    # source_resolved == dest_resolved (above) only catches aliases that
    # collapse to the same canonical *path* (symlinks, relative-path
    # spellings, "."/".." segments, etc. -- resolve() follows symlinks and
    # normalizes the string). A hard link is a second, distinct path that
    # is nonetheless the very same file (same device+inode): resolve()
    # cannot detect that, since there is no symlink to follow and the two
    # paths are textually different. Detect that case explicitly via
    # inode identity so a hard-linked alias of the source is refused with
    # the same same-file exit code 6 -- and, per requirement, regardless
    # of --force, since --force only governs "overwrite an unrelated
    # existing destination", never "treat the source itself as its own
    # migration target".
    if dest_resolved.exists():
        try:
            if os.path.samefile(str(source_resolved), str(dest_resolved)):
                print(
                    "error: refusing to migrate: source and destination are "
                    "the same file (hard-linked alias)",
                    file=sys.stderr,
                )
                return 6
        except OSError:
            # Either path vanished between resolve() and here, or some
            # other stat failure -- not an aliasing determination either
            # way. Fall through; the ordinary source-read/dest-exists
            # handling below will report the real underlying error.
            pass

    # Fast-path precondition check only: gives an immediate, cheap
    # diagnostic for the overwhelmingly common non-racing case (no need
    # to read/classify/rebuild the whole image just to report a
    # destination that plainly already exists). This is NOT where the
    # no-clobber guarantee actually lives -- see _publish_atomically()'s
    # os.link()-based publish below, which is the real, OS-enforced,
    # race-proof authority; a destination created after this check
    # (e.g. by a concurrent process) is caught there instead, since
    # dest.exists() here can never rule that out.
    if dest_resolved.exists() and not args.force:
        print(
            f"error: destination {dest} already exists; refusing to overwrite "
            "without --force (source left untouched, nothing written)",
            file=sys.stderr,
        )
        return 7

    try:
        save_compat_epoch = resolve_save_compat_epoch(repo_root)
        image = bytearray(read_image(source))
    except (SaveFormatError, ec.ConfigError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    source_state = classify_image(bytes(image), save_compat_epoch)
    if source_state not in MIGRATABLE_SOURCE_STATES:
        print(
            f"error: {source} classified as {source_state}, which is not migratable "
            f"in this slice (only {' or '.join(MIGRATABLE_SOURCE_STATES)} are); "
            "source left untouched, nothing written",
            file=sys.stderr,
        )
        return 4

    # Out-of-place, host-memory-only transform: stamp/refresh the current
    # metadata record. The source bytearray is never written back to
    # `source` at any point -- only ever to `dest`, and only after
    # re-verification below.
    new_meta = build_current_expansion_save_meta(repo_root)
    image[META_OFFSET:META_OFFSET + META_SIZE] = new_meta.pack()

    post_state = classify_image(bytes(image), save_compat_epoch)
    if post_state != SAVE_COMPAT_CURRENT:
        print(
            f"error: post-migration verification failed (got {post_state}, expected "
            f"{SAVE_COMPAT_CURRENT}); nothing written to {dest}",
            file=sys.stderr,
        )
        return 5

    publish_result = _publish_atomically(
        dest_resolved, bytes(image), save_compat_epoch, args.force
    )
    if publish_result is not None:
        exit_code, message = publish_result
        print(f"error: {message}", file=sys.stderr)
        return exit_code

    print(f"migrated {source} ({source_state}) -> {dest} (SAVE_COMPAT_CURRENT)")
    return 0


# --- argparse wiring ------------------------------------------------------------


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_default_repo_root(),
        help="repository root containing config.mk (default: auto-detected)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="classify and print an SRAM image")
    inspect_parser.add_argument("path", type=Path)
    inspect_parser.set_defaults(func=cmd_inspect)

    validate_parser = subparsers.add_parser("validate", help="fail if classification is unexpected")
    validate_parser.add_argument("path", type=Path)
    validate_parser.add_argument(
        "--expect",
        action="append",
        choices=ALL_SAVE_COMPAT_STATES,
        help="acceptable classification (repeatable; default: SAVE_COMPAT_CURRENT)",
    )
    validate_parser.set_defaults(func=cmd_validate)

    migrate_parser = subparsers.add_parser("migrate", help="out-of-place v0 -> v1 migration")
    migrate_parser.add_argument("source", type=Path)
    migrate_parser.add_argument("dest", type=Path)
    migrate_parser.add_argument(
        "--force",
        action="store_true",
        help="allow overwriting an existing destination (default: refuse)",
    )
    migrate_parser.set_defaults(func=cmd_migrate)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
