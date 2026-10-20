"""Turn a resource's bytes into a readable tree, using the schema extracted from ResourceED.exe.

Node shapes (tuples, so they compare cheaply):
  ('o', cls, fields)   object; cls is a class name or None; fields is a list of (label, owner, node)
  ('l', items)         list of nodes
  ('v', text)          a value, already formatted
  ('r', name, id)      a reference to another resource
  ('t', tags)          a tag set (sorted tuple of tag names)
  ('x', bytes)         bytes the decoder cannot read
"""
import struct, re
from aow4lib import try_record, rtext

STR_SLOTS = {'368', '376'}
STRLIST_SLOTS = {'384'}
FLOAT_SLOTS = {'264', '272', '280', '288', '480'}
VEC_SLOTS = {'208', '216', '224', '232', '240', '256'}
BOOL_SLOTS = {'72', '80', '296', '24'}
ID_SLOTS = {'96', '88'}
INT_SLOTS = {'8', '56', '64', '104', '112', '120', '128', '136', '144', '152', '160', '168', '176', '184', '192', '304', '312', '320'}

def f32(b):
    v = struct.unpack('<f', b)[0]
    if v != v or v in (float('inf'), float('-inf')): return repr(v)
    for p in range(1, 10):
        s = '%.*g' % (p, v)
        if struct.pack('<f', float(s)) == b: break
    if 'e' not in s and '.' not in s: s += '.0'
    return s

def idhex(b):
    lo, hi = struct.unpack_from('<II', b)
    return '%08X%08X' % (hi, lo)

def clean_label(name):
    if not name: return None
    n = name.split('->')[-1]
    m = re.match(r'^(?:ECAST_PTR(?:_OBJ)?\((?:\w+,\s*)?)?[&*]*(?:\(\w+\*?\)&?)?([A-Za-z_][\w.]*)', n)
    if m: n = m.group(1)
    n = n.split('.')[0] if n.endswith('.Value') or n.endswith('.Get') else n
    if re.match(r'^F[A-Z]', n): n = n[1:]
    return n

def is_list_rec(b):
    r = try_record(b)
    if r is None: return False
    keys = sorted(f for f, _ in r)
    return keys == list(range(len(keys)))

def special(cls, fields):
    """engine types with a fixed reading"""
    if cls == 'TEStringKeyReplacer':
        # {StringKeyItemList: [items]} -> the list itself
        for lab, own, node in fields:
            if node[0] == 'l': return node
            if node[0] == 'o' and not node[2]: return ('l', [])
    if cls in ('TEResourceLLI', 'TEResourceIDList'):
        f = {lab: v for lab, own, v in fields}
        ident = f.get('ID', ('v', None))[1]
        name = f.get('UserName', ('v', None))[1]
        name = name.strip("'") if name else (ident or '?')
        return ('r', name, ident)
    if cls == 'TELocalizedStringLink':
        ren = {'Tag': 'msgid', 'StringKeyReplacer': 'replacements'}
        return ('o', cls, [(ren.get(lab, lab), own, node) for lab, own, node in fields])
    if cls == 'TEStringKeyItem':
        out = []
        for lab, own, node in fields:
            out.append(({'field_2': 'key', 'field_3': 'value'}.get(lab, lab), own, node))
        return ('o', cls, out)
    return ('o', cls, fields)

# fields written by helpers the schema scan cannot name (seen in the editor)
FIELD_LABELS = {'TCombatDefenseBlock': {1: 'Armor', 2: 'Resistance'},
                'TCombatDamageBlock': {1: 'Physical', 2: 'Blight', 3: 'Shock', 4: 'Fire', 5: 'Spirit', 6: 'Frost'}}

# members the constructors do not reveal (set through a pointer)
FIXED_TYPES = {}

def is_list_type(S, T):
    n = S.cls[T]['n']
    return n.endswith('List') or n.endswith('ListEX') or S.isa(T, 'TEResourceLinkList') or S.isa(T, 'TELightResourceLinkList')

