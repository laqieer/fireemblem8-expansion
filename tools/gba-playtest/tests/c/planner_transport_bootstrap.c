#include <mgba/core/core.h>

#include <stdbool.h>
#include <stdint.h>

#ifndef PLANNER_OBSERVATION_ADDR
#error "planner bootstrap requires the fixed observation address"
#endif

#define KEY_A UINT32_C(0x001)
#define KEY_B UINT32_C(0x002)
#define KEY_START UINT32_C(0x008)
#define KEY_RIGHT UINT32_C(0x010)
#define KEY_LEFT UINT32_C(0x020)
#define KEY_DOWN UINT32_C(0x080)
#define PLANNER_MAGIC UINT32_C(0x41504C4E)
#define PLANNER_VERSION UINT32_C(2)
#define PLANNER_OBSERVATION_BYTES UINT32_C(996)
#define PLANNER_READY UINT32_C(1)

struct BootstrapInput
{
    uint32_t start;
    uint32_t end;
    uint32_t keys;
};

static const struct BootstrapInput sBootstrapInputs[] = {
    { 90, 95, KEY_A },
    { 155, 160, KEY_START },
    { 220, 225, KEY_A },
    { 285, 290, KEY_START },
    { 350, 355, KEY_A },
    { 415, 420, KEY_START },
    { 480, 485, KEY_A },
    { 545, 550, KEY_START },
    { 950, 956, KEY_A },
    { 1020, 1026, KEY_DOWN },
    { 1090, 1096, KEY_A },
    { 1380, 1386, KEY_A },
    { 1503, 1509, KEY_START },
    { 1599, 1605, KEY_START },
    { 1764, 1770, KEY_START },
    { 1860, 1866, KEY_START },
    { 2101, 2107, KEY_START },
    { 2254, 2260, KEY_START },
    { 2383, 2389, KEY_START },
    { 2624, 2630, KEY_START },
    { 2865, 2871, KEY_START },
    { 3106, 3112, KEY_START },
    { 3347, 3353, KEY_START },
    { 3500, 3506, KEY_LEFT },
    { 3570, 3576, KEY_A },
    { 3660, 3666, KEY_B },
    { 3760, 3766, KEY_RIGHT },
    { 3860, 3866, KEY_DOWN },
};

static bool PlannerReady(struct mCore* core)
{
    return core->busRead32(core, PLANNER_OBSERVATION_ADDR)
            == PLANNER_MAGIC
        && core->busRead32(core, PLANNER_OBSERVATION_ADDR + 4)
            == PLANNER_VERSION
        && core->busRead32(core, PLANNER_OBSERVATION_ADDR + 8)
            == PLANNER_OBSERVATION_BYTES
        && core->busRead32(core, PLANNER_OBSERVATION_ADDR + 5 * 4)
            == PLANNER_READY
        && core->busRead32(core, PLANNER_OBSERVATION_ADDR + 6 * 4) == 0
        && core->busRead32(core, PLANNER_OBSERVATION_ADDR + 7 * 4) == 1
        && core->busRead32(core, PLANNER_OBSERVATION_ADDR + 8 * 4) == 0;
}

static bool RunFrameToReady(struct mCore* core)
{
    core->runFrame(core);
    return PlannerReady(core);
}

bool PlannerTransport_TestBootstrap(struct mCore* core)
{
    uint32_t frame = 4;
    unsigned index;

    for (index = 0;
         index < sizeof(sBootstrapInputs) / sizeof(sBootstrapInputs[0]);
         index++)
    {
        const struct BootstrapInput* input = &sBootstrapInputs[index];

        core->setKeys(core, 0);
        while (frame < input->start)
        {
            if (RunFrameToReady(core))
                goto ready;
            frame++;
        }
        core->setKeys(core, input->keys);
        while (frame <= input->end)
        {
            if (RunFrameToReady(core))
                goto ready;
            frame++;
        }
    }
    core->setKeys(core, 0);
    while (frame <= 3950)
    {
        if (RunFrameToReady(core))
            goto ready;
        frame++;
    }
    for (index = 0; index < 8 && !PlannerReady(core); index++)
    {
        uint32_t count;

        core->setKeys(core, index % 2 == 0 ? KEY_START : KEY_A);
        for (count = 0; count < 6; count++)
            if (RunFrameToReady(core))
                goto ready;
        core->setKeys(core, 0);
        for (count = 0; count < 59; count++)
            if (RunFrameToReady(core))
                goto ready;
    }
ready:
    core->setKeys(core, 0);
    return PlannerReady(core);
}
