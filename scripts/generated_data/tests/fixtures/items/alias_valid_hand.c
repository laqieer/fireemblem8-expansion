#include "global.h"
#include "bmitem.h"
#include "constants/items.h"

CONST_DATA struct ItemData gItemData[] = {
	[ITEM_FIXTURE_NONE] = {
		.number = ITEM_FIXTURE_NONE,
		.weaponType = ITYPE_SWORD,
		.iconId = 0x0,
	},
	[ITEM_FIXTURE_A] = {
		.nameTextId = 0x1,
		.descTextId = 0x2,
		.number = ITEM_FIXTURE_A,
		.weaponType = ITYPE_SWORD,
		.attributes = IA_WEAPON,
		.maxUses = 0x1E,
		.might = 0x5,
		.hit = 0x5A,
		.weight = 0x5,
		.encodedRange = 0x11,
		.costPerUse = 0xA,
		.weaponRank = WPN_EXP_E,
		.iconId = 0x0,
		.weaponExp = 0x1,
	},
	[ITEM_FIXTURE_B] = {
		.nameTextId = 0x3,
		.descTextId = 0x4,
		.number = ITEM_FIXTURE_B,
		.weaponType = ITYPE_ITEM,
		.pStatBonuses = &ItemBonus_FixtureBonus,
		.pEffectiveness = ItemEffectiveness_FixtureTarget,
		.iconId = 0x1,
		.weaponEffectId = WPN_EFFECT_POISON,
	},
};
