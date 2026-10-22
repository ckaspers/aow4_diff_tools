"""Tooltip keys: does each text's {placeholder} have something to fill it, and does the key list
hold keys the text no longer uses?

A tooltip is a TELocalizedStringLink: a text id (msgid) plus a list of key/value replacements.
The game fills a {placeholder} from a key with a value typed in, or else from a data store: the
DataStoreTag of this resource or of a resource it links to ({TAG>Field}). GLOBAL. and LOC. are
global and always resolve.
"""
import os, re, struct, ast

def read_mo(path):
    b = open(path, 'rb').read()
    magic, rev, n, oo, ot = struct.unpack_from('<IIIII', b)
    out = {}
    for i in range(n):
        l1, o1 = struct.unpack_from('<II', b, oo + 8 * i); l2, o2 = struct.unpack_from('<II', b, ot + 8 * i)
        out[b[o1:o1 + l1].decode('utf-8', 'replace')] = b[o2:o2 + l2].decode('utf-8', 'replace')
    return out

def read_po(path):
    out = {}; cur = None; buf = {}
    def unq(s):
        try: return ast.literal_eval(s)
        except Exception: return s[1:-1]
    def flush():
        if buf.get('id'): out[buf['id']] = buf.get('str', '')
    for line in open(path, encoding='utf-8', errors='replace'):
        line = line.strip()
        if line.startswith('msgid '): flush(); buf = {'id': unq(line[6:]), 'str': ''}; cur = 'id'
        elif line.startswith('msgstr '): buf['str'] = unq(line[7:]); cur = 'str'
        elif line.startswith('"') and cur: buf[cur] = buf.get(cur, '') + unq(line)
        elif not line: cur = None
    flush()
    return out

def load_texts(game, mod_dir):
    """msgid -> English text: the mod's .po files over the game's EN.MO.
    A .po named after a game language (EN.po, DE.po, ...) is a copy of the game's own text made
    for reference, not loaded by the game and stale after an update: it is skipped."""
    txt = {}
    p = os.path.join(game, 'Language', 'EN', 'EN.MO')
    if os.path.exists(p): txt.update(read_mo(p))
    game_langs = set()
    if os.path.isdir(os.path.join(game, 'Language')):
        game_langs = {d.upper() for d in os.listdir(os.path.join(game, 'Language'))}
    lang = os.path.join(mod_dir, 'Language')
    if os.path.isdir(lang):
        for root, _, files in os.walk(lang):
            for f in files:
                base, ext = os.path.splitext(f)
                if ext.lower() == '.po' and base.upper() not in game_langs:
                    txt.update(read_po(os.path.join(root, f)))
    return txt

def unq(v):
    if not v or v[0] != 'v' or v[1] is None: return ''
    s = v[1]
    if len(s) >= 2 and s[0] == s[-1] and s[0] in '\'"':
        try: return ast.literal_eval(s)
        except Exception: return s[1:-1]
    return s

def links(node, path=()):
    """every tooltip in a decoded resource: (field path, msgid, [(key, value)])"""
    out = []
    def walk(n, p):
        k = n[0]
        if k == 'o':
            if n[1] == 'TELocalizedStringLink':
                f = {lab: v for lab, own, v in n[2]}
                keys = []
                rep = f.get('replacements')
                if rep and rep[0] == 'l':
                    for it in rep[1]:
                        if it[0] != 'o': continue
                        g = {lab: v for lab, own, v in it[2]}
                        key = unq(g.get('key', ('v', None)))
                        if key: keys.append((key, unq(g.get('value', ('v', None)))))
                out.append((' / '.join(p), unq(f.get('msgid', ('v', None))), keys))
                return
            if n[1] and n[1].startswith('TTrigger'): return   # inside a trigger script: the script supplies the values
            for lab, own, v in n[2]: walk(v, p + (lab,))
        elif k == 'l':
            for i, v in enumerate(n[1]): walk(v, p + ('[%d]' % i,))
    walk(node, tuple(path))
    return out

