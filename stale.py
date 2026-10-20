"""Signs that an override is an old copy: links to resources that exist nowhere any more.

A link names its target by id. When neither the game nor the mod has a resource with that id,
the link points at something the game removed (or the mod deleted) after the copy was made.
Links the game's own version has dangling too are left out: those are the game's, not the copy's.
"""
import os, json, hashlib

def all_ids(packs, mod_packs, cache_dir, res_head, pack_items):
    """every resource id in the game's packs (cached per game build) plus the mod's own packs"""
    key = hashlib.sha1(json.dumps(sorted((r, os.path.getsize(p), int(os.path.getmtime(p))) for r, p in packs.items())).encode()).hexdigest()[:16]
    cache = os.path.join(cache_dir, 'ids_%s.json' % key)
    ids = None
    if os.path.exists(cache):
        try: ids = set(json.load(open(cache)))
        except Exception: ids = None
    if ids is None:
        ids = set()
        for rel, full in packs.items():
            try: items, _ = pack_items(open(full, 'rb').read())
            except Exception: continue
            for raw in items:
                rid = res_head(raw)[1]
                if rid: ids.add(rid)
        try:
            os.makedirs(cache_dir, exist_ok=True)
            json.dump(sorted(ids), open(cache, 'w'))
            for f in os.listdir(cache_dir):
                if f.startswith('ids_') and f != os.path.basename(cache):
                    try: os.remove(os.path.join(cache_dir, f))
                    except OSError: pass
        except Exception:
            pass
    ids = set(ids)
    for p in mod_packs:
        try: items, _ = pack_items(open(p, 'rb').read())
        except Exception: continue
        for raw in items:
            rid = res_head(raw)[1]
            if rid: ids.add(rid)
    return ids

def refs(node):
    """(field path, name, id) of every link in a decoded resource"""
    out = []
    def walk(n, p):
        k = n[0]
        if k == 'o':
            for lab, own, v in n[2]: walk(v, p + (lab,))
        elif k == 'l':
            for i, v in enumerate(n[1]): walk(v, p + ('[%d]' % i,))
        elif k == 'r' and n[2]:
            rid = str(n[2]).strip("'").upper()
            if len(rid) == 16 and rid != '0' * 16:
                out.append((' / '.join(p), n[1], rid))
    walk(node, ())
    return out

def run(D, items, ids, mod_ids):
    """items: (row, mod raw, live raw or None) -> [row + {'links': [{p, name, id}]}]"""
    out = []
    for row, raw, live in items:
        try: mine = refs(D.resource(raw)[1])
        except Exception: continue
        bad = [(p, n, r) for p, n, r in mine if r not in ids and r not in mod_ids]
        if not bad: continue
        game_bad = set()
        if live is not None:
            try: game_bad = {r for p, n, r in refs(D.resource(live)[1]) if r not in ids}
            except Exception: pass
        bad = [{'p': p, 'name': n, 'id': r} for p, n, r in bad if r not in game_bad]
        if bad: out.append(dict(row, links=bad))
    return out

def only_in_copy(records):
    """entries the mod's copy has and the game's version does not: whole objects in a list,
    actions in a script, and things added inside an existing action (such as a prediction).
    -> [{'i': record index, 'items': [{'p': path, 'w': what, 'k': kind}]}]"""
    out = []
    for i, r in enumerate(records):
        items = []
        for c in r['c']:
            if c.get('m') or c.get('z'): continue
            d0 = c['d'][0]
            if d0.startswith('+ {') and 'replacements' not in c['p']:
                items.append({'p': c['p'], 'w': d0[2:], 'k': 'entry'})
        sec = None; minus = []
        for ln in r.get('s') or []:
            if ln.startswith('[') and ln.endswith(']'): sec = ln.strip('[]').title(); minus = []; continue
            if ln.startswith('@@') or not ln: minus = []; continue
            if ln.startswith('-'): minus.append(ln[1:].strip()); continue
            if ln.startswith('+'):
                t = ln[1:].strip()
                if not minus:
                    items.append({'p': 'Script / ' + (sec or ''), 'w': t, 'k': 'action'})
                else:
                    for m in minus:
                        base = m.rstrip(':').rstrip(']')
                        if len(base) > 10 and t.startswith(base) and len(t.rstrip(':')) > len(m.rstrip(':')):
                            items.append({'p': 'Script / ' + (sec or ''), 'w': t, 'k': 'inside', 'was': m}); break
                continue
            minus = []
        if items: out.append({'i': i, 'items': items})
    return out
