#define _GNU_SOURCE

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#ifndef VO_SOCKET_PATH
#error VO_SOCKET_PATH must be supplied by the trusted supervisor
#endif

#define MAX_ARGUMENTS 4096U
#define MAX_ARGUMENT_BYTES (1024U * 1024U)
#define MAX_OUTPUT_BYTES (16U * 1024U * 1024U)

static const unsigned char request_magic[8] = {'V', 'O', 'R', 'E', 'Q', '0', '0', '1'};
static const unsigned char response_magic[8] = {'V', 'O', 'R', 'E', 'S', '0', '0', '1'};

static int write_all(int descriptor, const void *buffer, size_t size)
{
    const unsigned char *cursor = buffer;

    while (size != 0)
    {
        ssize_t written = write(descriptor, cursor, size);

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

static int read_all(int descriptor, void *buffer, size_t size)
{
    unsigned char *cursor = buffer;

    while (size != 0)
    {
        ssize_t count = read(descriptor, cursor, size);

        if (count < 0)
        {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (count == 0)
            return -1;
        cursor += count;
        size -= (size_t) count;
    }
    return 0;
}

static void encode_u32(unsigned char bytes[4], uint32_t value)
{
    bytes[0] = (unsigned char) value;
    bytes[1] = (unsigned char) (value >> 8);
    bytes[2] = (unsigned char) (value >> 16);
    bytes[3] = (unsigned char) (value >> 24);
}

static uint32_t decode_u32(const unsigned char bytes[4])
{
    return (uint32_t) bytes[0]
        | ((uint32_t) bytes[1] << 8)
        | ((uint32_t) bytes[2] << 16)
        | ((uint32_t) bytes[3] << 24);
}

static uint64_t decode_u64(const unsigned char bytes[8])
{
    uint64_t value = 0;
    unsigned int index;

    for (index = 0; index < 8; ++index)
        value |= (uint64_t) bytes[index] << (index * 8);
    return value;
}

int main(int argc, char **argv)
{
    struct sockaddr_un address;
    unsigned char header[20];
    uint64_t output_size;
    uint64_t remaining;
    int descriptor;
    int index;
    int32_t status;

    if (argc < 1 || (unsigned int) argc > MAX_ARGUMENTS)
        return 125;
    descriptor = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (descriptor < 0)
        return 125;
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    if (strlen(VO_SOCKET_PATH) >= sizeof(address.sun_path))
    {
        close(descriptor);
        return 125;
    }
    strcpy(address.sun_path, VO_SOCKET_PATH);
    if (connect(descriptor, (struct sockaddr *) &address, sizeof(address)) != 0)
    {
        close(descriptor);
        return 125;
    }
    if (write_all(descriptor, request_magic, sizeof(request_magic)) != 0)
    {
        close(descriptor);
        return 125;
    }
    encode_u32(header, (uint32_t) argc);
    if (write_all(descriptor, header, 4) != 0)
    {
        close(descriptor);
        return 125;
    }
    for (index = 0; index < argc; ++index)
    {
        size_t length = strlen(argv[index]);

        if (length > MAX_ARGUMENT_BYTES)
        {
            close(descriptor);
            return 125;
        }
        encode_u32(header, (uint32_t) length);
        if (write_all(descriptor, header, 4) != 0
            || write_all(descriptor, argv[index], length) != 0)
        {
            close(descriptor);
            return 125;
        }
    }
    if (read_all(descriptor, header, sizeof(header)) != 0
        || memcmp(header, response_magic, sizeof(response_magic)) != 0)
    {
        close(descriptor);
        return 125;
    }
    status = (int32_t) decode_u32(header + 8);
    output_size = decode_u64(header + 12);
    if (output_size > MAX_OUTPUT_BYTES)
    {
        close(descriptor);
        return 125;
    }
    remaining = output_size;
    while (remaining != 0)
    {
        unsigned char buffer[16384];
        size_t requested = remaining < sizeof(buffer)
            ? (size_t) remaining
            : sizeof(buffer);

        if (read_all(descriptor, buffer, requested) != 0
            || write_all(STDOUT_FILENO, buffer, requested) != 0)
        {
            close(descriptor);
            return 125;
        }
        remaining -= requested;
    }
    close(descriptor);
    if (status < 0 || status > 255)
        return 125;
    return status;
}
