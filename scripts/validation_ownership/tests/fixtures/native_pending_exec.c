#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <signal.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static volatile sig_atomic_t received;

static void caught(int number)
{
    received = number;
}

static int receive(int number)
{
    struct sigaction action;
    sigset_t blocked;
    memset(&action, 0, sizeof(action));
    action.sa_handler = caught;
    if (sigemptyset(&action.sa_mask) || sigaction(number, &action, 0)
        || sigemptyset(&blocked) || sigaddset(&blocked, number)
        || sigprocmask(SIG_UNBLOCK, &blocked, 0))
        return 20;
    return received;
}

int main(int argc, char **argv)
{
    sigset_t blocked;
    int number = argc > 1 && !strcmp(argv[1], "pipe") ? SIGPIPE : SIGUSR1;
    if (argc > 1 && !strcmp(argv[1], "pending"))
        return receive(SIGUSR1) == SIGUSR1 ? 0 : 21;
    if (argc > 1 && !strcmp(argv[1], "pending-pipe"))
        return receive(SIGPIPE) == SIGPIPE ? 0 : 22;
    if (argc > 1 && !strcmp(argv[1], "empty"))
        return receive(SIGUSR1) == 0 ? 0 : 23;
    if (sigemptyset(&blocked) || sigaddset(&blocked, number)
        || sigprocmask(SIG_BLOCK, &blocked, 0))
        return 24;
    if (argc > 1 && !strcmp(argv[1], "ignored"))
    {
        struct sigaction action;
        memset(&action, 0, sizeof(action));
        action.sa_handler = SIG_IGN;
        if (sigaddset(&blocked, SIGUSR2) || sigprocmask(SIG_BLOCK, &blocked, 0)
            || kill(getpid(), SIGUSR2) || sigemptyset(&action.sa_mask)
            || sigaction(SIGUSR2, &action, 0))
            return 33;
    }
    if (number == SIGPIPE)
    {
        int descriptors[2];
        if (pipe(descriptors) || close(descriptors[0])
            || dup2(descriptors[1], 1) < 0 || close(descriptors[1]))
            return 25;
        if (write(1, "x", 1) != -1 || errno != EPIPE)
            return 26;
    }
    else if (argc > 1 && !strcmp(argv[1], "queued"))
    {
        union sigval value;
        value.sival_int = 7;
        if (sigqueue(getpid(), number, value))
            return 27;
    }
    else if (kill(getpid(), number))
        return 28;
    if (argc > 1 && !strcmp(argv[1], "fork"))
    {
        pid_t child = fork();
        int status = 0;
        if (child < 0)
            return 29;
        if (child)
        {
            if (receive(SIGUSR1) != SIGUSR1 || waitpid(child, &status, 0) != child)
                return 30;
            return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : 31;
        }
        execl(argv[0], argv[0], "empty", (char *)0);
    }
    else
        execl(argv[0], argv[0], number == SIGPIPE ? "pending-pipe" : "pending", (char *)0);
    return 32;
}
