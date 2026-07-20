"""Byte-exact proof that struct ExpansionSaveMeta's checksummed padding is
deterministically zeroed (issue #2 slice 1, review fix #3).

`BuildCurrentExpansionSaveMeta()` (src/bmsave-lib.c) previously populated
only the named fields, leaving every `STRUCT_PAD()` alignment byte inside
`EXPANSION_SAVE_META_SIZE_FOR_CHECKSUM`'s checksum domain holding whatever
was already on the stack -- making the checksummed bytes (and therefore
the whole metadata record) non-deterministic across builds/calls. The fix
zeroes the entire struct with `memset()` before setting any field.

This module extracts the *real* function/struct definitions verbatim from
src/bmsave-lib.c, src/bmlib.c, and include/save_format.h (not a
hand-retyped copy -- so this test automatically tracks any future edit to
the real source instead of silently drifting out of sync), assembles them
into a small host-native (not agbcc/ARM) C program, actually compiles and
*executes* it with the host's own `cc`, and compares the raw bytes it
produces -- including every pad/reserved byte -- against
scripts/modernize/save_format_tool.py's already byte-exact
struct.Struct(...) packing for the same input field values. The probe is
run twice to prove the output is bit-for-bit repeatable (the specific
property the uninitialized-padding bug violated).
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "modernize"))

import save_format_tool as sft  # noqa: E402


def _extract_c_function(text: str, name: str) -> str:
    """Extracts one C function's full definition (signature through its
    matching top-level closing brace) verbatim from `text`, by locating
    `name(` and then brace-counting -- robust to nested blocks, unlike a
    naive non-greedy regex."""
    decl_match = re.search(r"\n[\w][^\n;]*\b" + re.escape(name) + r"\s*\(", text)
    if decl_match is None:
        raise AssertionError(f"could not locate a definition of {name!r}")

    start = decl_match.start() + 1  # skip the leading \n
    brace_open = text.index("{", decl_match.end())

    depth = 0
    i = brace_open
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1

    raise AssertionError(f"unbalanced braces while extracting {name!r}")


def _extract_struct(text: str, name: str) -> str:
    decl_match = re.search(r"\nstruct " + re.escape(name) + r"\s*\{", text)
    if decl_match is None:
        raise AssertionError(f"could not locate struct {name!r}")
    start = decl_match.start() + 1
    end = text.index("};", decl_match.end()) + 2
    return text[start:end]


class BuildCurrentExpansionSaveMetaNativeByteTests(unittest.TestCase):
    """Compiles+runs the real C BuildCurrentExpansionSaveMeta() natively
    (host cc, not agbcc/ARM) and compares its raw output bytes -- pad
    bytes included -- to the Python mirror's struct.pack() for the same
    controlled inputs."""

    @classmethod
    def setUpClass(cls):
        cc = "cc"
        try:
            subprocess.run([cc, "--version"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, check=True)
        except (OSError, subprocess.CalledProcessError):
            raise unittest.SkipTest("no host 'cc' compiler available")
        cls.cc = cc

        cls.save_format_h = (ROOT / "include" / "save_format.h").read_text(encoding="utf-8")
        cls.bmsave_lib_c = (ROOT / "src" / "bmsave-lib.c").read_text(encoding="utf-8")
        cls.bmlib_c = (ROOT / "src" / "bmlib.c").read_text(encoding="utf-8")

    def _build_and_run_probe(self, defines):
        """Assembles the real struct + real function bodies (extracted
        verbatim above) into a standalone, host-compilable C program,
        compiles it with the given -D overrides for the FE8_EXPANSION_*
        diagnostic macros, executes it, and returns the raw stdout bytes
        (the 0x5C-byte ExpansionSaveMeta record)."""
        struct_def = _extract_struct(self.save_format_h, "ExpansionSaveMeta")
        string_compare_fn = _extract_c_function(self.bmlib_c, "StringCompare")
        checksum16_fn = _extract_c_function(self.bmsave_lib_c, "Checksum16")
        copy_string_bounded_fn = _extract_c_function(self.bmsave_lib_c, "CopyStringBounded")
        build_meta_fn = _extract_c_function(self.bmsave_lib_c, "BuildCurrentExpansionSaveMeta")
        checksum_fn = _extract_c_function(self.bmsave_lib_c, "ExpansionSaveMetaChecksum")

        probe_source = f"""\
#include <stdint.h>
#include <string.h>
#include <stdio.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int8_t s8;
typedef s8 bool;
enum {{ false, true }};

