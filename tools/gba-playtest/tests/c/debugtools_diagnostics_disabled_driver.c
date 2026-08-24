#include <stdio.h>
#include <string.h>

#include "global.h"
#include "expansion_debugtools.h"

int main(void)
{
    struct DebugToolsDiagnosticsSnapshot snapshot;
    u8* bytes = (u8*)&snapshot;
    int i;

    memset(&snapshot, 0xA5, sizeof(snapshot));
    if (DebugTools_CaptureDiagnostics(NULL)
        != DEBUGTOOLS_ERR_INVALID_ARGUMENT)
        return 1;
    if (DebugTools_CaptureDiagnostics(&snapshot)
        != DEBUGTOOLS_ERR_DISABLED)
        return 2;

    for (i = 0; i < (int)sizeof(snapshot); ++i)
        if (bytes[i] != 0)
            return 3;

    printf("DEBUGTOOLS_DIAGNOSTICS_DISABLED_HOST_TEST: PASS\n");
    return 0;
}
