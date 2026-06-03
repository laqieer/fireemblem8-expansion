#!/usr/bin/env python3
"""Propose semantic names for address-named banim data blobs.

Each blob (Img_/Tsa_/Pal_/AnimSprite_/AnimScr_<addr>) defined in
src/data/banim/data_banim.s is consumed by a well-named array or script
symbol in a per-animation banim source file. The consuming symbol's name
encodes the animation context, which becomes the blob's documentation.

Outputs a rename map (old<TAB>new) to stdout and a report to stderr.
Only emits renames where the consuming context is unambiguous and itself
well-named (not address-named), so we never invent a name from nothing.
"""
import os, re, sys, glob
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ADDR = re.compile(r'_0[0-9A-Fa-f]{6,7}$')
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
# top-level symbol definition in .c (array/object) or .s (label)
C_DEF = re.compile(r'^\s*[A-Za-z_].*?\b([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*\]\s*=')
S_LABEL = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):')

def load_targets():
    with open('/tmp/symdoc/databanim_targets.txt') as f:
        return set(l.strip() for l in f if l.strip())

def consuming_symbol_map(targets):
    """For each banim source file, walk lines tracking the current top-level
    symbol; record which target blobs are referenced under each symbol."""
    # blob -> set of (consumer_symbol)
    consumers = defaultdict(set)
    files = sorted(set(
        glob.glob(os.path.join(ROOT, 'src/banim*.c')) +
        glob.glob(os.path.join(ROOT, 'src/banim*.s')) +
        glob.glob(os.path.join(ROOT, 'src/data/banim/*.s')) +
        glob.glob(os.path.join(ROOT, 'src/data_banim*.s')) +
        glob.glob(os.path.join(ROOT, 'src/data/banim-*.s'))
    ))
    for path in files:
        is_c = path.endswith('.c')
        cur = None
        defin_file = os.path.relpath(path, ROOT)
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                if is_c:
                    m = C_DEF.match(line)
                    if m:
                        cur = m.group(1)
                else:
                    m = S_LABEL.match(line)
                    if m:
                        cur = m.group(1)
                # find target idents referenced on this line
                # skip the blob's own definition line and debug .ascii
                stripped = line.lstrip()
                if stripped.startswith('.ascii'):
                    continue
                for ident in IDENT.findall(line):
                    if ident in targets and ident != cur:
                        consumers[ident].add((cur, defin_file))
    return consumers

def base_context(consumer_name):
    """Strip a leading type-ish prefix from the consumer symbol to get the
    animation context. e.g. ImgArray_FenrirBg -> FenrirBg ; TsaArray_X -> X.
    Returns None if the consumer is itself address-named or empty."""
    if consumer_name is None:
        return None
    if ADDR.search(consumer_name):
        return None
    name = consumer_name
    for pre in ('ImgArray_', 'TsaArray_', 'PalArray_', 'AnimSpriteArray_',
                'gSpriteArray_', 'SpriteArray_', 'TsaLut_', 'ImgLut_',
                'PalLut_', 'Img_', 'Tsa_', 'Pal_',
                'AnimScr_', 'AnimSprite_', 'ProcScr_', 'Sprite_', 'Obj_',
                'gUnk_', 'g'):
        if name.startswith(pre) and len(name) > len(pre):
            name = name[len(pre):]
            break
    return name

def load_existing_symbols():
    """All symbols in the ELF, to guard against name collisions."""
    with open('/tmp/symdoc/base_syms.txt') as f:
        return set(l.strip() for l in f if l.strip())

def main():
    targets = load_targets()
    consumers = consuming_symbol_map(targets)
    # Existing symbols that are NOT themselves being renamed: a generated name
    # must avoid these to prevent duplicate-definition build breaks.
    existing = load_existing_symbols() - targets
    type_of = lambda s: s.split('_0')[0]  # Img / Tsa / Pal / AnimSprite / AnimScr / ...

    renames = {}
    unresolved = []
    used_names = set()
    # group blobs by (type, context) to assign ordinals deterministically by address
    buckets = defaultdict(list)
    for blob in sorted(targets):
        cons = consumers.get(blob, set())
        ctxs = set()
        for (cname, _f) in cons:
            ctx = base_context(cname)
            if ctx:
                ctxs.add(ctx)
        if len(ctxs) == 1:
            ctx = next(iter(ctxs))
            buckets[(type_of(blob), ctx)].append(blob)
        else:
            unresolved.append((blob, sorted(c for (c, _f) in cons)))

    for (typ, ctx), blobs in buckets.items():
        blobs_sorted = sorted(blobs)  # by address text -> stable
        multi = len(blobs_sorted) > 1
        for i, blob in enumerate(blobs_sorted):
            name = f"{typ}_{ctx}" + (f"_{i}" if multi else "")
            # ensure global uniqueness vs generated AND pre-existing symbols
            base = name; k = 0
            while name in used_names or name in existing:
                k += 1; name = f"{base}_x{k}"
            used_names.add(name)
            renames[blob] = name

    for blob, new in sorted(renames.items()):
        print(f"{blob}\t{new}")

    sys.stderr.write(f"resolved: {len(renames)}  unresolved: {len(unresolved)}\n")
    sys.stderr.write("--- unresolved (shared/ambiguous/no-named-consumer) ---\n")
    from collections import Counter
    reason = Counter()
    for blob, cons in unresolved:
        if not cons:
            reason['no_consumer'] += 1
        else:
            reason['multi_or_addr_consumer'] += 1
    for r, c in reason.items():
        sys.stderr.write(f"  {r}: {c}\n")
    for blob, cons in unresolved[:15]:
        sys.stderr.write(f"  {blob} <- {cons}\n")

if __name__ == '__main__':
    main()
