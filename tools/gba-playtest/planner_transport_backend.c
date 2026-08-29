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
#include <errno.h>
#include <poll.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

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
#define EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE UINT32_C(0)
#define EXPANSION_AUTOPLAY_PLANNER_REJECTION_NOT_READY UINT32_C(1)
#define EXPANSION_AUTOPLAY_PLANNER_REJECTION_CANCELLED UINT32_C(8)
#define EXPANSION_AUTOPLAY_PLANNER_REJECTION_TIMEOUT UINT32_C(10)
#define EXPANSION_AUTOPLAY_PLANNER_STATE_READY UINT32_C(1)
#define EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING UINT32_C(2)
#define EXPANSION_AUTOPLAY_PLANNER_STATE_CANCELLED UINT32_C(4)
#define EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED UINT32_C(5)
#define COMMAND_WORD_COUNT 16u
#define OBSERVATION_WORD_COUNT 256u
#define CHECKPOINT_WORD_COUNT 13u

#ifndef PLANNER_COMMAND_ACK_FRAME_LIMIT
#define PLANNER_COMMAND_ACK_FRAME_LIMIT 120u
#endif

#ifndef PLANNER_COMMAND_RESPONSE_FRAME_LIMIT
#define PLANNER_COMMAND_RESPONSE_FRAME_LIMIT 600u
#endif

#ifndef PLANNER_COMMIT_COMPLETION_FRAME_LIMIT
#define PLANNER_COMMIT_COMPLETION_FRAME_LIMIT 18000u
#endif

#ifndef PLANNER_DECISION_WALL_TIMEOUT_MS
#define PLANNER_DECISION_WALL_TIMEOUT_MS 5000u
#endif
#if PLANNER_DECISION_WALL_TIMEOUT_MS == 0 || PLANNER_DECISION_WALL_TIMEOUT_MS > 5000u
#error "PLANNER_DECISION_WALL_TIMEOUT_MS must be in 1..5000"
#endif

#define PLANNER_FRAME_INTERVAL_NS UINT64_C(16666667)

struct command_acknowledgement
{
    uint32_t id;
    uint32_t kind;
    uint32_t result;
    uint32_t rejection;
};

#if PLANNER_TRANSPORT_TEST_BOOTSTRAP
bool PlannerTransport_TestBootstrap(
    struct mCore* core,
    uint32_t expected_identities[4]);
