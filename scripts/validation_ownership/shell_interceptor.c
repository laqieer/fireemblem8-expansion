#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define EVENT_FD 3
#define MAPPING_FD 4
#define DOMAIN_FD 5
#define MAX_MAPPING_FILE_BYTES (1024 * 1024)

static int write_all(int fd, const void *buffer, size_t size)
{
    const unsigned char *cursor = buffer;

    while (size != 0)
    {
        ssize_t written = write(fd, cursor, size);

        if (written < 0)
        {
            if (errno == EINTR)
                continue;
            return -1;
        }
        cursor += written;
        size -= (size_t) written;
    }
    return 0;
}

static int write_u32(int fd, uint32_t value)
{
    unsigned char bytes[4] = {
        (unsigned char) value,
        (unsigned char) (value >> 8),
        (unsigned char) (value >> 16),
        (unsigned char) (value >> 24),
    };

    return write_all(fd, bytes, sizeof(bytes));
}

static unsigned char *read_file_at(int directory, const char *path, size_t *size)
{
    struct stat status;
    unsigned char *data;
    size_t offset = 0;
    int fd;

    fd = openat(directory, path, O_RDONLY | O_NOFOLLOW);
    if (fd < 0 || fstat(fd, &status) != 0 || status.st_size < 0)
    {
        if (fd >= 0)
            close(fd);
        return NULL;
    }
    if ((uint64_t) status.st_size > MAX_MAPPING_FILE_BYTES)
    {
        close(fd);
        return NULL;
    }
    data = malloc((size_t) status.st_size + 1);
    if (data == NULL)
    {
        close(fd);
        return NULL;
    }
    while (offset < (size_t) status.st_size)
    {
        ssize_t count = read(fd, data + offset, (size_t) status.st_size - offset);

        if (count < 0)
        {
            if (errno == EINTR)
                continue;
            free(data);
            close(fd);
            return NULL;
        }
        if (count == 0)
            break;
        offset += (size_t) count;
    }
    close(fd);
    data[offset] = '\0';
    *size = offset;
    return data;
}

static int log_domains(void)
{
    const char *count_text = getenv("VO_DOMAIN_COUNT");
    char *end;
    unsigned long count;
    unsigned long index;

    if (count_text == NULL || *count_text == '\0')
        return -1;
    errno = 0;
    count = strtoul(count_text, &end, 10);
    if (errno != 0 || *end != '\0' || count > UINT32_MAX)
        return -1;
    if (write_u32(DOMAIN_FD, (uint32_t) count) != 0)
        return -1;
    for (index = 0; index < count; ++index)
    {
        char name[64];
        const char *value;
        size_t length;

        if (snprintf(name, sizeof(name), "VO_DOMAIN_%lu", index)
            >= (int) sizeof(name))
        {
            return -1;
        }
        value = getenv(name);
        if (value == NULL)
            return -1;
        length = strlen(value);
        if (length > UINT32_MAX
            || write_u32(DOMAIN_FD, (uint32_t) length) != 0
            || write_all(DOMAIN_FD, value, length) != 0)
        {
            return -1;
        }
    }
    return 0;
}

static int has_domain_control(void)
{
    struct stat status;

    return fstat(DOMAIN_FD, &status) == 0 && S_ISFIFO(status.st_mode);
}

static int log_event(
    int argc,
    char **argv,
    int32_t match,
    uint32_t mapping_count,
    uint64_t command_hash
)
{
    int index;

    if (write_u32(EVENT_FD, (uint32_t) match) != 0
        || write_u32(EVENT_FD, mapping_count) != 0
        || write_u32(EVENT_FD, (uint32_t) command_hash) != 0
        || write_u32(EVENT_FD, (uint32_t) (command_hash >> 32)) != 0
        || write_u32(EVENT_FD, (uint32_t) argc) != 0)
    {
        return -1;
    }
    for (index = 0; index < argc; ++index)
    {
        size_t length = strlen(argv[index]);

        if (length > UINT32_MAX
            || write_u32(EVENT_FD, (uint32_t) length) != 0
            || write_all(EVENT_FD, argv[index], length) != 0)
        {
            return -1;
        }
    }
    return 0;
}

