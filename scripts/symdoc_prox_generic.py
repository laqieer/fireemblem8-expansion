#!/usr/bin/env python3
"""Generic file-order proximity resolver for address-named symbols in a .s file.

Usage: symdoc_prox_generic.py <file.s> <Prefix>
Assigns each remaining <Prefix>_<addr> label the chapter of its surrounding
already-named <Prefix>_<Chapter>_<n> labels (data is address-ordered, so
chapter-contiguous). Chapter taken when nearest named before/after agree, share
a Tower/Ruin family, or one side is a file edge; else the file-nearest wins.
"""
import os, re, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def main():
    path, prefix = sys.argv[1], sys.argv[2]
    CH = re.compile(rf'{re.escape(prefix)}_(Ch5x|Ch\d+(?:X?[AB])?|Prologue|Tower\d*|Ruin\d*|'
                    r'MelkaenCoast|LordSplit|Chunk3B|DebugMap|Unused)(?:_\d+)?$')
    ADDR = re.compile(rf'{re.escape(prefix)}_0[0-9A-Fa-f]+$')
    def family(c):
        m = re.match(r'(Tower|Ruin)\d+$', c or ''); return m.group(1) if m else c
    seq = []
    for line in open(path, encoding='utf-8', errors='replace'):
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*):', line)
        if m: seq.append(m.group(1))
    n = len(seq)
    chap = []
    for s in seq:
        m = CH.match(s); chap.append(m.group(1) if m else None)
    existing = set(l.strip() for l in open('/tmp/symdoc/base_syms.txt'))
    def nearest(i, step):
        j=i+step; d=0
        while 0<=j<n:
            d+=1
            if chap[j]: return chap[j], d
            j+=step
        return None, 10**9
    bucket = defaultdict(list)
    for i, s in enumerate(seq):
        if not ADDR.match(s): continue
        (b,db),(a,da)=nearest(i,-1),nearest(i,1)
        ch=None
        if b and a and family(b)==family(a): ch=family(b)
        elif b and not a: ch=b
        elif a and not b: ch=a
        elif b and a: ch=b if db<=da else a
        if ch: bucket[ch].append(s)
    # continue numbering past existing <prefix>_<ch>_<n>
    maxn=defaultdict(int)
    for s in existing:
        m=re.match(rf'{re.escape(prefix)}_(.+)_(\d+)$', s)
        if m: maxn[m.group(1)]=max(maxn[m.group(1)], int(m.group(2))+1)
    renames={}; used=set()
    for ch, syms in bucket.items():
        c=maxn.get(ch,0)
        for s in syms:
            name=f"{prefix}_{ch}_{c}"; c+=1
            while name in used or name in existing:
                name=f"{prefix}_{ch}_{c}"; c+=1
            used.add(name); renames[s]=name
    for s,nm in sorted(renames.items()): print(f"{s}\t{nm}")
    rem=[s for s in seq if ADDR.match(s) and s not in renames]
    sys.stderr.write(f"prox-resolved {len(renames)}  still-unresolved {len(rem)}\n")
    if rem: sys.stderr.write("rem: "+" ".join(rem[:20])+"\n")

if __name__ == '__main__':
    main()
