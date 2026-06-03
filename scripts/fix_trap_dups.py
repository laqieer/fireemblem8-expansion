#!/usr/bin/env python3
"""Fix duplicate TrapData_Event_<chapter>_<n> definitions in events_trapdata.c.

The proximity batch (orphan trap arrays, referenced only by their definition +
extern) reused per-chapter indices already taken by the reference-resolved
batch, creating duplicate definitions. Each def is tagged with a `// 0x<addr>`
comment, so we can identify the orphan copy (its address is in trap_prox.tsv)
and renumber ONLY it to a free per-chapter index, updating its one extern in
eventcall.h. The referenced copy keeps its name, so eventinfo.h refs stay valid.
"""
import os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = os.path.join(ROOT, 'src/events_trapdata.c')
H = os.path.join(ROOT, 'include/eventcall.h')

def addr_comment(addrhex):  # 089EDB6F -> '0x89EDB6F'
    return '0x' + format(int(addrhex, 16), 'X')

# orphan addresses (their def must be the one renumbered)
orphan_addrs = set()
for line in open('/tmp/symdoc/trap_prox.tsv'):
    o = line.split('\t')[0]
    m = re.search(r'_0([0-9A-Fa-f]+)$', o)
    if m: orphan_addrs.add(addr_comment('0'+m.group(1)))

lines = open(C).read().splitlines()
# collect defs: (lineno, name, addr_comment_on_prev_line)
defs = []
for i, ln in enumerate(lines):
    m = re.match(r'\s*CONST_DATA \w+ (TrapData_Event_[A-Za-z0-9_]+)\[\]', ln)
    if m:
        prev = lines[i-1].strip() if i > 0 else ''
        am = re.match(r'//\s*(0x[0-9A-Fa-f]+)', prev)
        defs.append((i, m.group(1), am.group(1).upper().replace('0X','0x') if am else None))

from collections import defaultdict, Counter
name_count = Counter(n for _, n, _ in defs)
chap_max = defaultdict(int)
for _, n, _ in defs:
    m = re.match(r'TrapData_Event_(.+)_(\d+)$', n)
    if m: chap_max[m.group(1)] = max(chap_max[m.group(1)], int(m.group(2)))

renames = []  # (lineno, old, new)
for i, n, ac in defs:
    if name_count[n] > 1 and ac in orphan_addrs:
        m = re.match(r'TrapData_Event_(.+)_(\d+)$', n)
        chap = m.group(1)
        chap_max[chap] += 1
        new = f"TrapData_Event_{chap}_{chap_max[chap]}"
        renames.append((i, n, new))

# apply to the .c (line-specific) and eventcall.h (one extern each)
htext = open(H).read().splitlines()
for i, old, new in renames:
    lines[i] = lines[i].replace(old, new, 1)
    # rename one matching extern in eventcall.h
    for j, hl in enumerate(htext):
        if re.search(rf'\bextern\b.*\b{old}\b', hl):
            htext[j] = hl.replace(old, new, 1); break
open(C, 'w').write('\n'.join(lines) + '\n')
open(H, 'w').write('\n'.join(htext) + '\n')
print(f"renumbered {len(renames)} orphan trap defs")
for i, o, n in renames: print(f"  line {i+1}: {o} -> {n}")
