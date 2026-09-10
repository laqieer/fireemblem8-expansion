from __future__ import annotations

import base64
import binascii
import re
import struct
import zlib

if __package__:
    from .authority import relative_path
    from .budget import MakeProbeError
else:
    from authority import relative_path
    from budget import MakeProbeError


METADATA_CALLS = {4, 5, 6, 21, 78, 89, 138, 217, 262, 267, 269, 332, 439}
METADATA_HEADER = struct.Struct("<IIIQQqIII")
TRANSPORT_FORMAT = "vo-metadata-frame"
TRANSPORT_VERSION = 1
TRANSPORT_ENCODING = "zlib-base64"
HEX_DECODE_SCRATCH_BYTES = 4096
_TRANSPORT_FIELDS = {"format", "version", "encoding", "record_count", "decoded_size", "payload"}
_HEX_RE = re.compile(r"(?:[0-9a-f]{2})*")
_HEX_NIBBLES = {ord(ch): value for value, ch in enumerate("0123456789abcdef")}


def validate_legacy_metadata_records(value, limit, *, runtime_paths=(), runtime_absent=()):
    if not isinstance(value, list) or len(value) > limit:
        raise MakeProbeError("malformed/excessive guest metadata records")
    records = []
    seen = set()
    for record in value:
        if not isinstance(record, list) or len(record) != 9:
            raise MakeProbeError("malformed guest metadata record")
        number, path, flags, mask, size, offset, result, before, after = record
        if (
            type(number) is not int or number not in METADATA_CALLS
            or not isinstance(path, str) or not (
                path == "/repo" or path.startswith("/repo/") or path in runtime_paths
                or any(path.startswith(absent + "/") for absent in runtime_absent)
            )
            or not path.startswith("/") or path != "/" and relative_path(path[1:]) != path[1:]
            or any(type(item) is not int or not 0 <= item < 1 << 32 for item in (flags, mask))
            or any(type(item) is not int or not 0 <= item < 1 << 64 for item in (size, offset))
            or type(result) is not int or not -(1 << 63) <= result < 1 << 63
        ):
            raise MakeProbeError("malformed guest metadata operation")
        for data in (before, after):
            if data is not None and (
                not isinstance(data, str) or len(data) != 2*size
                or size > 65536 or _HEX_RE.fullmatch(data) is None
            ):
                raise MakeProbeError("malformed guest metadata buffer")
        fixed = {4: 144, 5: 144, 6: 144, 21: 0, 138: 120, 262: 144, 269: 0, 332: 256, 439: 0}
        if number in fixed and size != fixed[number] or number not in {78, 217} and offset:
            raise MakeProbeError("guest metadata disagrees with its syscall ABI")
        canonical = tuple(record)
        if canonical in seen:
            raise MakeProbeError("duplicate guest metadata record")
        seen.add(canonical)
        records.append(canonical)
    return tuple(records)


def _decode_hex_chunk_into(data: str, scratch: bytearray) -> memoryview:
    size = len(data) // 2
    if size > len(scratch):
        raise MakeProbeError("metadata transport scratch chunk exceeded its bound")
    for index in range(size):
        hi = _HEX_NIBBLES.get(ord(data[2*index]))
        lo = _HEX_NIBBLES.get(ord(data[2*index + 1]))
        if hi is None or lo is None:
            raise MakeProbeError("malformed guest metadata buffer")
        scratch[index] = (hi << 4) | lo
    return memoryview(scratch)[:size]


