/*
 * Native, output-independent observation of the admitted GNU Make 4.3 ABI.
 * The prefix layouts below describe the exported GNU Make 4.3 x86-64 ABI:
 * https://git.savannah.gnu.org/cgit/make.git/tree/src/filedef.h?h=4.3
 * https://git.savannah.gnu.org/cgit/make.git/tree/src/dep.h?h=4.3
 * https://git.savannah.gnu.org/cgit/make.git/tree/src/commands.h?h=4.3
 * No candidate-loadable functions are registered with GNU Make.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <spawn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "dispatch.h"

struct FileView;
struct DependencyView
{
    struct DependencyView *next;
    const char *name;
    struct FileView *file;
    const char *stem;
    uint32_t flags;
};

struct CommandsView
{
    const char *filename;
    unsigned long line;
    unsigned long offset;
    const char *text;
    char **lines;
    unsigned char *line_flags;
    unsigned short line_count;
    char recipe_prefix;
    unsigned int any_recurse:1;
};

struct FileView
{
    const char *name;
    const char *hash_name;
    const char *vpath;
    struct DependencyView *dependencies;
    struct CommandsView *commands;
    const char *stem;
    struct DependencyView *also_make;
    struct FileView *previous;
};

extern struct FileView *lookup_file(const char *);
extern char *gmk_expand(const char *);
extern char *allocated_variable_expand_for_file(const char *, struct FileView *);
extern void initialize_file_variables(struct FileView *, int);
extern void set_file_variables(struct FileView *);
extern void chop_commands(struct CommandsView *);
extern int rebuilding_makefiles;
extern char **environ;

#define MAX_NODES 4096
#define MAX_NAMES 512
#define MAX_RESULT (16U * 1024U * 1024U)

static char *target;
static char *names;
static char *parsed_names;
static char *name_list[MAX_NAMES];
static size_t name_count;
static unsigned char *result;
static size_t used;
static size_t capacity;
static int finishing;
static long make_pid;

/* The syscall supervisor authorizes control I/O only at this trusted code IP,
 * not at libc IPs reachable from Make's file/include/eval builtins. */
static long raw_call(long number, long a, long b, long c)
{
    long value;
    __asm__ volatile(
        "syscall" : "=a"(value) : "a"(number), "D"(a), "S"(b), "d"(c)
        : "rcx", "r11", "memory"
    );
    return value;
}

static _Noreturn void fail(void)
{
    raw_call(SYS_exit_group, 125, 0, 0);
    __builtin_unreachable();
}

__attribute__((constructor)) static void setup(void)
{
    const char *goal = getenv("VO_OBSERVE_TARGET");
    const char *variables = getenv("VO_OBSERVE_NAMES");
    const char *limit = getenv("VO_OBSERVE_BYTES");
    char *end = NULL;
    unsigned long bound;

    if (!goal || !variables || !limit)
        fail();
    bound = strtoul(limit, &end, 10);
    if (!end || *end || !bound || bound > MAX_RESULT)
        fail();
    capacity = bound;
    target = strdup(goal);
    names = strdup(variables);
    parsed_names = strdup(variables);
    result = malloc(capacity);
    if (!target || !names || !parsed_names || !result)
        fail();
    {
        char *state;
        char *name;
        for (name = strtok_r(parsed_names, " ", &state); name; name = strtok_r(NULL, " ", &state))
        {
            if (name_count == MAX_NAMES || strlen(name) > 128)
                fail();
            name_list[name_count++] = name;
        }
    }
    unsetenv("VO_OBSERVE_TARGET");
    unsetenv("VO_OBSERVE_NAMES");
    unsetenv("VO_OBSERVE_BYTES");
    unsetenv("LD_PRELOAD");
    make_pid = raw_call(SYS_getpid, VO_READY, 0, 0);
}

int execvp(const char *file, char *const argv[])
{
    char limit[32];
    long status;

    /* Preserve Make's own remake/restart argv and environment. Job children
     * cannot use this boundary as an alternate native execution route. */
    if (raw_call(SYS_getpid, 0, 0, 0) != make_pid || strcmp(file, "/usr/bin/make"))
        fail();
    snprintf(limit, sizeof(limit), "%zu", capacity);
    if (setenv("VO_OBSERVE_TARGET", target, 1) || setenv("VO_OBSERVE_NAMES", names, 1)
        || setenv("VO_OBSERVE_BYTES", limit, 1) || setenv("LD_PRELOAD", "/lib/vo-observer.so", 1))
        fail();
    status = raw_call(SYS_execve, (long)file, (long)argv, (long)environ);
    errno = (int)-status;
    return -1;
}

static int recursive_graph(void)
{
    struct FileView *nodes[MAX_NODES];
    size_t count = 1;
    size_t index;

    nodes[0] = lookup_file(target);
    if (!nodes[0] || rebuilding_makefiles)
        return 1;
    for (index = 0; index < count; ++index)
    {
        struct FileView *file = nodes[index];
        struct DependencyView *dependency;
        size_t links = 0;
        if (file->commands)
        {
            chop_commands(file->commands);
            if (file->commands->any_recurse)
                return 1;
        }
        for (dependency = file->dependencies; dependency; dependency = dependency->next)
        {
            size_t found;
            if (++links > MAX_NODES)
                fail();
            if (!dependency->file)
                continue;
            for (found = 0; found < count && nodes[found] != dependency->file; ++found) {}
            if (found == count)
            {
                if (count == MAX_NODES)
                    fail();
                nodes[count++] = dependency->file;
            }
        }
        if (file->previous)
        {
            size_t found;
            for (found = 0; found < count && nodes[found] != file->previous; ++found) {}
            if (found == count)
            {
                if (count == MAX_NODES)
                    fail();
                nodes[count++] = file->previous;
            }
        }
    }
    return 0;
}

