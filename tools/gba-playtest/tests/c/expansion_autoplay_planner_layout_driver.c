#include "global.h"

#include <stddef.h>
#include <stdio.h>

#include "expansion_autoplay_planner.h"

int main(void)
{
    printf(
        "semantic_size=%lu\n",
        (unsigned long)sizeof(struct ExpansionAutoplayPlannerSemanticFieldV2));
    printf(
        "action_size=%lu\n",
        (unsigned long)sizeof(struct ExpansionAutoplayPlannerActionV2));
    printf(
        "unit_size=%lu\n",
        (unsigned long)sizeof(struct ExpansionAutoplayPlannerUnitV2));
    printf(
        "value_size=%lu\n",
        (unsigned long)sizeof(struct ExpansionAutoplayPlannerValueRecordV2));
    printf(
        "start_union_size=%lu\n",
        (unsigned long)sizeof(union ExpansionAutoplayPlannerRecordStartV2));
    printf(
        "count_union_size=%lu\n",
        (unsigned long)sizeof(union ExpansionAutoplayPlannerRecordCountV2));
    printf(
        "payload_union_size=%lu\n",
        (unsigned long)sizeof(union ExpansionAutoplayPlannerPayloadV2));
    printf(
        "observation_size=%lu\n",
        (unsigned long)sizeof(struct ExpansionAutoplayPlannerObservationV2));
    printf(
        "observation_start_offset=%lu\n",
        (unsigned long)offsetof(
            struct ExpansionAutoplayPlannerObservationV2,
            start));
    printf(
        "observation_count_offset=%lu\n",
        (unsigned long)offsetof(
            struct ExpansionAutoplayPlannerObservationV2,
            count));
    printf(
        "observation_payload_offset=%lu\n",
        (unsigned long)offsetof(
            struct ExpansionAutoplayPlannerObservationV2,
            payload));
    printf(
        "command_size=%lu\n",
        (unsigned long)sizeof(struct ExpansionAutoplayPlannerCommandV2));
    printf(
        "command_payload_offset=%lu\n",
        (unsigned long)offsetof(
            struct ExpansionAutoplayPlannerCommandV2,
            payload));
    printf(
        "command_result_offset=%lu\n",
        (unsigned long)offsetof(
            struct ExpansionAutoplayPlannerCommandV2,
            result));
    printf(
        "checkpoint_size=%lu\n",
        (unsigned long)sizeof(
            struct ExpansionAutoplayPlannerCampaignCheckpointV2));
    printf(
        "checkpoint_mode_offset=%lu\n",
        (unsigned long)offsetof(
            struct ExpansionAutoplayPlannerCampaignCheckpointV2,
            chapterMode));
    return 0;
}
