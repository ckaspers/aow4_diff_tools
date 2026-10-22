#!/usr/bin/env python3
"""AoW4 pack diff: compare game builds, or a mod's overrides against the live game, as HTML reports.

  python aow4diff.py after-update      the whole routine after a game update (see below)
  python aow4diff.py mod      [--acp <mod>.acp]          every mod in the Mods folder, or one
  python aow4diff.py update   [--old <snapshot folder>]   the latest snapshot against the live game
  python aow4diff.py snapshot [--label NAME]              keep a copy of the game data for next time

after-update: refreshes schema_db.json when ResourceED.exe changed, writes the update report
(latest snapshot -> live), writes a report per mod, then snapshots the live game for next time.

Paths come from config.json next to this script (written on first run; edit it if a path is wrong).
Only the Python standard library is needed, except extract_schema.py (capstone or objdump).
"""
import argparse, datetime, hashlib, json, os, re, shutil, struct, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from aow4lib import parse_record, try_record, rtext, Schema
from decode import Decoder
from differ import Diff, compare, FACETS, strip_positions
import acpread
import tooltips
import stale

# ---- reading packs -----------------------------------------------------------------
def pack_items(b):
    """-> (resource raws, dependency names) for one .rpk"""
    if b[:4] != b'RPK\0': return [], []
    top = dict(parse_record(b, 0xb0))
    out, deps = [], []
    lists = top.get(13)
    if not lists: return out, deps
    for fid, cat in parse_record(lists):
        if len(cat) < 5: continue
        r = dict(parse_record(cat, 4))
        lib = r.get(51)
        if not lib or lib == b'\x00': continue
        lib = dict(parse_record(lib))
        for key in (22, 26):
            if key in lib: deps += strlist(lib[key])
        lst = lib.get(21)
        if not lst or lst == b'\x00': continue
        lst = dict(parse_record(lst)).get(1)
        if not lst or lst == b'\x00': continue
        for idx, item in parse_record(lst):
            if len(item) >= 5: out.append(bytes(item))
    return out, deps

def strlist(b):
    try:
        n = struct.unpack_from('<I', b)[0]; p = 4; out = []
        for _ in range(n):
            L = struct.unpack_from('<I', b, p)[0]; p += 4
            out.append(b[p:p + L].decode('utf-8', 'replace')); p += L
        return out
    except Exception:
        return []

def res_head(raw):
    """(class id, 16-hex id, user name) of a resource without decoding it"""
    cid = struct.unpack_from('<I', raw)[0]
    rid, name = None, None
    if len(raw) > 5:
        r = try_record(raw[4:])
        if r:
            d = dict(r)
            if 24 in d and len(d[24]) == 8:
                lo, hi = struct.unpack_from('<II', d[24]); rid = '%08X%08X' % (hi, lo)
            name = rtext(d.get(21)) if 21 in d else None
    return cid, rid, name

def scan_game(content):
    """{relative pack path: absolute path} for every .rpk under Content"""
    out = {}
    for root, dirs, files in os.walk(content):
        for f in files:
            if f.lower().endswith('.rpk'):
                full = os.path.join(root, f)
                out[os.path.relpath(full, content).replace(os.sep, '/')] = full
    return out

def scan_acps(content):
    out = {}
    for root, dirs, files in os.walk(content):
        for f in files:
            if f.lower().endswith('.acp'):
                full = os.path.join(root, f)
                out[os.path.relpath(full, content).replace(os.sep, '/')] = full
    return out

def content_dir(game):
    c = os.path.join(game, 'Content')
    return c if os.path.isdir(c) else game

def build_label(game):
    """the exe's date: stable whatever can be read, and the game rarely updates twice a day"""
    for exe in ('AOW4.exe', 'ResourceED.exe'):
        p = os.path.join(game, exe)
        if os.path.exists(p):
            return 'exe of ' + datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m-%d')
    return 'unknown build'

