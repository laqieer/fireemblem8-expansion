#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/mman.h>
#include <unistd.h>

static volatile sig_atomic_t received;
static volatile sig_atomic_t invalid;
static int expected_signal = SIGUSR1;
static int queued_signal = 1;

static void caught(int number, siginfo_t *info, void *context)
{
    const unsigned char *bytes = (const unsigned char *)info;
    int index;
    (void)context;
    if (number != expected_signal || info->si_pid != getpid()
        || (queued_signal && info->si_value.sival_int != 37))
        invalid = 1;
    for (index = 32; queued_signal && index < 48; index++)
        if (bytes[index] != index)
            invalid = 1;
    received++;
}

static int queued(int number, int thread)
{
    siginfo_t info;
    unsigned char *bytes = (unsigned char *)&info;
    int index;
    memset(&info, 0, sizeof(info));
    info.si_signo = number;
    info.si_code = SI_QUEUE;
    info.si_pid = getpid();
    info.si_uid = getuid();
    info.si_value.sival_int = 37;
    for (index = 32; index < 48; index++)
        bytes[index] = index;
    switch (thread)
    {
    case 0: index = syscall(SYS_rt_sigqueueinfo, getpid(), number, &info); break;
    case 1: index = syscall(SYS_rt_tgsigqueueinfo, getpid(), getpid(), number, &info); break;
    case 2: index = kill(getpid(), number); break;
    case 3: index = syscall(SYS_tkill, getpid(), number); break;
    case 4: index = syscall(SYS_tgkill, getpid(), getpid(), number); break;
    default: return 1;
    }
    return index || memcmp(bytes + 32, "\x20\x21\x22\x23\x24\x25\x26\x27"
                                     "\x28\x29\x2a\x2b\x2c\x2d\x2e\x2f", 16);
}

int main(int argc, char **argv)
{
    sigset_t mask;
    siginfo_t info;
    struct sigaction action;
    struct timespec zero = {0, 0};
    const char *mode = argc > 1 ? argv[1] : "positive";
    int thread = argc > 2 ? atoi(argv[2]) : 0;
    int index;
    queued_signal = thread < 2;
    expected_signal = argc > 3 ? SIGRTMIN : SIGUSR1;
    sigemptyset(&mask);
    sigaddset(&mask, expected_signal);
    if (sigprocmask(SIG_BLOCK, &mask, 0) || queued(expected_signal, thread))
        return 2;
    if (!strcmp(mode, "timeout"))
    {
        sigset_t other;
        sigemptyset(&other);
        sigaddset(&other, SIGUSR2);
        if (sigtimedwait(&other, &info, &zero) != -1 || errno != EAGAIN)
            return 3;
    }
    else if (!strcmp(mode, "fault"))
    {
        if (syscall(SYS_rt_sigtimedwait, 0, &info, &zero, 8) != -1 || errno != EFAULT)
            return 4;
    }
    else if (!strcmp(mode, "mixed"))
    {
        if (queued(expected_signal, !thread))
            return 12;
    }
    else if (!strcmp(mode, "partial"))
    {
        unsigned char *page = mmap(0, 8192, PROT_READ | PROT_WRITE,
                                  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        int prefix;
        if (page == MAP_FAILED || munmap(page + 4096, 4096))
            return 13;
        for (prefix = 0; prefix <= 17; prefix++)
        {
            int accessible = prefix == 17 ? 64 : 32 + prefix;
            unsigned char *output = page + 4096 - accessible;
            memset(output, 0xaa, accessible);
            if (syscall(SYS_rt_sigtimedwait, &mask, output, &zero, 8) != -1
                || errno != EFAULT)
                return 14;
            for (index = 32; index < accessible && index < 48; index++)
                if (output[index] != index)
                {
                    fprintf(stderr, "partial siginfo padding mismatch at %d\n", index);
                    return 15;
                }
            if (queued(expected_signal, thread))
                return 16;
        }
        if (munmap(page, 4096))
            return 17;
    }
    else if (!strcmp(mode, "output-fault") || !strncmp(mode, "foreign-wait", 12))
    {
        if (syscall(SYS_rt_sigtimedwait, &mask, (void *)1, &zero, 8) != -1 || errno != EFAULT)
            return 10;
        if (!strncmp(mode, "foreign-wait", 12))
            return sigwaitinfo(&mask, !strcmp(mode, "foreign-wait-info") ? &info : 0)
                != expected_signal;
        if (queued(expected_signal, thread))
            return 11;
    }
    else
    {
        if (sigwaitinfo(&mask, !strcmp(mode, "null") ? 0 : &info) != expected_signal)
            return 5;
        if (strcmp(mode, "null"))
        {
            if (info.si_pid != getpid() || (queued_signal && info.si_value.sival_int != 37))
                return 6;
            for (index = 32; queued_signal && index < 48; index++)
                if (((unsigned char *)&info)[index] != index)
                    return 7;
        }
        if (strcmp(mode, "foreign") && queued(expected_signal, thread))
            return 8;
    }
    memset(&action, 0, sizeof(action));
    action.sa_sigaction = caught;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    if (sigaction(expected_signal, &action, 0) || sigprocmask(SIG_UNBLOCK, &mask, 0))
        return 9;
    printf("received:%d invalid:%d\n", received, invalid);
    return received != (!strcmp(mode, "mixed") ? 2 : 1) || invalid;
}
