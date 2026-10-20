"""Compare two decoded resource trees and describe every difference the way the report shows it."""
import re
from decode import short

FACETS = ['value', 'reference', 'tags', 'structure', 'script', 'undecoded']
F_VALUE, F_REF, F_TAGS, F_STRUCT, F_SCRIPT, F_UNDEC = range(6)

# differences nobody needs to act on: counted, not listed
COSMETIC_LABELS = {'Name', 'ResourceDevInfo', 'LockedID', 'OldResourceID'}
SCRIPT_CLASSES = re.compile(r'^T(Trigger|Script)')

def is_empty(n):
    k = n[0]
    if k == 'o': return not n[2]
    if k == 'l': return not n[1]
    if k in ('t', 'f'): return not n[1]
    if k == 'v':
        if len(n) > 2 and n[2] is not None: return n[1] == n[2]        # equal to the field's default
        return n[1] in ("''", '0', 'false', '0.0', '(0.0, 0.0, 0.0)', '(0.0, 0.0)')
    if k == 'r': return False
    return False

def describe(n):
    k = n[0]
    if k == 'v': return n[1]
    if k == 'r': return repr(n[1])
    if k == 't': return '{' + ', '.join(n[1]) + '}'
    if k == 'f': return '{tag filter, %d conditions}' % len(n[1])
    if k == 'x': return '(%d B undecoded)' % len(n[1])
    if k == 'o':
        cnt = len(n[2])
        flds = '%d field%s' % (cnt, '' if cnt == 1 else 's')
        if n[1] == 'TEStringKeyItem':
            kv = dict((l, v) for l, _, v in n[2])
            parts = []
            if 'key' in kv: parts.append('key: ' + short(kv['key']))
            if 'value' in kv: parts.append('value: ' + short(kv['value']))
            return '{' + ', '.join(parts) + '}'
        return '{' + (n[1] + ', ' if n[1] else '') + flds + '}'
    if k == 'l':
        return '[%d item%s]' % (len(n[1]), '' if len(n[1]) == 1 else 's')
    return str(n)

def item_key(n):
    """an identity for a list item, so reordering is not a change"""
    k = n[0]
    if k == 'r': return ('r', n[2] or n[1])
    if k == 'v': return ('v', n[1])
    if k == 'o':
        d = {}
        for lab, own, v in n[2]:
            d.setdefault(lab, v)
        if n[1] == 'TEResourceLLI' or ('ID' in d and 'UserName' in d):
            return ('id', d.get('ID', ('', ''))[1])
        if 'key' in d and d['key'][0] == 'v': return ('key', d['key'][1])
        for lab in ('Name', 'UserName', 'ID', 'Key', 'Resource', 'Link', 'Type'):
            v = d.get(lab)
            if v is not None and v[0] in ('v', 'r'):
                return (n[1], lab, v[1] if v[0] == 'v' else (v[2] or v[1]))
        # first reference in the item
        for lab, own, v in n[2]:
            if v[0] == 'r': return (n[1], lab, v[2] or v[1])
    return None

def key_label(key, n):
    if key is None: return None
    if key[0] == 'key': return key[1].strip("'")
    if key[0] in ('id',):
        for lab, own, v in n[2]:
            if lab == 'UserName' and v[0] == 'v': return v[1].strip("'")
        return key[1]
    if key[0] == 'r': return n[1]
    if key[0] == 'v': return key[1]
    if n[0] == 'o':
        for lab, own, v in n[2]:
            if lab == key[1]:
                return (v[1] if v[0] == 'r' else v[1]).strip("'") if v[0] in ('v', 'r') else str(key[2])
    return str(key[-1])

class Diff:
    def __init__(self):
        self.changes = []      # dicts: f, d, p, tp, o?, n?, m?
        self.cosmetic = 0
        self.script = []       # unified diff lines of the resource's scripts

    def add(self, f, head, p, tp, o=None, n=None, mig=False, extra=None):
        c = {'f': f, 'd': [head] + (extra or []), 'p': p, 'tp': tp}
        if o is not None: c['o'] = o; c['n'] = n
        if mig: c['m'] = 1
        self.changes.append(c)

