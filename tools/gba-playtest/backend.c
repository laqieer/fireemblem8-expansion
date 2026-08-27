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
#include <stdarg.h>
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
#define GBA_EWRAM_BASE 0x02000000u
#define GBA_EWRAM_SIZE 0x40000u
#define GBA_IWRAM_BASE 0x03000000u
#define GBA_IWRAM_SIZE 0x8000u
#define MAX_INPUT_RANGES 1000000u
#define MAX_RUN_PROBES 128u
#define MAX_TERMINALS 3u
#define MAX_TERMINAL_COMPARISONS 64u
#define MAX_TRACE_PROBES 512u
#define MAX_TRACE_RECORDS 450000u
#define MAX_CHECKPOINT_PROBES 1536u
#define MAX_BASELINE_PROBES 1536u

#define PLAYST_CONFIG_GAME_SPEED_MASK (1u << 7)
#define PLAYST_CONFIG_ANIMATION_TYPE_MASK (3u << 17)
#define PLAYST_CONFIG_ANIMATION_TYPE_OFF (1u << 17)

enum ComparisonOperator {
	COMPARE_EQ = 0,
	COMPARE_NE = 1,
	COMPARE_LT = 2,
	COMPARE_LE = 3,
	COMPARE_GT = 4,
	COMPARE_GE = 5,
};

enum TerminalReason {
	TERMINAL_NONE = 0,
	TERMINAL_SUCCESS = 1,
	TERMINAL_OBJECTIVE_FAILURE = 2,
	TERMINAL_CONTROLLER_EXHAUSTED = 3,
	TERMINAL_ENGINE_STALL = 4,
	TERMINAL_MAX_FRAMES = 5,
	TERMINAL_MAX_TURNS = 6,
	TERMINAL_MAX_ACTIONS = 7,
};

struct InputRange {
	uint32_t start;
	uint32_t end;
	uint32_t keys;
};

struct Probe {
	uint32_t address;
	unsigned size;
};

struct Comparison {
	size_t probe_index;
	unsigned operator;
	uint32_t value;
};

struct TerminalCondition {
	enum TerminalReason reason;
	size_t comparison_count;
	struct Comparison* comparisons;
};

struct CounterLimit {
	bool enabled;
	size_t probe_index;
	uint32_t maximum;
};

struct StallLimit {
	bool enabled;
	size_t progress_probe_index;
	struct Comparison work_expected;
	uint32_t max_unchanged_frames;
};

/* A [offset, offset+length) byte range within the 0x8000-byte SRAM image
 * excluded from a checkpoint's SRAM hash (see hash_sram()). */
struct ByteRange {
	uint32_t offset;
	uint32_t length;
};

/* A rectangular [x, x+width) x [y, y+height) sub-region of the 240x160
 * framebuffer, hashed on its own (see hash_region()) so a checkpoint can
 * prove a *specific screen area* changed/differs -- e.g. a locale-specific
 * visible decoration marker -- without that proof being diluted by (or
 * mistaken for) a whole-screen hash difference that could come from any
 * unrelated pixel anywhere on screen. */
struct Region {
	uint32_t x;
	uint32_t y;
	uint32_t width;
	uint32_t height;
};

/* A single (x, y) framebuffer coordinate whose canonical R,G,B byte triple
 * (same extraction as hash_framebuffer()/hash_region(), host-endianness-
 * and alpha/padding-independent) is read back directly -- the finest-
 * grained possible real visible-pixel assertion, one exact on-screen
 * color value at a time. */
struct PixelProbe {
	uint32_t x;
	uint32_t y;
};

struct Checkpoint {
	uint32_t frame;
	size_t probe_count;
	struct Probe* probes;
	bool framebuffer;
	bool sram_hash;
	size_t exclude_range_count;
	struct ByteRange* exclude_ranges;
	size_t region_count;
	struct Region* regions;
	size_t pixel_probe_count;
	struct PixelProbe* pixel_probes;
};

struct Plan {
	size_t range_count;
	struct InputRange* ranges;
	size_t checkpoint_count;
	struct Checkpoint* checkpoints;
	bool run_until;
	uint32_t max_frames;
	size_t run_probe_count;
	struct Probe* run_probes;
	size_t terminal_count;
	struct TerminalCondition* terminals;
	struct StallLimit stall;
	struct CounterLimit turn_limit;
	struct CounterLimit action_limit;
	bool has_seed_write;
	size_t baseline_probe_count;
	struct Probe* baseline_probes;
	uint32_t seed_frame;
	struct Probe seed_write;
	uint32_t seed_value;
	unsigned execution_profile;
	uint32_t config_apply_frame;
	uint32_t play_state_config_address;
	size_t trace_probe_count;
	struct Probe* trace_probes;
};

static FILE* sLogCapture;

static bool probe_value_fits(unsigned size, uint32_t value)
{
	if (size == 4)
		return true;
	return value < (UINT32_C(1) << (size * 8));
}