int posix_spawn(pid_t *pid, const char *path, const posix_spawn_file_actions_t *actions,
                const posix_spawnattr_t *attributes, char *const argv[], char *const envp[])
{
    int status;
    static int (*spawn)(pid_t *, const char *, const posix_spawn_file_actions_t *,
                        const posix_spawnattr_t *, char *const [], char *const []);
    if (!spawn)
        spawn = dlsym(RTLD_NEXT, "posix_spawn");
    if (!spawn)
        fail();
    /* Redirect execution, never Make's visible variables, origins or flags.
     * The kernel supervisor authenticates this notification and the child's
     * stdout FD. Recursive/remake contexts conservatively require mappings. */
    raw_call(SYS_getpid, VO_DISPATCH, (long)path, recursive_graph());
    status = spawn(pid, VO_INTERCEPTOR, actions, attributes, argv, envp);
    raw_call(SYS_getpid, VO_DISPATCH, 0, 0);
    return status;
}

/* A noexec mount also rejects executable mappings of candidate files. This
 * guard additionally rejects load of already mapped/trusted host objects. */
void *dlopen(const char *filename, int flags)
{
    (void)filename;
    (void)flags;
    fail();
}

static void bytes(const void *data, size_t count)
{
    if (count > capacity - used)
        fail();
    memcpy(result + used, data, count);
    used += count;
}

static void number(uint32_t value)
{
    unsigned char data[4] = {value, value >> 8, value >> 16, value >> 24};
    bytes(data, sizeof(data));
}

static void string(const char *value)
{
    size_t count = value ? strnlen(value, capacity + 1) : 0;
    if (count > capacity || count > UINT32_MAX)
        fail();
    number((uint32_t)count);
    if (count)
        bytes(value, count);
}

static void expanded(struct FileView *file, const char *expression)
{
    char *value = allocated_variable_expand_for_file(expression, file);
    if (!value)
        fail();
    string(value);
    free(value);
}

static void variable(struct FileView *file, const char *name)
{
    char expression[512];
    const char *forms[] = {"", "origin ", "flavor "};
    size_t form;
    string(name);
    for (form = 0; form < 3; ++form)
    {
        char *value;
        strcpy(expression, "$(");
        strcat(expression, forms[form]);
        strcat(expression, name);
        strcat(expression, ")");
        value = file ? allocated_variable_expand_for_file(expression, file) : gmk_expand(expression);
        if (!value)
            fail();
        string(value);
        free(value);
    }
}

static void observe(void)
{
    struct FileView *nodes[MAX_NODES];
    size_t count = 1;
    size_t index;
    long descriptor;

    nodes[0] = lookup_file(target);
    if (!nodes[0])
        fail();
    for (index = 0; index < count; ++index)
    {
        struct DependencyView *dependency;
        struct FileView *previous = nodes[index]->previous;
        size_t links = 0;

        for (dependency = nodes[index]->dependencies; dependency; dependency = dependency->next)
        {
            size_t found;
            if (++links > MAX_NODES)
                fail();
            if (!dependency->file)
                continue;
            for (found = 0; found < count && nodes[found] != dependency->file; ++found) {}
            if (found == count)
            {
                if (count == MAX_NODES)
                    fail();
                nodes[count++] = dependency->file;
            }
        }
        if (previous)
        {
            size_t found;
            for (found = 0; found < count && nodes[found] != previous; ++found) {}
            if (found == count)
            {
                if (count == MAX_NODES)
                    fail();
                nodes[count++] = previous;
            }
        }
    }
    bytes("VOMAKE1\0", 8);
    number((uint32_t)count);
    for (index = 0; index < count; ++index)
    {
        struct FileView *file = nodes[index];
        struct DependencyView *dependency;
        uint32_t links = 0;
        size_t name;
        initialize_file_variables(file, 0);
        set_file_variables(file);
        string(file->name);
        string(file->commands ? file->commands->filename : "");
        string(file->commands ? file->commands->text : "");
        expanded(file, "$(SHELL)");
        expanded(file, "$(.SHELLFLAGS)");
        for (dependency = file->dependencies; dependency; dependency = dependency->next)
            if (++links > MAX_NODES)
                fail();
        number(links);
        for (dependency = file->dependencies; dependency; dependency = dependency->next)
        {
            string(dependency->name ? dependency->name : dependency->file->name);
            number((dependency->flags >> 9) & 1U);
        }
        number((uint32_t)name_count);
        for (name = 0; name < name_count; ++name)
            variable(file, name_list[name]);
    }
    number((uint32_t)name_count);
    for (index = 0; index < name_count; ++index)
        variable(NULL, name_list[index]);

    /* All candidate expansion is finished before a result FD exists. */
    descriptor = raw_call(
        SYS_open, (long)"/control/result", O_WRONLY | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0
    );
    if (descriptor < 0)
        fail();
    for (index = 0; index < used;)
    {
        long written = raw_call(SYS_write, descriptor, (long)(result + index), used - index);
        if (written <= 0)
            fail();
        index += (size_t)written;
    }
    if (raw_call(SYS_close, descriptor, 0, 0))
        fail();
}

_Noreturn void exit(int status)
{
    if (finishing)
        fail();
    finishing = 1;
    if (status == 0)
        observe();
    fflush(NULL);
    raw_call(SYS_exit_group, status, 0, 0);
    __builtin_unreachable();
}
