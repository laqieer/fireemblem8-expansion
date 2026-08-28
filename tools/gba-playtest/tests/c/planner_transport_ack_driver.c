#define _GNU_SOURCE

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

bool PlannerTransport_IsAcknowledgementValid(
    uint32_t result,
    uint32_t rejection);
int PlannerTransport_ReadLineForTest(
    FILE* input,
    char* line,
    size_t capacity);

enum
{
    INPUT_LINE_EOF,
    INPUT_LINE_READY,
    INPUT_LINE_MALFORMED,
};

static int CheckLineFraming(void)
{
    char line[512];
    char exact[512];
    char chained[768];
    char no_newline[600];
    FILE* input;
    size_t length;

    memset(exact, ' ', sizeof(exact));
    memcpy(exact, "READ", 4);
    exact[sizeof(exact) - 1] = '\n';
    input = fmemopen(exact, sizeof(exact), "r");
    if (input == NULL
        || PlannerTransport_ReadLineForTest(input, line, sizeof(line))
            != INPUT_LINE_READY
        || strlen(line) != sizeof(line) - 1
        || PlannerTransport_ReadLineForTest(input, line, sizeof(line))
            != INPUT_LINE_EOF)
        return 1;
    fclose(input);

    memset(chained, 'X', 700);
    memcpy(
        chained + 700,
        " CANCEL 00000001 00000001\nREAD\n",
        sizeof(" CANCEL 00000001 00000001\nREAD\n") - 1);
    length = 700 + sizeof(" CANCEL 00000001 00000001\nREAD\n") - 1;
    input = fmemopen(chained, length, "r");
    if (input == NULL
        || PlannerTransport_ReadLineForTest(input, line, sizeof(line))
            != INPUT_LINE_MALFORMED
        || PlannerTransport_ReadLineForTest(input, line, sizeof(line))
            != INPUT_LINE_READY
        || strcmp(line, "READ") != 0
        || PlannerTransport_ReadLineForTest(input, line, sizeof(line))
            != INPUT_LINE_EOF)
        return 1;
    fclose(input);

    memset(no_newline, 'X', sizeof(no_newline));
    input = fmemopen(no_newline, sizeof(no_newline), "r");
    if (input == NULL
        || PlannerTransport_ReadLineForTest(input, line, sizeof(line))
            != INPUT_LINE_MALFORMED
        || PlannerTransport_ReadLineForTest(input, line, sizeof(line))
            != INPUT_LINE_EOF)
        return 1;
    fclose(input);

    input = fmemopen("READ", 4, "r");
    if (input == NULL
        || PlannerTransport_ReadLineForTest(input, line, sizeof(line))
            != INPUT_LINE_READY
        || strcmp(line, "READ") != 0
        || PlannerTransport_ReadLineForTest(input, line, sizeof(line))
            != INPUT_LINE_EOF)
        return 1;
    fclose(input);
    return 0;
}

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
        || PlannerTransport_IsAcknowledgementValid(UINT32_MAX, 1)
        || CheckLineFraming())
        return 1;
    puts("PLANNER_TRANSPORT_SECURITY_TEST: PASS");
    return 0;
}
