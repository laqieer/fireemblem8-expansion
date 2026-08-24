#include <string.h>

#include "global.h"

#include "bmmap.h"
#include "bmunit.h"
#include "ekrbattle.h"
#include "event.h"
#include "fontgrp.h"
#include "hardware.h"
#include "mapanim.h"
#include "playerphase.h"
#include "prepscreen.h"
#include "proc.h"
#include "rng.h"
#include "uimenu.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

struct PlaySt gPlaySt = {0};
struct BmSt gBmSt = {0};
struct Vec2 gBmMapSize = {0};
struct LCDControlBuffer gLCDControlBuffer = {0};
u16 gPaletteBuffer[0x200] = {0};
struct DebugToolsProbe gDebugToolsProbe = {0};
static struct KeyStatusBuffer sKeyStatus;
struct KeyStatusBuffer* CONST_DATA gKeyStatusPtr = &sKeyStatus;

static u16 sBgMaps[4][32 * 32];
static u8 sMapUnits[8][8];
static u8* sMapUnitRows[8];
u8** gBmMapUnit = sMapUnitRows;

static struct CharacterData sCharacter = {0};
static struct ClassData sClass = {0};
static struct Unit sUnit = {0};

static struct Font sFont = {0};
struct Font* gActiveFont = &sFont;

static struct Proc sContextProc;
static struct Proc sOwnerProc;
static struct Proc sScratchProc;
static ProcFunc sOwnerEndCb;
static int sHubActive;
static int sBattleActive;
static int sBattleEventDaemonActive;
static int sMapActive;
static int sPlayerPhaseActive;
static int sPrepActive;
static int sEventActive;
static int sFadeActive;
static int sGameLock;
static int sRestoredCount;

struct ProcCmd CONST_DATA gProc_BMapMain[] = {{0}};
struct ProcCmd gProcScr_PlayerPhase[] = {{0}};
struct ProcCmd CONST_DATA gProc_ekrBattle[] = {{0}};
struct ProcCmd CONST_DATA ProcScr_BattleEventEngine[] = {{0}};
struct ProcCmd CONST_DATA ProcScr_StdEventEngine[] = {{0}};
struct ProcCmd CONST_DATA ProcScr_MapAnimBattle[] = {{0}};
struct ProcCmd CONST_DATA ProcScr_MapAnimEventBattle[] = {{0}};
struct ProcCmd CONST_DATA gProcScr_SALLYCURSOR[] = {{0}};
struct ProcCmd CONST_DATA gProcScr_DebugToolsMenuTransition[] = {{0}};
struct ProcCmd gProcScr_TitleScreen[] = {{0}};

void InitText(struct Text* text, int tileWidth)
{
    (void)text;
    (void)tileWidth;
}

void ClearText(struct Text* text)
{
    (void)text;
}

void Text_SetColor(struct Text* text, int colorId)
{
    (void)text;
    (void)colorId;
}

void Text_DrawString(struct Text* text, const char* string)
{
    (void)text;
    (void)string;
}

void PutText(struct Text* text, u16* destination)
{
    (void)text;
    (void)destination;
}

void DebugToolsActions_ForceCleanup(void)
{
}

enum DebugToolsResult DebugTools_OpenHub(void)
{
    return DEBUGTOOLS_ERR_ALREADY_ACTIVE;
}

