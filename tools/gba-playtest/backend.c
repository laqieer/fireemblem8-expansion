/*
 * Headless libmGBA execution backend for gba-playtest.
 *
 * The core setup and frame-driving shape are adapted from
 * scripts/shiftcheck/mgba_oracle.c in this repository. That oracle remains
 * unchanged; this backend adds a declarative plan and RAM probes.
 */
#include <mgba/core/core.h>
#include <mgba/core/interface.h>
#include <mgba/core/log.h>

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if BYTES_PER_PIXEL != 4
#error "gba-playtest requires libmGBA's standard 32-bit color_t build"
#endif

#define GBA_SRAM_BASE 0x0E000000u
#define GBA_SRAM_SIZE 0x8000u

struct InputRange {
	uint32_t start;
	uint32_t end;
	uint32_t keys;
};

struct Probe {
	uint32_t address;
	unsigned size;
};

/* A [offset, offset+length) byte range within the 0x8000-byte SRAM image
 * excluded from a checkpoint's SRAM hash (see hash_sram()). */
struct ByteRange {
	uint32_t offset;
	uint32_t length;
};

struct Checkpoint {
	uint32_t frame;
	size_t probe_count;
	struct Probe* probes;
	bool sram_hash;
	size_t exclude_range_count;
	struct ByteRange* exclude_ranges;
};

struct Plan {
	size_t range_count;
	struct InputRange* ranges;
	size_t checkpoint_count;
	struct Checkpoint* checkpoints;
};

static void silent_log(struct mLogger* logger, int category,
                       enum mLogLevel level, const char* format, va_list args)
{
	(void) logger;
	(void) category;
	(void) level;
	(void) format;
	(void) args;
}

static void free_plan(struct Plan* plan)
{
	if (plan->checkpoints) {
		for (size_t i = 0; i < plan->checkpoint_count; ++i) {
			free(plan->checkpoints[i].probes);
			free(plan->checkpoints[i].exclude_ranges);
		}
	}
	free(plan->checkpoints);
	free(plan->ranges);
	memset(plan, 0, sizeof(*plan));
}

static bool read_plan(const char* path, struct Plan* plan)
{
	FILE* file = fopen(path, "r");
	if (!file) {
		fprintf(stderr, "cannot open plan: %s\n", path);
		return false;
	}
	char word[32];
	unsigned version;
	if (fscanf(file, "%31s %u", word, &version) != 2 ||
	    strcmp(word, "GBA_PLAYTEST_PLAN") != 0 || version != 2) {
		fprintf(stderr, "malformed plan header\n");
		goto fail;
	}
	if (fscanf(file, "%31s %zu", word, &plan->range_count) != 2 ||
	    strcmp(word, "RANGES") != 0 || plan->range_count > 10000) {
		fprintf(stderr, "malformed RANGES record\n");
		goto fail;
	}
	plan->ranges = calloc(plan->range_count, sizeof(*plan->ranges));
	if (plan->range_count && !plan->ranges) {
		fprintf(stderr, "out of memory reading ranges\n");
		goto fail;
	}
	for (size_t i = 0; i < plan->range_count; ++i) {
		struct InputRange* range = &plan->ranges[i];
		if (fscanf(file, "%" SCNu32 " %" SCNu32 " %" SCNu32,
		           &range->start, &range->end, &range->keys) != 3) {
			fprintf(stderr, "malformed input range %zu\n", i);
			goto fail;
		}
	}
	if (fscanf(file, "%31s %zu", word, &plan->checkpoint_count) != 2 ||
	    strcmp(word, "CHECKPOINTS") != 0 || plan->checkpoint_count == 0 ||
	    plan->checkpoint_count > 10000) {
		fprintf(stderr, "malformed CHECKPOINTS record\n");
		goto fail;
	}
	plan->checkpoints = calloc(plan->checkpoint_count, sizeof(*plan->checkpoints));
	if (!plan->checkpoints) {
		fprintf(stderr, "out of memory reading checkpoints\n");
		goto fail;
	}
	for (size_t i = 0; i < plan->checkpoint_count; ++i) {
		struct Checkpoint* checkpoint = &plan->checkpoints[i];
		unsigned sram_hash_flag;
		if (fscanf(file, "%" SCNu32 " %zu %u %zu", &checkpoint->frame,
		           &checkpoint->probe_count, &sram_hash_flag,
		           &checkpoint->exclude_range_count) != 4 ||
		    checkpoint->probe_count > 1024 ||
		    checkpoint->exclude_range_count > 64 ||
		    (sram_hash_flag != 0 && sram_hash_flag != 1)) {
			fprintf(stderr, "malformed checkpoint %zu\n", i);
			goto fail;
		}
		checkpoint->sram_hash = sram_hash_flag != 0;
		checkpoint->exclude_ranges = calloc(checkpoint->exclude_range_count,
		                                     sizeof(*checkpoint->exclude_ranges));
		if (checkpoint->exclude_range_count && !checkpoint->exclude_ranges) {
			fprintf(stderr, "out of memory reading exclude ranges\n");
			goto fail;
		}
		for (size_t j = 0; j < checkpoint->exclude_range_count; ++j) {
			struct ByteRange* range = &checkpoint->exclude_ranges[j];
			if (fscanf(file, "%" SCNu32 " %" SCNu32, &range->offset,
			           &range->length) != 2 ||
			    range->length == 0 ||
			    range->offset >= GBA_SRAM_SIZE ||
			    range->length > GBA_SRAM_SIZE - range->offset) {
				fprintf(stderr, "malformed exclude range %zu at checkpoint %zu\n", j, i);
				goto fail;
			}
		}
		checkpoint->probes = calloc(checkpoint->probe_count,
		                            sizeof(*checkpoint->probes));
		if (checkpoint->probe_count && !checkpoint->probes) {
			fprintf(stderr, "out of memory reading probes\n");
			goto fail;
		}
		for (size_t j = 0; j < checkpoint->probe_count; ++j) {
			struct Probe* probe = &checkpoint->probes[j];
			if (fscanf(file, "%" SCNu32 " %u", &probe->address,
			           &probe->size) != 2 ||
			    (probe->size != 1 && probe->size != 2 && probe->size != 4)) {
				fprintf(stderr, "malformed probe %zu at checkpoint %zu\n", j, i);
				goto fail;
			}
		}
	}
	if (fscanf(file, "%31s", word) == 1) {
		fprintf(stderr, "unexpected trailing plan data\n");
		goto fail;
	}
	fclose(file);
	return true;

fail:
	fclose(file);
	free_plan(plan);
	return false;
}

