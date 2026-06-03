#!/usr/bin/env bash
# Fetch the portable GBAHawk emulator + stage it for the TAS replay validation.
# GBAHawk is portable (no install). Run from WSL2; everything lands under a
# Windows-accessible working dir so the Windows GBAHawk.exe can read it.
#
# Usage: get_gbahawk.sh [version] [work_dir] [gba_bios_path]
#   version       default v2.1.1 (the version the vykan12 movie was recorded on;
#                 use the movie's emuVersion to avoid core-version desync)
#   work_dir      default /mnt/c/gbahawk_test  (= C:\gbahawk_test)
#   gba_bios_path optional path to gba_bios.bin (sha1 300c20df...) to drop into
#                 the emulator's Firmware/ dir (the movie requires the real BIOS)
set -euo pipefail

VER="${1:-v2.1.1}"
WORK="${2:-/mnt/c/gbahawk_test}"
BIOS="${3:-}"
BIOS_SHA1="300c20df6731a33952ded8c436f7f186d25d3492"

mkdir -p "$WORK"
zip="$WORK/GBAHawk.${VER}.zip"
url="https://github.com/alyosha-tas/GBAHawk/releases/download/${VER}/GBAHawk.${VER}.zip"
[ -f "$zip" ] || { echo "downloading $url"; curl -L -s -o "$zip" "$url"; }

dest="$WORK/GBAHawk_${VER}"
python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$zip" "$dest"
exe="$(find "$dest" -maxdepth 2 -iname GBAHawk.exe | head -1)"
[ -n "$exe" ] || { echo "GBAHawk.exe not found after extract" >&2; exit 1; }
echo "extracted: $exe"

if [ -n "$BIOS" ]; then
  got="$(sha1sum "$BIOS" | cut -d' ' -f1)"
  [ "$got" = "$BIOS_SHA1" ] || { echo "BIOS sha1 $got != $BIOS_SHA1" >&2; exit 1; }
  cp "$BIOS" "$(dirname "$exe")/Firmware/gba_bios.bin"
  echo "placed GBA BIOS in Firmware/ (sha1 ok)"
else
  echo "NOTE: no BIOS supplied; the movie requires the real GBA BIOS"
  echo "      (sha1 $BIOS_SHA1) in $(dirname "$exe")/Firmware/"
fi
echo "GBAHAWK_EXE=$exe"