void DebugToolsDiagnosticsHost_Reset(void)
{
    int i;

    memset(&gPlaySt, 0, sizeof(gPlaySt));
    memset(&gBmSt, 0, sizeof(gBmSt));
    memset(&gLCDControlBuffer, 0, sizeof(gLCDControlBuffer));
    memset(gPaletteBuffer, 0, sizeof(gPaletteBuffer));
    memset(sBgMaps, 0, sizeof(sBgMaps));
    memset(sMapUnits, 0, sizeof(sMapUnits));
    memset(&sContextProc, 0, sizeof(sContextProc));
    memset(&sOwnerProc, 0, sizeof(sOwnerProc));
    memset(&sScratchProc, 0, sizeof(sScratchProc));
    memset(&sFont, 0, sizeof(sFont));
    memset(&gDebugToolsProbe, 0, sizeof(gDebugToolsProbe));
    memset(&gDebugToolsDiagnosticsProbe, 0, sizeof(gDebugToolsDiagnosticsProbe));
    memset(&sKeyStatus, 0, sizeof(sKeyStatus));

    for (i = 0; i < 8; ++i)
        sMapUnitRows[i] = sMapUnits[i];

    sCharacter.number = 0x12;
    sClass.number = 0x34;
    sUnit.pCharacterData = &sCharacter;
    sUnit.pClassData = &sClass;
    sUnit.index = 1;
    sUnit.curHP = 17;
    sUnit.maxHP = 24;

    sFont.tileref = 0x80;
    sFont.chr_counter = 23;
    gActiveFont = &sFont;
    sOwnerEndCb = NULL;
    sHubActive = 1;
    sBattleActive = 0;
    sBattleEventDaemonActive = 0;
    sMapActive = 0;
    sPlayerPhaseActive = 0;
    sPrepActive = 0;
    sEventActive = 0;
    sFadeActive = 0;
    sGameLock = 0;
    sRestoredCount = 0;
    gBmMapSize.x = 8;
    gBmMapSize.y = 8;
}

void DebugToolsDiagnosticsHost_SetMap(int prep, int withUnit)
{
    sMapActive = 1;
    sPlayerPhaseActive = !prep;
    sPrepActive = prep;
    gPlaySt.chapterStateBits =
        prep ? PLAY_FLAG_PREPSCREEN : 0;
    gPlaySt.chapterIndex = 2;
    gPlaySt.chapterTurnNumber = 7;
    gPlaySt.faction = FACTION_BLUE;
    gPlaySt.chapterWeatherId = 3;
    gPlaySt.chapterVisionRange = 5;
    gBmSt.playerCursor.x = 3;
    gBmSt.playerCursor.y = 4;
    sMapUnits[4][3] = withUnit ? 1 : 0;
}

void DebugToolsDiagnosticsHost_SetBattle(int active)
{
    sBattleActive = active;
}

void DebugToolsDiagnosticsHost_SetBattleDaemon(int active)
{
    sBattleEventDaemonActive = active;
}

void DebugToolsDiagnosticsHost_SetEvent(int active)
{
    sEventActive = active;
}

void DebugToolsDiagnosticsHost_FillDisplay(u16 value)
{
    int bg;
    int x;
    int y;

    for (bg = 0; bg < 2; ++bg)
        for (y = 1; y < 23; ++y)
            for (x = 1; x <= DEBUGTOOLS_MENU_WIDTH_TILES; ++x)
                sBgMaps[bg][TILEMAP_INDEX(x, y)] = value;
}

int DebugToolsDiagnosticsHost_DisplayEquals(u16 value)
{
    int bg;
    int x;
    int y;

    for (bg = 0; bg < 2; ++bg)
        for (y = 1; y < 23; ++y)
            for (x = 1; x <= DEBUGTOOLS_MENU_WIDTH_TILES; ++x)
                if (sBgMaps[bg][TILEMAP_INDEX(x, y)] != value)
                    return 0;

    return 1;
}

void DebugToolsDiagnosticsHost_OverwriteDisplay(u16 value)
{
    DebugToolsDiagnosticsHost_FillDisplay(value);
    sFont.chr_counter = 99;
}

int DebugToolsDiagnosticsHost_GetFontCounter(void)
{
    return sFont.chr_counter;
}

int DebugToolsDiagnosticsHost_GetGameLock(void)
{
    return sGameLock;
}

int DebugToolsDiagnosticsHost_GetRestoredCount(void)
{
    return sRestoredCount;
}

void DebugToolsDiagnosticsHost_EndContext(void)
{
    Proc_End(&sOwnerProc);
}

u16* BG_GetMapBuffer(int bg)
{
    return sBgMaps[bg];
}

