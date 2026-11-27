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

static int CheckFramebufferProbes(void)
{
    color_t pixels[35];
    color_t changed[35];
    struct Region region = { 1, 1, 3, 2 };
    struct Region other = { 3, 2, 3, 2 };
    struct Region whole = { 0, 0, 7, 5 };
    struct PixelProbe probe = { 2, 3 };
    uint64_t region_hash;
    uint64_t whole_hash;

    for (unsigned y = 0; y < 5; ++y)
        for (unsigned x = 0; x < 7; ++x)
            pixels[y * 7 + x] = (x * 29 + 3) | ((y * 31 + 5) << 8) |
                ((x * 7 + y * 11) << 16) | (0xA5u << 24);

    /* FNV-1a over the six row-major RGB triples in region. */
    region_hash = hash_region(pixels, 7, &region);
    if (region_hash != UINT64_C(0xf76ad49d5f9c8f35)) {
        fprintf(stderr, "region hash does not match the RGB row-major vector\n");
        return 11;
    }
    if (hash_region(pixels, 7, &region) != region_hash) {
        fprintf(stderr, "region hash is not deterministic\n");
        return 12;
    }
    if (hash_region(pixels, 7, &other) == region_hash) {
        fprintf(stderr, "distinct regions have the same hash\n");
        return 13;
    }
    whole_hash = hash_region(pixels, 7, &whole);
    if (whole_hash != hash_framebuffer(pixels, 7, 5) || whole_hash == region_hash) {
        fprintf(stderr, "whole-frame and region hash payloads are inconsistent\n");
        return 14;
    }

    memcpy(changed, pixels, sizeof(pixels));
    changed[0] ^= 0x000000FFu;
    if (hash_region(changed, 7, &region) != region_hash) {
        fprintf(stderr, "outside pixel changed the region hash\n");
        return 15;
    }
    changed[1 * 7 + 2] ^= 0x000000FFu;
    if (hash_region(changed, 7, &region) == region_hash) {
        fprintf(stderr, "inside pixel did not change the region hash\n");
        return 16;
    }
    memcpy(changed, pixels, sizeof(pixels));
    changed[1 * 7 + 2] ^= 0xFF000000u;
    if (hash_region(changed, 7, &region) != region_hash) {
        fprintf(stderr, "padding changed the region hash\n");
        return 17;
    }
    if (read_pixel(pixels, 7, &probe) != 0x3D622Fu) {
        fprintf(stderr, "pixel did not use canonical RGB order\n");
        return 18;
    }
    memcpy(changed, pixels, sizeof(pixels));
    changed[3 * 7 + 2] ^= 0xFF000000u;
    if (read_pixel(changed, 7, &probe) != read_pixel(pixels, 7, &probe)) {
        fprintf(stderr, "padding changed the pixel value\n");
        return 19;
    }
    return 0;
}

int main(void)
{
    struct mCore core;
    uint64_t unchanged_a;
    uint64_t unchanged_b;
    uint64_t mutated;
    uint64_t null_unchanged_a;
    uint64_t null_unchanged_b;
    uint64_t null_mutated;
    uint64_t cloned_a;
    uint64_t cloned_b;

    memset(&core, 0, sizeof(core));
    memset(sCurrentSram, 0x11, sizeof(sCurrentSram));
    memcpy(sClonedSram, sCurrentSram, sizeof(sClonedSram));
    core.savedataClone = CloneFailure;
    core.busRead8 = ReadCurrentSram;

    unchanged_a = hash_sram(&core, NULL, 0);
    unchanged_b = hash_sram(&core, NULL, 0);
    if (unchanged_a != unchanged_b)
        return 1;

    sCurrentSram[0x1234] ^= 0xFF;
    mutated = hash_sram(&core, NULL, 0);
    if (mutated == unchanged_a)
        return 2;

    memset(sCurrentSram, 0x22, sizeof(sCurrentSram));
    core.savedataClone = NULL;
    null_unchanged_a = hash_sram(&core, NULL, 0);
    null_unchanged_b = hash_sram(&core, NULL, 0);
    if (null_unchanged_a != null_unchanged_b)
        return 3;

    sCurrentSram[0x2345] ^= 0xFF;
    null_mutated = hash_sram(&core, NULL, 0);
    if (null_mutated == null_unchanged_a)
        return 4;

    core.savedataClone = CloneStable;
    cloned_a = hash_sram(&core, NULL, 0);
    sCurrentSram[0x1234] ^= 0x7E;
    cloned_b = hash_sram(&core, NULL, 0);
    if (cloned_a != cloned_b)
        return 5;

    return CheckFramebufferProbes();
}
