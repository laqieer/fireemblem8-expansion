#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static volatile sig_atomic_t received;

static void caught(int number)
{
    if (number == SIGRTMIN || number == SIGUSR1)
        received++;
}

static int receive(int number, int expected)
{
    sigset_t blocked;
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = caught;
    if (sigemptyset(&blocked) || sigaddset(&blocked, number)
        || sigemptyset(&action.sa_mask) || sigaction(number, &action, 0)
        || sigprocmask(SIG_UNBLOCK, &blocked, 0))
        return 3;
    return received == expected ? 0 : 4;
}

int main(int argc, char **argv)
{
    sigset_t blocked;
    union sigval value;
    const char *mode = argc > 1 ? argv[1] : "queued";
    int number = !strcmp(mode, "standard") ? SIGUSR1 : SIGRTMIN;
    int count = !strcmp(mode, "failed") ? 3 : 2;
    int index;
    if (argc > 2)
        return receive(number, number == SIGUSR1 ? 1 : 2);
    if (sigemptyset(&blocked) || sigaddset(&blocked, number)
        || sigprocmask(SIG_BLOCK, &blocked, 0))
        return 2;
    value.sival_int = 1;
    for (index = 0; index < count; index++)
    {
        int result = !strcmp(mode, "kill")
            ? kill(getpid(), number) : sigqueue(getpid(), number, value);
        if (result && !(index == 1 && !strcmp(mode, "failed") && errno == EFAULT))
            return 5;
    }
    if (!strcmp(mode, "direct"))
        return receive(number, 2);
    execl(argv[0], argv[0], mode, "receive", (char *)0);
    return 6;
}
