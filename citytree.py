"""City tree links for the Evolved website: which city building follows which, and which culture,
sub-culture or tome it belongs to, read from the live game plus the mod.

    python citytree.py [--out PATH] [--acp MOD.acp]

Run it after copying a new DUMPALL dump into the website: it reads the site's
Data/EN/StructureUpgrades.json and Data/EN/Tomes.json, and writes Data/GEN/CityTreeLinks.json
(the site folder is config.json's "site", else an "evolved" folder next to this repository).

The output holds links and keys only; names, icons, costs and descriptions stay in the dump and are
looked up by resid (the resource id in decimal; the dump's "id" is not unique):

    roots       Town Hall I and Throne: the two things a city starts with. name = text id in all.json
    cultures    key (as in Icons/Text/<key>.png), name (text id), parent culture key for a sub-culture
    structures  resid, section (city / fortification / throne / tome), line (row group), th (Town Hall
                tier it needs), parents (resids or root keys, any of which may be present),
                cultures (keys that get it; empty for everyone), replaces (generic resids it takes
                the place of), variant (wizard / vampire tower), tome (resid of the tome)

How the game links buildings (checked against the editor's data):
- RequiredStructureProperties / MustHaveStructureProperties on a structure upgrade name properties
  that other buildings grant in their Structure Properties: those buildings are its parents.
- Each culture and sub-culture has a City Settings resource (TCityCultureSettingsR) whose
  StructureUpgradeReplacementList swaps a generic building for its own.
- TPropertyUnlockSettings unlock buildings for a player property: a tome skill, a sub-culture, ...
"""
import argparse, datetime, json, os, re, sys

import aow4diff, acpread, tooltips
from decode import Decoder

# the website's repository: config.json's "site", else an "evolved" folder next to this one
DEFAULT_SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'evolved')

# properties a city has from the start: the two roots of the tree
ROOTS = {
    'town_hall_i_town_center': {'props': {'Tier 1 - Town Hall', 'Town Hall Tier 1 - Base City Level - ID'},
                                'name': 'CITYUPGRADES_STRATEGIC@CITY_PROVINCE_BASE_1@NAME',
                                'icon': 'ExtraIcons/town_hall_i_town_center'},
    'throne': {'props': {'Throne City ID', 'Transfer With Throne ID Property'},
               'name': 'COLONYUPGRADES_STRATEGIC@HQ@NAME', 'icon': 'Text/throne'},
}
ROOT_OF = {p: k for k, r in ROOTS.items() for p in r['props']}

# "Tier N - <family>" properties: the row a building sits in
LINES = [('Gold', 'gold'), ('Mana', 'mana'), ('Research', 'knowledge'), ('Food', 'food'),
         ('Structure Production', 'production'), ('Unit Production', 'draft'), ('Ocean', 'fish'),
         ('Lava', 'lava'), ('City Stability', 'stability'), ('Trade Structure', 'trade')]
AFFINITY = re.compile(r'Focus Crystal|Bolstering Matrix|Amplifier Lens')
# special city types and one-off sources the tree leaves out
SKIP_PACKS = re.compile(r'CityDefinition_Strategic_|CityUpgrades_Definitions_(EldritchRealms|ArchonProphecy)|RaceTraits_')
# sub-culture icons whose file name differs from the game's telemetry name
ICON_ALIAS = {'nomad_conquerers': 'nomad_conquerors', 'mystic_astral_summoners': 'mystic_school_of_summoning',
              'mystic_spell_arcanists': 'mystic_school_of_potential',
              'mystic_attunement_seekers': 'mystic_school_of_attunement'}


def fields(node):
    return {l: n for (l, owner, n) in node[2]} if node and node[0] == 'o' else {}

def refs(node):
    """[(name, id)] of the references in a list or single reference node"""
    if not node: return []
    if node[0] == 'r': return [(node[1], node[2])]
    if node[0] == 'l': return [(x[1], x[2]) for x in node[1] if x[0] == 'r']
    return []

def names(node): return [n.strip() for n, i in refs(node)]    # some property names end in a space

def value(node): return tooltips.unq(node) if node and node[0] == 'v' else None

def tags(node): return list(node[1]) if node and node[0] == 't' else []

def msgid(node): return value(fields(node).get('msgid')) or None

def snake(s): return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')


