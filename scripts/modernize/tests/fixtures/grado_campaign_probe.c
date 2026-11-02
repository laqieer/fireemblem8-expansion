#include "global.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bm.h"
#include "eventinfo.h"
#include "grado_campaign_state.h"

u8 gChapterFlagBits[5];
u8 gPermanentFlagBits[25];
struct PlaySt gPlaySt;
struct Unit * gActiveUnit;

static const enum GradoStateSymbol sRequiredSymbols[] = {
    GRADO_STATE_LEGITIMACY,
    GRADO_STATE_RENAIS,
    GRADO_STATE_SALTROAD,
    GRADO_STATE_TRUTH,
    GRADO_STATE_RATION,
    GRADO_STATE_WYVERN,
    GRADO_STATE_VILLAGE,
    GRADO_STATE_RAUSTEN_MED,
    GRADO_STATE_RAUSTEN_DATA,
    GRADO_STATE_DIRECTION,
    GRADO_STATE_RODERICH,
    GRADO_STATE_LUND,
    GRADO_STATE_RETREAT_DUESSEL,
    GRADO_STATE_RETREAT_KNOLL,
    GRADO_STATE_RETREAT_CORMAG,
    GRADO_STATE_RETREAT_NATASHA,
    GRADO_FLAG_CH00_CLEAR,
    GRADO_FLAG_CH00_OMEN,
    GRADO_FLAG_CH00_WARN,
    GRADO_FLAG_CH00_CORTEGE_LOST,
    GRADO_FLAG_CH00_OPENED,
    GRADO_FLAG_CH01_CLEAR,
    GRADO_FLAG_CH01_V1,
    GRADO_FLAG_CH01_V2,
    GRADO_FLAG_CH01_REVA,
    GRADO_FLAG_CH01_OMEN,
    GRADO_FLAG_CH01_WARN,
    GRADO_FLAG_CH02_CENTRAL,
    GRADO_FLAG_CH02_COUNCIL,
    GRADO_FLAG_CH02_LOCAL,
    GRADO_FLAG_CH02_CLEAR,
    GRADO_FLAG_CH02_RODERICH_SEEN,
    GRADO_FLAG_CH02_CIVIC,
    GRADO_FLAG_CH02_ODO,
    GRADO_FLAG_CH02_PERRIN,
    GRADO_FLAG_CH02_KNOLL,
    GRADO_FLAG_CH02_SHOP_OPEN,
    GRADO_FLAG_CH02_OMEN,
    GRADO_FLAG_CH03_CLEAR,
    GRADO_FLAG_CH03_FACTION,
    GRADO_FLAG_CH03_WYVERN_KEPT,
    GRADO_FLAG_CH03_BALDRIC_GONE,
    GRADO_FLAG_CH03_OMEN,
    GRADO_FLAG_CH03_WARN,
    GRADO_FLAG_CH04_CLEAR,
    GRADO_FLAG_CH04_REIN,
    GRADO_FLAG_CH04_VILLAGE,
    GRADO_FLAG_CH04_HOUSE,
    GRADO_FLAG_CH04_CHEST,
    GRADO_FLAG_CH04_EVAC_COUNT,
    GRADO_FLAG_CH04_OMEN,
    GRADO_FLAG_CH04_WARN,
    GRADO_FLAG_CH05_CLEAR,
    GRADO_FLAG_CH05_VERA,
    GRADO_FLAG_CH05_STEWARD,
    GRADO_FLAG_CH05_ELFIRE,
    GRADO_FLAG_CH05_CHEST,
    GRADO_FLAG_CH05_LYONBOX,
    GRADO_FLAG_CH05_ARCHIVE_COUNT,
    GRADO_FLAG_CH05_OMEN,
    GRADO_FLAG_CH05_WARN,
    GRADO_FLAG_CH05_RWARN,
    GRADO_FLAG_CH05X_OPEN,
    GRADO_FLAG_CH05X_CLEAR,
    GRADO_FLAG_CH05X_WISH,
    GRADO_FLAG_CH05X_LETTER_A,
    GRADO_FLAG_CH05X_LETTER_B,
    GRADO_FLAG_CH05X_LETTERS_ALL,
    GRADO_FLAG_CH05X_PERSUADE,
    GRADO_FLAG_CH05X_CHEST,
    GRADO_FLAG_CH05X_CWARN,
    GRADO_FLAG_CH06_FULL,
    GRADO_FLAG_CH06_LIMITED,
    GRADO_FLAG_CH06_DECLINE,
    GRADO_FLAG_CH06_CLEAR,
    GRADO_FLAG_CH06_SHOP_OPEN,
    GRADO_FLAG_CH06_NORTH,
    GRADO_FLAG_CH06_SOUTH,
    GRADO_FLAG_CH06_ENVOY_SAFE,
    GRADO_FLAG_CH06_OMEN,
    GRADO_FLAG_CH06_WARN,
    GRADO_FLAG_CH07_CLEAR,
    GRADO_FLAG_CH07_FALK,
    GRADO_FLAG_CH07_GUARD_KEPT,
    GRADO_FLAG_CH07_BALDRIC_GONE,
    GRADO_FLAG_CH07_RALLY,
    GRADO_FLAG_CH07_OMEN,
    GRADO_FLAG_CH07_WARN,
    GRADO_FLAG_CH08_SUPPRESS,
    GRADO_FLAG_CH08_CONCEDE,
    GRADO_FLAG_CH08_AUTONOMY,
    GRADO_FLAG_CH08_CLEAR,
    GRADO_FLAG_CH08_SHOP_OPEN,
    GRADO_FLAG_CH08_HALRIC,
    GRADO_FLAG_CH08_VILLAGE,
    GRADO_FLAG_CH08_CHEST,
    GRADO_FLAG_CH08_TAXPOST,
    GRADO_FLAG_CH08_CONVOY_SAFE,
    GRADO_FLAG_CH08_KRAHL_GONE,
    GRADO_FLAG_CH08_OMEN,
    GRADO_FLAG_CH08_WARN,
    GRADO_FLAG_CH09_CLEAR,
    GRADO_FLAG_CH09_ELNA,
    GRADO_FLAG_CH09_ARTUR,
    GRADO_FLAG_CH09_VILLAGE,
    GRADO_FLAG_CH09_SHOP_OPEN,
    GRADO_FLAG_CH09_DEPOT,
    GRADO_FLAG_CH09_DOCTORS_SAFE,
    GRADO_FLAG_CH09_PACIFY,
    GRADO_FLAG_CH09_AGITATOR_DOWN,
    GRADO_FLAG_CH09_MEDNET,
    GRADO_FLAG_CH09_OMEN,
    GRADO_FLAG_CH09_WARN,
    GRADO_FLAG_CH10_FULL,
    GRADO_FLAG_CH10_LIMITED,
    GRADO_FLAG_CH10_SEALED,
    GRADO_FLAG_CH10_CLEAR,
    GRADO_FLAG_CH10_RODERICH,
    GRADO_FLAG_CH10_PHANTOM_NAMED,
    GRADO_FLAG_CH10_WITNESS_SAFE,
    GRADO_FLAG_CH10_WEISS_GONE,
    GRADO_FLAG_CH10_CHEST,
    GRADO_FLAG_CH10_WARN,
    GRADO_FLAG_CH11_CAPITAL,
    GRADO_FLAG_CH11_FRONTIER,
    GRADO_FLAG_CH11_EVEN,
    GRADO_FLAG_CH11_CLEAR,
    GRADO_FLAG_CH11_RUNEN,
    GRADO_FLAG_CH11_REGION_PENINSULA,
    GRADO_FLAG_CH11_REGION_SOUTH,
    GRADO_FLAG_CH11_KRAHL_DEAD,
    GRADO_FLAG_CH11_KRAHL_RAID,
    GRADO_FLAG_CH11_DEPOT,
    GRADO_FLAG_CH11_GRAIN_SAFE,
    GRADO_FLAG_CH11_OMEN,
    GRADO_FLAG_CH11_WARN,
    GRADO_FLAG_CH12_CLEAR,
    GRADO_FLAG_CH12_EWAN,
    GRADO_FLAG_CH12_CORES,
    GRADO_FLAG_CH12_DENY,
    GRADO_FLAG_CH12_NEWLYON_DENIED,
    GRADO_FLAG_CH12_CHEST,
    GRADO_FLAG_CH12_WARN,
    GRADO_FLAG_CH13_SHIELD,
    GRADO_FLAG_CH13_ARMY,
    GRADO_FLAG_CH13_DISBAND,
    GRADO_FLAG_CH13_CLEAR,
    GRADO_FLAG_CH13_VILLAGE,
    GRADO_FLAG_CH13_GUARD,
    GRADO_FLAG_CH13_RUIN_SEALED,
    GRADO_FLAG_CH13_BALDRIC_ACTIVE,
    GRADO_FLAG_CH13_BALDRIC_DEAD,
    GRADO_FLAG_CH13_REFUGEE,
    GRADO_FLAG_CH13_TANA_RETURN,
    GRADO_FLAG_CH13_WARN,
    GRADO_FLAG_CH13_WARN2,
    GRADO_FLAG_CH13_OMEN,
    GRADO_FLAG_CH14_SELF,
    GRADO_FLAG_CH14_GARRISON,
    GRADO_FLAG_CH14_MEDIATE,
    GRADO_FLAG_CH14_CLEAR,
    GRADO_FLAG_CH14_VILLAGE,
    GRADO_FLAG_CH14_SHOP_OPEN,
    GRADO_FLAG_CH14_CHEST,
    GRADO_FLAG_CH14_REP_SAFE,
    GRADO_FLAG_CH14_ELWIN_SAVED,
    GRADO_FLAG_CH14_FORGERY_NAMED,
    GRADO_FLAG_CH14_VOSK_DEAD,
    GRADO_FLAG_CH14_LUND_EXPOSED,
    GRADO_FLAG_CH14_OMEN,
    GRADO_FLAG_CH15_MED_FULL,
    GRADO_FLAG_CH15_MED_LIMITED,
    GRADO_FLAG_CH15_MED_REFUSE,
    GRADO_FLAG_CH15_MED_NATASHA_ABSENT,
    GRADO_FLAG_CH15_DATA_SURRENDER,
    GRADO_FLAG_CH15_DATA_JOINT,
    GRADO_FLAG_CH15_DATA_RETAIN,
    GRADO_FLAG_CH15_DATA_KNOLL_ABSENT,
    GRADO_FLAG_CH15_CLEAR,
    GRADO_FLAG_CH15_CHEST,
    GRADO_FLAG_CH15_SHOP_OPEN,
    GRADO_FLAG_CH15_ENVOY_ARRIVE,
    GRADO_FLAG_CH15_ENVOY_SAFE,
    GRADO_FLAG_CH15_OMEN,
    GRADO_FLAG_CH15_WARN,
    GRADO_FLAG_CH16_CLEAR,
    GRADO_FLAG_CH16_QUAKE_WARN,
    GRADO_FLAG_CH16_CITY_REVEALED,
    GRADO_FLAG_CH16_ARMORY,
    GRADO_FLAG_CH16_CHEST,
    GRADO_FLAG_CH16_EVAC,
    GRADO_FLAG_CH16_WEISS_DOWN,
    GRADO_FLAG_CH16_OMEN,
    GRADO_FLAG_CH17_CLEAR,
    GRADO_FLAG_CH17_CORMAG_GLEN,
    GRADO_FLAG_CH17_DUESSEL_STAND,
    GRADO_FLAG_CH17_GARM,
    GRADO_FLAG_CH17_GLEIPNIR,
    GRADO_FLAG_CH17_PHANTOM_VALTER,
    GRADO_FLAG_CH17_PHANTOM_SELENA,
    GRADO_FLAG_CH17_PHANTOM_CAELLACH,
    GRADO_FLAG_CH17_PHANTOM_RIEV,
    GRADO_FLAG_CH17_PHANTOM_GLEN,
    GRADO_FLAG_CH17_RODERICH_RELICS,
    GRADO_FLAG_CH17_CHEST,
    GRADO_FLAG_CH18_DIR_CENTRAL,
    GRADO_FLAG_CH18_DIR_AUTONOMY,
    GRADO_FLAG_CH18_DIR_FOREIGN,
    GRADO_FLAG_CH18_DIR_RECKONING,
    GRADO_FLAG_CH18_TRANSITION_SMOOTH,
    GRADO_FLAG_CH18_TRANSITION_MIXED,
    GRADO_FLAG_CH18_TRANSITION_CONFLICT,
    GRADO_FLAG_CH18_ABDICATE,
    GRADO_FLAG_CH18_FORCED_TRANSITION,
    GRADO_FLAG_CH18_CLEAR,
    GRADO_FLAG_CH18_DEFEND_HELD,
    GRADO_FLAG_CH18_DELEGATES,
    GRADO_FLAG_CH18_DELEGATE_COLLAPSE,
    GRADO_FLAG_CH18_ELWIN_FREED,
    GRADO_FLAG_CH18X_OPEN,
    GRADO_FLAG_CH18X_CLEAR,
    GRADO_FLAG_CH18X_RESPONSE_AMELIA,
    GRADO_FLAG_CH18X_RESPONSE_DUESSEL,
    GRADO_FLAG_CH18X_RESPONSE_KNOLL,
    GRADO_FLAG_CH18X_RESPONSE_CORMAG,
    GRADO_FLAG_CH18X_RESPONSE_NATASHA,
    GRADO_FLAG_CH18X_MEMORY,
    GRADO_FLAG_CH18X_COLLAPSE_WARN,
    GRADO_FLAG_CH18X_PHANTOM_NW,
    GRADO_FLAG_CH18X_PHANTOM_NE,
    GRADO_FLAG_CH18X_PHANTOM_SW,
    GRADO_FLAG_CH18X_PHANTOM_SE,
    GRADO_FLAG_CH18X_PHANTOM_S,
    GRADO_FLAG_CH18X_CHEST,
    GRADO_FLAG_FINAL_ENDING,
    GRADO_FLAG_FINAL_SEALED,
    GRADO_FLAG_FINAL_NAME,
    GRADO_FLAG_FINAL_WITNESS,
    GRADO_FLAG_FINAL_ABANDON,
    GRADO_FLAG_FINAL_DATA,
    GRADO_FLAG_FINAL_MARTYR,
    GRADO_FLAG_FINAL_ELITE,
    GRADO_FLAG_FINAL_CLEAR,
};

