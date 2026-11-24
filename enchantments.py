"""Unit enchantments for an aow4db-style website, from the live game with the mod: which units each one can go on and
what it does to them, so the website's unit cards can show an enchanted unit.

    python enchantments.py [--site PATH] [--acp MOD.acp | --no-mod]

Writes Data/GEN/UnitEnchantments.json. The site folder is --site, else config.json's "site"; the mod is --acp, else
the first .acp in the Mods folder.

    enchantments  {spell id (as in Data/EN/Spells.json): [upgrade, ...]}, one entry per unit upgrade the spell gives
                  (most give one; Dormant Enchantment one per class), each:
                  filter      {any, none, all}: the unit requisites the upgrade asks (any of, none of, all of)
                  passives    what the unit panel shows: {slug} of a property the site's abilities have, else {name,
                              text}: the property's Screen Name and Screen Description, filled in with its own values
                              (the site's markup, as all.json's); none: the site shows the spell itself
                  abilities   [{slug, per_tier}]: the abilities it gives (in Abilities.json or LinkedAbilities.json),
                              with the damage they add for each tier of the unit (Ice Nova: +4 for each past the first)
                  stats       {hp, armor, resistance, status_resistance}: unconditional changes
                  resistances {damage type: n}: unconditional Damage Reduction, as the site's resistances
                  attacks     [{filter {any, none, all} of ability requisites, notes [{name, text}] (the lines the
                              game's ability tooltip adds, whatever their conditions, each with its numbers), damage (the dump's
                              way: "4<damagefrost></damagefrost> "), existing (only to the damage types the attack
                              has), per_tier, multiplier, range, accuracy}]: what its ability modifiers do to the
                              abilities they apply to; numbers only for what applies whatever the target and the hit
    units         {resid: [requisite, ...]}: each unit's requisites that some filter names
    abilities     {ability slug: [requisite, ...]}: the same for the abilities of the site's units
    multipliers   {resid: [{filter, multiplier}]}: a unit's own damage multipliers, whatever the hit (a Mythic unit's
                  x1.1 to weapon attacks): the dump's damage has them; the site takes them out, adds an enchantment's
                  damage, and puts them back with the enchantment's. In the game the bonuses add up and the result is
                  rounded up: a Mythic Kraken's Melee Strike, 20 raw (22), with Empowered Beasts' +20% and Force of
                  Nature's +4 Blight, is 20 x 1.3 = 26 and 4 x 1.3 = 5.2, 6 (the maintainer's screenshots, 2026-11-24)

How the game links them (checked 2026-11-24 against the site's unit classes, which match exactly):
- A unit enchantment is a spell with Is Unit Enchantment. Its targeters' effects give a side property
  (TSideUnitUpgradeProperty) whose Unit Upgrade Link is a TUnitUpgrade: Requisite Filter (Required any, Forbidden none,
  Must Have all) and Properties.
- A unit has a requisite listed in its Requisites, its tier's or its Type's (the class: "Battle Mage Unit - Requisite"),
  or one whose Property Link (a TUnitRequisite's) is among its properties: Properties, Upgrades, nested groups' List.
- An ability modifier (TUnitAbilityModifierProperty) applies to an ability by its Requisite Filter against the ability's
  Requisites. The dump's ability slug is the ability's id, or its name in lower case, " - " and spaces as "_", without
  ":" and "'", with one or more id suffixes ("melee_strike_(base)_ability_000004E0000000C2").
- What only applies against some targets (a Target Property Filter: +10% against Frozen) is left to the passive's text.
"""
import argparse, collections, json, os, re, sys

import aow4diff, alltexts, tooltips
from citytree import Game, fields, refs, value, msgid, hexid

DAMAGE = [('Physical', 'damagephysical'), ('Blight', 'damageblight'), ('Shock', 'damagelightning'),
          ('Fire', 'damagefire'), ('Spirit', 'damagespirit'), ('Frost', 'damagefrost')]
# what an upgrade's properties are on the way to: nothing of these is shown or counted
LOOKS = {'TUnitModularityProviderProperty'}
# groups that apply only now and then (a condition, a turn's random pick): what they hold is left to the passive's text
SOMETIMES = {'TUnitConditionalPropertyGroup', 'TUnitDynamicPropertyGroup'}


def read(site, *parts):
    return json.load(open(os.path.join(site, *parts), encoding='utf-8-sig'))


def refs_in(node, out):
    """every reference in a node tree"""
    if not node: return
    if node[0] == 'r': out.append(node[2])
    elif node[0] == 'o':
        for (l, o, n) in node[2]: refs_in(n, out)
    elif node[0] == 'l':
        for n in node[1]: refs_in(n, out)


