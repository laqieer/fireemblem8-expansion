#!/usr/bin/env python3

import argparse
from pathlib import Path

from PIL import Image


WIDTH = 240
HEIGHT = 160
TILE = 8
PALETTE_COLORS = 16


def derive(source: Path, output: Path, banks: int) -> None:
    image = Image.open(source).convert("RGB")
    image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    result = Image.new("P", image.size)
    palette = [0] * (256 * 3)
    rows = HEIGHT // TILE
    for bank in range(banks):
        first = rows * bank // banks
        last = rows * (bank + 1) // banks
        strip = image.crop((0, first * TILE, WIDTH, last * TILE))
        indexed = strip.convert(
            "P", palette=Image.Palette.ADAPTIVE, colors=PALETTE_COLORS, dither=Image.Dither.NONE
        )
        colors = indexed.getpalette() or [0] * 768
        palette[bank * 48:(bank + 1) * 48] = colors[:48]
        pixels = indexed.load()
        target = result.load()
        for y in range(last * TILE - first * TILE):
            for x in range(WIDTH):
                target[x, first * TILE + y] = bank * PALETTE_COLORS + pixels[x, y]
    result.putpalette(palette)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.save(output, optimize=True)


def split_cg(source: Path, prefix: Path) -> None:
    data = source.read_bytes()
    count = 10
    part_size = 0x800
    if len(data) > part_size * count:
        raise ValueError(f"{source}: {len(data)} bytes exceeds {part_size * count}")
    padded = data + bytes(part_size * count - len(data))
    for part in range(count):
        (prefix.parent / f"{prefix.name}_{part}.bin").write_bytes(
            padded[part * part_size:(part + 1) * part_size]
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    derive_parser = subparsers.add_parser("derive")
    derive_parser.add_argument("source", type=Path)
    derive_parser.add_argument("output", type=Path)
    derive_parser.add_argument("--banks", required=True, type=int, choices=(6, 8))
    split_parser = subparsers.add_parser("split-cg")
    split_parser.add_argument("source", type=Path)
    split_parser.add_argument("prefix", type=Path)
    args = parser.parse_args()
    if args.command == "derive":
        derive(args.source, args.output, args.banks)
    else:
        split_cg(args.source, args.prefix)


if __name__ == "__main__":
    main()