int GetUnitItemSlot(struct Unit * unit, int item)
{
    (void)unit;
    (void)item;
    abort();
}

static void CheckRescueSubsets(void)
{
    const unsigned int counts[] = {0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4};
    const enum GradoRescueFact civilians[] = {
        GRADO_RESCUE_CIV_04, GRADO_RESCUE_CIV_01, GRADO_RESCUE_CIV_03, GRADO_RESCUE_CIV_02,
    };
    const enum GradoRescueFact archives[] = {
        GRADO_RESCUE_LYON_RESEARCH, GRADO_RESCUE_MILITARY_ORDERS,
        GRADO_RESCUE_TREASURY_LOGISTICS, GRADO_RESCUE_CIVILIAN_RELIEF,
    };
    unsigned int mask;
    unsigned int member;
    u16 value;

    for (mask = 0; mask < 16; ++mask)
    {
        ResetPermanentFlags();
        for (member = 0; member < 4; ++member)
        {
            if (mask & (1u << member))
            {
                assert(GradoState_RecordRescue(civilians[member]) == GRADO_STATE_VALUE_VALID);
                assert(GradoState_RecordRescue(civilians[member]) == GRADO_STATE_VALUE_VALID);
            }
            else
            {
                assert(GradoState_RecordRescue(archives[member]) == GRADO_STATE_VALUE_VALID);
                assert(GradoState_RecordRescue(archives[member]) == GRADO_STATE_VALUE_VALID);
            }
        }
        assert(GradoState_ReadSymbol(GRADO_FLAG_CH04_EVAC_COUNT, 17, &value)
            == GRADO_STATE_VALUE_VALID);
        assert(value == counts[mask]);
        assert(GradoState_ReadSymbol(GRADO_FLAG_CH05_ARCHIVE_COUNT, 20, &value)
            == GRADO_STATE_VALUE_VALID);
        assert(value == counts[15 - mask]);
        for (member = 0; member < 4; ++member)
        {
            assert(GradoState_ReadRescue(civilians[member], &value) == GRADO_STATE_VALUE_VALID);
            assert(value == !!(mask & (1u << member)));
            assert(GradoState_ReadRescue(archives[member], &value) == GRADO_STATE_VALUE_VALID);
            assert(value == !(mask & (1u << member)));
        }
    }
    ResetPermanentFlags();
}