def join(a, b): return b if not a else a + ' / ' + b

def compare(old, new, diff, p='', tp='', label=None, owner=None):
    """old/new: nodes (either may be None)"""
    if old == new: return
    if label in COSMETIC_LABELS and p.count(' / ') == 0:
        diff.cosmetic += 1; return
    if old is None or new is None:
        node = new if old is None else old
        sign = '+' if old is None else '-'
        if is_empty(node):
            diff.add(F_STRUCT, sign + ' (empty)', p, tp, mig=True)
            return
        if node[0] == 'v' and len(node) > 2 and node[2] is not None:
            # a field not stored has its default value: that is what the other side holds
            o, n = (node[2] + ' (default)', node[1]) if old is None else (node[1], node[2] + ' (default)')
            diff.add(F_VALUE, '%s -> %s' % (o, n), p, tp, o=o, n=n)
            return
        k = node[0]
        f = F_REF if k == 'r' else F_TAGS if k == 't' else F_VALUE if k == 'v' else F_UNDEC if k == 'x' else F_STRUCT
        if k == 'l' and node[1] and all(x[0] == 'r' for x in node[1]):
            for x in node[1]: diff.add(F_REF, sign + ' ' + repr(x[1]), p, tp)
            return
        if k == 't':
            for t in node[1]: diff.add(F_TAGS, sign + ' ' + t, p, tp)
            return
        if k == 'f':
            diff.add(F_TAGS, sign + ' tag filter', p, tp, extra=['  ' + sign + ' ' + l for l in node[1]])
            return
        diff.add(f, sign + ' ' + describe(node), p, tp)
        return
    ko, kn = old[0], new[0]
    if ko != kn and {ko, kn} == {'o', 'r'} and is_empty(old if ko == 'o' else new):
        if ko == 'o': diff.add(F_REF, '+ ' + repr(new[1]), p, tp)
        else: diff.add(F_REF, '- ' + repr(old[1]), p, tp)
        return
    if ko != kn:
        if is_empty(old) and is_empty(new):
            diff.cosmetic += 1; return
        diff.add(F_STRUCT, 'changed', p, tp, extra=['  - ' + describe(old), '  + ' + describe(new)])
        return
    if ko == 'v':
        diff.add(F_VALUE, '%s -> %s' % (old[1], new[1]), p, tp, o=old[1], n=new[1]); return
    if ko == 'r':
        if (old[2] or old[1]) == (new[2] or new[1]):
            diff.cosmetic += 1; return        # same target, drifted display name
        diff.add(F_REF, '%r -> %r' % (old[1], new[1]), p, tp, o=repr(old[1]), n=repr(new[1])); return
    if ko == 't':
        a, b = set(old[1]), set(new[1])
        for t in sorted(b - a): diff.add(F_TAGS, '+ ' + t, p, tp)
        for t in sorted(a - b): diff.add(F_TAGS, '- ' + t, p, tp)
        return
    if ko == 'f':
        import difflib
        lines = [l for l in difflib.unified_diff(list(old[1]), list(new[1]), lineterm='', n=1)][2:]
        extra = ['  ' + (l if l[:1] in '+-' else ' ' + l[1:] if l[:1] == ' ' else '  ' + l) for l in lines if not l.startswith('@@')]
        diff.add(F_TAGS, 'tag filter changed', p, tp, extra=extra)
        return
    if ko == 'x':
        diff.add(F_UNDEC, 'changed (%d B -> %d B, undecoded)' % (len(old[1]), len(new[1])), p, tp); return
    if ko == 'o' and (old[1] == 'TTriggerSettings' or new[1] == 'TTriggerSettings'):
        compare_script(old, new, diff, p, tp)
        return
    if ko == 'o':
        if old[1] != new[1] and old[1] and new[1]:
            diff.add(F_STRUCT, 'changed', p, tp, extra=['  - ' + describe(old), '  + ' + describe(new)])
            return
        cls = new[1] or old[1]
        base_tp = (join(tp, cls) if cls else tp) if tp else ''
        def keyed(fields):
            seen = {}; out = {}; order = []
            for lab, own, v in fields:
                i = seen.get(lab, 0); seen[lab] = i + 1
                k = (lab, i)
                out[k] = (own, v); order.append(k)
            return out, order
        a, ao = keyed(old[2]); b, bo = keyed(new[2])
        order = bo + [k for k in ao if k not in b]
        for k in order:
            lab = k[0] if k[1] == 0 else '%s#%d' % (k[0], k[1] + 1)
            own = (b.get(k) or a.get(k))[0]
            ov = a.get(k, (None, None))[1]; nv = b.get(k, (None, None))[1]
            sub_tp = join(base_tp, lab) if tp else join(own or cls or '', lab)
            compare(ov, nv, diff, join(p, lab), sub_tp, lab, own)
        return
    if ko == 'l':
        compare_list(old[1], new[1], diff, p, tp)

