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

The icon: the trait's Icon names an image in one of the GAMEPLAY_ICONS libraries by atlas and id (atlas_names reads
the records; the id counts one more). Where an atlas's records are not found, the library's RealmTraits_ image
whose name holds the trait's name is used ("RealmTraits_CelestialGrounds").
"""
import argparse, json, os, re, struct, sys

import aow4diff, alltexts, clbicons, tmlicons, tooltips
from citytree import Game, fields, refs, value

CATEGORIES = [(0, 'GEOGRAPHY'), (1, 'CLIME'), (2, 'INHABITANTS'), (3, 'PRESENCE'), (4, 'MISC')]
DEFAULT_CATEGORY = 4
CAMPAIGN_ONLY = 5


def squash(s): return re.sub(r'[^a-z0-9]', '', s.lower())


def atlas_names(path):
    """{atlas number: {image id: image name}} of a gameplay icon library. An image record is its name, its fields
    table ((field, offset) pairs: 1 id, 2 position, 3 size, 6 and 7 pixel size) and, a few values on, its body; the
    body is found where position and size are fractions and the pixel size a whole number. Id 0 is not written."""
    b = open(path, 'rb').read()
    h = struct.unpack_from('<6I', b)
    strings = b[0x20:0x20 + h[3]]
    recs = b[0x20 + h[3]:0x20 + h[3] + h[4]]
    libname = tmlicons.library_name(path)
    starts = sorted((recs.find(struct.pack('<II', len(m.group(0)), m.start())), int(m.group(2)))
                    for m in tmlicons.TXA.finditer(strings) if m.group(1).decode() == libname)
    starts = [s for s in starts if s[0] >= 0]
    names = {m.start(): m.group(0) for m in re.finditer(rb'[^\x00]+', strings)}
    out = {}
    for i in range(len(recs) - 16):
        length, off = struct.unpack_from('<II', recs, i)
        name = names.get(off)
        if not name or len(name) != length or b'\\' in name or b'.' in name: continue
        for skip in (8, 12, 13):
            j, pairs = i + skip, []
            while j + 1 < len(recs) and recs[j] <= 0x0f and (not pairs or recs[j] > pairs[-1][0]) \
                    and (not pairs or recs[j + 1] > pairs[-1][1] or recs[j + 1] == 0 and not pairs):
                pairs.append((recs[j], recs[j + 1]))
                j += 2
            fo = dict(pairs)
            if all(k in fo for k in (2, 3, 6, 7)): break
        else:
            continue
        for p in range(j, j + 48):
            try:
                u, v = struct.unpack_from('<2f', recs, p + fo[2])
                uw, vh = struct.unpack_from('<2f', recs, p + fo[3])
                w, hh = struct.unpack_from('<2I', recs, p + fo[6])
            except struct.error:
                break
            if 0 <= u < 1 and 0 <= v < 1 and 0 < uw <= 1 and 0 < vh <= 1 and 8 <= w <= 4096 and 8 <= hh <= 4096:
                ident = struct.unpack_from('<I', recs, p + fo[1])[0] if 1 in fo else 0
                atlas = next((n for a, n in reversed(starts) if a <= i), None)    # the nearest atlas before it
                out.setdefault(atlas, {})[ident] = name.decode()
                break
    return out


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

    # every gameplay icon library: its images by name, and its atlases' image names by id
    images, ids = {}, {}
    for path in clbicons.libraries(cfg['game']):
        lib = tmlicons.library_name(path)
        if lib and lib.startswith('GAMEPLAY_ICONS'):
            library = clbicons.Library(path)
            images[lib] = (library, dict(library.images()))
            ids[lib] = atlas_names(path)

    def icon_of(rid, name):
        """the image name of a trait's icon: by its atlas and id (the id counts one more than the atlas's record), else
        the library's RealmTraits_ image named after the trait (Primal Fury's atlas has no records read here)"""
        ic = refs(fields(game.node(rid)).get('field_2'))
        raw = game.raw.get(ic[0][1]) if ic else None
        m = tmlicons.TXA.search(raw) if raw else None
        if not m or m.group(1).decode() not in images: return None
        lib = m.group(1).decode()
        at = m.end()
        length = struct.unpack_from('<I', raw, at)[0]
        ident = struct.unpack_from('<I', raw, at + 4 + length)[0] if at + 8 + length <= len(raw) else 0
        found = ids[lib].get(int(m.group(2)), {}).get(ident - 1)
        if found and found in images[lib][1]: return lib, found
        want = squash(name)
        match = [n for n in images[lib][1] if n.startswith(('RealmTraits_', 'RealmIcon_')) and want and want in squash(n.split('_', 1)[1])]
        return (lib, min(match, key=len)) if match else None

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
        found = icon_of(rid, name)
        if found:
            library, icon = images[found[0]][0], found[1]
            tex = images[found[0]][1][icon]
            out = os.path.join(site, 'Icons', 'RealmTraits', icon + '.png')
            if not os.path.exists(out) and tex['format'] == clbicons.RGBA:
                os.makedirs(os.path.dirname(out), exist_ok=True)
                clbicons.write_png(out, tex['width'], tex['height'], library.pixels(tex))
            if not os.path.exists(out): icon = None
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