#define STRUCT_PAD(from, to) unsigned char _pad_ ## from[(to) - (from)]

#define EXPANSION_SAVE_META_MAGIC "FSAV"
#define EXPANSION_SAVE_META_MAGIC_SIZE 4
#define SAVE_FORMAT_VERSION_CURRENT 1
#define EXPANSION_SAVE_META_SIZE_FOR_CHECKSUM 0x2E

enum SaveAbiId {{
    SAVE_ABI_ID_APCS_GNU = 0,
    SAVE_ABI_ID_AAPCS = 1
}};

{struct_def};

#include "expansion_config.h"

{string_compare_fn}

{checksum16_fn}

{copy_string_bounded_fn}

{checksum_fn}

{build_meta_fn}

int main(void)
{{
    struct ExpansionSaveMeta meta;
    BuildCurrentExpansionSaveMeta(&meta);
    fwrite(&meta, sizeof(meta), 1, stdout);
    return 0;
}}
"""

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "probe.c"
            binary = tmp_path / "probe"
            source.write_text(probe_source, encoding="utf-8")

            compile_cmd = [
                self.cc,
                "-iquote", str(ROOT / "include"),
                "-std=c99",
                *defines,
                str(source), "-o", str(binary),
            ]
            compile_result = subprocess.run(
                compile_cmd, cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(
                compile_result.returncode, 0,
                f"native probe failed to compile:\n{compile_result.stdout}\n\n"
                f"--- generated source ---\n{probe_source}",
            )

            run_result = subprocess.run(
                [str(binary)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(run_result.returncode, 0, "native probe crashed")
            return run_result.stdout

    def test_native_c_output_matches_python_pack_byte_for_byte(self):
        defines = [
            "-DFE8_EXPANSION_SAVE_COMPAT_EPOCH=42",
            "-DFE8_EXPANSION_ABI=\"aapcs\"",
            "-DFE8_EXPANSION_VERSION_PACKED=0x010203u",
            "-DFE8_EXPANSION_CONFIG_FINGERPRINT=\"1234567890abcdef\"",
            "-DFE8_EXPANSION_BUILD_COMMIT=\"deadbeefcafebabe1234\"",
        ]
        c_bytes = self._build_and_run_probe(defines)

        py_meta = sft.ExpansionSaveMeta(
            magic=sft.META_MAGIC,
            format_version=sft.SAVE_FORMAT_VERSION_CURRENT,
            compat_epoch=42,
            abi_id=sft.SAVE_ABI_ID_AAPCS,
            framework_version_packed=0x010203,
            config_fingerprint=b"1234567890abcdef\x00",
            build_commit_short=(b"deadbeefcafebabe1234"[:8] + b"\x00" * 9)[:9],
            checksum=0,
            reserved=b"\x00" * (sft.META_SIZE - sft.META_CHECKSUM_DOMAIN - 2),
        )
        py_meta.checksum = py_meta.computed_checksum()
        py_bytes = py_meta.pack()

        self.assertEqual(len(c_bytes), sft.META_SIZE)
        self.assertEqual(
            c_bytes, py_bytes,
            f"C probe output {c_bytes.hex()} != Python pack {py_bytes.hex()}",
        )

        # Every STRUCT_PAD() byte inside the checksum domain must be
        # exactly zero, not leftover stack garbage -- the actual bug.
        pad_offsets = [0x05, 0x09, 0x0A, 0x0B, 0x21, 0x22, 0x23, 0x2D]
        for offset in pad_offsets:
            self.assertEqual(
                c_bytes[offset], 0,
                f"pad byte at offset 0x{offset:02X} was not deterministically zeroed",
            )

    def test_native_c_output_is_repeatable_across_builds(self):
        """The exact property the bug violated: rebuilding/re-running must
        yield bit-for-bit identical output, proving no uninitialized
        stack/heap memory leaks into the checksummed record."""
        defines = [
            "-DFE8_EXPANSION_SAVE_COMPAT_EPOCH=7",
            "-DFE8_EXPANSION_ABI=\"apcs-gnu\"",
            "-DFE8_EXPANSION_VERSION_PACKED=0x020304u",
            "-DFE8_EXPANSION_CONFIG_FINGERPRINT=\"abcdefabcdefabcd\"",
            "-DFE8_EXPANSION_BUILD_COMMIT=\"0011223344556677\"",
        ]
        first = self._build_and_run_probe(defines)
        second = self._build_and_run_probe(defines)
        third = self._build_and_run_probe(defines)
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual(len(first), sft.META_SIZE)


if __name__ == "__main__":
    unittest.main()