def name_index(packs, cache_dir=None):
    """resource id -> name over every pack, for references that carry only an id"""
    key = hashlib.sha1(json.dumps(sorted((r, os.path.getsize(p), int(os.path.getmtime(p))) for r, p in packs.items())).encode()).hexdigest()[:16]
    cache = os.path.join(cache_dir or HERE, 'cache', 'names_%s.json' % key)
    if os.path.exists(cache):
        return json.load(open(cache))
    names = {}
    for rel, full in packs.items():
        if os.path.getsize(full) > 8_000_000: continue      # texture and effect banks: no game data
        try:
            items, _ = pack_items(open(full, 'rb').read())
        except Exception:
            continue
        for raw in items:
            cid, rid, name = res_head(raw)
            if rid and name: names[rid] = name
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        json.dump(names, open(cache, 'w'))
        for f in os.listdir(os.path.dirname(cache)):
            if f.startswith('names_') and f != os.path.basename(cache):
                try: os.remove(os.path.join(os.path.dirname(cache), f))
                except OSError: pass
    except Exception:
        pass
    return names

# ---- the report model ------------------------------------------------------------
class Model:
    def __init__(self, schema, restamp_cosmetic=False):
        self.S = schema
        self.restamp_cosmetic = restamp_cosmetic
        self.D = Decoder(schema)
        self.packs = []; self.pack_ix = {}
        self.types = []; self.type_ix = {}
        self.sigs = []; self.sig_ix = {}
        self.migs = []; self.mig_ix = {}
        self.records = []
        self.came = []; self.went = []; self.recreated = []
        self.new_packs = []; self.dead_packs = []
        self.deps = []; self.acps = []; self.notes = []

    def pk(self, path):
        if path not in self.pack_ix:
            self.pack_ix[path] = len(self.packs); self.packs.append(path)
        return self.pack_ix[path]

    def ty(self, cid):
        c = self.S.cls.get(cid)
        label = (c.get('l') or c['n']) if c else 'class_%x' % cid
        if label not in self.type_ix:
            self.type_ix[label] = len(self.types); self.types.append(label)
        return self.type_ix[label]

    def row(self, pack, cid, rid, name):
        a, b = (int(rid[:8], 16), int(rid[8:], 16)) if rid else (None, None)
        return [self.pk(pack), name or '(unnamed)', self.ty(cid), a, b]

    def diff_record(self, pack, old_raw, new_raw, extra=None):
        cid_o, node_o = self.D.resource(old_raw)
        cid_n, node_n = self.D.resource(new_raw)
        d = Diff()
        compare(node_o, node_n, d)
        cid, rid, name = res_head(new_raw)
        a, b = (int(rid[:8], 16), int(rid[8:], 16)) if rid else (None, None)
        r = {'k': self.pk(pack), 'n': name or '(unnamed)', 't': self.ty(cid), 'a': a, 'b': b, 'c': []}
        opaque = False
        if self.restamp_cosmetic:
            n0 = len(d.changes)
            d.changes = [c for c in d.changes if not c.get('z')]
            d.cosmetic += n0 - len(d.changes)
        for c in d.changes:
            if c.get('m'):
                sign = c['d'][0][0]
                key = (c['p'], sign, c['tp'])
                if key not in self.mig_ix:
                    self.mig_ix[key] = len(self.migs); self.migs.append([c['p'], sign, c['tp']])
                c['h'] = self.mig_ix[key]
            else:
                key = (strip_positions(c['p']), c['d'][0], strip_positions(c['tp']))
                if key not in self.sig_ix:
                    self.sig_ix[key] = len(self.sigs); self.sigs.append(list(key))
                c['g'] = self.sig_ix[key]
            if c['f'] == 5: opaque = True
            if not c['tp'] or c['tp'] == c['p']: c.pop('tp')
            r['c'].append(c)
        if d.cosmetic: r['x'] = d.cosmetic
        if d.script: r['s'] = d.script
        if opaque: r['q'] = 1
        if extra: r.update(extra)
        self.records.append(r)
        return r

    def json(self, **kw):
        M = dict(kw)
        M.update(facets=FACETS, types=self.types, packs=self.packs, sigs=self.sigs, migs=self.migs,
                 records=self.records, came=self.came, went=self.went, recreated=self.recreated,
                 newPacks=self.new_packs, deadPacks=self.dead_packs, deps=self.deps, acps=self.acps,
                 notes=self.notes)
        M.setdefault('generated', datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
        M.setdefault('registry', None)
        M.setdefault('words', {})
        return M

def write_html(M, out):
    t = open(os.path.join(HERE, 'template.html'), encoding='utf-8').read()
    data = json.dumps(M, separators=(',', ':'), ensure_ascii=False).replace('</', '<\\/')
    open(out, 'w', encoding='utf-8').write(t.replace('__DATA__', data, 1))

def load_schema(path=None):
    S = Schema(path or os.path.join(HERE, 'schema_db.json'))
    import script
    script.REG.clear(); script.REG.update(S.trigger)
    return S

# ---- mod check ---------------------------------------------------------------------
MOD_WORDS = {
    "title": "AoW4 mod overrides", "tool": "aow4diff.py mod", "noModCheck": True, "noRegistry": "",
    "tileBreaking": "deleted by the mod", "tileAdded": "overrides with no target",
    "tileChanged": "overrides differ from live", "tileSchema": "layout-only differences",
    "schema": "Layout-only differences", "schemaNav": "Layout", "schemaSmall": " \u00b7 no effect in game",
    "tileSystemic": "repeated differences", "tilePacks": "overridden packs hold a difference",
    "breaking": "Deleted by the mod",
    "breakingLead": "Resources the mod removes from the game (Mark Deleted in the editor). Each is still in the live game under that id.",
    "removed": "Marked deleted", "new": "No target in the game", "added": "Override applies to nothing",
    "systemic": "Repeated differences", "minBulk": 10,
    "systemicLead": "Either a pass the mod made on purpose across many resources, or something the game changed after these overrides were made, which the copies now undo. The second kind reads reversed: the copy sets the old value. A difference you do not recognise as your own is the one to look at.",
    "changed": "Overrides",
    "schemaLead": "Fields that are at their default value on both sides, but are written out in one file and left out of the other. "
                  "The game reads both the same, so nothing here changes how your mod plays. "
                  "+ means your copy stores the field and the game's version doesn't; \u2212 means the reverse. "
                  "It usually means the game added or removed the field after you made your copy, or the editor saved the copy differently.",
}

def cmd_mod(args):
    S = load_schema(args.schema)
    game = args.game
    content = content_dir(game)
    packs = scan_game(content)
    by_base = {}
    for rel in packs:
        by_base.setdefault(os.path.basename(rel).upper(), []).append(rel)
    acp = open(args.acp, 'rb').read()
    modname, ovs = acpread.mod_overrides(acp)
    print('%s: %d overrides; reading resource names from %d game packs' % (modname, len(ovs), len(packs)), flush=True)
    M = Model(S, restamp_cosmetic=True)
    M.D.names = name_index(packs)
    print('comparing the overrides with the live game', flush=True)
    live_label = 'live (%s)' % build_label(game)
    cache = {}
    def live(rel):
        if rel not in cache:
            items, deps = pack_items(open(packs[rel], 'rb').read())
            cache[rel] = {res_head(x)[1]: x for x in items}
        return cache[rel]
    identical = 0; differ_packs = set(); compared = set(); no_pack = 0
    tips = []                                   # (row, mod raw, live raw) for the tooltip check
    def tip(rel, raw, live_raw):
        cid, rid, name = res_head(raw)
        a, b = (int(ov['id'][:8], 16), int(ov['id'][8:], 16))
        tips.append(({'n': name or ov['name'] or '(unnamed)', 'k': M.pk(rel), 't': M.ty(cid), 'a': a, 'b': b}, raw, live_raw))
    t0 = time.time()
    for ov in ovs:
        rels = by_base.get(ov['pack'].upper(), [])
        rel = None
        for r in rels:
            if ov['id'] in live(r): rel = r; break
        if rel is None and rels: rel = rels[0]
        if rel is None:
            no_pack += 1
            raw = ov['raw']
            if raw: tip(ov['pack'], raw, None)
            cid, rid, name = res_head(raw) if raw else (0, ov['id'], None)
            M.came.append(M.row(ov['pack'], cid, ov['id'], name or ov['name']))
            continue
        compared.add(rel)
        target = live(rel).get(ov['id'])
        if ov['raw'] is None:
            if target is not None:
                cid, rid, name = res_head(target)
                M.went.append(M.row(rel, cid, rid, name))
                differ_packs.add(rel)
            continue
        tip(rel, ov['raw'], target)
        if target is None:
            cid, rid, name = res_head(ov['raw'])
            M.came.append(M.row(rel, cid, ov['id'], name))
            continue
        if target == ov['raw']:
            identical += 1; continue
        r = M.diff_record(rel, target, ov['raw'])
        if any(not c.get('m') for c in r['c']): differ_packs.add(rel)
    # say why an override has no target: moved to another pack, or re-created under a new id
    if M.came:
        print('looking up %d override%s with no target' % (len(M.came), 's' if len(M.came) != 1 else ''), flush=True)
        where = {}; byname = {}
        for rel, full in packs.items():
            if os.path.getsize(full) > 8_000_000: continue
            for raw in pack_items(open(full, 'rb').read())[0]:
                cid, rid, name = res_head(raw)
                if rid: where[rid] = rel
                if name: byname.setdefault(name, []).append((rid, rel))
        for x in M.came:
            rid = '%08X%08X' % (x[3], x[4]) if x[3] is not None else None
            if rid in where:
                x[1] += '  (moved: now in %s)' % where[rid]
            elif x[1] in byname:
                nid, nrel = byname[x[1]][0]
                x[1] += '  (re-created: now id %s%s)' % (nid, '' if nrel == M.packs[x[0]] else ' in ' + nrel)
    print('checking tooltip keys', flush=True)
    txt = tooltips.load_texts(game, os.path.dirname(os.path.abspath(args.acp)))
    gtxt = tooltips.load_texts(game, os.path.join(game, '_no_mod_'))
    mod_packs = [os.path.join(r, f) for r, _, fs in os.walk(os.path.dirname(os.path.abspath(args.acp)))
                 for f in fs if f.lower().endswith('.rpk')]     # the mod's own new resources
    M.tips = tooltips.run(M.D, tips, txt, gtxt, mod_packs + list(packs.values()), [acp])
    print('looking for old content', flush=True)
    ids = stale.all_ids(packs, mod_packs, os.path.join(HERE, 'cache'), res_head, pack_items)
    M.stale = stale.run(M.D, tips, ids, {o['id'] for o in ovs if o['raw']})
    M.only = stale.only_in_copy(M.records)
    found = mark_unmerged(M, load_update_reports(getattr(args, 'history', None) or []))
    if found:
        M.notes.append('%d change%s the game made in earlier updates %s not in your copies yet: see "Not merged from game updates"'
                       % (len(found), 's' if len(found) != 1 else '', 'are' if len(found) != 1 else 'is'))
    M.notes.append("every difference is the mod's copy against the live resource: the mod's own edits "
                   "and whatever the game changed since the copy was made, not told apart")
    if identical: M.notes.append('%d override%s byte-identical to the live resource' % (identical, 's are' if identical != 1 else ' is'))
    if no_pack: M.notes.append('%d override%s a pack this game does not have' % (no_pack, 's target' if no_pack != 1 else ' targets'))
    data = M.json(old=live_label, new='%s (%d overrides)' % (modname, len(ovs)),
                  nCompared=len(compared), nDiffer=len(differ_packs),
                  allPacks=sorted(set(os.path.basename(p).upper() for p in compared)), words=MOD_WORDS,
                  tips=getattr(M, 'tips', None), stale=getattr(M, 'stale', None), only=getattr(M, 'only', None))
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.acp)),
                                   re.sub(r'[^\w.-]+', '_', modname) + '_vs_live.html')
    write_html(data, out)
    print('%d overrides, %d differ, %d identical, %d deleted, %d without target -> %s (%.0fs)' % (
        len(ovs), len(M.records), identical, len(M.went), len(M.came), out, time.time() - t0))

