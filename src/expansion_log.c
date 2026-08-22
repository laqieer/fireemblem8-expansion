#include "global.h"

#include <stdarg.h>

#include "expansion_log.h"

#if FE8_EXPANSION_MODERN_BUILD && FE8_EXPANSION_LOGGING_ENABLED

#define MGBA_LOG_ENABLE_ADDRESS 0x04FFF780
#define MGBA_LOG_BUFFER_ADDRESS 0x04FFF600
#define MGBA_LOG_SEND_ADDRESS 0x04FFF700
#define MGBA_LOG_ENABLE_MAGIC 0xC0DE
#define MGBA_LOG_READY_MAGIC 0x1DEA
#define MGBA_LOG_SEND_FLAG 0x100

static s8 sBackendAvailable = -1;

#ifdef EXPANSION_LOG_TEST_BACKEND
extern void ExpansionLog_TestWrite16(u32 address, u16 value);
extern u16 ExpansionLog_TestRead16(u32 address);
extern void ExpansionLog_TestWrite8(u32 address, u8 value);

void ExpansionLog_TestResetBackend(void)
{
    sBackendAvailable = -1;
}

static void Write16(u32 address, u16 value)
{
    ExpansionLog_TestWrite16(address, value);
}

static u16 Read16(u32 address)
{
    return ExpansionLog_TestRead16(address);
}

static void Write8(u32 address, u8 value)
{
    ExpansionLog_TestWrite8(address, value);
}
#else
static void Write16(u32 address, u16 value)
{
    *(volatile u16*)address = value;
}

static u16 Read16(u32 address)
{
    return *(volatile u16*)address;
}

static void Write8(u32 address, u8 value)
{
    *(volatile u8*)address = value;
}
#endif

static int AppendChar(char* buffer, int* length, char value)
{
    if (*length >= EXPANSION_LOG_MESSAGE_MAX)
        return 0;

    buffer[*length] = value;
    *length += 1;
    return 1;
}

static int AppendString(char* buffer, int* length, const char* value)
{
    int complete;

    if (value == NULL)
        value = "(null)";

    complete = 1;
    while (*value != '\0')
    {
        if (!AppendChar(buffer, length, *value))
        {
            complete = 0;
            break;
        }

        value++;
    }

    return complete;
}

static int AppendUnsigned(
    char* buffer, int* length, unsigned long value, unsigned base, int uppercase)
{
    char digits[sizeof(unsigned long) * 3];
    const char* alphabet;
    int digitCount;
    int complete;

    alphabet = uppercase ? "0123456789ABCDEF" : "0123456789abcdef";
    digitCount = 0;
    do
    {
        digits[digitCount++] = alphabet[value % base];
        value /= base;
    } while (value != 0 && digitCount < (int)sizeof(digits));

    complete = 1;
    while (digitCount != 0)
    {
        digitCount--;
        if (!AppendChar(buffer, length, digits[digitCount]))
            complete = 0;
    }

    return complete;
}

