#!/usr/bin/env python3
"""Resolve remaining address-named banim/animation data blobs across ALL banim
files (data_banim, banim-ekr*, banimconf, ekrtriangle, efxlvupfx, ending reels,
mapanim, opanim...). Names a blob from the common animation token of its named
consumers, falling back to the defining file's animation name. Iterates so
chains (AnimScr->AnimScr, AnimSprite->AnimScr) inherit context.

Usage: symdoc_banim_all.py <targets_file>
"""
import os, re, glob, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDR = re.compile(r'_0[0-9A-Fa-f]{6,7}$')
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
C_DEF = re.compile(r'^\s*[A-Za-z_].*?\b([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*\]\s*=')
S_LABEL = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):')
STRIP = sorted(['AnimSpriteArray_','AnimScrArray_','AnimScr_','AnimSprite_','AnimConf_',
  'TsaArray_','ImgArray_','PalArray_','TsaLut_','ImgLut_','PalLut_','gSpriteArray_',
  'SpriteArray_','ProcScr_','StartCRSubSpell_','StartSubSpell_','StartSpellAnim',
  'StartSpell','StartCR','Start','Sprite_','Img_','Tsa_','Pal_','Obj_','Frames_',
  'gUnk_','gProcScr_','gSprite_'], key=len, reverse=True)

def strip_prefix(n):
    for p in STRIP:
        if n.startswith(p) and len(n) > len(p): return n[len(p):]
    if n.startswith('g') and len(n) > 1 and n[1].isupper(): return n[1:]
    return n

def common_token(rs):
    rs = sorted(set(rs))
    if not rs: return None
    pre = os.path.commonprefix(rs)
    pre = re.sub(r'\d+$', '', pre).rstrip('_')
    return pre if len(pre) >= 3 else None

def type_of(s): return s.split('_0')[0]

def file_token(f):
    b = re.sub(r'\.(c|s)$', '', os.path.basename(f))
    b = b.replace('banim-','').replace('data_','').replace('anim_','')
    b = re.sub(r'^efxmagic-|^banim','', b)
    return ''.join(p.capitalize() for p in re.split(r'[-_]', b) if p)

FILES = sorted(set(
    glob.glob(ROOT+'/src/banim*.c')+glob.glob(ROOT+'/src/banim*.s')+
    glob.glob(ROOT+'/src/data/banim/*.s')+glob.glob(ROOT+'/src/data_banim*.s')+
    glob.glob(ROOT+'/src/data/banim-*.s')+glob.glob(ROOT+'/src/opanim*.s')+
    glob.glob(ROOT+'/src/opanim*.c')+glob.glob(ROOT+'/src/data/mapanim/*.s')+
    glob.glob(ROOT+'/src/data/ending/*.s')+glob.glob(ROOT+'/src/*reel*.s')))

def scan(remaining):
    cons = defaultdict(list); confile = defaultdict(set); deffile = {}
    for path in FILES:
        isc = path.endswith('.c'); cur=None; rf=os.path.relpath(path,ROOT)
        for line in open(path, encoding='utf-8', errors='replace'):
            m = (C_DEF if isc else S_LABEL).match(line)
            if m: cur = m.group(1)
            ml = S_LABEL.match(line)
            if ml and ml.group(1) in remaining: deffile[ml.group(1)] = rf
            if line.lstrip().startswith('.ascii'): continue
            for idt in IDENT.findall(line):
                if idt in remaining and idt != cur and cur:
                    cons[idt].append(cur); confile[idt].add(rf)
    return cons, confile, deffile

def main():
    remaining = set(l.strip() for l in open(sys.argv[1]) if l.strip())
    existing = set(l.strip() for l in open('/tmp/symdoc/base_syms.txt')) - remaining
    renames = {}; used = set()
    for rnd in range(8):
        cons, confile, deffile = scan(remaining - set(renames))
        def view(c): return renames.get(c, c)
        pend = {}
        for blob in sorted(remaining - set(renames)):
            named = [strip_prefix(view(c)) for c in cons.get(blob, [])
                     if not ADDR.search(view(c)) and not view(c).islower()]
            tok = common_token(named)
            if not tok:
                df = deffile.get(blob)
                if df: tok = file_token(df)
            if tok and len(tok) >= 3:
                pend[blob] = tok
        if not pend: break
        bucket = defaultdict(list)
        for blob, tok in pend.items():
            bucket[(type_of(blob), tok)].append(blob)
        prog = 0
        for (typ, tok), blobs in bucket.items():
            multi = len(blobs) > 1
            for i, blob in enumerate(sorted(blobs)):
                name = f"{typ}_{tok}" + (f"_{i}" if multi else "")
                base = name; k = 0
                while name in used or name in existing:
                    k += 1; name = f"{base}_{k}"
                used.add(name); renames[blob] = name; prog += 1
        sys.stderr.write(f"round {rnd}: +{prog} total {len(renames)}/{len(remaining)}\n")
        if prog == 0: break
    # quality filter
    good = 0
    for blob, n in sorted(renames.items()):
        if re.search(r'_[0-9A-Fa-f]{3,}(_|$)', n) or 'Start' in n or re.search(r'_efx_\d|_efx$', n):
            continue
        toks = n.split('_')
        if any(toks[i] and toks[i]==toks[i+1] for i in range(len(toks)-1)): continue
        print(f"{blob}\t{n}"); good += 1
    sys.stderr.write(f"emitted {good}/{len(renames)} (after quality filter)\n")

if __name__ == '__main__':
    main()
