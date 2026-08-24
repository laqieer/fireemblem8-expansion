#include <stdio.h>
#include <string.h>

#include "global.h"

static int HexDigit(char value)
{
    if (value >= '0' && value <= '9')
        return value - '0';

    if (value >= 'a' && value <= 'f')
        return value - 'a' + 10;

    if (value >= 'A' && value <= 'F')
        return value - 'A' + 10;

    return -1;
}

int main(int argc, char **argv)
{
    u32 words[64];
    u8 *bytes = (u8 *)words;
    int length;
    int i;

    if (argc != 2)
        return 2;

    length = strlen(argv[1]);
    if ((length & 1) || length > sizeof(words) * 2)
        return 3;

    for (i = 0; i < length; i += 2)
    {
        int high = HexDigit(argv[1][i]);
        int low = HexDigit(argv[1][i + 1]);

        if (high < 0 || low < 0)
            return 4;

        bytes[i / 2] = (high << 4) | low;
    }

    printf("%08lx\n", (unsigned long)ComputeChecksum32(words, length / 2));
    return 0;
}
