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
    const struct GradoStateField chapterChoice = {
        7, 2, GRADO_STATE_ONE_HOT, 2, 3, GRADO_STATE_CHAPTER
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
    const struct GradoStateField monotonic = {
        147, 1, GRADO_STATE_MONOTONIC, 1, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT
    };
    const struct GradoStateField temporaryMonotonic = {
        7, 1, GRADO_STATE_MONOTONIC, 1, 3, GRADO_STATE_CHAPTER
    };
    const struct GradoStateField * valueFields[] = {
        &boolean, &permanent, &chapter, &choice, &monotonic
    };
    const enum GradoGuideStoragePolicy invalidPolicies[] = {
        (enum GradoGuideStoragePolicy)-1, (enum GradoGuideStoragePolicy)2
    };
    const struct
    {
        struct GradoStateField field;
        u16 value;
        u16 setFlag;
    } guide[] = {
        {{180, 1, GRADO_STATE_BOOLEAN, 1, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT}, 1, 180},
        {{233, 3, GRADO_STATE_ONE_HOT, 3, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT}, 3, 235},
        {{240, 2, GRADO_STATE_COUNTER, 3, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT}, 2, 241},
        {{295, 1, GRADO_STATE_MONOTONIC, 1, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT}, 1, 295},
    };
    const struct
    {
        const struct GradoStateField * field;
        enum GradoGuideStoragePolicy policy;
    } oneHot[] = {
        {&choice, GRADO_GUIDE_PROGRESSIVE},
        {&chapterChoice, GRADO_GUIDE_PROGRESSIVE},
        {&guide[1].field, GRADO_GUIDE_STATIC},
    };
    u8 beforePermanent[25];
    u8 expectedPermanent[25];
    u8 beforeChapter[5];
    u8 expectedChapter[5];
    u16 value;
    unsigned int i;
    unsigned int bit;
    unsigned int policy;

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
    for (policy = 0; policy < sizeof(invalidPolicies) / sizeof(invalidPolicies[0]); ++policy)
        for (i = 0; i < sizeof(valueFields) / sizeof(valueFields[0]); ++i)
        {
            value = 0xBEEF;
            assert(GradoState_ReadValue(valueFields[i], invalidPolicies[policy], 3, &value)
                == GRADO_STATE_VALUE_INPUT && value == 0xBEEF);
            assert(GradoState_WriteValue(valueFields[i], invalidPolicies[policy], 3, 0)
                == GRADO_STATE_VALUE_INPUT);
            assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
            assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);
        }
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

    for (i = 0; i < sizeof(oneHot) / sizeof(oneHot[0]); ++i)
    {
        assert(GradoState_WriteValue(oneHot[i].field, oneHot[i].policy, 3,
            oneHot[i].field->maxValue) == GRADO_STATE_VALUE_VALID);
        memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
        memcpy(beforeChapter, gChapterFlagBits, sizeof(beforeChapter));
        for (bit = 0; bit < oneHot[i].field->bitCount; ++bit)
            ClearFlag(oneHot[i].field->firstFlag + bit);
        memcpy(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent));
        memcpy(expectedChapter, gChapterFlagBits, sizeof(expectedChapter));
        memcpy(gPermanentFlagBits, beforePermanent, sizeof(beforePermanent));
        memcpy(gChapterFlagBits, beforeChapter, sizeof(beforeChapter));
        assert(GradoState_WriteValue(oneHot[i].field, oneHot[i].policy, 3, 0)
            == GRADO_STATE_VALUE_VALID);
        assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
        assert(memcmp(expectedChapter, gChapterFlagBits, sizeof(expectedChapter)) == 0);
        value = 0xBEEF;
        assert(GradoState_ReadValue(oneHot[i].field, oneHot[i].policy, 3, &value)
            == GRADO_STATE_VALUE_UNSET && value == 0xBEEF);
        assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
        assert(memcmp(expectedChapter, gChapterFlagBits, sizeof(expectedChapter)) == 0);
    }

    ClearFlag(147);
    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    assert(GradoState_WriteValue(&monotonic, GRADO_GUIDE_PROGRESSIVE, 3, 0)
        == GRADO_STATE_VALUE_VALID);
    assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
    SetFlag(147);
    memcpy(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent));
    memcpy(gPermanentFlagBits, beforePermanent, sizeof(beforePermanent));
    assert(GradoState_WriteValue(&monotonic, GRADO_GUIDE_PROGRESSIVE, 3, 1)
        == GRADO_STATE_VALUE_VALID);
    assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
    assert(GradoState_ReadValue(&monotonic, GRADO_GUIDE_PROGRESSIVE, 3, &value)
        == GRADO_STATE_VALUE_VALID && value == 1);
    assert(GradoState_WriteValue(&monotonic, GRADO_GUIDE_PROGRESSIVE, 3, 0)
        == GRADO_STATE_VALUE_MONOTONIC);
    assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);
    assert(GradoState_WriteValue(&monotonic, GRADO_GUIDE_PROGRESSIVE, 3, 1)
        == GRADO_STATE_VALUE_VALID);
    assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
    assert(GradoState_WriteValue(&monotonic, GRADO_GUIDE_PROGRESSIVE, 3, 2)
        == GRADO_STATE_VALUE_RANGE);
    assert(GradoState_WriteValue(&temporaryMonotonic, GRADO_GUIDE_PROGRESSIVE, 3, 1)
        == GRADO_STATE_VALUE_LAYOUT);
    assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);

    for (i = 0; i < sizeof(guide) / sizeof(guide[0]); ++i)
    {
        for (bit = 0; bit < guide[i].field.bitCount; ++bit)
            ClearFlag(guide[i].field.firstFlag + bit);
        memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
        value = 0xBEEF;
        assert(GradoState_ReadValue(&guide[i].field, GRADO_GUIDE_PROGRESSIVE, 3, &value)
            == GRADO_STATE_VALUE_LAYOUT && value == 0xBEEF);
        assert(GradoState_WriteValue(&guide[i].field, GRADO_GUIDE_PROGRESSIVE, 3, guide[i].value)
            == GRADO_STATE_VALUE_LAYOUT);
        assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
        assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);

        SetFlag(guide[i].setFlag);
        memcpy(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent));
        memcpy(gPermanentFlagBits, beforePermanent, sizeof(beforePermanent));
        assert(GradoState_WriteValue(&guide[i].field, GRADO_GUIDE_STATIC, 3, guide[i].value)
            == GRADO_STATE_VALUE_VALID);
        assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
        assert(GradoState_ReadValue(&guide[i].field, GRADO_GUIDE_STATIC, 3, &value)
            == GRADO_STATE_VALUE_VALID && value == guide[i].value);
        assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
        value = 0xBEEF;
        assert(GradoState_ReadValue(&guide[i].field, GRADO_GUIDE_PROGRESSIVE, 3, &value)
            == GRADO_STATE_VALUE_LAYOUT && value == 0xBEEF);
        assert(GradoState_WriteValue(&guide[i].field, GRADO_GUIDE_PROGRESSIVE, 3, 0)
            == GRADO_STATE_VALUE_LAYOUT);
        assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
        assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);
    }
    {
        struct GradoStateFlagView view;
        struct GradoStateFlagView invalid;
        u8 savedPermanent[25];
        u8 savedChapter[5];
        u8 savedBeforePermanent[25];
        u8 savedBeforeChapter[5];
        u8 corruptPermanent[25];
        u8 corruptBefore[25];
        u8 unsetPermanent[25] = {0};

        ResetPermanentFlags();
        ResetChapterFlags();
        assert(GradoState_WriteValue(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, 2)
            == GRADO_STATE_VALUE_VALID);
        assert(GradoState_WriteValue(&chapter, GRADO_GUIDE_PROGRESSIVE, 3, 2)
            == GRADO_STATE_VALUE_VALID);
        assert(GradoState_WriteValue(&choice, GRADO_GUIDE_PROGRESSIVE, 3, 2)
            == GRADO_STATE_VALUE_VALID);
        memcpy(savedPermanent, gPermanentFlagBits, sizeof(savedPermanent));
        memcpy(savedChapter, gChapterFlagBits, sizeof(savedChapter));
        memcpy(savedBeforePermanent, savedPermanent, sizeof(savedPermanent));
        memcpy(savedBeforeChapter, savedChapter, sizeof(savedChapter));
        memset(gPermanentFlagBits, 0xFF, sizeof(gPermanentFlagBits));
        memset(gChapterFlagBits, 0xFF, sizeof(gChapterFlagBits));
        memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
        memcpy(beforeChapter, gChapterFlagBits, sizeof(beforeChapter));
        view.chapterFlags = savedChapter;
        view.chapterBytes = sizeof(savedChapter);
        view.permanentFlags = savedPermanent;
        view.permanentBytes = sizeof(savedPermanent);

        assert(GradoState_ReadValueFrom(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, &view, &value)
            == GRADO_STATE_VALUE_VALID && value == 2);
        assert(GradoState_ReadValueFrom(&chapter, GRADO_GUIDE_PROGRESSIVE, 3, &view, &value)
            == GRADO_STATE_VALUE_VALID && value == 2);
        assert(GradoState_ReadValueFrom(&choice, GRADO_GUIDE_PROGRESSIVE, 3, &view, &value)
            == GRADO_STATE_VALUE_VALID && value == 2);
        assert(GradoState_ReadValueFrom(&boolean, GRADO_GUIDE_PROGRESSIVE, 3, &view, &value)
            == GRADO_STATE_VALUE_VALID && value == 0);
        invalid = view;
        invalid.chapterFlags = NULL;
        invalid.chapterBytes = 0;
        assert(GradoState_ReadValueFrom(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, &invalid, &value)
            == GRADO_STATE_VALUE_VALID && value == 2);

        value = 0xBEEF;
        assert(GradoState_ReadValueFrom(&chapter, GRADO_GUIDE_PROGRESSIVE, 3, &invalid, &value)
            == GRADO_STATE_VALUE_INPUT && value == 0xBEEF);
        invalid = view;
        invalid.permanentBytes = sizeof(savedPermanent) - 1;
        assert(GradoState_ReadValueFrom(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, &invalid, &value)
            == GRADO_STATE_VALUE_INPUT && value == 0xBEEF);
        invalid = view;
        invalid.permanentFlags = NULL;
        assert(GradoState_ReadValueFrom(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, &invalid, &value)
            == GRADO_STATE_VALUE_INPUT && value == 0xBEEF);
        invalid = view;
        invalid.chapterBytes = sizeof(savedChapter) - 1;
        assert(GradoState_ReadValueFrom(&chapter, GRADO_GUIDE_PROGRESSIVE, 3, &invalid, &value)
            == GRADO_STATE_VALUE_INPUT && value == 0xBEEF);
        assert(GradoState_ReadValueFrom(&chapter, GRADO_GUIDE_PROGRESSIVE, 4, &view, &value)
            == GRADO_STATE_VALUE_CHAPTER && value == 0xBEEF);
        assert(GradoState_ReadValueFrom(&reserved, GRADO_GUIDE_PROGRESSIVE, 3, &view, &value)
            == GRADO_STATE_VALUE_LAYOUT && value == 0xBEEF);
        assert(GradoState_ReadValueFrom(&guide[0].field, GRADO_GUIDE_PROGRESSIVE, 3, &view, &value)
            == GRADO_STATE_VALUE_LAYOUT && value == 0xBEEF);
        assert(GradoState_ReadValueFrom(&guide[0].field, GRADO_GUIDE_STATIC, 3, &view, &value)
            == GRADO_STATE_VALUE_VALID && value == 0);
        value = 0xBEEF;
        assert(GradoState_ReadValueFrom(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, NULL, &value)
            == GRADO_STATE_VALUE_INPUT && value == 0xBEEF);
        assert(GradoState_ReadValueFrom(&permanent, GRADO_GUIDE_PROGRESSIVE, 3, &view, NULL)
            == GRADO_STATE_VALUE_INPUT);
        invalid = view;
        invalid.permanentFlags = unsetPermanent;
        assert(GradoState_ReadValueFrom(&choice, GRADO_GUIDE_PROGRESSIVE, 3, &invalid, &value)
            == GRADO_STATE_VALUE_UNSET && value == 0xBEEF);

        memcpy(gPermanentFlagBits, savedPermanent, sizeof(savedPermanent));
        SetFlag(143);
        memcpy(corruptPermanent, gPermanentFlagBits, sizeof(corruptPermanent));
        memcpy(corruptBefore, corruptPermanent, sizeof(corruptBefore));
        memcpy(gPermanentFlagBits, beforePermanent, sizeof(beforePermanent));
        invalid.permanentFlags = corruptPermanent;
        assert(GradoState_ReadValueFrom(&choice, GRADO_GUIDE_PROGRESSIVE, 3, &invalid, &value)
            == GRADO_STATE_VALUE_CORRUPT && value == 0xBEEF);
        assert(memcmp(corruptBefore, corruptPermanent, sizeof(corruptBefore)) == 0);
        memcpy(gPermanentFlagBits, savedPermanent, sizeof(savedPermanent));
        SetFlag(141);
        memcpy(corruptPermanent, gPermanentFlagBits, sizeof(corruptPermanent));
        memcpy(corruptBefore, corruptPermanent, sizeof(corruptBefore));
        memcpy(gPermanentFlagBits, beforePermanent, sizeof(beforePermanent));
        assert(GradoState_ReadValueFrom(&bounded, GRADO_GUIDE_PROGRESSIVE, 3, &invalid, &value)
            == GRADO_STATE_VALUE_CORRUPT && value == 0xBEEF);
        assert(memcmp(corruptBefore, corruptPermanent, sizeof(corruptBefore)) == 0);
        assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
        assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);
        assert(memcmp(savedBeforePermanent, savedPermanent, sizeof(savedPermanent)) == 0);
        assert(memcmp(savedBeforeChapter, savedChapter, sizeof(savedChapter)) == 0);
    }
    return 0;
}
