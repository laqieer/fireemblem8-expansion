#ifndef GUARD_FIXTURE_BMITEM_ALIAS_H
#define GUARD_FIXTURE_BMITEM_ALIAS_H

/* Like mini_bmitem.h, but with a second ITYPE_* name aliasing the same
 * numeric value as ITYPE_SWORD -- used to prove weaponType round-trip
 * comparison is symbol-blind (resolves through the live enum mapping),
 * not a raw string compare, matching attributes/requiredWexp/
 * weaponEffect. include/bmitem.h's real ITYPE_* enum has no such alias
 * (every value is distinct), so this is a synthetic fixture. */

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
    ITYPE_SWORD       = 0,
    ITYPE_SWORD_ALIAS = 0,
    ITYPE_LANCE       = 1,
    ITYPE_ITEM        = 2,
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
