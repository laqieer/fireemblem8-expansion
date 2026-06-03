#!/usr/bin/env python3
"""Generic chapter-based namer for address-named event data symbols.

Usage: symdoc_events_generic.py <targets_file> <NewPrefix>
  targets_file: one OLD symbol name per line (e.g. EventScr_08591F64)
  NewPrefix:    base for new names (e.g. EventScr, TrapData, ShopList)

Maps each symbol to a chapter via the src/events/<chapter>-event*.h header that
references it, then names <NewPrefix>_<Chapter>_<n>. Symbols referenced from no
chapter header, or from several, are reported unresolved (handled separately).
"""
import os, re, sys, subprocess
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_NORM = {'prologue':'Prologue','ch5x':'Ch5x','MelkaenCoast':'MelkaenCoast',
           'chunk3B':'Chunk3B','lordsplit':'LordSplit','debugmap':'DebugMap',
           'messed':'Messed','unused':'Unused'}
def norm(tok):
    if tok in CH_NORM: return CH_NORM[tok]
    m = re.match(r'^ch(\d+)([abx]*)$', tok)
    if m: return 'Ch'+m.group(1)+m.group(2).upper()
    m = re.match(r'^(ruin|tower)(\d*)$', tok)
    if m: return m.group(1).capitalize()+m.group(2)
    tok=re.sub(r'[^A-Za-z0-9]+',' ',tok).title().replace(' ','')
    return tok

def family(c):
    m = re.match(r'(Tower|Ruin)\d+$', c or '')
    return m.group(1) if m else c

def chapter_of(sym):
    try:
        files = subprocess.check_output(['grep','-rlw',sym,os.path.join(ROOT,'src/events')],
                                        text=True, stderr=subprocess.DEVNULL).split()
    except subprocess.CalledProcessError:
        files = []
    toks = set()
    for f in files:
        m = re.match(r'^(.+?)-(eventscript|eventinfo|eventscr|eventudefs|wm|tutorials)\.h$', os.path.basename(f))
        if m: toks.add(m.group(1))
    toks = {t for t in toks if t not in ('common','messed')}
    if not toks: return None
    if len(toks) == 1: return norm(next(iter(toks)))
    fams = {family(norm(t)) for t in toks}
    if len(fams) == 1: return next(iter(fams))
    prim = [t for t in toks if re.match(r'^(ch\d+[abx]*|prologue)$', t)]
    if len(prim) == 1: return norm(prim[0])
    return None

def main():
    targets = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    prefix = sys.argv[2]
    existing = set(l.strip() for l in open('/tmp/symdoc/base_syms.txt'))
    bucket = defaultdict(list); unresolved = []
    for sym in targets:
        ch = chapter_of(sym)
        (bucket[ch] if ch else unresolved).append(sym)
    renames = {}; used = set()
    for ch, syms in bucket.items():
        if ch is None: continue
        c = 0
        for sym in sorted(syms):
            name = f"{prefix}_{ch}_{c}"; c += 1
            while name in used or name in existing:
                name = f"{prefix}_{ch}_{c}"; c += 1
            used.add(name); renames[sym] = name
    for s, n in sorted(renames.items()):
        print(f"{s}\t{n}")
    sys.stderr.write(f"resolved {len(renames)}  unresolved {len(unresolved)}\n")
    if unresolved:
        sys.stderr.write("unresolved: " + " ".join(unresolved[:30]) + "\n")

if __name__ == '__main__':
    main()
