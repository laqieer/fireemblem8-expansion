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

struct InputRange {
	uint32_t start;
	uint32_t end;
	uint32_t keys;
};

struct Probe {
	uint32_t address;
	unsigned size;
};

struct Checkpoint {
	uint32_t frame;
	size_t probe_count;
	struct Probe* probes;
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
		for (size_t i = 0; i < plan->checkpoint_count; ++i)
			free(plan->checkpoints[i].probes);
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
	    strcmp(word, "GBA_PLAYTEST_PLAN") != 0 || version != 1) {
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
		if (fscanf(file, "%" SCNu32 " %zu", &checkpoint->frame,
		           &checkpoint->probe_count) != 2 ||
		    checkpoint->probe_count > 1024) {
			fprintf(stderr, "malformed checkpoint %zu\n", i);
			goto fail;
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

static int run(const char* rom_path, const struct Plan* plan)
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
	if (argc != 3) {
		fprintf(stderr, "usage: %s <rom.gba> <plan>\n", argv[0]);
		return 2;
	}
	struct mLogger logger = {.log = silent_log, .filter = NULL};
	mLogSetDefaultLogger(&logger);
	struct Plan plan = {0};
	if (!read_plan(argv[2], &plan))
		return 2;
	int result = run(argv[1], &plan);
	free_plan(&plan);
	return result;
}
