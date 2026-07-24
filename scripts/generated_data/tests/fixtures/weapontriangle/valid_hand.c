/* Minimal fixture standing in for src/bmbattle.c's hand block, used to
 * test scripts/generated_data/weapontriangle/parser.py in isolation. */
static CONST_DATA struct WeaponTriangleRule sWeaponTriangleRules[] = {
    { ITYPE_SWORD, ITYPE_LANCE, -15, -1 },
    { ITYPE_SWORD, ITYPE_AXE,   +15, +1 },

    { ITYPE_LANCE, ITYPE_AXE,   -15, -1 },
    { ITYPE_LANCE, ITYPE_SWORD, +15, +1 },

    { ITYPE_AXE,   ITYPE_SWORD, -15, -1 },
    { ITYPE_AXE,   ITYPE_LANCE, +15, +1 },

    { ITYPE_ANIMA, ITYPE_DARK,  -15, -1 },
    { ITYPE_ANIMA, ITYPE_LIGHT, +15, +1 },

    { ITYPE_LIGHT, ITYPE_ANIMA, -15, -1 },
    { ITYPE_LIGHT, ITYPE_DARK,  +15, +1 },

    { ITYPE_DARK,  ITYPE_LIGHT, -15, -1 },
    { ITYPE_DARK,  ITYPE_ANIMA, +15, +1 },

    { -1 },
};
