#!/usr/bin/env python3
"""Second-pass resolver for the remaining address-named banim blobs.

Operates on the CURRENT file state (first-pass renames already applied), so
consumer symbols now carry good names. Resolves a blob's animation context by
taking the common token shared by all its named consumers, and iterates so
AnimScr/AnimSprite chains inherit context once their consumers are named.
"""
import os, re, glob, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDR = re.compile(r'_0[0-9A-Fa-f]{6,7}$')
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
C_DEF = re.compile(r'^\s*[A-Za-z_].*?\b([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*\]\s*=')
S_LABEL = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):')

# Prefixes to strip from a consumer symbol to reach its animation token.
# Longest first so e.g. AnimSpriteArray_ beats AnimSprite_.
STRIP = sorted([
    'AnimSpriteArray_', 'AnimScrArray_', 'AnimScr_', 'AnimSprite_',
    'TsaArray_', 'ImgArray_', 'PalArray_', 'TsaLut_', 'ImgLut_', 'PalLut_',
    'gSpriteArray_', 'SpriteArray_', 'ProcScr_', 'StartCRSubSpell_',
    'StartSubSpell_', 'StartSpellAnim', 'StartSpell', 'StartCR', 'Start',
    'Sprite_', 'Img_', 'Tsa_', 'Pal_', 'Obj_', 'Frames_', 'gUnk_',
], key=len, reverse=True)

def strip_prefix(name):
    for p in STRIP:
        if name.startswith(p) and len(name) > len(p):
            return name[len(p):]
    if name.startswith('g') and len(name) > 1 and name[1].isupper():
        return name[1:]
    return name

def common_token(remainders):
    """Longest common prefix of remainders, trimmed to a sensible boundary."""
    if not remainders:
        return None
    rs = sorted(set(remainders))
    pre = os.path.commonprefix(rs)
    # trim trailing partial word: cut at last position where next char in any
    # string starts an uppercase or digit run boundary; simplest: cut trailing
    # lowercase tail that differs. Trim trailing non-boundary chars.
    pre = re.sub(r'\d+$', '', pre)  # drop trailing pose digits like TeonoObj2
    # trim trailing underscores
    pre = pre.rstrip('_')
    return pre if len(pre) >= 3 else None

def type_of(s):
    return s.split('_0')[0]

def file_token(fname):
    b = os.path.basename(fname)
    b = re.sub(r'\.(c|s)$', '', b)
    b = b.replace('banim-', '').replace('data_', '')
    b = re.sub(r'efxmagic-', '', b)
    parts = re.split(r'[-_]', b)
    return ''.join(p.capitalize() for p in parts if p)

def scan(remaining):
    """Map each remaining blob -> (named_consumer_remainders, files)."""
    cons = defaultdict(list); confile = defaultdict(set)
    files = sorted(set(
        glob.glob(ROOT+'/src/banim*.c') + glob.glob(ROOT+'/src/banim*.s') +
        glob.glob(ROOT+'/src/data/banim/*.s') + glob.glob(ROOT+'/src/data_banim*.s') +
        glob.glob(ROOT+'/src/data/banim-*.s')))
    for path in files:
        isc = path.endswith('.c'); cur = None; rf = os.path.relpath(path, ROOT)
        for line in open(path, encoding='utf-8', errors='replace'):
            m = (C_DEF if isc else S_LABEL).match(line)
            if m: cur = m.group(1)
            if line.lstrip().startswith('.ascii'): continue
            for idt in IDENT.findall(line):
                if idt in remaining and idt != cur and cur:
                    cons[idt].append(cur); confile[idt].add(rf)
    return cons, confile

def main():
    # remaining addr-named blobs still defined in data_banim.s
    remaining = set()
    for line in open(ROOT+'/src/data/banim/data_banim.s'):
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*):', line)
        if m and ADDR.search(m.group(1)):
            remaining.add(m.group(1))
    existing = set(l.strip() for l in open('/tmp/symdoc/base_syms.txt')) - remaining
    sys.stderr.write(f"remaining addr-named in data_banim.s: {len(remaining)}\n")

    renames = {}
    used = set()
    # iterate: each round, resolve blobs whose consumers yield a token
    for round_no in range(8):
        cons, confile = scan(remaining - set(renames))
        progressed = 0
        # current name view: consumer name -> resolved name if renamed
        def view(c):
            return renames.get(c, c)
        bucket = defaultdict(list)
        pend = {}
        for blob in sorted(remaining - set(renames)):
            named = [strip_prefix(view(c)) for c in cons.get(blob, [])
                     if not ADDR.search(view(c)) and not view(c).islower()]
            tok = common_token(named)
            if not tok:
                # fallback: single consuming file
                fs = confile.get(blob, set())
                non_databanim = {f for f in fs if 'data_banim.s' not in f}
                if len(non_databanim) == 1:
                    tok = file_token(next(iter(non_databanim)))
            if tok:
                pend[blob] = tok
        for blob, tok in pend.items():
            bucket[(type_of(blob), tok)].append(blob)
        for (typ, tok), blobs in bucket.items():
            multi = len(blobs) > 1
            for i, blob in enumerate(sorted(blobs)):
                name = f"{typ}_{tok}" + (f"_{i}" if multi else "")
                base = name; k = 0
                while name in used or name in existing:
                    k += 1; name = f"{base}_x{k}"
                used.add(name); renames[blob] = name; progressed += 1
        sys.stderr.write(f"round {round_no}: resolved {progressed}, total {len(renames)}/{len(remaining)}\n")
        if progressed == 0:
            break

    for blob, new in sorted(renames.items()):
        print(f"{blob}\t{new}")
    unresolved = sorted(remaining - set(renames))
    sys.stderr.write(f"FINAL resolved {len(renames)}, unresolved {len(unresolved)}\n")
    for b in unresolved[:30]:
        sys.stderr.write(f"  UNRESOLVED {b}\n")

if __name__ == '__main__':
    main()