# ---- game changes from earlier update reports that a mod's copies never picked up -----
def load_update_reports(folders):
    """the data of every update report (not mod reports) in these folders, oldest build first"""
    out = []; seen = set()
    for d in folders:
        if not d or not os.path.isdir(d): continue
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith('.html'): continue
            p = os.path.join(d, f)
            try:
                s = open(p, encoding='utf-8', errors='replace').read()
                m = re.search(r'<script id="data" type="application/json">(.*?)</script>', s, re.S)
                if not m: continue
                M = json.loads(m.group(1))
            except Exception:
                continue
            if (M.get('words') or {}).get('noModCheck') or 'records' not in M: continue
            key = (M.get('old'), M.get('new'))
            if key in seen: continue
            seen.add(key); out.append(M)
    return out

def build_of(label):
    m = re.search(r'(\d{6,})', label or '')
    return m.group(1) if m else (label or '?')

def mark_unmerged(M, reports):
    """flag the changes in this mod report that undo a game change listed in an update report"""
    strip = lambda p: re.sub(r' \[[^\]]*\]', '', p)
    norm = lambda s: s.replace('"', "'")
    base = lambda packs, k: packs[k].rsplit('/', 1)[-1].upper()
    mine = {}
    for r in M.records:
        if r['a'] is not None: mine[(base(M.packs, r['k']), r['a'], r['b'])] = r
    found = []
    def span(U):
        try: return int(build_of(U.get('new'))) - int(build_of(U.get('old')))
        except ValueError: return 1 << 40
    for U in sorted(reports, key=span):          # the narrowest update first: it names when a change came
        label = '%s \u2192 %s' % (build_of(U.get('old')), build_of(U.get('new')))
        for r in U['records']:
            if r.get('a') is None: continue
            mr = mine.get((base(U['packs'], r['k']), r['a'], r['b']))
            if mr is None: continue
            for c in r['c']:
                if c.get('m') or c.get('z'): continue
                p = strip(c['p']); h = norm(c['d'][0])
                if h[:2] in ('+ ', '- '):
                    want = ('- ' if h[0] == '+' else '+ ') + h[2:]
                elif 'o' in c:
                    want = norm('%s -> %s' % (c['n'], c['o']))
                else:
                    continue
                for mc in mr['c']:
                    if strip(mc['p']) == p and norm(mc['d'][0]) == want and 'u' not in mc:
                        mc['u'] = label; found.append((mr['n'], mc['p'], label)); break
    return found

