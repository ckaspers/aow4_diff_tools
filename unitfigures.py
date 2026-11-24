"""How many figures (models) each unit has, for an aow4db-style website, from the live game with the mod.

    python unitfigures.py [--site PATH] [--acp MOD.acp | --no-mod]

Writes Data/GEN/UnitFigures.json: {resid: figures} for every unit of the site's Data/EN/Units.json (resid in decimal,
as the dump writes it). The site folder is --site, else config.json's "site"; the mod is --acp, else the first .acp in
the Mods folder.

A unit's figures are the positions of the formation its Figure Settings name (Formation Link, a TFormationResource's
Positions); a unit without one is a single figure. In play a faction mount's figure override (cavalry, and with an
exotic mount the culture units that can be cavalry) and a transformation that switches to the formation's Positions
(Small) or (Big) (Angelize, Spider's Embrace, Supergrowth, Spawnkin...) can change it: those are left out, as the unit
card shows the unit as it is drafted.

The game's own DUMPALL may write a "figure" field: it gave 4 for the seven units on the 7-position "Special 4 3
staggered" formation (MinionsArt's dump of 2026-11-19, which she corrected by hand), so the site reads this file.
"""
import argparse, json, os, sys

import aow4diff
from citytree import Game, fields, refs, hexid


def positions(game, rid):
    e = fields(fields(game.node(rid)).get('Positions')).get('Entries')
    return len(e[1]) if e and e[0] == 'l' else 0


def build(game, site):
    units = json.load(open(os.path.join(site, 'Data', 'EN', 'Units.json'), encoding='utf-8-sig'))
    out, warn = {}, []
    for u in units:
        resid = str(u['resid'])
        if resid in out: continue
        rid = hexid(resid)
        if rid not in game.raw or game.cls(rid) != 'TBaseUnitR':
            warn.append('not a unit in the game or the mod: %s (%s)' % (u['id'], resid)); continue
        link = refs(fields(fields(game.node(rid)).get('Figure Settings')).get('Formation Link'))
        if not link:
            out[resid] = 1
        elif link[0][1] in game.raw:
            out[resid] = positions(game, link[0][1])
        else:
            warn.append('formation %s of %s not found' % (link[0][0], u['id']))
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
    path = os.path.join(site, 'Data', 'GEN', 'UnitFigures.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    counts = {}
    for n in out.values(): counts[n] = counts.get(n, 0) + 1
    print('%d units -> %s; by figures: %s' % (len(out), path, ', '.join('%d: %d' % kv for kv in sorted(counts.items()))))
    for w in warn: print('  ', w)


if __name__ == '__main__':
    main()
