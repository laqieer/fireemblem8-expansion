#include "global.h"

#include <stddef.h>

#include "bmguide.h"
#include "EAstdlib.h"
#include "event.h"
#include "eventinfo.h"
#include "hardware.h"

#if GRADO_GUIDE_SELECTED
#include "grado_campaign_state.h"
#endif

struct GuideSelectionProbe
{
    u32 phase;
    u32 coldLocked;
    u32 coldAllRead;
    u32 resetLocked;
    u32 resetAllRead;
    u32 unlockedLocked;
    u32 unlockedAllRead;
    u32 entryCount;
    u32 visibleCount;
    u32 readCount;
    u32 topicCount;
    u32 chapterCount;
    u32 sortSet;
    u32 sortCleared;
    u32 sortChapterMode;
    u32 sortChapterFlag;
    u32 sortTopicMode;
    u32 sortTopicFlag;
    u32 redrawCount;
    u32 unexpectedCall;
    u32 prologueFlag;
    u32 worldFlag;
    u32 natasha;
    u32 falk;
    u32 normalEventFlag;
    u32 bgmEventFlag;
    u32 directEventFlag;
    u32 dynamicEventFlag;
    u8 topicOrder[20];
    u8 chapterOrder[20];
    u8 sortChapterIndices[4];
    u8 sortTopicIndices[4];
    u8 resetPermanent[25];
    u8 resetChapter[5];
    u8 unlockedPermanent[25];
    u8 sortChapterPermanent[25];
    u8 sortChapterFlags[5];
    u8 afterConsumersPermanent[25];
    u8 afterConsumersChapter[5];
    u8 afterInheritedPermanent[25];
    u8 afterInheritedChapter[5];
};

u8 IWRAM_DATA gPermanentFlagBits[25];
u8 IWRAM_DATA gChapterFlagBits[5];
u32 gEventSlots[EVENT_SLOT_COUNT];
u8 EWRAM_DATA gGenericBuffer[0x2000];
volatile struct GuideSelectionProbe gGuideSelectionProbe;
u16 gBG0TilemapBuffer[32 * 32];
u16 gBG1TilemapBuffer[32 * 32];
u16 gBG2TilemapBuffer[32 * 32];
struct PlaySt gPlaySt;

static struct KeyStatusBuffer sGuideKeys;
static struct GuideProc sGuideParent;
static struct GuideProc sGuideChild;
struct KeyStatusBuffer * CONST_DATA gKeyStatusPtr = &sGuideKeys;

extern struct GuideSt * gGuideSt;
extern struct ProcCmd gProcScr_GuideCategoryRedraw[];
extern EventListScr EventScr_Prologue_TutMessageTurn1[];
extern EventListScr EventScr_Prologue_EirikaAttacked[];
extern EventScr EventScrWM_Ch2_BeginningTutorial[];
void BuildGuideTopicList(void);
void BuildGuideChapterList(void);
void Guide_MainLoop(struct GuideProc * proc);

static _Noreturn void UnexpectedCall(u32 call)
{
    gGuideSelectionProbe.unexpectedCall = call;
    gGuideSelectionProbe.phase = 3;
    for (;;)
        __asm__ volatile ("nop");
}

static const EventListScr sProjectEvent[] = {
    ENUT(180)
    ENDA
};

static const EventListScr sDynamicProjectEvent[] = {
    ENUT(65535)
    ENDA
};

static void ExecuteFlagCommands(const EventListScr * script)
{
    const u16 * command = (const u16 *)script;
    struct EventEngineProc proc = { 0 };
    unsigned int i;

    for (i = 0; i < 256; ++i)
    {
        unsigned int opcode = *command >> 8;
        unsigned int length = EVT_CMD_LEN(command);

        if (length == 0 || (length & 1))
            UnexpectedCall(13);
        if (opcode == EV_CMD_END)
            return;
        if (opcode == EV_CMD_EVSET)
        {
            proc.pEventCurrent = command;
            if (Event02_EvBitAndIdMod(&proc) != EVC_ADVANCE_CONTINUE)
                UnexpectedCall(14);
        }
        command += length;
    }
    UnexpectedCall(15);
}

ProcPtr Proc_StartBlocking(const struct ProcCmd * script, ProcPtr parent)
{
    if (script != gProcScr_GuideCategoryRedraw || parent != &sGuideParent)
        UnexpectedCall(1);
    ++gGuideSelectionProbe.redrawCount;
    return &sGuideChild;
}

ProcPtr Proc_Start(const struct ProcCmd * script, ProcPtr parent)
{
    (void)script;
    (void)parent;
    UnexpectedCall(2);
}

