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
    long result;
    memset(&information, 0, sizeof(information));
    information.si_signo = SIGKILL;
    information.si_code = SI_QUEUE;
    information.si_pid = pid;
    information.si_uid = getuid();
    if (number == 129)
        result = syscall(number, pid, SIGKILL, &information);
    else if (number == 234)
        result = syscall(number, pid, pid, SIGKILL);
    else if (number == 297)
        result = syscall(number, pid, pid, SIGKILL, &information);
    else
        result = syscall(number, pid, SIGKILL);
    return result ? 2 : 3;
}
