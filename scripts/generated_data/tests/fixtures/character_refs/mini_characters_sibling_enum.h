/* Minimal characters.h-shaped fixture for testing
 * read_character_designators()'s enum-block scoping in isolation: a
 * primary anonymous CHARACTER_NONE..CHARACTER_BETA block, followed by a
 * separate, *named* sibling enum that reuses the same "CHARACTER_"
 * textual prefix (mirroring the real header's
 * event_autoload_pid_idx/CHARACTER_EVT_* collision) with a negative
 * value thrown in. read_character_designators() must return only the
 * three primary-block names and must never see the sibling names.
 */
enum {
    CHARACTER_NONE  = 0x00,
    CHARACTER_ALPHA = 0x01,
    CHARACTER_BETA  = 0x02,
};

enum some_other_pid_idx {
    CHARACTER_SIBLING_LEADER = 0,
    CHARACTER_SIBLING_FAKE = -5,
};