def num(node):
    v = value(node)
    try: return float(v) if v is not None else 0.0
    except ValueError: return 0.0


def block(node):
    """{label: number} of a damage or defense block"""
    out = {}
    if node and node[0] == 'o':
        for label, owner, n in node[2]:
            if value(n) is not None and num(n): out[label] = num(n)
    return out


class Enchantments:
    def __init__(self, game, site, T):
        self.g, self.site, self.T = game, site, T
        self.req_of_prop = collections.defaultdict(set)
        for r in game.of_class('TUnitRequisite'):
            for n, p in refs(fields(game.node(r)).get('Property Link')): self.req_of_prop[p].add(r)
        self.warn = []
        self.granted = set()    # the abilities enchantments give: their requisites are written too

    # ---- units and abilities ----
    def props(self, rid, seen):
        if rid in seen or rid not in self.g.raw: return
        seen.add(rid)
        f = fields(self.g.node(rid))
        for key in ('Properties', 'List', 'Upgrades', 'Unit Upgrade Link'):
            for n, p in refs(f.get(key)): self.props(p, seen)

    def unit_requisites(self, uid):
        f = fields(self.g.node(uid))
        out = {r for n, r in refs(f.get('Requisites'))}
        for n, t in refs(f.get('Tier Resource Link')) + refs(f.get('Type')):
            if t in self.g.raw: out |= {r for n, r in refs(fields(self.g.node(t)).get('Requisites'))}
        seen = set()
        for key in ('Properties', 'Upgrades'):
            for n, p in refs(f.get(key)): self.props(p, seen)
        for p in seen: out |= self.req_of_prop.get(p, set())
        return out

    def ability_resources(self):
        """the dump's ability slug -> the game's ability"""
        def snake(s): return s.lower().replace(' - ', ' ').replace(':', '').replace("'", '').replace('/', '_').strip().replace(' ', '_')
        by = collections.defaultdict(list)
        for r in self.g.raw:
            if self.g.cls(r) == 'TAbilityProperty': by[snake(self.g.name(r))].append(r)
        return by

    def ability_of(self, slug, by):
        if re.fullmatch(r'[0-9a-fA-F]{16}', slug):
            return slug.upper() if slug.upper() in self.g.raw else None
        m = re.match(r'(.*?)((_[0-9A-Fa-f]{16})+)$', slug)
        c = by.get(m.group(1) if m else slug, [])
        return c[0] if c else None

    # ---- an enchantment ----
    def upgrades(self, spell):
        """the TUnitUpgrades a spell gives, through its targeters, their effects and side properties"""
        out, seen, todo = [], set(), [spell]
        while todo:
            r = todo.pop()
            if r in seen or r not in self.g.raw: continue
            seen.add(r)
            c = self.g.cls(r)
            if c == 'TUnitUpgrade':
                out.append(r); continue
            if r != spell and not re.search(r'Targeter|Effect|SideUnitUpgrade', c): continue
            nxt = []
            refs_in(self.g.node(r), nxt)
            todo.extend(nxt)
        return sorted(out)

    def closure(self, rid):
        """an upgrade's properties, through groups (not those that apply only sometimes)"""
        out, stack, seen = [], [rid], set()
        while stack:
            p = stack.pop()
            if p in seen or p not in self.g.raw: continue
            seen.add(p)
            if self.g.cls(p) in SOMETIMES: continue
            f = fields(self.g.node(p))
            for n, q in refs(f.get('Properties')) + refs(f.get('List')):
                if q in self.g.raw and self.g.cls(q) not in LOOKS:
                    out.append(q); stack.append(q)
        return out

    def filter(self, node):
        f = fields(node)
        return {'any': sorted(self.g.name(r) for n, r in refs(f.get('Required Requisites'))),
                'none': sorted(self.g.name(r) for n, r in refs(f.get('Forbidden Requisites'))),
                'all': sorted(self.g.name(r) for n, r in refs(f.get('Must Have Requisites')))}

    @staticmethod
    def empty(node):
        """a filter with nothing in it"""
        f = fields(node)
        return not any(refs(v) for v in f.values())

    # a targeted ability modifier's conditions (TTargetedUnitAbilityModifier, TDamageBoostingAbilityModifier): one set
    # to other than its default limits it to some hits (Savage Strike: Criticals Only)
    CONDITIONS = ('Criticals Only', 'Overwatch Attack', 'Retaliation Attack', 'Flanking Attack', 'Hexes To Move',
                  'Min Range', 'Max Range', 'Max Health Only', 'Water Filter', 'Infiltrated Targets Only',
                  'Own Turn Only', 'Friendly Fire Filter', 'Defense Mode', 'Engaged', 'Attack Count Filter',
                  'Compare Caster/Target Tier', 'Outnumber Filter', 'Min Distance From Turn Start', 'Units Or Obstacles')
    FILTERS = ('Target Requisite Filter', 'Target Property Filter', 'Attacker Filter', 'Ability Requisite Filter',
               'Blocked By Marker Filter')

    def unconditional(self, rid):
        """what applies whatever the target and the hit: no filter of a target, no condition set"""
        f = fields(self.g.node(rid))
        for key in self.FILTERS:
            if key in f and not self.empty(f[key]): return False
        if refs(fields(f.get('Link Filter')).get('Link Partner Effect Link')): return False
        for key in self.CONDITIONS:
            n = f.get(key)
            if n and n[0] == 'v' and n[1] not in (None, 'false', '0', '0.0') and (len(n) < 3 or n[1] != n[2]):
                return False
        return not refs(f.get('Count Property Link')) and not refs(f.get('Multiply By Attacker Property'))

    def text(self, rid, field, context=None):
        """a text link of a resource filled in with its own values, in the site's markup (as all.json's): the
        Single Shot and the repeating version of a modifier share a text, each with its number. context: the
        resources whose values are the text's own (default: the resource itself)"""
        if context is None:
            t = self.T.link_text(rid, field)
        else:
            link = fields(self.g.node(rid)).get(field)
            mid = msgid(link) if link else None
            if not mid or mid not in self.T.T: return None
            found = tooltips.links(link)
            vals = {k: v for k, v in found[0][2] if v not in (None, '')} if found else {}
            t = self.T.fill(self.T.T[mid], vals, mid, tuple(context))
        if not t: return None
        return alltexts.site_markup(t).replace('\0', '<eventColor>{').replace('\1', '}</eventColor>')

    def own_resources(self, up):
        """what an enchantment's upgrade reaches, its modifiers' checks and effects included: a passive's text takes
        its values from these, the lowest of its versions (a repeating attack's 60%, not the Single Shot's 90%), as
        the game shows it"""
        return {r for r in self.T.linked(set(self.closure(up)) | {up}, 3) if r}

    def modifiers(self, node, only_own=False):
        """what a list of ability modifiers does to the abilities it applies to:
        notes      [{name, text}]: the lines the game's ability tooltip shows (Effect Name, Effect Description) of those
                   not kept out of it (Display In Ability Tooltips), whatever their conditions (Savage Strike on a
                   critical hit; Dissonant)
        damage     extra damage that applies whatever the hit (the dump's way of writing it); existing: only to the
                   damage types the attack has (Adding Mode 2)
        per_tier   {damage, skip_first, existing}: extra damage for each tier of the unit (Counting Mode Caster Tier)
        multiplier, range, accuracy
        only_own: an ability's own modifiers (Ice Nova's per-tier damage): what its card leaves out, its damage per tier"""
        g, a, dmg, notes = self.g, {}, collections.Counter(), []
        for n, m in refs(node):
            if m not in g.raw: continue
            mc, mf = g.cls(m), fields(g.node(m))
            note = {'name': self.text(m, 'Effect Name'), 'text': self.text(m, 'Effect Description')}
            if not only_own and value(mf.get('Display In Ability Tooltips')) != 'false' and note['name'] and note not in notes:
                notes.append(note)
            if not self.unconditional(m): continue
            # Adding Mode (no names in the schema): 1 a random channel, 2 the channels the attack has
            mode = value(mf.get('Adding Mode')) or '0'
            if mc == 'TCountingExtraDamageAbilityModifier' and value(mf.get('Counting Mode')) == 'Caster Tier' and mode != '1':
                d = block(mf.get('Damage'))
                a['per_tier'] = {'damage': ''.join('%d<%s></%s> ' % (d[t], tag, tag) for t, tag in DAMAGE if d.get(t)),
                                 'skip_first': value(mf.get('Skip First')) == 'true', 'existing': mode == '2'}
            if only_own: continue
            if mc == 'TExtraDamageAbilityModifier' and mode != '1':
                if mode == '2': a['existing'] = True
                dmg.update(block(mf.get('Damage')))
            elif mc == 'TDamageMultiplierAbilityModifier' and self.empty(mf.get('Damage Channel Filter')) \
                    and num(mf.get('Multiplier')) not in (0, 1):
                a['multiplier'] = round(a.get('multiplier', 1) * num(mf.get('Multiplier')), 4)
            elif mc == 'TRangeBoostingAbilityModifier' and num(mf.get('Range To Add')):
                a['range'] = a.get('range', 0) + int(num(mf.get('Range To Add')))
            elif mc == 'TAccuracyBoostingAbilityModifier' and num(mf.get('Accuracy Modifier')):
                a['accuracy'] = a.get('accuracy', 0) + num(mf.get('Accuracy Modifier'))
        if dmg:
            a['damage'] = ''.join('%d<%s></%s> ' % (dmg[t], tag, tag) for t, tag in DAMAGE if dmg.get(t))
        if notes:
            a['notes'] = notes
        return a

    def upgrade(self, up, site_slugs):
        g = self.g
        f = fields(g.node(up))
        e = {'filter': self.filter(f.get('Requisite Filter')), 'passives': [], 'abilities': [], 'stats': {},
             'resistances': {}, 'attacks': []}
        stats, res = collections.Counter(), collections.Counter()
        # what the unit panel shows: the upgrade's own properties, those that show
        for n, p in refs(f.get('Properties')):
            if p not in g.raw or g.cls(p) in LOOKS or g.cls(p) == 'TAbilityProperty': continue
            pf = fields(g.node(p))
            if value(pf.get('Display in Unit Panel')) == 'false': continue
            if p.lower() in site_slugs:
                e['passives'].append({'slug': site_slugs[p.lower()]}); continue
            name = self.text(p, 'Screen Name')
            if name:
                e['passives'].append({'name': name, 'text': self.text(p, 'Screen Description', self.own_resources(up))})
        for p in self.closure(up):
            c, pf = g.cls(p), fields(g.node(p))
            if c == 'TAbilityProperty':
                if p.lower() in site_slugs:
                    ab = {'slug': site_slugs[p.lower()]}
                    ab.update(self.modifiers(pf.get('Modifiers'), only_own=True))   # its own per-tier damage
                    e['abilities'].append(ab)
                    self.granted.add(p)
                else: self.warn.append('ability not on the site: %s' % g.name(p))
            elif c == 'TStatModifierProperty' and self.unconditional(p):
                for key in ('Bonus', 'Stat Modifiers', 'Stats'):
                    s = fields(pf.get(key))
                    if not s: continue
                    stats['hp'] += num(s.get('Hit Points'))
                    d = block(s.get('Defenses'))
                    stats['armor'] += d.get('Armor', 0); stats['resistance'] += d.get('Resistance', 0)
                    res.update(block(s.get('Damage Reduction')))
            elif c == 'TUnitDefenseProperty' and self.unconditional(p):
                d = block(pf.get('Defense Modification'))
                stats['armor'] += d.get('Armor', 0); stats['resistance'] += d.get('Resistance', 0)
                res.update(block(pf.get('Damage Reduction')))
            elif c == 'TUnitStatusResistanceProperty' and self.unconditional(p):
                stats['status_resistance'] += num(pf.get('Resistance'))
            elif c == 'TUnitAbilityModifierProperty':
                a = {'filter': self.filter(pf.get('Requisite Filter'))}
                a.update(self.modifiers(pf.get('Modifiers')))
                if len(a) > 1: e['attacks'].append(a)
        e['stats'] = {k: int(v) for k, v in stats.items() if v}
        e['resistances'] = {k: int(v) for k, v in res.items() if v}
        return {k: v for k, v in e.items() if v or k == 'filter'}

    def build(self):
        g, site = self.g, self.site
        spells = {hexid(s['resid']): s for s in read(site, 'Data', 'EN', 'Spells.json') if 'Unit Enchantment' in s.get('spellType', '')}
        # the site's slugs as it writes them (an id in upper or lower case), by the id in lower case
        site_slugs = {a['slug'].lower(): a['slug'] for a in read(site, 'Data', 'GEN', 'LinkedAbilities.json')}
        site_slugs.update({a['slug'].lower(): a['slug'] for a in read(site, 'Data', 'EN', 'Abilities.json')})
        out = {}
        for rid, s in spells.items():
            if rid not in g.raw:
                self.warn.append('not in the game: %s' % s['id']); continue
            ups = self.upgrades(rid)
            if not ups:
                self.warn.append('no unit upgrade: %s' % s['id']); continue
            out[s['id']] = [self.upgrade(u, site_slugs) for u in ups]
        # the requisites the filters name, for each unit and each of its abilities
        unit_names = {n for ups in out.values() for u in ups for k in ('any', 'none', 'all') for n in u['filter'][k]}
        dump_units = read(site, 'Data', 'EN', 'Units.json')
        multipliers = {}
        for u in dump_units:
            uid = hexid(u['resid'])
            if uid in g.raw and str(u['resid']) not in multipliers:
                multipliers[str(u['resid'])] = self.unit_multipliers(uid, u)
        multipliers = {k: v for k, v in multipliers.items() if v}
        ability_names = {n for ups in out.values() for u in ups for a in u.get('attacks', [])
                         for k in ('any', 'none', 'all') for n in a['filter'][k]}
        ability_names |= {n for ms in multipliers.values() for m in ms for k in ('any', 'none', 'all') for n in m['filter'][k]}
        units, abilities, by = {}, {}, self.ability_resources()
        for u in dump_units:
            uid = hexid(u['resid'])
            if uid not in g.raw: continue
            if str(u['resid']) not in units:    # a unit per sub-culture is in the dump once each, with its own abilities
                units[str(u['resid'])] = sorted(n for n in (g.name(r) for r in self.unit_requisites(uid)) if n in unit_names)
            for a in u['abilities']:
                if a['slug'] in abilities: continue
                aid = self.ability_of(a['slug'], by)
                if not aid:
                    self.warn.append('ability not found in the game: %s' % a['slug']); continue
                abilities[a['slug']] = sorted(n for n in (g.name(r) for n, r in refs(fields(g.node(aid)).get('Requisites'))) if n in ability_names)
        for aid in self.granted:    # an enchantment's other modifiers can apply to the ability it gives (Ice Nova)
            slug = site_slugs[aid.lower()]
            abilities.setdefault(slug, sorted(n for n in (g.name(r) for n, r in refs(fields(g.node(aid)).get('Requisites'))) if n in ability_names))
        return {'enchantments': out, 'units': units, 'abilities': abilities, 'multipliers': multipliers}

    def unit_multipliers(self, uid, dump_unit):
        """the damage multipliers a unit has of its own, whatever the hit: [{filter, multiplier}], those of one ability
        modifier added up (Mythic Unit - Group's x1.1 to weapon attacks). Its properties, and the groups the dump lists
        as its passives (a class's group: the Mythic one comes no other way); not what applies only sometimes"""
        g, out, seen, stack = self.g, [], set(), []
        f = fields(g.node(uid))
        for key in ('Properties', 'Upgrades'):
            stack += [p for n, p in refs(f.get(key))]
        for p in dump_unit.get('primary_passives', []) + dump_unit.get('secondary_passives', []):
            if re.fullmatch(r'[0-9a-fA-F]{16}', p['slug']): stack.append(p['slug'].upper())
        while stack:
            p = stack.pop()
            if p in seen or p not in g.raw or g.cls(p) in SOMETIMES: continue
            seen.add(p)
            pf = fields(g.node(p))
            for key in ('Properties', 'List', 'Unit Upgrade Link'):
                stack += [q for n, q in refs(pf.get(key))]
            if g.cls(p) != 'TUnitAbilityModifierProperty': continue
            m = 1.0
            for n, mod in refs(pf.get('Modifiers')):
                if mod in g.raw and g.cls(mod) == 'TDamageMultiplierAbilityModifier' and self.unconditional(mod):
                    mf = fields(g.node(mod))
                    if self.empty(mf.get('Damage Channel Filter')): m += num(mf.get('Multiplier')) - 1
            if round(m, 4) != 1:
                out.append({'filter': self.filter(pf.get('Requisite Filter')), 'multiplier': round(m, 4)})
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
    E = Enchantments(game, site, alltexts.Texts(game, tooltips.load_texts(cfg['game'], os.path.dirname(acp) if acp else '')))
    out = E.build()
    path = os.path.join(site, 'Data', 'GEN', 'UnitEnchantments.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write('\n')
    ench = out['enchantments']
    ups = [u for v in ench.values() for u in v]
    print('%d unit enchantments (%d upgrades), %d units, %d abilities -> %s' % (len(ench), len(ups), len(out['units']), len(out['abilities']), path))
    counts = [sum(1 for u in ups if u.get(k)) for k in ('stats', 'resistances', 'attacks', 'abilities', 'passives')]
    print('  with stats %d, resistances %d, attacks %d, abilities %d, passives %d, no passive %d' % (
        tuple(counts) + (sum(1 for u in ups if not u.get('passives')),)))
    for w in sorted(set(E.warn)): print('  ', w)


if __name__ == '__main__':
    main()
