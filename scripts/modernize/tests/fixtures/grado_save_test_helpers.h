#ifndef GUARD_GRADO_SAVE_TEST_HELPERS_H
#define GUARD_GRADO_SAVE_TEST_HELPERS_H

#include <string.h>

enum
{
#define GRADO_FIELD(symbol, first, bits, kind, maximum, chapter, scope) \
    GRADO_TEST_FIRST_##symbol = first,
#define GRADO_ALIAS(symbol, target, value)
#define GRADO_COUNT(symbol, group)
#define GRADO_ENDING(symbol, target)
#define GRADO_PRIVATE(symbol, first, bits, kind, maximum) GRADO_TEST_FIRST_##symbol = first,
#include "grado_campaign_state.def"
#undef GRADO_FIELD
#undef GRADO_ALIAS
#undef GRADO_COUNT
#undef GRADO_ENDING
#undef GRADO_PRIVATE
};

static void GradoTestSetPackedBit(u8 * bits, unsigned int index)
{
    bits[index / 8] |= 1u << (index % 8);
}

static void GradoTestSetPermanentFlag(u8 * bits, unsigned int flag)
{
    GradoTestSetPackedBit(bits, flag - 101);
}

static void GradoTestExpectedTwoRecoveries(u8 * expected, const u8 * saved)
{
    memcpy(expected, saved, GRADO_STATE_PERMANENT_BYTES);
    GradoTestSetPermanentFlag(expected, GRADO_TEST_FIRST_GRADO_STATE_LEGITIMACY + 1);
    GradoTestSetPermanentFlag(expected, GRADO_TEST_FIRST_GRADO_STATE_RENAIS + 1);
}

static void GradoTestCorruptChapterGuard(u8 * chapter)
{
    GradoTestSetPackedBit(chapter, GRADO_TEST_FIRST_GRADO_FLAG_CH13_GUARD - 1);
    GradoTestSetPackedBit(chapter, GRADO_TEST_FIRST_GRADO_FLAG_CH13_GUARD);
}

#endif
