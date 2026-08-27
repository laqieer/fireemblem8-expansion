/*
 * Restricted interactive libmGBA transport for the autoplay planner.
 *
 * The executable is compiled with the three exact linked symbol addresses.
 * Its line protocol can read only the fixed observation/checkpoint records
 * and can write only fields of the fixed command mailbox.
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

#if !defined(PLANNER_OBSERVATION_ADDR) || !defined(PLANNER_COMMAND_ADDR) \
    || !defined(PLANNER_CHECKPOINT_ADDR)
#error "planner transport requires exact linked planner symbol addresses"
#endif

#define EXPANSION_AUTOPLAY_PLANNER_MAGIC UINT32_C(0x41504C4E)
#define EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION UINT32_C(2)
#define EXPANSION_AUTOPLAY_PLANNER_COMMAND_START UINT32_C(1)
#define EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT UINT32_C(2)
#define EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL UINT32_C(3)
#define EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE UINT32_C(4)
#define COMMAND_WORD_COUNT 16u
#define OBSERVATION_WORD_COUNT 249u
#define CHECKPOINT_WORD_COUNT 13u

static void discard_log(
    struct mLogger* logger,
    int category,
    enum mLogLevel level,
    const char* format,
    va_list args)
{
    (void)logger;
    (void)category;
    (void)level;
    (void)format;
    (void)args;
}

static uint32_t read_word(struct mCore* core, uint32_t base, size_t index)
{
    return core->busRead32(core, base + (uint32_t)(index * sizeof(uint32_t)));
}

static void write_word(
    struct mCore* core,
    uint32_t base,
    size_t index,
    uint32_t value)
{
    core->busWrite32(
        core,
        base + (uint32_t)(index * sizeof(uint32_t)),
        value);
}

static void clear_command(struct mCore* core)
{
    size_t index;

    for (index = 0; index < COMMAND_WORD_COUNT; index++)
        write_word(core, PLANNER_COMMAND_ADDR, index, 0);
}

static void write_command(
    struct mCore* core,
    uint32_t kind,
    uint32_t run_id,
    uint32_t observation_id,
    uint32_t page_index,
    uint32_t ordinal,
    uint32_t token_lo,
    uint32_t token_hi,
    uint32_t expected_rom,
    uint32_t expected_config,
    uint32_t expected_scenario,
    uint32_t expected_seed)
{
    clear_command(core);
    write_word(core, PLANNER_COMMAND_ADDR, 0, EXPANSION_AUTOPLAY_PLANNER_MAGIC);
    write_word(
        core,
        PLANNER_COMMAND_ADDR,
        1,
        EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION);
    write_word(
        core,
        PLANNER_COMMAND_ADDR,
        2,
        COMMAND_WORD_COUNT * sizeof(uint32_t));
    write_word(core, PLANNER_COMMAND_ADDR, 3, kind);
    write_word(core, PLANNER_COMMAND_ADDR, 4, run_id);
    write_word(core, PLANNER_COMMAND_ADDR, 5, observation_id);
    write_word(core, PLANNER_COMMAND_ADDR, 6, page_index);
    write_word(core, PLANNER_COMMAND_ADDR, 7, ordinal);
    write_word(core, PLANNER_COMMAND_ADDR, 8, token_lo);
    write_word(core, PLANNER_COMMAND_ADDR, 9, token_hi);
    write_word(core, PLANNER_COMMAND_ADDR, 10, expected_rom);
    write_word(core, PLANNER_COMMAND_ADDR, 11, expected_config);
    write_word(core, PLANNER_COMMAND_ADDR, 12, expected_scenario);
    write_word(core, PLANNER_COMMAND_ADDR, 13, expected_seed);
}

static void emit_state(struct mCore* core)
{
    size_t index;

    fputs("OBS", stdout);
    for (index = 0; index < OBSERVATION_WORD_COUNT; index++)
        printf(" %08" PRIx32, read_word(core, PLANNER_OBSERVATION_ADDR, index));
    fputs(" CHECKPOINT", stdout);
    for (index = 0; index < CHECKPOINT_WORD_COUNT; index++)
        printf(" %08" PRIx32, read_word(core, PLANNER_CHECKPOINT_ADDR, index));
    fputs(" COMMAND", stdout);
    for (index = 0; index < COMMAND_WORD_COUNT; index++)
        printf(" %08" PRIx32, read_word(core, PLANNER_COMMAND_ADDR, index));
    fputc('\n', stdout);
    fflush(stdout);
}

static void run_until_command_settled(
    struct mCore* core,
    uint32_t kind,
    uint32_t previous_state,
    uint32_t previous_observation,
    uint32_t previous_page,
    uint32_t previous_rejection,
    uint32_t requested_page,
    bool expect_start_success)
{
    int frames;

    for (frames = 0; frames < 120; frames++)
    {
        uint32_t state;
        uint32_t observation;
        uint32_t page;
        uint32_t rejection;

        core->runFrame(core);
        if (read_word(core, PLANNER_COMMAND_ADDR, 3) != 0)
            continue;
        state = read_word(core, PLANNER_OBSERVATION_ADDR, 5);
        observation = read_word(core, PLANNER_OBSERVATION_ADDR, 4);
        page = read_word(core, PLANNER_OBSERVATION_ADDR, 6);
        rejection = read_word(core, PLANNER_OBSERVATION_ADDR, 13);
        if (kind == EXPANSION_AUTOPLAY_PLANNER_COMMAND_START)
        {
            if ((expect_start_success
                    && (state == 2 || state == 4 || state == 5))
                || (!expect_start_success
                    && rejection != previous_rejection))
                return;
        }
        else if (kind == EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE)
        {
            if (page == requested_page || rejection != previous_rejection)
                return;
        }
        else if (kind == EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT)
        {
            if ((observation != previous_observation && state == 2)
                || rejection != previous_rejection
                || state == 4)
                return;
        }
        else if (state != previous_state
            || observation != previous_observation
            || page != previous_page
            || rejection != previous_rejection)
        {
            return;
        }
    }
}

static bool parse_hex(const char* text, uint32_t* value)
{
    char* end;
    unsigned long parsed;

    parsed = strtoul(text, &end, 16);
    if (text[0] == '\0' || *end != '\0' || parsed > UINT32_MAX)
        return false;
    *value = (uint32_t)parsed;
    return true;
}

static bool read_values(
    char** token,
    size_t count,
    uint32_t* values)
{
    size_t index;

    for (index = 0; index < count; index++)
    {
        token[index] = strtok(NULL, " \t\r\n");
        if (token[index] == NULL || !parse_hex(token[index], &values[index]))
            return false;
    }
    return strtok(NULL, " \t\r\n") == NULL;
}

static int run_transport(const char* rom_path)
{
    struct mCore* core;
    color_t* buffer;
    unsigned width;
    unsigned height;
    char line[512];
    int startup_frames;

    core = mCoreFind(rom_path);
    if (core == NULL || !core->init(core))
    {
        fprintf(stderr, "planner transport could not initialize libmGBA\n");
        return 2;
    }
    mCoreInitConfig(core, NULL);
    if (!mCoreLoadFile(core, rom_path))
    {
        fprintf(stderr, "planner transport could not load ROM\n");
        mCoreConfigDeinit(&core->config);
        core->deinit(core);
        return 2;
    }
    core->desiredVideoDimensions(core, &width, &height);
    buffer = calloc((size_t)width * height, sizeof(*buffer));
    if (buffer == NULL)
    {
        fprintf(stderr, "planner transport could not allocate video buffer\n");
        mCoreConfigDeinit(&core->config);
        core->deinit(core);
        return 2;
    }
    core->setVideoBuffer(core, buffer, width);
    core->reset(core);
    for (startup_frames = 0; startup_frames < 4; startup_frames++)
        core->runFrame(core);
    emit_state(core);

    while (fgets(line, sizeof(line), stdin) != NULL)
    {
        char* command = strtok(line, " \t\r\n");
        char* tokens[11];
        uint32_t values[11];
        uint32_t kind = 0;
        uint32_t requested_page = 0;
        uint32_t previous_state =
            read_word(core, PLANNER_OBSERVATION_ADDR, 5);
        uint32_t previous_observation =
            read_word(core, PLANNER_OBSERVATION_ADDR, 4);
        uint32_t previous_page =
            read_word(core, PLANNER_OBSERVATION_ADDR, 6);
        uint32_t previous_rejection =
            read_word(core, PLANNER_OBSERVATION_ADDR, 13);
        bool expect_start_success = false;

        if (command == NULL)
            continue;
        if (strcmp(command, "QUIT") == 0)
            break;
        if (strcmp(command, "READ") == 0)
        {
            if (strtok(NULL, " \t\r\n") != NULL)
            {
                fputs("ERROR malformed READ\n", stdout);
                fflush(stdout);
                continue;
            }
            emit_state(core);
            continue;
        }
        if (strcmp(command, "STEP") == 0)
        {
            if (strtok(NULL, " \t\r\n") != NULL)
            {
                fputs("ERROR malformed STEP\n", stdout);
                fflush(stdout);
                continue;
            }
            core->runFrame(core);
            emit_state(core);
            continue;
        }
        if (strcmp(command, "RUN") == 0)
        {
            uint32_t frame_count;
            uint32_t keys;
            uint32_t frame;

            if (!read_values(tokens, 2, values)
                || values[0] == 0
                || values[0] > 100000
                || values[1] > 0x3FF)
            {
                fputs("ERROR malformed RUN\n", stdout);
                fflush(stdout);
                continue;
            }
            frame_count = values[0];
            keys = values[1];
            core->setKeys(core, keys);
            for (frame = 0; frame < frame_count; frame++)
                core->runFrame(core);
            core->setKeys(core, 0);
            emit_state(core);
            continue;
        }
        if (strcmp(command, "START") == 0)
        {
            if (!read_values(tokens, 4, values))
            {
                fputs("ERROR malformed START\n", stdout);
                fflush(stdout);
                continue;
            }
            write_command(
                core,
                EXPANSION_AUTOPLAY_PLANNER_COMMAND_START,
                0,
                0,
                0,
                0,
                0,
                0,
                values[0],
                values[1],
                values[2],
                values[3]);
            kind = EXPANSION_AUTOPLAY_PLANNER_COMMAND_START;
            expect_start_success =
                values[0] == read_word(core, PLANNER_OBSERVATION_ADDR, 21)
                && values[1] == read_word(core, PLANNER_OBSERVATION_ADDR, 22)
                && values[2] == read_word(core, PLANNER_OBSERVATION_ADDR, 23)
                && values[3] == read_word(core, PLANNER_OBSERVATION_ADDR, 24);
        }
        else if (strcmp(command, "PAGE") == 0)
        {
            if (!read_values(tokens, 3, values))
            {
                fputs("ERROR malformed PAGE\n", stdout);
                fflush(stdout);
                continue;
            }
            write_command(
                core,
                EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
                values[0],
                values[1],
                values[2],
                0,
                0,
                0,
                0,
                0,
                0,
                0);
            kind = EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE;
            requested_page = values[2];
        }
        else if (strcmp(command, "COMMIT") == 0)
        {
            if (!read_values(tokens, 5, values))
            {
                fputs("ERROR malformed COMMIT\n", stdout);
                fflush(stdout);
                continue;
            }
            write_command(
                core,
                EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
                values[0],
                values[1],
                0,
                values[2],
                values[3],
                values[4],
                0,
                0,
                0,
                0);
            kind = EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT;
        }
        else if (strcmp(command, "CANCEL") == 0)
        {
            if (!read_values(tokens, 2, values))
            {
                fputs("ERROR malformed CANCEL\n", stdout);
                fflush(stdout);
                continue;
            }
            write_command(
                core,
                EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL,
                values[0],
                values[1],
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0);
            kind = EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL;
        }
        else if (strcmp(command, "MALFORMED") == 0)
        {
            if (!read_values(tokens, 3, values))
            {
                fputs("ERROR malformed MALFORMED\n", stdout);
                fflush(stdout);
                continue;
            }
            write_command(
                core,
                values[0],
                values[1],
                values[2],
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0);
            kind = values[0];
        }
        else
        {
            fputs("ERROR unknown typed command\n", stdout);
            fflush(stdout);
            continue;
        }
        run_until_command_settled(
            core,
            kind,
            previous_state,
            previous_observation,
            previous_page,
            previous_rejection,
            requested_page,
            expect_start_success);
        emit_state(core);
    }

    free(buffer);
    mCoreConfigDeinit(&core->config);
    core->deinit(core);
    return 0;
}

int main(int argc, char** argv)
{
    struct mLogger logger = { .log = discard_log, .filter = NULL };

    if (argc != 2)
    {
        fprintf(stderr, "usage: %s <planner-rom.gba>\n", argv[0]);
        return 2;
    }
    mLogSetDefaultLogger(&logger);
    return run_transport(argv[1]);
}