class Game:
    """the game's resources with the mod's packs and overrides on top"""
    def __init__(self, cfg, acp):
        self.dir = cfg['game']
        self.S = aow4diff.load_schema()
        self.D = Decoder(self.S)
        self.raw, self.pack = {}, {}
        for rel, full in aow4diff.scan_game(aow4diff.content_dir(cfg['game'])).items():
            if os.path.getsize(full) > 8_000_000: continue
            self._add(open(full, 'rb').read(), rel)
        packs = os.path.join(os.path.dirname(acp), 'Packs')
        if os.path.isdir(packs):
            for f in os.listdir(packs):
                if f.lower().endswith('.rpk'): self._add(open(os.path.join(packs, f), 'rb').read(), 'MOD/' + f)
        for o in acpread.mod_overrides(open(acp, 'rb').read())[1]:
            if not o['id']: continue
            if o['raw']: self.raw[o['id']] = o['raw']
            else: self.raw.pop(o['id'], None)
        self._node = {}

    def _add(self, b, rel):
        for raw in aow4diff.pack_items(b)[0]:
            cid, rid, name = aow4diff.res_head(raw)
            if rid: self.raw[rid] = raw; self.pack[rid] = rel

    def cls(self, rid): return self.S.name(aow4diff.res_head(self.raw[rid])[0])

    def name(self, rid): return aow4diff.res_head(self.raw[rid])[2] or ''

    def node(self, rid):
        if rid not in self._node: self._node[rid] = self.D.resource(self.raw[rid])[1]
        return self._node[rid]

    def of_class(self, cls):
        return [r for r in self.raw if self.cls(r) == cls]


def hexid(resid): return '%016X' % (int(resid) & 0xFFFFFFFFFFFFFFFF)


