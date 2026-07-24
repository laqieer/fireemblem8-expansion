/* Same as valid_hand.c but with the ITYPE_SWORD -> ITYPE_LANCE rule's
 * hitBonus/atkBonus values changed, for compare_records() mismatch
 * detection tests. */
static CONST_DATA struct WeaponTriangleRule sWeaponTriangleRules[] = {
    { ITYPE_SWORD, ITYPE_LANCE, -10, -1 },
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
