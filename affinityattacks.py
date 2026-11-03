"""The Architect units' attacks by affinity, which the game's DUMPALL gets wrong, for an aow4db-style website.

    python affinityattacks.py [--site PATH] [--acp MOD.acp | --no-mod]

Writes Data/GEN/AffinityAttacks.json: {ability slug: {damage, name, unit, affinity}}, the dump's way of writing damage
("6<damagespirit></damagespirit> "). The site folder is --site, else config.json's "site"; the mod is --acp, else the
first .acp in the Mods folder.

The dump writes an Architect unit once per starting affinity (its abilities' slugs end in the affinity's Faction Property
Giver id), but each with its abilities' default attack: the Surveyor's Magic Bolts as 10 Lightning. The game gives each
affinity version its own attack through a TUnitDynamicPropertyGroupData: its Requisite Filter names a requisite of the
unit ("Architect Surveyor Unit Requisite"), its name the affinity, and its Properties are ability plugins
("(Surveyor) Spirit Bolts - Plugin": 6 Spirit). A plugin replaces the default of the unit's ability whose PluginLabel is
its Label. Its damage is the plugin's unit damage effects, its damage modifiers, and the unit's own unconditional damage
modifier properties for that kind of attack (a "+2 Physical Damage Modifier" for Missile (Base)), as the dump counts them
for other units.
"""
import argparse, collections, json, os, re, sys

import aow4diff, alltexts, tooltips
from citytree import Game, fields, refs, names, value, msgid, hexid

AFFINITIES = ('Order', 'Chaos', 'Astral', 'Materium', 'Nature', 'Shadow')
# the dump's damage tags, in its order
DAMAGE = [('Physical', 'damagephysical'), ('Blight', 'damageblight'), ('Shock', 'damagelightning'),
          ('Fire', 'damagefire'), ('Spirit', 'damagespirit'), ('Frost', 'damagefrost')]


def damage_block(node):
    """{type: amount} of a TCombatDamageBlock"""
    out = collections.Counter()
    if node and node[0] == 'o':
        for label, owner, n in node[2]:
            if value(n) is not None:
                out[label] += int(float(value(n)))
    return out


def written(dmg):
    return ''.join(f'{dmg[t]}<{tag}></{tag}> ' for t, tag in DAMAGE if dmg.get(t))


def unconditional(game, mod):
    """a damage modifier that applies to every target"""
    f = fields(game.node(mod))
    flt = fields(f.get('Target Requisite Filter'))
    return not any(names(flt.get(k)) for k in ('Required Requisites', 'Forbidden Requisites', 'Must Have Requisites')) \
        and not any(names(fields(f.get('Target Property Filter')).get(k)) for k in ('Required Properties', 'Forbidden Properties'))


def modifier_damage(game, rids):
    out = collections.Counter()
    for m in rids:
        if m in game.raw and game.cls(m) == 'TExtraDamageAbilityModifier' and unconditional(game, m):
            out.update(damage_block(fields(game.node(m)).get('Damage')))
    return out


def unit_modifiers(game, rid, depth=0):
    """[(required requisites, damage)] of the damage modifier properties a unit has, through its property groups"""
    out = []
    if depth > 5 or rid not in game.raw: return out
    f = fields(game.node(rid))
    if game.cls(rid) == 'TUnitAbilityModifierProperty':
        req = set(names(fields(f.get('Requisite Filter')).get('Required Requisites')))
        dmg = modifier_damage(game, [m for n, m in refs(f.get('Modifiers'))])
        if dmg: out.append((req, dmg))
    for key in ('Properties', 'List'):
        for n, i in refs(f.get(key)): out += unit_modifiers(game, i, depth + 1)
    return out


def build(game, texts, site):
    read = lambda *p: json.load(open(os.path.join(site, *p), encoding='utf-8-sig'))
    units, abilities = read('Data', 'EN', 'Units.json'), {a['slug']: a for a in read('Data', 'EN', 'Abilities.json')}
    text = lambda rid, field: alltexts.plain_name(texts.get(msgid(fields(game.node(rid)).get(field)) or '', ''))

    # the abilities by their name on the site and their plugin label
    ability_of = {}
    for rid in game.of_class('TAbilityProperty'):
        f = fields(game.node(rid))
        label = value(f.get('PluginLabel'))
        if label: ability_of.setdefault((text(rid, 'Screen Name'), label), rid)

    # the affinity groups, by the requisite they ask of a unit
    groups = collections.defaultdict(list)
    for rid in game.of_class('TUnitDynamicPropertyGroupData'):
        f = fields(game.node(rid))
        m = re.search(r'\b(%s)\b' % '|'.join(AFFINITIES), game.name(rid))
        req = names(fields(f.get('Requisite Filter')).get('Required Requisites'))
        if m and req:
            plugins = [i for n, i in refs(f.get('Properties')) if i in game.raw and game.cls(i) == 'TAbilityPluginProperty']
            if plugins: groups[m.group(1)].append((set(req), plugins))

    out = {}
    for u in units:
        rid = hexid(u['resid'])
        if rid not in game.raw: continue
        affinity = None
        for a in u['abilities']:
            m = re.search(r'_([0-9a-f]{16})$', a['slug'])
            giver = m and m.group(1).upper()
            if giver in game.raw:
                affinity = re.match(r'(\w+) Starting Affinity', game.name(giver))
                affinity = affinity and affinity.group(1)
                break
        if affinity not in AFFINITIES: continue
        own = set(names(fields(game.node(rid)).get('Requisites')))
        mods = unit_modifiers(game, rid)
        for req, plugins in groups[affinity]:
            if not req <= own: continue
            for p in plugins:
                f = fields(game.node(p))
                label, plugin = value(f.get('Label')), fields(fields(game.node(p)).get('Plugin'))
                hit = [a for a in u['abilities'] if a['slug'] in abilities and (abilities[a['slug']]['name'], label) in ability_of]
                if not hit: continue
                dmg = collections.Counter()
                for n, e in refs(plugin.get('Effects')):
                    if e in game.raw and game.cls(e) == 'TTacticalUnitDamageEffect':
                        dmg.update(damage_block(fields(game.node(e)).get('field_1')))
                if not dmg: continue    # a plugin that changes no damage (Cultivate Affinity, Conjure Affinity Tesseract)
                dmg.update(modifier_damage(game, [m for n, m in refs(plugin.get('Ability Modifiers'))]))
                for a in hit:
                    ab = ability_of[(abilities[a['slug']]['name'], label)]
                    ab_req = set(names(fields(game.node(ab)).get('Requisites')))
                    total = collections.Counter(dmg)
                    for need, extra in mods:
                        if need and need <= ab_req: total.update(extra)
                    name = text(p, 'ScreenNameOverride') or abilities[a['slug']]['name']
                    out[a['slug']] = {'damage': written(+total), 'name': name, 'unit': u['name'], 'affinity': affinity}
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
    texts = tooltips.load_texts(cfg['game'], os.path.dirname(acp) if acp else '')
    out = build(game, texts, site)
    path = os.path.join(site, 'Data', 'GEN', 'AffinityAttacks.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print(f'{len(out)} affinity attacks of {len({e["unit"] for e in out.values()})} units -> {path}')
    for e in sorted({(e['unit'], e['affinity'], e['name'], e['damage']) for e in out.values()}):
        print('  ', *e)


if __name__ == '__main__':
    main()
