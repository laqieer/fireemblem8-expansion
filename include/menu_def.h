#ifndef GUARD_MENU_DEF_H
#define GUARD_MENU_DEF_H

struct MenuDef;
struct SelectTarget;

extern struct MenuDef CONST_DATA gDebugClearMenuDef;
extern struct MenuDef CONST_DATA gDebugChargeMenuDef;
extern struct MenuDef CONST_DATA gDebugContinueMenuDef;
extern struct MenuDef CONST_DATA gDebugChuudanMenuDef;
extern struct MenuDef CONST_DATA gDebugMenuDef;
extern struct MenuDef CONST_DATA gMenuInfo_RepairItems;
extern struct MenuDef CONST_DATA gStealItemMenuDef;
extern struct MenuDef CONST_DATA gYesNoSelectionMenuDef;
extern struct MenuDef CONST_DATA gItemSubMenuDef;
extern struct MenuDef CONST_DATA gItemMenuDef;
extern struct MenuDef CONST_DATA gStaffItemSelectMenuDef;
extern struct MenuDef CONST_DATA gItemSelectMenuDef;
extern struct MenuDef CONST_DATA gBallistaRangeMenuDef;
extern struct MenuDef CONST_DATA gWeaponSelectMenuDef;
extern struct MenuDef CONST_DATA gUnitActionMenuDef;

extern struct SelectInfo CONST_DATA gSelectInfo_OffensiveStaff;
extern struct SelectInfo CONST_DATA gSelectInfo_Barrier;
extern struct SelectInfo CONST_DATA gSelectInfo_Restore;
extern struct SelectInfo CONST_DATA gSelectInfo_Heal;
extern struct SelectInfo CONST_DATA gSelectInfo_Dance;
extern struct SelectInfo CONST_DATA gSelectInfo_PutTrap;
extern struct SelectInfo CONST_DATA gSelectInfo_WarpUnit;
extern struct SelectInfo CONST_DATA gSelectInfo_Steal;
extern struct SelectInfo CONST_DATA gSelectInfo_Summon;
extern struct SelectInfo CONST_DATA gSelectInfo_Pick;
extern struct SelectInfo CONST_DATA gSelectInfo_Support;
extern struct SelectInfo CONST_DATA gSelectInfo_Talk;
extern struct SelectInfo CONST_DATA gSelectInfo_Repair;
extern struct SelectInfo CONST_DATA gSelectInfo_Trade;
extern struct SelectInfo CONST_DATA gSelectInfo_Attack;
extern struct SelectInfo CONST_DATA gSelectInfo_Give;
extern struct SelectInfo CONST_DATA gSelectInfo_Take;
extern struct SelectInfo CONST_DATA gSelectInfo_Drop;
extern struct SelectInfo CONST_DATA gSelectInfo_Rescue;

#endif // GUARD_MENU_DEF_H