def _visit_metadata_frame_parts(records, visitor) -> int:
    scratch = bytearray(HEX_DECODE_SCRATCH_BYTES)
    decoded_size = 4
    visitor(struct.pack("<I", len(records)))
    for record in records:
        if not isinstance(record, (list, tuple)) or len(record) != 9:
            raise MakeProbeError("malformed guest metadata record")
        number, path, flags, mask, size, offset, result, before, after = record
        if not isinstance(path, str):
            raise MakeProbeError("malformed guest metadata operation")
        try:
            name = path.encode("utf-8")
        except UnicodeEncodeError as error:
            raise MakeProbeError("malformed guest metadata operation") from error
        header = METADATA_HEADER.pack(
            number, flags, mask, size, offset, result, len(name),
            0xFFFFFFFF if before is None else len(before) // 2,
            0xFFFFFFFF if after is None else len(after) // 2,
        )
        visitor(header)
        visitor(name)
        decoded_size += len(header) + len(name)
        for data in (before, after):
            if data is None:
                continue
            decoded_size += len(data) // 2
            for start in range(0, len(data), 2*len(scratch)):
                visitor(_decode_hex_chunk_into(data[start:start + 2*len(scratch)], scratch))
    return decoded_size


def metadata_frame(records) -> bytes:
    data = bytearray()
    _visit_metadata_frame_parts(records, data.extend)
    return bytes(data)


def _push_base64(parts: list[str], carry: bytes, data: bytes, *, final: bool) -> bytes:
    combined = carry + data
    if final:
        if combined:
            parts.append(base64.b64encode(combined).decode("ascii"))
        return b""
    used = len(combined) // 3 * 3
    if used:
        parts.append(base64.b64encode(combined[:used]).decode("ascii"))
    return combined[used:]


def encode_metadata_transport(records) -> dict[str, object]:
    payload_parts: list[str] = []
    compressor = zlib.compressobj()
    carry = b""

    def visitor(chunk):
        nonlocal carry
        raw = bytes(chunk) if isinstance(chunk, memoryview) else chunk
        compressed = compressor.compress(raw)
        if compressed:
            carry = _push_base64(payload_parts, carry, compressed, final=False)

    decoded_size = _visit_metadata_frame_parts(records, visitor)
    carry = _push_base64(payload_parts, carry, compressor.flush(), final=True)
    if carry:
        raise MakeProbeError("metadata transport encoder retained unexpected base64 tail")
    return {
        "format": TRANSPORT_FORMAT,
        "version": TRANSPORT_VERSION,
        "encoding": TRANSPORT_ENCODING,
        "record_count": len(records),
        "decoded_size": decoded_size,
        "payload": "".join(payload_parts),
    }


def _transport_header(value, limit, decoded_limit):
    if not isinstance(value, dict) or set(value) != _TRANSPORT_FIELDS:
        raise MakeProbeError("malformed guest metadata transport")
    if value["format"] != TRANSPORT_FORMAT or value["encoding"] != TRANSPORT_ENCODING:
        raise MakeProbeError("unknown guest metadata transport")
    if type(value["version"]) is not int or value["version"] != TRANSPORT_VERSION:
        raise MakeProbeError("unknown guest metadata transport")
    if type(value["record_count"]) is not int or not 0 <= value["record_count"] <= limit:
        raise MakeProbeError("malformed/excessive guest metadata records")
    if (
        type(value["decoded_size"]) is not int
        or not 4 <= value["decoded_size"] <= decoded_limit
    ):
        raise MakeProbeError("guest metadata transport exceeds its byte bound")
    if not isinstance(value["payload"], str):
        raise MakeProbeError("malformed guest metadata transport")
    try:
        value["payload"].encode("ascii")
    except UnicodeEncodeError as error:
        raise MakeProbeError("malformed guest metadata transport") from error
    return value["record_count"], value["decoded_size"], value["payload"]


