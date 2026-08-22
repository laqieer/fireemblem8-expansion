#!/usr/bin/env python3
"""Semantic host and libmGBA checks for the optional HQ MP2K mixer."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gba_playtest
from probe_bindings import ElfSymbolResolver, ProbeBindingError, resolve_elf_symbol
from scripts.linker_report import budget

HQ_NO_REVERB_CODE_BYTES = 0xAC0
HQ_MIX_BUFFER_BYTES = 0x380
HQ_EWRAM_BOOKKEEPING_BYTES = 0x418
SOUND_INFO_PCM_BUFFER_OFFSET = 0x350
PCM_CHANNEL_STRIDE = 0x630
PCM_SAMPLE_OFFSETS = (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xD0)
RUNTIME_SCENARIOS = {
    "debug": (
        ROOT / "tools/gba-playtest/scenarios/starter-hook-modern-debug.json",
        (120, 600, 3285, 3290, 3295, 3300, 3305, 3310, 3315, 3320),
    ),
    "release": (
        ROOT / "tools/gba-playtest/scenarios/starter-hook-clean-modern-release.json",
        (120, 600, 3280, 3290, 3300, 3310, 3320, 3330, 3340, 3450, 3550),
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enabled-rom", type=Path, required=True)
    parser.add_argument("--enabled-elf", type=Path, required=True)
    parser.add_argument("--enabled-map", type=Path, required=True)
    parser.add_argument("--disabled-rom", type=Path, required=True)
    parser.add_argument("--disabled-elf", type=Path, required=True)
    parser.add_argument("--disabled-map", type=Path, required=True)
    parser.add_argument("--config", choices=("debug", "release"), required=True)
    parser.add_argument("--nm", default=os.environ.get("MODERN_NM", "arm-none-eabi-nm"))
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser.parse_args()


def fail(message: str) -> None:
    raise RuntimeError(message)


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        fail(f"{label} is missing: {path}")


def require_symbol(resolver: ElfSymbolResolver, symbol: str) -> tuple[int, int]:
    try:
        return resolver(symbol)
    except ProbeBindingError as error:
        fail(str(error))
    raise AssertionError("unreachable")


def require_absent_symbol(elf: Path, symbol: str, nm: str) -> None:
    try:
        address, size = resolve_elf_symbol(elf, symbol, nm)
    except ProbeBindingError:
        return
    if address == 0 and size == 0:
        return
    fail(f"{symbol} must be absent from disabled ELF {elf}")


def check_budget(map_path: Path, elf_path: Path, enabled: bool) -> dict:
    regions, sections, assignments = budget.parse_map(map_path.read_text(encoding="utf-8"))
    elf_sections = budget.parse_elf_sections(str(elf_path))
    report = budget.generate_report(regions, sections, assignments, elf_sections)
    cross_validation = budget.elf_cross_validation_errors(report, require_available=True)
    if cross_validation:
        fail(f"{elf_path} does not match {map_path}: {', '.join(cross_validation)}")
    headroom_errors = budget.positive_headroom_errors(report, ("ewram", "iwram"))
    if headroom_errors:
        fail(f"{map_path} fails memory headroom validation: {'; '.join(headroom_errors)}")
    iwram = next((region for region in report["regions"] if region["name"] == "iwram"), None)
    if iwram is None:
        fail(f"{map_path} has no IWRAM report")
    if not iwram["static_budget_available"]:
        fail(f"{map_path} lacks linker-defined IWRAM stack-budget symbols")
    if iwram["minimum_user_stack_margin_bytes"] != 0x1000:
        fail(
            f"{map_path} changed the minimum user stack margin to "
            f"0x{iwram['minimum_user_stack_margin_bytes']:x}"
        )
    if iwram["usable_static_headroom_bytes"] < 0x1000:
        fail(
            f"{map_path} leaves only 0x{iwram['usable_static_headroom_bytes']:x} "
            "bytes below the user stack"
        )
    if iwram["stack_margin_violation"]:
        fail(f"{map_path} violates the linker-defined user stack floor")
    if enabled and iwram["static_growth_headroom_bytes"] != 0:
        fail(
            f"{map_path} expected the HQ mixer to consume the declared IWRAM "
            f"growth headroom, got 0x{iwram['static_growth_headroom_bytes']:x}"
        )
    ewram = next((region for region in report["regions"] if region["name"] == "ewram"), None)
    if ewram is None:
        fail(f"{map_path} has no EWRAM report")
    return {"iwram": iwram, "ewram": ewram}


def check_link_selection(enabled_elf: Path, disabled_elf: Path, nm: str) -> dict:
    enabled = ElfSymbolResolver(enabled_elf, nm)
    code, code_size = require_symbol(enabled, "SoundMainRAM")
    code_end, _ = require_symbol(enabled, "SoundMainRAM_End")
    code_buffer, code_buffer_size = require_symbol(enabled, "SoundMainRAM_Buffer")
    mix_buffer, mix_buffer_size = require_symbol(enabled, "SoundMainRAM_MixBuffer")
    probe, probe_size = require_symbol(enabled, "gExpansionHqMixerProbe")

    if not 0x08000000 <= code < 0x0A000000:
        fail(f"HQ SoundMainRAM is not linked in ROM: 0x{code:08x}")
    if (
        code_size != HQ_NO_REVERB_CODE_BYTES
        or code_end - code != HQ_NO_REVERB_CODE_BYTES
    ):
        fail(
            f"HQ SoundMainRAM extent is 0x{code_size:x}/0x{code_end - code:x}, "
            f"expected no-reverb profile size 0x{HQ_NO_REVERB_CODE_BYTES:x}"
        )
    if not 0x03000000 <= code_buffer < 0x03008000:
        fail(f"HQ code buffer is not in IWRAM: 0x{code_buffer:08x}")
    if code_buffer_size != HQ_NO_REVERB_CODE_BYTES:
        fail(
            f"HQ code buffer size is 0x{code_buffer_size:x}, "
            f"expected no-reverb profile size 0x{HQ_NO_REVERB_CODE_BYTES:x}"
        )
    if not 0x03000000 <= mix_buffer < 0x03008000:
        fail(f"HQ intermediate buffer is not in IWRAM: 0x{mix_buffer:08x}")
    if mix_buffer % 4 or mix_buffer_size != HQ_MIX_BUFFER_BYTES:
        fail(
            f"HQ intermediate buffer is not a 4-byte-aligned 0x{HQ_MIX_BUFFER_BYTES:x} "
            f"byte region: address=0x{mix_buffer:08x} size=0x{mix_buffer_size:x}"
        )
    if probe_size != 0x2C:
        fail(f"HQ runtime probe size is 0x{probe_size:x}, expected 0x2c")

    for symbol in ("SoundMainRAM_End", "SoundMainRAM_MixBuffer", "gExpansionHqMixerProbe"):
        require_absent_symbol(disabled_elf, symbol, nm)

    return {
        "code_address": code,
        "code_bytes": code_size,
        "code_buffer_address": code_buffer,
        "code_buffer_bytes": code_buffer_size,
        "mix_buffer_address": mix_buffer,
        "mix_buffer_bytes": mix_buffer_size,
        "probe_address": probe,
    }


def _probe(resolver: ElfSymbolResolver, expression: str, size: int) -> gba_playtest.Probe:
    try:
        address = gba_playtest.resolve_probe_expression(expression, size, resolver, expression)
    except ProbeBindingError as error:
        fail(str(error))
    return gba_playtest.Probe(expression, address, size, None)


def capture_pcm_profile(
    rom: Path,
    elf: Path,
    nm: str,
    enabled: bool,
    config: str,
) -> dict:
    resolver = ElfSymbolResolver(elf, nm)
    probes = [
        _probe(resolver, "gSoundInfo+0x04", 1),
        _probe(resolver, "gSoundInfo+0x0b", 1),
    ]
    if enabled:
        for offset in range(0, 0x2C, 4):
            probes.append(_probe(resolver, f"gExpansionHqMixerProbe+0x{offset:x}", 4))

    pcm_probe_start = len(probes)
    for offset in PCM_SAMPLE_OFFSETS:
        probes.append(_probe(resolver, f"gSoundInfo+0x{SOUND_INFO_PCM_BUFFER_OFFSET + offset:x}", 4))
    for offset in PCM_SAMPLE_OFFSETS:
        probes.append(
            _probe(
                resolver,
                f"gSoundInfo+0x{SOUND_INFO_PCM_BUFFER_OFFSET + PCM_CHANNEL_STRIDE + offset:x}",
                4,
            )
        )

    input_scenario_path, checkpoint_frames = RUNTIME_SCENARIOS[config]
    input_scenario = json.loads(input_scenario_path.read_text(encoding="utf-8"))
    inputs = tuple(
        gba_playtest.InputRange(
            frame_range["start"],
            frame_range["end"],
            sum(gba_playtest.KEY_BITS[key] for key in frame_range["keys"]),
        )
        for frame_range in input_scenario["frames"]
    )
    checkpoints = []
    for frame in checkpoint_frames:
        checkpoints.append(
            gba_playtest.Checkpoint(
                name=f"audio-fixture-{frame}",
                frame=frame,
                framebuffer=True,
                expected_framebuffer_hash=None,
                sram_hash=False,
                expected_sram_hash=None,
                sram_hash_exclude_ranges=(),
                probes=(
                    tuple(probes)
                    if frame == checkpoint_frames[0]
                    else tuple(probes[:2] + probes[pcm_probe_start:])
                ),
                regions=(),
                pixel_probes=(),
            )
        )
    scenario = gba_playtest.Scenario(
        name="hq-mixer-battle-pcm",
        description="Ordinary scripted-battle progression with PCM and interrupt-buffer probes.",
        disabled=False,
        blocker=None,
        inputs=inputs,
        checkpoints=tuple(checkpoints),
    )
    return gba_playtest.capture(rom, scenario)


def parse_probe_value(capture: dict, index: int, checkpoint_index: int = -1) -> int:
    return int(capture["checkpoints"][checkpoint_index]["probes"][index]["value"], 16)


def validate_pcm_capture(capture: dict, enabled: bool, selection: dict | None = None) -> dict:
    framebuffer_hashes = [
        checkpoint.get("framebuffer_hash") for checkpoint in capture["checkpoints"]
    ]
    if len(set(framebuffer_hashes)) < 3:
        fail("scripted battle/HBlank progression did not produce distinct rendered states")

    counter = parse_probe_value(capture, 0)
    period = parse_probe_value(capture, 1)
    if period == 0 or counter > period:
        fail(f"invalid PCM interrupt-buffer state: counter={counter} period={period}")

    index = 2
    probe = {}
    if enabled:
        names = (
            "initialization_count",
            "source_address",
            "destination_address",
            "code_bytes",
            "source_checksum",
            "destination_checksum",
            "mix_buffer_address",
            "mix_buffer_bytes",
            "dma_enabled",
            "sound_main_count",
            "invalid_dma_buffer_count",
        )
        probe = {
            name: parse_probe_value(capture, index + value, checkpoint_index=0)
            for value, name in enumerate(names)
        }
        index += len(names)
        if probe["initialization_count"] == 0 or probe["sound_main_count"] == 0:
            fail("HQ mixer did not initialize and execute SoundMain")
        if probe["code_bytes"] != HQ_NO_REVERB_CODE_BYTES:
            fail(
                f"HQ copy used 0x{probe['code_bytes']:x} bytes, expected no-reverb "
                f"profile size 0x{HQ_NO_REVERB_CODE_BYTES:x}"
            )
        if probe["source_checksum"] == 0 or probe["source_checksum"] != probe["destination_checksum"]:
            fail("HQ mixer IWRAM copy checksum does not match its ROM source")
        if selection is None:
            fail("missing HQ selection data")
        if probe["destination_address"] != selection["code_buffer_address"]:
            fail("HQ mixer copied to an unexpected IWRAM destination")
        if probe["mix_buffer_address"] != selection["mix_buffer_address"]:
            fail("HQ mixer used an unexpected intermediate-buffer address")
        if probe["mix_buffer_bytes"] != HQ_MIX_BUFFER_BYTES:
            fail("HQ mixer intermediate-buffer extent changed")
        if probe["dma_enabled"] != 0:
            fail("HQ mixer unexpectedly enabled upstream DMA3 use")
        if probe["invalid_dma_buffer_count"] != 0:
            fail("HQ mixer observed an invalid PCM interrupt-buffer state")

    final_pcm_start = 2
    final_left = [
        parse_probe_value(capture, final_pcm_start + offset)
        for offset in range(len(PCM_SAMPLE_OFFSETS))
    ]
    final_right = [
        parse_probe_value(capture, final_pcm_start + len(PCM_SAMPLE_OFFSETS) + offset)
        for offset in range(len(PCM_SAMPLE_OFFSETS))
    ]
    left = []
    right = []
    for checkpoint_index, checkpoint in enumerate(capture["checkpoints"]):
        pcm_start = 2 + (len(probe) if enabled and checkpoint_index == 0 else 0)
        values = [int(item["value"], 16) for item in checkpoint["probes"]]
        left.extend(values[pcm_start:pcm_start + len(PCM_SAMPLE_OFFSETS)])
        right.extend(
            values[
                pcm_start + len(PCM_SAMPLE_OFFSETS):
                pcm_start + 2 * len(PCM_SAMPLE_OFFSETS)
            ]
        )
    if not any(left):
        fail("left PCM output is silent at every captured sample")
    if not any(right):
        fail("right PCM output is silent at every captured sample")
    if left == right:
        fail("PCM capture has no observable stereo channel distinction")
    return {
        "framebuffer_hashes": framebuffer_hashes,
        "pcm_dma_counter": counter,
        "pcm_dma_period": period,
        "stereo_output_observed": True,
        "final_left_pcm_words": final_left,
        "final_right_pcm_words": final_right,
        "left_pcm_words": left,
        "right_pcm_words": right,
        "hq_probe": probe,
    }


def quantization_rms() -> tuple[float, float]:
    """Compare per-voice 8-bit rounding against one final 16-bit mix round."""
    reference = []
    stock = []
    hq = []
    for frame in range(256):
        voices = (
            0.91 * math.sin(frame * 0.131),
            0.73 * math.sin(frame * 0.217 + 0.41),
            0.54 * math.sin(frame * 0.337 + 1.27),
            0.38 * math.sin(frame * 0.509 + 2.03),
        )
        precise = sum(voices) * 38.0
        reference.append(precise)
        stock.append(sum(round(voice * 38.0) for voice in voices))
        hq.append(round(precise))
    stock_rms = math.sqrt(
        sum((actual - expected) ** 2 for actual, expected in zip(stock, reference))
        / len(reference)
    )
    hq_rms = math.sqrt(
        sum((actual - expected) ** 2 for actual, expected in zip(hq, reference))
        / len(reference)
    )
    return stock_rms, hq_rms


def main() -> int:
    args = parse_args()
    for path, label in (
        (args.enabled_rom, "enabled ROM"),
        (args.enabled_elf, "enabled ELF"),
        (args.enabled_map, "enabled map"),
        (args.disabled_rom, "disabled ROM"),
        (args.disabled_elf, "disabled ELF"),
        (args.disabled_map, "disabled map"),
    ):
        require_file(path, label)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    temporary_root = args.out_dir / "tmp"
    temporary_root.mkdir(exist_ok=True)
    os.environ["TMPDIR"] = str(temporary_root)
    selection = check_link_selection(args.enabled_elf, args.disabled_elf, args.nm)
    enabled_budget = check_budget(args.enabled_map, args.enabled_elf, enabled=True)
    disabled_budget = check_budget(args.disabled_map, args.disabled_elf, enabled=False)
    if (
        enabled_budget["ewram"]["occupied_bytes"]
        - disabled_budget["ewram"]["occupied_bytes"]
        != HQ_EWRAM_BOOKKEEPING_BYTES
    ):
        fail(
            "HQ mixer EWRAM bookkeeping delta differs from the linker contract: "
            f"enabled=0x{enabled_budget['ewram']['occupied_bytes']:x} "
            f"disabled=0x{disabled_budget['ewram']['occupied_bytes']:x}"
        )

    enabled_pcm = validate_pcm_capture(
        capture_pcm_profile(args.enabled_rom, args.enabled_elf, args.nm, enabled=True, config=args.config),
        enabled=True,
        selection=selection,
    )
    disabled_pcm = validate_pcm_capture(
        capture_pcm_profile(args.disabled_rom, args.disabled_elf, args.nm, enabled=False, config=args.config),
        enabled=False,
    )
    stock_rms, hq_rms = quantization_rms()
    if hq_rms >= stock_rms:
        fail(
            f"high-resolution synthetic mixer RMS {hq_rms:.9f} is not lower than "
            f"the stock per-voice result {stock_rms:.9f}"
        )

    report = {
        "config": args.config,
        "enabled": {
            "selection": selection,
            "iwram_budget": enabled_budget,
            "pcm_capture": enabled_pcm,
        },
        "disabled": {
            "iwram_budget": disabled_budget,
            "pcm_capture": disabled_pcm,
        },
        "quantization_rms": {
            "stock_per_voice": stock_rms,
            "hq_final_quantization": hq_rms,
        },
    }
    output = args.out_dir / "hq-mixer-runtime.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "HQ mixer runtime check passed: "
        f"config={args.config} stock_rms={stock_rms:.9f} hq_rms={hq_rms:.9f}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
