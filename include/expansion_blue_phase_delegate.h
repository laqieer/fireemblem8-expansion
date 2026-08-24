#ifndef GUARD_EXPANSION_BLUE_PHASE_DELEGATE_H
#define GUARD_EXPANSION_BLUE_PHASE_DELEGATE_H

#include "global.h"

enum ExpansionBluePhaseDelegateResult
{
    EXPANSION_BLUE_PHASE_DELEGATE_OK = 0,
    EXPANSION_BLUE_PHASE_DELEGATE_ERR_WRONG_PHASE = 1,
    EXPANSION_BLUE_PHASE_DELEGATE_ERR_LOCKED = 2,
    EXPANSION_BLUE_PHASE_DELEGATE_ERR_BUSY = 3,
    EXPANSION_BLUE_PHASE_DELEGATE_ERR_NO_ELIGIBLE_UNIT = 4,
    EXPANSION_BLUE_PHASE_DELEGATE_ERR_CONTROL = 5,
};

#if FE8_EXPANSION_BLUE_PHASE_DELEGATE
int ExpansionBluePhaseDelegate_CountEligibleBlueUnits(void);
enum ExpansionBluePhaseDelegateResult ExpansionBluePhaseDelegate_GetAvailability(void);
enum ExpansionBluePhaseDelegateResult ExpansionBluePhaseDelegate_Start(void);
bool ExpansionBluePhaseDelegate_IsPending(void);
#endif

#endif /* GUARD_EXPANSION_BLUE_PHASE_DELEGATE_H */
