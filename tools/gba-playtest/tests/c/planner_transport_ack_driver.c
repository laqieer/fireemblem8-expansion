#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

bool PlannerTransport_IsAcknowledgementValid(
    uint32_t result,
    uint32_t rejection);

int main(void)
{
    uint32_t rejection;

    if (!PlannerTransport_IsAcknowledgementValid(1, 0))
        return 1;
    for (rejection = 1; rejection <= 10; rejection++)
        if (!PlannerTransport_IsAcknowledgementValid(0, rejection))
            return 1;
    if (PlannerTransport_IsAcknowledgementValid(0, 0)
        || PlannerTransport_IsAcknowledgementValid(1, 1)
        || PlannerTransport_IsAcknowledgementValid(0, 11)
        || PlannerTransport_IsAcknowledgementValid(0, UINT32_MAX)
        || PlannerTransport_IsAcknowledgementValid(2, 0)
        || PlannerTransport_IsAcknowledgementValid(UINT32_MAX, 1))
        return 1;
    puts("PLANNER_TRANSPORT_ACK_TEST: PASS");
    return 0;
}