void Proc_Break(ProcPtr proc)
{
    (void)proc;
    UnexpectedCall(3);
}

void m4aSongNumStart(u16 song)
{
    (void)song;
    UnexpectedCall(4);
}

void LockMenuScrollBar(void)
{
    UnexpectedCall(5);
}

void UnlockMenuScrollBar(void)
{
    UnexpectedCall(6);
}

void BG_EnableSyncByMask(int bg)
{
    (void)bg;
    UnexpectedCall(7);
}

void ClearText(struct Text * text)
{
    (void)text;
    UnexpectedCall(8);
}

void PutDrawText(struct Text * text, u16 * dest, int color, int x, int width, const char * string)
{
    (void)text;
    (void)dest;
    (void)color;
    (void)x;
    (void)width;
    (void)string;
    UnexpectedCall(9);
}

void Text_DrawString(struct Text * text, const char * string)
{
    (void)text;
    (void)string;
    UnexpectedCall(10);
}

char * GetStringFromIndex(int index)
{
    (void)index;
    UnexpectedCall(11);
}

const char * GetCharTextLen(const char * string, u32 * width)
{
    (void)string;
    (void)width;
    UnexpectedCall(12);
}

static void SeedGuideIndices(void)
{
    gGuideSt->categoryIdx = 4;
    gGuideSt->unk_2a = 3;
    gGuideSt->unk_2b = 2;
    gGuideSt->unk_2c = 1;
}

static void CaptureGuideIndices(volatile u8 * indices)
{
    indices[0] = gGuideSt->categoryIdx;
    indices[1] = gGuideSt->unk_2a;
    indices[2] = gGuideSt->unk_2b;
    indices[3] = gGuideSt->unk_2c;
}

static int GuideReadFlag(int flag)
{
#if GRADO_GUIDE_SELECTED
    return GradoGuide_CheckFlag(flag);
#else
    return CheckFlag(flag);
#endif
}

static void GuideSetFlag(int flag)
{
#if GRADO_GUIDE_SELECTED
    GradoGuide_SetFlag(flag);
#else
    SetFlag(flag);
#endif
}

static void GuideClearFlag(int flag)
{
#if GRADO_GUIDE_SELECTED
    GradoGuide_ClearFlag(flag);
#else
    ClearFlag(flag);
#endif
}