# ---- snapshots -------------------------------------------------------------------
def cmd_snapshot(args):
    game = args.game
    content = content_dir(game)
    label = args.label or re.sub(r'[^\w.-]+', '_', build_label(game).replace('build ', ''))
    dest = os.path.join(args.dest, label)
    if os.path.exists(os.path.join(dest, 'snapshot.json')) and not args.force:
        sys.exit('%s exists already (use --force to overwrite)' % dest)
    os.makedirs(dest, exist_ok=True)
    files = list(scan_game(content).items()) + list(scan_acps(content).items())
    n = 0; size = 0; t0 = time.time()
    for rel, full in files:
        target = os.path.join(dest, 'Content', rel)
        st = os.stat(full)
        size += st.st_size; n += 1
        if os.path.exists(target):       # resuming an interrupted snapshot
            tt = os.stat(target)
            if tt.st_size == st.st_size and int(tt.st_mtime) == int(st.st_mtime): continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(full, target)
    if os.path.exists(os.path.join(HERE, 'schema_db.json')):
        shutil.copy2(os.path.join(HERE, 'schema_db.json'), os.path.join(dest, 'schema_db.json'))
    info = {'label': build_label(game), 'taken': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'files': n}
    json.dump(info, open(os.path.join(dest, 'snapshot.json'), 'w'), indent=1)
    print('snapshot %s: %d files, %.0f MB (%.0fs)' % (dest, n, size / 1e6, time.time() - t0))

