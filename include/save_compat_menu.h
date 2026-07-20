#ifndef GUARD_SAVE_COMPAT_MENU_H
#define GUARD_SAVE_COMPAT_MENU_H

/*
 * Global save-compatibility gate UI (issue #2 slice 2).
 *
 * This is the dedicated, self-contained blocking proc that StartSaveMenu()
 * (src/savemenu.c) diverts into whenever ClassifySramSaveCompat() returns
 * anything other than SAVE_COMPAT_CURRENT. It shows a state-specific
 * diagnostic message and a small Back / Erase All Save Data menu (Back is
 * always the default/first control). It never calls IsSaveValid,
 * ReadSaveBlockInfo, ReadGameSave, or any slot/current-struct accessor --
 * only the global classifier and the format-independent whole-chip
 * initializer (InitGlobalSaveInfodata()) on confirmed erase. See
 * docs/save_format.md.
 */

#include "proc.h"
#include "save_format.h"

/*
 * Read-only diagnostic probe for host/runtime tests (issue #2 slice 2
 * requirement 4). Does not change persisted SRAM layout; both fields live
 * in EWRAM and are updated only by the compatibility proc itself.
 *
 * gSaveCompatMenuActive is nonzero for exactly as long as the compat proc
 * is blocking the normal save menu from starting (i.e. from the moment
 * StartSaveCompatMenu() is invoked until it ends, either via Back or via
 * a confirmed erase re-entering the normal save menu). gSaveCompatMenuLastState
 * records the SaveCompatState classification that most recently triggered
 * (or would have triggered) the compat proc; it is left at its last value
 * once the proc ends, so a test can observe which state was diagnosed even
 * after the proc itself has finished.
 *
 * Both globals are zero-initialized (ewram_data is a NOLOAD linker section
 * -- see ldscript.txt -- so a non-zero compile-time initializer is never
 * actually applied on real hardware or under emulation; this matches every
 * other EWRAM_DATA global in this codebase). Before the compat proc has
 * ever run, gSaveCompatMenuLastState therefore reads 0 (== SAVE_COMPAT_EMPTY)
 * regardless of the real SRAM state -- this is not a meaningful
 * classification. Callers/tests must only trust gSaveCompatMenuLastState
 * once gSaveCompatMenuActive has been observed nonzero at least once (i.e.
 * after the gate has actually run and written a real classification), never
 * as a default "no dialog shown" reading.
 *
 * EWRAM budget: 2 bytes total (u8 + u8), well within existing per-slice
 * diagnostic-global precedent (e.g. gBoolSramWorking, include/bmsave.h).
 */
extern EWRAM_DATA u8 gSaveCompatMenuActive;
extern EWRAM_DATA u8 gSaveCompatMenuLastState;

/* Starts the blocking compatibility proc as a child of `parent` (the same
 * ProcPtr StartSaveMenu() itself received). `compat` must not be
 * SAVE_COMPAT_CURRENT. */
void StartSaveCompatMenu(ProcPtr parent, enum SaveCompatState compat);

#endif /* GUARD_SAVE_COMPAT_MENU_H */
