#ifndef GUARD_FIXTURE_BMITEM_H
#define GUARD_FIXTURE_BMITEM_H

enum {
    // Item attributes
    IA_NONE           = 0,

    IA_WEAPON         = (1 << 0),
    IA_MAGIC          = (1 << 1),
    IA_STAFF          = (1 << 2),
    IA_UNBREAKABLE    = (1 << 3),

    // Helpers
    IA_REQUIRES_WEXP = (IA_WEAPON | IA_STAFF),
};

enum {
    ITYPE_SWORD = 0,
    ITYPE_LANCE = 1,
    ITYPE_ITEM  = 2,
};

enum {
    WPN_EFFECT_NONE   = 0,
    WPN_EFFECT_POISON = 1,
};

enum {
    WPN_EXP_0 = 0,
    WPN_EXP_E = 1,
    WPN_EXP_D = 31,
};

extern CONST_DATA struct ItemStatBonuses ItemBonus_FixtureBonus;

#endif
