#include "global.h"
#include "bmunit.h"
#include "bmitem.h"
#include "bmreliance.h"
#include "constants/characters.h"
#include "constants/classes.h"
#include "constants/items.h"

CONST_DATA struct CharacterData gCharacterData[] = {
    [CHARACTER_EIRIKA - 1] = {
        .nameTextId = 10,
        .descTextId = 11,
        .number = CHARACTER_EIRIKA,
        .defaultClass = CLASS_MYRMIDON,
        .portraitId = 1,
        .affinity = UNIT_AFFIN_LIGHT,
        .baseLevel = 1,

        .baseHP  = 18,
        .basePow = 4,
        .baseSkl = 8,
        .baseSpd = 9,
        .baseDef = 4,
        .baseRes = 1,
        .baseLck = 5,
        .baseCon = 5,

        .baseRanks = {
            [ITYPE_SWORD] = WPN_EXP_E,
        },

        .growthHP  = 70,
        .growthPow = 40,
        .growthSkl = 60,
        .growthSpd = 60,
        .growthDef = 30,
        .growthRes = 30,
        .growthLck = 99,
        .attributes = CA_LORD | CA_FEMALE,
        .pSupportData = &SupportData_Eirika,
        .visit_group = 0x7,
    },
    [0x1b - 1] = {
        .nameTextId = 12,
        .descTextId = 13,
        .number = 0x1b,
        .defaultClass = CLASS_CAVALIER,
        .miniPortrait = 0x2,
        .baseLevel = 1,

        .baseHP  = 0,
        .basePow = 0,
        .baseSkl = 0,
        .baseSpd = 0,
        .baseDef = 0,
        .baseRes = 0,
        .baseLck = 0,
        .baseCon = 0,

        .baseRanks = {
            [ITYPE_LANCE] = WPN_EXP_D,
        },

        .growthHP  = 60,
        .growthPow = 20,
        .growthSkl = 30,
        .growthSpd = 35,
        .growthDef = 15,
        .growthRes = 15,
        .growthLck = 25,
    },
    [0x100 - 1] = {
        .nameTextId = 14,
        .descTextId = 15,
        .number = 0,
        .defaultClass = CLASS_CAVALIER,
        .baseLevel = 1,

        .baseHP  = 0,
        .basePow = 0,
        .baseSkl = 0,
        .baseSpd = 0,
        .baseDef = 0,
        .baseRes = 0,
        .baseLck = 0,
        .baseCon = 0,

        .growthHP  = 60,
        .growthPow = 20,
        .growthSkl = 30,
        .growthSpd = 35,
        .growthDef = 15,
        .growthRes = 15,
        .growthLck = 25,
    },
};