# ---- update diff -------------------------------------------------------------------
def registry_diff(old, new):
    oc = {k: v for k, v in old.items() if not k.startswith('_')}; nc = {k: v for k, v in new.items() if not k.startswith('_')}
    on = {v['n']: v for v in oc.values()}; nn = {v['n']: v for v in nc.values()}
    res = {'classes_new': [], 'classes_gone': [], 'fields': []}
    for n in sorted(set(nn) - set(on)): res['classes_new'].append([n, nn[n].get('l') or None])
    for n in sorted(set(on) - set(nn)): res['classes_gone'].append([n, on[n].get('l') or None])
    for n in sorted(set(on) & set(nn)):
        a, b = on[n]['f'], nn[n]['f']
        lab = nn[n].get('l') or None
        for fid in sorted(set(a) | set(b), key=int):
            fa = a.get(fid); fb = b.get(fid)
            na = fa[0] if fa else None; nb = fb[0] if fb else None
            if fa and not fb: res['fields'].append([n, lab, int(fid), '-', na, None])
            elif fb and not fa: res['fields'].append([n, lab, int(fid), '+', None, nb])
            elif na != nb: res['fields'].append([n, lab, int(fid), '~', na, nb])
    return res

def cmd_update(args):
    S = load_schema(args.schema)
    old_root = args.old
    old_content = content_dir(old_root)
    new_content = content_dir(args.game)
    info = {}
    if os.path.exists(os.path.join(old_root, 'snapshot.json')):
        info = json.load(open(os.path.join(old_root, 'snapshot.json')))
    old_label = 'snapshot %s (%s)' % (info.get('label', os.path.basename(old_root.rstrip('/\\'))), info.get('taken', '?')[:10])
    new_label = 'live (%s)' % build_label(args.game)
    old_packs = scan_game(old_content); new_packs = scan_game(new_content)
    M = Model(S)
    M.D.names = name_index(new_packs)
    t0 = time.time()
    all_rel = sorted(set(old_packs) | set(new_packs))
    n_differ = 0
    old_ids = {}; new_ids = {}     # rid -> (rel, raw) over all packs, for moves
    changed_packs = []
    for rel in all_rel:
        a = open(old_packs[rel], 'rb').read() if rel in old_packs else None
        b = open(new_packs[rel], 'rb').read() if rel in new_packs else None
        if a == b: continue
        n_differ += 1
        ia, da = pack_items(a) if a else ([], [])
        ib, db = pack_items(b) if b else ([], [])
        if a is None: M.new_packs.append(M.pk(rel))
        if b is None: M.dead_packs.append(M.pk(rel))
        if a is not None and b is not None and set(da) != set(db):
            M.deps.append([M.pk(rel), sorted(set(db) - set(da)), sorted(set(da) - set(db))])
        for x in ia: old_ids.setdefault(res_head(x)[1], []).append((rel, x))
        for x in ib: new_ids.setdefault(res_head(x)[1], []).append((rel, x))
        changed_packs.append(rel)
    # resources in packs that did not change take part in moves too
    def ids_in(packs_map, rel):
        items, _ = pack_items(open(packs_map[rel], 'rb').read())
        return {res_head(x)[1]: x for x in items}
    moved_from = {}
    for rid, lst in new_ids.items():
        for rel, raw in lst:
            olds = old_ids.get(rid, [])
            same = [x for x in olds if x[0] == rel]
            if same:
                if same[0][1] != raw:
                    M.diff_record(rel, same[0][1], raw)
            elif olds:
                orel, oraw = olds[0]
                still = any(r == orel for r, _ in lst)
                if not still:
                    r = M.diff_record(rel, oraw, raw, {'mv': M.pk(orel)})
                    moved_from[(orel, rid)] = rel
            else:
                cid, rid2, name = res_head(raw)
                M.came.append(M.row(rel, cid, rid, name))
    for rid, lst in old_ids.items():
        for rel, raw in lst:
            if any(r == rel for r, _ in new_ids.get(rid, [])): continue
            if (rel, rid) in moved_from: continue
            if rel in new_packs and rel not in changed_packs: continue
            cid, rid2, name = res_head(raw)
            M.went.append(M.row(rel, cid, rid, name))
    # re-created: same pack, name and type gone under one id and back under another
    came_key = {}
    for i, x in enumerate(M.came): came_key.setdefault((x[0], x[1], x[2]), []).append(i)
    keep_came = set(range(len(M.came))); keep_went = []
    for x in M.went:
        k = (x[0], x[1], x[2])
        hit = [i for i in came_key.get(k, []) if i in keep_came]
        if hit and len(came_key[k]) == 1:
            y = M.came[hit[0]]; keep_came.discard(hit[0])
            M.recreated.append([x[0], x[1], x[2], x[3], x[4], y[3], y[4]])
        keep_went.append(x)
    # the viewer subtracts re-created rows itself; keep both lists whole
    old_acps = scan_acps(old_content); new_acps = scan_acps(new_content)
    for rel in sorted(set(old_acps) | set(new_acps)):
        a = open(old_acps[rel], 'rb').read() if rel in old_acps else None
        b = open(new_acps[rel], 'rb').read() if rel in new_acps else None
        if a != b: M.acps.append(rel)
    registry = None
    osch = os.path.join(old_root, 'schema_db.json')
    if os.path.exists(osch):
        registry = registry_diff(json.load(open(osch)), json.load(open(args.schema or os.path.join(HERE, 'schema_db.json'))))
    else:
        M.notes.append('the snapshot holds no schema, so the exe registries were not compared')
    data = M.json(old=old_label, new=new_label, nCompared=len(all_rel), nDiffer=n_differ,
                  allPacks=sorted(set(os.path.basename(p).upper() for p in all_rel)), registry=registry)
    out = args.out or os.path.join(getattr(args, 'reports', None) or os.getcwd(), 'update_%s_to_%s.html' % (
        re.sub(r'\W+', '_', info.get('label', 'old')).strip('_'), re.sub(r'\W+', '_', build_label(args.game)).strip('_')))
    write_html(data, out)
    print('%d packs compared, %d differ, %d resources changed, %d added, %d removed -> %s (%.0fs)' % (
        len(all_rel), n_differ, len(M.records), len(M.came), len(M.went), out, time.time() - t0))

