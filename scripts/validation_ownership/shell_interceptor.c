#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "dispatch.h"

/* PR186's length-framed event and exact FNV-keyed mapping protocol is retained.
 * Channels are opened by this authenticated static executable only, never
 * inherited from GNU Make and never mounted in registered-command capsules. */
#define MAX_COMMAND (64U * 1024U)
#define MAX_OUTPUT (1024U * 1024U)

static int write_all(int fd, const void *buffer, size_t size)
{
    const unsigned char *cursor = buffer;
    while (size)
    {
        ssize_t count = write(fd, cursor, size);
        if (count < 0 && errno == EINTR)
            continue;
        if (count <= 0)
            return -1;
        cursor += count;
        size -= (size_t)count;
    }
    return 0;
}

static unsigned char *read_file_at(int directory, const char *name, size_t *size)
{
    struct stat status;
    unsigned char *result;
    size_t used = 0;
    int fd = openat(directory, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0)
        return NULL;
    if (fstat(fd, &status) || !S_ISREG(status.st_mode)
        || status.st_size < 0 || status.st_size > MAX_OUTPUT)
    {
        close(fd);
        return NULL;
    }
    result = malloc((size_t)status.st_size + 1);
    if (!result)
    {
        close(fd);
        return NULL;
    }
    while (used < (size_t)status.st_size)
    {
        ssize_t count = read(fd, result + used, (size_t)status.st_size - used);
        if (count < 0 && errno == EINTR)
            continue;
        if (count <= 0)
        {
            free(result);
            close(fd);
            return NULL;
        }
        used += (size_t)count;
    }
    close(fd);
    result[used] = 0;
    *size = used;
    return result;
}

static const char *canonical_program(const char *program)
{
    if (!strcmp(program, "/usr/bin/make"))
        return program;
    if (!strncmp(program, "/usr/bin/", 9))
        return program + 9;
    return program;
}

static void put_u32(unsigned char **cursor, uint32_t value)
{
    *(*cursor)++ = value;
    *(*cursor)++ = value >> 8;
    *(*cursor)++ = value >> 16;
    *(*cursor)++ = value >> 24;
}

static int append(char *command, size_t *used, const char *data, size_t size)
{
    if (size >= MAX_COMMAND - *used)
        return -1;
    memcpy(command + *used, data, size);
    *used += size;
    command[*used] = 0;
    return 0;
}

static int append_argument(char *command, size_t *used, const char *argument)
{
    const unsigned char *cursor = (const unsigned char *)argument;
    int safe = *cursor != 0;
    for (; *cursor; ++cursor)
    {
        unsigned char ch = *cursor;
        if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z')
            || (ch >= '0' && ch <= '9') || strchr("_@%+=:,./-", ch)))
            safe = 0;
    }
    if (!*argument)
        return append(command, used, "\"\"", 2);
    if (safe)
        return append(command, used, argument, strlen(argument));
    if (append(command, used, "'", 1))
        return -1;
    for (cursor = (const unsigned char *)argument; *cursor; ++cursor)
    {
        if (*cursor == '\'')
        {
            if (append(command, used, "'\"'\"'", 5))
                return -1;
        }
        else if (append(command, used, (const char *)cursor, 1))
            return -1;
    }
    return append(command, used, "'", 1);
}

int main(int argc, char **argv)
{
    char command[MAX_COMMAND];
    char path[64];
    unsigned char event[MAX_COMMAND];
    unsigned char *cursor = event;
    unsigned char *mapped;
    uint64_t hash = UINT64_C(14695981039346656037);
    uint32_t mapping_count;
    size_t size;
    size_t index;
    int match = -1;
    int mapping;
    int events;
    long kind = syscall(SYS_getpid, VO_QUERY_KIND);
    const char *program = argv[0];
    int shell = !strcmp(program, "/bin/sh") || !strcmp(program, "/bin/bash");

    if (kind == VO_RECIPE)
        return 0;
    if (kind != VO_VALUE)
        return 125;
    if (argc < 1 || argc > 1024)
        return 125;
    if (shell)
    {
        if (argc != 3 || (strcmp(argv[1], "-c") && strcmp(argv[1], "-ec"))
            || strlen(argv[2]) >= sizeof(command))
            return 125;
        strcpy(command, argv[2]);
    }
    else
    {
        size_t used = 0;
        if (append_argument(command, &used, canonical_program(program)))
            return 125;
        for (index = 1; index < (size_t)argc; ++index)
        {
            if (append(command, &used, " ", 1) || append_argument(command, &used, argv[index]))
                return 125;
        }
    }
    for (index = 0; command[index]; ++index)
    {
        hash ^= (unsigned char)command[index];
        hash *= UINT64_C(1099511628211);
    }
    mapping = open("/control/map", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    events = open("/control/events", O_WRONLY | O_APPEND | O_NOFOLLOW | O_CLOEXEC);
    if (mapping < 0 || events < 0)
        return 125;
    mapped = read_file_at(mapping, "count", &size);
    if (!mapped || size != 4)
        return 125;
    mapping_count = (uint32_t)mapped[0] | (uint32_t)mapped[1] << 8
        | (uint32_t)mapped[2] << 16 | (uint32_t)mapped[3] << 24;
    free(mapped);
    snprintf(path, sizeof(path), "%016llx.cmd", (unsigned long long)hash);
    mapped = read_file_at(mapping, path, &size);
    if (mapped)
    {
        if (size == strlen(command) && !memcmp(mapped, command, size))
            match = 0;
        free(mapped);
    }
    put_u32(&cursor, (uint32_t)match);
    put_u32(&cursor, mapping_count);
    put_u32(&cursor, (uint32_t)hash);
    put_u32(&cursor, (uint32_t)(hash >> 32));
    put_u32(&cursor, (uint32_t)argc);
    for (index = 0; index < (size_t)argc; ++index)
    {
        size_t length = strlen(argv[index]);
        if (length + 4 > sizeof(event) - (size_t)(cursor - event))
            return 125;
        put_u32(&cursor, (uint32_t)length);
        memcpy(cursor, argv[index], length);
        cursor += length;
    }
    /* One append prevents cross-helper frame interleaving. */
    if (write(events, event, (size_t)(cursor - event)) != cursor - event)
        return 125;
    close(events);
    if (match == 0)
    {
        snprintf(path, sizeof(path), "%016llx.out", (unsigned long long)hash);
        mapped = read_file_at(mapping, path, &size);
        if (!mapped || write_all(STDOUT_FILENO, mapped, size))
            return 125;
        free(mapped);
    }
    close(mapping);
    return 0;
}
