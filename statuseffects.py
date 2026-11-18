"""The status effects of an aow4db-style website, with what the game says of each, read from the live game with a mod on top
if one is given.

    python statuseffects.py --site PATH [--acp MOD.acp | --no-mod]

Writes Data/GEN/StatusEffects.json: the dump's status effects (Data/EN/StatusEffects.json, its order), each

    slug         the dump's ability slug (Data/EN/Abilities.json)
    name         its name, to read the file by
    kinds        ["Negative"], ["Positive"], or both: what the game counts it as
    stacks       how many times it stacks, or null
    counters     the slugs of the status effects it counters (and is countered by)
    dispellable  whether a dispel or cleanse can remove it

The game applies a status effect through a status effect description (TMagicStatusEffectDescription, "Magic - Status Effect
Description"), which names the property (Property Link) and says how it applies: AI Is Positive (the dispels' Positive
Filter reads it: "Negative Only", "Positive Only"), Allow Stacking and Max Stacks, its Opposing Status Effect Descriptions
(the effects it counters, "Strengthened counters Weakened"), and its Type Link List, where "Status Effect - Dispelable" is
what the game's dispels and cleanses remove. A property can have several descriptions (one per duration): a fact holds if
one of them says so, and a property some describe as positive and others as negative is both (Refuged, Gilded). The dump's
mind control effects (Dominated, Possessed, Final Ultimatum...) have no description: the game's mind control effects apply
them. They are negative.
"""
import argparse, collections, datetime, json, os, sys

import aow4diff
from citytree import Game, fields, refs, names, value

DESCRIPTION = 'TMagicStatusEffectDescription'
DISPELABLE = 'Status Effect - Dispelable'


def build(game, site):
    read = lambda *p: json.load(open(os.path.join(site, *p), encoding='utf-8-sig'))
    listed = [e['slug'] for e in read('Data', 'EN', 'StatusEffects.json')]
    abilities = {a['slug']: a for a in read('Data', 'EN', 'Abilities.json')}
    of_property = collections.defaultdict(list)
    for d in game.of_class(DESCRIPTION):
        f = fields(game.node(d))
        link = refs(f.get('Property Link'))
        if link: of_property[link[0][1]].append(d)
    # a description's own property, to name what it counters
    property_of = {d: p for p, ds in of_property.items() for d in ds}
    slug_of = {s.upper(): s for s in listed}

    out, missing = [], []
    for slug in listed:
        rid = slug.upper()
        descs = of_property.get(rid, [])
        kinds, stacks, counters, dispellable = set(), None, [], False
        for d in descs:
            f = fields(game.node(d))
            # AI Is Positive is true unless written (the schema's default)
            kinds.add('Negative' if value(f.get('AI Is Positive')) == 'false' else 'Positive')
            if value(f.get('Allow Stacking')) == 'true':
                n = int(value(f.get('Max Stacks')) or 0)
                if n > 1: stacks = max(stacks or 0, n)
            for _, o in refs(f.get('Opposing Status Effect Descriptions')):
                s = slug_of.get(property_of.get(o, ''))
                if s and s != slug and s not in counters: counters.append(s)
            if DISPELABLE in names(f.get('Type Link List')): dispellable = True
        if not descs:
            # mind control: applied by the game's mind control effects, not by a description
            kinds.add('Negative')
            missing.append(abilities.get(slug, {}).get('name', slug))
        out.append({'slug': slug, 'name': abilities.get(slug, {}).get('name', ''),
                    'kinds': [k for k in ('Negative', 'Positive') if k in kinds],
                    'stacks': stacks, 'counters': counters, 'dispellable': dispellable})
    return out, missing


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
    effects, missing = build(game, site)
    out = os.path.join(site, 'Data', 'GEN', 'StatusEffects.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
                   'mod': os.path.splitext(os.path.basename(acp))[0] if acp else None, 'effects': effects},
                  f, indent=1, ensure_ascii=False)
        f.write('\n')
    count = collections.Counter(' and '.join(e['kinds']) for e in effects)
    print('%d status effects (%s); with no description, taken as mind control: %s -> %s'
          % (len(effects), ', '.join('%d %s' % (n, k) for k, n in count.most_common()), ', '.join(missing) or 'none', out))


if __name__ == '__main__':
    main()
