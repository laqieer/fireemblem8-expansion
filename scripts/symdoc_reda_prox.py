#!/usr/bin/env python3
"""Resolve orphan address-named REDA_<addr> arrays by file-order proximity.

REDAs are defined in one address-ordered block at the top of events_udefs.c,
interspersed with the already-named ones. An orphan REDA (not referenced by any
.redas field) takes the chapter of its surrounding named REDAs. REDAs carry no
allegiance, so the name is REDA_<Chapter>_<n> (no role) -- chapter assigned only
when the nearest named REDA before and after agree (or share a Tower/Ruin family
or one side is a file edge); otherwise the file-nearest block wins.
"""
import os, re, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UDEF_C = os.path.join(ROOT, 'src/events_udefs.c')

CH_RE = re.compile(r'REDA_(?:Event_)?(Ch5x|Ch\d+(?:X?[AB])?|Prologue|Tower\d*|Ruin\d*|'
                   r'MelkaenCoast|LordSplit|Chunk3B|Unused)(?:Ally|Enemy|NPC|Purple|Mixed|Units)?')

def chapter_of_name(sym):
    m = CH_RE.match(sym)
    return m.group(1) if m else None

def family(c):
    if not c: return None
    m = re.match(r'(Tower|Ruin)\d+$', c)
    return m.group(1) if m else c

def main():
    text = open(UDEF_C, encoding='utf-8', errors='replace').read()
    seq = [m.group(1) for m in re.finditer(r'struct REDA ([A-Za-z0-9_]+)\[\]', text)]
    n = len(seq)
    chap = [chapter_of_name(s) for s in seq]
    existing = set(l.strip() for l in open('/tmp/symdoc/base_syms.txt'))

    def nearest(idx, step):
        j = idx + step; d = 0
        while 0 <= j < n:
            d += 1
            if chap[j]:
                return chap[j], d
            j += step
        return None, 10**9

    bucket = defaultdict(list)
    for idx, sym in enumerate(seq):
        if not re.match(r'REDA_0[0-9A-Fa-f]+$', sym):
            continue
        (b, db), (a, da) = nearest(idx, -1), nearest(idx, +1)
        ch = None
        if b and a and family(b) == family(a):
            ch = family(b)
        elif b and not a:
            ch = b
        elif a and not b:
            ch = a
        elif b and a:
            ch = b if db <= da else a
        if ch:
            bucket[ch].append((idx, sym))

    existing_ctx_max = defaultdict(int)  # continue numbering past existing REDA_<ch>_<n>
    for s in existing:
        m = re.match(r'REDA_(Ch5x|Ch\d+(?:X?[AB])?|Prologue|Tower\d*|Ruin\d*|MelkaenCoast|LordSplit|Chunk3B|Unused)_(\d+)$', s)
        if m:
            existing_ctx_max[m.group(1)] = max(existing_ctx_max[m.group(1)], int(m.group(2)) + 1)

    renames = {}; used = set()
    for ch, items in bucket.items():
        c = existing_ctx_max.get(ch, 0)
        for idx, sym in sorted(items):
            name = f"REDA_{ch}_{c}"; c += 1
            while name in used or name in existing:
                name = f"REDA_{ch}_{c}"; c += 1
            used.add(name); renames[sym] = name
    for s, nm in sorted(renames.items()):
        print(f"{s}\t{nm}")
    sys.stderr.write(f"reda-proximity resolved {len(renames)}\n")

if __name__ == '__main__':
    main()
