#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

extern char **environ;

enum {
    EXIT_DEADLINE = 222,
    EXIT_PARENT = 223,
    EXIT_HARDENING = 224,
};

static int WatchProcessGroup(pid_t parent, pid_t process_group)
{
    struct pollfd input = {
        .fd = STDIN_FILENO,
        .events = POLLIN | POLLHUP | POLLERR,
    };

    for (;;) {
        char byte;
        int result;

        if (getppid() != parent)
            break;
        result = poll(&input, 1, 10);
        if (result < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        if (result == 0)
            continue;
        result = read(STDIN_FILENO, &byte, 1);
        if (result == 1 && byte == 'X')
            return 0;
        break;
    }
    if (kill(-process_group, SIGKILL) != 0 && errno != ESRCH)
        return EXIT_HARDENING;
    return 0;
}

static int64_t ParseInteger(const char *value)
{
    char *end = NULL;
    long long parsed;

    errno = 0;
    parsed = strtoll(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || parsed < 0)
        _exit(EXIT_HARDENING);
    return parsed;
}

static int DeadlineElapsed(
    int64_t wall_sec,
    int64_t wall_nsec,
    int64_t mono_sec,
    int64_t mono_nsec
) {
    struct timespec wall;
    struct timespec monotonic;

    if (
        clock_gettime(CLOCK_REALTIME, &wall) != 0
        || clock_gettime(CLOCK_MONOTONIC, &monotonic) != 0
    )
        _exit(EXIT_HARDENING);
    if (
        wall.tv_sec > wall_sec
        || (wall.tv_sec == wall_sec && wall.tv_nsec >= wall_nsec)
    )
        return 1;
    if (
        monotonic.tv_sec > mono_sec
        || (monotonic.tv_sec == mono_sec && monotonic.tv_nsec >= mono_nsec)
    )
        return 1;
    return 0;
}

int main(int argc, char **argv)
{
    pid_t parent;
    int64_t wall_sec;
    int64_t wall_nsec;
    int64_t mono_sec;
    int64_t mono_nsec;
    int64_t file_limit;
    int status_fd;
    const char *delay;
    struct rlimit limit;

    if (argc == 4 && strcmp(argv[1], "--watch") == 0)
        return WatchProcessGroup(
            (pid_t) ParseInteger(argv[2]),
            (pid_t) ParseInteger(argv[3])
        );
    if (argc < 10 || argv[8][0] != '-' || argv[8][1] != '-' || argv[8][2] != '\0')
        return EXIT_HARDENING;
    parent = (pid_t) ParseInteger(argv[1]);
    wall_sec = ParseInteger(argv[2]);
    wall_nsec = ParseInteger(argv[3]);
    mono_sec = ParseInteger(argv[4]);
    mono_nsec = ParseInteger(argv[5]);
    file_limit = ParseInteger(argv[6]);
    status_fd = (int) ParseInteger(argv[7]);
    if (wall_nsec >= 1000000000 || mono_nsec >= 1000000000)
        return EXIT_HARDENING;

    if (getppid() != parent)
        return EXIT_PARENT;
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent)
        return EXIT_PARENT;
    if (setsid() < 0)
        return EXIT_HARDENING;
    limit.rlim_cur = 0;
    limit.rlim_max = 0;
    if (setrlimit(RLIMIT_CORE, &limit) != 0)
        return EXIT_HARDENING;
    limit.rlim_cur = (rlim_t) file_limit;
    limit.rlim_max = (rlim_t) file_limit;
    if (setrlimit(RLIMIT_FSIZE, &limit) != 0)
        return EXIT_HARDENING;

    delay = getenv("WORKFLOW_PILOT_DEADLINE_EXEC_TEST_DELAY_NS");
    if (delay != NULL) {
        int64_t delay_ns = ParseInteger(delay);
        struct timespec requested = {
            .tv_sec = delay_ns / 1000000000,
            .tv_nsec = delay_ns % 1000000000,
        };
        while (nanosleep(&requested, &requested) != 0 && errno == EINTR)
            ;
    }
    if (getppid() != parent)
        return EXIT_PARENT;
    if (DeadlineElapsed(wall_sec, wall_nsec, mono_sec, mono_nsec))
        return EXIT_DEADLINE;
    if (
        fcntl(status_fd, F_SETFD, FD_CLOEXEC) != 0
        || write(status_fd, "E", 1) != 1
    )
        return EXIT_HARDENING;
    execve(argv[9], &argv[9], environ);
    return EXIT_HARDENING;
}