# ---- configuration -----------------------------------------------------------------
DEFAULTS = {
    'game': r'C:\Program Files (x86)\Steam\steamapps\common\Age of Wonders 4',
    'mods': os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming', 'Paradox Interactive', 'Age of Wonders 4', 'Mods'),
    'snapshots': os.path.join(HERE, 'snapshots'),
    'reports': os.path.join(HERE, 'reports'),
}

def resolve(path):
    """a Windows path from config.json, also when run in a Linux shell that mounts the folder by name"""
    if path: path = os.path.expandvars(path)      # %APPDATA% and the like
    if not path or os.path.exists(path): return path
    name = re.split(r'[\\/]', path.rstrip('\\/'))[-1]
    for cand in (os.path.join(os.path.expanduser('~'), 'mnt', name),):
        if os.path.exists(cand): return cand
    return path

def load_config():
    p = os.path.join(HERE, 'config.json')
    cfg = dict(DEFAULTS)
    if os.path.exists(p):
        cfg.update(json.load(open(p, encoding='utf-8')))
    else:
        json.dump(cfg, open(p, 'w', encoding='utf-8'), indent=2)
        print('wrote %s: check the paths in it' % p)
    for k in ('game', 'mods'): cfg[k] = resolve(cfg[k])
    for k in ('snapshots', 'reports'):
        if not os.path.isabs(cfg[k]): cfg[k] = os.path.join(HERE, cfg[k])
    if not os.path.isdir(cfg['game']):
        sys.exit('Game folder not found: %s\nSet "game" in %s' % (cfg['game'], p))
    return cfg

