#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

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

static unsigned char *read_file(const char *path, size_t *size)
{
    struct stat status;
    unsigned char *data;
    size_t offset = 0;
    int fd;

    fd = open(path, O_RDONLY);
    if (fd < 0 || fstat(fd, &status) != 0 || status.st_size < 0)
    {
        if (fd >= 0)
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

static int log_event(
    int argc,
    char **argv,
    int32_t match,
    uint32_t mapping_count,
    uint64_t command_hash
)
{
    const char *path = getenv("VO_EVENT_PATH");
    int fd;
    int index;

    if (path == NULL)
        return -1;
    fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd < 0)
        return -1;
    if (write_u32(fd, (uint32_t) match) != 0
        || write_u32(fd, mapping_count) != 0
        || write_u32(fd, (uint32_t) command_hash) != 0
        || write_u32(fd, (uint32_t) (command_hash >> 32)) != 0
        || write_u32(fd, (uint32_t) argc) != 0)
    {
        close(fd);
        return -1;
    }
    for (index = 0; index < argc; ++index)
    {
        size_t length = strlen(argv[index]);

        if (length > UINT32_MAX
            || write_u32(fd, (uint32_t) length) != 0
            || write_all(fd, argv[index], length) != 0)
        {
            close(fd);
            return -1;
        }
    }
    return close(fd);
}

int main(int argc, char **argv)
{
    const char *count_text = getenv("VO_COMMAND_COUNT");
    const char *map_dir = getenv("VO_MAP_DIR");
    const char *command = NULL;
    unsigned long count = 0;
    int32_t match = -1;
    uint64_t command_hash = 0;

    if (argc == 3 && strcmp(argv[1], "-c") == 0)
        command = argv[2];
    if (count_text != NULL)
        count = strtoul(count_text, NULL, 10);
    if (command != NULL && map_dir != NULL)
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
        if (snprintf(path, sizeof(path), "%s/%016llx.cmd", map_dir,
            (unsigned long long) hash) >= (int) sizeof(path))
            return 125;
        candidate = read_file(path, &size);
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
        if (snprintf(path, sizeof(path), "%s/%016llx.out", map_dir,
            (unsigned long long) hash)
            >= (int) sizeof(path))
            return 125;
        output = read_file(path, &size);
        if (output == NULL)
            return 125;
        if (write_all(STDOUT_FILENO, output, size) != 0)
        {
            free(output);
            return 125;
        }
        free(output);
    }
    return 0;
}