static void CheckRetainedContinuity(void)
{
    const struct
    {
        enum GradoStateSymbol symbol;
        unsigned int producer;
        unsigned int reader;
    } retained[] = {
        {GRADO_FLAG_CH05X_PERSUADE, 6, 12},
        {GRADO_FLAG_CH05X_LETTER_A, 6, 11},
        {GRADO_FLAG_CH05X_LETTER_B, 6, 13},
        {GRADO_FLAG_CH05X_LETTERS_ALL, 6, 13},
        {GRADO_FLAG_CH09_ELNA, 10, 16},
        {GRADO_FLAG_CH09_ARTUR, 10, 16},
        {GRADO_FLAG_CH02_RODERICH_SEEN, 2, 19},
        {GRADO_FLAG_CH02_SHOP_OPEN, 2, 7},
        {GRADO_FLAG_CH10_RODERICH, 11, 19},
        {GRADO_FLAG_CH10_WEISS_GONE, 11, 17},
        {GRADO_FLAG_CH11_REGION_PENINSULA, 12, 14},
        {GRADO_FLAG_CH11_REGION_SOUTH, 12, 17},
        {GRADO_FLAG_CH12_NEWLYON_DENIED, 13, 18},
        {GRADO_FLAG_CH12_DENY, 13, 21},
        {GRADO_FLAG_CH13_REFUGEE, 14, 21},
        {GRADO_FLAG_CH14_REP_SAFE, 15, 21},
        {GRADO_FLAG_CH14_ELWIN_SAVED, 15, 21},
        {GRADO_FLAG_CH14_FORGERY_NAMED, 15, 19},
        {GRADO_FLAG_CH14_LUND_EXPOSED, 15, 19},
        {GRADO_FLAG_CH16_EVAC, 17, 21},
        {GRADO_FLAG_CH18_DELEGATES, 19, 21},
    };
    enum GradoStateValueResult result;
    unsigned int i;
    unsigned int failures;
    u16 value;

    failures = 0;
    for (i = 0; i < sizeof(retained) / sizeof(retained[0]); ++i)
    {
        assert(GradoState_WriteSymbol(retained[i].symbol, retained[i].producer, 1)
            == GRADO_STATE_VALUE_VALID);
        ResetChapterFlags();
        value = 0xBEEF;
        result = GradoState_ReadSymbol(retained[i].symbol, retained[i].reader, &value);
        if (result != GRADO_STATE_VALUE_VALID || value != 1)
        {
            fprintf(stderr, "symbol=%u producer=%u reader=%u result=%u value=%u\n",
                retained[i].symbol, retained[i].producer, retained[i].reader, result, value);
            ++failures;
        }
        value = 0xBEEF;
        result = GradoState_ReadSymbol(retained[i].symbol, 21, &value);
        if (result != GRADO_STATE_VALUE_VALID || value != 1)
            ++failures;
    }
    assert(failures == 0);
    ResetPermanentFlags();
}

