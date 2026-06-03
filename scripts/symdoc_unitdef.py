#!/usr/bin/env python3
"""Propose names for address-named UnitDef_<addr> arrays in events_udefs.c.

Follows the project's established convention UnitDef_<Chapter><Role> (cf.
UnitDef_Event_Ch2Ally). Chapter comes from the per-chapter event header that
references the symbol; Role comes from the unit allegiances in the array.
"""
import os, re, sys, subprocess
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UDEF_C = os.path.join(ROOT, 'src/events_udefs.c')

ROLE = {'FACTION_ID_BLUE': 'Ally', 'FACTION_ID_RED': 'Enemy',
        'FACTION_ID_GREEN': 'NPC', 'FACTION_ID_PURPLE': 'Purple'}

CH_NORM = {  # event-file token -> CamelCase chapter label
    'prologue': 'Prologue', 'ch5x': 'Ch5x', 'MelkaenCoast': 'MelkaenCoast',
    'chunk3B': 'Chunk3B', 'lordsplit': 'LordSplit', 'debugmap': 'DebugMap',
    'messed': 'Messed', 'unused': 'Unused',
}
def norm_chapter(tok):
    if tok in CH_NORM:
        return CH_NORM[tok]
    m = re.match(r'^ch(\d+)([abx]*)$', tok)
    if m:
        return 'Ch' + m.group(1) + m.group(2).upper()
    m = re.match(r'^(ruin|tower)(\d*)$', tok)
    if m:
        return m.group(1).capitalize() + m.group(2)
    return tok[0].upper() + tok[1:]

def parse_unitdefs():
    """Return {symbol: [allegiance,...]} for addr-named UnitDef arrays."""
    text = open(UDEF_C, encoding='utf-8', errors='replace').read()
    out = {}
    # split on array definitions
    for m in re.finditer(r'struct UnitDefinition (UnitDef_0[0-9A-Fa-f]+)\[\]\s*=\s*\{', text):
        sym = m.group(1)
        # find matching closing brace
        i = m.end(); depth = 1
        while i < len(text) and depth:
            if text[i] == '{': depth += 1
            elif text[i] == '}': depth -= 1
            i += 1
        body = text[m.end():i]
        alleg = re.findall(r'\.allegiance\s*=\s*(FACTION_ID_\w+)', body)
        out[sym] = alleg
    return out

def chapter_of(sym):
    """Most-specific chapter token from referencing event header filename."""
    try:
        files = subprocess.check_output(
            ['grep', '-rlw', sym, os.path.join(ROOT, 'src/events')],
            text=True, stderr=subprocess.DEVNULL).split()
    except subprocess.CalledProcessError:
        files = []
    toks = set()
    for f in files:
        b = os.path.basename(f)
        m = re.match(r'^(.+?)-(eventscript|eventinfo|eventscr|eventudefs)\.h$', b)
        if m:
            toks.add(m.group(1))
    # ignore non-chapter helper headers that reference everything
    toks = {t for t in toks if t not in ('common', 'messed')}
    chs = sorted(toks)
    if len(chs) == 1:
        return norm_chapter(chs[0])
    if len(chs) > 1:
        # prefer a primary-chapter token over world-map/skirmish variants
        prim = [t for t in chs if re.match(r'^(ch\d+[abx]*|prologue)$', t)]
        if len(prim) == 1:
            return norm_chapter(prim[0])
        return None  # genuinely shared/ambiguous -> handle separately
    return None

def role_of(allegs):
    facs = set(allegs)
    if not facs:
        return 'Units'
    if len(facs) == 1:
        return ROLE.get(next(iter(facs)), 'Units')
    return 'Mixed'

def main():
    udefs = parse_unitdefs()
    existing = set(l.strip() for l in open('/tmp/symdoc/base_syms.txt'))
    renames = {}; used = set(); unresolved = []
    bucket = defaultdict(list)
    for sym in sorted(udefs):
        ch = chapter_of(sym)
        if not ch:
            unresolved.append(sym); continue
        role = role_of(udefs[sym])
        bucket[(ch, role)].append(sym)
    for (ch, role), syms in bucket.items():
        multi = len(syms) > 1
        for i, sym in enumerate(sorted(syms)):
            name = f"UnitDef_{ch}{role}" + (f"_{i}" if multi else "")
            base = name; k = 0
            while name in used or (name in existing and name not in udefs):
                k += 1; name = f"{base}_{k}"
            used.add(name); renames[sym] = name
    for s, n in sorted(renames.items()):
        print(f"{s}\t{n}")
    sys.stderr.write(f"resolved {len(renames)}  unresolved(chapter) {len(unresolved)}\n")
    sys.stderr.write("unresolved: " + " ".join(unresolved[:40]) + "\n")

if __name__ == '__main__':
    main()
