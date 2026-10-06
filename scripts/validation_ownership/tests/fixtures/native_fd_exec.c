#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    char number[16], data[16];
    const char *kind;
    int descriptor, result, pair[2], status;
    pid_t child;
    ssize_t size;

    if (argc < 2)
        return 2;
    if (!strcmp(argv[1], "after"))
    {
        if (argc != 4)
            return 3;
        kind = argv[2];
        descriptor = atoi(argv[3]);
        if (!strcmp(kind, "reuse"))
        {
            result = open("second", O_RDONLY);
            if (result != descriptor)
                return 4;
        }
        if (!strcmp(kind, "directory"))
        {
            descriptor = openat(descriptor, "input", O_RDONLY);
            if (descriptor < 0)
                return 5;
        }
        size = read(descriptor, data, sizeof(data));
        if (!strcmp(kind, "closed") ||
            (strstr(kind, "cloexec") && !strstr(kind, "clear")))
            return size == -1 && errno == EBADF ? 0 : 6;
        if (size <= 0 || write(1, data, size) != size)
            return 7;
        return 0;
    }
    kind = argv[1];
    if (!strcmp(kind, "pipe"))
    {
        if (pipe(pair) || write(pair[1], "pipe", 4) != 4 || close(pair[1]))
            return 8;
        descriptor = pair[0];
    }
    else
    {
        descriptor = open(!strcmp(kind, "directory") ? "data" : "input",
                          O_RDONLY | (!strcmp(kind, "cloexec") || !strcmp(kind, "reuse") ||
                                      !strcmp(kind, "cloexec-clear") ||
                                      !strcmp(kind, "ioctl-cloexec-clear") ? O_CLOEXEC : 0));
        if (descriptor < 0)
            return 9;
    }
    if (!strcmp(kind, "dup"))
    {
        result = dup2(descriptor, 7);
        if (result != 7 || close(descriptor))
            return 10;
        descriptor = result;
    }
    if (!strcmp(kind, "fcntl") || !strcmp(kind, "dup-cloexec") ||
        !strcmp(kind, "dup-cloexec-clear"))
    {
        result = fcntl(descriptor, !strcmp(kind, "fcntl") ? F_DUPFD : F_DUPFD_CLOEXEC, 9);
        if (result < 0 || close(descriptor))
            return 11;
        descriptor = result;
    }
    if (!strcmp(kind, "stdin-cloexec"))
    {
        if (dup2(descriptor, 0) != 0 || close(descriptor) || fcntl(0, F_SETFD, FD_CLOEXEC))
            return 12;
        descriptor = 0;
    }
    if ((!strcmp(kind, "set-cloexec") && fcntl(descriptor, F_SETFD, FD_CLOEXEC)) ||
        (!strcmp(kind, "ioctl-cloexec") && ioctl(descriptor, FIOCLEX)) ||
        ((!strcmp(kind, "cloexec-clear") || !strcmp(kind, "dup-cloexec-clear")) &&
         fcntl(descriptor, F_SETFD, 0)) ||
        (!strcmp(kind, "ioctl-cloexec-clear") && ioctl(descriptor, FIONCLEX)))
        return 18;
    if (!strcmp(kind, "closed") && close(descriptor))
        return 13;
    if (!strcmp(kind, "unknown"))
        descriptor = 120;
    if (!strcmp(kind, "fork"))
    {
        child = fork();
        if (child < 0)
            return 14;
        if (child)
            return waitpid(child, &status, 0) == child && WIFEXITED(status) ?
                WEXITSTATUS(status) : 15;
    }
    if (snprintf(number, sizeof(number), "%d", descriptor) < 0)
        return 16;
    execl(argv[0], argv[0], "after", kind, number, (char *) NULL);
    if (!strcmp(kind, "exec-failure") && errno == EFAULT)
    {
        size = read(descriptor, data, sizeof(data));
        return size > 0 && write(1, data, size) == size ? 0 : 19;
    }
    return 17;
}