static void CheckTypedDomains(void)
{
    const struct
    {
        enum GradoStateSymbol symbol;
        unsigned int chapter;
        unsigned int maximum;
        int oneHot;
        enum GradoStateSymbol aliases[4];
        unsigned int aliasCount;
    } domains[] = {
        {GRADO_STATE_LEGITIMACY, 2, 3, 1,
            {GRADO_FLAG_CH02_CENTRAL, GRADO_FLAG_CH02_COUNCIL, GRADO_FLAG_CH02_LOCAL}, 3},
        {GRADO_STATE_RENAIS, 7, 3, 1,
            {GRADO_FLAG_CH06_FULL, GRADO_FLAG_CH06_LIMITED, GRADO_FLAG_CH06_DECLINE}, 3},
        {GRADO_STATE_SALTROAD, 9, 3, 1,
            {GRADO_FLAG_CH08_SUPPRESS, GRADO_FLAG_CH08_CONCEDE, GRADO_FLAG_CH08_AUTONOMY}, 3},
        {GRADO_STATE_TRUTH, 11, 3, 1,
            {GRADO_FLAG_CH10_FULL, GRADO_FLAG_CH10_LIMITED, GRADO_FLAG_CH10_SEALED}, 3},
        {GRADO_STATE_RATION, 12, 3, 1,
            {GRADO_FLAG_CH11_CAPITAL, GRADO_FLAG_CH11_FRONTIER, GRADO_FLAG_CH11_EVEN}, 3},
        {GRADO_STATE_WYVERN, 14, 3, 1,
            {GRADO_FLAG_CH13_SHIELD, GRADO_FLAG_CH13_ARMY, GRADO_FLAG_CH13_DISBAND}, 3},
        {GRADO_STATE_VILLAGE, 15, 3, 1,
            {GRADO_FLAG_CH14_SELF, GRADO_FLAG_CH14_GARRISON, GRADO_FLAG_CH14_MEDIATE}, 3},
        {GRADO_STATE_RAUSTEN_MED, 16, 3, 1,
            {GRADO_FLAG_CH15_MED_FULL, GRADO_FLAG_CH15_MED_LIMITED, GRADO_FLAG_CH15_MED_REFUSE}, 3},
        {GRADO_STATE_RAUSTEN_DATA, 16, 3, 1,
            {GRADO_FLAG_CH15_DATA_SURRENDER, GRADO_FLAG_CH15_DATA_JOINT,
                GRADO_FLAG_CH15_DATA_RETAIN}, 3},
        {GRADO_STATE_DIRECTION, 19, 4, 1,
            {GRADO_FLAG_CH18_DIR_CENTRAL, GRADO_FLAG_CH18_DIR_AUTONOMY,
                GRADO_FLAG_CH18_DIR_FOREIGN, GRADO_FLAG_CH18_DIR_RECKONING}, 4},
        {GRADO_STATE_RODERICH, 19, 3, 1},
        {GRADO_STATE_LUND, 19, 3, 1},
        {GRADO_FLAG_CH07_GUARD_KEPT, 8, 3, 1},
        {GRADO_FLAG_CH09_MEDNET, 10, 3, 1},
        {GRADO_FLAG_CH03_WYVERN_KEPT, 3, 3, 0},
        {GRADO_FLAG_CH10_WITNESS_SAFE, 11, 3, 0},
        {GRADO_FLAG_CH12_CORES, 13, 4, 0},
        {GRADO_FLAG_CH13_GUARD, 14, 2, 0},
        {GRADO_FLAG_CH13_REFUGEE, 14, 3, 0},
        {GRADO_FLAG_CH16_EVAC, 17, 3, 0},
        {GRADO_FLAG_CH18_DELEGATES, 19, 4, 0},
        {GRADO_FLAG_CH18X_MEMORY, 20, 5, 0},
    };
    const enum GradoStateSymbol retired[] = {
        GRADO_STATE_RETREAT_DUESSEL, GRADO_STATE_RETREAT_KNOLL,
        GRADO_STATE_RETREAT_CORMAG, GRADO_STATE_RETREAT_NATASHA,
    };
    enum GradoStateValueResult result;
    u8 permanent[25];
    u8 chapter[5];
    unsigned int i;
    unsigned int expected;
    unsigned int alias;
    u16 value;
    u16 selected;

    for (i = 0; i < sizeof(domains) / sizeof(domains[0]); ++i)
    {
        ResetPermanentFlags();
        ResetChapterFlags();
        assert(GradoState_WriteSymbol(domains[i].symbol, domains[i].chapter, domains[i].maximum)
            == GRADO_STATE_VALUE_VALID);
        assert(GradoState_ReadSymbol(domains[i].symbol, domains[i].chapter, &value)
            == GRADO_STATE_VALUE_VALID);
        assert(value == domains[i].maximum);
        for (expected = 0; expected <= domains[i].maximum; ++expected)
        {
            assert(GradoState_WriteSymbol(domains[i].symbol, domains[i].chapter, expected)
                == GRADO_STATE_VALUE_VALID);
            value = 0xBEEF;
            result = GradoState_ReadSymbol(domains[i].symbol, domains[i].chapter, &value);
            if (domains[i].oneHot && expected == 0)
            {
                assert(result == GRADO_STATE_VALUE_UNSET);
                assert(value == 0xBEEF);
            }
            else
            {
                assert(result == GRADO_STATE_VALUE_VALID);
                assert(value == expected);
            }
            for (alias = 0; alias < domains[i].aliasCount; ++alias)
            {
                assert(GradoState_ReadSymbol(domains[i].aliases[alias], domains[i].chapter,
                    &selected) == GRADO_STATE_VALUE_VALID);
                assert(selected == (expected == alias + 1));
            }
        }
        memcpy(permanent, gPermanentFlagBits, sizeof(permanent));
        memcpy(chapter, gChapterFlagBits, sizeof(chapter));
        assert(GradoState_WriteSymbol(domains[i].symbol, domains[i].chapter,
            domains[i].maximum + 1) == GRADO_STATE_VALUE_RANGE);
        assert(memcmp(permanent, gPermanentFlagBits, sizeof(permanent)) == 0);
        assert(memcmp(chapter, gChapterFlagBits, sizeof(chapter)) == 0);
    }
    for (i = 0; i < sizeof(retired) / sizeof(retired[0]); ++i)
    {
        assert(GradoState_WriteSymbol(retired[i], 20, 1) == GRADO_STATE_VALUE_VALID);
        assert(GradoState_WriteSymbol(retired[i], 20, 1) == GRADO_STATE_VALUE_VALID);
        assert(GradoState_WriteSymbol(retired[i], 20, 0) == GRADO_STATE_VALUE_MONOTONIC);
        assert(GradoState_ReadSymbol(retired[i], 20, &value) == GRADO_STATE_VALUE_VALID);
        assert(value == 1);
    }
    ResetPermanentFlags();
    ResetChapterFlags();
}

