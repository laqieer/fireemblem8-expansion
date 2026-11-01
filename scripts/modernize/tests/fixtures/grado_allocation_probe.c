#include "global.h"

#include <assert.h>
#include <string.h>

#include "grado_state.h"

struct SingleCase
{
    struct GradoStateField field;
    enum GradoAllocationResult progressive;
    enum GradoAllocationResult fixed;
};

struct LifetimeCase
{
    struct GradoStateField fields[3];
    enum GradoAllocationResult expected;
};

static void Check(
    struct GradoStateField * fields,
    unsigned int count,
    enum GradoGuideStoragePolicy policy,
    enum GradoAllocationResult expected)
{
    struct GradoStateField before[8];

    assert(count <= sizeof(before) / sizeof(before[0]));
    if (fields != 0 && count != 0)
        memcpy(before, fields, count * sizeof(*fields));

    assert(GradoState_ValidateAllocation(fields, count, policy) == expected);

    if (fields != 0 && count != 0)
        assert(memcmp(before, fields, count * sizeof(*fields)) == 0);
}

static void CheckSingles(void)
{
    static const struct SingleCase cases[] = {
        {{7, 1, GRADO_STATE_BOOLEAN, 1, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALID, GRADO_ALLOCATION_VALID},
        {{40, 1, GRADO_STATE_BOOLEAN, 1, 21, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALID, GRADO_ALLOCATION_VALID},
        {{7, 1, GRADO_STATE_BOOLEAN, 1, 22, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_SCOPE, GRADO_ALLOCATION_SCOPE},
        {{120, 1, GRADO_STATE_BOOLEAN, 1, 0, GRADO_STATE_PERMANENT},
            GRADO_ALLOCATION_SCOPE, GRADO_ALLOCATION_SCOPE},
        {{7, 1, GRADO_STATE_BOOLEAN, 1, 0, 2},
            GRADO_ALLOCATION_SCOPE, GRADO_ALLOCATION_SCOPE},
        {{7, 0, GRADO_STATE_BOOLEAN, 1, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALUE, GRADO_ALLOCATION_VALUE},
        {{7, 9, GRADO_STATE_COUNTER, 255, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALUE, GRADO_ALLOCATION_VALUE},
        {{7, 2, GRADO_STATE_BOOLEAN, 1, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALUE, GRADO_ALLOCATION_VALUE},
        {{7, 1, GRADO_STATE_BOOLEAN, 2, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALUE, GRADO_ALLOCATION_VALUE},
        {{7, 1, GRADO_STATE_ONE_HOT, 1, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALUE, GRADO_ALLOCATION_VALUE},
        {{7, 3, GRADO_STATE_ONE_HOT, 3, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALID, GRADO_ALLOCATION_VALID},
        {{7, 3, GRADO_STATE_ONE_HOT, 4, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALUE, GRADO_ALLOCATION_VALUE},
        {{7, 2, GRADO_STATE_COUNTER, 0, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALUE, GRADO_ALLOCATION_VALUE},
        {{7, 2, GRADO_STATE_COUNTER, 3, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALID, GRADO_ALLOCATION_VALID},
        {{7, 2, GRADO_STATE_COUNTER, 4, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALUE, GRADO_ALLOCATION_VALUE},
        {{7, 8, GRADO_STATE_COUNTER, 255, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALID, GRADO_ALLOCATION_VALID},
        {{7, 8, GRADO_STATE_COUNTER, 256, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALUE, GRADO_ALLOCATION_VALUE},
        {{7, 1, 255, 1, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_VALUE, GRADO_ALLOCATION_VALUE},
        {{0, 1, GRADO_STATE_BOOLEAN, 1, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_CAPACITY, GRADO_ALLOCATION_CAPACITY},
        {{40, 2, GRADO_STATE_COUNTER, 3, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_CAPACITY, GRADO_ALLOCATION_CAPACITY},
        {{100, 1, GRADO_STATE_BOOLEAN, 1, 255, GRADO_STATE_PERMANENT},
            GRADO_ALLOCATION_CAPACITY, GRADO_ALLOCATION_CAPACITY},
        {{300, 1, GRADO_STATE_BOOLEAN, 1, 255, GRADO_STATE_PERMANENT},
            GRADO_ALLOCATION_VALID, GRADO_ALLOCATION_VALID},
        {{300, 2, GRADO_STATE_COUNTER, 3, 255, GRADO_STATE_PERMANENT},
            GRADO_ALLOCATION_CAPACITY, GRADO_ALLOCATION_CAPACITY},
        {{38, 2, GRADO_STATE_ONE_HOT, 2, 0, GRADO_STATE_CHAPTER},
            GRADO_ALLOCATION_RESERVED, GRADO_ALLOCATION_RESERVED},
        {{126, 5, GRADO_STATE_COUNTER, 31, 255, GRADO_STATE_PERMANENT},
            GRADO_ALLOCATION_RESERVED, GRADO_ALLOCATION_RESERVED},
        {{178, 2, GRADO_STATE_COUNTER, 3, 255, GRADO_STATE_PERMANENT},
            GRADO_ALLOCATION_RESERVED, GRADO_ALLOCATION_RESERVED},
        {{238, 3, GRADO_STATE_COUNTER, 7, 255, GRADO_STATE_PERMANENT},
            GRADO_ALLOCATION_GUIDE_OWNED, GRADO_ALLOCATION_VALID},
    };
    static const u16 reserved[] = {
        101,102,103,104,105,106,107,108,109,110,111,112,113,114,115,116,
        117,118,119,125,130,132,135,136,137,138,179
    };
    struct GradoStateField field;
    unsigned int i;

    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i)
    {
        field = cases[i].field;
        Check(&field, 1, GRADO_GUIDE_PROGRESSIVE, cases[i].progressive);
        Check(&field, 1, GRADO_GUIDE_STATIC, cases[i].fixed);
    }

    field = cases[0].field;
    for (i = 1; i <= 6; ++i)
    {
        field.firstFlag = i;
        Check(&field, 1, GRADO_GUIDE_PROGRESSIVE, GRADO_ALLOCATION_RESERVED);
        Check(&field, 1, GRADO_GUIDE_STATIC, GRADO_ALLOCATION_RESERVED);
    }
    field.firstFlag = 39;
    Check(&field, 1, GRADO_GUIDE_PROGRESSIVE, GRADO_ALLOCATION_RESERVED);
    Check(&field, 1, GRADO_GUIDE_STATIC, GRADO_ALLOCATION_RESERVED);

    field.scope = GRADO_STATE_PERMANENT;
    field.chapter = GRADO_STATE_NO_CHAPTER;
    for (i = 0; i < sizeof(reserved) / sizeof(reserved[0]); ++i)
    {
        field.firstFlag = reserved[i];
        Check(&field, 1, GRADO_GUIDE_PROGRESSIVE, GRADO_ALLOCATION_RESERVED);
        Check(&field, 1, GRADO_GUIDE_STATIC, GRADO_ALLOCATION_RESERVED);
    }
    for (i = 180; i <= 300; ++i)
    {
        field.firstFlag = i;
        Check(&field, 1, GRADO_GUIDE_STATIC, GRADO_ALLOCATION_VALID);
        Check(&field, 1, GRADO_GUIDE_PROGRESSIVE,
            i <= 235 || (i >= 240 && i <= 295)
                ? GRADO_ALLOCATION_GUIDE_OWNED : GRADO_ALLOCATION_VALID);
    }

    Check(0, 1, GRADO_GUIDE_STATIC, GRADO_ALLOCATION_INPUT);
    Check(&field, 0, GRADO_GUIDE_STATIC, GRADO_ALLOCATION_INPUT);
    Check(&field, 1, (enum GradoGuideStoragePolicy)2, GRADO_ALLOCATION_INPUT);
}

static void CheckLifetimes(void)
{
    static const struct LifetimeCase cases[] = {
        {{{7,3,GRADO_STATE_ONE_HOT,3,0,GRADO_STATE_CHAPTER},
          {10,2,GRADO_STATE_COUNTER,3,0,GRADO_STATE_CHAPTER},
          {12,1,GRADO_STATE_BOOLEAN,1,0,GRADO_STATE_CHAPTER}}, GRADO_ALLOCATION_VALID},
        {{{7,3,GRADO_STATE_ONE_HOT,3,0,GRADO_STATE_CHAPTER},
          {8,1,GRADO_STATE_BOOLEAN,1,0,GRADO_STATE_CHAPTER},
          {12,1,GRADO_STATE_BOOLEAN,1,0,GRADO_STATE_CHAPTER}}, GRADO_ALLOCATION_OVERLAP},
        {{{7,3,GRADO_STATE_ONE_HOT,3,0,GRADO_STATE_CHAPTER},
          {7,2,GRADO_STATE_COUNTER,3,1,GRADO_STATE_CHAPTER},
          {10,1,GRADO_STATE_BOOLEAN,1,0,GRADO_STATE_CHAPTER}}, GRADO_ALLOCATION_VALID},
        {{{140,3,GRADO_STATE_ONE_HOT,3,255,GRADO_STATE_PERMANENT},
          {142,1,GRADO_STATE_BOOLEAN,1,255,GRADO_STATE_PERMANENT},
          {146,2,GRADO_STATE_COUNTER,3,255,GRADO_STATE_PERMANENT}}, GRADO_ALLOCATION_OVERLAP},
        {{{140,3,GRADO_STATE_ONE_HOT,3,255,GRADO_STATE_PERMANENT},
          {143,1,GRADO_STATE_BOOLEAN,1,255,GRADO_STATE_PERMANENT},
          {146,2,GRADO_STATE_COUNTER,3,255,GRADO_STATE_PERMANENT}}, GRADO_ALLOCATION_VALID},
        {{{7,1,GRADO_STATE_BOOLEAN,1,0,GRADO_STATE_CHAPTER},
          {140,1,GRADO_STATE_BOOLEAN,1,255,GRADO_STATE_PERMANENT},
          {7,1,GRADO_STATE_BOOLEAN,1,1,GRADO_STATE_CHAPTER}}, GRADO_ALLOCATION_VALID},
    };
    static const u8 orders[6][3] = {{0,1,2},{0,2,1},{1,0,2},{1,2,0},{2,0,1},{2,1,0}};
    struct GradoStateField fields[8];
    struct GradoStateField saved;
    unsigned int i;
    unsigned int j;
    unsigned int k;

    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i)
        for (j = 0; j < sizeof(orders) / sizeof(orders[0]); ++j)
        {
            for (k = 0; k < 3; ++k)
                fields[k] = cases[i].fields[orders[j][k]];
            Check(fields, 3, GRADO_GUIDE_PROGRESSIVE, cases[i].expected);
            Check(fields, 3, GRADO_GUIDE_STATIC, cases[i].expected);
        }

    for (i = 0; i < 8; ++i)
    {
        fields[i].firstFlag = 7 + i;
        fields[i].bitCount = 1;
        fields[i].kind = GRADO_STATE_BOOLEAN;
        fields[i].maxValue = 1;
        fields[i].chapter = 0;
        fields[i].scope = GRADO_STATE_CHAPTER;
    }
    Check(fields, 8, GRADO_GUIDE_PROGRESSIVE, GRADO_ALLOCATION_VALID);
    Check(fields, 8, GRADO_GUIDE_STATIC, GRADO_ALLOCATION_VALID);
    for (i = 0; i < 8; ++i)
        for (j = i + 1; j < 8; ++j)
        {
            saved = fields[j];
            fields[j] = fields[i];
            Check(fields, 8, GRADO_GUIDE_PROGRESSIVE, GRADO_ALLOCATION_OVERLAP);
            Check(fields, 8, GRADO_GUIDE_STATIC, GRADO_ALLOCATION_OVERLAP);
            fields[j] = saved;
        }
}

int main(void)
{
    CheckSingles();
    CheckLifetimes();
    return 0;
}
