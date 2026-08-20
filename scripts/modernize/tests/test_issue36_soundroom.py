"""Focused host tests for issue #36 sound-room persistence.

The fixtures are synthetic bytes built in memory. They prove the 256-slot
capacity boundary, the lossless legacy marker migration, and rejection of
corrupt/unknown records without touching a real save image.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "modernize"))

import save_format_tool as sft  # noqa: E402


def make_sound_room(flags=None, marker=sft.SOUND_ROOM_FORMAT_LEGACY):
    raw = bytearray(sft.SOUND_ROOM_SIZE)
    for song_id in flags or ():
        if not sft.sound_room_song_id_is_valid(song_id):
            raise ValueError(song_id)
        word_offset = (song_id >> 5) * 4
        word = int.from_bytes(raw[word_offset:word_offset + 4], "little")
        word |= 1 << (song_id & 31)
        raw[word_offset:word_offset + 4] = word.to_bytes(4, "little")

    checksum = sft.checksum16(bytes(raw[:sft.SOUND_ROOM_CHECKSUM_DOMAIN]))
    raw[sft.SOUND_ROOM_CHECKSUM_DOMAIN:sft.SOUND_ROOM_CHECKSUM_DOMAIN + 2] = (
        checksum.to_bytes(2, "little")
    )
    raw[0x22:0x24] = marker.to_bytes(2, "little")
    return bytes(raw)


class SoundRoomSaveTests(unittest.TestCase):
    def test_runtime_bitsets_use_sound_room_proc_and_call_local_storage(self):
        source = (ROOT / "src" / "soundroom.c").read_text(encoding="utf-8")
        header = (ROOT / "include" / "soundroom.h").read_text(encoding="utf-8")
        self.assertNotIn("sSoundRoomVisibleFlags", source)
        self.assertNotIn("sSoundRoomPlayableFlags", source)
        self.assertIn(
            "u32 visibleFlags[SOUND_ROOM_CATALOG_FLAG_WORDS];",
            source,
        )
        self.assertIn(
            "u32 playableFlags[SOUND_ROOM_CATALOG_FLAG_WORDS];",
            header,
        )
        self.assertIn(
            "sizeof(struct SoundRoomProc) <= sizeof(struct Proc)",
            (
                ROOT / "scripts" / "modernize" / "tests"
                / "test_save_format_layout.py"
            ).read_text(encoding="utf-8"),
        )

    def test_capacity_accepts_127_128_and_255(self):
        self.assertTrue(sft.sound_room_song_id_is_valid(127))
        self.assertTrue(sft.sound_room_song_id_is_valid(128))
        self.assertTrue(sft.sound_room_song_id_is_valid(255))
        self.assertFalse(sft.sound_room_song_id_is_valid(-1))
        self.assertFalse(sft.sound_room_song_id_is_valid(256))

    def test_legacy_migration_preserves_every_unlock_bit_and_checksum(self):
        legacy = make_sound_room((0, 127, 128, 255))
        migrated = sft.migrate_sound_room_save_bytes(legacy)

        self.assertEqual(sft.sound_room_save_state(legacy), "SOUND_ROOM_SAVE_LEGACY")
        self.assertEqual(
            sft.sound_room_save_state(migrated), "SOUND_ROOM_SAVE_CURRENT"
        )
        self.assertEqual(
            migrated[:sft.SOUND_ROOM_CHECKSUM_DOMAIN],
            legacy[:sft.SOUND_ROOM_CHECKSUM_DOMAIN],
        )
        self.assertEqual(
            migrated[sft.SOUND_ROOM_CHECKSUM_DOMAIN:sft.SOUND_ROOM_CHECKSUM_DOMAIN + 2],
            legacy[sft.SOUND_ROOM_CHECKSUM_DOMAIN:sft.SOUND_ROOM_CHECKSUM_DOMAIN + 2],
        )
        self.assertEqual(
            int.from_bytes(migrated[0x22:0x24], "little"),
            sft.SOUND_ROOM_FORMAT_CURRENT,
        )

    def test_corrupt_checksum_and_unknown_marker_are_rejected(self):
        corrupt = bytearray(make_sound_room((128,)))
        corrupt[0] ^= 0x02
        with self.assertRaises(sft.SaveFormatError):
            sft.migrate_sound_room_save_bytes(bytes(corrupt))

        unknown = make_sound_room((255,), marker=0x0802)
        with self.assertRaises(sft.SaveFormatError):
            sft.migrate_sound_room_save_bytes(unknown)

    def test_current_migration_is_idempotent(self):
        current = make_sound_room((128, 255), marker=sft.SOUND_ROOM_FORMAT_CURRENT)
        self.assertEqual(sft.migrate_sound_room_save_bytes(current), current)

    def test_display_condition_reveals_a_playable_song_without_completion_credit(self):
        source = (ROOT / "src" / "soundroom.c").read_text(encoding="utf-8")
        reveal = source.index("if (!IsSoundRoomSongUnlocked(&soundRoomData")
        reveal_block = source[reveal:source.index("proc->totalSongs =", reveal)]
        self.assertIn(
            "proc->playableFlags[i >> 5] |= 1 << (i & 0x1f);",
            reveal_block,
        )
        completion = source[source.index("proc->playableSongs = 0;"):reveal]
        self.assertIn(
            "if (gSoundRoomTable[i].displayCondFunc == NULL)",
            completion,
        )
        self.assertIn("proc->playableSongs++;", completion)


if __name__ == "__main__":
    unittest.main()