def compare_list(a, b, diff, p, tp):
    # references: a set
    if a + b and all(x[0] == 'r' for x in a + b):
        from collections import Counter
        ca = Counter((x[2] or x[1]) for x in a); cb = Counter((x[2] or x[1]) for x in b)
        nm = {(x[2] or x[1]): x[1] for x in a + b}
        if ca == cb:
            if [x[2] for x in a] != [x[2] for x in b]: diff.cosmetic += 1
            return
        for k in (cb - ca).elements(): diff.add(F_REF, '+ ' + repr(nm[k]), p, tp)
        for k in (ca - cb).elements(): diff.add(F_REF, '- ' + repr(nm[k]), p, tp)
        return
    if all(x[0] == 'v' for x in a + b):
        from collections import Counter
        ca, cb = Counter(x[1] for x in a), Counter(x[1] for x in b)
        if ca == cb: 
            diff.cosmetic += 1; return
        for v in sorted((cb - ca).elements()): diff.add(F_VALUE, '+ ' + v, p, tp)
        for v in sorted((ca - cb).elements()): diff.add(F_VALUE, '- ' + v, p, tp)
        return
    ka = [item_key(x) for x in a]; kb = [item_key(x) for x in b]
    usable = all(k is not None for k in ka + kb) and len(set(ka)) == len(ka) and len(set(kb)) == len(kb)
    if usable:
        ia = dict(zip(ka, a)); ib = dict(zip(kb, b))
        for k in kb + [k for k in ka if k not in ib]:
            x = ib.get(k) or ia.get(k)
            lab = '[%s]' % key_label(k, x)
            compare(ia.get(k), ib.get(k), diff, p + ' ' + lab, tp + ' ' + lab)
        return
    import difflib
    sm = difflib.SequenceMatcher(None, [repr(x) for x in a], [repr(x) for x in b], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal': continue
        n = max(i2 - i1, j2 - j1)
        for t in range(n):
            x = a[i1 + t] if i1 + t < i2 else None
            y = b[j1 + t] if j1 + t < j2 else None
            i = j1 + t if y is not None else i1 + t
            compare(x, y, diff, p + ' [%d]' % i, tp + ' [%d]' % i)

def strip_positions(path):
    return re.sub(r' \[[^\]]*\]', '', path)

def compare_script(old, new, diff, p, tp):
    from script import SECTIONS, section_diff
    fo = {l: v for l, o, v in old[2]}; fn = {l: v for l, o, v in new[2]}
    rest_o = ('o', old[1], [x for x in old[2] if x[0] not in SECTIONS])
    rest_n = ('o', new[1], [x for x in new[2] if x[0] not in SECTIONS])
    compare(rest_o, rest_n, diff, p, tp)
    for sec in SECTIONS:
        kind, lines = section_diff(sec, fo.get(sec), fn.get(sec))
        if kind is None: continue
        sp = join(p, sec); stp = join(tp or 'TTriggerSettings', sec)
        if kind == 'restamped':
            diff.add(F_SCRIPT, 'node ids re-stamped, script identical', sp, stp)
            diff.changes[-1]['z'] = 1
        else:
            diff.add(F_SCRIPT, 'script changed', sp, stp)
            if lines:
                if diff.script: diff.script.append('')
                diff.script += lines
