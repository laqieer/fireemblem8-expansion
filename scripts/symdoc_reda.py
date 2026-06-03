#!/usr/bin/env python3
"""Name address-named REDA_<addr> arrays (reinforcement event data) after the
UnitDefinition entry that owns them via its .redas field.

Name form: REDA_<UnitDefContext>_<CharOrIdx>, where UnitDefContext is the owning
UnitDef symbol minus its 'UnitDef_' prefix. Only emitted when the owning UnitDef
is itself documented (not address-named), so REDAs never inherit an address.
"""
import os, re, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UDEF_C = os.path.join(ROOT, 'src/events_udefs.c')
ADDR = re.compile(r'_0[0-9A-Fa-f]{5,7}$')

def main():
    existing = set(l.strip() for l in open('/tmp/symdoc/base_syms.txt'))
    lines = open(UDEF_C, encoding='utf-8', errors='replace').read().splitlines()
    cur_udef = None
    cur_char = None
    entry_idx = defaultdict(int)   # per-udef running entry counter
    owners = {}                    # reda -> (udef, char, idx)
    udef_re = re.compile(r'struct UnitDefinition ([A-Za-z0-9_]+)\[\]')
    for ln in lines:
        m = udef_re.search(ln)
        if m:
            cur_udef = m.group(1); cur_char = None
            continue
        mc = re.search(r'\.charIndex\s*=\s*(?:CHARACTER_)?([A-Za-z0-9_]+)', ln)
        if mc:
            cur_char = mc.group(1)
        mr = re.search(r'\.redas\s*=\s*(REDA_0[0-9A-Fa-f]+)', ln)
        if mr and cur_udef:
            reda = mr.group(1)
            i = entry_idx[cur_udef]; entry_idx[cur_udef] += 1
            owners[reda] = (cur_udef, cur_char, i)

    renames = {}; used = set(); skipped_addr_owner = 0
    bucket = defaultdict(list)
    for reda, (udef, char, i) in owners.items():
        if ADDR.search(udef):
            skipped_addr_owner += 1
            continue
        ctx = udef[len('UnitDef_'):] if udef.startswith('UnitDef_') else udef
        bucket[ctx].append((reda, char, i))
    for ctx, items in bucket.items():
        # disambiguate within the same UnitDef context
        items.sort(key=lambda t: t[2])
        # decide whether char names are distinct & useful
        def good_char(c):
            return bool(c) and bool(re.match(r'^[A-Z][A-Z0-9_]+$', c))
        chars = [c for (_r, c, _i) in items]
        use_char = all(good_char(c) for c in chars) and len(set(chars)) == len(chars)
        for (reda, char, i) in items:
            tag = char if (use_char and good_char(char)) else str(i)
            name = f"REDA_{ctx}_{tag}"
            base = name; k = 0
            while name in used or name in existing:
                k += 1; name = f"{base}_{k}"
            used.add(name); renames[reda] = name
    for r, n in sorted(renames.items()):
        print(f"{r}\t{n}")
    sys.stderr.write(f"named {len(renames)} REDAs; skipped(addr owner) {skipped_addr_owner}; "
                     f"total .redas owners {len(owners)}\n")

if __name__ == '__main__':
    main()
