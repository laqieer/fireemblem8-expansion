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
HQ_DISABLED_IWRAM_STATIC_END = 0x030067F0
HQ_ENABLED_IWRAM_STATIC_END = 0x03006E00
HQ_IWRAM_STATIC_DELTA = HQ_ENABLED_IWRAM_STATIC_END - HQ_DISABLED_IWRAM_STATIC_END
MPLAY_INFO_BYTES = 0x40
MPLAY_INFO_TRACKS_OFFSET = 0x2C
MPLAY_INFO_IDENT_OFFSET = 0x34
MPLAY_ID_NUMBER = 0x68736D53
MPLAY_INFO_SYMBOLS = (
    "gMPlayInfo_SE4_BMP2",
    "gMPlayInfo_SE5_BMP3",
    "gMPlayInfo_BGM1",
    "gMPlayInfo_SE6_BMP4",
    "gMPlayInfo_BGM2",
    "gMPlayInfo_SE1_SYS1",
    "gMPlayInfo_SE3_BMP1",
    "gMPlayInfo_SE7_EVT",
    "gMPlayInfo_SE2_SYS2",
)
DISABLED_MPLAY_INFO_ADDRESSES = {
    "gMPlayInfo_SE4_BMP2": 0x030063C0,
    "gMPlayInfo_SE5_BMP3": 0x03006400,
    "gMPlayInfo_BGM1": 0x03006440,
    "gMPlayInfo_SE6_BMP4": 0x03006610,
    "gMPlayInfo_BGM2": 0x03006650,
    "gMPlayInfo_SE1_SYS1": 0x03006690,
    "gMPlayInfo_SE3_BMP1": 0x030066D0,
    "gMPlayInfo_SE7_EVT": 0x03006720,
    "gMPlayInfo_SE2_SYS2": 0x03006760,
}
SOUND_INFO_CHANNEL_OFFSET = 0x50
SOUND_CHANNEL_STRIDE = 0x40
SOUND_CHANNEL_TRACK_OFFSET = 0x2C
SOUND_CHANNEL_COUNT = 12
SOUND_INFO_PCM_BUFFER_OFFSET = 0x350
PCM_CHANNEL_STRIDE = 0x630
PCM_SAMPLE_OFFSETS = (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xD0)
LATE_WINDOW_CHECKPOINT_INDEX = 2
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
        resolve_elf_symbol(elf, symbol, nm)
    except ProbeBindingError:
        return
    fail(f"{symbol} must be absent from disabled ELF {elf}")


def validate_player_ranges(
    players: dict[str, tuple[int, int]],
    mix_buffer: tuple[int, int] | None,
) -> None:
    ranges = sorted(
        (address, address + size, symbol)
        for symbol, (address, size) in players.items()
    )
    for (_, previous_end, previous_symbol), (start, _, symbol) in zip(ranges, ranges[1:]):
        if start < previous_end:
            fail(
                f"MP2K player-info symbols overlap: {previous_symbol} and {symbol}"
            )
    if mix_buffer is None:
        return
    mix_start, mix_size = mix_buffer
    mix_end = mix_start + mix_size
    for start, end, symbol in ranges:
        if start < mix_end and mix_start < end:
            fail(
                f"{symbol} overlaps HQ mix-buffer interval "
                f"[0x{mix_start:08x}, 0x{mix_end:08x})"
            )


def map_section_for_range(
    sections: list[budget.OutputSection],
    address: int,
    size: int,
    label: str,
    map_path: Path,
) -> str:
    matches = [
        section.name
        for section in sections
        if section.address <= address
        and address + size <= section.address + section.size
    ]
    if len(matches) != 1:
        fail(
            f"{label} at [0x{address:08x}, 0x{address + size:08x}) "
            f"has ambiguous map ownership in {map_path}: {matches}"
        )
    return matches[0]