def tags_and_refs(node):
    """DataStoreTag values and referenced resource ids anywhere in a decoded resource"""
    tags, refs = set(), set()
    def walk(n):
        k = n[0]
        if k == 'o':
            for lab, own, v in n[2]:
                if lab == 'DataStoreTag':
                    t = unq(v)
                    if t: tags.add(t)
                else: walk(v)
        elif k == 'l':
            for v in n[1]: walk(v)
        elif k == 'r' and n[2]:
            refs.add(str(n[2]).strip("'").upper())
    walk(node)
    return tags, refs

PH = re.compile(r'\{([^{}]+)\}')
def root_of(ph):
    return re.split(r'[>.]', ph, 1)[0]

def check(lks, txt):
    """-> (leftover [(path, key, value)], needs [(path, placeholder)]) for the tooltips of one resource;
    needs = placeholders that only a data store can fill (no GLOBAL./LOC., no typed-in value)"""
    left, needs = [], []
    for path, msgid, keys in lks:
        t = txt.get(msgid)
        if t is None: continue
        used = set('{%s}' % x for x in PH.findall(t))
        for k, v in keys:
            if k not in used: left.append((path, k, v))
        typed = {k for k, v in keys if v}
        for ph in sorted(used):
            if ph in typed: continue
            r = root_of(ph[1:-1])
            if r in ('GLOBAL', 'LOC'): continue
            needs.append((path, ph))
    return left, needs

CAPS = re.compile(r'^[A-Z0-9_]+$')

def existing_tags(roots, pack_paths, extra_bytes=()):
    """which of these ALL-CAPS names are stored as a string anywhere (a DataStoreTag, normally)"""
    roots = set(roots); found = set()
    if not roots: return found
    pat = re.compile(b'|'.join(re.escape(struct.pack('<I', len(r)) + r.encode()) for r in sorted(roots)))
    def scan(b):
        for m in pat.finditer(b): found.add(m.group(0)[4:].decode())
    for b in extra_bytes: scan(b)
    for p in pack_paths:
        if len(found) == len(roots): break
        try:
            if os.path.getsize(p) > 8_000_000: continue
            scan(open(p, 'rb').read())
        except OSError: pass
    return found

def run(D, items, txt, gtxt, pack_paths, extra_bytes=()):
    """items: (row, mod raw, live raw or None); row = dict with the record's n/k/t/a/b
    -> {'leftover': [...], 'missing': [...]}, one entry per resource and tooltip field"""
    left_out, miss_out, pending = [], [], []
    for row, raw, live in items:
        try:
            left, needs = check(links(D.resource(raw)[1]), txt)
        except Exception:
            continue
        g_left, g_needs = set(), set()
        if live is not None:
            try:
                gl, gn = check(links(D.resource(live)[1]), gtxt)
                g_left = {k for p, k, v in gl}; g_needs = {ph for p, ph in gn}
            except Exception:
                pass
        by = {}
        for p, k, v in left:
            by.setdefault(p, []).append({'key': k, 'v': v, 'g': int(k in g_left)})
        for p, ks in by.items():
            left_out.append(dict(row, p=p, keys=ks))
        by = {}
        for p, ph in needs:
            r = root_of(ph[1:-1])
            why = 'store' if CAPS.match(r) else 'nokey'   # a data store name, or one only a typed-in key fills
            by.setdefault(p, []).append({'ph': ph, 'r': r, 'why': why, 'g': int(ph in g_needs)})
        for p, phs in by.items():
            pending.append(dict(row, p=p, phs=phs))
    found = existing_tags({x['r'] for e in pending for x in e['phs'] if x['why'] == 'store'}, pack_paths, extra_bytes)
    for e in pending:
        keep = []
        for x in e['phs']:
            if x['why'] == 'store':
                if x['r'] in found: continue        # the store exists; the game links it at run time
                x['why'] = 'nostore'
            keep.append({'ph': x['ph'], 'why': x['why'], 'g': x['g']})
        if keep: e['phs'] = keep; miss_out.append(e)
    return {'leftover': left_out, 'missing': miss_out}
