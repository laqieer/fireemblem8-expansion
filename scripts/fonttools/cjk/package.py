"""Validate FEBuilderGBA schema-v1 packages and import compact ROM assets."""

from __future__ import annotations

import binascii
import csv
import hashlib
import io
import json
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .inventory import (
    CjkFontError,
    LOCALES,
    STYLES,
    json_bytes,
    scalar_text,
    sha256_bytes,
)

PACKAGE_ARCHIVE = "build/tmp/cjk-fonts/febuilder-schema-v1.zip"
GENERATION_REPORT = "fonts/cjk/reports/febuilder-generation-report.json"
GATE_REPORT = "fonts/cjk/reports/febuilder-gates.json"
FEHRR_SOURCES = "fonts/cjk/fehrr-sources.json"
FEHBUILDER_BASELINE_MANIFEST = "fonts/cjk/febuilder-baseline-manifest.json"
FEHBUILDER_BASELINE_ASSET_ROOT = "fonts/cjk/febuilder-baseline"
FEHBUILDER_BASELINE_ASSET_MANIFEST = (
    "fonts/cjk/febuilder-baseline/manifest.json"
)
ASSET_ROOT = "graphics/fonts/cjk"
COMPACT_ASSET_SUFFIXES = {
    "codepoints": ".codepoints.u32le",
    "widths": ".widths.u8",
    "bitmap": ".glyphs.2bpp",
}
FORBIDDEN_COMPACT_ASSET_EXTENSION = ".bin"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SLOTS_HEADER = (
    "moji\tunicode\tstyle\twidth\tfilename\tpackedSha256\tpngSha256"
)
FEHRR_REPOSITORY = "https://github.com/laqieer/FEHRR.git"
FEHRR_LOCALES = {
    "ja": "fe8j",
    "zh-Hans": "fe8cn",
}
FEHRR_SOURCE_STYLES = {
    "system": "item",
    "talk": "text",
}
FEHRR_CROSS_STYLE = {
    "item": "text",
    "text": "item",
}
FEHRR_PRIORITY_POLICY = (
    "same-game same-style FEHRR glyph first; same-game cross-style FEHRR "
    "glyph second; pinned FEHRR supplemental style tier third; verified "
    "FEBuilder baseline fallback last"
)
FEHRR_SUPPLEMENTAL_TIERS = {
    "ja": {
        "system": (
            ("common-japanese", "glyph/Microsoft Sans Serif/常用日语汉字"),
            ("fe6j", "glyph/fe6j"),
            ("fe7j", "glyph/fe7j"),
            ("fe8u", "glyph/fe8u"),
            ("missing-glyph", "glyph/Microsoft Sans Serif/缺字增补"),
        ),
        "talk": (
            ("common-japanese", "glyph/Microsoft Sans Serif/常用日语汉字"),
            ("fe6j", "glyph/fe6j"),
            ("fe7j", "glyph/fe7j"),
            ("fe8u", "glyph/fe8u"),
            ("missing-glyph", "glyph/Microsoft Sans Serif/缺字增补"),
        ),
    },
    "zh-Hans": {
        "system": (
            ("punctuation", "glyph/标点符号/道具标点"),
            ("gba-punctuation", "glyph/GBA火纹中文字库/道具标点"),
            ("gba-tier1", "glyph/GBA火纹中文字库/一级道具字体"),
            ("gba-tier2", "glyph/GBA火纹中文字库/二级道具字体"),
            ("fe6cn", "glyph/fe6cn"),
            ("fe7cn", "glyph/fe7cn"),
            ("fe8j", "glyph/fe8j"),
            ("fe8u", "glyph/fe8u"),
            ("missing-glyph", "glyph/Microsoft Sans Serif/缺字增补"),
        ),
        "talk": (
            ("punctuation", "glyph/标点符号/对话标点"),
            ("gba-punctuation", "glyph/GBA火纹中文字库/对话标点"),
            ("gba-tier1", "glyph/GBA火纹中文字库/一级对话字体"),
            ("gba-tier2", "glyph/GBA火纹中文字库/二级对话字体"),
            ("fe6cn", "glyph/fe6cn"),
            ("fe7cn", "glyph/fe7cn"),
            ("fe8j", "glyph/fe8j"),
            ("fe8u", "glyph/fe8u"),
            ("missing-glyph", "glyph/Microsoft Sans Serif/缺字增补"),
        ),
    },
}


def compact_asset_filenames(prefix: str) -> Dict[str, str]:
    return {
        kind: f"{prefix}{suffix}"
        for kind, suffix in COMPACT_ASSET_SUFFIXES.items()
    }


def _reject_generic_compact_assets(root: Path) -> None:
    asset_root = root / ASSET_ROOT
    generic_paths = sorted(
        path.relative_to(root).as_posix()
        for path in asset_root.iterdir()
        if path.is_file() and path.suffix == FORBIDDEN_COMPACT_ASSET_EXTENSION
    )
    if generic_paths:
        raise CjkFontError(
            "generic compact asset path(s) are forbidden: "
            + ", ".join(generic_paths)
        )


