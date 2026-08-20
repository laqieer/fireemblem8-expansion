#include <stdio.h>
#include <string.h>

#include "global.h"
#include "expansion_aoe_reference.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "AOE_DISABLED_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

int main(void)
{
    struct ExpansionAoETargetSet targets;
    struct ExpansionAoEExecutionResult result;

    memset(&targets, 0xA5, sizeof(targets));
    memset(&result, 0xA5, sizeof(result));

    CHECK(ExpansionAoEReference_IsEnabled() == 0, "reference must default off");
    CHECK(ExpansionAoEReference_Apply(1, &targets, &result)
              == EXPANSION_AOE_ERR_DISABLED,
          "disabled reference returns ERR_DISABLED");
    CHECK(targets.count == 0 && targets.totalCount == 0,
          "disabled reference produces no targets");
    CHECK(result.appliedCount == 0 && result.expAwarded == 0,
          "disabled reference produces no effects");
    printf("AOE_DISABLED_HOST_TEST: PASS\n");
    return 0;
}
