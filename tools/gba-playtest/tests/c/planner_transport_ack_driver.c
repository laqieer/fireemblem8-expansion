#define _GNU_SOURCE

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

bool PlannerTransport_IsAcknowledgementValid(uint32_t result, uint32_t rejection);
bool PlannerTransport_IsReadyObservationValid(
    const uint32_t words[256],
    const uint32_t expected_identities[4]);
uint32_t PlannerTransport_ObservationDigest(const uint32_t words[256]);
int PlannerTransport_ReadLineForTest(FILE* input, char* line, size_t capacity);
bool PlannerTransport_ParseHexForTest(const char* text, uint32_t* value);

enum
{
    INPUT_LINE_EOF,
    INPUT_LINE_READY,
    INPUT_LINE_MALFORMED,
};

static int CheckLineFraming(void)
{
    char line[512], exact[512];
    char chained[768], no_newline[600];
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
    memcpy(chained + 700, " CANCEL 00000001 00000001\nREAD\n",
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

static int CheckHexParser(void)
{
    static const char* invalid[] = {
        "", "-0", "+0", "-1", "+1", " -0", "\t+0", "100000000",
        "FFFFFFFFF", "0x1", "1g", "1 " };
    uint32_t value;
    size_t index;
    if (!PlannerTransport_ParseHexForTest("0", &value) || value != 0
        || !PlannerTransport_ParseHexForTest("FFFFFFFF", &value)
        || value != UINT32_MAX)
        return 1;
    for (index = 0; index < sizeof(invalid) / sizeof(*invalid); index++)
        if (PlannerTransport_ParseHexForTest(invalid[index], &value))
            return 1;
    return 0;
}

static bool ReadyRejectsWithoutMutation(
    uint32_t words[256],
    const uint32_t expected[4])
{
    uint32_t before[256];

    memcpy(before, words, sizeof(before));
    return !PlannerTransport_IsReadyObservationValid(words, expected)
        && memcmp(before, words, sizeof(before)) == 0;
}

static int CheckReadyObservation(void)
{
    static const uint32_t fixed_mutations[][2] = {
        { 0, 0 }, { 1, 3 }, { 2, 1020 }, { 5, 2 }, { 7, 2 },
    };
    uint32_t words[256] = { 0 };
    uint32_t expected[] = { 11, 22, 33, 44 };
    size_t index;

    words[0] = UINT32_C(0x41504C4E);
    words[1] = 2;
    words[2] = sizeof(words);
    words[5] = 1;
    words[7] = 1;
    memcpy(&words[21], expected, sizeof(expected));
    words[255] = PlannerTransport_ObservationDigest(words);
    if (!PlannerTransport_IsReadyObservationValid(words, NULL)
        || !PlannerTransport_IsReadyObservationValid(words, expected))
        return 1;
    for (index = 0; index < sizeof(fixed_mutations) / sizeof(*fixed_mutations); index++)
    {
        uint32_t saved = words[fixed_mutations[index][0]];

        words[fixed_mutations[index][0]] = fixed_mutations[index][1];
        words[255] = PlannerTransport_ObservationDigest(words);
        if (!ReadyRejectsWithoutMutation(words, expected))
            return 1;
        words[fixed_mutations[index][0]] = saved;
        words[255] = PlannerTransport_ObservationDigest(words);
    }
    for (index = 3; index < 256; index++)
    {
        if (index == 5 || index == 7 || index == 255
            || (index >= 21 && index <= 24))
            continue;
        words[index] = 1;
        words[255] = PlannerTransport_ObservationDigest(words);
        if (!ReadyRejectsWithoutMutation(words, expected))
            return 1;
        words[index] = 0;
        words[255] = PlannerTransport_ObservationDigest(words);
    }
    for (index = 0; index < 4; index++)
    {
        words[21 + index] = 0;
        words[255] = PlannerTransport_ObservationDigest(words);
        if (!ReadyRejectsWithoutMutation(words, expected))
            return 1;
        words[21 + index] = expected[index] ^ 1;
        words[255] = PlannerTransport_ObservationDigest(words);
        if (!ReadyRejectsWithoutMutation(words, expected))
            return 1;
        words[21 + index] = expected[index];
        words[255] = PlannerTransport_ObservationDigest(words);
    }
    words[255] ^= 1;
    if (!ReadyRejectsWithoutMutation(words, expected))
        return 1;
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
        || CheckLineFraming()
        || CheckHexParser()
        || CheckReadyObservation())
        return 1;
    puts("PLANNER_TRANSPORT_SECURITY_TEST: PASS");
    return 0;
}
