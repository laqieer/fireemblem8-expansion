/*
 * Archival-only loop shapes retained for byte-identical agbcc output.
 * Supported modern builds use the NULL-safe loops at each call site.
 */
#define ARCHIVAL_END_PROC_BY_FIELD(field) \
    do \
    { \
        proc = Proc_FindNext(&procIter); \
        if (proc->field == index) \
        { \
            Proc_End(proc); \
            return; \
        } \
    } while (proc != NULL)

#define ARCHIVAL_PROC_EXISTS_BY_FIELD(field, found) \
    do \
    { \
        proc = Proc_FindNext(&procIter); \
        if (proc->field == index) \
        { \
            return found; \
        } \
    } while (proc != NULL)

#define ARCHIVAL_SET_PROC_FLAG_BY_FIELD(field) \
    do \
    { \
        proc = Proc_FindNext(&procIter); \
        if (index < 0) \
        { \
            proc->flag = 1; \
        } \
        else if (proc->field == index) \
        { \
            proc->flag = 1; \
            return; \
        } \
    } while (proc != NULL)
