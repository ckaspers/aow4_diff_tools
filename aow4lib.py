"""Age of Wonders 4 resource pack (.rpk) and mod (.acp) reader.

Container framing: a record is [count byte][(field id, offset) pairs][bodies back to back];
a set high bit on the count byte means a u32 follows with the number of (u32, u32) pairs.
A polymorphic object is [u32 class id][record]. Field names and types come from a schema
extracted from ResourceED.exe (see extract_schema.py).
"""
import struct, json, os, re

def parse_record(b, start=0, end=None):
    if end is None: end = len(b)
    b0 = b[start]; cur = start + 1; large = 0
    if b0 & 0x80:
        large = struct.unpack_from('<I', b, cur)[0]; cur += 4
    pairs = []
    for _ in range(b0 & 0x7f):
        pairs.append((b[cur], b[cur + 1])); cur += 2
    for _ in range(large):
        pairs.append(struct.unpack_from('<II', b, cur)); cur += 8
    offs = sorted(set(p[1] for p in pairs))
    nxt = {o: (offs[i + 1] if i + 1 < len(offs) else None) for i, o in enumerate(offs)}
    out = []
    for fid, off in pairs:
        n = nxt[off]
        out.append((fid, b[cur + off: (cur + n) if n is not None else end]))
    return out

def try_record(b):
    n = len(b)
    if n < 3: return None
    b0 = b[0]; cur = 1; large = 0
    if b0 & 0x80:
        if n < 5: return None
        large = struct.unpack_from('<I', b, 1)[0]; cur = 5
        if large == 0 or large > 200000: return None
    small = b0 & 0x7f
    if small + large == 0: return None
    hdr = cur + 2 * small + 8 * large
    if hdr > n: return None
    pairs = []
    for _ in range(small):
        pairs.append((b[cur], b[cur + 1])); cur += 2
    for _ in range(large):
        pairs.append(struct.unpack_from('<II', b, cur)); cur += 8
    offs = sorted(set(p[1] for p in pairs))
    if offs[0] != 0 or offs[-1] > n - hdr: return None
    fids = [p[0] for p in pairs]
    if len(set(fids)) != len(fids): return None
    nxt = {o: (offs[i + 1] if i + 1 < len(offs) else None) for i, o in enumerate(offs)}
    out = []
    for fid, off in pairs:
        e = nxt[off]
        out.append((fid, b[hdr + off: (hdr + e) if e is not None else n]))
    return out

def rtext(b):
    if not b or len(b) < 4: return None
    n = struct.unpack_from('<I', b)[0]
    if n != len(b) - 4: return None
    try: return b[4:4 + n].decode('utf-8')
    except UnicodeDecodeError: return None

class Schema:
    def __init__(self, path):
        d = json.load(open(path))
        self.trigger = {int(k): (v[0], {int(f): x for f, x in v[1].items()}) for k, v in d.pop('_trigger', {}).items()}
        self.meta = d.pop('_meta', {})
        self.enums = {int(k, 16): v for k, v in d.pop('_enums', {}).items()}
        self.cls = {int(k, 16): v for k, v in d.items()}
        for v in self.cls.values():
            v['f'] = {int(k): x for k, x in v['f'].items()}
            v['d'] = {int(k): x for k, x in v['d'].items()}
            v['p'] = int(v['p'], 16) if v['p'] else None
            v['m'] = {int(k): int(x, 16) for k, x in v.get('m', {}).items()}
            v['e'] = {int(k): int(x, 16) for k, x in v.get('e', {}).items()}
        self.by_name = {v['n']: k for k, v in self.cls.items()}
    def name(self, cid):
        c = self.cls.get(cid)
        return c['n'] if c else 'class_%x' % cid
    def chain(self, cid):
        out = []
        while cid and cid in self.cls and cid not in out:
            out.append(cid); cid = self.cls[cid]['p']
        return out
    def memtype(self, cid, off):
        for c in self.chain(cid):
            t = self.cls[c]['m'].get(off)
            if t: return t
        return None
    def isa(self, cid, name):
        return any(self.cls[c]['n'] == name for c in self.chain(cid))
    def display(self, cid, off):
        for c in self.chain(cid):
            d = self.cls[c]['d'].get(off)
            if d: return d
        return None

# ---- pack files -------------------------------------------------------------
def read_pack(path):
    b = open(path, 'rb').read()
    return pack_resources(b)

def pack_resources(b):
    """-> list of (cid, raw object bytes incl. cid) for every resource in a .rpk"""
    if b[:4] != b'RPK\0': raise ValueError('not an rpk')
    top = dict(parse_record(b, 0xb0))
    out = []
    lists = top.get(13)
    if not lists: return out
    for fid, cat in parse_record(lists):
        cid = struct.unpack_from('<I', cat)[0]
        r = dict(parse_record(cat, 4))
        lib = r.get(51)
        if not lib: continue
        lib = dict(parse_record(lib))
        lst = lib.get(21)
        if not lst: continue
        lst = dict(parse_record(lst)).get(1)
        if not lst: continue
        for idx, item in parse_record(lst):
            if len(item) >= 4:
                out.append(item)
    return out
