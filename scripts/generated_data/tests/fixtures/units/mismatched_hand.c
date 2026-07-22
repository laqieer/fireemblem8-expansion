CONST_DATA struct REDA REDA_Fixture_0[] = {
    {
        .x = 2,
        .y = 2,
        .b = 0xffff,
    },
};

CONST_DATA struct UnitDefinition UnitDef_Fixture_Test[] = {
    {
        .charIndex = CHARACTER_EIRIKA,
        .classIndex = CLASS_EIRIKA_LORD,
        .allegiance = FACTION_ID_BLUE,
        .level = 1,
        .xPosition = 1,
        .yPosition = 0,
        .redaCount = 1,
        .redas = REDA_Fixture_0,
        .items = {
            ITEM_SWORD_RAPIER,
            ITEM_VULNERARY,
        },
    },
    {
        .charIndex = 0x8e,
        .classIndex = CLASS_BRIGAND,
        .autolevel = 1,
        .allegiance = FACTION_ID_RED,
        .level = 5,
        .xPosition = 9,
        .yPosition = 14,
        .items = {
            ITEM_AXE_IRON,
        },
        .ai = {0x0, 0x11, 0x9, 0x0},
    },
    { 0 },
};
