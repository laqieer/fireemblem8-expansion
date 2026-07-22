#include "global.h"
#include "bmreliance.h"
#include "constants/characters.h"

CONST_DATA struct SupportData SupportData_Eirika = {
    .characters = {
        CHARACTER_SETH,
        CHARACTER_FRANZ,
    },
    .supportExpBase = {
        10,
        5,
    },
    .supportExpGrowth = {
        2,
        1,
    },
    .supportCount = 2,
};

CONST_DATA struct SupportData SupportData_Seth = {
    .characters = {
        CHARACTER_EIRIKA,
    },
    .supportExpBase = {
        10,
    },
    .supportExpGrowth = {
        2,
    },
    .supportCount = 1,
};

CONST_DATA struct SupportData SupportData_Franz = {
    .characters = {
        CHARACTER_EIRIKA,
    },
    .supportExpBase = {
        5,
    },
    .supportExpGrowth = {
        1,
    },
    .supportCount = 1,
};
