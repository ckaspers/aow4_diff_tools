"""Realm traits for an aow4db-style website: every trait the realm setup offers, by category, read from the live game
with a mod on top if one is given.

    python realmtraits.py --site PATH [--acp MOD.acp | --no-mod]

Writes Data/GEN/RealmTraits.json, and the traits' icons to Icons/RealmTraits/<image>.png:

    categories  [{key, name, description}] in the game's order (Geography, Clime, Inhabitants, Presence, Misc)
    traits      [{resid, name, description, category, subcategory, exclusive: [names], dlc, icon}]

A trait is a TPlayerEmpireWorldTraitR. The schema names its fields; the decoder shows them by number: field_0 Name,
field_1 Description, field_2 Icon, field_6 Category (left out at its default, 4 Misc), field_12 Sub-Category, Entries
the mutually exclusive traits. Deprecated traits and Campaign Only ones (category 5) are left out.
Texts are filled in as all.json's are (alltexts.Texts), from the trait's own text links.

The icon: the trait's Icon names an image in one of the GAMEPLAY_ICONS libraries by atlas and id, in records not yet
read here. The image used is the library's RealmTraits_/RealmIcon_ image whose name holds the trait's name
("RealmTraits_CelestialGrounds"); a trait with no such image has none.
"""
import argparse, json, os, re, sys

import aow4diff, alltexts, clbicons, tmlicons, tooltips
from citytree import Game, fields, refs, value

CATEGORIES = [(0, 'GEOGRAPHY'), (1, 'CLIME'), (2, 'INHABITANTS'), (3, 'PRESENCE'), (4, 'MISC')]
DEFAULT_CATEGORY = 4
CAMPAIGN_ONLY = 5


def squash(s): return re.sub(r'[^a-z0-9]', '', s.lower())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--site', help='the website\'s folder (default: "site" in config.json)')
    ap.add_argument('--acp', help='the mod (default: the first .acp in the Mods folder)')
    ap.add_argument('--no-mod', action='store_true', help='read the game alone')
    args = ap.parse_args()
    cfg = aow4diff.load_config()
    site = args.site or cfg.get('site')
    if not site: sys.exit('which website? pass --site or set "site" in config.json')
    acp = None if args.no_mod else args.acp or (aow4diff.mod_acps(cfg) or [None])[0]
    game = Game(cfg, acp)
    T = alltexts.Texts(game, tooltips.load_texts(cfg['game'], os.path.dirname(acp) if acp else ''))
    text = lambda rid, field: alltexts.site_markup(T.link_text(rid, field) or '').replace('\0', '<eventColor>{').replace('\1', '}</eventColor>')

    # the realm trait images of every gameplay icon library, by library
    images = {}
    for path in clbicons.libraries(cfg['game']):
        lib = tmlicons.library_name(path)
        if lib and lib.startswith('GAMEPLAY_ICONS'):
            found = [(n, t) for n, t in clbicons.Library(path).images() if n.startswith(('RealmTraits_', 'RealmIcon_'))]
            if found: images[lib] = (clbicons.Library(path), found)

    traits = []
    for rid in game.of_class('TPlayerEmpireWorldTraitR'):
        f = fields(game.node(rid))
        if value(f.get('Is Deprecated')) == 'true': continue
        cat = value(f.get('field_6'))
        cat = int(cat) if cat is not None else DEFAULT_CATEGORY
        if cat == CAMPAIGN_ONLY: continue
        name = alltexts.plain_name(re.sub(r'<[^>]+>', '', text(rid, 'field_0')))
        if not name: continue
        sub = refs(f.get('field_12'))
        exclusive = [alltexts.plain_name(re.sub(r'<[^>]+>', '', text(i, 'field_0')))
                     for n, i in refs(f.get('Entries')) if i in game.raw]
        dlc = game.pack[rid].split('/')[0]
        icon = None
        ic = refs(f.get('field_2'))
        m = tmlicons.TXA.search(game.raw[ic[0][1]]) if ic and ic[0][1] in game.raw else None
        if m and m.group(1).decode() in images:
            want = squash(name)
            lib, found = images[m.group(1).decode()]
            match = [(n, t) for n, t in found if want and want in squash(n.split('_', 1)[1])]
            if len(match) >= 1:
                n, t = min(match, key=lambda x: len(x[0]))
                icon = n
                out = os.path.join(site, 'Icons', 'RealmTraits', n + '.png')
                if not os.path.exists(out):
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    px = lib.pixels(t)
                    clbicons.write_png(out, t['width'], t['height'], px, crop=False)
        traits.append({'resid': str(int(rid, 16)), 'name': name, 'description': text(rid, 'field_1'),
                       'category': cat, 'subcategory': (T.link_text(sub[0][1], 'Name') if sub and sub[0][1] in game.raw else None),
                       'exclusive': sorted(set(e for e in exclusive if e and e != name)),
                       'dlc': dlc.upper() if dlc not in ('Title', 'MOD') else None, 'icon': icon})
    # the same trait can be in the game twice (a DLC copy): keep the first of each name and description
    seen, unique = set(), []
    for t in sorted(traits, key=lambda t: (t['category'], t['subcategory'] or '', t['name'])):
        k = (t['name'], t['description'])
        if k not in seen: seen.add(k); unique.append(t)
    categories = [{'key': c, 'name': T.T.get('REALM_TRAIT@CATEGORY@' + key, key.title()),
                   'description': T.T.get('REALM_TRAIT@CATEGORY@' + key + '_DESC', '')} for c, key in CATEGORIES]
    data = {'generated': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
            'mod': os.path.splitext(os.path.basename(acp))[0] if acp else None, 'categories': categories, 'traits': unique}
    out = os.path.join(site, 'Data', 'GEN', 'RealmTraits.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    from collections import Counter
    print('%d realm traits (%d with an icon) -> %s' % (len(unique), sum(1 for t in unique if t['icon']), out))
    print('  by category:', {categories[c]['name']: n for c, n in sorted(Counter(t['category'] for t in unique).items())})


if __name__ == '__main__':
    main()
