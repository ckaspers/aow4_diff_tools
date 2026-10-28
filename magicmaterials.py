"""Magic materials for an aow4db-style website: each set's bonus and what it needs, and each material's
texts, local income and combat enchantment, read from the live game, with a mod on top if one is given.

    python magicmaterials.py --site PATH [--out PATH] [--acp MOD.acp | --no-mod]

Writes Data/GEN/MagicMaterials.json. The site folder is --site, else config.json's "site"; the mod is --acp,
else the first .acp in the Mods folder.

Every text is the live game's or the mod's own, filled in with the values of the resource that shows it and
written as all.json's are (alltexts.Texts): a concept link in the concept's own words ("Every [turn/] Turn"), a
counted one agreeing with its number ("1 Turn", "2 Turns").

    sets        key (DataStoreTag), category, name, description: texts; required: how many materials;
                unique: whether they must differ; materials: their keys
    materials   key -> name, description (global effect), lore, income [[resource, value]] (local),
                combat {name, description}, dlc (as the site's dlcMap names it)

Texts are in the site's format: <bulletlist><bullet>, <icon></icon>, [hyperlink,GLOBAL.X]words[/hyperlink].
"""
import argparse, datetime, json, os, re, sys

import aow4diff, alltexts, tooltips
from citytree import Game, fields, refs, names, value, tags, msgid, income

# TTokenSetBonus fields the decoder leaves out at their defaults (schema_db.json: FRequiredAmount 3,
# FRequiresUniqueTokens on)
REQUIRED_DEFAULT = 3
UNIQUE_DEFAULT = True


def text_of(T, rid, field):
    """a resource's text as all.json's are written (alltexts.py): its own values, concept links as the game words them"""
    t = T.link_text(rid, field)
    return alltexts.site_markup(t or '').replace('\0', '<eventColor>{').replace('\1', '}</eventColor>')


def build(game, T):
    warn = []

    materials, tags_of = {}, {}
    for rid in game.of_class('TStructureR'):
        f = fields(game.node(rid))
        if 'Magic Material' not in names(f.get('LocationTypes')): continue
        key = (value(f.get('DataStoreTag')) or game.name(rid)).lower()
        giver = {}
        for e in (f['Entries'][1] if f.get('Entries') and f['Entries'][0] == 'l' else []):
            if value(fields(e).get('List')) == 'STRUCTUREPROPERTYGIVER': giver = fields(e)
        m = {'name': text_of(T, rid, 'Screen Name'), 'description': text_of(T, rid, 'Screen Description'),
             'lore': text_of(T, rid, 'Screen Lore Description'), 'income': []}
        # by id: a reference keeps its old name when the resource is renamed (Silvertongue Fruit's income
        # is "Euphoric Trollstools Node - Sector Income" now)
        for p, i in refs(giver.get('Sector Properties')):
            if i in game.raw and 'Income' in game.cls(i): m['income'] += income(game, i)
        # the combat enchantment: the first structure property with a name (the others are hidden helpers)
        for p, i in refs(giver.get('Structure Properties')):
            cf = fields(game.node(i)) if i in game.raw else {}
            if msgid(cf.get('Screen Name')):
                m['combat'] = {'name': text_of(T, i, 'Screen Name'), 'description': text_of(T, i, 'Description')}
                break
        else:
            warn.append('%s: no combat enchantment' % key)
        dlc = game.pack[rid].split('/')[0]
        if dlc not in ('Title', 'MOD'): m['dlc'] = dlc.upper()
        materials[key] = m
        tags_of[key] = set(tags(f.get('Tags')))

    sets = []
    for rid in game.of_class('TTokenSetBonus'):
        f = fields(game.node(rid))
        need = set(tags(f.get('Required Token Tags')))
        members = sorted(k for k, t in tags_of.items() if t & need)
        if not members: continue
        req, uniq = value(f.get('Required Amount')), value(f.get('Requires Unique Tokens'))
        key = value(f.get('DataStoreTag')) or re.sub(r' - Token Set Bonus$', '', game.name(rid))
        sets.append({'key': re.sub(r'\W+', '_', key).lower(),
                     'category': text_of(T, rid, 'Category Name'), 'name': text_of(T, rid, 'Screen Name'),
                     'description': text_of(T, rid, 'Screen Description'),
                     'required': int(req) if req is not None else REQUIRED_DEFAULT,
                     'unique': (uniq == 'true') if uniq is not None else UNIQUE_DEFAULT,
                     'materials': members})
    in_a_set = {k for s in sets for k in s['materials']}
    for k in materials:
        if k not in in_a_set: warn.append('%s is in no set' % k)
    return {'sets': sets, 'materials': materials}, warn


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--site', help='the website\'s folder (default: "site" in config.json)')
    ap.add_argument('--out', help='output file (default: <site>/Data/GEN/MagicMaterials.json)')
    ap.add_argument('--acp', help='the mod (default: the first .acp in the Mods folder)')
    ap.add_argument('--no-mod', action='store_true', help='read the game alone')
    args = ap.parse_args()
    cfg = aow4diff.load_config()
    site = args.site or cfg.get('site')
    if not site: sys.exit('which website? pass --site or set "site" in config.json')
    acp = None if args.no_mod else args.acp or (aow4diff.mod_acps(cfg) or [None])[0]
    game = Game(cfg, acp)
    T = alltexts.Texts(game, tooltips.load_texts(cfg['game'], os.path.dirname(acp) if acp else ''))
    data, warn = build(game, T)
    data = {'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
            'mod': os.path.splitext(os.path.basename(acp))[0] if acp else None, **data}
    out = args.out or os.path.join(site, 'Data', 'GEN', 'MagicMaterials.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%d sets, %d materials -> %s' % (len(data['sets']), len(data['materials']), out))
    for s in data['sets']: print('  %-22s needs %d%s: %s' % (s['key'], s['required'], ' different' if s['unique'] else '', ', '.join(s['materials'])))
    for w in dict.fromkeys(warn): print('  warning:', w)


if __name__ == '__main__':
    main()