static uint64_t hash_framebuffer(const color_t* buffer, unsigned width, unsigned height)
{
	/* FNV-1a over canonical R,G,B bytes; ignore host endianness and alpha/padding. */
	uint64_t hash = UINT64_C(14695981039346656037);
	for (size_t i = 0; i < (size_t) width * height; ++i) {
		uint32_t pixel = buffer[i];
		for (unsigned shift = 0; shift < 24; shift += 8) {
			hash ^= (pixel >> shift) & 0xFF;
			hash *= UINT64_C(1099511628211);
		}
	}
	return hash;
}

static uint32_t read_probe(struct mCore* core, const struct Probe* probe)
{
	switch (probe->size) {
	case 1:
		return core->busRead8(core, probe->address);
	case 2:
		return core->busRead16(core, probe->address);
	default:
		return core->busRead32(core, probe->address);
	}
}

static bool offset_excluded(const struct ByteRange* ranges, size_t range_count,
                            uint32_t offset)
{
	for (size_t i = 0; i < range_count; ++i) {
		if (offset >= ranges[i].offset && offset < ranges[i].offset + ranges[i].length)
			return true;
	}
	return false;
}

static uint64_t hash_sram(struct mCore* core, const struct ByteRange* exclude_ranges,
                           size_t exclude_range_count)
{
	/* Same FNV-1a construction as hash_framebuffer(), applied to the raw
	 * cart SRAM bus range (0x0E000000..0x0E007FFF): proves the entire
	 * 0x8000-byte image -- minus any `exclude_ranges` bytes -- is
	 * byte-for-byte unchanged across a checkpoint without needing one
	 * probe per byte (the plan format caps probes per checkpoint at
	 * 1024). With no exclude ranges (the common case) this proves the
	 * *entire* image unchanged, exactly as before. `exclude_ranges` exists
	 * only for checkpoints whose SRAM legitimately contains intentionally
	 * build-variable diagnostic bytes (ExpansionSaveMeta's buildCommitShort
	 * and its dependent checksum -- see docs/save_format.md's "SRAM hash
	 * policy: exact vs. normalized"); excluded bytes are skipped entirely
	 * (never substituted/zeroed), so the hash is still sensitive to every
	 * other byte in the image. */
	uint64_t hash = UINT64_C(14695981039346656037);
	for (uint32_t offset = 0; offset < GBA_SRAM_SIZE; ++offset) {
		if (offset_excluded(exclude_ranges, exclude_range_count, offset))
			continue;
		uint8_t byte = core->busRead8(core, GBA_SRAM_BASE + offset);
		hash ^= byte;
		hash *= UINT64_C(1099511628211);
	}
	return hash;
}

