#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static volatile sig_atomic_t deliveries, received_signal, received_code, sender;

static void receive(int number, siginfo_t *information, void *context)
{
    (void) context;
    deliveries++;
    received_signal = number;
    received_code = information->si_code;
    sender = information->si_pid;
}

static long send_self(int number, unsigned long value, unsigned long marker)
{
    siginfo_t information;
    unsigned long target = (unsigned long) getpid() | marker;
    memset(&information, 0, sizeof(information));
    information.si_signo = (int) value;
    information.si_code = SI_QUEUE;
    information.si_pid = getpid();
    information.si_uid = getuid();
    if (number == 129)
        return syscall(number, target, value, &information);
    if (number == 234)
        return syscall(number, target, target, value);
    if (number == 297)
        return syscall(number, target, target, value, &information);
    return syscall(number, target, value);
}

int main(int argc, char **argv)
{
    struct sigaction action;
    sigset_t blocked, previous;
    unsigned long marker, value;
    long first = 0, result;
    int number, first_error = 0;
    const char *mode;
    if (argc != 4)
        return 2;
    number = atoi(argv[1]);
    mode = argv[2];
    marker = !strcmp(argv[3], "ones") ? 0xffffffffUL << 32 : 1UL << 32;
    if (!strcmp(mode, "fatal"))
    {
        send_self(number, marker | SIGKILL, marker);
        return 3;
    }
    memset(&action, 0, sizeof(action));
    action.sa_sigaction = receive;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGUSR1);
    if (sigaction(SIGUSR1, &action, NULL) || sigprocmask(SIG_BLOCK, &blocked, &previous))
        return 4;
    if (!strcmp(mode, "zero") || !strcmp(mode, "negative") || !strcmp(mode, "large") ||
        !strcmp(mode, "prior"))
    {
        if (!strcmp(mode, "prior") && send_self(number, marker | SIGUSR1, marker))
            return 5;
        value = !strcmp(mode, "zero") ? 0 : !strcmp(mode, "negative") ? 0xffffffffUL : 65;
        first = send_self(number, marker | value, marker);
        first_error = errno;
        if (!strcmp(mode, "zero") ? first != 0 : first != -1 || first_error != EINVAL)
            return 6;
    }
    result = !strcmp(mode, "prior") ? 0 : send_self(number, marker | SIGUSR1, marker);
    if (result || sigprocmask(SIG_SETMASK, &previous, NULL))
        return 7;
    if (deliveries != 1 || received_signal != SIGUSR1 || sender != getpid())
        return 8;
    printf("{\"number\":%d,\"count\":%d,\"signal\":%d,\"code\":%d,"
           "\"sender\":%d,\"pid\":%d,\"first_result\":%ld,\"first_error\":%d,"
           "\"send_result\":%ld}\n",
           number, (int) deliveries, (int) received_signal, (int) received_code,
           (int) sender, (int) getpid(), first, first_error, result);
    return 0;
}