static bool writable_probe_address(uint32_t address, unsigned size)
{
	return (address >= GBA_EWRAM_BASE &&
	        address <= GBA_EWRAM_BASE + GBA_EWRAM_SIZE - size) ||
	       (address >= GBA_IWRAM_BASE &&
	        address <= GBA_IWRAM_BASE + GBA_IWRAM_SIZE - size);
}

static void capture_log(struct mLogger* logger, int category,
                        enum mLogLevel level, const char* format, va_list args)
{
	char line[1024];
	va_list copy;

	(void) logger;
	if (!sLogCapture)
		return;
	va_copy(copy, args);
	vsnprintf(line, sizeof(line), format, copy);
	va_end(copy);
	fprintf(sLogCapture, "%d\t%d\t%s\n", category, level, line);
	fflush(sLogCapture);
}

static void free_plan(struct Plan* plan)
{
	if (plan->checkpoints) {
		for (size_t i = 0; i < plan->checkpoint_count; ++i) {
			free(plan->checkpoints[i].probes);
			free(plan->checkpoints[i].exclude_ranges);
			free(plan->checkpoints[i].regions);
			free(plan->checkpoints[i].pixel_probes);
		}
	}
	if (plan->terminals) {
		for (size_t i = 0; i < plan->terminal_count; ++i)
			free(plan->terminals[i].comparisons);
	}
	free(plan->checkpoints);
	free(plan->ranges);
	free(plan->run_probes);
	free(plan->terminals);
	free(plan->trace_probes);
	free(plan->baseline_probes);
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
	    strcmp(word, "GBA_PLAYTEST_PLAN") != 0 ||
	    (version != 3 && version != 4 && version != 5 && version != 6)) {
		fprintf(stderr, "malformed plan header\n");
		goto fail;
	}
	if (fscanf(file, "%31s %zu", word, &plan->range_count) != 2 ||
	    strcmp(word, "RANGES") != 0 || plan->range_count > MAX_INPUT_RANGES) {
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
		unsigned framebuffer_flag = 1;
		int checkpoint_fields;

		if (version == 5) {
			checkpoint_fields = fscanf(
			    file, "%" SCNu32 " %zu %u %zu %zu %zu %u",
			    &checkpoint->frame, &checkpoint->probe_count, &sram_hash_flag,
			    &checkpoint->exclude_range_count, &checkpoint->region_count,
			    &checkpoint->pixel_probe_count, &framebuffer_flag);
		} else {
			checkpoint_fields = fscanf(
			    file, "%" SCNu32 " %zu %u %zu %zu %zu",
			    &checkpoint->frame, &checkpoint->probe_count, &sram_hash_flag,
			    &checkpoint->exclude_range_count, &checkpoint->region_count,
			    &checkpoint->pixel_probe_count);
		}
		if (checkpoint_fields != (version == 5 ? 7 : 6) ||
		    checkpoint->probe_count > MAX_CHECKPOINT_PROBES ||
		    checkpoint->exclude_range_count > 64 ||
		    checkpoint->region_count > 64 ||
		    checkpoint->pixel_probe_count > 256 ||
		    (sram_hash_flag != 0 && sram_hash_flag != 1) ||
		    (framebuffer_flag != 0 && framebuffer_flag != 1) ||
		    (!framebuffer_flag &&
		     (checkpoint->region_count != 0 || checkpoint->pixel_probe_count != 0))) {
			fprintf(stderr, "malformed checkpoint %zu\n", i);
			goto fail;
		}
		checkpoint->framebuffer = framebuffer_flag != 0;
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
		checkpoint->regions = calloc(checkpoint->region_count,
		                             sizeof(*checkpoint->regions));
		if (checkpoint->region_count && !checkpoint->regions) {
			fprintf(stderr, "out of memory reading regions\n");
			goto fail;
		}
		for (size_t j = 0; j < checkpoint->region_count; ++j) {
			struct Region* region = &checkpoint->regions[j];
			if (fscanf(file, "%" SCNu32 " %" SCNu32 " %" SCNu32 " %" SCNu32,
			           &region->x, &region->y, &region->width,
			           &region->height) != 4 ||
			    region->width == 0 || region->height == 0 ||
			    region->x >= 240 || region->y >= 160 ||
			    region->width > 240 - region->x ||
			    region->height > 160 - region->y) {
				fprintf(stderr, "malformed region %zu at checkpoint %zu\n", j, i);
				goto fail;
			}
		}
		checkpoint->pixel_probes = calloc(checkpoint->pixel_probe_count,
		                                  sizeof(*checkpoint->pixel_probes));
		if (checkpoint->pixel_probe_count && !checkpoint->pixel_probes) {
			fprintf(stderr, "out of memory reading pixel probes\n");
			goto fail;
		}
		for (size_t j = 0; j < checkpoint->pixel_probe_count; ++j) {
			struct PixelProbe* pixel = &checkpoint->pixel_probes[j];
			if (fscanf(file, "%" SCNu32 " %" SCNu32, &pixel->x, &pixel->y) != 2 ||
			    pixel->x >= 240 || pixel->y >= 160) {
				fprintf(stderr, "malformed pixel probe %zu at checkpoint %zu\n", j, i);
				goto fail;
			}
		}
	}
	if (version >= 4) {
		unsigned reason_mask = 0;
		unsigned enabled;

		plan->run_until = true;
		if (plan->checkpoint_count != 1 ||
		    fscanf(file, "%31s %" SCNu32, word, &plan->max_frames) != 2 ||
		    strcmp(word, "RUN_UNTIL") != 0 ||
		    plan->max_frames == 0 || plan->max_frames > 10000001 ||
		    plan->checkpoints[0].frame != plan->max_frames - 1) {
			fprintf(stderr, "malformed RUN_UNTIL record\n");
			goto fail;
		}
		for (size_t i = 0; i < plan->range_count; ++i) {
			if (plan->ranges[i].start > plan->ranges[i].end ||
			    plan->ranges[i].end >= plan->max_frames ||
			    plan->ranges[i].keys > 0x3FFu ||
			    (i > 0 && plan->ranges[i].start <= plan->ranges[i - 1].end)) {
				fprintf(stderr, "input range %zu is outside run-until bounds\n", i);
				goto fail;
			}
		}
		if (fscanf(file, "%31s %zu", word, &plan->run_probe_count) != 2 ||
		    strcmp(word, "RUN_PROBES") != 0 ||
		    plan->run_probe_count == 0 ||
		    plan->run_probe_count > MAX_RUN_PROBES) {
			fprintf(stderr, "malformed RUN_PROBES record\n");
			goto fail;
		}
		plan->run_probes = calloc(plan->run_probe_count, sizeof(*plan->run_probes));
		if (!plan->run_probes) {
			fprintf(stderr, "out of memory reading run-until probes\n");
			goto fail;
		}
		for (size_t i = 0; i < plan->run_probe_count; ++i) {
			struct Probe* probe = &plan->run_probes[i];
			if (fscanf(file, "%" SCNu32 " %u", &probe->address,
			           &probe->size) != 2 ||
			    (probe->size != 1 && probe->size != 2 && probe->size != 4)) {
				fprintf(stderr, "malformed run-until probe %zu\n", i);
				goto fail;
			}
		}
		if (fscanf(file, "%31s %zu", word, &plan->terminal_count) != 2 ||
		    strcmp(word, "TERMINALS") != 0 ||
		    plan->terminal_count == 0 ||
		    plan->terminal_count > MAX_TERMINALS) {
			fprintf(stderr, "malformed TERMINALS record\n");
			goto fail;
		}
		plan->terminals = calloc(plan->terminal_count, sizeof(*plan->terminals));
		if (!plan->terminals) {
			fprintf(stderr, "out of memory reading terminals\n");
			goto fail;
		}
		for (size_t i = 0; i < plan->terminal_count; ++i) {
			struct TerminalCondition* terminal = &plan->terminals[i];
			unsigned reason;

			if (fscanf(file, "%u %zu", &reason,
			           &terminal->comparison_count) != 2 ||
			    reason < TERMINAL_SUCCESS ||
			    reason > TERMINAL_CONTROLLER_EXHAUSTED ||
			    (reason_mask & (1u << reason)) != 0 ||
			    terminal->comparison_count == 0 ||
			    terminal->comparison_count > MAX_TERMINAL_COMPARISONS) {
				fprintf(stderr, "malformed terminal %zu\n", i);
				goto fail;
			}
			terminal->reason = (enum TerminalReason) reason;
			reason_mask |= 1u << reason;
			terminal->comparisons = calloc(
			    terminal->comparison_count, sizeof(*terminal->comparisons));
			if (!terminal->comparisons) {
				fprintf(stderr, "out of memory reading terminal comparisons\n");
				goto fail;
			}
			for (size_t j = 0; j < terminal->comparison_count; ++j) {
				struct Comparison* comparison = &terminal->comparisons[j];
				if (fscanf(file, "%zu %u %" SCNu32,
				           &comparison->probe_index, &comparison->operator,
				           &comparison->value) != 3 ||
				    comparison->probe_index >= plan->run_probe_count ||
				    comparison->operator > COMPARE_GE ||
				    !probe_value_fits(
				        plan->run_probes[comparison->probe_index].size,
				        comparison->value)) {
					fprintf(stderr,
					        "malformed comparison %zu at terminal %zu\n",
					        j, i);
					goto fail;
				}
				for (size_t prior = 0; prior < j; ++prior) {
					const struct Comparison* previous =
					    &terminal->comparisons[prior];
					if (previous->probe_index == comparison->probe_index &&
					    previous->operator == comparison->operator &&
					    previous->value == comparison->value) {
						fprintf(stderr,
						        "duplicate comparison %zu at terminal %zu\n",
						        j, i);
						goto fail;
					}
				}
			}
		}
		if ((reason_mask & (1u << TERMINAL_SUCCESS)) == 0) {
			fprintf(stderr, "TERMINALS has no success condition\n");
			goto fail;
		}
		if (fscanf(file, "%31s %u", word, &enabled) != 2 ||
		    strcmp(word, "STALL") != 0 || enabled > 1) {
			fprintf(stderr, "malformed STALL record\n");
			goto fail;
		}
		plan->stall.enabled = enabled != 0;
		if (plan->stall.enabled &&
		    (fscanf(file, "%zu %zu %u %" SCNu32 " %" SCNu32,
		            &plan->stall.progress_probe_index,
		            &plan->stall.work_expected.probe_index,
		            &plan->stall.work_expected.operator,
		            &plan->stall.work_expected.value,
		            &plan->stall.max_unchanged_frames) != 5 ||
		     plan->stall.progress_probe_index >= plan->run_probe_count ||
		     plan->stall.work_expected.probe_index >= plan->run_probe_count ||
		     plan->stall.work_expected.operator > COMPARE_GE ||
		     !probe_value_fits(
		         plan->run_probes[plan->stall.work_expected.probe_index].size,
		         plan->stall.work_expected.value) ||
		     plan->stall.max_unchanged_frames == 0 ||
		     plan->stall.max_unchanged_frames >= plan->max_frames)) {
			fprintf(stderr, "malformed STALL payload\n");
			goto fail;
		}
		if (fscanf(file, "%31s %u", word, &enabled) != 2 ||
		    strcmp(word, "TURN_LIMIT") != 0 || enabled > 1) {
			fprintf(stderr, "malformed TURN_LIMIT record\n");
			goto fail;
		}
		plan->turn_limit.enabled = enabled != 0;
		if (plan->turn_limit.enabled &&
		    (fscanf(file, "%zu %" SCNu32, &plan->turn_limit.probe_index,
		            &plan->turn_limit.maximum) != 2 ||
		     plan->turn_limit.probe_index >= plan->run_probe_count ||
		     plan->turn_limit.maximum == 0 ||
		     !probe_value_fits(
		         plan->run_probes[plan->turn_limit.probe_index].size,
		         plan->turn_limit.maximum))) {
			fprintf(stderr, "malformed TURN_LIMIT payload\n");
			goto fail;
		}
		if (fscanf(file, "%31s %u", word, &enabled) != 2 ||
		    strcmp(word, "ACTION_LIMIT") != 0 || enabled > 1) {
			fprintf(stderr, "malformed ACTION_LIMIT record\n");
			goto fail;
		}
		plan->action_limit.enabled = enabled != 0;
		if (plan->action_limit.enabled &&
		    (fscanf(file, "%zu %" SCNu32, &plan->action_limit.probe_index,
		            &plan->action_limit.maximum) != 2 ||
		     plan->action_limit.probe_index >= plan->run_probe_count ||
		     plan->action_limit.maximum == 0 ||
		     !probe_value_fits(
		         plan->run_probes[plan->action_limit.probe_index].size,
		         plan->action_limit.maximum))) {
			fprintf(stderr, "malformed ACTION_LIMIT payload\n");
			goto fail;
		}
		if (version == 5) {
			if (fscanf(file, "%31s %u %" SCNu32 " %" SCNu32,
			           word, &plan->execution_profile,
			           &plan->config_apply_frame,
			           &plan->play_state_config_address) != 4 ||
			    strcmp(word, "PROFILE") != 0 ||
			    plan->execution_profile > 1 ||
			    (plan->execution_profile == 0 &&
			     (plan->config_apply_frame != 0 ||
			      plan->play_state_config_address != 0)) ||
			    (plan->execution_profile == 1 &&
			     (plan->config_apply_frame >= plan->max_frames ||
			      (plan->play_state_config_address & 3u) != 0))) {
				fprintf(stderr, "malformed PROFILE record\n");
				goto fail;
			}
			if (fscanf(file, "%31s %zu", word, &plan->trace_probe_count) != 2 ||
			    strcmp(word, "TRACE") != 0 ||
			    plan->trace_probe_count == 0 ||
			    plan->trace_probe_count > MAX_TRACE_PROBES) {
				fprintf(stderr, "malformed TRACE record\n");
				goto fail;
			}
			plan->trace_probes = calloc(
			    plan->trace_probe_count, sizeof(*plan->trace_probes));
			if (!plan->trace_probes) {
				fprintf(stderr, "out of memory reading trace probes\n");
				goto fail;
			}
			for (size_t i = 0; i < plan->trace_probe_count; ++i) {
				struct Probe* probe = &plan->trace_probes[i];
				if (fscanf(file, "%" SCNu32 " %u", &probe->address,
				           &probe->size) != 2 ||
				    (probe->size != 1 && probe->size != 2 && probe->size != 4)) {
					fprintf(stderr, "malformed trace probe %zu\n", i);
					goto fail;
				}
				for (size_t prior = 0; prior < i; ++prior) {
					const struct Probe* previous = &plan->trace_probes[prior];
					if (previous->address == probe->address &&
					    previous->size == probe->size) {
						fprintf(stderr, "duplicate trace probe %zu\n", i);
						goto fail;
					}
				}
			}
			if ((uint64_t) plan->max_frames * plan->trace_probe_count >
			    MAX_TRACE_RECORDS) {
				fprintf(stderr, "trace record budget exceeds %u\n",
				        MAX_TRACE_RECORDS);
				goto fail;
			}
		}
	}
	if (version == 6) {
		if (fscanf(file, "%31s %zu", word, &plan->baseline_probe_count) != 2 ||
		    strcmp(word, "BASELINE_PROBES") != 0 ||
		    plan->baseline_probe_count > MAX_BASELINE_PROBES) {
			fprintf(stderr, "malformed BASELINE_PROBES record\n");
			goto fail;
		}
		plan->baseline_probes = calloc(
		    plan->baseline_probe_count, sizeof(*plan->baseline_probes));
		if (plan->baseline_probe_count && !plan->baseline_probes) {
			fprintf(stderr, "out of memory reading baseline probes\n");
			goto fail;
		}
		for (size_t i = 0; i < plan->baseline_probe_count; ++i) {
			struct Probe* probe = &plan->baseline_probes[i];
			if (fscanf(file, "%" SCNu32 " %u", &probe->address,
			           &probe->size) != 2 ||
			    (probe->size != 1 && probe->size != 2 &&
			     probe->size != 4)) {
				fprintf(stderr, "malformed baseline probe %zu\n", i);
				goto fail;
			}
			for (size_t prior = 0; prior < i; ++prior) {
				const struct Probe* previous = &plan->baseline_probes[prior];
				if (previous->address == probe->address &&
				    previous->size == probe->size) {
					fprintf(stderr, "duplicate baseline probe %zu\n", i);
					goto fail;
				}
			}
		}
		if (fscanf(file, "%31s %" SCNu32 " %" SCNu32 " %u %" SCNu32,
		           word, &plan->seed_frame, &plan->seed_write.address,
		           &plan->seed_write.size, &plan->seed_value) != 5 ||
		    strcmp(word, "SEED_WRITE") != 0 ||
		    plan->seed_frame >= plan->max_frames ||
		    (plan->seed_write.size != 1 && plan->seed_write.size != 2 &&
		     plan->seed_write.size != 4) ||
		    !probe_value_fits(plan->seed_write.size, plan->seed_value) ||
		    !writable_probe_address(plan->seed_write.address,
		                            plan->seed_write.size)) {
			fprintf(stderr, "malformed SEED_WRITE record\n");
			goto fail;
		}
		plan->has_seed_write = true;
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

/* Same FNV-1a construction/pixel-byte extraction as hash_framebuffer(),
 * restricted to a single rectangular sub-region -- proves a *specific*
 * screen area changed/differs (e.g. a locale-specific visible decoration
 * marker) independently of every other pixel on screen, unlike a
 * whole-frame hash which cannot distinguish "this exact area changed"
 * from "something, somewhere on screen, changed". Scans row-major within
 * the region only, so is deterministic and stable across builds exactly
 * like hash_framebuffer() itself. */
static uint64_t hash_region(const color_t* buffer, unsigned width,
                            const struct Region* region)
{
	uint64_t hash = UINT64_C(14695981039346656037);
	for (uint32_t row = 0; row < region->height; ++row) {
		const color_t* line = buffer + (size_t) (region->y + row) * width + region->x;
		for (uint32_t col = 0; col < region->width; ++col) {
			uint32_t pixel = line[col];
			for (unsigned shift = 0; shift < 24; shift += 8) {
				hash ^= (pixel >> shift) & 0xFF;
				hash *= UINT64_C(1099511628211);
			}
		}
	}
	return hash;
}

/* Reads back a single pixel's canonical 24-bit color value, re-packed as
 * a conventional 0xRRGGBB integer (R in bits 16-23, G in bits 8-15, B in
 * bits 0-7) so its printed hex text reads in the familiar left-to-right
 * RRGGBB order -- host endianness/alpha/padding-independent, using the
 * exact same per-byte extraction as hash_framebuffer()/hash_region()
 * (shifts 0/8/16 = R/G/B of the source color_t). The finest-grained real
 * visible-pixel proof available: one exact on-screen color at a time. */
static uint32_t read_pixel(const color_t* buffer, unsigned width,
                           const struct PixelProbe* pixel)
{
	uint32_t value = buffer[(size_t) pixel->y * width + pixel->x];
	uint32_t r = value & 0xFFu;
	uint32_t g = (value >> 8) & 0xFFu;
	uint32_t b = (value >> 16) & 0xFFu;
	return (r << 16) | (g << 8) | b;
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

static void write_probe(struct mCore* core, const struct Probe* probe, uint32_t value)
{
	switch (probe->size) {
	case 1:
		core->busWrite8(core, probe->address, value);
		break;
	case 2:
		core->busWrite16(core, probe->address, value);
		break;
	default:
		core->busWrite32(core, probe->address, value);
		break;
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
	/* Clone libmGBA's actual save backing store rather than reading the
	 * cartridge bus window: bus reads can expose transient mapper state even
	 * when the persisted 0x8000-byte image is unchanged. */
	void* save_data = NULL;
	size_t save_size = 0;
	bool owns_save_data = true;
	bool use_bus = false;
	uint64_t hash = UINT64_C(14695981039346656037);

	if (core->savedataClone != NULL)
		save_size = core->savedataClone(core, &save_data);

	if (save_data == NULL || save_size != GBA_SRAM_SIZE) {
		free(save_data);
		use_bus = true;
		owns_save_data = false;
		save_data = NULL;
		save_size = GBA_SRAM_SIZE;
	}

	for (uint32_t offset = 0; offset < GBA_SRAM_SIZE; ++offset) {
		if (offset_excluded(exclude_ranges, exclude_range_count, offset))
			continue;
		uint8_t byte = use_bus
		    ? core->busRead8(core, GBA_SRAM_BASE + offset)
		    : ((const uint8_t*) save_data)[offset];
		hash ^= byte;
		hash *= UINT64_C(1099511628211);
	}
	if (owns_save_data)
		free(save_data);
	return hash;
}

static bool comparison_matches(uint32_t actual, const struct Comparison* comparison)
{
	switch (comparison->operator) {
	case COMPARE_EQ:
		return actual == comparison->value;
	case COMPARE_NE:
		return actual != comparison->value;
	case COMPARE_LT:
		return actual < comparison->value;
	case COMPARE_LE:
		return actual <= comparison->value;
	case COMPARE_GT:
		return actual > comparison->value;
	default:
		return actual >= comparison->value;
	}
}

static bool terminal_matches(const struct TerminalCondition* terminal,
                             const uint32_t* run_values)
{
	for (size_t i = 0; i < terminal->comparison_count; ++i) {
		const struct Comparison* comparison = &terminal->comparisons[i];
		if (!comparison_matches(run_values[comparison->probe_index], comparison))
			return false;
	}
	return true;
}

static const char* terminal_reason_name(enum TerminalReason reason)
{
	switch (reason) {
	case TERMINAL_SUCCESS:
		return "success";
	case TERMINAL_OBJECTIVE_FAILURE:
		return "objective_failure";
	case TERMINAL_CONTROLLER_EXHAUSTED:
		return "controller_exhausted";
	case TERMINAL_ENGINE_STALL:
		return "engine_stall";
	case TERMINAL_MAX_FRAMES:
		return "max_frames";
	case TERMINAL_MAX_TURNS:
		return "max_turns";
	case TERMINAL_MAX_ACTIONS:
		return "max_actions";
	default:
		return NULL;
	}
}

static void emit_checkpoint(struct mCore* core, const color_t* buffer,
                            unsigned width, unsigned height,
                            const struct Checkpoint* checkpoint,
                            size_t checkpoint_index, uint32_t frame)
{
	if (checkpoint->framebuffer) {
		uint64_t hash = hash_framebuffer(buffer, width, height);
		printf("CHECKPOINT\t%zu\t%" PRIu32 "\t%016" PRIx64 "\n",
		       checkpoint_index, frame, hash);
	} else {
		printf("CHECKPOINT\t%zu\t%" PRIu32 "\n", checkpoint_index, frame);
	}
	for (size_t probe_index = 0;
	     probe_index < checkpoint->probe_count; ++probe_index) {
		printf("PROBE\t%zu\t%zu\t%" PRIu32 "\n",
		       checkpoint_index, probe_index,
		       read_probe(core, &checkpoint->probes[probe_index]));
	}
	if (checkpoint->sram_hash) {
		uint64_t sram = hash_sram(core, checkpoint->exclude_ranges,
		                           checkpoint->exclude_range_count);
		printf("SRAMHASH\t%zu\t%016" PRIx64 "\n", checkpoint_index, sram);
	}
	for (size_t region_index = 0;
	     region_index < checkpoint->region_count; ++region_index) {
		uint64_t region_hash = hash_region(
		    buffer, width, &checkpoint->regions[region_index]);
		printf("REGIONHASH\t%zu\t%zu\t%016" PRIx64 "\n",
		       checkpoint_index, region_index, region_hash);
	}
	for (size_t pixel_index = 0;
	     pixel_index < checkpoint->pixel_probe_count; ++pixel_index) {
		uint32_t rgb = read_pixel(
		    buffer, width, &checkpoint->pixel_probes[pixel_index]);
		printf("PIXEL\t%zu\t%zu\t%06" PRIx32 "\n",
		       checkpoint_index, pixel_index, rgb);
	}
}

static bool apply_accelerated_fidelity_config(struct mCore* core,
                                              const struct Plan* plan,
                                              uint32_t frame)
{
	uint32_t before;
	uint32_t after;
	uint32_t observed;

	if (plan->execution_profile != 1 || frame != plan->config_apply_frame)
		return true;
	before = core->busRead32(core, plan->play_state_config_address);
	after = before | PLAYST_CONFIG_GAME_SPEED_MASK;
	after &= ~PLAYST_CONFIG_ANIMATION_TYPE_MASK;
	after |= PLAYST_CONFIG_ANIMATION_TYPE_OFF;
	core->busWrite32(core, plan->play_state_config_address, after);
	observed = core->busRead32(core, plan->play_state_config_address);
	if (observed != after) {
		fprintf(stderr,
		        "accelerated config write was not applied at %08" PRIx32
		        ": expected %08" PRIx32 ", read %08" PRIx32 "\n",
		        plan->play_state_config_address, after, observed);
		return false;
	}
	printf("PROFILE\t%" PRIu32 "\t%08" PRIx32 "\t%08" PRIx32 "\n",
	       frame, before, observed);
	return true;
}

static void emit_trace(struct mCore* core, const struct Plan* plan,
                       uint32_t frame, uint32_t* previous_values,
                       bool* have_previous_values)
{
	uint32_t values[MAX_TRACE_PROBES];
	bool changed = !*have_previous_values;

	for (size_t i = 0; i < plan->trace_probe_count; ++i) {
		values[i] = read_probe(core, &plan->trace_probes[i]);
		if (*have_previous_values && values[i] != previous_values[i])
			changed = true;
	}
	if (!changed)
		return;
	for (size_t i = 0; i < plan->trace_probe_count; ++i)
		printf("TRACE\t%" PRIu32 "\t%zu\t%" PRIu32 "\n",
		       frame, i, values[i]);
	memcpy(previous_values, values, plan->trace_probe_count * sizeof(*values));
	*have_previous_values = true;
}

static void apply_frame_input(struct mCore* core, const struct Plan* plan,
                              size_t* range_index, uint32_t frame)
{
	uint32_t keys = 0;

	while (*range_index < plan->range_count &&
	       plan->ranges[*range_index].end < frame)
		(*range_index)++;
	if (*range_index < plan->range_count &&
	    plan->ranges[*range_index].start <= frame)
		keys = plan->ranges[*range_index].keys;
	if (plan->has_seed_write && plan->seed_frame == frame)
		write_probe(core, &plan->seed_write, plan->seed_value);
	core->setKeys(core, keys);
	core->runFrame(core);
}

static int run_fixed(struct mCore* core, const struct Plan* plan,
                     const color_t* buffer, unsigned width, unsigned height)
{
	size_t range_index = 0;
	size_t checkpoint_index = 0;
	uint32_t last_frame = plan->checkpoints[plan->checkpoint_count - 1].frame;

	for (uint32_t frame = 0; frame <= last_frame; ++frame) {
		apply_frame_input(core, plan, &range_index, frame);
		if (checkpoint_index < plan->checkpoint_count &&
		    plan->checkpoints[checkpoint_index].frame == frame) {
			emit_checkpoint(core, buffer, width, height,
			                &plan->checkpoints[checkpoint_index],
			                checkpoint_index, frame);
			++checkpoint_index;
		}
	}
	return checkpoint_index == plan->checkpoint_count ? 0 : 2;
}

static int run_until(struct mCore* core, const struct Plan* plan,
                     const color_t* buffer, unsigned width, unsigned height)
{
	size_t range_index = 0;
	bool have_previous_epoch = false;
	bool previous_work_expected = false;
	uint32_t previous_epoch = 0;
	uint32_t unchanged_frames = 0;
	uint32_t trace_values[MAX_TRACE_PROBES];
	bool have_trace_values = false;

	for (uint32_t frame = 0; frame < plan->max_frames; ++frame) {
		uint32_t run_values[MAX_RUN_PROBES];
		enum TerminalReason reason = TERMINAL_NONE;
		size_t matched_terminals = 0;
		uint32_t turn_value = 0;
		uint32_t action_value = 0;
		bool work_expected = false;

		if (!apply_accelerated_fidelity_config(core, plan, frame))
			return 2;
		if (plan->has_seed_write && frame == plan->seed_frame) {
			for (size_t i = 0; i < plan->baseline_probe_count; ++i)
				printf("BASELINE\t%zu\t%" PRIu32 "\n", i,
				       read_probe(core, &plan->baseline_probes[i]));
		}
		apply_frame_input(core, plan, &range_index, frame);
		if (plan->trace_probe_count != 0)
			emit_trace(core, plan, frame, trace_values, &have_trace_values);
		for (size_t i = 0; i < plan->run_probe_count; ++i)
			run_values[i] = read_probe(core, &plan->run_probes[i]);

		if (plan->stall.enabled) {
			uint32_t epoch = run_values[plan->stall.progress_probe_index];
			if (have_previous_epoch && epoch < previous_epoch) {
				fprintf(stderr,
				        "progress epoch regressed at frame %" PRIu32
				        " from %" PRIu32 " to %" PRIu32 "\n",
				        frame, previous_epoch, epoch);
				return 2;
			}
			work_expected = comparison_matches(
			    run_values[plan->stall.work_expected.probe_index],
			    &plan->stall.work_expected);
			if (work_expected && previous_work_expected &&
			    have_previous_epoch && epoch == previous_epoch)
				++unchanged_frames;
			else
				unchanged_frames = 0;
			previous_epoch = epoch;
			have_previous_epoch = true;
			previous_work_expected = work_expected;
		}

		for (size_t i = 0; i < plan->terminal_count; ++i) {
			if (terminal_matches(&plan->terminals[i], run_values)) {
				reason = plan->terminals[i].reason;
				++matched_terminals;
			}
		}
		if (matched_terminals > 1) {
			fprintf(stderr,
			        "multiple terminal conditions matched at frame %" PRIu32 "\n",
			        frame);
			return 2;
		}
		if (reason == TERMINAL_NONE && plan->stall.enabled && work_expected &&
		    unchanged_frames >= plan->stall.max_unchanged_frames)
			reason = TERMINAL_ENGINE_STALL;

		if (plan->turn_limit.enabled) {
			turn_value = run_values[plan->turn_limit.probe_index];
			if (reason == TERMINAL_NONE &&
			    turn_value >= plan->turn_limit.maximum)
				reason = TERMINAL_MAX_TURNS;
		}
		if (plan->action_limit.enabled) {
			action_value = run_values[plan->action_limit.probe_index];
			if (reason == TERMINAL_NONE &&
			    action_value >= plan->action_limit.maximum)
				reason = TERMINAL_MAX_ACTIONS;
		}
		if (reason == TERMINAL_NONE && frame + 1 == plan->max_frames)
			reason = TERMINAL_MAX_FRAMES;

		if (reason != TERMINAL_NONE) {
			const char* reason_name = terminal_reason_name(reason);
			if (!reason_name) {
				fprintf(stderr, "unknown terminal reason at frame %" PRIu32 "\n",
				        frame);
				return 2;
			}
			printf("TERMINAL\t%s\t%" PRIu32 "\t%u\t%" PRIu32
			       "\t%u\t%" PRIu32 "\n",
			       reason_name, frame, plan->turn_limit.enabled ? 1u : 0u,
			       turn_value, plan->action_limit.enabled ? 1u : 0u,
			       action_value);
			emit_checkpoint(core, buffer, width, height,
			                &plan->checkpoints[0], 0, frame);
			return 0;
		}
	}
	fprintf(stderr, "run-until exhausted without a terminal reason\n");
	return 2;
}

static bool dump_sram(struct mCore* core, const char* output_path)
{
	FILE* output;

	if (!output_path)
		return true;

	output = fopen(output_path, "wb");
	if (!output) {
		fprintf(stderr, "could not open SRAM output: %s\n", output_path);
		return false;
	}
	for (uint32_t offset = 0; offset < GBA_SRAM_SIZE; ++offset) {
		uint8_t byte = core->busRead8(core, GBA_SRAM_BASE + offset);

		if (fwrite(&byte, 1, 1, output) != 1) {
			fprintf(stderr, "could not write SRAM output: %s\n", output_path);
			fclose(output);
			return false;
		}
	}
	if (fclose(output) != 0) {
		fprintf(stderr, "could not close SRAM output: %s\n", output_path);
		return false;
	}
	return true;
}

static int run(
	const char* rom_path,
	const struct Plan* plan,
	const char* sram_path,
	const char* sram_output_path)
{
	struct mCore* core = mCoreFind(rom_path);
	int result;
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
		FILE* file = fopen(sram_path, "rb");

		if (file == NULL) {
			fprintf(stderr, "mGBA could not open SRAM image: %s\n", sram_path);
			mCoreConfigDeinit(&core->config);
			core->deinit(core);
			return 2;
		}
		fclose(file);
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

	result = plan->run_until
	    ? run_until(core, plan, buffer, width, height)
	    : run_fixed(core, plan, buffer, width, height);
	if (result == 0 && !dump_sram(core, sram_output_path))
		result = 2;
	free(buffer);
	mCoreConfigDeinit(&core->config);
	core->deinit(core);
	return result;
}

int main(int argc, char** argv)
{
	const char* log_capture_path;
	int result;

	if (argc != 3 && argc != 4 && argc != 5) {
		fprintf(stderr, "usage: %s <rom.gba> <plan> [sram-image] [sram-output]\n", argv[0]);
		return 2;
	}
	log_capture_path = getenv("GBA_PLAYTEST_LOG_CAPTURE");
	if (log_capture_path) {
		sLogCapture = fopen(log_capture_path, "w");
		if (!sLogCapture) {
			fprintf(stderr, "cannot open mGBA log capture: %s\n", log_capture_path);
			return 2;
		}
	}
	struct mLogger logger = {.log = capture_log, .filter = NULL};
	mLogSetDefaultLogger(&logger);
	struct Plan plan = {0};
	if (!read_plan(argv[2], &plan)) {
		if (sLogCapture)
			fclose(sLogCapture);
		return 2;
	}
	result = run(
		argv[1],
		&plan,
		argc >= 4 ? argv[3] : NULL,
		argc == 5 ? argv[4] : NULL);
	free_plan(&plan);
	if (sLogCapture && fclose(sLogCapture) != 0) {
		fprintf(stderr, "cannot finalize mGBA log capture: %s\n", log_capture_path);
		return 2;
	}
	return result;
}
