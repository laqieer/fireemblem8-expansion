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

int main(void)
{
    enum GradoStateValueResult result;
    u8 beforePermanent[25];
    u8 beforeChapter[5];
    unsigned int symbol;
    unsigned int direction;
    u16 value;

    assert(GradoState_ValidateCampaign() == GRADO_ALLOCATION_VALID);
    CheckRescueSubsets();
    CheckRetainedContinuity();

    for (symbol = 0; symbol < GRADO_STATE_SYMBOL_COUNT; ++symbol)
    {
        value = 0xBEEF;
        result = GradoState_ReadSymbol(symbol, 0, &value);
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
