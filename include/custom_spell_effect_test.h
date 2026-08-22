#ifndef GUARD_CUSTOM_SPELL_EFFECT_TEST_H
#define GUARD_CUSTOM_SPELL_EFFECT_TEST_H

#include "global.h"

struct Anim;
struct CustomSpellEffect;

#ifndef FE8_EXPANSION_CUSTOM_SPELL_TEST
#define FE8_EXPANSION_CUSTOM_SPELL_TEST 0
#endif

#if (FE8_EXPANSION_CUSTOM_SPELL_TEST != 0) \
    && (FE8_EXPANSION_CUSTOM_SPELL_TEST != 1)
#error "FE8_EXPANSION_CUSTOM_SPELL_TEST must be 0 or 1"
#endif

#if FE8_EXPANSION_CUSTOM_SPELL_TEST && !FE8_EXPANSION_MODERN_BUILD
#error "FE8_EXPANSION_CUSTOM_SPELL_TEST is available only in the modern test lane"
#endif

#if FE8_EXPANSION_CUSTOM_SPELL_TEST

#define CUSTOM_SPELL_EFFECT_TEST_PROBE_MAGIC 0x43535031

struct CustomSpellEffectTestProbe
{
    /* 000 */ u32 magic;
    /* 004 */ u32 enabled;
    /* 008 */ u32 completedMask;
    /* 00C */ u32 failureMask;
    /* 010 */ u32 harnessEnded;

    /* 014 */ u32 normalCustomDispatches;
    /* 018 */ u32 normalStarts;
    /* 01C */ u32 normalResourceLoads;
    /* 020 */ u32 normalHits;
    /* 024 */ u32 normalCleanups;
    /* 028 */ u32 normalChildCreates;
    /* 02C */ u32 normalChildDeletes;
    /* 030 */ u32 normalFinalActive;
    /* 034 */ u32 normalFinalSemaphore;
    /* 038 */ u32 normalFinalSpellState;

    /* 03C */ u32 vanillaDispatches;
    /* 040 */ u32 vanillaCustomDispatches;

    /* 044 */ u32 missingCustomDispatches;
    /* 048 */ u32 missingFallbackReason;
    /* 04C */ u32 missingFallbackAnimation;
    /* 050 */ u32 missingResourceLoads;
    /* 054 */ u32 missingFinalActive;

    /* 058 */ u32 invalidCustomDispatches;
    /* 05C */ u32 invalidFallbackReason;
    /* 060 */ u32 invalidFallbackAnimation;
    /* 064 */ u32 invalidResourceLoads;
    /* 068 */ u32 invalidFinalActive;

    /* 06C */ u32 reentrantCustomDispatches;
    /* 070 */ u32 reentrantStarts;
    /* 074 */ u32 reentrantFallbacks;
    /* 078 */ u32 reentrantFallbackReason;
    /* 07C */ u32 reentrantResourceLoads;
    /* 080 */ u32 reentrantCleanups;
    /* 084 */ u32 reentrantFinalActive;
    /* 088 */ u32 reentrantFinalSemaphore;

    /* 08C */ u32 resourceFailureCustomDispatches;
    /* 090 */ u32 resourceFailureStarts;
    /* 094 */ u32 resourceFailureFallbacks;
    /* 098 */ u32 resourceFailureFallbackReason;
    /* 09C */ u32 resourceFailureResourceLoads;
    /* 0A0 */ u32 resourceFailureCleanups;
    /* 0A4 */ u32 resourceFailureChildCreates;
    /* 0A8 */ u32 resourceFailureFinalActive;
    /* 0AC */ u32 resourceFailureFinalSemaphore;

    /* 0B0 */ u32 backgroundsCustomDispatches;
    /* 0B4 */ u32 backgroundsFallbackReason;
    /* 0B8 */ u32 backgroundsFallbackAnimation;
    /* 0BC */ u32 backgroundsResourceLoads;
    /* 0C0 */ u32 backgroundsFinalActive;

    /* 0C4 */ u32 semaphoreCustomDispatches;
    /* 0C8 */ u32 semaphoreFallbackReason;
    /* 0CC */ u32 semaphoreFallbackAnimation;
    /* 0D0 */ u32 semaphoreResourceLoads;
    /* 0D4 */ u32 semaphorePreserved;
    /* 0D8 */ u32 semaphoreFinalActive;

    /* 0DC */ u32 forcedCustomDispatches;
    /* 0E0 */ u32 forcedStarts;
    /* 0E4 */ u32 forcedCleanups;
    /* 0E8 */ u32 forcedChildCreates;
    /* 0EC */ u32 forcedChildDeletes;
    /* 0F0 */ u32 forcedFinalActive;
    /* 0F4 */ u32 forcedFinalSemaphore;

    /* 0F8 */ u32 finalCustomActive;
    /* 0FC */ u32 finalSpellCastActive;
    /* 100 */ u32 finalSemaphore;
    /* 104 */ u32 finalSpellState;
};

extern struct CustomSpellEffectTestProbe gCustomSpellEffectTestProbe;

void CustomSpellEffectTest_Start(void);
void CustomSpellEffectTest_RecordDispatch(s16 animationIndex, bool8 custom);
const struct CustomSpellEffect *CustomSpellEffectTest_OverrideLookup(
    u8 animationId,
    const struct CustomSpellEffect *reference);
bool8 CustomSpellEffectTest_InterceptFallback(u8 animationId, u8 reason);
bool8 CustomSpellEffectTest_InterceptHit(struct Anim *target, int hitted);
bool8 CustomSpellEffectTest_ShouldFailResourceLoad(void);
void CustomSpellEffectTest_RecordStart(void);
void CustomSpellEffectTest_RecordResourceLoad(void);
void CustomSpellEffectTest_RecordCleanup(void);
void CustomSpellEffectTest_RecordChildCreate(void);
void CustomSpellEffectTest_RecordChildDelete(void);
void CustomSpellEffectTest_ForceEndOwner(void);

#endif /* FE8_EXPANSION_CUSTOM_SPELL_TEST */

#endif /* GUARD_CUSTOM_SPELL_EFFECT_TEST_H */
