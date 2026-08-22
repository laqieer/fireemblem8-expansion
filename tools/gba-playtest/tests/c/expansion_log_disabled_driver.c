#include <stdio.h>

#include "global.h"
#include "expansion_log.h"

static int sArgumentEvaluationCount;

static const char* SideEffect(void)
{
    sArgumentEvaluationCount++;
    return "release must not evaluate this";
}

int main(void)
{
    EXPANSION_LOG_INFO("%s", SideEffect());
    if (sArgumentEvaluationCount != 0)
    {
        fprintf(stderr, "EXPANSION_LOG_DISABLED_HOST_TEST: FAIL: macro evaluated arguments\n");
        return 1;
    }

    if (ExpansionLog_Write(EXPANSION_LOG_INFO, "release") != EXPANSION_LOG_DISABLED)
    {
        fprintf(stderr, "EXPANSION_LOG_DISABLED_HOST_TEST: FAIL: direct API was not inert\n");
        return 1;
    }

    printf("EXPANSION_LOG_DISABLED_HOST_TEST: PASS\n");
    return 0;
}