static int run(const char* rom_path, const struct Plan* plan, const char* sram_path)
{
	struct mCore* core = mCoreFind(rom_path);
	if (!core) {
		fprintf(stderr, "no mGBA core recognizes ROM: %s\n", rom_path);
		return 2;
	}
	if (!core->init(core)) {
		fprintf(stderr, "mGBA core initialization failed\n");
		core->deinit(core);
		return 2;
	}
	mCoreInitConfig(core, NULL);
	if (!mCoreLoadFile(core, rom_path)) {
		fprintf(stderr, "mGBA could not load ROM: %s\n", rom_path);
		mCoreConfigDeinit(&core->config);
		core->deinit(core);
		return 2;
	}
	if (sram_path) {
		/*
		 * Loaded before core->reset() so the pre-boot SRAM image is what
		 * the game's own boot-time classifier (ClassifySramSaveCompat)
		 * observes on the very first frame, exactly as a real cartridge
		 * with existing save data would present it.
		 *
		 * `temporary=true` is required here: it maps to
		 * GBACoreLoadTemporarySave -> GBASavedataMask(), which copies the
		 * file's bytes into the in-memory savedata buffer once and then
		 * detaches from the backing file. `temporary=false` instead keeps
		 * the opened O_RDWR VFile as the live savedata backing store, so
		 * every in-game SRAM write (including the compatibility menu's
		 * whole-chip erase) is flushed straight back to sram_path on disk
		 * -- silently mutating the input fixture and making repeated runs
		 * against the same file non-reproducible (confirmed empirically:
		 * identical re-invocations diverged once the fixture had been
		 * quietly rewritten by a prior run).
		 */
		if (!mCoreLoadSaveFile(core, sram_path, true)) {
			fprintf(stderr, "mGBA could not load SRAM image: %s\n", sram_path);
			mCoreConfigDeinit(&core->config);
			core->deinit(core);
			return 2;
		}
	}
	unsigned width;
	unsigned height;
	core->desiredVideoDimensions(core, &width, &height);
	if (width != 240 || height != 160) {
		fprintf(stderr, "unexpected GBA framebuffer dimensions: %ux%u\n", width, height);
		mCoreConfigDeinit(&core->config);
		core->deinit(core);
		return 2;
	}
	color_t* buffer = calloc((size_t) width * height, sizeof(*buffer));
	if (!buffer) {
		fprintf(stderr, "out of memory allocating framebuffer\n");
		mCoreConfigDeinit(&core->config);
		core->deinit(core);
		return 2;
	}
	core->setVideoBuffer(core, buffer, width);
	core->reset(core);

	size_t range_index = 0;
	size_t checkpoint_index = 0;
	uint32_t last_frame = plan->checkpoints[plan->checkpoint_count - 1].frame;
	for (uint32_t frame = 0; frame <= last_frame; ++frame) {
		while (range_index < plan->range_count &&
		       plan->ranges[range_index].end < frame)
			++range_index;
		uint32_t keys = 0;
		if (range_index < plan->range_count &&
		    plan->ranges[range_index].start <= frame)
			keys = plan->ranges[range_index].keys;
		core->setKeys(core, keys);
		core->runFrame(core);
		if (checkpoint_index < plan->checkpoint_count &&
		    plan->checkpoints[checkpoint_index].frame == frame) {
			const struct Checkpoint* checkpoint =
			    &plan->checkpoints[checkpoint_index];
			uint64_t hash = hash_framebuffer(buffer, width, height);
			printf("CHECKPOINT\t%zu\t%" PRIu32 "\t%016" PRIx64 "\n",
			       checkpoint_index, frame, hash);
			for (size_t probe_index = 0;
			     probe_index < checkpoint->probe_count; ++probe_index) {
				printf("PROBE\t%zu\t%zu\t%" PRIu32 "\n",
				       checkpoint_index, probe_index,
				       read_probe(core, &checkpoint->probes[probe_index]));
			}
			if (checkpoint->sram_hash) {
				uint64_t sram = hash_sram(core, checkpoint->exclude_ranges,
				                           checkpoint->exclude_range_count);
				printf("SRAMHASH\t%zu\t%016" PRIx64 "\n",
				       checkpoint_index, sram);
			}
			++checkpoint_index;
		}
	}
	free(buffer);
	mCoreConfigDeinit(&core->config);
	core->deinit(core);
	return checkpoint_index == plan->checkpoint_count ? 0 : 2;
}

int main(int argc, char** argv)
{
	if (argc != 3 && argc != 4) {
		fprintf(stderr, "usage: %s <rom.gba> <plan> [sram-image]\n", argv[0]);
		return 2;
	}
	struct mLogger logger = {.log = silent_log, .filter = NULL};
	mLogSetDefaultLogger(&logger);
	struct Plan plan = {0};
	if (!read_plan(argv[2], &plan))
		return 2;
	int result = run(argv[1], &plan, argc == 4 ? argv[3] : NULL);
	free_plan(&plan);
	return result;
}