class Decoder:
    def __init__(self, schema):
        self.S = schema
        self.cls = schema.cls
        # unscoped classes that own fields, for guessing the type of a bare record
        self.bare = [(cid, v) for cid, v in self.cls.items() if not v['sc'] and v['own'] and v['f']]
        self._guess = {}
        self.names = {}          # resource id -> name, for references that carry only an id
        tl = schema.by_name.get('TTriggerDataList')
        if tl:
            ts = schema.by_name.get('TTriggerSettings')
            for fid in (11, 12, 13, 14): FIXED_TYPES[('TTriggerSettings', fid)] = tl

    # ---- public --------------------------------------------------------
    def resource(self, raw):
        """raw = [u32 class][record] -> (class id, node)"""
        cid = struct.unpack_from('<I', raw)[0]
        return cid, self.obj(cid, raw[4:])

    # ---- objects -------------------------------------------------------
    def chain_tables(self, cid):
        return [c for c in self.S.chain(cid) if not self.cls[c]['sc']]

    def scope_tables(self, cid):
        out = []
        for i, c in enumerate(self.S.chain(cid)):
            if i > 0 and self.cls[c]['sc']: break
            out.append(c)
        return out

    def obj(self, cid, body):
        fields = []
        if body and body != b'\x00':
            r = try_record(body)
            if r is None:
                return ('o', self.S.name(cid), [('payload', None, ('x', bytes(body)))])
            self.fields_into(r, self.chain_tables(cid), fields)
        return special(self.S.name(cid), fields)

    def is_scope(self, fid):
        return fid > 0xffff and fid in self.cls

    def fields_into(self, rec, tables, out):
        for fid, body in rec:
            if fid > 0xffff and self.is_scope(fid):
                sub = try_record(body) if body and body != b'\x00' else []
                if sub is None:
                    out.append((self.S.name(fid), self.S.name(fid), ('x', bytes(body))))
                else:
                    inner = []
                    self.fields_into(sub, self.scope_tables(fid), inner)
                    sp = special(self.S.name(fid), inner)
                    if sp[0] == 'o': out.extend(sp[2])
                    else: out.append((self.S.name(fid), self.S.name(fid), sp))
                continue
            owner = None; f = None
            for c in tables:
                f = self.cls[c]['f'].get(fid)
                if f: owner = c; break
            if f:
                disp = self.S.display(owner, f[2]) if f[2] is not None else None
                label = disp[0] if disp else (clean_label(f[0]) or 'field_%d' % fid)
                T = self.S.memtype(owner, f[2]) if f[2] is not None else None
                if T is None: T = FIXED_TYPES.get((self.cls[owner]['n'], fid))
                if T is not None and f[1] in ('40',) or (T is not None and f[1].startswith('h')):
                    node = self.typed(T, body)
                else:
                    node = self.value(body, f[1], disp[1] if disp else None)
                    if node[0] == 'v':
                        node = ('v', node[1], self.default_text(f, len(body), disp[1] if disp else None))
                        en = self.cls[owner].get('e', {}).get(fid)
                        if en in self.S.enums:
                            names = self.S.enums[en]
                            node = ('v',) + tuple((repr(names[x]) if x in names else x) if x is not None else None for x in node[1:])
                out.append((label, self.cls[owner]['n'], node))
            else:
                owner = tables[0] if tables else None
                node = self.guess(body)
                if node[0] != 'l' and bytes(body) in (b'\0', b'\0\0\0\0') and owner and is_list_type(self.S, owner):
                    node = ('l', [])        # a list class with no entries left: a zero count, not an empty object
                known = FIELD_LABELS.get(self.cls[owner]['n'] if owner else None, {})
                lab = known.get(fid) or ('Entries' if node[0] == 'l' else 'field_%d' % fid)
                if fid in known and node[0] == 'v': node = ('v', node[1], '0')
                out.append((lab, self.cls[owner]['n'] if owner else None, node))

    def typed(self, T, b):
        """a member whose class the exe's constructors tell us"""
        S = self.S
        tn = self.cls[T]['n'] if T in self.cls else None
        if tn is None: return self.guess(b)
        if len(b) == 0 or b == b'\x00':
            if S.isa(T, 'TETagSet') or S.isa(T, 'TETagSetLinkList'): return ('t', ())
            if S.isa(T, 'TETagFilter') or 'TagFilter' in tn: return ('f', ())
            if is_list_type(S, T): return ('l', [])
            return ('o', tn, [])
        if len(b) >= 5:
            cid = struct.unpack_from('<I', b)[0]
            if cid in self.cls and cid != T and (cid == T or S.isa(cid, tn)) and (b[4:] == b'\x00' or try_record(b[4:]) is not None):
                return self.obj(cid, b[4:])
        r = try_record(b)
        if r is None: return self.guess(b)
        d = dict(r)
        if S.isa(T, 'TETagSet') or S.isa(T, 'TETagSetLinkList') or tn.endswith('TagLinkList') and 1 in d:
            t = self.tagset(d.get(1, b'')) if 1 in d else None
            if t is not None: return t
        if S.isa(T, 'TETagFilter') or 'TagFilter' in tn:
            return self.tagfilter(r)
        if set(d) == {2} and len(d[2]) == 8 and (tn.endswith('LightLink') or S.isa(T, 'TELightResourceLink')):
            ident = idhex(d[2])
            return ('r', self.names.get(ident, ident), ident)
        if set(d) <= {19, 20, 21, 22, 23, 24} and (20 in d or 22 in d):
            return self.link(d)
        if any(f > 0xffff and self.is_scope(f) for f in d) or any(f in self.cls[c]['f'] for c in self.chain_tables(T) for f in d):
            out = []
            self.fields_into(r, self.chain_tables(T), out)
            node = special(tn, out)
            if node[0] == 'o' and is_list_type(S, T):
                lists = [v for lab, own, v in node[2] if v[0] == 'l']
                if len(lists) == 1 and len(node[2]) == 1: return lists[0]
            return node
        return self.guess(b)

    TF_MODES = {0: 'Always', 1: 'All', 2: 'One or More', 3: 'Exactly one (and no others)',
                10: 'Contains', 11: 'Contains inherited'}

    def filter_nodes(self, b, depth=0, maxdepth=3):
        """the count-prefixed node list of a tag filter, wherever it sits"""
        r = try_record(b) if b and b != b'\x00' else None
        if r is None or depth > maxdepth: return None
        dd = dict(r)
        keys = sorted(dd)
        if keys and keys[0] == 0 and len(dd[0]) == 4 and keys == list(range(len(keys))) and \
                struct.unpack('<I', dd[0])[0] == len(keys) - 1 and len(keys) > 1:
            nodes = []
            for k in keys[1:]:
                it = try_record(dd[k])
                if it is None: return None
                it = dict(it)
                if 1 not in it or len(it[1]) != 4 or struct.unpack('<I', it[1])[0] > 64: return None
                if set(it) - {0, 1, 2, 3}: return None
                nodes.append(it)
            return nodes
        for k in keys:
            n = self.filter_nodes(dd[k], depth + 1, maxdepth)
            if n is not None: return n
        return None

    def tagfilter(self, r):
        """a tag filter as one line per condition: ('f', lines)"""
        b = b''
        for f, x in r:
            n = self.filter_nodes(x)
            if n is not None: break
        else:
            n = None
        out = []
        for f, x in r:
            sub = try_record(x) if x and x != b'\x00' else None
            if sub:
                sd = dict(sub)
                if set(sd) <= {19, 20, 21, 22, 23, 24} and (20 in sd or 22 in sd):
                    out.append('uses %r' % self.link(sd)[1])
        for it in (n or []):
            mode = struct.unpack('<I', it[1])[0]
            inv = it.get(2) not in (None, b'\x00')
            tag = try_record(it.get(0, b'')) if it.get(0) not in (None, b'\x00') else None
            name = book = None
            if tag:
                t = dict(tag)
                name = rtext(t.get(2)) if 2 in t else None
                bk = try_record(t.get(3, b'')) if t.get(3) not in (None, b'\x00') else None
                if bk: book = rtext(dict(bk).get(4))
            m = self.TF_MODES.get(mode, 'mode %d' % mode)
            if inv: m = 'NOT ' + m
            out.append(m + ((" '%s'" % name) if name else '') + (('   [%s]' % book) if book else ''))
        return ('f', tuple(out))

    # ---- values --------------------------------------------------------
    def value(self, b, slot, pm=None):
        n = len(b)
        if slot in STR_SLOTS or (slot.startswith('fn') and rtext(b) is not None):
            t = rtext(b)
            return ('v', repr(t)) if t is not None else ('x', bytes(b))
        if slot in STRLIST_SLOTS:
            return self.strlist(b)
        if slot in FLOAT_SLOTS and n == 4:
            return ('v', f32(b))
        if slot in VEC_SLOTS and n % 4 == 0 and n:
            return ('v', '(' + ', '.join(f32(b[i:i + 4]) for i in range(0, n, 4)) + ')')
        if slot in BOOL_SLOTS and n == 1:
            return ('v', 'true' if b[0] else 'false')
        if slot in ID_SLOTS and n == 8:
            return ('v', idhex(b))
        if slot in INT_SLOTS and n in (1, 2, 4, 8):
            if pm is not None and self.S.name(pm) == 'TEPMColor' and n == 4:
                return ('v', '#%08X' % struct.unpack('<I', b)[0])
            v = int.from_bytes(b, 'little', signed=True)
            return ('v', str(v))
        return self.guess(b)

    def default_text(self, f, n, pm=None):
        """how the field's value reads when it is not stored (the serializer skips defaults)"""
        slot = f[1]; dv = f[3] if len(f) > 3 else 0
        if slot in BOOL_SLOTS: return 'true' if dv else 'false'
        if slot in INT_SLOTS:
            if pm is not None and self.S.name(pm) == 'TEPMColor' and n == 4: return '#%08X' % (dv & 0xffffffff)
            v = dv & 0xffffffff
            return str(v - (1 << 32) if v >= 1 << 31 else v)
        if slot in FLOAT_SLOTS: return '0.0'
        if slot in STR_SLOTS or slot.startswith('fn'): return "''"
        return None

    def strlist(self, b):
        try:
            cnt = struct.unpack_from('<I', b)[0]; p = 4; items = []
            for _ in range(cnt):
                L = struct.unpack_from('<I', b, p)[0]; p += 4
                items.append(('v', repr(b[p:p + L].decode('utf-8')))); p += L
            if p == len(b): return ('l', items)
        except Exception:
            pass
        return ('x', bytes(b))

    def guess(self, b):
        """a body whose type the schema does not give: an object, a record, text or bytes"""
        n = len(b)
        if n == 0: return ('v', "''")
        if n == 1 and b[0] == 0: return ('o', None, [])
        if n >= 4:
            cid = struct.unpack_from('<I', b)[0]
            if cid in self.cls and (n == 5 and b[4] == 0 or try_record(b[4:]) is not None):
                return self.obj(cid, b[4:])
        t = rtext(b)
        if t is not None and n > 4: return ('v', repr(t))
        r = try_record(b)
        if r is not None: return self.bare_record(r, b)
        if n in (1, 2, 4): return ('v', str(int.from_bytes(b, 'little', signed=True)))
        if n <= 64: return ('v', '0x' + bytes(b).hex())        # an id, hash or small blob
        return ('x', bytes(b))

    def bare_record(self, r, raw=None):
        fids = [f for f, _ in r]
        d = dict(r)
        if raw is not None and set(fids) <= {0, 1, 2, 3} and self.filter_nodes(raw, 0, 1) is not None:
            return self.tagfilter(r)
        # a scoped object without its class id: its scope keys say what it is
        if any(f > 0xffff and self.is_scope(f) for f in fids):
            out = []
            self.fields_into(r, [], out)
            top = next(self.S.name(f) for f in fids if f > 0xffff and self.is_scope(f))
            return special(top, out)
        if self.looks_tagfilter(d) and self.filter_nodes(d.get(0, b'')) is not None:
            return self.tagfilter(r)
        keys = sorted(fids)
        # a list: keys 0..n-1, or a count followed by the items
        if keys == list(range(keys[0], keys[0] + len(keys))):
            k0 = keys[0]
            if len(d[k0]) == 4 and struct.unpack('<I', d[k0])[0] == len(keys) - 1:
                tags = self.tagset_items(d, k0)
                if tags is not None: return tags
                return ('l', [self.guess(d[k]) for k in keys[1:]])
            if k0 == 0:
                return self.items(d)
        # list wrappers: {1: items} (TEList), {30: {1: items}} (resource link list)
        if keys == [1] and (d[1] == b'\x00' or is_list_rec(d[1])):
            tags = self.tagset(d[1])
            if tags is not None: return tags
            return self.items(dict(try_record(d[1]) or []))
        if keys == [30]:
            return self.guess(d[30])
        # a resource reference
        if set(d) <= {19, 20, 21, 22, 23, 24} and (20 in d or 22 in d) and (23 in d or 20 in d):
            return self.link(d)
        # a localized string link: {1: msgid, 2: replacer}
        if set(d) <= {1, 2} and (1 not in d or rtext(d[1]) is not None) and (2 not in d or self.is_replacer(d[2])):
            out = []
            if 1 in d: out.append(('msgid', None, ('v', repr(rtext(d[1])))))
            if 2 in d:
                rep = self.guess(d[2])
                out.append(('replacements', None, rep))
            return ('o', 'TELocalizedStringLink', out)
        return ('o', None, [('field_%d' % f, None, self.guess(b)) for f, b in r])

    def looks_tagfilter(self, d):
        if 0 not in d or set(d) - {0, 1, 2, 3}: return False
        root = try_record(d[0])
        if not root: return False
        root = dict(root)
        if 0 not in root or len(root[0]) != 4: return False
        for k, v in root.items():
            if k == 0: continue
            it = try_record(v)
            if it is None: return False
            it = dict(it)
            if 1 in it and len(it[1]) != 4: return False
            if 0 in it and it[0] != b'\x00':
                t = try_record(it[0])
                if not t or 2 not in dict(t): return False
        return True

    def is_replacer(self, b):
        r = try_record(b)
        return r is not None and any(f == 0x401604 for f, _ in r)

    def items(self, d):
        keys = sorted(d)
        return ('l', [self.guess(d[k]) for k in keys])

    def tagset(self, b):
        r = try_record(b)
        if not r: return None
        d = dict(r)
        if 0 not in d or len(d[0]) != 4: return None
        return self.tagset_items(d, 0)

    def tagset_items(self, d, k0):
        names = []
        for k in sorted(d):
            if k == k0: continue
            t = try_record(d[k])
            if not t: return None
            t = dict(t)
            if 2 not in t or rtext(t[2]) is None: return None
            names.append(rtext(t[2]))
        return ('t', tuple(sorted(names)))

    def link(self, d):
        name = rtext(d.get(23)) if 23 in d else None
        ident = None
        if 22 in d and len(d[22]) == 8: ident = idhex(d[22])
        elif 24 in d and len(d[24]) == 8: ident = idhex(d[24])
        target = rtext(d.get(20)) if 20 in d else None
        if name is None: name = target or ident or '?'
        return ('r', name, ident or target)

    def guess_class(self, fids):
        if fids in self._guess: return self._guess[fids]
        best = None; bs = None
        fs = set(fids)
        for cid, v in self.bare:
            own = set(v['f'])
            if fs <= own:
                score = len(own) - len(fs)
                if bs is None or score < bs: best, bs = cid, score
        self._guess[fids] = best
        return best

