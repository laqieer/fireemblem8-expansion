#ifndef GUARD_EXPANSION_LOG_H
#define GUARD_EXPANSION_LOG_H

/*
 * Bounded, mGBA-compatible diagnostic logging for modern debug ROMs.
 * Calls are main-thread only and must not run from an interrupt handler.
 */

#include <stdarg.h>

#include "expansion_config.h"

enum ExpansionLogLevel
{
    EXPANSION_LOG_FATAL = 0,
    EXPANSION_LOG_ERROR,
    EXPANSION_LOG_WARN,
    EXPANSION_LOG_INFO,
    EXPANSION_LOG_DEBUG,
    EXPANSION_LOG_LEVEL_COUNT
};

enum ExpansionLogStatus
{
    EXPANSION_LOG_DISABLED = 0,
    EXPANSION_LOG_UNAVAILABLE,
    EXPANSION_LOG_AVAILABLE,
    EXPANSION_LOG_SENT,
    EXPANSION_LOG_TRUNCATED,
    EXPANSION_LOG_INVALID_ARGUMENT,
    EXPANSION_LOG_FORMAT_ERROR
};

#define EXPANSION_LOG_MESSAGE_MAX 255

#if FE8_EXPANSION_MODERN_BUILD && FE8_EXPANSION_LOGGING_ENABLED

enum ExpansionLogStatus ExpansionLog_Init(void);
enum ExpansionLogStatus ExpansionLog_Write(enum ExpansionLogLevel level, const char* message);
enum ExpansionLogStatus ExpansionLog_VPrintf(
    enum ExpansionLogLevel level, const char* format, va_list args);
enum ExpansionLogStatus ExpansionLog_Printf(
    enum ExpansionLogLevel level, const char* format, ...);

#define EXPANSION_LOG_FATAL(...) \
    ExpansionLog_Printf(EXPANSION_LOG_FATAL, __VA_ARGS__)
#define EXPANSION_LOG_ERROR(...) \
    ExpansionLog_Printf(EXPANSION_LOG_ERROR, __VA_ARGS__)
#define EXPANSION_LOG_WARN(...) \
    ExpansionLog_Printf(EXPANSION_LOG_WARN, __VA_ARGS__)
#define EXPANSION_LOG_INFO(...) \
    ExpansionLog_Printf(EXPANSION_LOG_INFO, __VA_ARGS__)
#define EXPANSION_LOG_DEBUG(...) \
    ExpansionLog_Printf(EXPANSION_LOG_DEBUG, __VA_ARGS__)

#else

#define ExpansionLog_Init() EXPANSION_LOG_DISABLED
#define ExpansionLog_Write(level, message) EXPANSION_LOG_DISABLED
#define ExpansionLog_VPrintf(level, format, args) EXPANSION_LOG_DISABLED
#define ExpansionLog_Printf(...) EXPANSION_LOG_DISABLED

#define EXPANSION_LOG_FATAL(...) ((void)0)
#define EXPANSION_LOG_ERROR(...) ((void)0)
#define EXPANSION_LOG_WARN(...) ((void)0)
#define EXPANSION_LOG_INFO(...) ((void)0)
#define EXPANSION_LOG_DEBUG(...) ((void)0)

#endif

#endif /* GUARD_EXPANSION_LOG_H */