def _decode_payload(payload: str, decoded_size: int) -> bytes:
    if len(payload) % 4:
        raise MakeProbeError("malformed guest metadata transport")
    frame = bytearray(decoded_size)
    written = 0
    decoder = zlib.decompressobj()
    for start in range(0, len(payload), 8192):
        chunk = payload[start:start + 8192]
        if start + 8192 < len(payload) and "=" in chunk:
            raise MakeProbeError("malformed guest metadata transport")
        try:
            compressed = base64.b64decode(chunk, validate=True)
        except (binascii.Error, ValueError) as error:
            raise MakeProbeError("malformed guest metadata transport") from error
        if decoder.eof and compressed:
            raise MakeProbeError("trailing guest metadata transport data")
        while compressed:
            piece = decoder.decompress(compressed, decoded_size - written)
            end = written + len(piece)
            frame[written:end] = piece
            written = end
            compressed = decoder.unconsumed_tail
            if compressed and written == decoded_size:
                raise MakeProbeError("guest metadata transport size mismatch")
        if decoder.unused_data:
            raise MakeProbeError("trailing guest metadata transport data")
    remaining = decoded_size - written
    try:
        tail = decoder.flush() if remaining == 0 else decoder.flush(remaining)
    except zlib.error as error:
        raise MakeProbeError("invalid guest metadata transport zlib stream") from error
    end = written + len(tail)
    if end > decoded_size:
        raise MakeProbeError("guest metadata transport size mismatch")
    frame[written:end] = tail
    if (
        not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
        or end != decoded_size
    ):
        raise MakeProbeError("incomplete or trailing guest metadata transport stream")
    return bytes(frame)


def _read_u32(frame: bytes, cursor: int) -> tuple[int, int]:
    if cursor + 4 > len(frame):
        raise MakeProbeError("truncated guest metadata frame")
    return struct.unpack_from("<I", frame, cursor)[0], cursor + 4


def _read_u64(frame: bytes, cursor: int) -> tuple[int, int]:
    if cursor + 8 > len(frame):
        raise MakeProbeError("truncated guest metadata frame")
    return struct.unpack_from("<Q", frame, cursor)[0], cursor + 8


def _decode_metadata_frame(frame: bytes, record_count: int):
    count, cursor = _read_u32(frame, 0)
    if count != record_count:
        raise MakeProbeError("guest metadata record count mismatch")
    if count > 32768:
        raise MakeProbeError("malformed/excessive guest metadata records")
    records = []
    for _ in range(count):
        if cursor + METADATA_HEADER.size > len(frame):
            raise MakeProbeError("truncated guest metadata frame")
        number, flags, mask, size, offset, result, path_size, before_size, after_size = (
            METADATA_HEADER.unpack_from(frame, cursor)
        )
        cursor += METADATA_HEADER.size
        if (
            path_size < 1 or path_size > 4096
            or cursor + path_size > len(frame)
            or frame[cursor] != ord("/")
            or 0 in frame[cursor:cursor + path_size]
        ):
            raise MakeProbeError("malformed guest metadata frame")
        path_bytes = frame[cursor:cursor + path_size]
        cursor += path_size
        try:
            path = path_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MakeProbeError("malformed guest metadata frame") from error
        buffers = []
        for encoded_size in (before_size, after_size):
            if encoded_size == 0xFFFFFFFF:
                buffers.append(None)
                continue
            if encoded_size != size or cursor + encoded_size > len(frame):
                raise MakeProbeError("malformed guest metadata frame")
            buffers.append(frame[cursor:cursor + encoded_size].hex())
            cursor += encoded_size
        records.append([number, path, flags, mask, size, offset, result, *buffers])
    if cursor != len(frame):
        raise MakeProbeError("trailing guest metadata frame data")
    return records


def decode_metadata_transport(
    value,
    limit,
    *,
    decoded_limit,
    runtime_paths=(),
    runtime_absent=(),
    reserve=None,
):
    record_count, decoded_size, payload = _transport_header(value, limit, decoded_limit)
    if reserve is not None:
        reserve(decoded_size)
    frame = _decode_payload(payload, decoded_size)
    records = _decode_metadata_frame(frame, record_count)
    return validate_legacy_metadata_records(
        records, limit, runtime_paths=runtime_paths, runtime_absent=runtime_absent,
    )
