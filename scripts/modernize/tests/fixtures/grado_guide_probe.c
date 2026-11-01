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

int main(void)
{
    u8 beforePermanent[25];
    u8 beforeChapter[5];
    struct GuideEnt * entry;
    unsigned int count;
    unsigned int i;

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

    ClearFlag(0xB3);
    assert(!CheckFlag(0xB3));
    assert(!GradoGuide_CheckFlag(0xB3));
    GradoGuide_SetFlag(0xB3);
    assert(CheckFlag(0xB3));
    assert(GradoGuide_CheckFlag(0xB3));
    GradoGuide_ClearFlag(0xB3);
    assert(!CheckFlag(0xB3));
    assert(!GradoGuide_CheckFlag(0xB3));
    ClearFlag(7);
    assert(!CheckFlag(7));
    assert(!GradoGuide_CheckFlag(7));
    GradoGuide_SetFlag(7);
    assert(CheckFlag(7));
    assert(GradoGuide_CheckFlag(7));
    GradoGuide_ClearFlag(7);
    assert(!CheckFlag(7));
    assert(!GradoGuide_CheckFlag(7));
#else
    (void)entry;
    (void)count;
    (void)i;
    assert(IsGuideLocked());
    BmGuideTextSetAllGreen();
    assert(!IsGuideLocked());
    assert(!BmGuideTextShowGreenOrNormal());
#endif

    return 0;
}
