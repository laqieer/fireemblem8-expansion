#include "global.h"

#include <assert.h>
#include <stdlib.h>
#include <string.h>

#include "bm.h"
#include "eventinfo.h"
#include "grado_state.h"

u8 gChapterFlagBits[5];
u8 gPermanentFlagBits[25];
struct PlaySt gPlaySt;
struct Unit * gActiveUnit;

/* The backend retains this unrelated dispatch dependency; executing it is an error. */
int GetUnitItemSlot(struct Unit * unit, int item)
{
    (void)unit;
    (void)item;
    abort();
}

int main(void)
{
    const struct GradoStateField permanent = {
        139, 2, GRADO_STATE_COUNTER, 3, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT
    };
    const struct GradoStateField chapter = {
        7, 2, GRADO_STATE_COUNTER, 3, 3, GRADO_STATE_CHAPTER
    };
    const struct GradoStateField choice = {
        143, 3, GRADO_STATE_ONE_HOT, 3, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT
    };
    const struct GradoStateField bounded = {
        139, 3, GRADO_STATE_COUNTER, 3, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT
    };
    const struct GradoStateField boolean = {
        300, 1, GRADO_STATE_BOOLEAN, 1, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT
    };
    const struct GradoStateField reserved = {
        125, 1, GRADO_STATE_BOOLEAN, 1, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT
    };
    u8 beforePermanent[25];
    u8 expectedPermanent[25];
    u8 beforeChapter[5];
    u8 expectedChapter[5];
    u16 value;
    unsigned int i;

    for (i = 0; i < sizeof(gPermanentFlagBits); ++i)
        gPermanentFlagBits[i] = i * 13 + 7;
    for (i = 0; i < sizeof(gChapterFlagBits); ++i)
        gChapterFlagBits[i] = i * 11 + 3;

    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    memcpy(beforeChapter, gChapterFlagBits, sizeof(beforeChapter));
    ClearFlag(139);
    SetFlag(140);
    memcpy(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent));
    memcpy(gPermanentFlagBits, beforePermanent, sizeof(beforePermanent));
    assert(GradoState_WriteValue(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, 2)
        == GRADO_STATE_VALUE_VALID);
    assert(!CheckFlag(139) && CheckFlag(140));
    assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
    value = 0xBEEF;
    assert(GradoState_ReadValue(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, &value)
        == GRADO_STATE_VALUE_VALID);
    assert(value == 2);
    assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);

    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    assert(GradoState_WriteValue(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, 4)
        == GRADO_STATE_VALUE_RANGE);
    assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);
    assert(GradoState_WriteValue(&chapter, GRADO_GUIDE_PROGRESSIVE, 4, 1)
        == GRADO_STATE_VALUE_CHAPTER);
    assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);

    SetFlag(7);
    SetFlag(8);
    memcpy(expectedChapter, gChapterFlagBits, sizeof(expectedChapter));
    memcpy(gChapterFlagBits, beforeChapter, sizeof(beforeChapter));
    assert(GradoState_WriteValue(&chapter, GRADO_GUIDE_PROGRESSIVE, 3, 3)
        == GRADO_STATE_VALUE_VALID);
    assert(memcmp(expectedChapter, gChapterFlagBits, sizeof(expectedChapter)) == 0);
    assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
    value = 0;
    assert(GradoState_ReadValue(&chapter, GRADO_GUIDE_PROGRESSIVE, 3, &value)
        == GRADO_STATE_VALUE_VALID && value == 3);
    assert(memcmp(expectedChapter, gChapterFlagBits, sizeof(expectedChapter)) == 0);
    memcpy(beforeChapter, gChapterFlagBits, sizeof(beforeChapter));
    value = 0xBEEF;
    assert(GradoState_ReadValue(&chapter, GRADO_GUIDE_PROGRESSIVE, 4, &value)
        == GRADO_STATE_VALUE_CHAPTER && value == 0xBEEF);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);

    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    value = 0xBEEF;
    assert(GradoState_ReadValue(NULL, GRADO_GUIDE_PROGRESSIVE, 3, &value)
        == GRADO_STATE_VALUE_INPUT && value == 0xBEEF);
    assert(GradoState_ReadValue(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, NULL)
        == GRADO_STATE_VALUE_INPUT);
    assert(GradoState_WriteValue(&reserved, GRADO_GUIDE_PROGRESSIVE, 3, 1)
        == GRADO_STATE_VALUE_LAYOUT);
    assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);

    SetFlag(139);
    SetFlag(140);
    SetFlag(141);
    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    assert(GradoState_ReadValue(&bounded, GRADO_GUIDE_PROGRESSIVE, 3, &value)
        == GRADO_STATE_VALUE_CORRUPT && value == 0xBEEF);
    assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    SetFlag(300);
    memcpy(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent));
    memcpy(gPermanentFlagBits, beforePermanent, sizeof(beforePermanent));
    assert(GradoState_WriteValue(&boolean, GRADO_GUIDE_PROGRESSIVE, 3, 1)
        == GRADO_STATE_VALUE_VALID);
    assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);
    assert(GradoState_ReadValue(&boolean, GRADO_GUIDE_PROGRESSIVE, 3, &value)
        == GRADO_STATE_VALUE_VALID && value == 1);
    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    ClearFlag(300);
    memcpy(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent));
    memcpy(gPermanentFlagBits, beforePermanent, sizeof(beforePermanent));
    assert(GradoState_WriteValue(&boolean, GRADO_GUIDE_PROGRESSIVE, 3, 0)
        == GRADO_STATE_VALUE_VALID);
    assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);
    assert(GradoState_ReadValue(&boolean, GRADO_GUIDE_PROGRESSIVE, 3, &value)
        == GRADO_STATE_VALUE_VALID && value == 0);

    ClearFlag(143);
    ClearFlag(144);
    ClearFlag(145);
    value = 0xBEEF;
    assert(GradoState_ReadValue(&choice, GRADO_GUIDE_PROGRESSIVE, 3, &value)
        == GRADO_STATE_VALUE_UNSET && value == 0xBEEF);
    SetFlag(143);
    SetFlag(144);
    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    assert(GradoState_ReadValue(&choice, GRADO_GUIDE_PROGRESSIVE, 3, &value)
        == GRADO_STATE_VALUE_CORRUPT && value == 0xBEEF);
    assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
    ClearFlag(143);
    ClearFlag(144);
    SetFlag(145);
    memcpy(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent));
    memcpy(gPermanentFlagBits, beforePermanent, sizeof(beforePermanent));
    assert(GradoState_WriteValue(&choice, GRADO_GUIDE_PROGRESSIVE, 3, 3)
        == GRADO_STATE_VALUE_VALID);
    assert(!CheckFlag(143) && !CheckFlag(144) && CheckFlag(145));
    assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);
    assert(GradoState_ReadValue(&choice, GRADO_GUIDE_PROGRESSIVE, 3, &value)
        == GRADO_STATE_VALUE_VALID && value == 3);
    return 0;
}
