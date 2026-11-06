#!/usr/bin/env python3
"""Two mods side by side, each against the live game.

  python modcompare.py <other mod> [--base <mod>] [--out FILE] [--history DIR]

A mod is its folder (the one holding the .acp) or the .acp itself. --base defaults to the first mod
in the Mods folder of config.json. Only what a mod changes takes part: overrides that differ from the
live game, the resources of the mods' own packs, and .po texts that are not the game's own words.
Resources neither mod changes, and overrides that are copies of the game's version, are left out.

The report's sections:
  Both change it the same way    resources whose changes match field for field
  Both change it differently     per field: the game's value, the base mod's and the other's
  Only <other> / only <base>     what one mod changes and the other leaves at the game's
  New resources                  each mod's own packs, paired across the mods by type and name
  Texts                          msgids both .po files set, or one of them

A change that puts back a value a game update replaced is marked with that update, from the update
reports in the reports folder (and --history). Older updates than those reports cover can't be told
from the modder's own edits.
"""
import argparse, json, os, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from aow4diff import (pack_items, res_head, scan_game, content_dir, build_label, name_index, Model,
                      load_schema, load_config, history_dirs, load_update_reports, mark_unmerged, mod_acps,
                      ensure_schema, safe)
from differ import Diff, compare, F_SCRIPT
from decode import render
import acpread, tooltips, stale


def find_acp(path):
    path = os.path.abspath(path)
    if os.path.isfile(path): return path, os.path.dirname(path)
    acps = sorted(f for f in os.listdir(path) if f.lower().endswith('.acp'))
    if not acps: sys.exit('no .acp in %s' % path)
    return os.path.join(path, acps[0]), path


class Mod:
    def __init__(self, path, game_langs):
        self.acp, self.dir = find_acp(path)
        self.name, ovs = acpread.mod_overrides(open(self.acp, 'rb').read())
        self.ovs = {}; self.repeats = 0
        for o in ovs:
            if not o['id']: continue
            if o['id'] in self.ovs: self.repeats += 1; continue
            self.ovs[o['id']] = o
        self.packs = sorted(os.path.join(r, f) for r, _, fs in os.walk(self.dir) for f in fs if f.lower().endswith('.rpk'))
        self.new = {}                                       # id -> (pack file, raw)
        for p in self.packs:
            for raw in pack_items(open(p, 'rb').read())[0]:
                rid = res_head(raw)[1]
                if rid: self.new[rid] = (os.path.basename(p), raw)
        # the mod's texts: its .po files (what the modder edits), else its .mo; a .po named after a
        # game language is a reference copy of the game's text (see tooltips.load_texts)
        po, mo = {}, {}
        for r, _, fs in os.walk(os.path.join(self.dir, 'Language')):
            for f in sorted(fs):
                base, ext = os.path.splitext(f)
                if base.upper() in game_langs: continue
                if ext.lower() == '.po': po.update(tooltips.read_po(os.path.join(r, f)))
                elif ext.lower() == '.mo': mo.update(tooltips.read_mo(os.path.join(r, f)))
        self.texts = {k: v for k, v in (po or mo).items() if k}


class Live:
    def __init__(self, game):
        self.packs = scan_game(content_dir(game))
        self.by_base = {}
        for rel in self.packs: self.by_base.setdefault(os.path.basename(rel).upper(), []).append(rel)
        self.cache = {}

    def items(self, rel):
        if rel not in self.cache:
            self.cache[rel] = {res_head(x)[1]: x for x in pack_items(open(self.packs[rel], 'rb').read())[0]}
        return self.cache[rel]

    def find(self, ov):
        """-> (pack, the game's resource or None) for an override"""
        rels = self.by_base.get(ov['pack'].upper(), [])
        for r in rels:
            if ov['id'] in self.items(r): return r, self.items(r)[ov['id']]
        return (rels[0] if rels else ov['pack']), None


def tlabel(S, cid):
    c = S.cls.get(cid)
    return (c.get('l') or c['n']) if c else 'class_%x' % cid


def split(c):
    """one change against the game -> (the game's, the mod's), as text"""
    d = c['d']; h = d[0]
    if 'o' in c: return c['o'], c['n']
    if h == 'changed' and len(d) == 3 and d[1].startswith('  - ') and d[2].startswith('  + '):
        return d[1][4:], d[2][4:]
    extra = [x[2:] for x in d[1:]]
    if h.startswith('+ '): return '', '\n'.join(['adds ' + h[2:]] + extra)
    if h.startswith('- '): return h[2:], '\n'.join(['removes ' + h[2:]] + extra)
    return '', '\n'.join([h] + extra)


