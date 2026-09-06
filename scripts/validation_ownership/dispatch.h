#ifndef GUARD_OWNERSHIP_DISPATCH_H
#define GUARD_OWNERSHIP_DISPATCH_H

#include <stdint.h>

/* Private syscall protocol, authenticated by executable role/instruction IP. */
#define VO_READY UINT64_C(0x564f4d4b00000001)
#define VO_DISPATCH UINT64_C(0x564f4d4b00000002)
#define VO_QUERY_KIND UINT64_C(0x564f4d4b00000003)
#define VO_PUBLISH UINT64_C(0x564f4d4b00000004)
#define VO_RECIPE UINT64_C(0x564f4d4b00000011)
#define VO_VALUE UINT64_C(0x564f4d4b00000012)
#define VO_INTERCEPTOR "/control/interceptor"

#endif
