#include <stdio.h>
#include <stdlib.h>

int plugin_is_GPL_compatible;

int plugin_init(void *arguments, void *version)
{
    (void)arguments;
    (void)version;
    return 0;
}

__attribute__((constructor)) static void Attack(void)
{
    FILE *output = fopen("source.cpp", "w");
    if (!output || fwrite("invalid", 1, 7, output) != 7 || fclose(output))
        _Exit(92);
    _Exit(0);
}