void BG_SetPosition(u16 bg, u16 x, u16 y)
{
    gLCDControlBuffer.bgoffset[bg].x = x;
    gLCDControlBuffer.bgoffset[bg].y = y;
}

void BG_EnableSyncByMask(int mask)
{
    (void)mask;
}

void EnablePaletteSync(void)
{
}

int DebugTools_IsHubActive(void)
{
    return sHubActive;
}

int DebugTools_GetRegisteredCount(void)
{
    return 9;
}

int DebugTools_GetLogCount(void)
{
    return 3;
}

u32 DebugTools_GetAssertFailureCount(void)
{
    return 4;
}

u32 DebugTools_GetLastAssertCode(void)
{
    return 2;
}

u32 GetGameClock(void)
{
    return 1234;
}

int CountProcs(const struct ProcCmd* script)
{
    (void)script;
    return 12;
}

void StoreRNState(u16* seeds)
{
    seeds[0] = 0x1111;
    seeds[1] = 0x2222;
    seeds[2] = 0x3333;
}

int GetUnitCurrentHp(struct Unit* unit)
{
    return unit->curHP;
}

int GetUnitMaxHp(struct Unit* unit)
{
    return unit->maxHP;
}

struct Unit* GetUnit(int id)
{
    return id == 1 ? &sUnit : NULL;
}

bool8 DoesBMXFADEExist(void)
{
    return sFadeActive;
}

ProcPtr Proc_Find(const struct ProcCmd* script)
{
    if (sOwnerProc.proc_script == script)
        return &sOwnerProc;
    if (sScratchProc.proc_script == script)
        return &sScratchProc;

    if (script == gProc_ekrBattle
        || script == ProcScr_MapAnimBattle
        || script == ProcScr_MapAnimEventBattle)
        return sBattleActive ? &sContextProc : NULL;

    if (script == ProcScr_BattleEventEngine)
        return sBattleActive || sBattleEventDaemonActive
            ? &sContextProc
            : NULL;

    if (script == gProc_BMapMain)
        return sMapActive ? &sContextProc : NULL;

    if (script == gProcScr_TitleScreen)
        return &sContextProc;

    if (script == gProcScr_PlayerPhase)
        return sPlayerPhaseActive ? &sContextProc : NULL;

    if (script == gProcScr_SALLYCURSOR)
        return sPrepActive ? &sContextProc : NULL;

    if (script == ProcScr_StdEventEngine)
        return sEventActive ? &sContextProc : NULL;

    return NULL;
}

ProcPtr Proc_Start(const struct ProcCmd* script, ProcPtr parent)
{
    struct Proc* proc =
        parent == &sOwnerProc ? &sScratchProc : &sOwnerProc;

    memset(proc, 0, sizeof(*proc));
    proc->proc_script = script;
    proc->proc_parent = parent;
    return proc;
}

ProcPtr Proc_StartBlocking(const struct ProcCmd* script, ProcPtr parent)
{
    return Proc_Start(script, parent);
}

void Proc_SetEndCb(ProcPtr proc, ProcFunc func)
{
    (void)proc;
    sOwnerEndCb = func;
}

void Proc_End(ProcPtr proc)
{
    if (proc == &sOwnerProc && sOwnerEndCb != NULL)
    {
        ProcFunc end = sOwnerEndCb;
        sOwnerEndCb = NULL;
        sScratchProc.proc_script = NULL;
        end(proc);
    }
}

void Proc_EndEach(const struct ProcCmd* script)
{
    (void)script;
}

struct Proc* EndMenu(struct MenuProc* menu)
{
    (void)menu;
    return NULL;
}

struct MenuProc* StartMenu(const struct MenuDef* def, ProcPtr parent)
{
    (void)def;
    (void)parent;
    return NULL;
}

void LockGame(void)
{
    sGameLock++;
    gBmSt.lock++;
}

void UnlockGame(void)
{
    sGameLock--;
    gBmSt.lock--;
}

void DebugToolsDiagnostics_OnSessionRestored(void)
{
    sHubActive = 0;
    sRestoredCount++;
}
