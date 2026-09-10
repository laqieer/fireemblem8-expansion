from __future__ import annotations

import base64
import random
import struct
import tracemalloc
import unittest
import zlib
from unittest.mock import patch

from scripts.validation_ownership import metadata_transport
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from scripts.validation_ownership.metadata_transport import (
    HEX_DECODE_SCRATCH_BYTES,
    TRANSPORT_ENCODING,
    TRANSPORT_FORMAT,
    TRANSPORT_VERSION,
    decode_metadata_transport,
    encode_metadata_transport,
    metadata_frame,
)


class MetadataTransportTests(unittest.TestCase):
    def legacy_records(self):
        return [
            [4, "/repo/data/value", 0, 0, 144, 0, 0, "a5" * 144, "5a" * 144],
            [217, "/repo/data", 0, 0, 32, 7, 12, "11" * 32, "22" * 32],
            [21, "/repo/data/value", 4, 0, 0, 0, -13, "", ""],
        ]

    def encode(self, records=None):
        if records is None:
            records = self.legacy_records()
        return encode_metadata_transport(records)

    def decode(self, envelope, *, records=None, decoded_limit=None, reserve=None, runtime_paths=()):
        if records is None:
            records = self.legacy_records()
        if decoded_limit is None:
            decoded_limit = len(metadata_frame(records))
        return decode_metadata_transport(
            envelope, len(records), decoded_limit=decoded_limit, reserve=reserve, runtime_paths=runtime_paths,
        )

    def test_round_trip_preserves_legacy_records_and_exact_frame(self):
        records = self.legacy_records()
        envelope = self.encode(records)
        frame = metadata_frame(records)
        reserved = []
        self.assertEqual(
            envelope,
            {
                "format": TRANSPORT_FORMAT,
                "version": TRANSPORT_VERSION,
                "encoding": TRANSPORT_ENCODING,
                "record_count": len(records),
                "decoded_size": len(frame),
                "payload": envelope["payload"],
            },
        )
        self.assertEqual(zlib.decompress(base64.b64decode(envelope["payload"])), frame)
        self.assertEqual(self.decode(envelope, records=records, reserve=reserved.append), tuple(map(tuple, records)))
        self.assertEqual(reserved, [len(frame), len(envelope["payload"])])

    def test_control_exhaustion_rejects_before_decoder_allocation(self):
        envelope = self.encode()
        frame_size = envelope["decoded_size"]
        total = frame_size + len(envelope["payload"])
        for control_limit in (frame_size - 1, total - 1, total):
            with self.subTest(control_limit=control_limit):
                budget = ProbeBudget(Limits(control_bytes=control_limit))
                with patch.object(
                    metadata_transport, "_decode_payload", wraps=metadata_transport._decode_payload,
                ) as decode:
                    if control_limit < total:
                        with self.assertRaisesRegex(MakeProbeError, "control byte budget exhausted"):
                            self.decode(envelope, reserve=lambda size: budget.charge("control", size))
                        decode.assert_not_called()
                        self.assertTrue(budget.failed)
                    else:
                        self.assertEqual(
                            self.decode(envelope, reserve=lambda size: budget.charge("control", size)),
                            tuple(map(tuple, self.legacy_records())),
                        )
                        decode.assert_called_once()
                        self.assertEqual(budget.bytes["control"], total)

    def test_decode_rejects_unknown_transport_shape_and_header_types(self):
        envelope = self.encode()
        for mutated in (
            {**envelope, "extra": 1},
            {**envelope, "format": "other"},
            {**envelope, "version": 2},
            {**envelope, "encoding": "plain"},
            {**envelope, "record_count": True},
            {**envelope, "decoded_size": True},
            {**envelope, "payload": b"ascii"},
        ):
            with self.subTest(mutated=mutated):
                with self.assertRaisesRegex(MakeProbeError, "metadata transport|metadata records"):
                    self.decode(mutated)

    def test_decode_rejects_invalid_base64_incomplete_and_trailing_zlib(self):
        envelope = self.encode()
        broken_base64 = {**envelope, "payload": "%"}
        with self.assertRaisesRegex(MakeProbeError, "metadata transport"):
            self.decode(broken_base64)

        compressed = zlib.compress(metadata_frame(self.legacy_records()))
        truncated = {**envelope, "payload": base64.b64encode(compressed[:-1]).decode("ascii")}
        with self.assertRaisesRegex(MakeProbeError, "invalid guest metadata transport zlib stream|incomplete"):
            self.decode(truncated)

        trailing = {
            **envelope,
            "payload": base64.b64encode(compressed + b"trail").decode("ascii"),
        }
        with self.assertRaisesRegex(MakeProbeError, "trailing guest metadata transport data|incomplete"):
            self.decode(trailing)

        concatenated = {
            **envelope,
            "payload": base64.b64encode(compressed + zlib.compress(b"extra")).decode("ascii"),
        }
        with self.assertRaisesRegex(MakeProbeError, "trailing guest metadata transport data|incomplete"):
            self.decode(concatenated)

    def test_decode_rejects_non_zlib_payload_as_protocol_error(self):
        envelope = {**self.encode(), "payload": base64.b64encode(b"not-zlib").decode("ascii")}
        with self.assertRaisesRegex(MakeProbeError, "metadata transport"):
            self.decode(envelope)

    def stored_stream(self, prefix, suffix):
        empty_blocks, remainder = divmod(6144 - 7 - len(prefix), 5)
        self.assertEqual(remainder, 0)
        return (
            b"\x78\x01" + b"\x00\x00\x00\xff\xff" * empty_blocks
            + b"\x00" + struct.pack("<HH", len(prefix), 65535 - len(prefix)) + prefix
            + b"\x01" + struct.pack("<HH", len(suffix), 65535 - len(suffix)) + suffix
            + struct.pack(">I", zlib.adler32(prefix + suffix))
        )

    def test_decode_bomb_never_grows_reserved_storage(self):
        prefix, suffix = b"\0" * 7, b"x" * 10000
        compressed = self.stored_stream(prefix, suffix)
        self.assertEqual(zlib.decompress(compressed), prefix + suffix)
        envelope = {
            **self.encode([]), "decoded_size": len(prefix),
            "payload": base64.b64encode(compressed).decode("ascii"),
        }
        allocations = []
        case = self

        class ReservedFrame(bytearray):
            def __init__(self, size):
                super().__init__(size)
                allocations.append(size)

            def __setitem__(self, key, value):
                if isinstance(key, slice):
                    start, stop, step = key.indices(len(self))
                    case.assertEqual(step, 1)
                    case.assertEqual(stop - start, len(value), "decode resized its reserved frame")
                super().__setitem__(key, value)

        with patch.object(metadata_transport, "bytearray", ReservedFrame, create=True):
            with self.assertRaisesRegex(MakeProbeError, "size mismatch"):
                self.decode(envelope, records=[], decoded_limit=len(prefix))
        self.assertEqual(allocations, [len(prefix)])

    def test_decode_accepts_split_footer_without_additional_output(self):
        records = [[21, "/repo", 0, 0, 0, 0, 0, "", ""]]
        frame = metadata_frame(records)
        compressed = self.stored_stream(frame, b"")
        self.assertEqual(zlib.decompress(compressed), frame)
        envelope = {
            **self.encode(records),
            "payload": base64.b64encode(compressed).decode("ascii"),
        }
        self.assertEqual(self.decode(envelope, records=records), tuple(map(tuple, records)))

    def test_decode_keeps_only_one_full_frame_allocation(self):
        frame = b"x" * (1 << 20)
        payload = base64.b64encode(zlib.compress(frame)).decode("ascii")
        tracemalloc.start()
        try:
            decoded = metadata_transport._decode_payload(payload, len(frame))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(decoded, frame)
        self.assertLess(peak, len(frame) + len(frame) // 2)

    def test_encoder_passes_reused_scratch_directly_to_compressor(self):
        size = 2 * HEX_DECODE_SCRATCH_BYTES
        records = [[217, "/repo", 0, 0, size, 0, size, "a5" * size, "5a" * size]]
        compressor = zlib.compressobj()
        owners = []
        case = self

        class ObservedCompressor:
            def compress(self, chunk):
                if len(chunk) == HEX_DECODE_SCRATCH_BYTES:
                    case.assertTrue(isinstance(chunk, memoryview), "compressor received copied scratch")
                    owners.append(id(chunk.obj))
                return compressor.compress(chunk)

            def flush(self):
                return compressor.flush()

        with patch.object(metadata_transport.zlib, "compressobj", return_value=ObservedCompressor()):
            envelope = self.encode(records)
        self.assertEqual(len(owners), 4)
        self.assertEqual(len(set(owners)), 1)
        self.assertEqual(zlib.decompress(base64.b64decode(envelope["payload"])), metadata_frame(records))
        self.assertEqual(self.decode(envelope, records=records), tuple(map(tuple, records)))

    def test_encoder_bounds_base64_parts_for_incompressible_buffers(self):
        data = random.Random(240).randbytes(65536)
        records = [[217, "/repo", 0, 0, len(data), 0, 0, data.hex(), data[::-1].hex()]]
        encode = base64.b64encode
        sizes = []

        def observed(chunk):
            sizes.append(len(chunk))
            return encode(chunk)

        with patch.object(metadata_transport.base64, "b64encode", side_effect=observed):
            envelope = self.encode(records)
        self.assertGreater(len(sizes), 2)
        self.assertLessEqual(max(sizes), HEX_DECODE_SCRATCH_BYTES // 3 * 3)
        self.assertEqual(zlib.decompress(base64.b64decode(envelope["payload"])), metadata_frame(records))
        self.assertEqual(self.decode(envelope, records=records), tuple(map(tuple, records)))

    def test_decode_rejects_decoded_size_record_count_and_limit_mismatches(self):
        records = self.legacy_records()
        envelope = self.encode(records)
        frame = metadata_frame(records)
        reserved = []
        too_small_limit = len(frame) - 1
        with self.assertRaisesRegex(MakeProbeError, "exceeds its byte bound"):
            self.decode(envelope, records=records, decoded_limit=too_small_limit, reserve=reserved.append)
        self.assertEqual(reserved, [])

        wrong_size = {**envelope, "decoded_size": len(frame) - 1}
        with self.assertRaisesRegex(MakeProbeError, "size mismatch|incomplete"):
            self.decode(wrong_size, records=records)

        wrong_count = {**envelope, "record_count": len(records) - 1}
        with self.assertRaisesRegex(MakeProbeError, "record count mismatch|metadata records"):
            self.decode(wrong_count, records=records)

    def test_decode_rejects_duplicate_runtime_and_abi_violations(self):
        records = self.legacy_records()
        duplicate = self.encode([records[0], records[0]])
        with self.assertRaisesRegex(MakeProbeError, "duplicate guest metadata record"):
            decode_metadata_transport(duplicate, 2, decoded_limit=len(metadata_frame([records[0], records[0]])))

        runtime = self.encode([[4, "/runtime/data", 0, 0, 144, 0, 0, "a5" * 144, "5a" * 144]])
        with self.assertRaisesRegex(MakeProbeError, "malformed guest metadata operation"):
            decode_metadata_transport(runtime, 1, decoded_limit=len(metadata_frame([[4, "/runtime/data", 0, 0, 144, 0, 0, "a5" * 144, "5a" * 144]])))

        invalid_runtime = decode_metadata_transport(
            runtime, 1,
            decoded_limit=len(metadata_frame([[4, "/runtime/data", 0, 0, 144, 0, 0, "a5" * 144, "5a" * 144]])),
            runtime_paths={"/runtime/data"},
        )
        self.assertEqual(invalid_runtime[0][1], "/runtime/data")

        wrong_abi = self.encode([[4, "/repo/data/value", 0, 0, 143, 0, 0, "a5" * 143, "5a" * 143]])
        with self.assertRaisesRegex(MakeProbeError, "guest metadata disagrees with its syscall ABI"):
            decode_metadata_transport(wrong_abi, 1, decoded_limit=len(metadata_frame([[4, "/repo/data/value", 0, 0, 143, 0, 0, "a5" * 143, "5a" * 143]])))

    def test_decode_rejects_truncated_noncanonical_and_tampered_frames(self):
        records = self.legacy_records()
        frame = bytearray(metadata_frame(records))
        envelope = self.encode(records)

        truncated = {**envelope, "payload": base64.b64encode(zlib.compress(frame[:-1])).decode("ascii"),
                     "decoded_size": len(frame) - 1}
        with self.assertRaisesRegex(MakeProbeError, "truncated guest metadata frame|record count mismatch|malformed guest metadata frame"):
            self.decode(truncated, records=records, decoded_limit=len(frame) - 1)

        tampered_path = bytearray(frame)
        tampered_path[52] = ord("x")
        path_envelope = {**envelope, "payload": base64.b64encode(zlib.compress(tampered_path)).decode("ascii")}
        with self.assertRaisesRegex(MakeProbeError, "malformed guest metadata frame|malformed guest metadata operation"):
            self.decode(path_envelope, records=records)

        tampered_trailing = {
            **envelope,
            "decoded_size": len(frame) + 1,
            "payload": base64.b64encode(zlib.compress(frame + b"!")).decode("ascii"),
        }
        with self.assertRaisesRegex(MakeProbeError, "trailing guest metadata frame data"):
            self.decode(tampered_trailing, records=records, decoded_limit=len(frame) + 1)