static enum ExpansionLogStatus FormatMessage(
    char* buffer, const char* format, va_list args)
{
    int length;
    int truncated;

    length = 0;
    truncated = 0;
    while (*format != '\0')
    {
        int complete;
        char specifier;

        if (*format != '%')
        {
            if (!AppendChar(buffer, &length, *format))
                truncated = 1;
            format++;
            continue;
        }

        format++;
        specifier = *format;
        if (specifier == '\0')
            return EXPANSION_LOG_FORMAT_ERROR;

        complete = 1;
        switch (specifier)
        {
        case '%':
            complete = AppendChar(buffer, &length, '%');
            break;

        case 'c':
            complete = AppendChar(buffer, &length, (char)va_arg(args, int));
            break;

        case 's':
            complete = AppendString(buffer, &length, va_arg(args, const char*));
            break;

        case 'd':
        case 'i':
        {
            int value;

            value = va_arg(args, int);
            if (value < 0)
            {
                complete = AppendChar(buffer, &length, '-');
                if (value == INT_MIN)
                    complete &= AppendUnsigned(
                        buffer, &length, (unsigned long)INT_MAX + 1UL, 10, 0);
                else
                    complete &= AppendUnsigned(buffer, &length, (unsigned long)-value, 10, 0);
            }
            else
            {
                complete = AppendUnsigned(buffer, &length, (unsigned long)value, 10, 0);
            }
            break;
        }

        case 'u':
            complete = AppendUnsigned(
                buffer, &length, (unsigned long)va_arg(args, unsigned int), 10, 0);
            break;

        case 'x':
        case 'X':
            complete = AppendUnsigned(
                buffer, &length, (unsigned long)va_arg(args, unsigned int), 16,
                specifier == 'X');
            break;

        case 'p':
            complete = AppendString(buffer, &length, "0x");
            complete &= AppendUnsigned(
                buffer, &length, (unsigned long)va_arg(args, void*), 16, 0);
            break;

        default:
            return EXPANSION_LOG_FORMAT_ERROR;
        }

        if (!complete)
            truncated = 1;
        format++;
    }

    buffer[length] = '\0';
    return truncated ? EXPANSION_LOG_TRUNCATED : EXPANSION_LOG_SENT;
}

enum ExpansionLogStatus ExpansionLog_Init(void)
{
    if (sBackendAvailable < 0)
    {
        Write16(MGBA_LOG_ENABLE_ADDRESS, MGBA_LOG_ENABLE_MAGIC);
        sBackendAvailable = Read16(MGBA_LOG_ENABLE_ADDRESS) == MGBA_LOG_READY_MAGIC;
    }

    return sBackendAvailable ? EXPANSION_LOG_AVAILABLE : EXPANSION_LOG_UNAVAILABLE;
}

enum ExpansionLogStatus ExpansionLog_Write(enum ExpansionLogLevel level, const char* message)
{
    char buffer[EXPANSION_LOG_MESSAGE_MAX + 1];
    int length;
    int index;
    enum ExpansionLogStatus status;

    if (level < EXPANSION_LOG_FATAL || level >= EXPANSION_LOG_LEVEL_COUNT || message == NULL)
        return EXPANSION_LOG_INVALID_ARGUMENT;

    status = ExpansionLog_Init();
    if (status != EXPANSION_LOG_AVAILABLE)
        return status;

    length = 0;
    while (message[length] != '\0' && length < EXPANSION_LOG_MESSAGE_MAX)
    {
        buffer[length] = message[length];
        length++;
    }
    buffer[length] = '\0';

    for (index = 0; index <= length; index++)
        Write8(MGBA_LOG_BUFFER_ADDRESS + index, (u8)buffer[index]);

    Write16(MGBA_LOG_SEND_ADDRESS, (u16)(MGBA_LOG_SEND_FLAG | level));
    return message[length] == '\0' ? EXPANSION_LOG_SENT : EXPANSION_LOG_TRUNCATED;
}

enum ExpansionLogStatus ExpansionLog_VPrintf(
    enum ExpansionLogLevel level, const char* format, va_list args)
{
    char buffer[EXPANSION_LOG_MESSAGE_MAX + 1];
    enum ExpansionLogStatus status;
    enum ExpansionLogStatus sendStatus;

    if (level < EXPANSION_LOG_FATAL || level >= EXPANSION_LOG_LEVEL_COUNT || format == NULL)
        return EXPANSION_LOG_INVALID_ARGUMENT;

    status = FormatMessage(buffer, format, args);
    if (status == EXPANSION_LOG_FORMAT_ERROR)
        return status;

    sendStatus = ExpansionLog_Write(level, buffer);
    if (sendStatus != EXPANSION_LOG_SENT)
        return sendStatus;

    return status;
}

enum ExpansionLogStatus ExpansionLog_Printf(
    enum ExpansionLogLevel level, const char* format, ...)
{
    va_list args;
    enum ExpansionLogStatus status;

    va_start(args, format);
    status = ExpansionLog_VPrintf(level, format, args);
    va_end(args);
    return status;
}

#endif
