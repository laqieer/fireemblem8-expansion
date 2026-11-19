#ifndef GUARD_GRADO_GUIDE_FLAG_OWNERSHIP_H
#define GUARD_GRADO_GUIDE_FLAG_OWNERSHIP_H

#define GRADO_STATE_IS_PROGRESSIVE_GUIDE_FLAG(flag) \
    (((flag) >= 0xB4 && (flag) <= 0xEB) || ((flag) >= 0xF0 && (flag) <= 0x127))

#endif