def check_player_layout(
    elf: Path,
    map_path: Path,
    map_sections: list[budget.OutputSection],
    nm: str,
    enabled: bool,
    mix_buffer: tuple[int, int] | None,
) -> dict[str, dict[str, int | str]]:
    resolver = ElfSymbolResolver(elf, nm)
    players = {
        symbol: require_symbol(resolver, symbol)
        for symbol in MPLAY_INFO_SYMBOLS
    }
    validate_player_ranges(players, mix_buffer)
    expected_section = "ewram_data" if enabled else "IWRAM"
    report = {}
    for symbol, (address, size) in players.items():
        if size != MPLAY_INFO_BYTES:
            fail(
                f"{symbol} size is 0x{size:x}, expected 0x{MPLAY_INFO_BYTES:x}"
            )
        if enabled:
            if not 0x02000000 <= address < 0x02040000:
                fail(f"{symbol} is not owned by EWRAM in enabled ELF: 0x{address:08x}")
        elif address != DISABLED_MPLAY_INFO_ADDRESSES[symbol]:
            fail(
                f"{symbol} disabled address is 0x{address:08x}, expected "
                f"0x{DISABLED_MPLAY_INFO_ADDRESSES[symbol]:08x}"
            )
        section = map_section_for_range(
            map_sections, address, size, symbol, map_path
        )
        if section != expected_section:
            fail(
                f"{symbol} map owner is {section}, expected {expected_section}"
            )
        report[symbol] = {
            "address": address,
            "bytes": size,
            "map_section": section,
        }
    return report


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
    expected_static_end = (
        HQ_ENABLED_IWRAM_STATIC_END if enabled else HQ_DISABLED_IWRAM_STATIC_END
    )
    if iwram["static_end_address"] != expected_static_end:
        fail(
            f"{map_path} IWRAM static end is 0x{iwram['static_end_address']:08x}, "
            f"expected 0x{expected_static_end:08x}"
        )
    ewram = next((region for region in report["regions"] if region["name"] == "ewram"), None)
    if ewram is None:
        fail(f"{map_path} has no EWRAM report")
    return {"iwram": iwram, "ewram": ewram}


def check_link_selection(
    enabled_elf: Path,
    enabled_map: Path,
    disabled_elf: Path,
    disabled_map: Path,
    nm: str,
) -> dict:
    enabled = ElfSymbolResolver(enabled_elf, nm)
    code, code_size = require_symbol(enabled, "SoundMainRAM")
    code_end, _ = require_symbol(enabled, "SoundMainRAM_End")
    code_buffer, code_buffer_size = require_symbol(enabled, "SoundMainRAM_Buffer")
    mix_buffer, mix_buffer_size = require_symbol(enabled, "SoundMainRAM_MixBuffer")
    probe, probe_size = require_symbol(enabled, "gExpansionHqMixerProbe")
    _, enabled_map_sections, _ = budget.parse_map(
        enabled_map.read_text(encoding="utf-8")
    )
    _, disabled_map_sections, _ = budget.parse_map(
        disabled_map.read_text(encoding="utf-8")
    )

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
    mix_section = map_section_for_range(
        enabled_map_sections,
        mix_buffer,
        mix_buffer_size,
        "SoundMainRAM_MixBuffer",
        enabled_map,
    )
    if mix_section != "IWRAM":
        fail(f"HQ mix-buffer map owner is {mix_section}, expected IWRAM")

    enabled_players = check_player_layout(
        enabled_elf,
        enabled_map,
        enabled_map_sections,
        nm,
        enabled=True,
        mix_buffer=(mix_buffer, mix_buffer_size),
    )
    disabled_players = check_player_layout(
        disabled_elf,
        disabled_map,
        disabled_map_sections,
        nm,
        enabled=False,
        mix_buffer=None,
    )

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
        "enabled_player_info": enabled_players,
        "disabled_player_info": disabled_players,
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
    common_probes = [
        _probe(resolver, "gSoundInfo+0x04", 1),
        _probe(resolver, "gSoundInfo+0x0b", 1),
        _probe(resolver, f"gMPlayInfo_BGM1+0x{MPLAY_INFO_TRACKS_OFFSET:x}", 4),
        _probe(resolver, f"gMPlayInfo_BGM1+0x{MPLAY_INFO_IDENT_OFFSET:x}", 4),
    ]
    for channel in range(SOUND_CHANNEL_COUNT):
        channel_offset = SOUND_INFO_CHANNEL_OFFSET + channel * SOUND_CHANNEL_STRIDE
        common_probes.append(_probe(resolver, f"gSoundInfo+0x{channel_offset:x}", 1))
        common_probes.append(
            _probe(
                resolver,
                f"gSoundInfo+0x{channel_offset + SOUND_CHANNEL_TRACK_OFFSET:x}",
                4,
            )
        )
    for offset in PCM_SAMPLE_OFFSETS:
        common_probes.append(
            _probe(resolver, f"gSoundInfo+0x{SOUND_INFO_PCM_BUFFER_OFFSET + offset:x}", 4)
        )
    for offset in PCM_SAMPLE_OFFSETS:
        common_probes.append(
            _probe(
                resolver,
                f"gSoundInfo+0x{SOUND_INFO_PCM_BUFFER_OFFSET + PCM_CHANNEL_STRIDE + offset:x}",
                4,
            )
        )

    first_checkpoint_probes = list(common_probes[:2])
    if enabled:
        for offset in range(0, 0x2C, 4):
            first_checkpoint_probes.append(
                _probe(resolver, f"gExpansionHqMixerProbe+0x{offset:x}", 4)
            )
    first_checkpoint_probes.extend(common_probes[2:])

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
                    tuple(first_checkpoint_probes)
                    if frame == checkpoint_frames[0]
                    else tuple(common_probes)
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