static const char *canonical_program(const char *program)
{
    if (strcmp(program, "/usr/bin/find") == 0)
        return "find";
    if (strcmp(program, "/usr/bin/printf") == 0)
        return "printf";
    if (strcmp(program, "/usr/bin/python3") == 0)
        return "python3";
    if (strcmp(program, "/usr/bin/uname") == 0)
        return "uname";
    if (strcmp(program, "/bin/vo-make") == 0)
        return "/usr/bin/make";
    return program;
}

static char *direct_command(int argc, char **argv)
{
    size_t size = strlen(canonical_program(argv[0])) + 1;
    char *result;
    int index;

    for (index = 1; index < argc; ++index)
        size += strlen(argv[index]) + 4;
    result = malloc(size);
    if (result == NULL)
        return NULL;
    strcpy(result, canonical_program(argv[0]));
    for (index = 1; index < argc; ++index)
    {
        strcat(result, " ");
        if (argv[index][0] == '\0')
            strcat(result, "\"\"");
        else
            strcat(result, argv[index]);
    }
    return result;
}

int main(int argc, char **argv)
{
    const char *count_text = getenv("VO_COMMAND_COUNT");
    char *count_end;
    const char *command = NULL;
    char *owned_command = NULL;
    unsigned long count = 0;
    int32_t match = -1;
    uint64_t command_hash = 0;

    if (argc >= 3 && strcmp(argv[argc - 2], "-c") == 0)
        command = argv[argc - 1];
    else if (argc >= 1)
    {
        owned_command = direct_command(argc, argv);
        command = owned_command;
    }
    if (count_text != NULL)
    {
        errno = 0;
        count = strtoul(count_text, &count_end, 10);
        if (errno != 0 || *count_text == '\0' || *count_end != '\0'
            || count > UINT32_MAX)
        {
            free(owned_command);
            return 125;
        }
    }
    if (command != NULL
        && strcmp(command, "/usr/bin/vo-domain-observer") == 0
        && has_domain_control())
    {
        int result = log_domains();

        free(owned_command);
        return result == 0 ? 0 : 125;
    }
    if (command != NULL)
    {
        const unsigned char *cursor = (const unsigned char *) command;
        uint64_t hash = UINT64_C(14695981039346656037);
        char path[4096];
        unsigned char *candidate;
        size_t size;

        while (*cursor != '\0')
        {
            hash ^= *cursor++;
            hash *= UINT64_C(1099511628211);
        }
        command_hash = hash;
        if (snprintf(path, sizeof(path), "%016llx.cmd",
            (unsigned long long) hash) >= (int) sizeof(path))
            return 125;
        candidate = read_file_at(MAPPING_FD, path, &size);
        if (candidate != NULL)
        {
            if (size == strlen(command)
                && memcmp(candidate, command, size) == 0)
                match = 0;
            free(candidate);
        }
    }
    if (log_event(argc, argv, match, (uint32_t) count, command_hash) != 0)
        return 125;
    if (match >= 0)
    {
        char path[4096];
        unsigned char *output;
        size_t size;

        const unsigned char *cursor = (const unsigned char *) command;
        uint64_t hash = UINT64_C(14695981039346656037);

        while (*cursor != '\0')
        {
            hash ^= *cursor++;
            hash *= UINT64_C(1099511628211);
        }
        if (snprintf(path, sizeof(path), "%016llx.out",
            (unsigned long long) hash)
            >= (int) sizeof(path))
            return 125;
        output = read_file_at(MAPPING_FD, path, &size);
        if (output == NULL)
            return 125;
        if (write_all(STDOUT_FILENO, output, size) != 0)
        {
            free(output);
            return 125;
        }
        free(output);
    }
    free(owned_command);
    return 0;
}
