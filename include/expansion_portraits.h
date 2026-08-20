#ifndef GUARD_EXPANSION_PORTRAITS_H
#define GUARD_EXPANSION_PORTRAITS_H

#include "bmunit.h"

#define EXPANSION_PORTRAIT_MATCH_ANY 0xFFFF
#define EXPANSION_PORTRAIT_CHAPTER_ANY 0xFF
#define EXPANSION_PORTRAIT_FULL_ID_MAX 0x00AC
#define EXPANSION_PORTRAIT_MINIMUG_ID_MIN 0x7F00
#define EXPANSION_PORTRAIT_MINIMUG_ID_MAX 0x7F07

enum ExpansionPortraitKind {
    EXPANSION_PORTRAIT_KIND_FULL = 0,
    EXPANSION_PORTRAIT_KIND_MINIMUG = 1,
};

struct ExpansionPortraitContext {
    const struct Unit *unit;
    const struct CharacterData *character;
    const struct ClassData *class_data;
    u16 character_id;
    u16 class_id;
    u8 chapter_id;
    u32 flags;
};

struct ExpansionPortraitRule {
    u16 character_id;
    u16 class_id;
    u8 chapter_id;
    u8 reserved;
    u32 required_flags;
    u32 forbidden_flags;
    u16 full_portrait_id;
    u16 minimug_id;
};

extern const struct ExpansionPortraitRule gExpansionPortraitRules[];
extern const unsigned gExpansionPortraitRuleCount;

u16 ExpansionPortrait_Resolve(
    const struct ExpansionPortraitContext *context,
    enum ExpansionPortraitKind kind
);
u16 ExpansionPortrait_ResolveUnit(const struct Unit *unit, enum ExpansionPortraitKind kind);
u16 ExpansionPortrait_ResolveUnitWithFlags(
    const struct Unit *unit,
    enum ExpansionPortraitKind kind,
    u32 flags
);
u16 ExpansionPortrait_ResolveCharacter(
    const struct CharacterData *character,
    const struct ClassData *class_data,
    enum ExpansionPortraitKind kind,
    u32 flags
);
int ExpansionPortrait_ValidateRegistry(void);

#endif