int main(void)
{
    enum GradoStateValueResult result;
    u8 beforePermanent[25];
    u8 beforeChapter[5];
    u8 visited[GRADO_STATE_SYMBOL_COUNT] = {0};
    unsigned int symbol;
    unsigned int direction;
    u16 value;

    assert(GradoState_ValidateCampaign() == GRADO_ALLOCATION_VALID);
    CheckRescueSubsets();
    CheckRetainedContinuity();
    CheckTypedDomains();

    assert(sizeof(sRequiredSymbols) / sizeof(sRequiredSymbols[0]) == 16 + 227);
    assert(sizeof(sRequiredSymbols) / sizeof(sRequiredSymbols[0]) == GRADO_STATE_SYMBOL_COUNT);
    for (symbol = 0; symbol < sizeof(sRequiredSymbols) / sizeof(sRequiredSymbols[0]); ++symbol)
    {
        assert((unsigned int)sRequiredSymbols[symbol] < GRADO_STATE_SYMBOL_COUNT);
        assert(!visited[sRequiredSymbols[symbol]]);
        visited[sRequiredSymbols[symbol]] = 1;
        value = 0xBEEF;
        result = GradoState_ReadSymbol(sRequiredSymbols[symbol], 0, &value);
        assert(result == GRADO_STATE_VALUE_VALID || result == GRADO_STATE_VALUE_UNSET
            || result == GRADO_STATE_VALUE_CHAPTER);
    }

    assert(GradoState_WriteSymbol(GRADO_STATE_LEGITIMACY, 2, 2) == GRADO_STATE_VALUE_VALID);
    assert(GradoState_ReadSymbol(GRADO_FLAG_CH02_COUNCIL, 3, &value) == GRADO_STATE_VALUE_VALID);
    assert(value == 1);
    assert(GradoState_ReadSymbol(GRADO_FLAG_CH02_CENTRAL, 3, &value) == GRADO_STATE_VALUE_VALID);
    assert(value == 0);
    for (direction = 1; direction <= 4; ++direction)
    {
        assert(GradoState_WriteSymbol(GRADO_STATE_DIRECTION, 20, direction)
            == GRADO_STATE_VALUE_VALID);
        assert(GradoState_ReadSymbol(GRADO_FLAG_FINAL_ENDING, 21, &value)
            == GRADO_STATE_VALUE_VALID);
        assert(value == direction);
    }

    assert(GradoState_RecordRescue(GRADO_RESCUE_CIV_02) == GRADO_STATE_VALUE_VALID);
    assert(GradoState_RecordRescue(GRADO_RESCUE_CIV_02) == GRADO_STATE_VALUE_VALID);
    assert(GradoState_RecordRescue(GRADO_RESCUE_CIV_04) == GRADO_STATE_VALUE_VALID);
    assert(GradoState_ReadSymbol(GRADO_FLAG_CH04_EVAC_COUNT, 17, &value) ==
        GRADO_STATE_VALUE_VALID);
    assert(value == 2);
    assert(GradoState_RecordRescue(GRADO_RESCUE_MILITARY_ORDERS) == GRADO_STATE_VALUE_VALID);
    assert(GradoState_RecordRescue(GRADO_RESCUE_LYON_RESEARCH) == GRADO_STATE_VALUE_VALID);
    assert(GradoState_ReadSymbol(GRADO_FLAG_CH05_ARCHIVE_COUNT, 20, &value) ==
        GRADO_STATE_VALUE_VALID);
    assert(value == 2);
    assert(GradoState_ReadRescue(GRADO_RESCUE_LYON_RESEARCH, &value) == GRADO_STATE_VALUE_VALID);
    assert(value == 1);
    assert(GradoState_ReadRescue(GRADO_RESCUE_TREASURY_LOGISTICS, &value) ==
        GRADO_STATE_VALUE_VALID);
    assert(value == 0);

    assert(GradoState_WriteSymbol(GRADO_FLAG_CH03_WYVERN_KEPT, 3, 3) == GRADO_STATE_VALUE_VALID);
    assert(GradoState_WriteSymbol(GRADO_FLAG_CH07_GUARD_KEPT, 8, 2) == GRADO_STATE_VALUE_VALID);
    assert(GradoState_WriteSymbol(GRADO_FLAG_CH13_GUARD, 14, 2) == GRADO_STATE_VALUE_VALID);
    assert(GradoState_WriteSymbol(GRADO_STATE_RETREAT_KNOLL, 3, 1) == GRADO_STATE_VALUE_VALID);

    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    memcpy(beforeChapter, gChapterFlagBits, sizeof(beforeChapter));
    assert(GradoState_WriteSymbol(GRADO_FLAG_CH02_COUNCIL, 2, 0) == GRADO_STATE_VALUE_READ_ONLY);
    assert(GradoState_WriteSymbol(GRADO_FLAG_FINAL_ENDING, 21, 1) == GRADO_STATE_VALUE_READ_ONLY);
    assert(GradoState_WriteSymbol(GRADO_FLAG_CH04_EVAC_COUNT, 4, 4) == GRADO_STATE_VALUE_READ_ONLY);
    assert(GradoState_WriteSymbol(GRADO_FLAG_CH05_ARCHIVE_COUNT, 5, 4) ==
        GRADO_STATE_VALUE_READ_ONLY);
    assert(GradoState_WriteSymbol(GRADO_FLAG_CH03_WYVERN_KEPT, 3, 4) == GRADO_STATE_VALUE_RANGE);
    assert(GradoState_WriteSymbol(GRADO_FLAG_CH07_GUARD_KEPT, 8, 4) == GRADO_STATE_VALUE_RANGE);
    assert(GradoState_WriteSymbol(GRADO_FLAG_CH13_GUARD, 14, 3) == GRADO_STATE_VALUE_RANGE);
    assert(GradoState_WriteSymbol(GRADO_FLAG_CH13_GUARD, 15, 1) == GRADO_STATE_VALUE_CHAPTER);
    assert(GradoState_WriteSymbol(GRADO_STATE_RETREAT_KNOLL, 17, 0) == GRADO_STATE_VALUE_MONOTONIC);
    assert(GradoState_RecordRescue(GRADO_RESCUE_COUNT) == GRADO_STATE_VALUE_INPUT);
    value = 0xBEEF;
    assert(GradoState_ReadSymbol(GRADO_STATE_SYMBOL_COUNT, 0, &value) == GRADO_STATE_VALUE_INPUT);
    assert(value == 0xBEEF);
    assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);

    SetFlag(139);
    memcpy(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent));
    value = 0xBEEF;
    assert(GradoState_ReadSymbol(GRADO_STATE_LEGITIMACY, 17, &value)
        == GRADO_STATE_VALUE_CORRUPT);
    assert(value == 0xBEEF);
    assert(GradoState_ReadSymbol(GRADO_FLAG_CH02_COUNCIL, 17, &value)
        == GRADO_STATE_VALUE_CORRUPT);
    assert(value == 0xBEEF);
    assert(memcmp(beforePermanent, gPermanentFlagBits, sizeof(beforePermanent)) == 0);
    assert(memcmp(beforeChapter, gChapterFlagBits, sizeof(beforeChapter)) == 0);
    return 0;
}
