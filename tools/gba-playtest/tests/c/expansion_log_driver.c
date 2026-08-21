#include <stdio.h>
#include <string.h>

#include "global.h"
#include "expansion_log.h"

#define MGBA_LOG_ENABLE_ADDRESS 0x04FFF780
#define MGBA_LOG_BUFFER_ADDRESS 0x04FFF600
#define MGBA_LOG_SEND_ADDRESS 0x04FFF700

#define CHECK(condition, message) \
    do { \
        if (!(condition)) { \
            fprintf(stderr, "EXPANSION_LOG_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

static u16 sHandshakeValue;
static int sEnableWrites;
static int sSendWrites;
static u16 sLastSend;
static u8 sBuffer[EXPANSION_LOG_MESSAGE_MAX + 1];
static int sBufferWrites;

void ExpansionLog_TestWrite16(u32 address, u16 value)
{
    if (address == MGBA_LOG_ENABLE_ADDRESS)
    {
        CHECK(value == 0xC0DE, "handshake uses the mGBA enable magic");
        sEnableWrites++;
    }
    else if (address == MGBA_LOG_SEND_ADDRESS)
    {
        sSendWrites++;
        sLastSend = value;
    }
}

u16 ExpansionLog_TestRead16(u32 address)
{
    CHECK(address == MGBA_LOG_ENABLE_ADDRESS, "only the handshake register is read");
    return sHandshakeValue;
}

void ExpansionLog_TestWrite8(u32 address, u8 value)
{
    CHECK(address >= MGBA_LOG_BUFFER_ADDRESS, "payload starts in mGBA buffer");
    CHECK(address <= MGBA_LOG_BUFFER_ADDRESS + EXPANSION_LOG_MESSAGE_MAX,
          "payload stays within the 256-byte mGBA buffer");
    sBuffer[address - MGBA_LOG_BUFFER_ADDRESS] = value;
    sBufferWrites++;
}

void ExpansionLog_TestResetBackend(void);

static void ResetTransport(u16 handshake)
{
    sHandshakeValue = handshake;
    sEnableWrites = 0;
    sSendWrites = 0;
    sLastSend = 0;
    sBufferWrites = 0;
    memset(sBuffer, 0xA5, sizeof(sBuffer));
    ExpansionLog_TestResetBackend();
}

int main(void)
{
    char oversized[EXPANSION_LOG_MESSAGE_MAX + 2];
    int index;
    int level;

    ResetTransport(0);
    CHECK(ExpansionLog_Init() == EXPANSION_LOG_UNAVAILABLE,
          "failed handshake reports unavailable");
    CHECK(ExpansionLog_Write(EXPANSION_LOG_INFO, "must not send")
              == EXPANSION_LOG_UNAVAILABLE,
          "failed handshake does not pretend to log");
    CHECK(sEnableWrites == 1, "failed handshake is cached");
    CHECK(sSendWrites == 0, "failed handshake never writes the send register");
    CHECK(sBufferWrites == 0, "failed handshake never writes a payload");

    ResetTransport(0x1DEA);
    for (level = EXPANSION_LOG_FATAL; level < EXPANSION_LOG_LEVEL_COUNT; level++)
    {
        CHECK(ExpansionLog_Write((enum ExpansionLogLevel)level, "level")
                  == EXPANSION_LOG_SENT,
              "every supported severity sends successfully");
        CHECK(sLastSend == (u16)(0x100 | level),
              "every severity uses mGBA's typed send encoding");
    }
    CHECK(sEnableWrites == 1, "successful handshake remains cached across severities");
    CHECK(sSendWrites == EXPANSION_LOG_LEVEL_COUNT,
          "every supported severity sends exactly once");

    ResetTransport(0x1DEA);
    CHECK(ExpansionLog_Printf(EXPANSION_LOG_INFO, "ready %u %s", 7u, "ok")
              == EXPANSION_LOG_SENT,
          "available transport logs a supported format");
    CHECK(sEnableWrites == 1, "successful handshake happens once");
    CHECK(sSendWrites == 1 && sLastSend == 0x103,
          "info level uses mGBA's typed send encoding");
    CHECK(strcmp((char*)sBuffer, "ready 7 ok") == 0, "formatted payload is exact");
    CHECK(sBufferWrites == (int)strlen("ready 7 ok") + 1,
          "payload includes exactly one terminator");

    for (index = 0; index < EXPANSION_LOG_MESSAGE_MAX + 1; index++)
        oversized[index] = 'x';
    oversized[EXPANSION_LOG_MESSAGE_MAX + 1] = '\0';
    CHECK(ExpansionLog_Write(EXPANSION_LOG_DEBUG, oversized) == EXPANSION_LOG_TRUNCATED,
          "oversized payload reports truncation");
    CHECK(sSendWrites == 2 && sLastSend == 0x104,
          "debug level remains typed after truncation");
    CHECK(sBuffer[EXPANSION_LOG_MESSAGE_MAX] == '\0',
          "oversized payload is terminated at the protocol boundary");

    CHECK(ExpansionLog_Printf(EXPANSION_LOG_INFO, "bad %q")
              == EXPANSION_LOG_FORMAT_ERROR,
          "unsupported format returns an explicit error");
    CHECK(sSendWrites == 2, "format failure does not send a partial payload");
    CHECK(ExpansionLog_Write(EXPANSION_LOG_LEVEL_COUNT, "bad")
              == EXPANSION_LOG_INVALID_ARGUMENT,
          "invalid severity returns an explicit error");

    printf("EXPANSION_LOG_HOST_TEST: PASS\n");
    return 0;
}