def checkpoint_probe_values(checkpoint: dict) -> dict[str, int]:
    return {
        probe["address"]: int(probe["value"], 16)
        for probe in checkpoint["probes"]
    }


def require_sustained_late_audio(
    checkpoint_frames: list[int],
    left_by_checkpoint: list[list[int]],
    right_by_checkpoint: list[list[int]],
    active_channel_counts: list[int],
    profile: str,
) -> None:
    if not (
        len(checkpoint_frames)
        == len(left_by_checkpoint)
        == len(right_by_checkpoint)
        == len(active_channel_counts)
    ):
        fail(f"{profile} PCM checkpoint data has inconsistent lengths")
    late_rows = list(
        zip(
            checkpoint_frames[LATE_WINDOW_CHECKPOINT_INDEX:],
            left_by_checkpoint[LATE_WINDOW_CHECKPOINT_INDEX:],
            right_by_checkpoint[LATE_WINDOW_CHECKPOINT_INDEX:],
            active_channel_counts[LATE_WINDOW_CHECKPOINT_INDEX:],
        )
    )
    if not late_rows:
        fail(f"{profile} PCM capture has no declared late window")
    for frame, left, right, active_channels in late_rows:
        if not any(left) or not any(right):
            fail(f"{profile} late PCM window is silent at frame {frame}")
        if left == right:
            fail(
                f"{profile} late PCM window has identical left/right samples "
                f"at frame {frame}"
            )
        if active_channels == 0:
            fail(f"{profile} late PCM window has no active MP2K channel at frame {frame}")
    if not any(left_by_checkpoint[-1]) or not any(right_by_checkpoint[-1]):
        fail(f"{profile} final PCM checkpoint is silent")