def build(game, site):
    warn = []
    dump = json.load(open(os.path.join(site, 'Data', 'EN', 'StructureUpgrades.json'), encoding='utf-8-sig'))
    tomes = json.load(open(os.path.join(site, 'Data', 'EN', 'Tomes.json'), encoding='utf-8-sig'))
    tome_of = {s['upgrade_slug']: t['resid'] for t in tomes for s in t.get('skills', []) if s.get('upgrade_slug')}

    # ---- the city buildings the website shows ----
    B = {}
    for e in dump:
        if e.get('is_sector_upgrade') or not e.get('icon'): continue
        rid = hexid(e['resid'])
        if rid not in game.raw or game.cls(rid) != 'TStructureUpgradeR':
            warn.append('not in the game or the mod: %s (%s)' % (e['id'], e['resid'])); continue
        f = fields(game.node(rid))
        uname = game.name(rid)
        if uname.startswith(('Outpost -', '(Unused)')) or SKIP_PACKS.search(game.pack[rid]): continue
        # a property nothing grants: how the game (and the mod) switches a building off
        if any('Unreachable' in p for p in names(f.get('MustHaveStructureProperties')) + names(f.get('RequiredStructureProperties'))):
            continue
        B[rid] = {'resid': str(e['resid']), 'id': e['id'], 'uname': uname, 'pack': game.pack[rid],
                  'req': names(f.get('RequiredStructureProperties')),
                  'must': names(f.get('MustHaveStructureProperties')),
                  # "<building> - ID" properties are granted as colony properties
                  'gives': names(f.get('Structure Properties')) + names(f.get('Colony Properties')),
                  'side': names(f.get('RequiredSideProperties')),
                  'requisites': names(f.get('Requisites')),
                  'city_tier': value(f.get('IsCityTierUpgrade')) == 'true',
                  'tags': tags(f.get('Tags')), 'cultures': [], 'replaces': []}

    # ---- unlocks by player property (tome skills, sub-cultures) ----
    unlocked_by = {}
    for r in game.of_class('TPropertyUnlockSettings'):
        f = fields(game.node(r))
        prop = (names(f.get('Property')) or [None])[0]
        for n, i in refs(f.get('Structure Upgrades')):
            if i in B: unlocked_by.setdefault(i, []).append((prop, game.pack[r]))

    # ---- cultures ----
    cultures = {}
    def giver(rid):
        f = fields(game.node(rid))
        # the telemetry name matches the site's icon names; it is left out when at its default
        tele = value(f.get('TelemetryName')) or re.sub(r' Culture - Faction Property Giver$', '', game.name(rid))
        return snake(tele), msgid(f.get('Screen Name'))
    sub_by_prop = {}
    for r in game.of_class('TCityCultureSettingsR'):
        s = fields(fields(game.node(r)).get('Settings'))
        c = refs(s.get('Culture Link'))
        if not c or not c[0][1] or c[0][1] not in game.raw: warn.append('culture settings without a culture: ' + game.name(r)); continue
        ckey, cname = giver(c[0][1])
        cultures.setdefault(ckey, {'key': ckey, 'name': cname, 'parent': None})
        key = ckey
        sub = refs(s.get('Sub Culture Link'))
        if sub and sub[0][1] in game.raw:
            key, sname = giver(sub[0][1])
            cultures.setdefault(key, {'key': key, 'name': sname, 'parent': ckey})
            # the player property a sub-culture's own buildings are unlocked by
            m = re.match(r"^(?:(\w+) Subculture - (.+?)|(\w+) (\w+) Subculture)(?: - Faction Property Giver)?$", sub[0][0])
            if m:
                cult, sname_ = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
                sub_by_prop[(cult.lower(), snake(sname_))] = key
        rl = fields(s.get('StructureUpgradeReplacementList'))
        ups = rl.get('Upgrades')
        for it in (ups[1] if ups and ups[0] == 'l' else []):
            g = fields(it)
            generic = refs(g.get('UpgradeLink'))
            for k, v in g.items():
                if not k.startswith('Replace'): continue
                for n, target in refs(v):
                    if target not in B: continue
                    if key not in B[target]['cultures']: B[target]['cultures'].append(key)
                    for gn, gi in generic:
                        if gi and str(int(gi, 16)) not in B[target]['replaces']: B[target]['replaces'].append(str(int(gi, 16)))

    # Primal's animal sub-cultures have no City Settings, only the temples they unlock
    mo = tooltips.read_mo(os.path.join(game.dir, 'Language', 'EN', 'EN.MO'))
    def sub_of_prop(prop):
        m = re.match(r'^(\w+) Subculture - (.+?) - Player Property ID$', prop or '')
        if not m: return None
        cult, sn = m.group(1).lower(), m.group(2)
        if (cult, snake(sn)) in sub_by_prop: return sub_by_prop[(cult, snake(sn))]
        # the text id uses the sub-culture's name or an older one (Oathsworn's Harmony is WISDOM)
        ids = sorted((k for k in mo if re.search(r'SUBCULTURE_%s@([^@]+)@NAME$' % cult.upper(), k)
                      and (k.split('@')[-2] == sn.upper() or mo[k] == sn)), key=len)
        if not ids or cult not in cultures: return None
        key = cult + '_' + snake(mo[ids[0]])
        cultures.setdefault(key, {'key': key, 'name': ids[0], 'parent': cult})
        return key

    # ---- what each building belongs to ----
    for rid, b in B.items():
        props = [p for p, pk in unlocked_by.get(rid, [])] + b['side']
        tome_pack = re.search(r'/Tome_', b['pack']) or any(re.search(r'/Tome_', pk) for p, pk in unlocked_by.get(rid, []))
        if tome_pack:
            b['section'] = 'tome'
            b['tome'] = tome_of.get(b['id'])
            if not b['tome']: warn.append('tome building without a tome in Tomes.json: ' + b['id'])
            continue
        subs = [k for k in (sub_of_prop(p) for p in props) if k]
        if subs and not b['cultures']: b['cultures'] = sorted(set(subs))
        m = re.search(r'/Culture_(\w+)\.rpk', b['pack'])
        if m and not b['cultures']:
            if b['city_tier'] and not b['req']: b['skip'] = True; continue    # tier-1 keeps of free cities
            b['cultures'] = [m.group(1).lower()]
        allreq = b['req'] + b['must']
        if any(p in ROOT_OF and ROOT_OF[p] == 'throne' for p in allreq) or 'ThronesOfBlood' in b['pack']:
            b['section'] = 'throne'
            b['variant'] = ('vampire' if 'Vampire Castle' in b['uname'] or 'ThronesOfBlood' in b['pack']
                            else None if AFFINITY.search(b['uname']) or b['uname'].startswith('Hero Cap')
                            else 'wizard' if b['uname'].startswith("Wizard's Tower") else None)
        elif ('Defense Structure' in b['gives'] or 'Fortification Building Requisite' in b['requisites']
              or any(t.startswith('Economy.ResourceType.Defense') for t in b['tags'])):
            b['section'] = 'fortification'
        else:
            b['section'] = 'city'
    B = {r: b for r, b in B.items() if not b.get('skip')}

    # ---- parents: who grants the properties a building needs ----
    gives = {}
    for rid, b in B.items():
        for p in b['gives']: gives.setdefault(p, []).append(rid)
    def th_of(p):
        m = (re.match(r'^Tier (\d) - Town Hall$', p) or re.match(r'^Town Hall Tier (\d) - Base City Level - ID$', p)
             or re.match(r'^City Tier (\d) - .* - ID$', p))
        return int(m.group(1)) if m else None
    for rid, b in B.items():
        tiers = [th_of(p) for p in b['req'] + b['must'] if th_of(p)]
        if b['city_tier']:
            t = [int(x[-1]) for x in b['tags'] if re.match(r'Worldmap\.Tier\.T\d$', x)]
            tiers += t
        b['th'] = max(tiers) if tiers else None
        if b['section'] == 'tome': b['parents'] = []; continue
        def providers(props, townhall):
            out = []
            for p in props:
                if p in ROOT_OF or (th_of(p) is not None) != townhall: continue
                out += [g for g in gives.get(p, []) if g != rid]
                if not gives.get(p) and not townhall and not p.endswith('Player Property'):
                    warn.append('%s needs %r, which no building grants' % (b['id'], p))
            return out
        # the building it upgrades from, else what it needs besides; town halls only when that is all
        par = (providers(b['req'], False) or providers(b['must'], False)
               or providers(b['req'], True) or providers(b['must'], True)
               or [ROOT_OF[p] for p in b['req'] + b['must'] if p in ROOT_OF]
               or ['throne' if b['section'] == 'throne' else 'town_hall_i_town_center'])
        b['parents'] = list(dict.fromkeys(B[p]['resid'] if p in B else p for p in par))

    # ---- row groups ----
    def line_of(b):
        if b['city_tier']: return 'townhall'
        if b['section'] == 'throne':
            return 'affinity' if AFFINITY.search(b['uname']) else 'heroes' if b['uname'].startswith('Hero Cap') else 'tower'
        if b['section'] != 'city': return b['section']
        fam = [m.group(1) for p in b['gives'] + b['req'] for m in [re.match(r'^Tier \d - (.+)$', p)] if m]
        for f, l in LINES:
            if f in fam: return l
        return None
    for b in B.values(): b['line'] = line_of(b)
    by_resid = {b['resid']: b for b in B.values()}
    for _ in range(4):
        for b in B.values():
            if b['line'] is None:
                # a culture's special building hangs off a town hall but is no town hall itself
                b['line'] = next((by_resid[p]['line'] for p in b['parents'] if p in by_resid
                                  and by_resid[p]['line'] not in (None, 'townhall')), None)
    for b in B.values():
        if b['line'] is None: b['line'] = 'special'

    # ---- culture icons that exist on the site ----
    for c in cultures.values():
        for k in (c['key'], ICON_ALIAS.get(c['key'])):
            if k and os.path.exists(os.path.join(site, 'Icons', 'Text', k + '.png')): c['icon'] = k; break
        else:
            c['icon'] = None    # the page shows the parent culture's icon

    out = []
    for b in sorted(B.values(), key=lambda b: (b['section'], b['line'], b['th'] or 0, b['id'])):
        o = {k: b[k] for k in ('resid', 'section', 'line', 'th', 'parents')}
        for k in ('cultures', 'replaces', 'variant', 'tome'):
            if b.get(k): o[k] = b[k]
        out.append(o)
    roots = [{'key': k, 'name': r['name'], 'icon': r['icon']} for k, r in ROOTS.items()]
    return {'roots': roots, 'cultures': sorted(cultures.values(), key=lambda c: (c['parent'] or c['key'], c['parent'] is not None, c['key'])),
            'structures': out}, warn, B


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', help='output file (default: <site>/Data/GEN/CityTreeLinks.json)')
    ap.add_argument('--acp', help='the mod (default: the first .acp in the Mods folder)')
    args = ap.parse_args()
    cfg = aow4diff.load_config()
    site = cfg.get('site') or DEFAULT_SITE
    acp = args.acp or (aow4diff.mod_acps(cfg) or [None])[0]
    if not acp: sys.exit('no mod .acp found')
    game = Game(cfg, acp)
    data, warn, B = build(game, site)
    data = {'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
            'mod': os.path.splitext(os.path.basename(acp))[0], **data}
    out = args.out or os.path.join(site, 'Data', 'GEN', 'CityTreeLinks.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    from collections import Counter
    print('%d buildings, %d cultures -> %s' % (len(data['structures']), len(data['cultures']), out))
    print('  by section:', dict(Counter(s['section'] for s in data['structures'])))
    print('  city rows:', dict(Counter(s['line'] for s in data['structures'] if s['section'] == 'city')))
    for w in dict.fromkeys(warn): print('  warning:', w)


if __name__ == '__main__':
    main()
