"""What the dump leaves out of each unit, for an aow4db-style website, from the live game with the mod: how many
figures (models) it has and its upkeep when summoned.

    python unitextras.py [--site PATH] [--acp MOD.acp | --no-mod]

Writes Data/GEN/UnitExtras.json: {resid: {"figures": n, "summoned_upkeep": "30<mana></mana> 5<influence></influence> "}}
for every unit of the site's Data/EN/Units.json (resid in decimal, as the dump writes it; the upkeep in the dump's way
of writing its "upkeep"). The site folder is --site, else config.json's "site"; the mod is --acp, else the first .acp
in the Mods folder.

Figures: the positions of the formation the unit's Figure Settings name (Formation Link, a TFormationResource's
Positions); a unit without one is a single figure. In play a faction mount's figure override (cavalry, and with an
exotic mount the culture units that can be cavalry) and a transformation that switches to the formation's Positions
(Small) or (Big) (Angelize, Spider's Embrace, Supergrowth, Spawnkin...) can change it: those are left out, as the unit
card shows the unit as it is drafted. The game's own DUMPALL may write a "figure" field: it gave 4 for the seven units
on the 7-position "Special 4 3 staggered" formation (MinionsArt's dump of 2026-11-19, which she corrected by hand).

Summoned upkeep: the Upkeep Cost (Summoned) of the unit's tier (Tier Resource Link, a TTierResource; the dump's
"upkeep" is the same tier's Upkeep Cost). The site had the game's numbers by hand (Tier IV 3 Imperium, Tier V 7), which
the mod makes 5 and 10.
"""
import argparse, json, os, sys

import aow4diff
from citytree import Game, fields, refs, value, hexid

# the dump's tags for the resources an upkeep names
TAGS = {'Gold': 'gold', 'Mana': 'mana', 'Imperium': 'influence'}


def positions(game, rid):
    e = fields(fields(game.node(rid)).get('Positions')).get('Entries')
    return len(e[1]) if e and e[0] == 'l' else 0


def upkeep(game, node, warn):
    """an income block as the dump writes an upkeep: "30<mana></mana> 5<influence></influence> " """
    e = fields(node).get('Entries')
    out = ''
    for it in (e[1] if e and e[0] == 'l' else []):
        f = fields(it)
        r = refs(f.get('Resource'))
        rid = r[0][1] if r else value(f.get('Resource'))
        name = game.name(rid) if rid in game.raw else rid
        tag = TAGS.get(name)
        if not tag:
            warn.append('upkeep resource with no tag: %s' % name); tag = str(name).lower()
        out += '%d<%s></%s> ' % (int(float(value(f.get('Value')))), tag, tag)
    return out


def build(game, site):
    units = json.load(open(os.path.join(site, 'Data', 'EN', 'Units.json'), encoding='utf-8-sig'))
    out, warn = {}, []
    for u in units:
        resid = str(u['resid'])
        if resid in out: continue
        rid = hexid(resid)
        if rid not in game.raw or game.cls(rid) != 'TBaseUnitR':
            warn.append('not a unit in the game or the mod: %s (%s)' % (u['id'], resid)); continue
        f = fields(game.node(rid))
        e = {}
        link = refs(fields(f.get('Figure Settings')).get('Formation Link'))
        if not link:
            e['figures'] = 1
        elif link[0][1] in game.raw:
            e['figures'] = positions(game, link[0][1])
        else:
            warn.append('formation %s of %s not found' % (link[0][0], u['id']))
        tier = refs(f.get('Tier Resource Link'))
        if tier and tier[0][1] in game.raw:
            e['summoned_upkeep'] = upkeep(game, fields(game.node(tier[0][1])).get('Upkeep Cost (Summoned)'), warn)
        else:
            warn.append('no tier for %s (%s)' % (u['id'] or u['name'] or '(nameless)', resid))
        out[resid] = e
    return out, warn


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
    out, warn = build(Game(cfg, acp), site)
    path = os.path.join(site, 'Data', 'GEN', 'UnitExtras.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    figs, ups = {}, {}
    for e in out.values():
        figs[e.get('figures')] = figs.get(e.get('figures'), 0) + 1
        ups[e.get('summoned_upkeep')] = ups.get(e.get('summoned_upkeep'), 0) + 1
    print('%d units -> %s' % (len(out), path))
    print('  by figures:', ', '.join('%s: %d' % kv for kv in sorted(figs.items(), key=lambda kv: kv[0] or 0)))
    print('  summoned upkeep:', '; '.join('%s(%d)' % kv for kv in sorted(ups.items(), key=lambda kv: -kv[1])))
    for w in warn: print('  ', w)


if __name__ == '__main__':
    main()
