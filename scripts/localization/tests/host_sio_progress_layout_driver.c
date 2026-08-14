#include <stdio.h>

#include "global.h"
#include "sio.h"

struct LayoutCase
{
    int textWidth;
    int labelWidth;
    int suffixWidth;
    int expectedValueX;
};

int main(void)
{
    static const struct LayoutCase cases[] =
    {
        {80, 32, 6, 52},
        {80, 40, 6, 60},
        {80, 66, 6, 66},
        {64, 40, 6, 50},
        {96, 40, 6, 60},
    };
    int i;

    for (i = 0; i < (int)(sizeof(cases) / sizeof(cases[0])); ++i)
    {
        int actual = SioGetProgressValueX(
            cases[i].textWidth,
            cases[i].labelWidth,
            cases[i].suffixWidth);

        if (actual != cases[i].expectedValueX)
        {
            fprintf(
                stderr,
                "case %d: expected %d, got %d\n",
                i,
                cases[i].expectedValueX,
                actual);
            return 1;
        }
    }

    puts("SIO_PROGRESS_LAYOUT_HOST_TEST: PASS");
    return 0;
}
