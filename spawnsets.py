"""Unit categories and unit pools for an aow4db-style website (Data/GEN/all_spawnsets.json, and the world map's
unit pools in Data/GEN/all_spawnsets_strategic.json, see build_pools): which unit sets a summon spell, an
event or a spawner can pick from, read from the live game with a mod on top if one is given.

    python spawnsets.py --site PATH [--out PATH] [--acp MOD.acp | --no-mod]

Each TUnitCategory becomes {"category": its CategoryName, "items": its Sets, "names": the units of each set}. A set
that appears more than once in a category is that much likelier, so items keep their repeats; the site works out the
chances from them (Data/src/lookuputils.js, ConvertSpawnTable). Several resources can share a category name (Call
Wild Animal's water animals are two); the site adds them up. names are the units' Screen Names, " / " between the
units of a set that holds more than one; a set the game has no units for keeps its own name.

Why not upstream's file: it holds the vanilla game's categories at the time it was made, so a mod's changes are
missing (Evolved's Call Greater Animal offers three animals in cold lands, not eight).
"""
import argparse, json, os, re, sys

import aow4diff, tooltips
from citytree import Game, fields, refs, value, msgid


def build(game, texts):
    def name(rid):
        t = texts.get(msgid(fields(game.node(rid)).get('Screen Name')) or '')
        return re.sub(r'\^\w*', '', t).strip() if t else None

    units_of = {}
    # a category names a TUnitSet by its SetName, or a unit itself by its DataStoreTag (GRIMBEAK_CROW)
    for rid in game.of_class('TBaseUnitR'):
        tag = value(fields(game.node(rid)).get('DataStoreTag'))
        un = name(rid) if tag else None
        if un: units_of.setdefault(tag, [un])
    for rid in game.of_class('TUnitSet'):
        f = fields(game.node(rid))
        set_name = value(f.get('SetName'))
        if not set_name: continue
        units = [u for u in (name(i) for n, i in refs(f.get('Units')) if i in game.raw) if u]
        if units: units_of[set_name] = list(dict.fromkeys(units))    # a set wins over a unit's own tag
    out = []
    for rid in sorted(game.of_class('TUnitCategory')):
        f = fields(game.node(rid))
        category = value(f.get('CategoryName'))
        if not category: continue
        sets = f.get('Sets')
        items = [value(x) for x in sets[1]] if sets and sets[0] == 'l' else []
        out.append({'category': category, 'items': items,
                    'names': [' / '.join(units_of.get(i) or []) or i.replace('_', ' ').title() for i in items]})
    out.sort(key=lambda e: e['category'])
    unknown = sorted({i for e in out for i in e['items'] if i not in units_of})
    return out, unknown


def build_pools(game, texts):
    """all_spawnsets_strategic.json: every TUnitPool (the units an event or a spawner on the world map fields) as
    {"pool": its name, "units": the units' Screen Names, "others": what else it draws from}. others are the tag
    filters' names ("All Fiends (Accursed + Infernal) Units"), unit upgrades and "Hero" for a generated hero; a race
    transformation changes units and is no part of who is fielded."""
    def name(rid):
        t = texts.get(msgid(fields(game.node(rid)).get('Screen Name')) or '')
        return re.sub(r'\^\w*', '', t).strip() if t else None
    out = []
    for rid in game.of_class('TUnitPool'):
        listed = fields(fields(game.node(rid)).get('Entries')).get('Entries')
        units, others = [], []
        for it in (listed[1] if listed and listed[0] == 'l' else []):
            f = fields(it)
            kind = value(f.get('Entry Type')) or ''
            if kind == 'Unit':
                r = refs(f.get('Unit Resource'))
                u = (name(r[0][1]) if r and r[0][1] in game.raw else None) or value(f.get('Name'))
                if u and u not in units: units.append(u)
            elif 'TagFilter' in kind and not kind.startswith('Race'):
                tf = f.get('Tag Filter')
                for t in (tf[1] if tf and tf[0] == 'f' else ()):
                    m = re.match(r"uses '(.*?)(?: - TagFilter)?'$", t)
                    if m and m.group(1) not in others: others.append(m.group(1))
            elif kind == 'Unit Upgrade':
                r = refs(f.get('Unit Upgrade'))
                o = value(f.get('Name')) or (r[0][0] if r else None)
                if o and o not in others: others.append(o)
            elif kind == 'Hero Desc' and 'Hero' not in others:
                others.append('Hero')
        out.append({'pool': game.name(rid), 'units': units, 'others': others})
    out.sort(key=lambda e: e['pool'])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--site', help='the website\'s folder (default: "site" in config.json)')
    ap.add_argument('--out', help='output file (default: <site>/Data/GEN/all_spawnsets.json)')
    ap.add_argument('--acp', help='the mod (default: the first .acp in the Mods folder)')
    ap.add_argument('--no-mod', action='store_true', help='read the game alone')
    args = ap.parse_args()
    cfg = aow4diff.load_config()
    site = args.site or cfg.get('site')
    if not site: sys.exit('which website? pass --site or set "site" in config.json')
    acp = None if args.no_mod else args.acp or (aow4diff.mod_acps(cfg) or [None])[0]
    game = Game(cfg, acp)
    texts = tooltips.load_texts(cfg['game'], os.path.dirname(acp) if acp else '')
    data, unknown = build(game, texts)
    pools = build_pools(game, texts)
    ppath = os.path.join(site, 'Data', 'GEN', 'all_spawnsets_strategic.json')
    with open(ppath, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(pools, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%d unit pools -> %s' % (len(pools), ppath))
    out = args.out or os.path.join(site, 'Data', 'GEN', 'all_spawnsets.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%d unit categories -> %s' % (len(data), out))
    if unknown: print('  %d set names with no units in the game, shown as they are: %s' % (len(unknown), ', '.join(unknown[:12])))


if __name__ == '__main__':
    main()
