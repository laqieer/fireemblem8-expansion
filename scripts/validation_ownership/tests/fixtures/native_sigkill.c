#define _GNU_SOURCE
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    siginfo_t information;
    int number = argc > 1 ? atoi(argv[1]) : 62;
    long pid = getpid();
    unsigned long target_pid = pid, target_tid = pid;
    long result;
    if (argc > 2)
    {
        if (!strcmp(argv[2], "pid-high") || !strcmp(argv[2], "both-high"))
            target_pid |= 1UL << 32;
        if (!strcmp(argv[2], "tid-high") || !strcmp(argv[2], "both-high"))
            target_tid |= 1UL << 32;
        if (!strcmp(argv[2], "ones-high"))
        {
            target_pid |= 0xffffffffUL << 32;
            target_tid |= 0xffffffffUL << 32;
        }
    }
    memset(&information, 0, sizeof(information));
    information.si_signo = SIGKILL;
    information.si_code = SI_QUEUE;
    information.si_pid = pid;
    information.si_uid = getuid();
    if (number == 129)
        result = syscall(number, target_pid, SIGKILL, &information);
    else if (number == 234)
        result = syscall(number, target_pid, target_tid, SIGKILL);
    else if (number == 297)
        result = syscall(number, target_pid, target_tid, SIGKILL, &information);
    else
        result = syscall(number, target_pid, SIGKILL);
    return result ? 2 : 3;
}