#endif

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
    const uint32_t token[4],
    const uint32_t expected_identities[4])
{
    size_t index;
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
    write_word(core, PLANNER_COMMAND_ADDR, 4, run_id);
    write_word(core, PLANNER_COMMAND_ADDR, 5, observation_id);
    write_word(core, PLANNER_COMMAND_ADDR, 6, page_index);
    write_word(core, PLANNER_COMMAND_ADDR, 7, ordinal);
    if (token != NULL)
        for (index = 0; index < 4; index++)
            write_word(core, PLANNER_COMMAND_ADDR, 8 + index, token[index]);
    if (expected_identities != NULL)
        for (index = 0; index < 4; index++)
            write_word(
                core,
                PLANNER_COMMAND_ADDR,
                8 + index,
                expected_identities[index]);
    write_word(core, PLANNER_COMMAND_ADDR, 3, kind);
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

static void emit_acknowledgement(
    const struct command_acknowledgement* acknowledgement)
{
    printf(
        "ACK %08" PRIx32 " %08" PRIx32 " %08" PRIx32 " %08" PRIx32 "\n",
        acknowledgement->id,
        acknowledgement->kind,
        acknowledgement->result,
        acknowledgement->rejection);
    fflush(stdout);
}

static void emit_completion(
    const struct command_acknowledgement* acknowledgement,
    uint32_t response_frames)
{
    printf(
        "COMPLETE %08" PRIx32 " %08" PRIx32 " %08" PRIx32 "\n",
        acknowledgement->id,
        acknowledgement->kind,
        response_frames);
    fflush(stdout);
}

static void emit_transport_error(
    const char* code,
    uint32_t command_id,
    uint32_t kind)
{
    printf(
        "TRANSPORT_ERROR %s %08" PRIx32 " %08" PRIx32 "\n",
        code,
        command_id,
        kind);
    fflush(stdout);
}

static bool wait_for_command_acknowledgement(
    struct mCore* core,
    uint32_t command_id,
    uint32_t kind,
    struct command_acknowledgement* acknowledgement)
{
    uint32_t frames;
    for (frames = 0; frames < PLANNER_COMMAND_ACK_FRAME_LIMIT; frames++)
    {
        core->runFrame(core);
        if (read_word(core, PLANNER_COMMAND_ADDR, 3) != 0)
            continue;
        acknowledgement->id = command_id;
        acknowledgement->kind = kind;
        acknowledgement->result =
            read_word(core, PLANNER_COMMAND_ADDR, 14);
        acknowledgement->rejection =
            read_word(core, PLANNER_COMMAND_ADDR, 15);
        return true;
    }
    return false;
}

static bool is_terminal_state(uint32_t state)
{
    return state == EXPANSION_AUTOPLAY_PLANNER_STATE_CANCELLED
        || state == EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED;
}

bool PlannerTransport_IsReadyObservationValid(
    const uint32_t words[OBSERVATION_WORD_COUNT],
    const uint32_t expected_identities[4])
{
    size_t index;

    if (words == NULL
        || words[0] != EXPANSION_AUTOPLAY_PLANNER_MAGIC
        || words[1] != EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION
        || words[2] != OBSERVATION_WORD_COUNT * sizeof(uint32_t)
        || words[5] != EXPANSION_AUTOPLAY_PLANNER_STATE_READY
        || words[7] != 1)
        return false;
    for (index = 3; index < OBSERVATION_WORD_COUNT; index++)
    {
        if (index == 5 || index == 7
            || (index >= 21 && index <= 24))
            continue;
        if (words[index] != 0)
            return false;
    }
    for (index = 0; index < 4; index++)
    {
        if (words[21 + index] == 0
            || (expected_identities != NULL
                && words[21 + index] != expected_identities[index]))
            return false;
    }
    return true;
}

bool PlannerTransport_IsReady(
    struct mCore* core,
    const uint32_t expected_identities[4])
{
    uint32_t words[OBSERVATION_WORD_COUNT];
    uint32_t confirmation[OBSERVATION_WORD_COUNT];
    size_t index;

    for (index = 0; index < OBSERVATION_WORD_COUNT; index++)
        words[index] = read_word(core, PLANNER_OBSERVATION_ADDR, index);
    if (!PlannerTransport_IsReadyObservationValid(words, expected_identities))
        return false;
    for (index = 0; index < OBSERVATION_WORD_COUNT; index++)
        confirmation[index] = read_word(core, PLANNER_OBSERVATION_ADDR, index);
    return memcmp(words, confirmation, sizeof(words)) == 0
        && PlannerTransport_IsReadyObservationValid(
            confirmation,
            expected_identities == NULL ? &words[21] : expected_identities);
}

bool PlannerTransport_IsAcknowledgementValid(
    uint32_t result,
    uint32_t rejection)
{
    return (result == 0
            && rejection
                >= EXPANSION_AUTOPLAY_PLANNER_REJECTION_NOT_READY
            && rejection
                <= EXPANSION_AUTOPLAY_PLANNER_REJECTION_TIMEOUT)
        || (result == 1
            && rejection == EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE);
}

static bool is_command_response_complete(
    struct mCore* core,
    const struct command_acknowledgement* acknowledgement,
    uint32_t previous_observation,
    uint32_t requested_page)
{
    uint32_t state =
        read_word(core, PLANNER_OBSERVATION_ADDR, 5);
    uint32_t observation =
        read_word(core, PLANNER_OBSERVATION_ADDR, 4);
    uint32_t page =
        read_word(core, PLANNER_OBSERVATION_ADDR, 6);
    if (acknowledgement->result == 0)
    {
        if (acknowledgement->rejection == 0)
            return false;
        if (acknowledgement->kind
                == EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL
            && acknowledgement->rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_CANCELLED)
            return is_terminal_state(state);
        return true;
    }
    if (acknowledgement->result != 1
        || acknowledgement->rejection != 0)
        return false;
    switch (acknowledgement->kind)
    {
    case EXPANSION_AUTOPLAY_PLANNER_COMMAND_START:
        return state == EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING
            || is_terminal_state(state);
    case EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE:
        return state == EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING
            && page == requested_page;
    case EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT:
        return (state == EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING
                && observation != previous_observation)
            || is_terminal_state(state);
    default:
        return false;
    }
}

static bool wait_for_command_response(
    struct mCore* core,
    const struct command_acknowledgement* acknowledgement,
    uint32_t previous_observation,
    uint32_t requested_page,
    uint32_t* response_frames)
{
    uint32_t frame_limit =
        acknowledgement->kind
            == EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT
        ? PLANNER_COMMIT_COMPLETION_FRAME_LIMIT
        : PLANNER_COMMAND_RESPONSE_FRAME_LIMIT;
    uint32_t frames;
    *response_frames = 0;
    if (is_command_response_complete(
            core,
            acknowledgement,
            previous_observation,
            requested_page))
        return true;
    for (frames = 0; frames < frame_limit; frames++)
    {
        core->runFrame(core);
        *response_frames = frames + 1;
        if (is_command_response_complete(
                core,
                acknowledgement,
                previous_observation,
                requested_page))
            return true;
    }
    return false;
}

static bool parse_hex(const char* text, uint32_t* value)
{
    uint32_t parsed = 0, digit;
    if (*text == '\0')
        return false;
    for (; *text != '\0'; text++)
    {
        if (*text >= '0' && *text <= '9')
            digit = *text - '0';
        else if (*text >= 'a' && *text <= 'f')
            digit = *text - 'a' + 10;
        else if (*text >= 'A' && *text <= 'F')
            digit = *text - 'A' + 10;
        else
            return false;
        if (parsed > (UINT32_MAX - digit) / 16)
            return false;
        parsed = parsed * 16 + digit;
    }
    *value = parsed;
    return true;
}

static bool read_values(char** token, size_t count, uint32_t* values)
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

enum input_line_result
{
    INPUT_LINE_ERROR = -1,
    INPUT_LINE_EOF,
    INPUT_LINE_READY,
    INPUT_LINE_MALFORMED,
    INPUT_LINE_IDLE,
};

struct input_line_state
{
    size_t length;
    bool malformed;
    bool eof;
};

struct decision_timer
{
    uint32_t run_id;
    uint32_t observation_id;
    uint64_t deadline;
    uint64_t next_frame;
    bool active;
};

static int __attribute__((unused)) read_input_line(
    FILE* input, char* line, size_t capacity)
{
    size_t length = 0;
    bool malformed = false;
    int byte;
    while ((byte = fgetc(input)) != EOF)
    {
        if (byte == '\n')
        {
            line[length] = '\0';
            return malformed
                ? INPUT_LINE_MALFORMED
                : INPUT_LINE_READY;
        }
        if (byte == '\0' || length + 1 >= capacity)
        {
            malformed = true;
            continue;
        }
        if (!malformed)
            line[length++] = (char)byte;
    }
    line[length] = '\0';
    if (malformed)
        return INPUT_LINE_MALFORMED;
    return length == 0 ? INPUT_LINE_EOF : INPUT_LINE_READY;
}

static bool monotonic_now(uint64_t* value)
{
    struct timespec time;

    if (clock_gettime(CLOCK_MONOTONIC, &time) != 0)
        return false;
    *value = (uint64_t)time.tv_sec * UINT64_C(1000000000) + (uint64_t)time.tv_nsec;
    return true;
}

static int poll_input_line(
    int fd,
    struct input_line_state* state,
    char* line,
    size_t capacity,
    int timeout)
{
    struct pollfd descriptor = { fd, POLLIN, 0 };
    unsigned char byte;
    ssize_t count;
    int result;

    if (state->eof)
        return INPUT_LINE_EOF;
    do
        result = poll(&descriptor, 1, timeout);
    while (result < 0 && errno == EINTR);
    if (result < 0)
        return INPUT_LINE_ERROR;
    if (result == 0)
        return INPUT_LINE_IDLE;
    do
        count = read(fd, &byte, 1);
    while (count < 0 && errno == EINTR);
    if (count == 0)
    {
        line[state->length] = '\0';
        result = state->malformed
            ? INPUT_LINE_MALFORMED
            : (state->length == 0 ? INPUT_LINE_EOF : INPUT_LINE_READY);
        state->length = 0;
        state->malformed = false;
        state->eof = true;
        return result;
    }
    if (count < 0)
        return errno == EAGAIN || errno == EWOULDBLOCK
            ? INPUT_LINE_IDLE : INPUT_LINE_ERROR;
    if (byte == '\n')
    {
        line[state->length] = '\0';
        result = state->malformed ? INPUT_LINE_MALFORMED : INPUT_LINE_READY;
        state->length = 0;
        state->malformed = false;
        return result;
    }
    if (byte == '\0' || state->length + 1 >= capacity)
        state->malformed = true;
    else if (!state->malformed)
        line[state->length++] = (char)byte;
    return INPUT_LINE_IDLE;
}

static bool refresh_decision_timer(
    struct mCore* core,
    struct decision_timer* timer)
{
    uint32_t state = read_word(core, PLANNER_OBSERVATION_ADDR, 5);
    uint32_t run_id = read_word(core, PLANNER_OBSERVATION_ADDR, 3);
    uint32_t observation_id = read_word(core, PLANNER_OBSERVATION_ADDR, 4);
    uint64_t now;

    if (state != EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING)
    {
        timer->active = false;
        return true;
    }
    if (timer->active
        && timer->run_id == run_id
        && timer->observation_id == observation_id)
        return true;
    if (!monotonic_now(&now))
        return false;
    timer->run_id = run_id;
    timer->observation_id = observation_id;
    timer->deadline = now + (uint64_t)PLANNER_DECISION_WALL_TIMEOUT_MS * UINT64_C(1000000);
    timer->next_frame = now + PLANNER_FRAME_INTERVAL_NS;
    timer->active = true;
    return true;
}

static int service_decision_timer(
    struct mCore* core,
    struct decision_timer* timer)
{
    uint64_t now;
    uint32_t state;
    uint32_t frames;

    if (!timer->active)
        return 0;
    if (!monotonic_now(&now))
        return -1;
    while (now >= timer->next_frame && now < timer->deadline)
    {
        core->runFrame(core);
        timer->next_frame += PLANNER_FRAME_INTERVAL_NS;
        state = read_word(core, PLANNER_OBSERVATION_ADDR, 5);
        if (state != EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING)
        {
            emit_state(core);
            timer->active = false;
            return 1;
        }
    }
    if (now < timer->deadline)
        return 0;
    for (frames = 0; frames < 300
         && read_word(core, PLANNER_OBSERVATION_ADDR, 5)
            == EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING;
         frames++)
        core->runFrame(core);
    state = read_word(core, PLANNER_OBSERVATION_ADDR, 5);
    if (!is_terminal_state(state))
        return -1;
    emit_state(core);
    timer->active = false;
    return 1;
}

static int decision_poll_timeout(const struct decision_timer* timer)
{
    uint64_t now;
    uint64_t next;

    if (!timer->active)
        return -1;
    if (!monotonic_now(&now))
        return 0;
    next = timer->next_frame < timer->deadline ? timer->next_frame : timer->deadline;
    if (next <= now)
        return 0;
    return (int)((next - now + UINT64_C(999999)) / UINT64_C(1000000));
}

#if PLANNER_TRANSPORT_LINE_TEST
int PlannerTransport_ReadLineForTest(
    FILE* input,
    char* line,
    size_t capacity)
{
    return read_input_line(input, line, capacity);
}

bool PlannerTransport_ParseHexForTest(const char* text, uint32_t* value)
{
    return parse_hex(text, value);
}
#endif

static int run_transport(const char* rom_path)
{
    struct mCore* core;
    color_t* buffer;
    unsigned width;
    unsigned height;
    char line[512];
    int startup_frames;
    int transport_result = 0;
    int line_result;
    int timer_result;
    uint32_t next_command_id = 1;
#if PLANNER_TRANSPORT_TEST_BOOTSTRAP
    uint32_t expected_ready_identities[4];
#endif
    struct input_line_state input_state = { 0, false, false };
    struct decision_timer decision = { 0 };
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
#if PLANNER_TRANSPORT_TEST_BOOTSTRAP
    if (!PlannerTransport_TestBootstrap(core, expected_ready_identities))
    {
        fprintf(stderr, "planner transport bootstrap did not reach READY\n");
        free(buffer);
        mCoreConfigDeinit(&core->config);
        core->deinit(core);
        return 3;
    }
#endif
    if (!PlannerTransport_IsReady(
            core,
#if PLANNER_TRANSPORT_TEST_BOOTSTRAP
            expected_ready_identities
#else
            NULL
#endif
        ))
    {
        fprintf(stderr, "planner transport startup is not READY\n");
        free(buffer);
        mCoreConfigDeinit(&core->config);
        core->deinit(core);
        return 3;
    }
    emit_state(core);
    if (!refresh_decision_timer(core, &decision))
        transport_result = 3;
    while (transport_result == 0)
    {
        char* command;
        char* tokens[11];
        uint32_t values[11];
        uint32_t kind = 0;
        uint32_t requested_page = 0;
        uint32_t previous_observation =
            read_word(core, PLANNER_OBSERVATION_ADDR, 4);
        uint32_t response_frames;
        struct command_acknowledgement acknowledgement;
        timer_result = service_decision_timer(core, &decision);
        if (timer_result < 0)
        {
            fputs("planner transport monotonic decision timer failed\n", stderr);
            transport_result = 3;
            break;
        }
        if (timer_result > 0)
        {
            input_state.length = 0;
            input_state.malformed = false;
            continue;
        }
        line_result = poll_input_line(
            STDIN_FILENO,
            &input_state,
            line,
            sizeof(line),
            decision_poll_timeout(&decision));
        if (line_result == INPUT_LINE_ERROR)
        {
            fputs("planner transport stdin poll failed\n", stderr);
            transport_result = 3;
            break;
        }
        if (line_result == INPUT_LINE_IDLE)
            continue;
        if (line_result == INPUT_LINE_EOF)
            break;
        timer_result = service_decision_timer(core, &decision);
        if (timer_result != 0)
        {
            if (timer_result < 0)
                transport_result = 3;
            input_state.length = 0;
            input_state.malformed = false;
            continue;
        }
        command = strtok(line, " \t\r\n");
        if (line_result == INPUT_LINE_MALFORMED)
        {
            fputs("ERROR malformed line\n", stdout);
            fflush(stdout);
            continue;
        }
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
            if (!refresh_decision_timer(core, &decision))
                transport_result = 3;
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
                NULL,
                values);
            kind = EXPANSION_AUTOPLAY_PLANNER_COMMAND_START;
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
                NULL,
                NULL);
            kind = EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE;
            requested_page = values[2];
        }
        else if (strcmp(command, "COMMIT") == 0)
        {
            if (!read_values(tokens, 7, values))
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
                &values[3],
                NULL);
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
                NULL,
                NULL);
            kind = EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL;
        }
        else
        {
            fputs("ERROR unknown typed command\n", stdout);
            fflush(stdout);
            continue;
        }
        if (!wait_for_command_acknowledgement(
                core,
                next_command_id,
                kind,
                &acknowledgement))
        {
            emit_transport_error(
                "COMMAND_ACK_TIMEOUT",
                next_command_id,
                kind);
            transport_result = 3;
            break;
        }
        if (!PlannerTransport_IsAcknowledgementValid(
                acknowledgement.result,
                acknowledgement.rejection))
        {
            emit_transport_error(
                "INVALID_COMMAND_ACK",
                next_command_id,
                kind);
            transport_result = 3;
            break;
        }
        emit_acknowledgement(&acknowledgement);
        if (!wait_for_command_response(
                core,
                &acknowledgement,
                previous_observation,
                requested_page,
                &response_frames))
        {
            emit_transport_error(
                kind == EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT
                    && acknowledgement.result == 1
                    && acknowledgement.rejection == 0
                    ? "ACTION_COMPLETION_TIMEOUT"
                    : "COMMAND_RESPONSE_TIMEOUT",
                next_command_id,
                kind);
            transport_result = 3;
            break;
        }
        emit_completion(&acknowledgement, response_frames);
        emit_state(core);
        if (!refresh_decision_timer(core, &decision))
        {
            transport_result = 3;
            break;
        }
        next_command_id++;
        if (next_command_id == 0)
            next_command_id = 1;
    }
    free(buffer);
    mCoreConfigDeinit(&core->config);
    core->deinit(core);
    return transport_result;
}

#ifndef PLANNER_TRANSPORT_NO_MAIN
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
#endif