def safe(s): return re.sub(r'[^\w.-]+', '_', s).strip('_')

def snapshots(cfg):
    root = cfg['snapshots']
    if not os.path.isdir(root): return []
    out = []
    for n in os.listdir(root):
        info = os.path.join(root, n, 'snapshot.json')
        if os.path.exists(info):
            out.append((json.load(open(info)).get('taken', ''), os.path.join(root, n)))
    return [p for t, p in sorted(out)]

def mod_acps(cfg):
    out = []
    root = cfg['mods']
    if os.path.isdir(root):
        for d in sorted(os.listdir(root)):
            sub = os.path.join(root, d)
            if os.path.isdir(sub):
                out += [os.path.join(sub, f) for f in sorted(os.listdir(sub)) if f.lower().endswith('.acp')]
    return out

def schema_matches(cfg):
    exe = os.path.join(cfg['game'], 'ResourceED.exe')
    p = os.path.join(HERE, 'schema_db.json')
    if not os.path.exists(p) or not os.path.exists(exe): return False
    meta = json.load(open(p)).get('_meta', {})
    return meta.get('size') == os.path.getsize(exe)

def ensure_schema(cfg, refresh=True):
    if schema_matches(cfg): return True
    exe = os.path.join(cfg['game'], 'ResourceED.exe')
    if not refresh:
        print('note: schema_db.json was built from another ResourceED.exe; run extract_schema.py for exact field names')
        return False
    print('ResourceED.exe changed: rebuilding schema_db.json')
    try:
        import extract_schema
        db = extract_schema.Extractor(exe).build()
        json.dump(db, open(os.path.join(HERE, 'schema_db.json'), 'w'), separators=(',', ':'))
        return True
    except SystemExit as e:
        print('could not rebuild the schema (%s); using the old one' % e)
    except Exception as e:
        print('could not rebuild the schema (%r); using the old one' % e)
    return False

class A:  # argparse-like holder
    def __init__(self, **kw): self.__dict__.update(kw)

def run_mod(cfg, acp, label):
    os.makedirs(cfg['reports'], exist_ok=True)
    name = os.path.splitext(os.path.basename(acp))[0]
    out = os.path.join(cfg['reports'], '%s_vs_live_%s.html' % (safe(name), safe(label)))
    cmd_mod(A(game=cfg['game'], acp=acp, out=out, schema=None, history=history_dirs(cfg)))
    return out