def render(node, ind=0, out=None):
    """indented text of a node, for debugging and for script diffs"""
    if out is None: out = []
    p = '    ' * ind
    k = node[0]
    if k == 'o':
        for lab, own, v in node[2]:
            if v[0] in ('o', 'l') and (v[1] if v[0] == 'l' else v[2]):
                out.append(p + lab + (' <%s>' % v[1] if v[0] == 'o' and v[1] else '') + ':')
                render(v, ind + 1, out)
            else:
                out.append(p + lab + ' = ' + short(v))
    elif k == 'l':
        for i, v in enumerate(node[1]):
            if v[0] in ('o', 'l') and (v[1] if v[0] == 'l' else v[2]):
                out.append(p + '[%d]' % i + (' <%s>' % v[1] if v[0] == 'o' and v[1] else '') + ':')
                render(v, ind + 1, out)
            else:
                out.append(p + '[%d] = ' % i + short(v))
    else:
        out.append(p + short(node))
    return out

def short(v):
    k = v[0]
    if k == 'v': return v[1]
    if k == 'r': return repr(v[1])
    if k == 't': return '{' + ', '.join(v[1]) + '}'
    if k == 'f': return '<tag filter: ' + '; '.join(v[1]) + '>'
    if k == 'x': return '<%d B undecoded>' % len(v[1])
    if k == 'o': return '{%s}' % (v[1] + ', ' if v[1] else '') + ('%d fields' % len(v[2]) if v[2] else 'empty')
    if k == 'l': return '[%d items]' % len(v[1])
    return str(v)
