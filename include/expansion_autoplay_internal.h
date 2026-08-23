#ifndef GUARD_EXPANSION_AUTOPLAY_INTERNAL_H
#define GUARD_EXPANSION_AUTOPLAY_INTERNAL_H

#include "expansion_autoplay.h"

#define EXPANSION_AUTOPLAY_SCENARIO_HOTKEY_MASK (SELECT_BUTTON | START_BUTTON | R_BUTTON)

bool ExpansionAutoplay_TryActivateScenario(u16 newKeys, u16 heldKeys);
bool ExpansionAutoplay_IsBlueComputerPhase(void);
#if FE8_EXPANSION_BLUE_PHASE_DELEGATE
bool ExpansionAutoplay_TryRestorePlayerControlAfterPhase(void);
#endif
void ExpansionAutoplay_OnPlayerPhaseStart(void);
void ExpansionAutoplay_OnBlueComputerPhaseStart(void);
void ExpansionAutoplay_OnBlueComputerPhaseComplete(void);
void ExpansionAutoplay_RecordEligibleActors(int side, int count);
void ExpansionAutoplay_RecordCommittedAction(
    int side,
    u8 actorSlot,
    u8 actionId,
    u8 targetSlot,
    enum ExpansionAutoplayTargetRelation relation);
void ExpansionAutoplay_RecordRelationCheck(int leftSlot, int rightSlot, bool allied);
void ExpansionAutoplay_RecordUnsupportedEscape(void);
void ExpansionAutoplay_RecordSuspendSuppressed(void);

#endif /* GUARD_EXPANSION_AUTOPLAY_INTERNAL_H */