void AgbMain(void)
{
    struct GuideEnt * entry;
    u8 * topics;
    u8 * chapters;
    unsigned int i;

    gGuideSelectionProbe.phase = 1;
    for (i = 0; i < sizeof(gChapterFlagBits); ++i)
        gChapterFlagBits[i] = 0xA6;

    SetFlag(180);
    SetFlag(182);
    SetFlag(240);
    gGuideSelectionProbe.coldLocked = IsGuideLocked();
    gGuideSelectionProbe.coldAllRead = BmGuideTextShowGreenOrNormal();

    for (i = 0; i < sizeof(gPermanentFlagBits); ++i)
        gPermanentFlagBits[i] = 0xFF;
    ResetPermanentFlags();
    for (i = 0; i < sizeof(gPermanentFlagBits); ++i)
        gGuideSelectionProbe.resetPermanent[i] = gPermanentFlagBits[i];
    for (i = 0; i < sizeof(gChapterFlagBits); ++i)
        gGuideSelectionProbe.resetChapter[i] = gChapterFlagBits[i];
    gGuideSelectionProbe.resetLocked = IsGuideLocked();
    gGuideSelectionProbe.resetAllRead = BmGuideTextShowGreenOrNormal();

    BmGuideTextSetAllGreen();
    for (i = 0; i < sizeof(gPermanentFlagBits); ++i)
        gGuideSelectionProbe.unlockedPermanent[i] = gPermanentFlagBits[i];
    gGuideSelectionProbe.unlockedLocked = IsGuideLocked();
    gGuideSelectionProbe.unlockedAllRead = BmGuideTextShowGreenOrNormal();

    for (entry = gGuideTable; entry->title != 12; ++entry)
    {
        ++gGuideSelectionProbe.entryCount;
        gGuideSelectionProbe.visibleCount += !!GuideReadFlag(entry->displayFlag);
        gGuideSelectionProbe.readCount += !!GuideReadFlag(entry->readFlag);
    }

    BuildGuideTopicList();
    BuildGuideChapterList();
    topics = (u8 *)gGuideSt + offsetof(struct GuideSt, unk_54);
    chapters = (u8 *)gGuideSt + offsetof(struct GuideSt, unk_40);
    gGuideSelectionProbe.topicCount = gGuideSt->unk_3d;
    gGuideSelectionProbe.chapterCount = gGuideSt->unk_3c;
    for (i = 0; i < gGuideSt->unk_3d && i < sizeof(gGuideSelectionProbe.topicOrder); ++i)
        gGuideSelectionProbe.topicOrder[i] = topics[i];
    for (i = 0; i < gGuideSt->unk_3c && i < sizeof(gGuideSelectionProbe.chapterOrder); ++i)
        gGuideSelectionProbe.chapterOrder[i] = chapters[i];

    GuideSetFlag(179);
    gGuideSelectionProbe.sortSet = CheckFlag(179);
    GuideClearFlag(179);
    gGuideSelectionProbe.sortCleared = CheckFlag(179);

    gPlaySt.config.disableSoundEffects = 1;
    gGuideSt->state = GUIDE_STATE_0;
    gGuideSt->sortMode = GUIDE_SORT_MODE_TOPIC;
    sGuideKeys.newKeys = SELECT_BUTTON;
    SeedGuideIndices();
    Guide_MainLoop(&sGuideParent);
    gGuideSelectionProbe.sortChapterMode = gGuideSt->sortMode;
    gGuideSelectionProbe.sortChapterFlag = CheckFlag(179);
    CaptureGuideIndices(gGuideSelectionProbe.sortChapterIndices);
    for (i = 0; i < sizeof(gPermanentFlagBits); ++i)
        gGuideSelectionProbe.sortChapterPermanent[i] = gPermanentFlagBits[i];
    for (i = 0; i < sizeof(gChapterFlagBits); ++i)
        gGuideSelectionProbe.sortChapterFlags[i] = gChapterFlagBits[i];

    sGuideKeys.newKeys = SELECT_BUTTON;
    SeedGuideIndices();
    Guide_MainLoop(&sGuideParent);
    gGuideSelectionProbe.sortTopicMode = gGuideSt->sortMode;
    gGuideSelectionProbe.sortTopicFlag = CheckFlag(179);
    CaptureGuideIndices(gGuideSelectionProbe.sortTopicIndices);
    for (i = 0; i < sizeof(gPermanentFlagBits); ++i)
        gGuideSelectionProbe.afterConsumersPermanent[i] = gPermanentFlagBits[i];
    for (i = 0; i < sizeof(gChapterFlagBits); ++i)
        gGuideSelectionProbe.afterConsumersChapter[i] = gChapterFlagBits[i];

    ResetPermanentFlags();
    ExecuteFlagCommands(EventScr_Prologue_TutMessageTurn1);
    ExecuteFlagCommands(EventScrWM_Ch2_BeginningTutorial);
    gGuideSelectionProbe.prologueFlag = CheckFlag(180);
    gGuideSelectionProbe.worldFlag = CheckFlag(226);
#if GRADO_GUIDE_SELECTED
    {
        u16 value;

        if (GradoState_ReadSymbol(GRADO_STATE_RETREAT_NATASHA, GRADO_STATE_NO_CHAPTER, &value)
            != GRADO_STATE_VALUE_VALID)
            UnexpectedCall(16);
        gGuideSelectionProbe.natasha = value;
        if (GradoState_ReadSymbol(GRADO_FLAG_CH07_FALK, GRADO_STATE_NO_CHAPTER, &value)
            != GRADO_STATE_VALUE_VALID)
            UnexpectedCall(17);
        gGuideSelectionProbe.falk = value;
    }
#else
    gGuideSelectionProbe.natasha = 0xFFFFFFFF;
    gGuideSelectionProbe.falk = 0xFFFFFFFF;
#endif
    for (i = 0; i < sizeof(gPermanentFlagBits); ++i)
        gGuideSelectionProbe.afterInheritedPermanent[i] = gPermanentFlagBits[i];
    for (i = 0; i < sizeof(gChapterFlagBits); ++i)
        gGuideSelectionProbe.afterInheritedChapter[i] = gChapterFlagBits[i];

    ResetPermanentFlags();
    ExecuteFlagCommands(EventScr_Prologue_EirikaAttacked);
    gGuideSelectionProbe.normalEventFlag = CheckFlag(0x66);
    gGuideSelectionProbe.bgmEventFlag = CheckFlag(EVFLAG_BGM_CHANGE);
    ResetPermanentFlags();
    ExecuteFlagCommands(sProjectEvent);
    gGuideSelectionProbe.directEventFlag = CheckFlag(180);
    ResetPermanentFlags();
    gEventSlots[EVT_SLOT_2] = 180;
    ExecuteFlagCommands(sDynamicProjectEvent);
    gGuideSelectionProbe.dynamicEventFlag = CheckFlag(180);

    gGuideSelectionProbe.phase = 2;
    for (;;)
        __asm__ volatile ("nop");
}
