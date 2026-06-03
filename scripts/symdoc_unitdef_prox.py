#!/usr/bin/env python3
"""Resolve remaining address-named UnitDef_<addr> arrays by file-order proximity.

events_udefs.c is laid out in ROM-address order, which is chapter-contiguous.
An orphan UnitDef (not referenced by any decompiled event) is assigned the
chapter of its surrounding named UnitDefs -- but ONLY when the nearest named
UnitDef before AND after it agree on the chapter, so we never guess across a
chapter boundary. Role comes from the unit allegiances.
"""
import os, re, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UDEF_C = os.path.join(ROOT, 'src/events_udefs.c')
ROLE = {'FACTION_ID_BLUE': 'Ally', 'FACTION_ID_RED': 'Enemy',
        'FACTION_ID_GREEN': 'NPC', 'FACTION_ID_PURPLE': 'Purple'}

def chapter_label(named_sym):
    """Extract the chapter portion from an already-documented UnitDef name."""
    m = re.match(r'UnitDef_(Ch\d+[ABX]*|Prologue|Ch5x|Ruin\d*|Tower\d*|'
                 r'MelkaenCoast|LordSplit|Chunk3B|Unused|DebugMap)'
                 r'(Ally|Enemy|NPC|Purple|Mixed|Units)', named_sym)
    return m.group(1) if m else None

def role_of(allegs):
    facs = set(allegs)
    if not facs: return 'Units'
    if len(facs) == 1: return ROLE.get(next(iter(facs)), 'Units')
    return 'Mixed'

def main():
    text = open(UDEF_C, encoding='utf-8', errors='replace').read()
    # ordered list of (symbol, body)
    seq = []
    for m in re.finditer(r'struct UnitDefinition ([A-Za-z0-9_]+)\[\]\s*=\s*\{', text):
        sym = m.group(1)
        i = m.end(); depth = 1
        while i < len(text) and depth:
            if text[i] == '{': depth += 1
            elif text[i] == '}': depth -= 1
            i += 1
        seq.append((sym, text[m.end():i]))

    n = len(seq)
    chap = [chapter_label(s) for s, _ in seq]
    existing = set(l.strip() for l in open('/tmp/symdoc/base_syms.txt'))

    def family(c):
        if not c: return None
        m = re.match(r'(Tower|Ruin)\d+$', c)
        return m.group(1) if m else c

    def nearest(idx, step):
        j = idx + step; dist = 0
        while 0 <= j < n:
            dist += 1
            if chap[j]:
                return chap[j], dist
            j += step
        return None, 10**9

    renames = {}; used = set(); bucket = defaultdict(list); ambiguous = []
    for idx, (sym, body) in enumerate(seq):
        if not re.match(r'UnitDef_0[0-9A-Fa-f]+$', sym):
            continue
        (before, db), (after, da) = nearest(idx, -1), nearest(idx, +1)
        # Confident when both sides agree (same chapter or same Tower/Ruin
        # family), or only one side exists (file edge, e.g. trailing Unused).
        # Otherwise fall back to the file-nearest named neighbor (data is
        # address-contiguous, so the closer block is the likely owner).
        ch = None
        if before and after and family(before) == family(after):
            ch = family(before)
        elif before and not after:
            ch = before
        elif after and not before:
            ch = after
        elif before and after:
            ch = before if db <= da else after   # nearest-block tiebreak
        if ch:
            role = role_of(re.findall(r'\.allegiance\s*=\s*(FACTION_ID_\w+)', body))
            bucket[(ch, role)].append(sym)
        else:
            ambiguous.append((sym, before, after))
    for (ch, role), syms in bucket.items():
        for sym in syms:
            base = f"UnitDef_{ch}{role}"; name = base; k = 0
            # continue numbering past existing same-context symbols
            while name in used or name in existing:
                k += 1; name = f"{base}_{k}"
            used.add(name); renames[sym] = name
    for s, nm in sorted(renames.items()):
        print(f"{s}\t{nm}")
    sys.stderr.write(f"proximity-resolved {len(renames)}  ambiguous(boundary) {len(ambiguous)}\n")
    for s, b, a in ambiguous[:40]:
        sys.stderr.write(f"  AMBIG {s} before={b} after={a}\n")

if __name__ == '__main__':
    main()
