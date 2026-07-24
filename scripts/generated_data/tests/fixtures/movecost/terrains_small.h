#ifndef GUARD_FIXTURE_TERRAINS_SMALL_H
#define GUARD_FIXTURE_TERRAINS_SMALL_H

/* Fixture-only terrain enum: 3 real terrains, kept small so movecost
 * fixtures don't need to author all 65 real TERRAIN_COUNT entries. */
enum {
    TERRAIN_NONE = 0x00,
    TERRAIN_PLAINS = 0x01,
    TERRAIN_FOREST = 0x02,
    TERRAIN_COUNT = 0x03,
};

#endif /* GUARD_FIXTURE_TERRAINS_SMALL_H */