def slots(changes):
    """a key per change that the other mod's change of the same thing shares"""
    out = {}; seen = {}
    for c in changes:
        h = c['d'][0]
        k = (c['p'], h) if ('o' not in c and h[:2] in ('+ ', '- ')) else (c['p'],)
        i = seen.get(k, 0); seen[k] = i + 1
        out[k + (i,)] = c
    return out


def row(p, c, side, other=None):
    g, v = split(c)
    r = {'p': p, 'g': g, side: v, 's': side}
    if c.get('u'): r['u' + side] = c['u']
    if other is not None:
        r['b' if side == 'a' else 'a'] = split(other)[1]
        if other.get('u'): r['ub' if side == 'a' else 'ua'] = other['u']
    return r


def cmd_compare(args):
    cfg = load_config()
    if args.game: cfg['game'] = args.game
    ensure_schema(cfg, refresh=False)
    game = cfg['game']
    S = load_schema()
    langs = {d.upper() for d in os.listdir(os.path.join(game, 'Language'))} if os.path.isdir(os.path.join(game, 'Language')) else set()
    base = args.base or (mod_acps(cfg) or [None])[0]
    if not base: sys.exit('no --base given and no mod in %s' % cfg['mods'])
    A, B = Mod(base, langs), Mod(args.other, langs)
    print('%s: %d overrides, %d new resources, %d texts' % (A.name, len(A.ovs), len(A.new), len(A.texts)))
    print('%s: %d overrides, %d new resources, %d texts' % (B.name, len(B.ovs), len(B.new), len(B.texts)))
    t0 = time.time()
    live = Live(game)
    MA, MB = Model(S, restamp_cosmetic=True), Model(S, restamp_cosmetic=True)
    names = name_index(live.packs)
    for M in (MA, MB): M.D.names = names
    D = MA.D

    # each mod's override of each resource: deleted, edited, a copy of the game's, or with no target
    def side(M, mod, rid):
        ov = mod.ovs.get(rid)
        if ov is None: return None
        rel, target = live.find(ov)
        s = {'rel': rel, 'raw': ov['raw'], 'live': target, 'name': ov['name']}
        if ov['raw'] is None:
            s['st'] = 'deleted' if target is not None else 'gone'
        elif target is None:
            s['st'] = 'notarget'
        elif target == ov['raw']:
            s['st'] = 'copy'
        else:
            r = M.diff_record(rel, target, ov['raw'])
            s['r'] = r
            s['c'] = [c for c in r['c'] if not c.get('m')]
            s['st'] = 'edit' if s['c'] else 'copy'
        return s
    ids = sorted(set(A.ovs) | set(B.ovs))
    sides = {rid: (side(MA, A, rid), side(MB, B, rid)) for rid in ids}

    # changes that put back what a game update replaced
    reports = load_update_reports(history_dirs(cfg) + (args.history or []))
    for M in (MA, MB): mark_unmerged(M, reports)
    used = sorted({'%s → %s' % (U.get('old'), U.get('new')) for U in reports})

    # links to resources that exist nowhere (an old copy), and to resources the other mod deletes
    all_ids = stale.all_ids(live.packs, A.packs + B.packs, os.path.join(HERE, 'cache'), res_head, pack_items)
    deleted = {'a': {rid for rid, (sa, sb) in sides.items() if sa and sa['st'] == 'deleted'},
               'b': {rid for rid, (sa, sb) in sides.items() if sb and sb['st'] == 'deleted'}}
    def flags(raw, live_raw, mine, other_key, other_name):
        out = []
        try: node = D.resource(raw)[1]
        except Exception: return out
        links = stale.refs(node)
        game_bad = set()
        if live_raw is not None:
            try: game_bad = {r for _, _, r in stale.refs(D.resource(live_raw)[1]) if r not in all_ids}
            except Exception: pass
        dead = sorted({n for p, n, r in links if r not in all_ids and r not in mine and r not in game_bad})
        if dead: out.append('links to %s, which the game no longer has: an old copy' % ', '.join(dead))
        gone = sorted({n for p, n, r in links if r in deleted[other_key]})
        if gone: out.append('links to %s, which %s deletes' % (', '.join(gone), other_name))
        return out

    sections = {'same': [], 'diff': [], 'a': [], 'b': []}
    notes = {'a': {'copy': 0, 'notarget': [], 'gone': 0}, 'b': {'copy': 0, 'notarget': [], 'gone': 0}}
    for rid in ids:
        sa, sb = sides[rid]
        for key, s in (('a', sa), ('b', sb)):
            if s is None: continue
            if s['st'] == 'copy': notes[key]['copy'] += 1
            elif s['st'] == 'gone': notes[key]['gone'] += 1
            elif s['st'] == 'notarget':
                cid, _, name = res_head(s['raw'])
                notes[key]['notarget'].append({'n': name or s['name'], 't': tlabel(S, cid), 'k': s['rel'], 'id': rid})
        on_a = sa is not None and sa['st'] in ('edit', 'deleted')
        on_b = sb is not None and sb['st'] in ('edit', 'deleted')
        if not on_a and not on_b: continue
        ref = sa if on_a else sb
        cid, _, name = res_head(ref['live'])
        e = {'n': name or ref['name'], 't': tlabel(S, cid), 'k': ref['rel'].rsplit('/', 1)[-1], 'id': rid, 'rows': [], 'f': []}
        for key, s, mine, oth, oname in (('a', sa, A, 'b', B.name), ('b', sb, B, 'a', A.name)):
            if s and s['st'] == 'edit':
                e['f'] += [('%s: ' % mine.name) + x for x in flags(s['raw'], s['live'], set(mine.new), oth, oname)]
                if s['r'].get('s'): e['s' + key] = s['r']['s']
        if on_a and not on_b or on_b and not on_a:
            key, s, other = ('a', sa, sb) if on_a else ('b', sb, sa)
            if s['st'] == 'deleted':
                e['rows'].append({'p': '(whole resource)', 'g': 'in the game', key: 'deleted', 's': key})
            else:
                e['rows'] = [row(c['p'], c, key) for c in s['c']]
            if other is not None and other['st'] == 'copy':
                e['copy'] = 1           # the other mod overrides it too, with the game's version
            sections[key].append(e)
            continue
        # both mods change it
        if sa['st'] == 'deleted' or sb['st'] == 'deleted':
            if sa['st'] == sb['st']:
                e['rows'].append({'p': '(whole resource)', 'g': 'in the game', 'a': 'deleted', 'b': 'deleted', 's': 'same'})
                sections['same'].append(e); continue
            ed = sb if sa['st'] == 'deleted' else sa
            e['rows'].append({'p': '(whole resource)', 'g': 'in the game', 'a': 'deleted' if sa['st'] == 'deleted' else 'edited (below)',
                              'b': 'deleted' if sb['st'] == 'deleted' else 'edited (below)', 's': 'diff'})
            e['rows'] += [row(c['p'], c, 'a' if ed is sa else 'b') for c in ed['c']]
            sections['diff'].append(e); continue
        ab = Diff(); compare(D.resource(sa['raw'])[1], D.resource(sb['raw'])[1], ab)
        ab = [c for c in ab.changes if not c.get('m') and not c.get('z')]
        script_differs = {c['p'] for c in ab if c['f'] == F_SCRIPT}
        ka, kb = slots(sa['c']), slots(sb['c'])
        for k in list(ka) + [k for k in kb if k not in ka]:
            ca, cb = ka.get(k), kb.get(k)
            if ca and cb:
                same = ca['d'] == cb['d'] and not (ca['f'] == F_SCRIPT and ca['p'] in script_differs)
                if not ab: same = True
                r = row(ca['p'], ca, 'a', cb); r['s'] = 'same' if same else 'diff'
                e['rows'].append(r)
            elif ca: e['rows'].append(row(ca['p'], ca, 'a'))
            else: e['rows'].append(row(cb['p'], cb, 'b'))
        sections['same' if all(r['s'] == 'same' for r in e['rows']) else 'diff'].append(e)

    # the mods' own packs: new resources, paired by type and name
    def new_list(mod, other_key, other_name):
        out = {}
        for rid, (pack, raw) in mod.new.items():
            cid, _, name = res_head(raw)
            try: lines = render(D.resource(raw)[1])
            except Exception as ex: lines = ['(could not decode: %r)' % ex]
            x = {'n': name or '(unnamed)', 't': tlabel(S, cid), 'k': pack, 'id': rid, 'body': lines, 'raw': raw}
            if any(rid in live.items(r) for r in live.by_base.get(pack.upper(), [])): x['f'] = ['the game has a resource of this id']
            fl = flags(raw, None, set(mod.new), other_key, other_name)
            if fl: x['f'] = x.get('f', []) + fl
            out.setdefault((x['t'], x['n']), []).append(x)
        return out
    na, nb = new_list(A, 'b', B.name), new_list(B, 'a', A.name)
    new_both = []
    for k in sorted(set(na) & set(nb)):
        xa, xb = na.pop(k)[0], nb.pop(k)[0]
        d = Diff(); compare(D.resource(xa['raw'])[1], D.resource(xb['raw'])[1], d)
        rows = [{'p': c['p'], 'g': '', 'a': split(c)[0] or '(not there)', 'b': split(c)[1], 's': 'diff'}
                for c in d.changes if not c.get('m') and not c.get('z')]
        new_both.append({'n': k[1], 't': k[0], 'k': xa['k'] + ' / ' + xb['k'], 'id': xa['id'] + ' / ' + xb['id'], 'rows': rows,
                         's': 'same' if not rows else 'diff', 'body': xb['body']})
    def flat(d):
        out = [x for v in d.values() for x in v]
        for x in out: x.pop('raw', None)
        return sorted(out, key=lambda x: (x['t'], x['n']))

    # a changed text link shows the words it stands for: each mod's own text, else the game's
    game_txt = tooltips.read_mo(os.path.join(game, 'Language', 'EN', 'EN.MO'))
    def words(v, *where):
        k = (v or '').strip().strip('\'"')
        for d in where:
            if k in d: return d[k]
    for e in [e for v in sections.values() for e in v] + new_both:
        for r in e['rows']:
            if not r['p'].endswith('msgid'): continue
            for key, where in (('g', (game_txt,)), ('a', (A.texts, game_txt)), ('b', (B.texts, game_txt))):
                w = words(r.get(key), *where)
                if w: r['t' + key] = w

    # texts
    texts = []; same_as_game = {'a': 0, 'b': 0}
    for k in sorted(set(A.texts) | set(B.texts)):
        g = game_txt.get(k)
        ta, tb = A.texts.get(k), B.texts.get(k)
        if ta is not None and ta == g: ta = None; same_as_game['a'] += 1
        if tb is not None and tb == g: tb = None; same_as_game['b'] += 1
        if ta is None and tb is None: continue
        s = ('same' if ta == tb else 'diff') if ta is not None and tb is not None else 'a' if ta is not None else 'b'
        t = {'id': k, 's': s, 'g': g}
        if ta is not None: t['a'] = ta
        if tb is not None: t['b'] = tb
        texts.append(t)

    for v in sections.values(): v.sort(key=lambda e: (e['t'], e['n']))
    for v in notes.values(): v['notarget'].sort(key=lambda e: (e['t'], e['n']))
    info = lambda mod, key: {'name': mod.name, 'path': mod.dir, 'ovs': len(mod.ovs), 'repeats': mod.repeats,
                             'new': len(mod.new), 'texts': len(mod.texts), 'packs': [os.path.basename(p) for p in mod.packs],
                             'copies': notes[key]['copy'], 'notarget': notes[key]['notarget'], 'gone': notes[key]['gone'],
                             'textsSame': same_as_game[key]}
    data = {'generated': time.strftime('%Y-%m-%d %H:%M'), 'game': 'live (%s)' % build_label(game),
            'A': info(A, 'a'), 'B': info(B, 'b'), 'updates': used,
            'same': sections['same'], 'diff': sections['diff'], 'onlyA': sections['a'], 'onlyB': sections['b'],
            'newBoth': new_both, 'newA': flat(na), 'newB': flat(nb), 'texts': texts}
    out = args.out or os.path.join(cfg['reports'], '%s_vs_%s_%s.html' % (safe(A.name), safe(B.name), safe(build_label(game))))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    t = open(os.path.join(HERE, 'compare_template.html'), encoding='utf-8').read()
    js = json.dumps(data, separators=(',', ':'), ensure_ascii=False).replace('</', '<\\/')
    open(out, 'w', encoding='utf-8').write(t.replace('__DATA__', js, 1))
    tc = {s: sum(1 for x in texts if x['s'] == s) for s in ('same', 'diff', 'a', 'b')}
    print('same change %d, changed differently %d, only %s %d, only %s %d; new resources: both %d, %s %d, %s %d; '
          'texts: same %d, different %d, only %s %d, only %s %d -> %s (%.0fs)' % (
              len(sections['same']), len(sections['diff']), B.name, len(sections['b']), A.name, len(sections['a']),
              len(new_both), B.name, len(data['newB']), A.name, len(data['newA']),
              tc['same'], tc['diff'], B.name, tc['b'], A.name, tc['a'], out, time.time() - t0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('other', help="the other mod's folder or .acp")
    ap.add_argument('--base', help='the mod to compare it with (default: the first mod in the Mods folder)')
    ap.add_argument('--out', help='the report (default: <base>_vs_<other>_<build>.html in the reports folder)')
    ap.add_argument('--history', action='append', help='another folder of update reports (can repeat)')
    ap.add_argument('--game', help='override the game folder from config.json')
    cmd_compare(ap.parse_args())

if __name__ == '__main__':
    main()
