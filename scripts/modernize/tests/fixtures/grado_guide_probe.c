#include "global.h"

#include <assert.h>
#include <stdlib.h>
#include <string.h>

#include "eventinfo.h"
#include "bmguide.h"
#include "bm.h"
#include "grado_state.h"

u8 gChapterFlagBits[5];
u8 gPermanentFlagBits[25];
struct PlaySt gPlaySt;
struct Unit * gActiveUnit;

/* The retained event-dispatch table needs this unrelated symbol, never its execution. */
int GetUnitItemSlot(struct Unit * unit, int item)
{
    (void)unit;
    (void)item;
    abort();
}

#if EXPECT_STATIC_GUIDE
static void VerifyPolicyFlag(int flag, int guideOwned)
{
    u8 beforePermanent[25];
    u8 beforeChapter[5];
    u8 expectedPermanent[25];
    u8 expectedChapter[5];
    void (*const rawOperations[2])(int) = {SetFlag, ClearFlag};
    void (*const policyOperations[2])(int) = {GradoGuide_SetFlag, GradoGuide_ClearFlag};
    int initialState;
    unsigned int action;

    for (initialState = 0; initialState < 2; ++initialState)
    {
        if (initialState)
            SetFlag(flag);
        else
            ClearFlag(flag);

        assert(CheckFlag(flag) == initialState);
        memcpy(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent));
        memcpy(expectedChapter, gChapterFlagBits, sizeof(expectedChapter));
        assert(GradoGuide_CheckFlag(flag) == (guideOwned || initialState));
        assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
        assert(memcmp(expectedChapter, gChapterFlagBits, sizeof(expectedChapter)) == 0);

        for (action = 0; action < 2; ++action)
        {
            memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
            memcpy(beforeChapter, gChapterFlagBits, sizeof(beforeChapter));

            if (!guideOwned)
                rawOperations[action](flag);

            memcpy(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent));
            memcpy(expectedChapter, gChapterFlagBits, sizeof(expectedChapter));
            memcpy(gPermanentFlagBits, beforePermanent, sizeof(beforePermanent));
            memcpy(gChapterFlagBits, beforeChapter, sizeof(beforeChapter));
            policyOperations[action](flag);

            assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
            assert(memcmp(expectedChapter, gChapterFlagBits, sizeof(expectedChapter)) == 0);
            assert(GradoGuide_CheckFlag(flag) == (guideOwned || CheckFlag(flag)));
            assert(memcmp(expectedPermanent, gPermanentFlagBits, sizeof(expectedPermanent)) == 0);
            assert(memcmp(expectedChapter, gChapterFlagBits, sizeof(expectedChapter)) == 0);
        }
    }
}
#endif

int main(void)
{
    u8 beforePermanent[25];
    u8 beforeChapter[5];
    struct GuideEnt * entry;
    unsigned int count;
    unsigned int i;
    int guideOwned;

    assert(GetPermanentFlagBitsSize() == sizeof(beforePermanent));
    assert(GetChapterFlagBitsSize() == sizeof(beforeChapter));
    ResetPermanentFlags();

#if EXPECT_STATIC_GUIDE
    assert(!IsGuideLocked());
    assert(BmGuideTextShowGreenOrNormal());

    for (i = 0; i < sizeof(gPermanentFlagBits); ++i)
        gPermanentFlagBits[i] = i * 13 + 7;

    for (i = 0; i < sizeof(gChapterFlagBits); ++i)
        gChapterFlagBits[i] = i * 11 + 3;

    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    memcpy(beforeChapter, gChapterFlagBits, sizeof(beforeChapter));

    BmGuideTextSetAllGreen();
    assert(!IsGuideLocked());
    assert(BmGuideTextShowGreenOrNormal());

    for (entry = gGuideTable, count = 0; entry->title != 12; ++entry, ++count)
    {
        assert(GradoGuide_CheckFlag(entry->displayFlag));
        assert(GradoGuide_CheckFlag(entry->readFlag));
        GradoGuide_SetFlag(entry->displayFlag);
        GradoGuide_ClearFlag(entry->displayFlag);
        GradoGuide_SetFlag(entry->readFlag);
        GradoGuide_ClearFlag(entry->readFlag);
    }

    assert(count == 56);
    assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);

    for (i = 1; i <= 100 + sizeof(beforePermanent) * 8; ++i)
    {
        if (i > sizeof(beforeChapter) * 8 && i <= 100)
            continue;

        guideOwned = 0;
        for (entry = gGuideTable; entry->title != 12; ++entry)
            if (entry->displayFlag == i || entry->readFlag == i)
                guideOwned = 1;

        assert(GradoState_IsProgressiveGuideFlag(i) == guideOwned);
        VerifyPolicyFlag(i, guideOwned);
    }
#else
    (void)entry;
    (void)count;
    (void)i;
    (void)guideOwned;
    assert(IsGuideLocked());
    BmGuideTextSetAllGreen();
    assert(!IsGuideLocked());
    assert(!BmGuideTextShowGreenOrNormal());
#endif

    return 0;
}
