"""A mod's .acp: which shipped resources it overrides, and its copy of each."""
import struct, re
from aow4lib import parse_record, rtext

def _blocks(body):
    out = []
    lst = dict(parse_record(body)).get(1)
    if not lst or lst == b'\x00': return out
    for fid, entry in parse_record(lst):
        cls = struct.unpack_from('<I', entry)[0]
        out.append((cls, dict(parse_record(entry, 4)).get(cls)))
    return out

def mod_overrides(b):
    """-> (mod name, [{pack, id, name, raw (None when the mod deletes it)}])"""
    if b[:4] != b'CPR\0': raise ValueError('not an .acp file')
    top = dict(parse_record(b, 0x24))
    ovs = []
    for cls, pk in (_blocks(top[38]) if 38 in top else []):
        pk = dict(parse_record(pk))
        pack = rtext(pk.get(1)) or ''
        for c2, ov in (_blocks(pk[2]) if 2 in pk else []):
            ov = dict(parse_record(ov))
            ident = rtext(ov.get(3)) or ''
            m = re.match(r'^\[#([0-9A-Fa-f]{16})\]', ident)
            raw = ov.get(1) if ov.get(1) else None
            name = None
            if raw:
                d = dict(parse_record(raw, 4))
                name = rtext(d.get(21))
            ovs.append({'pack': pack, 'id': m.group(1).upper() if m else None, 'name': name or '(unnamed)',
                        'raw': bytes(raw) if raw else None, 'target': ident})
    return rtext(top.get(21)) or '(unnamed mod)', ovs