def _safe_member(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise CjkFontError(f"unsafe package member {name!r}")
    return path.as_posix()


class PackageReader:
    def __init__(self, path: Path):
        self.path = path
        self.archive = None
        if path.is_dir():
            self.kind = "directory"
        elif path.is_file() and zipfile.is_zipfile(path):
            self.kind = "zip"
            self.archive = zipfile.ZipFile(path, "r")
            seen = set()
            for info in self.archive.infolist():
                name = _safe_member(info.filename)
                if name in seen:
                    raise CjkFontError(f"duplicate ZIP member {name}")
                seen.add(name)
                if info.is_dir():
                    raise CjkFontError("package ZIP must not contain directory entries")
        else:
            raise CjkFontError(f"{path}: expected a package directory or ZIP archive")

    def close(self) -> None:
        if self.archive is not None:
            self.archive.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def names(self) -> Tuple[str, ...]:
        if self.kind == "zip":
            return tuple(sorted(info.filename for info in self.archive.infolist()))
        names = []
        for path in self.path.rglob("*"):
            if path.is_file():
                names.append(path.relative_to(self.path).as_posix())
        return tuple(sorted(names))

    def read(self, name: str) -> bytes:
        name = _safe_member(name)
        if self.kind == "zip":
            try:
                return self.archive.read(name)
            except KeyError as error:
                raise CjkFontError(f"package member is missing: {name}") from error
        path = self.path / name
        if not path.is_file():
            raise CjkFontError(f"package member is missing: {name}")
        return path.read_bytes()


def _archive_bytes(members: Iterable[Tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_STORED,
        strict_timestamps=True,
    ) as archive:
        for name, data in sorted(members):
            name = _safe_member(name)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, data)
    return buffer.getvalue()


def archive_package(package_dir: Path, output: Path) -> bytes:
    if not package_dir.is_dir():
        raise CjkFontError(f"{package_dir}: package directory is missing")
    members = (
        (path.relative_to(package_dir).as_posix(), path.read_bytes())
        for path in package_dir.rglob("*")
        if path.is_file()
    )
    data = _archive_bytes(members)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return data


def check_package_archive(package_path: Path) -> None:
    committed = package_path.read_bytes()
    with PackageReader(package_path) as package:
        rebuilt = _archive_bytes((name, package.read(name)) for name in package.names())
    if rebuilt != committed:
        raise CjkFontError("FEBuilder package ZIP is not canonical")


def _read_png_indices(png: bytes) -> bytes:
    if not png.startswith(PNG_SIGNATURE):
        raise CjkFontError("invalid PNG signature")
    position = len(PNG_SIGNATURE)
    chunks: List[Tuple[bytes, bytes]] = []
    while position < len(png):
        if position + 12 > len(png):
            raise CjkFontError("truncated PNG chunk")
        length = struct.unpack_from(">I", png, position)[0]
        chunk_type = png[position + 4 : position + 8]
        start = position + 8
        end = start + length
        if end + 4 > len(png):
            raise CjkFontError("PNG chunk exceeds file bounds")
        payload = png[start:end]
        expected_crc = struct.unpack_from(">I", png, end)[0]
        actual_crc = binascii.crc32(chunk_type + payload) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise CjkFontError(f"{chunk_type!r} PNG CRC mismatch")
        chunks.append((chunk_type, payload))
        position = end + 4
        if chunk_type == b"IEND":
            break
    if position != len(png):
        raise CjkFontError("PNG has trailing bytes")
    if [chunk_type for chunk_type, _ in chunks] != [
        b"IHDR",
        b"PLTE",
        b"tRNS",
        b"IDAT",
        b"IEND",
    ]:
        raise CjkFontError("PNG chunk order is not canonical")
    ihdr = chunks[0][1]
    if (
        len(ihdr) != 13
        or struct.unpack_from(">II", ihdr, 0) != (16, 16)
        or ihdr[8:] != bytes((2, 3, 0, 0, 0))
    ):
        raise CjkFontError("PNG is not canonical 16x16 indexed 2bpp")
    if len(chunks[1][1]) != 12:
        raise CjkFontError("PNG palette must contain exactly four RGB entries")
    if chunks[2][1] != bytes((0, 255, 255, 255)):
        raise CjkFontError("PNG transparency table is not canonical")
    try:
        raw = zlib.decompress(chunks[3][1])
    except zlib.error as error:
        raise CjkFontError(f"PNG zlib stream is invalid: {error}") from error
    if len(raw) != 80:
        raise CjkFontError("PNG decompressed scanline size is invalid")
    indices = bytearray()
    for row in range(16):
        scanline = raw[row * 5 : (row + 1) * 5]
        if scanline[0] != 0:
            raise CjkFontError("PNG must use filter None for every scanline")
        for packed in scanline[1:]:
            indices.extend(
                (
                    (packed >> 6) & 3,
                    (packed >> 4) & 3,
                    (packed >> 2) & 3,
                    packed & 3,
                )
            )
    return bytes(indices)


def _pack_engine_tile(indices: bytes) -> bytes:
    if len(indices) != 256 or any(value > 3 for value in indices):
        raise CjkFontError("glyph indices must contain 256 values in 0..3")
    packed = bytearray()
    for offset in range(0, 256, 4):
        packed.append(
            indices[offset]
            | (indices[offset + 1] << 2)
            | (indices[offset + 2] << 4)
            | (indices[offset + 3] << 6)
        )
    return bytes(packed)


def _read_fehrr_png_indices(png: bytes) -> bytes:
    if not png.startswith(PNG_SIGNATURE):
        raise CjkFontError("FEHRR glyph has an invalid PNG signature")
    position = len(PNG_SIGNATURE)
    chunks: List[Tuple[bytes, bytes]] = []
    saw_iend = False
    while position < len(png):
        if position + 12 > len(png):
            raise CjkFontError("FEHRR glyph has a truncated PNG chunk")
        length = struct.unpack_from(">I", png, position)[0]
        chunk_type = png[position + 4 : position + 8]
        start = position + 8
        end = start + length
        if end + 4 > len(png):
            raise CjkFontError("FEHRR glyph PNG chunk exceeds file bounds")
        payload = png[start:end]
        expected_crc = struct.unpack_from(">I", png, end)[0]
        actual_crc = binascii.crc32(chunk_type + payload) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise CjkFontError(f"FEHRR glyph {chunk_type!r} PNG CRC mismatch")
        chunks.append((chunk_type, payload))
        position = end + 4
        if chunk_type == b"IEND":
            saw_iend = True
            break
    if not saw_iend or position != len(png):
        raise CjkFontError("FEHRR glyph PNG is missing IEND or has trailing bytes")
    if not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise CjkFontError("FEHRR glyph PNG chunk order is invalid")
    ihdr = chunks[0][1]
    if len(ihdr) != 13:
        raise CjkFontError("FEHRR glyph IHDR is invalid")
    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if (
        width != 16
        or height != 16
        or depth != 8
        or color_type not in (2, 3)
        or compression != 0
        or filter_method != 0
        or interlace != 0
    ):
        raise CjkFontError("FEHRR glyph is not supported 16x16 PNG data")
    palette = [payload for chunk_type, payload in chunks if chunk_type == b"PLTE"]
    transparency = [payload for chunk_type, payload in chunks if chunk_type == b"tRNS"]
    idat = [payload for chunk_type, payload in chunks if chunk_type == b"IDAT"]
    if color_type == 3 and (
        len(palette) != 1
        or len(palette[0]) < 3
        or len(palette[0]) > 256 * 3
        or len(palette[0]) % 3
    ):
        raise CjkFontError("FEHRR glyph palette is invalid")
    if (color_type == 2 and palette) or transparency or not idat:
        raise CjkFontError("FEHRR glyph must not use transparency and needs image data")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as error:
        raise CjkFontError(f"FEHRR glyph PNG zlib stream is invalid: {error}") from error
    bytes_per_pixel = 3 if color_type == 2 else 1
    row_bytes = 16 * bytes_per_pixel
    if len(raw) != 16 * (row_bytes + 1):
        raise CjkFontError("FEHRR glyph decompressed scanline size is invalid")
    decoded_rows = []
    previous = bytearray(row_bytes)
    for row in range(16):
        scanline = raw[
            row * (row_bytes + 1) : (row + 1) * (row_bytes + 1)
        ]
        filter_type = scanline[0]
        current = bytearray(scanline[1:])
        if filter_type == 0:
            pass
        elif filter_type == 1:
            for index in range(row_bytes):
                left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                current[index] = (current[index] + left) & 0xFF
        elif filter_type == 2:
            for index in range(row_bytes):
                current[index] = (current[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(row_bytes):
                left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                current[index] = (current[index] + ((left + previous[index]) // 2)) & 0xFF
        elif filter_type == 4:
            for index in range(row_bytes):
                left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                up = previous[index]
                upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                predictor = left + up - upper_left
                left_distance = abs(predictor - left)
                up_distance = abs(predictor - up)
                upper_left_distance = abs(predictor - upper_left)
                if left_distance <= up_distance and left_distance <= upper_left_distance:
                    paeth = left
                elif up_distance <= upper_left_distance:
                    paeth = up
                else:
                    paeth = upper_left
                current[index] = (current[index] + paeth) & 0xFF
        else:
            raise CjkFontError("FEHRR glyph PNG uses an invalid filter")
        decoded_rows.append(bytes(current))
        previous = current
    if color_type == 3:
        indices = bytearray().join(decoded_rows)
    else:
        pixels = [
            tuple(row[index : index + 3])
            for row in decoded_rows
            for index in range(0, row_bytes, 3)
        ]
        frequencies = {pixel: pixels.count(pixel) for pixel in set(pixels)}
        background = min(
            frequencies,
            key=lambda pixel: (-frequencies[pixel], pixel),
        )
        foreground = sorted(
            (pixel for pixel in frequencies if pixel != background),
            key=lambda pixel: (sum(pixel), pixel),
        )
        palette_indices = {
            pixel: min(index + 1, 3) for index, pixel in enumerate(foreground)
        }
        indices = bytearray(
            0 if pixel == background else palette_indices[pixel] for pixel in pixels
        )
    if any(index > 3 for index in indices):
        raise CjkFontError("FEHRR glyph pixels must use palette entries 0 through 3")
    return bytes(indices)


def _source_tree_sha256(members: Iterable[Tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    seen = set()
    for name, data in sorted(members):
        name = _safe_member(name)
        if name in seen:
            raise CjkFontError(f"duplicate FEHRR source member {name}")
        seen.add(name)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(struct.pack(">Q", len(data)))
        digest.update(data)
    return digest.hexdigest()


def _run_git(source_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(source_root), *arguments),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise CjkFontError(f"{source_root}: git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.strip()


def _verify_fehrr_checkout(source_root: Path) -> Dict[str, str]:
    if not source_root.is_dir():
        raise CjkFontError(f"{source_root}: FEHRR source checkout is missing")
    commit = _run_git(source_root, "rev-parse", "HEAD")
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise CjkFontError(f"{source_root}: FEHRR HEAD is not a full commit SHA")
    if _run_git(source_root, "status", "--porcelain=v1"):
        raise CjkFontError(f"{source_root}: FEHRR checkout must be clean")
    origin = _run_git(source_root, "remote", "get-url", "origin")
    if origin not in (FEHRR_REPOSITORY, FEHRR_REPOSITORY.removesuffix(".git")):
        raise CjkFontError(f"{source_root}: FEHRR origin must be {FEHRR_REPOSITORY}")
    return {
        "repository": FEHRR_REPOSITORY,
        "commit": commit,
    }


def _parse_fehrr_font_map(
    source_root: Path,
    source_locale: str,
    relative_map: str | None = None,
) -> Tuple[str, bytes, Dict[str, Dict[int, Dict[str, object]]], List[Dict[str, object]]]:
    if relative_map is None:
        relative_map = f"glyph/{source_locale}/font.fontall.txt"
    map_path = source_root / relative_map
    try:
        map_data = map_path.read_bytes()
        text = map_data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CjkFontError(f"{map_path}: FEHRR font map is not UTF-8") from error
    records: Dict[str, Dict[int, Dict[str, object]]] = {"item": {}, "text": {}}
    duplicate_widths = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("//"):
            continue
        columns = line.split("\t")
        if len(columns) != 4:
            raise CjkFontError(f"{map_path}:{line_number}: expected four tab-separated fields")
        character, source_style, width_text, filename = columns
        if source_style not in records:
            raise CjkFontError(f"{map_path}:{line_number}: unsupported style {source_style!r}")
        if len(character) != 1:
            continue
        try:
            width = int(width_text, 10)
        except ValueError as error:
            raise CjkFontError(f"{map_path}:{line_number}: width is not decimal") from error
        if not 1 <= width <= 16:
            raise CjkFontError(f"{map_path}:{line_number}: width is outside 1..16")
        expected_prefix = "FontItem" if source_style == "item" else "FontText"
        if (
            Path(filename).name != filename
            or not filename.startswith(expected_prefix)
            or not filename.endswith(".png")
        ):
            raise CjkFontError(f"{map_path}:{line_number}: glyph filename is unsafe")
        scalar = ord(character)
        record = {
            "width": width,
            "filename": filename,
            "line": line_number,
        }
        previous = records[source_style].get(scalar)
        if previous is None:
            records[source_style][scalar] = record
        elif previous["width"] != width or previous["filename"] != filename:
            duplicate_widths.append(
                {
                    "source_locale": source_locale,
                    "source_style": source_style,
                    "scalar": scalar_text(scalar),
                    "selected_line": previous["line"],
                    "selected_width": previous["width"],
                    "ignored_line": line_number,
                    "ignored_width": width,
                    "filename": filename,
                }
            )
    return relative_map, map_data, records, duplicate_widths


def _collect_fehrr_sources(
    root: Path,
    source_root: Path,
    jobs: Mapping[str, Mapping[str, object]],
    corpora: Mapping[str, Tuple[Path, bytes, Tuple[int, ...]]],
) -> Tuple[Dict[str, object], Dict[str, Dict[int, Tuple[int, bytes]]]]:
    checkout = _verify_fehrr_checkout(source_root)
    source_maps = {}
    supplemental_maps = {}
    duplicate_widths = []
    for locale, source_locale in FEHRR_LOCALES.items():
        source_maps[locale] = _parse_fehrr_font_map(source_root, source_locale)
        duplicate_widths.extend(source_maps[locale][3])
        for runtime_style, tiers in FEHRR_SUPPLEMENTAL_TIERS[locale].items():
            loaded = []
            for tier_name, glyph_dir in tiers:
                relative_map = f"{glyph_dir}/font.fontall.txt"
                parsed = _parse_fehrr_font_map(
                    source_root,
                    f"{source_locale}:{tier_name}",
                    relative_map,
                )
                duplicate_widths.extend(parsed[3])
                loaded.append((tier_name, glyph_dir, parsed))
            supplemental_maps[(locale, runtime_style)] = tuple(loaded)

    assets: Dict[str, object] = {}
    glyph_data: Dict[str, Dict[int, Tuple[int, bytes]]] = {}
    tree_members: List[Tuple[str, bytes]] = []
    tree_member_names = set()
    for locale in LOCALES:
        relative_map, map_data, records, _ = source_maps[locale]
        if relative_map not in tree_member_names:
            tree_members.append((relative_map, map_data))
            tree_member_names.add(relative_map)
        for style in STYLES:
            job_id = f"{locale.lower()}-{style}".replace("-hans", "-hans")
            if job_id not in jobs:
                raise CjkFontError(f"FEBuilder manifest is missing {job_id}")
            _, _, corpus = corpora[job_id]
            source_style = FEHRR_SOURCE_STYLES[style]
            rows = []
            selected = {}
            fallback_rows = []
            selection_counts = {
                "same_game_same_style": 0,
                "same_game_cross_style": 0,
                "fehrr_supplemental": 0,
                "febuilder_fallback": 0,
            }
            supplemental_lock = []
            for tier_name, glyph_dir, (supplemental_map, supplemental_data, _, _) in supplemental_maps[
                (locale, style)
            ]:
                if supplemental_map not in tree_member_names:
                    tree_members.append((supplemental_map, supplemental_data))
                    tree_member_names.add(supplemental_map)
                supplemental_lock.append(
                    {
                        "tier": tier_name,
                        "glyph_directory": glyph_dir,
                        "path": supplemental_map,
                        "sha256": sha256_bytes(supplemental_data),
                    }
                )
            for scalar in corpus:
                source_record = records[source_style].get(scalar)
                selected_style = source_style
                selection_kind = "same_game_same_style"
                glyph_directory = f"glyph/{FEHRR_LOCALES[locale]}"
                selected_map = relative_map
                selected_tier = "same-game"
                if source_record is None:
                    selected_style = FEHRR_CROSS_STYLE[source_style]
                    source_record = records[selected_style].get(scalar)
                    selection_kind = "same_game_cross_style"
                if source_record is None:
                    for tier_name, glyph_dir, (supplemental_map, _, supplemental_records, _) in supplemental_maps[
                        (locale, style)
                    ]:
                        source_record = supplemental_records[source_style].get(scalar)
                        if source_record is not None:
                            selection_kind = "fehrr_supplemental"
                            glyph_directory = glyph_dir
                            selected_map = supplemental_map
                            selected_tier = tier_name
                            break
                if source_record is None:
                    selection_counts["febuilder_fallback"] += 1
                    fallback_rows.append(
                        {
                            "scalar": scalar_text(scalar),
                            "reason": "absent from configured FEHRR style tiers",
                        }
                    )
                    continue
                relative_glyph = f"{glyph_directory}/{source_record['filename']}"
                glyph_path = source_root / relative_glyph
                if not glyph_path.is_file():
                    raise CjkFontError(f"{glyph_path}: FEHRR glyph is missing")
                png = glyph_path.read_bytes()
                packed = _pack_engine_tile(_read_fehrr_png_indices(png))
                if not any(packed):
                    fallback_style = FEHRR_CROSS_STYLE[selected_style]
                    fallback_record = records[fallback_style].get(scalar)
                    if fallback_record is not None:
                        selected_style = fallback_style
                        selection_kind = "same_game_cross_style"
                        glyph_directory = f"glyph/{FEHRR_LOCALES[locale]}"
                        selected_map = relative_map
                        selected_tier = "same-game"
                        relative_glyph = (
                            f"{glyph_directory}/{fallback_record['filename']}"
                        )
                        glyph_path = source_root / relative_glyph
                        png = glyph_path.read_bytes()
                        packed = _pack_engine_tile(_read_fehrr_png_indices(png))
                    if not any(packed):
                        selection_counts["febuilder_fallback"] += 1
                        fallback_rows.append(
                            {
                                "scalar": scalar_text(scalar),
                                "reason": (
                                    "configured FEHRR glyph is blank and no "
                                    "visible same-game fallback exists"
                                ),
                            }
                        )
                        continue
                if relative_glyph not in tree_member_names:
                    tree_members.append((relative_glyph, png))
                    tree_member_names.add(relative_glyph)
                selected[scalar] = (int(source_record["width"]), packed)
                selection_counts[selection_kind] += 1
                rows.append(
                    {
                        "scalar": scalar_text(scalar),
                        "filename": relative_glyph,
                        "selection_kind": selection_kind,
                        "source_map": selected_map,
                        "source_style": selected_style,
                        "source_tier": selected_tier,
                        "width": int(source_record["width"]),
                        "png_sha256": sha256_bytes(png),
                        "packed_sha256": sha256_bytes(packed),
                    }
                )
            prefix = f"{locale}.{style}"
            assets[prefix] = {
                "source_locale": FEHRR_LOCALES[locale],
                "preferred_source_style": source_style,
                "selection_counts": selection_counts,
                "supplemental_maps": supplemental_lock,
                "font_map": {
                    "path": relative_map,
                    "sha256": sha256_bytes(map_data),
                },
                "glyph_count": len(rows),
                "febuilder_fallback_glyph_count": len(corpus) - len(rows),
                "febuilder_fallbacks": fallback_rows,
                "glyphs": rows,
            }
            glyph_data[prefix] = selected

    lock = {
        "schema_version": 1,
        "source": {
            **checkout,
            "tree_sha256": _source_tree_sha256(tree_members),
        },
        "duplicate_width_resolution": {
            "rule": "first declaration in font.fontall.txt wins",
            "conflicts": duplicate_widths,
        },
        "selection_policy": FEHRR_PRIORITY_POLICY,
        "assets": assets,
    }
    return lock, glyph_data


def _parse_scalar(text: str) -> int:
    if not text.startswith("U+"):
        raise CjkFontError(f"invalid Unicode scalar spelling {text!r}")
    try:
        value = int(text[2:], 16)
    except ValueError as error:
        raise CjkFontError(f"invalid Unicode scalar spelling {text!r}") from error
    if text != scalar_text(value) or value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
        raise CjkFontError(f"non-canonical Unicode scalar {text!r}")
    return value


def _parse_slots(data: bytes, job_id: str) -> List[Dict[str, object]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CjkFontError(f"{job_id}/slots.tsv is not UTF-8") from error
    if "\r" in text or not text.endswith("\n"):
        raise CjkFontError(f"{job_id}/slots.tsv must use canonical LF text")
    lines = text.splitlines()
    if not lines or lines[0] != SLOTS_HEADER:
        raise CjkFontError(f"{job_id}/slots.tsv header is invalid")
    rows = []
    reader = csv.reader(lines[1:], delimiter="\t", strict=True)
    for columns in reader:
        if len(columns) != 7:
            raise CjkFontError(f"{job_id}/slots.tsv row must have seven columns")
        moji, scalar, style, width, filename, packed_hash, png_hash = columns
        try:
            moji_value = int(moji, 16)
            width_value = int(width, 10)
        except ValueError as error:
            raise CjkFontError(f"{job_id}/slots.tsv has invalid numeric data") from error
        if moji != f"{moji_value:X}" or width != str(width_value):
            raise CjkFontError(f"{job_id}/slots.tsv numeric spelling is not canonical")
        if style not in ("item", "text") or not 1 <= width_value <= 16:
            raise CjkFontError(f"{job_id}/slots.tsv style or width is invalid")
        if filename != f"{style}_{moji_value:X}.png":
            raise CjkFontError(f"{job_id}/slots.tsv filename is not canonical")
        for digest in (packed_hash, png_hash):
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise CjkFontError(f"{job_id}/slots.tsv hash is invalid")
        rows.append(
            {
                "moji": moji_value,
                "scalar": _parse_scalar(scalar),
                "style": style,
                "width": width_value,
                "filename": filename,
                "packed_sha256": packed_hash,
                "png_sha256": png_hash,
            }
        )
    if not rows:
        raise CjkFontError(f"{job_id}/slots.tsv is empty")
    return rows


def _expected_jobs(root: Path) -> Dict[str, Dict[str, object]]:
    manifest_path = root / "fonts/cjk/febuilder-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    jobs = {}
    for job in manifest["jobs"]:
        job_id = job["id"]
        if job_id in jobs:
            raise CjkFontError(f"duplicate FEBuilder job id {job_id}")
        jobs[job_id] = job
    return jobs


def _job_corpora(
    root: Path,
    jobs: Mapping[str, Mapping[str, object]],
) -> Dict[str, Tuple[Path, bytes, Tuple[int, ...]]]:
    corpora = {}
    for job_id, job in jobs.items():
        corpus_path = root / "fonts/cjk" / job["corpus"]["path"]
        corpus_data = corpus_path.read_bytes()
        try:
            corpus = corpus_data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CjkFontError(f"{corpus_path}: corpus is not UTF-8") from error
        scalars = tuple(ord(character) for character in corpus)
        if scalars != tuple(sorted(set(scalars))):
            raise CjkFontError(f"{corpus_path}: scalars must be sorted and unique")
        if sha256_bytes(corpus_data) != job["corpus"]["sha256"]:
            raise CjkFontError(f"{corpus_path}: corpus SHA-256 mismatch")
        corpora[job_id] = (corpus_path, corpus_data, scalars)
    return corpora


def _load_report(
    root: Path,
    report_data: bytes,
    expected_mode: str,
) -> Tuple[
    Dict[str, object],
    Dict[str, Dict[str, object]],
    Dict[str, Tuple[Path, bytes, Tuple[int, ...]]],
]:
    try:
        report = json.loads(report_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CjkFontError("FEBuilder report is not valid UTF-8 JSON") from error
    if report.get("schemaVersion") != 1 or report.get("mode") != expected_mode:
        raise CjkFontError(f"FEBuilder report is not schema-v1 {expected_mode} output")

    manifest_data = (root / "fonts/cjk/febuilder-manifest.json").read_bytes()
    if report.get("manifestSha256") != sha256_bytes(manifest_data):
        raise CjkFontError("FEBuilder report manifest SHA-256 mismatch")
    if report.get("outcomes") != []:
        raise CjkFontError("FEBuilder report records non-success outcomes")

    jobs = _expected_jobs(root)
    corpora = _job_corpora(root, jobs)
    report_rows = report.get("jobs", [])
    if not isinstance(report_rows, list):
        raise CjkFontError("FEBuilder report jobs must be a list")
    report_jobs = {}
    for row in report_rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise CjkFontError("FEBuilder report job is invalid")
        job_id = row["id"]
        if job_id in report_jobs:
            raise CjkFontError(f"duplicate FEBuilder report job id {job_id}")
        report_jobs[job_id] = row
    if set(report_jobs) != set(jobs):
        raise CjkFontError("FEBuilder report does not cover exactly the manifest jobs")
    for job_id, job in jobs.items():
        row = report_jobs[job_id]
        _, corpus_data, scalars = corpora[job_id]
        if row.get("scalarCount") != len(scalars):
            raise CjkFontError(f"{job_id}: FEBuilder scalar count mismatch")
        if row.get("rowCount") != len(scalars):
            raise CjkFontError(f"{job_id}: FEBuilder row count mismatch")
        if row.get("corpusSha256") != sha256_bytes(corpus_data):
            raise CjkFontError(f"{job_id}: FEBuilder corpus SHA-256 mismatch")
        if row.get("locale") != job["locale"] or row.get("format") != job["format"]:
            raise CjkFontError(f"{job_id}: FEBuilder job contract mismatch")
    return report, jobs, corpora


def _gate_record(
    report: Mapping[str, object],
    *,
    mode: str,
    oracle: str,
    job_count: int,
    row_count: int,
) -> Dict[str, object]:
    return {
        "full_tree_sha256": report.get("fullTreeSha256", ""),
        "job_count": job_count,
        "manifest_sha256": report["manifestSha256"],
        "mode": mode,
        "oracle": oracle,
        "outcomes": report["outcomes"],
        "payload_tree_sha256": report.get("payloadTreeSha256", ""),
        "row_count": row_count,
    }


def record_gate_evidence(
    root: Path,
    dry_run_report_path: Path,
    generation_report_path: Path,
    output_report_path: Path,
    gate_report_path: Path,
    *,
    cli_command: str,
    commit: str,
    dotnet_sdk: str,
    repository: str,
) -> Dict[str, object]:
    dry_run_data = dry_run_report_path.read_bytes()
    generation_data = generation_report_path.read_bytes()
    dry_run, dry_jobs, dry_corpora = _load_report(root, dry_run_data, "dry-run")
    generation, jobs, corpora = _load_report(root, generation_data, "generate")
    if set(dry_jobs) != set(jobs):
        raise CjkFontError("dry-run and generation reports cover different jobs")
    if {
        job_id: values[2] for job_id, values in dry_corpora.items()
    } != {
        job_id: values[2] for job_id, values in corpora.items()
    }:
        raise CjkFontError("dry-run and generation report corpora differ")

    job_count = len(jobs)
    row_count = sum(len(values[2]) for values in corpora.values())
    manifest_data = (root / "fonts/cjk/febuilder-manifest.json").read_bytes()
    generated_gate = _gate_record(
        generation,
        mode="generate",
        oracle="generation",
        job_count=job_count,
        row_count=row_count,
    )
    evidence = {
        "febuilder": {
            "cli_command": cli_command,
            "commit": commit,
            "dotnet_sdk": dotnet_sdk,
            "repository": repository,
        },
        "files": {
            "generation_report_sha256": sha256_bytes(generation_data),
            "manifest_sha256": sha256_bytes(manifest_data),
        },
        "gates": {
            "dry_run": _gate_record(
                dry_run,
                mode="dry-run",
                oracle="plan-and-provenance-only",
                job_count=job_count,
                row_count=row_count,
            ),
            "generate": generated_gate,
            "roundtrip": {
                **generated_gate,
                "mode": "roundtrip",
                "oracle": "immutable-external-report",
            },
            "validate": {
                **generated_gate,
                "mode": "validate",
                "oracle": "immutable-external-report",
            },
        },
        "result": "all FEBuilder schema-v1 gates exited 0",
        "schema_version": 1,
    }
    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    gate_report_path.parent.mkdir(parents=True, exist_ok=True)
    output_report_path.write_bytes(generation_data)
    gate_report_path.write_bytes(json_bytes(evidence))
    return evidence


def _tree_sha256(package: PackageReader, names: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(names):
        data = package.read(name)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(struct.pack(">Q", len(data)))
        digest.update(data)
    return digest.hexdigest()


def _job_contract(job: Mapping[str, object]) -> Tuple[str, str, str]:
    locale = str(job["locale"])
    if locale not in LOCALES:
        raise CjkFontError(f"unsupported locale {locale}")
    styles = job["styles"]
    if styles == ["item"]:
        return locale, "system", "item"
    if styles == ["text"]:
        return locale, "talk", "text"
    raise CjkFontError(f"{job['id']}: expected exactly one item/text style")


def build_compact_assets(
    root: Path,
    package_path: Path,
    report_path: Path,
) -> Dict[str, bytes]:
    manifest_data = (root / "fonts/cjk/febuilder-manifest.json").read_bytes()
    inventory_data = (root / "fonts/cjk/inventory.json").read_bytes()
    report_data = report_path.read_bytes()
    report, jobs, corpora = _load_report(root, report_data, "generate")

    outputs: Dict[str, bytes] = {}
    asset_records: Dict[str, object] = {}
    payload_total = 0
    aligned_total = 0
    expected_names = {"package-report.json"}

    with PackageReader(package_path) as package:
        package_names = set(package.names())
        actual_full_tree = _tree_sha256(package, package_names)
        if actual_full_tree != report.get("fullTreeSha256"):
            raise CjkFontError("generation report full-tree SHA-256 mismatch")
        package_report_data = package.read("package-report.json")
        try:
            package_report = json.loads(package_report_data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CjkFontError("package-report.json is invalid") from error
        if package_report.get("manifestSha256") != sha256_bytes(manifest_data):
            raise CjkFontError("package report manifest SHA-256 mismatch")
        payload_names = package_names - {"package-report.json"}
        actual_payload_tree = _tree_sha256(package, payload_names)
        if actual_payload_tree != package_report.get("payloadTreeSha256"):
            raise CjkFontError("package report payload-tree SHA-256 mismatch")
        if actual_payload_tree != report.get("payloadTreeSha256"):
            raise CjkFontError("generation report payload-tree SHA-256 mismatch")
        report_jobs = {row["id"]: row for row in report.get("jobs", [])}
        package_jobs = {row["id"]: row for row in package_report.get("jobs", [])}
        if set(report_jobs) != set(jobs) or set(package_jobs) != set(jobs):
            raise CjkFontError("FEBuilder reports do not cover exactly the manifest jobs")

        for job_id in sorted(jobs):
            job = jobs[job_id]
            locale, runtime_style, package_style = _job_contract(job)
            _, _, expected_scalars = corpora[job_id]

            slots_name = f"{job_id}/slots.tsv"
            fontall_name = f"{job_id}/{job_id}.fontall.txt"
            rows = _parse_slots(package.read(slots_name), job_id)
            expected_names.update((slots_name, fontall_name))
            rows.sort(key=lambda row: row["scalar"])
            actual_scalars = tuple(row["scalar"] for row in rows)
            if actual_scalars != expected_scalars:
                raise CjkFontError(f"{job_id}: package scalar coverage mismatch")
            if len(set(actual_scalars)) != len(actual_scalars):
                raise CjkFontError(f"{job_id}: duplicate Unicode scalar")
            if any(row["style"] != package_style for row in rows):
                raise CjkFontError(f"{job_id}: package style mismatch")
            if report_jobs[job_id]["scalarCount"] != len(rows):
                raise CjkFontError(f"{job_id}: generation report scalar count mismatch")
            if package_jobs[job_id]["scalarCount"] != len(rows):
                raise CjkFontError(f"{job_id}: package report scalar count mismatch")

            glyphs = bytearray()
            widths = bytearray()
            codepoints = bytearray()
            for row in rows:
                png_name = f"{job_id}/{row['filename']}"
                png = package.read(png_name)
                expected_names.add(png_name)
                if sha256_bytes(png) != row["png_sha256"]:
                    raise CjkFontError(f"{png_name}: PNG SHA-256 mismatch")
                packed = _pack_engine_tile(_read_png_indices(png))
                if sha256_bytes(packed) != row["packed_sha256"]:
                    raise CjkFontError(f"{png_name}: packed SHA-256 mismatch")
                if not any(packed):
                    raise CjkFontError(f"{png_name}: all-zero glyph")
                glyphs.extend(packed)
                widths.append(row["width"])
                codepoints.extend(struct.pack("<I", row["scalar"]))

            prefix = f"{locale}.{runtime_style}"
            filenames = compact_asset_filenames(prefix)
            files = {
                "codepoints": (filenames["codepoints"], bytes(codepoints)),
                "widths": (filenames["widths"], bytes(widths)),
                "glyphs": (filenames["bitmap"], bytes(glyphs)),
            }
            for _, (filename, data) in files.items():
                outputs[f"{ASSET_ROOT}/{filename}"] = data
                payload_total += len(data)
                aligned_total += (len(data) + 3) & ~3
            asset_records[prefix] = {
                "locale": locale,
                "runtime_style": runtime_style,
                "febuilder_job": job_id,
                "febuilder_style": package_style,
                "glyph_count": len(rows),
                "bitmap": {
                    "format": "16x16 row-major 2bpp, four pixels per byte, low-bit-first",
                    "stride_bytes": 64,
                    "path": f"{ASSET_ROOT}/{files['glyphs'][0]}",
                    "byte_count": len(files["glyphs"][1]),
                    "sha256": sha256_bytes(files["glyphs"][1]),
                },
                "widths": {
                    "format": "one unsigned byte per glyph; valid range 1..16",
                    "path": f"{ASSET_ROOT}/{files['widths'][0]}",
                    "byte_count": len(files["widths"][1]),
                    "sha256": sha256_bytes(files["widths"][1]),
                },
                "codepoints": {
                    "format": "sorted unique little-endian uint32 Unicode scalars",
                    "path": f"{ASSET_ROOT}/{files['codepoints'][0]}",
                    "byte_count": len(files["codepoints"][1]),
                    "sha256": sha256_bytes(files["codepoints"][1]),
                },
            }

        if package_names != expected_names:
            extras = sorted(package_names - expected_names)
            missing = sorted(expected_names - package_names)
            raise CjkFontError(
                f"package tree mismatch; extras={extras[:5]} missing={missing[:5]}"
            )

    asset_manifest = {
        "schema_version": 1,
        "contract": {
            "lookup": (
                "binary-search codepoints; use the same index for widths and "
                "the fixed 64-byte bitmap stride"
            ),
            "ascii": "continue using the existing runtime ASCII font",
            "spacing": (
                "U+3000 is inventoried as a spacing scalar, not a bitmap; "
                "Sprint 3 must give it an explicit advance"
            ),
        },
        "spacing_scalars": [
            {
                "scalar": "U+3000",
                "advance": 16,
                "locales": ["ja"],
                "runtime_styles": ["system", "talk"],
                "bitmap": None,
            }
        ],
        "sources": {
            "inventory": {
                "path": "fonts/cjk/inventory.json",
                "sha256": sha256_bytes(inventory_data),
            },
            "febuilder_manifest": {
                "path": "fonts/cjk/febuilder-manifest.json",
                "sha256": sha256_bytes(manifest_data),
            },
            "febuilder_package": {
                "disposition": (
                    "temporary maintainer artifact under build/tmp; not committed"
                ),
                "package_report_sha256": sha256_bytes(package_report_data),
                "payload_tree_sha256": package_report.get("payloadTreeSha256"),
                "full_tree_sha256": report.get("fullTreeSha256"),
            },
            "febuilder_generation_report": {
                "path": GENERATION_REPORT,
                "sha256": sha256_bytes(report_data),
                "byte_count": len(report_data),
            },
        },
        "assets": asset_records,
        "rom_budget": {
            "payload_bytes": payload_total,
            "four_byte_aligned_blob_bytes": aligned_total,
            "bytes_per_glyph": 69,
            "includes": "64-byte bitmap + 1-byte width + 4-byte Unicode scalar",
        },
    }
    outputs[f"{ASSET_ROOT}/manifest.json"] = json_bytes(asset_manifest)
    return outputs


def write_compact_assets(
    root: Path,
    package_path: Path,
    report_path: Path,
) -> Dict[str, bytes]:
    outputs = build_compact_assets(root, package_path, report_path)
    repeated = build_compact_assets(root, package_path, report_path)
    if repeated != outputs:
        raise CjkFontError("FEBuilder package import is not deterministic")
    _reject_generic_compact_assets(root)
    for relative_path, data in outputs.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return outputs


def _read_fehrr_lock(root: Path, sources: Mapping[str, object]) -> Dict[str, object]:
    priority = sources.get("fehrr_priority")
    if priority is None:
        return {}
    if not isinstance(priority, dict):
        raise CjkFontError("compact asset FEHRR priority provenance is invalid")
    lock_path = root / FEHRR_SOURCES
    lock_data = lock_path.read_bytes()
    expected = {
        "path": FEHRR_SOURCES,
        "sha256": sha256_bytes(lock_data),
        "policy": "FEHRR original-game glyphs and widths before FEBuilderGBA fallback",
    }
    if priority != expected:
        raise CjkFontError("compact asset FEHRR priority provenance drifted")
    try:
        lock = json.loads(lock_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CjkFontError("FEHRR source lock is not valid UTF-8 JSON") from error
    source = lock.get("source", {})
    if (
        lock.get("schema_version") != 1
        or not isinstance(source, dict)
        or source.get("repository") != FEHRR_REPOSITORY
        or not isinstance(source.get("commit"), str)
        or len(source["commit"]) != 40
        or any(character not in "0123456789abcdef" for character in source["commit"])
        or not isinstance(source.get("tree_sha256"), str)
        or len(source["tree_sha256"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in source["tree_sha256"]
        )
    ):
        raise CjkFontError("FEHRR source lock provenance is invalid")
    if not isinstance(lock.get("assets"), dict):
        raise CjkFontError("FEHRR source lock assets are invalid")
    return lock


def _fehrr_scalar(value: object) -> int:
    if not isinstance(value, str):
        raise CjkFontError("FEHRR source lock scalar is invalid")
    return _parse_scalar(value)


def _validate_fehrr_priority(
    root: Path,
    asset_manifest: Mapping[str, object],
    lock: Mapping[str, object],
    report: Mapping[str, object],
    jobs: Mapping[str, Mapping[str, object]],
    corpora: Mapping[str, Tuple[Path, bytes, Tuple[int, ...]]],
    payloads: Mapping[str, Mapping[str, bytes]],
) -> None:
    lock_assets = lock["assets"]
    if set(lock_assets) != set(asset_manifest.get("assets", {})):
        raise CjkFontError("FEHRR source lock does not cover exactly the compact assets")
    report_jobs = {
        row["id"]: row
        for row in report.get("jobs", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    for job_id in sorted(jobs):
        locale, runtime_style, source_style = _job_contract(jobs[job_id])
        prefix = f"{locale}.{runtime_style}"
        _, _, expected_scalars = corpora[job_id]
        lock_asset = lock_assets[prefix]
        if not isinstance(lock_asset, dict):
            raise CjkFontError(f"{prefix}: FEHRR source lock asset is invalid")
        expected_source_locale = FEHRR_LOCALES[locale]
        expected_source_style = FEHRR_SOURCE_STYLES[runtime_style]
        if (
            lock_asset.get("source_locale") != expected_source_locale
            or lock_asset.get("source_style") != expected_source_style
            or lock_asset.get("glyph_count") != len(lock_asset.get("glyphs", []))
            or lock_asset.get("febuilder_fallback_glyph_count")
            != len(expected_scalars) - lock_asset.get("glyph_count", 0)
        ):
            raise CjkFontError(f"{prefix}: FEHRR source lock contract is invalid")
        font_map = lock_asset.get("font_map", {})
        if (
            font_map.get("path") != f"glyph/{expected_source_locale}/font.fontall.txt"
            or len(font_map.get("sha256", "")) != 64
            or any(
                character not in "0123456789abcdef"
                for character in font_map.get("sha256", "")
            )
        ):
            raise CjkFontError(f"{prefix}: FEHRR source map provenance is invalid")
        rows = lock_asset.get("glyphs")
        if not isinstance(rows, list):
            raise CjkFontError(f"{prefix}: FEHRR source rows are invalid")
        rows_by_scalar = {}
        for row in rows:
            if not isinstance(row, dict):
                raise CjkFontError(f"{prefix}: FEHRR source row is invalid")
            scalar = _fehrr_scalar(row.get("scalar"))
            if scalar in rows_by_scalar:
                raise CjkFontError(f"{prefix}: duplicate FEHRR source scalar")
            filename = row.get("filename", "")
            if (
                not isinstance(filename, str)
                or not filename.startswith(f"glyph/{expected_source_locale}/")
                or Path(filename).name != filename.rsplit("/", 1)[-1]
                or not filename.startswith(
                    f"glyph/{expected_source_locale}/"
                    + ("FontItem_" if expected_source_style == "item" else "FontText_")
                )
            ):
                raise CjkFontError(f"{prefix}: FEHRR source filename is invalid")
            width = row.get("width")
            if not isinstance(width, int) or not 1 <= width <= 16:
                raise CjkFontError(f"{prefix}: FEHRR source width is invalid")
            for hash_name in ("png_sha256", "packed_sha256"):
                digest = row.get(hash_name, "")
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    raise CjkFontError(f"{prefix}: FEHRR source {hash_name} is invalid")
            rows_by_scalar[scalar] = row
        source_scalars = tuple(sorted(rows_by_scalar))
        if source_scalars != tuple(rows_by_scalar):
            raise CjkFontError(f"{prefix}: FEHRR source rows must be sorted")
        if not set(source_scalars) <= set(expected_scalars):
            raise CjkFontError(f"{prefix}: FEHRR source rows are outside the corpus")

        priority = asset_manifest["assets"][prefix].get("source_priority")
        expected_priority = {
            "fehrr_glyph_count": len(source_scalars),
            "febuilder_fallback_glyph_count": len(expected_scalars) - len(source_scalars),
            "source_locale": expected_source_locale,
            "source_style": expected_source_style,
        }
        if priority != expected_priority:
            raise CjkFontError(f"{prefix}: FEHRR source-priority record drifted")

        scalar_index = {scalar: index for index, scalar in enumerate(expected_scalars)}
        widths = payloads[prefix]["widths"]
        glyphs = payloads[prefix]["bitmap"]
        for scalar, row in rows_by_scalar.items():
            index = scalar_index[scalar]
            packed = glyphs[index * 64 : (index + 1) * 64]
            if widths[index] != row["width"] or sha256_bytes(packed) != row["packed_sha256"]:
                raise CjkFontError(f"{prefix}: FEHRR glyph or width drifted")
        zero_scalars = {
            scalar
            for scalar, index in scalar_index.items()
            if not any(glyphs[index * 64 : (index + 1) * 64])
        }
        if not zero_scalars <= set(source_scalars):
            raise CjkFontError(f"{prefix}: blank glyph is not sourced from FEHRR")
        fallback_rows = {
            row["unicodeScalar"]: row
            for row in report_jobs[job_id].get("glyphs", [])
            if isinstance(row, dict) and isinstance(row.get("unicodeScalar"), int)
        }
        if set(fallback_rows) != set(expected_scalars):
            raise CjkFontError(f"{prefix}: FEBuilder fallback oracle coverage drifted")
        for scalar in set(expected_scalars) - set(source_scalars):
            index = scalar_index[scalar]
            packed = glyphs[index * 64 : (index + 1) * 64]
            fallback = fallback_rows[scalar]
            if (
                widths[index] != fallback.get("width")
                or sha256_bytes(packed) != fallback.get("packedSha256")
            ):
                raise CjkFontError(f"{prefix}: non-FEHRR glyph is not the FEBuilder fallback")


def _source_selection(lock: Mapping[str, object]) -> Dict[str, Tuple[int, ...]]:
    return {
        prefix: tuple(_fehrr_scalar(row["scalar"]) for row in asset["glyphs"])
        for prefix, asset in lock["assets"].items()
    }


def _existing_scalar_payloads(
    root: Path,
    asset: Mapping[str, object],
) -> Dict[int, Tuple[int, bytes]]:
    codepoints = (root / asset["codepoints"]["path"]).read_bytes()
    widths = (root / asset["widths"]["path"]).read_bytes()
    bitmaps = (root / asset["bitmap"]["path"]).read_bytes()
    if len(codepoints) != len(widths) * 4 or len(bitmaps) != len(widths) * 64:
        raise CjkFontError("existing compact asset lengths are inconsistent")
    scalars = struct.unpack(f"<{len(widths)}I", codepoints)
    if tuple(scalars) != tuple(sorted(set(scalars))):
        raise CjkFontError("existing compact asset codepoints are not sorted")
    return {
        scalar: (widths[index], bitmaps[index * 64 : (index + 1) * 64])
        for index, scalar in enumerate(scalars)
    }


def _baseline_scalar_payloads(root: Path, prefix: str) -> Dict[int, Tuple[int, bytes]]:
    base = root / FEHBUILDER_BASELINE_ASSET_ROOT
    codepoints = (base / f"{prefix}.codepoints.u32le").read_bytes()
    widths = (base / f"{prefix}.widths.u8").read_bytes()
    bitmaps = (base / f"{prefix}.glyphs.2bpp").read_bytes()
    if len(codepoints) != len(widths) * 4 or len(bitmaps) != len(widths) * 64:
        raise CjkFontError(f"{prefix}: FEBuilder baseline asset lengths are inconsistent")
    scalars = struct.unpack(f"<{len(widths)}I", codepoints)
    return {
        scalar: (widths[index], bitmaps[index * 64 : (index + 1) * 64])
        for index, scalar in enumerate(scalars)
    }


def _check_baseline_assets(root: Path) -> bytes:
    path = root / FEHBUILDER_BASELINE_ASSET_MANIFEST
    data = path.read_bytes()
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CjkFontError("FEBuilder baseline asset manifest is invalid") from error
    if (
        manifest.get("kind") != "fe8u-febuilder-full-union-baseline"
        or manifest.get("schema_version") != 1
        or set(manifest.get("assets", {}))
        != {"ja.system", "ja.talk", "zh-Hans.system", "zh-Hans.talk"}
    ):
        raise CjkFontError("FEBuilder baseline asset manifest schema drifted")
    for prefix, files in manifest["assets"].items():
        for suffix, record in files.items():
            payload = (root / FEHBUILDER_BASELINE_ASSET_ROOT / f"{prefix}.{suffix}").read_bytes()
            if (
                record.get("byte_count") != len(payload)
                or record.get("sha256") != sha256_bytes(payload)
            ):
                raise CjkFontError(f"{prefix}: FEBuilder baseline asset hash drifted")
    return data


def _asset_record(
    *,
    locale: str,
    runtime_style: str,
    job_id: str,
    package_style: str,
    codepoints: bytes,
    widths: bytes,
    bitmaps: bytes,
) -> Dict[str, object]:
    prefix = f"{locale}.{runtime_style}"
    filenames = compact_asset_filenames(prefix)
    return {
        "locale": locale,
        "runtime_style": runtime_style,
        "febuilder_job": job_id,
        "febuilder_style": package_style,
        "glyph_count": len(widths),
        "bitmap": {
            "format": "16x16 row-major 2bpp, four pixels per byte, low-bit-first",
            "stride_bytes": 64,
            "path": f"{ASSET_ROOT}/{filenames['bitmap']}",
            "byte_count": len(bitmaps),
            "sha256": sha256_bytes(bitmaps),
        },
        "widths": {
            "format": "one unsigned byte per glyph; valid range 1..16",
            "path": f"{ASSET_ROOT}/{filenames['widths']}",
            "byte_count": len(widths),
            "sha256": sha256_bytes(widths),
        },
        "codepoints": {
            "format": "sorted unique little-endian uint32 Unicode scalars",
            "path": f"{ASSET_ROOT}/{filenames['codepoints']}",
            "byte_count": len(codepoints),
            "sha256": sha256_bytes(codepoints),
        },
    }


def build_runtime_split_assets(
    root: Path,
    source_root: Path,
) -> Dict[str, bytes]:
    """Filter the verified full-union baseline into usage-specific assets.

    The checked-in FEBuilder generation report predates this split but covers
    its full scalar superset. It remains a precise fallback oracle: every new
    corpus scalar is selected from that verified baseline unless FEHRR wins
    under the explicit source-priority policy.
    """

    jobs = _expected_jobs(root)
    corpora = _job_corpora(root, jobs)
    baseline_path = root / FEHBUILDER_BASELINE_MANIFEST
    if not baseline_path.is_file():
        raise CjkFontError(f"{baseline_path}: missing verified full-union baseline")
    baseline_data = baseline_path.read_bytes()
    baseline_asset_data = _check_baseline_assets(root)
    report_data = (root / GENERATION_REPORT).read_bytes()
    report = json.loads(report_data.decode("utf-8"))
    if report.get("manifestSha256") != sha256_bytes(baseline_data):
        raise CjkFontError("FEBuilder baseline manifest/report provenance drifted")

    lock, selected = _collect_fehrr_sources(root, source_root, jobs, corpora)
    outputs: Dict[str, bytes] = {}
    assets = {}
    payload_total = 0
    aligned_total = 0
    for job_id in sorted(jobs):
        locale, runtime_style, package_style = _job_contract(jobs[job_id])
        prefix = f"{locale}.{runtime_style}"
        _, _, scalars = corpora[job_id]
        fallback = _baseline_scalar_payloads(root, prefix)
        codepoints = bytearray()
        widths = bytearray()
        bitmaps = bytearray()
        for scalar in scalars:
            try:
                width, bitmap = selected[prefix].get(scalar, fallback[scalar])
            except KeyError as error:
                raise CjkFontError(f"{prefix}: no verified fallback for {scalar_text(scalar)}") from error
            codepoints.extend(struct.pack("<I", scalar))
            widths.append(width)
            bitmaps.extend(bitmap)
        record = _asset_record(
            locale=locale,
            runtime_style=runtime_style,
            job_id=job_id,
            package_style=package_style,
            codepoints=bytes(codepoints),
            widths=bytes(widths),
            bitmaps=bytes(bitmaps),
        )
        lock_asset = lock["assets"][prefix]
        record["source_priority"] = {
            **lock_asset["selection_counts"],
            "policy": FEHRR_PRIORITY_POLICY,
        }
        assets[prefix] = record
        for kind, data in (
            ("codepoints", bytes(codepoints)),
            ("widths", bytes(widths)),
            ("bitmap", bytes(bitmaps)),
        ):
            filename = compact_asset_filenames(prefix)[kind]
            outputs[f"{ASSET_ROOT}/{filename}"] = data
            payload_total += len(data)
            aligned_total += (len(data) + 3) & ~3

    inventory_data = (root / "fonts/cjk/inventory.json").read_bytes()
    manifest_data = (root / "fonts/cjk/febuilder-manifest.json").read_bytes()
    asset_manifest = {
        "schema_version": 2,
        "contract": {
            "ascii": "continue using the existing runtime ASCII font",
            "lookup": "binary-search codepoints; use the same index for widths and the fixed 64-byte bitmap stride",
            "style_corpora": "derived from checked runtime usage; descriptions are intentionally both",
        },
        "sources": {
            "inventory": {
                "path": "fonts/cjk/inventory.json",
                "sha256": sha256_bytes(inventory_data),
            },
            "febuilder_manifest": {
                "path": "fonts/cjk/febuilder-manifest.json",
                "sha256": sha256_bytes(manifest_data),
            },
            "febuilder_baseline_manifest": {
                "path": FEHBUILDER_BASELINE_MANIFEST,
                "sha256": sha256_bytes(baseline_data),
            },
            "febuilder_baseline_assets": {
                "path": FEHBUILDER_BASELINE_ASSET_MANIFEST,
                "sha256": sha256_bytes(baseline_asset_data),
            },
            "febuilder_generation_report": {
                "path": GENERATION_REPORT,
                "sha256": sha256_bytes(report_data),
                "byte_count": len(report_data),
                "role": "verified full-union fallback baseline",
            },
            "fehrr_priority": {
                "path": FEHRR_SOURCES,
                "sha256": sha256_bytes(json_bytes(lock)),
                "policy": FEHRR_PRIORITY_POLICY,
            },
        },
        "assets": assets,
        "rom_budget": {
            "payload_bytes": payload_total,
            "four_byte_aligned_blob_bytes": aligned_total,
            "bytes_per_glyph": 69,
            "includes": "64-byte bitmap + 1-byte width + 4-byte Unicode scalar",
        },
        "spacing_scalars": [
            {
                "scalar": "U+3000",
                "advance": 16,
                "bitmap": None,
                "locales": list(LOCALES),
                "runtime_styles": list(STYLES),
            }
        ],
    }
    outputs[FEHRR_SOURCES] = json_bytes(lock)
    outputs[f"{ASSET_ROOT}/manifest.json"] = json_bytes(asset_manifest)
    return outputs


def write_runtime_split_assets(root: Path, source_root: Path) -> Dict[str, bytes]:
    outputs = build_runtime_split_assets(root, source_root)
    repeated = build_runtime_split_assets(root, source_root)
    if outputs != repeated:
        raise CjkFontError("runtime corpus split is not deterministic")
    for relative, data in outputs.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return outputs


def build_fehrr_priority_assets(
    root: Path,
    source_root: Path,
) -> Dict[str, bytes]:
    existing = check_compact_assets(root)
    report_data = (root / GENERATION_REPORT).read_bytes()
    report, jobs, corpora = _load_report(root, report_data, "generate")
    asset_manifest_path = root / ASSET_ROOT / "manifest.json"
    asset_manifest = json.loads(asset_manifest_path.read_text(encoding="utf-8"))
    sources = asset_manifest.get("sources")
    if not isinstance(sources, dict):
        raise CjkFontError("compact asset sources are invalid")

    lock, source_glyphs = _collect_fehrr_sources(root, source_root, jobs, corpora)
    old_lock = _read_fehrr_lock(root, sources)
    if old_lock and _source_selection(old_lock) != _source_selection(lock):
        raise CjkFontError(
            "FEHRR source coverage changed; regenerate the FEBuilder baseline before refresh"
        )

    outputs = dict(existing)
    for job_id in sorted(jobs):
        locale, runtime_style, _ = _job_contract(jobs[job_id])
        prefix = f"{locale}.{runtime_style}"
        _, _, expected_scalars = corpora[job_id]
        codepoints = existing[
            f"{ASSET_ROOT}/{compact_asset_filenames(prefix)['codepoints']}"
        ]
        widths = bytearray(
            existing[f"{ASSET_ROOT}/{compact_asset_filenames(prefix)['widths']}"]
        )
        glyphs = bytearray(
            existing[f"{ASSET_ROOT}/{compact_asset_filenames(prefix)['bitmap']}"]
        )
        scalar_index = {scalar: index for index, scalar in enumerate(expected_scalars)}
        for scalar, (width, packed) in source_glyphs[prefix].items():
            index = scalar_index[scalar]
            widths[index] = width
            glyphs[index * 64 : (index + 1) * 64] = packed
        filenames = compact_asset_filenames(prefix)
        outputs[f"{ASSET_ROOT}/{filenames['codepoints']}"] = codepoints
        outputs[f"{ASSET_ROOT}/{filenames['widths']}"] = bytes(widths)
        outputs[f"{ASSET_ROOT}/{filenames['bitmap']}"] = bytes(glyphs)

        asset = asset_manifest["assets"][prefix]
        for kind, filename in filenames.items():
            path = f"{ASSET_ROOT}/{filename}"
            data = outputs[path]
            asset_kind = "bitmap" if kind == "bitmap" else kind
            asset[asset_kind]["byte_count"] = len(data)
            asset[asset_kind]["sha256"] = sha256_bytes(data)
        asset["source_priority"] = {
            "fehrr_glyph_count": len(source_glyphs[prefix]),
            "febuilder_fallback_glyph_count": len(expected_scalars)
            - len(source_glyphs[prefix]),
            "source_locale": FEHRR_LOCALES[locale],
            "source_style": FEHRR_SOURCE_STYLES[runtime_style],
        }

    lock_data = json_bytes(lock)
    sources["fehrr_priority"] = {
        "path": FEHRR_SOURCES,
        "sha256": sha256_bytes(lock_data),
        "policy": "FEHRR original-game glyphs and widths before FEBuilderGBA fallback",
    }
    outputs[FEHRR_SOURCES] = lock_data
    outputs[f"{ASSET_ROOT}/manifest.json"] = json_bytes(asset_manifest)
    return outputs


def write_fehrr_priority_assets(
    root: Path,
    source_root: Path,
) -> Dict[str, bytes]:
    outputs = build_fehrr_priority_assets(root, source_root)
    repeated = build_fehrr_priority_assets(root, source_root)
    if outputs != repeated:
        raise CjkFontError("FEHRR source-priority import is not deterministic")
    for relative_path, data in outputs.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    check_compact_assets(root)
    return outputs


def refresh_compact_asset_inventory_provenance(root: Path) -> Dict[str, bytes]:
    report_path = root / GENERATION_REPORT
    report_data = report_path.read_bytes()

    inventory_data = (root / "fonts/cjk/inventory.json").read_bytes()
    asset_manifest_path = root / ASSET_ROOT / "manifest.json"
    asset_manifest = json.loads(asset_manifest_path.read_text(encoding="utf-8"))
    sources = asset_manifest.get("sources")
    if not isinstance(sources, dict):
        raise CjkFontError("compact asset sources are invalid")
    if asset_manifest.get("schema_version") != 2:
        _load_report(root, report_data, "generate")
    sources["inventory"] = {
        "path": "fonts/cjk/inventory.json",
        "sha256": sha256_bytes(inventory_data),
    }
    asset_manifest_path.write_bytes(json_bytes(asset_manifest))
    return check_compact_assets(root)


def _check_runtime_split_assets(root: Path, asset_manifest: Mapping[str, object]) -> Dict[str, bytes]:
    """Validate assets derived from the immutable full-union FEBuilder baseline."""

    sources = asset_manifest.get("sources")
    if not isinstance(sources, dict):
        raise CjkFontError("runtime split manifest sources are invalid")
    manifest_data = (root / "fonts/cjk/febuilder-manifest.json").read_bytes()
    inventory_data = (root / "fonts/cjk/inventory.json").read_bytes()
    baseline_data = (root / FEHBUILDER_BASELINE_MANIFEST).read_bytes()
    baseline_asset_data = _check_baseline_assets(root)
    report_data = (root / GENERATION_REPORT).read_bytes()
    try:
        report = json.loads(report_data.decode("utf-8"))
        lock = json.loads((root / FEHRR_SOURCES).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CjkFontError("runtime split provenance JSON is invalid") from error
    expected_sources = {
        "inventory": {
            "path": "fonts/cjk/inventory.json",
            "sha256": sha256_bytes(inventory_data),
        },
        "febuilder_manifest": {
            "path": "fonts/cjk/febuilder-manifest.json",
            "sha256": sha256_bytes(manifest_data),
        },
        "febuilder_baseline_manifest": {
            "path": FEHBUILDER_BASELINE_MANIFEST,
            "sha256": sha256_bytes(baseline_data),
        },
        "febuilder_baseline_assets": {
            "path": FEHBUILDER_BASELINE_ASSET_MANIFEST,
            "sha256": sha256_bytes(baseline_asset_data),
        },
    }
    for key, value in expected_sources.items():
        if sources.get(key) != value:
            raise CjkFontError(f"runtime split {key} provenance drifted")
    report_source = sources.get("febuilder_generation_report", {})
    if (
        report_source.get("path") != GENERATION_REPORT
        or report_source.get("sha256") != sha256_bytes(report_data)
        or report_source.get("byte_count") != len(report_data)
        or report_source.get("role") != "verified full-union fallback baseline"
        or report.get("manifestSha256") != sha256_bytes(baseline_data)
    ):
        raise CjkFontError("runtime split FEBuilder baseline provenance drifted")
    priority = sources.get("fehrr_priority", {})
    if (
        priority.get("path") != FEHRR_SOURCES
        or priority.get("sha256") != sha256_bytes(json_bytes(lock))
        or priority.get("policy") != FEHRR_PRIORITY_POLICY
        or lock.get("selection_policy") != FEHRR_PRIORITY_POLICY
    ):
        raise CjkFontError("runtime split FEHRR priority provenance drifted")

    jobs = _expected_jobs(root)
    corpora = _job_corpora(root, jobs)
    report_jobs = {row["id"]: row for row in report.get("jobs", [])}
    outputs: Dict[str, bytes] = {}
    payload_total = 0
    aligned_total = 0
    for job_id in sorted(jobs):
        locale, runtime_style, package_style = _job_contract(jobs[job_id])
        prefix = f"{locale}.{runtime_style}"
        _, _, scalars = corpora[job_id]
        asset = asset_manifest.get("assets", {}).get(prefix)
        lock_asset = lock.get("assets", {}).get(prefix)
        if not isinstance(asset, dict) or not isinstance(lock_asset, dict):
            raise CjkFontError(f"{prefix}: runtime split asset/lock is missing")
        if (
            asset.get("glyph_count") != len(scalars)
            or asset.get("locale") != locale
            or asset.get("runtime_style") != runtime_style
            or asset.get("febuilder_job") != job_id
            or asset.get("febuilder_style") != package_style
        ):
            raise CjkFontError(f"{prefix}: runtime split contract drifted")
        filenames = compact_asset_filenames(prefix)
        data = {}
        for kind, filename in filenames.items():
            key = "bitmap" if kind == "bitmap" else kind
            path = f"{ASSET_ROOT}/{filename}"
            payload = (root / path).read_bytes()
            record = asset.get(key, {})
            if (
                record.get("path") != path
                or record.get("byte_count") != len(payload)
                or record.get("sha256") != sha256_bytes(payload)
            ):
                raise CjkFontError(f"{prefix}: {kind} hash or path drifted")
            data[key] = payload
            outputs[path] = payload
            payload_total += len(payload)
            aligned_total += (len(payload) + 3) & ~3
        codepoints = tuple(
            struct.unpack(f"<{len(data['widths'])}I", data["codepoints"])
        )
        if codepoints != scalars or len(data["bitmap"]) != len(scalars) * 64:
            raise CjkFontError(f"{prefix}: runtime split corpus coverage drifted")
        rows = lock_asset.get("glyphs", [])
        selected = { _fehrr_scalar(row["scalar"]): row for row in rows }
        if len(selected) != len(rows) or not set(selected) <= set(scalars):
            raise CjkFontError(f"{prefix}: runtime split FEHRR rows drifted")
        counts = {
            "same_game_same_style": 0,
            "same_game_cross_style": 0,
            "fehrr_supplemental": 0,
            "febuilder_fallback": len(scalars) - len(selected),
        }
        scalar_index = {scalar: index for index, scalar in enumerate(scalars)}
        fallback_rows = {
            row["unicodeScalar"]: row for row in report_jobs[job_id].get("glyphs", [])
        }
        fallback_lock = {
            _fehrr_scalar(row["scalar"]): row
            for row in lock_asset.get("febuilder_fallbacks", [])
        }
        for scalar in scalars:
            index = scalar_index[scalar]
            bitmap = data["bitmap"][index * 64 : (index + 1) * 64]
            if scalar in selected:
                row = selected[scalar]
                kind = row.get("selection_kind")
                if kind not in (
                    "same_game_same_style",
                    "same_game_cross_style",
                    "fehrr_supplemental",
                ):
                    raise CjkFontError(f"{prefix}: invalid FEHRR selection kind")
                counts[kind] += 1
                if (
                    data["widths"][index] != row.get("width")
                    or sha256_bytes(bitmap) != row.get("packed_sha256")
                ):
                    raise CjkFontError(f"{prefix}: FEHRR glyph payload drifted")
            else:
                fallback = fallback_rows.get(scalar)
                if (
                    fallback is None
                    or fallback_lock.get(scalar, {}).get("reason")
                    != "absent from configured FEHRR style tiers"
                    or data["widths"][index] != fallback.get("width")
                    or sha256_bytes(bitmap) != fallback.get("packedSha256")
                ):
                    raise CjkFontError(f"{prefix}: FEBuilder fallback payload drifted")
        if set(fallback_lock) != set(scalars) - set(selected):
            raise CjkFontError(f"{prefix}: FEBuilder fallback lock drifted")
        if asset.get("source_priority") != {**counts, "policy": FEHRR_PRIORITY_POLICY}:
            raise CjkFontError(f"{prefix}: runtime split source counts drifted")

    if asset_manifest.get("rom_budget", {}).get("payload_bytes") != payload_total:
        raise CjkFontError("runtime split payload budget drifted")
    if (
        asset_manifest.get("rom_budget", {}).get("four_byte_aligned_blob_bytes")
        != aligned_total
    ):
        raise CjkFontError("runtime split aligned budget drifted")
    return outputs


def check_compact_assets(root: Path) -> Dict[str, bytes]:
    _reject_generic_compact_assets(root)
    asset_manifest_path = root / ASSET_ROOT / "manifest.json"
    asset_manifest = json.loads(asset_manifest_path.read_text(encoding="utf-8"))
    if asset_manifest.get("schema_version") == 2:
        return _check_runtime_split_assets(root, asset_manifest)
    report_path = root / GENERATION_REPORT
    report_data = report_path.read_bytes()
    report, jobs, corpora = _load_report(root, report_data, "generate")
    manifest_data = (root / "fonts/cjk/febuilder-manifest.json").read_bytes()
    inventory_data = (root / "fonts/cjk/inventory.json").read_bytes()

    gate_path = root / GATE_REPORT
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if gate.get("result") != "all FEBuilder schema-v1 gates exited 0":
        raise CjkFontError("FEBuilder gate evidence does not record a passing run")
    if gate.get("schema_version") != 1:
        raise CjkFontError("FEBuilder gate evidence schema is invalid")
    manifest_hash = sha256_bytes(manifest_data)
    expected_job_count = len(jobs)
    expected_row_count = sum(len(values[2]) for values in corpora.values())
    expected_gate_contracts = {
        "dry_run": ("dry-run", "plan-and-provenance-only", "", ""),
        "generate": (
            "generate",
            "generation",
            report.get("payloadTreeSha256"),
            report.get("fullTreeSha256"),
        ),
        "validate": (
            "validate",
            "immutable-external-report",
            report.get("payloadTreeSha256"),
            report.get("fullTreeSha256"),
        ),
        "roundtrip": (
            "roundtrip",
            "immutable-external-report",
            report.get("payloadTreeSha256"),
            report.get("fullTreeSha256"),
        ),
    }
    for name, contract in expected_gate_contracts.items():
        mode, oracle, payload_tree, full_tree = contract
        result = gate.get("gates", {}).get(name, {})
        if (
            result.get("manifest_sha256") != manifest_hash
            or result.get("job_count") != expected_job_count
            or result.get("row_count") != expected_row_count
            or result.get("mode") != mode
            or result.get("oracle") != oracle
            or result.get("outcomes") != []
            or result.get("payload_tree_sha256") != payload_tree
            or result.get("full_tree_sha256") != full_tree
        ):
            raise CjkFontError(f"FEBuilder {name} gate evidence is invalid")
    if gate.get("files", {}).get("manifest_sha256") != manifest_hash:
        raise CjkFontError("FEBuilder gate manifest hash drifted")
    if gate.get("files", {}).get("generation_report_sha256") != sha256_bytes(
        report_data
    ):
        raise CjkFontError("FEBuilder gate generation-report hash drifted")

    asset_manifest_data = asset_manifest_path.read_bytes()
    asset_manifest = json.loads(asset_manifest_data.decode("utf-8"))
    sources = asset_manifest.get("sources", {})
    if not isinstance(sources, dict):
        raise CjkFontError("compact asset sources are invalid")
    lock = _read_fehrr_lock(root, sources)
    if sources.get("inventory") != {
        "path": "fonts/cjk/inventory.json",
        "sha256": sha256_bytes(inventory_data),
    }:
        raise CjkFontError("compact asset inventory provenance drifted")
    if sources.get("febuilder_manifest") != {
        "path": "fonts/cjk/febuilder-manifest.json",
        "sha256": manifest_hash,
    }:
        raise CjkFontError("compact asset FEBuilder manifest provenance drifted")
    generation_source = sources.get("febuilder_generation_report", {})
    if (
        generation_source.get("path") != GENERATION_REPORT
        or generation_source.get("sha256") != sha256_bytes(report_data)
        or generation_source.get("byte_count") != len(report_data)
    ):
        raise CjkFontError("compact asset generation-report provenance drifted")
    package_source = sources.get("febuilder_package", {})
    if (
        package_source.get("disposition")
        != "temporary maintainer artifact under build/tmp; not committed"
        or package_source.get("payload_tree_sha256")
        != report.get("payloadTreeSha256")
        or package_source.get("full_tree_sha256") != report.get("fullTreeSha256")
    ):
        raise CjkFontError("compact asset temporary-package provenance drifted")
    package_report_hash = package_source.get("package_report_sha256", "")
    if len(package_report_hash) != 64 or any(
        character not in "0123456789abcdef" for character in package_report_hash
    ):
        raise CjkFontError("compact asset package-report hash is invalid")

    outputs: Dict[str, bytes] = {}
    payloads: Dict[str, Mapping[str, bytes]] = {}
    expected_assets = set()
    payload_total = 0
    aligned_total = 0
    for job_id in sorted(jobs):
        job = jobs[job_id]
        locale, runtime_style, package_style = _job_contract(job)
        _, _, expected_scalars = corpora[job_id]
        prefix = f"{locale}.{runtime_style}"
        expected_assets.add(prefix)
        asset = asset_manifest.get("assets", {}).get(prefix)
        if not isinstance(asset, dict):
            raise CjkFontError(f"compact asset manifest is missing {prefix}")
        if (
            asset.get("locale") != locale
            or asset.get("runtime_style") != runtime_style
            or asset.get("febuilder_job") != job_id
            or asset.get("febuilder_style") != package_style
            or asset.get("glyph_count") != len(expected_scalars)
        ):
            raise CjkFontError(f"{prefix}: compact asset contract drifted")

        expected_paths = {
            kind: f"{ASSET_ROOT}/{filename}"
            for kind, filename in compact_asset_filenames(prefix).items()
        }
        data_by_kind = {}
        for kind, relative_path in expected_paths.items():
            record = asset.get(kind, {})
            if record.get("path") != relative_path:
                raise CjkFontError(f"{prefix}: {kind} path drifted")
            data = (root / relative_path).read_bytes()
            if (
                record.get("byte_count") != len(data)
                or record.get("sha256") != sha256_bytes(data)
            ):
                raise CjkFontError(f"{prefix}: {kind} hash or size drifted")
            outputs[relative_path] = data
            data_by_kind[kind] = data
            payload_total += len(data)
            aligned_total += (len(data) + 3) & ~3

        expected_codepoints = b"".join(
            struct.pack("<I", scalar) for scalar in expected_scalars
        )
        if data_by_kind["codepoints"] != expected_codepoints:
            raise CjkFontError(f"{prefix}: codepoints do not cover the corpus")
        widths = data_by_kind["widths"]
        glyphs = data_by_kind["bitmap"]
        if len(widths) != len(expected_scalars) or any(
            not 1 <= width <= 16 for width in widths
        ):
            raise CjkFontError(f"{prefix}: widths are invalid")
        if len(glyphs) != len(expected_scalars) * 64:
            raise CjkFontError(f"{prefix}: glyph payload is invalid")
        if not lock and any(
            not any(glyphs[offset : offset + 64])
            for offset in range(0, len(glyphs), 64)
        ):
            raise CjkFontError(f"{prefix}: glyph payload is invalid")
        payloads[prefix] = data_by_kind

    if set(asset_manifest.get("assets", {})) != expected_assets:
        raise CjkFontError("compact asset manifest has unexpected locale/style assets")
    budget = asset_manifest.get("rom_budget", {})
    if (
        budget.get("payload_bytes") != payload_total
        or budget.get("four_byte_aligned_blob_bytes") != aligned_total
        or budget.get("bytes_per_glyph") != 69
    ):
        raise CjkFontError("compact asset ROM budget drifted")
    if lock:
        _validate_fehrr_priority(
            root,
            asset_manifest,
            lock,
            report,
            jobs,
            corpora,
            payloads,
        )
        outputs[FEHRR_SOURCES] = (root / FEHRR_SOURCES).read_bytes()
    outputs[f"{ASSET_ROOT}/manifest.json"] = asset_manifest_data
    return outputs