def validate_pcm_capture(capture: dict, enabled: bool, selection: dict | None = None) -> dict:
    profile = "enabled" if enabled else "disabled"
    framebuffer_hashes = [
        checkpoint.get("framebuffer_hash") for checkpoint in capture["checkpoints"]
    ]
    if len(set(framebuffer_hashes)) < 3:
        fail("scripted battle/HBlank progression did not produce distinct rendered states")

    values_by_checkpoint = [
        checkpoint_probe_values(checkpoint)
        for checkpoint in capture["checkpoints"]
    ]
    checkpoint_frames = [
        int(checkpoint["frame"]) for checkpoint in capture["checkpoints"]
    ]
    counters = [values["gSoundInfo+0x04"] for values in values_by_checkpoint]
    periods = [values["gSoundInfo+0x0b"] for values in values_by_checkpoint]
    for frame, counter, period in zip(checkpoint_frames, counters, periods):
        if period == 0 or counter > period:
            fail(
                f"invalid PCM interrupt-buffer state at frame {frame}: "
                f"counter={counter} period={period}"
            )

    player_states = []
    active_channel_counts = []
    left_by_checkpoint = []
    right_by_checkpoint = []
    for frame, values in zip(checkpoint_frames, values_by_checkpoint):
        tracks = values[f"gMPlayInfo_BGM1+0x{MPLAY_INFO_TRACKS_OFFSET:x}"]
        ident = values[f"gMPlayInfo_BGM1+0x{MPLAY_INFO_IDENT_OFFSET:x}"]
        if tracks == 0 or ident != MPLAY_ID_NUMBER:
            fail(
                f"{profile} MP2K BGM player state did not survive initialization "
                f"at frame {frame}: tracks=0x{tracks:08x} ident=0x{ident:08x}"
            )
        player_states.append({"frame": frame, "tracks": tracks, "ident": ident})

        active_channels = 0
        orphan_channels = []
        for channel in range(SOUND_CHANNEL_COUNT):
            channel_offset = SOUND_INFO_CHANNEL_OFFSET + channel * SOUND_CHANNEL_STRIDE
            status = values[f"gSoundInfo+0x{channel_offset:x}"]
            track = values[
                f"gSoundInfo+0x{channel_offset + SOUND_CHANNEL_TRACK_OFFSET:x}"
            ]
            if status & 0xC7:
                active_channels += 1
                if track == 0:
                    orphan_channels.append(channel)
        if orphan_channels:
            fail(
                f"{profile} MP2K channels are active without track ownership at "
                f"frame {frame}: {orphan_channels}"
            )
        active_channel_counts.append(active_channels)
        left_by_checkpoint.append(
            [
                values[f"gSoundInfo+0x{SOUND_INFO_PCM_BUFFER_OFFSET + offset:x}"]
                for offset in PCM_SAMPLE_OFFSETS
            ]
        )
        right_by_checkpoint.append(
            [
                values[
                    f"gSoundInfo+0x{SOUND_INFO_PCM_BUFFER_OFFSET + PCM_CHANNEL_STRIDE + offset:x}"
                ]
                for offset in PCM_SAMPLE_OFFSETS
            ]
        )

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
            name: values_by_checkpoint[0][f"gExpansionHqMixerProbe+0x{value * 4:x}"]
            for value, name in enumerate(names)
        }
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

    require_sustained_late_audio(
        checkpoint_frames,
        left_by_checkpoint,
        right_by_checkpoint,
        active_channel_counts,
        profile,
    )
    final_left = left_by_checkpoint[-1]
    final_right = right_by_checkpoint[-1]
    left = [word for checkpoint in left_by_checkpoint for word in checkpoint]
    right = [word for checkpoint in right_by_checkpoint for word in checkpoint]
    if not any(left):
        fail("left PCM output is silent at every captured sample")
    if not any(right):
        fail("right PCM output is silent at every captured sample")
    if left == right:
        fail("PCM capture has no observable stereo channel distinction")
    return {
        "framebuffer_hashes": framebuffer_hashes,
        "pcm_dma_counter": counters[-1],
        "pcm_dma_period": periods[-1],
        "stereo_output_observed": True,
        "final_left_pcm_words": final_left,
        "final_right_pcm_words": final_right,
        "left_pcm_words": left,
        "right_pcm_words": right,
        "late_window_frames": checkpoint_frames[LATE_WINDOW_CHECKPOINT_INDEX:],
        "late_left_pcm_words": left_by_checkpoint[LATE_WINDOW_CHECKPOINT_INDEX:],
        "late_right_pcm_words": right_by_checkpoint[LATE_WINDOW_CHECKPOINT_INDEX:],
        "late_active_channel_counts": active_channel_counts[
            LATE_WINDOW_CHECKPOINT_INDEX:
        ],
        "player_states": player_states,
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
    selection = check_link_selection(
        args.enabled_elf,
        args.enabled_map,
        args.disabled_elf,
        args.disabled_map,
        args.nm,
    )
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
    if (
        enabled_budget["iwram"]["static_end_address"]
        - disabled_budget["iwram"]["static_end_address"]
        != HQ_IWRAM_STATIC_DELTA
    ):
        fail(
            "HQ mixer IWRAM static delta differs from the linker contract: "
            f"enabled=0x{enabled_budget['iwram']['static_end_address']:08x} "
            f"disabled=0x{disabled_budget['iwram']['static_end_address']:08x}"
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