def history_dirs(cfg):
    """where earlier update reports live: the reports folder plus any listed in config.json"""
    extra = cfg.get('history') or []
    if isinstance(extra, str): extra = [extra]
    return [cfg['reports']] + [resolve(x) for x in extra]

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('after-update', help='schema, update report, mod reports, new snapshot')
    p = sub.add_parser('mod', help="a mod's overrides against the live game")
    p.add_argument('--acp', help='one .acp (default: every mod in the Mods folder)'); p.add_argument('--out')
    p = sub.add_parser('snapshot', help='keep a copy of the game data to compare the next update with')
    p.add_argument('--label'); p.add_argument('--force', action='store_true')
    p = sub.add_parser('dump', help='print one resource as the decoder reads it')
    p.add_argument('name', help='resource name (as in the reports), or its 16-hex id')
    p.add_argument('--acp', help="read the mod's copy from this .acp instead of the game")
    p = sub.add_parser('update', help='a snapshot against the live game')
    p.add_argument('--old', help='snapshot folder (default: the latest)'); p.add_argument('--out')
    for q in sub.choices.values():
        q.add_argument('--game', help='override the game folder from config.json')
    args = ap.parse_args()
    cfg = load_config()
    if getattr(args, 'game', None): cfg['game'] = args.game
    label = build_label(cfg['game']).replace('build ', '')

    if args.cmd == 'mod':
        ensure_schema(cfg, refresh=False)
        if args.acp:
            if args.out: cmd_mod(A(game=cfg['game'], acp=args.acp, out=args.out, schema=None, history=history_dirs(cfg)))
            else: run_mod(cfg, args.acp, label)
        else:
            acps = mod_acps(cfg)
            if not acps: sys.exit('no .acp found under %s' % cfg['mods'])
            for acp in acps: run_mod(cfg, acp, label)
    elif args.cmd == 'dump':
        from decode import render
        S = load_schema(); D = Decoder(S)
        D.names = name_index(scan_game(content_dir(cfg['game'])))
        hits = []
        if args.acp:
            for ov in acpread.mod_overrides(open(args.acp, 'rb').read())[1]:
                if ov['raw'] and args.name in (ov['name'], ov['id']): hits.append(('mod copy, ' + ov['pack'], ov['raw']))
        else:
            for rel, full in sorted(scan_game(content_dir(cfg['game'])).items()):
                if os.path.getsize(full) > 8_000_000: continue
                for raw in pack_items(open(full, 'rb').read())[0]:
                    cid, rid, name = res_head(raw)
                    if args.name in (name, rid): hits.append((rel, raw))
        if not hits: sys.exit('not found: %s' % args.name)
        for where, raw in hits:
            cid, node = D.resource(raw)
            print('## %s  [%s]  %s' % (args.name, S.name(cid), where))
            print('\n'.join(render(node)))
    elif args.cmd == 'snapshot':
        cmd_snapshot(A(game=cfg['game'], dest=cfg['snapshots'], label=args.label, force=args.force))
    elif args.cmd == 'update':
        ensure_schema(cfg, refresh=False)
        old = args.old or (snapshots(cfg) or [None])[-1]
        if not old: sys.exit('no snapshot yet: run "snapshot" before the next game update')
        os.makedirs(cfg['reports'], exist_ok=True)
        cmd_update(A(old=old, game=cfg['game'], out=args.out, schema=None, reports=cfg['reports']))
    elif args.cmd == 'after-update':
        ensure_schema(cfg, refresh=True)
        snaps = snapshots(cfg)
        os.makedirs(cfg['reports'], exist_ok=True)
        cur = build_label(cfg['game'])
        if snaps and json.load(open(os.path.join(snaps[-1], 'snapshot.json'))).get('label') == cur:
            print('the latest snapshot is of this same build (%s): no update report' % cur)
        elif snaps:
            cmd_update(A(old=snaps[-1], game=cfg['game'], out=None, schema=None, reports=cfg['reports']))
        else:
            print('no earlier snapshot: skipping the update report this time')
        for acp in mod_acps(cfg): run_mod(cfg, acp, label)
        snap_label = safe(label)
        if os.path.exists(os.path.join(cfg['snapshots'], snap_label)):
            print('snapshot %s exists already: not taken again' % snap_label)
        else:
            cmd_snapshot(A(game=cfg['game'], dest=cfg['snapshots'], label=snap_label, force=False))
        print('reports are in', cfg['reports'])

if __name__ == '__main__':
    main()
