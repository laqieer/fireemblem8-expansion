#include "global.h"

#include "expansion_portraits.h"

/*
 * Add project-owned rules before the sentinel and update the count. Rules
 * are evaluated in declaration order, so the first matching nonzero result
 * wins. An empty registry preserves the legacy character/class fallback.
 */
const struct ExpansionPortraitRule gExpansionPortraitRules[] = {
    { 0, 0, 0, 0, 0, 0, 0, 0 },
};

const unsigned gExpansionPortraitRuleCount = 0;

static int IsValidPortraitId(u16 id)
{
    return id != 0 && id <= EXPANSION_PORTRAIT_FULL_ID_MAX;
}

static int IsValidMinimugId(u16 id)
{
    return id >= EXPANSION_PORTRAIT_MINIMUG_ID_MIN
        && id <= EXPANSION_PORTRAIT_MINIMUG_ID_MAX;
}

int ExpansionPortrait_ValidateRegistry(void)
{
    unsigned i;

    for (i = 0; i < gExpansionPortraitRuleCount; ++i) {
        const struct ExpansionPortraitRule *rule = &gExpansionPortraitRules[i];

        if (rule->character_id != EXPANSION_PORTRAIT_MATCH_ANY && rule->character_id == 0)
            return 0;

        if (rule->class_id != EXPANSION_PORTRAIT_MATCH_ANY && rule->class_id == 0)
            return 0;

        if (rule->reserved != 0)
            return 0;

        if (rule->required_flags & rule->forbidden_flags)
            return 0;

        if (rule->full_portrait_id != 0 && !IsValidPortraitId(rule->full_portrait_id))
            return 0;

        if (rule->minimug_id != 0 && !IsValidMinimugId(rule->minimug_id))
            return 0;
    }

    return 1;
}

static int RuleMatches(
    const struct ExpansionPortraitRule *rule,
    const struct ExpansionPortraitContext *context
)
{
    if (rule->character_id != EXPANSION_PORTRAIT_MATCH_ANY
        && rule->character_id != context->character_id)
        return 0;

    if (rule->class_id != EXPANSION_PORTRAIT_MATCH_ANY
        && rule->class_id != context->class_id)
        return 0;

    if (rule->chapter_id != EXPANSION_PORTRAIT_CHAPTER_ANY
        && rule->chapter_id != context->chapter_id)
        return 0;

    if ((context->flags & rule->required_flags) != rule->required_flags)
        return 0;

    if (context->flags & rule->forbidden_flags)
        return 0;

    return 1;
}

static u16 ResolveLegacy(
    const struct ExpansionPortraitContext *context,
    enum ExpansionPortraitKind kind
)
{
    const struct CharacterData *character = context->character;
    const struct ClassData *class_data = context->class_data;

    if (kind == EXPANSION_PORTRAIT_KIND_MINIMUG && character != NULL
        && character->miniPortrait != 0)
        return EXPANSION_PORTRAIT_MINIMUG_ID_MIN + character->miniPortrait;

    if (character != NULL && character->portraitId != 0) {
        if (context->unit != NULL
            && context->chapter_id == 0x22
            && character->portraitId == 0x4A)
            return 0x46;

        return character->portraitId;
    }

    if (class_data != NULL && class_data->defaultPortraitId != 0)
        return class_data->defaultPortraitId;

    return 0;
}

u16 ExpansionPortrait_Resolve(
    const struct ExpansionPortraitContext *context,
    enum ExpansionPortraitKind kind
)
{
    unsigned i;

    if (context == NULL)
        return 0;

    if (kind != EXPANSION_PORTRAIT_KIND_FULL
        && kind != EXPANSION_PORTRAIT_KIND_MINIMUG)
        return 0;

    if (!ExpansionPortrait_ValidateRegistry())
        return ResolveLegacy(context, kind);

    for (i = 0; i < gExpansionPortraitRuleCount; ++i) {
        const struct ExpansionPortraitRule *rule = &gExpansionPortraitRules[i];
        u16 result;

        if (!RuleMatches(rule, context))
            continue;

        result = kind == EXPANSION_PORTRAIT_KIND_MINIMUG
            ? rule->minimug_id
            : rule->full_portrait_id;

        if (result != 0)
            return result;
    }

    return ResolveLegacy(context, kind);
}

u16 ExpansionPortrait_ResolveUnitWithFlags(
    const struct Unit *unit,
    enum ExpansionPortraitKind kind,
    u32 flags
)
{
    struct ExpansionPortraitContext context;

    context.unit = unit;
    context.character = unit != NULL ? unit->pCharacterData : NULL;
    context.class_data = unit != NULL ? unit->pClassData : NULL;
    context.character_id = unit != NULL && unit->pCharacterData != NULL
        ? unit->pCharacterData->number
        : 0;
    context.class_id = unit != NULL && unit->pClassData != NULL
        ? unit->pClassData->number
        : 0;
    context.chapter_id = gPlaySt.chapterIndex;
    context.flags = flags;

    return ExpansionPortrait_Resolve(&context, kind);
}

u16 ExpansionPortrait_ResolveUnit(const struct Unit *unit, enum ExpansionPortraitKind kind)
{
    return ExpansionPortrait_ResolveUnitWithFlags(unit, kind, 0);
}

u16 ExpansionPortrait_ResolveCharacter(
    const struct CharacterData *character,
    const struct ClassData *class_data,
    enum ExpansionPortraitKind kind,
    u32 flags
)
{
    struct ExpansionPortraitContext context;

    context.unit = NULL;
    context.character = character;
    context.class_data = class_data;
    context.character_id = character != NULL ? character->number : 0;
    context.class_id = class_data != NULL ? class_data->number : 0;
    context.chapter_id = gPlaySt.chapterIndex;
    context.flags = flags;

    return ExpansionPortrait_Resolve(&context, kind);
}
