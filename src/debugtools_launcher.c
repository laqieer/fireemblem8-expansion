#include "global.h"

#include "proc.h"
#include "uimenu.h"
#include "rng.h"
#include "bmio.h"
#include "bmunit.h"
#include "eventinfo.h"
#include "gamecontrol.h"
#include "constants/chapters.h"
#include "expansion_debugtools.h"

/*
 * Issue #11 slice 1 -- the one built-in, safe, deterministic launcher
 * action ("Fast Boot: Prologue"), registered as a normal contributor
 * action through DebugTools_RegisterAction(). See docs/debugtools.md
 * "Deterministic launcher contract" for the full policy this
 * implements.
 */

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

/* Reused verbatim from AgbMain's own clean-boot RNG seed (src/main.c) --
 * reseeding to this exact constant immediately before starting the
 * chapter makes the launcher's outcome independent of how many
 * frames/RNG draws happened before the hotkey was pressed. */
#define DEBUGTOOLS_LAUNCHER_RNG_SEED 0x42D690E9

static u8 DebugToolsLauncher_FastBootPrologue(struct MenuProc* menu, struct MenuItemProc* item)
{
    struct GameCtrlProc* proc;

    (void)menu;
    (void)item;

    gDebugToolsProbe.launcherArmed = DEBUGTOOLS_LAUNCHER_ARMED_MAGIC;

    /* Explicit deterministic RNG policy: always reseed to the same fixed
     * constant, regardless of any RNG draws already consumed this play
     * session. */
    SetLCGRNValue(DEBUGTOOLS_LAUNCHER_RNG_SEED);
    InitRN(AdvanceGetLCGRNValue());

    /* Same "new game" bootstrap sequence as GameControl_InitTutorialGame
     * (src/gamecontrol.c), minus the PLAY_FLAG_TUTORIAL bit -- this is a
     * normal chapter boot, not the tutorial map. InitPlayConfig zeroes
     * gPlaySt (EWRAM only); none of these calls touch SRAM. */
    InitPlayConfig(0, 0);
    ResetPermanentFlags();
    ResetChapterFlags();
    InitUnits();
    gPlaySt.chapterIndex = CHAPTER_L_PROLOGUE;

    /* Tear down the entire GameCtrlProc tree -- including the very hub
     * menu whose onSelected callback is executing right now -- and start
     * a fresh one positioned directly at the "goto bmmap" label. This is
     * the exact "EndEach + Start + Goto" idiom already used by
     * RestartGameAndGoto8/RestartGameAndGoto12 (src/gamecontrol.c), which
     * is itself already invoked from inside a MenuItemDef::onSelected
     * callback by the dormant DebugContinueMenu_ManualContinue/
     * InitializeFile handlers in src/bmdebug.c -- proven safe by existing
     * (if dormant) code. LGAMECTRL_EXEC_BM_EXT bypasses both the world
     * map and the interactive Save Menu entirely, so no SRAM is ever
     * read or written by this launcher. */
    Proc_EndEach(gProcScr_GameControl);

    proc = Proc_Start(gProcScr_GameControl, PROC_TREE_3);
    proc->nextChapter = CHAPTER_L_PROLOGUE;

    Proc_Goto(proc, LGAMECTRL_EXEC_BM_EXT);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sFastBootPrologueAction = {
    1, "Fast Boot: Prologue", DebugToolsLauncher_FastBootPrologue
};

void DebugTools_RegisterBuiltinActions(void)
{
    /* Idempotent: a repeat call reports DEBUGTOOLS_ERR_DUPLICATE (same
     * id/label) -- an expected, non-silent result that this one-shot
     * lazy-init call site deliberately ignores. */
    DebugTools_RegisterAction(&sFastBootPrologueAction);
}

#else /* !FE8_EXPANSION_DEBUGTOOLS_ENABLED */

void DebugTools_RegisterBuiltinActions(void)
{
    /* No-op: nothing to register in a release build. */
}

#endif /* FE8_EXPANSION_DEBUGTOOLS_ENABLED */
