#define main GbaPlaytestBackendMain
#include "../../backend.c"
#undef main

static uint8_t sCurrentSram[GBA_SRAM_SIZE];
static uint8_t sClonedSram[GBA_SRAM_SIZE];

static size_t CloneFailure(struct mCore* core, void** out)
{
    (void)core;
    *out = NULL;
    return 0;
}

static size_t CloneStable(struct mCore* core, void** out)
{
    uint8_t* clone;

    (void)core;
    clone = malloc(GBA_SRAM_SIZE);
    if (clone == NULL) {
        *out = NULL;
        return 0;
    }

    memcpy(clone, sClonedSram, GBA_SRAM_SIZE);
    *out = clone;
    return GBA_SRAM_SIZE;
}

static uint32_t ReadCurrentSram(struct mCore* core, uint32_t address)
{
    (void)core;
    return sCurrentSram[address - GBA_SRAM_BASE];
}

int main(void)
{
    struct mCore core;
    uint8_t initial_sram[GBA_SRAM_SIZE];
    uint64_t unchanged_a;
    uint64_t unchanged_b;
    uint64_t mutated;
    uint64_t cloned_a;
    uint64_t cloned_b;

    memset(&core, 0, sizeof(core));
    memset(initial_sram, 0xA5, sizeof(initial_sram));
    memset(sCurrentSram, 0x11, sizeof(sCurrentSram));
    memcpy(sClonedSram, sCurrentSram, sizeof(sClonedSram));
    core.savedataClone = CloneFailure;
    core.busRead8 = ReadCurrentSram;

    unchanged_a = hash_sram(&core, NULL, 0, initial_sram);
    unchanged_b = hash_sram(&core, NULL, 0, initial_sram);
    if (unchanged_a != unchanged_b)
        return 1;

    sCurrentSram[0x1234] ^= 0xFF;
    mutated = hash_sram(&core, NULL, 0, initial_sram);
    if (mutated == unchanged_a)
        return 2;

    core.savedataClone = CloneStable;
    cloned_a = hash_sram(&core, NULL, 0, initial_sram);
    sCurrentSram[0x1234] ^= 0x7E;
    cloned_b = hash_sram(&core, NULL, 0, initial_sram);
    if (cloned_a != cloned_b)
        return 3;

    return 0;
}
