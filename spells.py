"""The spells and siege projects for an aow4db-style website, read from the live game, with a mod on top if one is given.

    python spells.py --site PATH [--acp MOD.acp | --no-mod]

Writes Data/GEN/SpellBook.json and Relics.json, and the icons of what the dump leaves out to Icons/SpellIcons/ and Icons/SiegeProjectIcons/.
The site folder is --site, else config.json's "site"; the mod is --acp, else the first .acp in the Mods folder.

Costs, categories, where a spell comes from and DLCs are the live game's. Texts are the dump's (Data/EN/Spells.json): the
game fills in its own values there, where alltexts.Texts can only give the lowest a shared tag holds; only what the
dump leaves out, or a value it lost ("a -hex radius"), is filled here.

    spells   key, id, name, variant (of spells sharing an id, what tells them apart), page, tabs, type, tactical, cost and
             upkeep [[amount, icon]], op (casting points), description, requisites, units, spawns (of a spell that summons
             a unit at random, the unit categories it draws from: spawn_categories), sources, tier, affinities, dlc, icon
    siege    key, id, name, tab, cost, damage, description, sources, tier, affinities, dlc, icon
    sources  kind (Tome, Magic Victory, Hero Skill, Artifact, Secret Spell, Ancient Wonder, Landmark, Cosmic Happening, City Structure,
             Culture, Trait, Ruler), name, and of a tome its id, tier, icon, affinities
    sites    id, name, type, link, image, units: the wonders, landmarks and Oceanic happenings that give units, for a unit's page
    left_out name, why: spells the game has and no one can get, which the old pages listed

A spell is unlocked by a player property (Required Unlock Side Properties); the resources that hold the property's id are
where it comes from. Several spells sharing an id and a text are one card with all their sources (a Counter Stance for each
oath); with different texts each has its own, under its variant's name (an Ancestral Harmony for each animal).
"""
import argparse, bisect, collections, datetime, json, os, re, struct, sys

import aow4diff, alltexts, stale, tooltips
from ancientwonders import GameIcons, text_of
from citytree import Game, fields, refs, names, value, msgid, hexid, units_needing, units_of
from worldmap import dlc_of, key_of

SPELL_CLASSES = ('TCombatSpecialOperationR', 'TSustainedSpecialOperationR', 'TInstantSpecialOperationR',
                 'TRaceTransformationSpecialOperationR', 'TDoomsdaySpecialOperationR', 'TMapSpecialOperationR',
                 'TBoostHouseSpecialOperationR', 'TShareResourcesSpecialOperationR',
                 'TStrategicPositionInstantSpecialOperationR', 'TBoostDiplomaticTokenSpecialOperationR')
# a property nothing gives: how the game switches a spell off, or casts it from elsewhere (the Empire Tree's Rites)
OUT_OF_REACH = re.compile(r'Dummy|DUMMY|Unreachable|Inaccessible|Unobtainable|\(Unused\)|Block Test|Never Get')
STORY = re.compile(r'Realm_Scripts')
# what the data doesn't tell, from the maintainer: spell resources that are no spell a player has
NOT_SPELLS = {'construct_population': 'Not in use', 'signet_of_loyalty': 'Not in use', 'move_portal': 'Not a spell: the button that moves a Shrouded Isles portal',
              'bone_horror_ritual': 'An old mod spell, not in use', 'astral_wisp_ritual': "Replaced by Astral Keeper Ritual in the mod"}
STRATEGIC = {'Summon Spell': 'Summon', 'Terraforming Spell': 'Terraforming', 'Enemy Army Spell': 'EnemyArmy',
             'Friendly Army Spell': 'FriendlyArmy', 'World Spell': 'World', 'Sustained World Spell': 'World', 'City Spell': 'City',
             'Sustained City Spell': 'City', 'Spell Victory': 'Victory'}
TRANSFORMATION = {'Minor Race Transformation': 'Minor', 'Major Race Transformation': 'Major'}
AFFINITIES = {'empireorder': 'Order', 'empirechaos': 'Chaos', 'empirenature': 'Nature', 'empirematter': 'Materium',
              'empirearcana': 'Astral', 'empireshadow': 'Shadow'}
# a count the dump writes in one form for any number ("3 Turn", "15 Temporary Hit Point"); not "a 3 Turn cooldown"
COUNTED = re.compile(r'(?<![\w./-])(\d+)(\s*(?:<\w+></\w+>\s*)?(?:<hyperlink>)?\s*(?:<\w+></\w+>\s*)?(?:Temporary )?)'
                     r'(Hit Point|Turn|Thrall|Soul|Transformation)\b(?!s\b)(?!(?:</hyperlink>)?\s*(?:cooldown|duration|limit))', re.I)
LOST_VALUE = re.compile(r'(?<![\d%])-hex\b')


def plain(s): return re.sub(r'<[^>]+>', '', s or '').strip()


def dump_text(s):
    """a dump's text with what it does differently from the site: numbers run into a link's icon, one form for any count"""
    t = re.sub(r'(\d)(<hyperlink>)(<\w+></\w+>)\s*', r'\1 \2\3 ', s or '')
    return COUNTED.sub(lambda m: m.group(0) if m.group(1) == '1' else m.group(1) + m.group(2) + m.group(3) + 's', t)


def resources(node):
    block = fields(node).get('Entries') if node else None
    return [((refs(fields(e).get('Resource')) or [(value(fields(e).get('Resource')),)])[0][0], int(float(value(fields(e).get('Value')) or 0)))
            for e in (block[1] if block and block[0] == 'l' else [])]


def spawn_categories(game, rid, sets_of):
    """the unit categories of a spell that summons a unit at random (their CategoryName, all_spawnsets.json's category), [] for
    the others: the categories its summon effects name, reached through its targeters and effects. A category the game still
    has but no effect names is no part of it: Evolved's Raise Undead Army summons from its T3 category alone, while the game
    keeps its Low and Mid ones.
    At random is a summon from a category of more than one unit, or a recruit offering fewer of them to choose from than it
    has (Call Wild Animal: 4 of the 14 of its default animals). One that offers them all is the player's choice (Call Young
    Dragon, Summon Sprite), and a category of one unit is that unit (Crystallize Essence's Lithorine of each tier). A spell
    that is random anywhere keeps all its categories: Summon Elemental's single spirits tell what each land gives.
    sets_of: {CategoryName: its units' sets}, of every category of that name, as the game adds them up"""
    out, random, seen, todo = [], False, set(), [(rid, 0)]
    while todo:
        r, depth = todo.pop(0)
        if r in seen or r not in game.raw: continue
        seen.add(r)
        for n, i in alltexts.all_refs(game.node(r)):
            if i not in game.raw: continue
            if game.cls(i) == 'TUnitCategory':
                category = value(fields(game.node(i)).get('CategoryName'))
                if not category: continue
                if category not in out: out.append(category)
                units = len(set(sets_of.get(category, [])))
                # FNumRecruitmentOptions' default in the schema is 3
                offered = int(value(fields(game.node(r)).get('Num Recruit Options')) or 3) if 'Recruit' in game.cls(r) else 1
                if units > max(offered, 1) or (offered == 1 and units > 1): random = True
            elif depth < 5 and re.search(r'Targeter|Effect|Event', game.cls(i)): todo.append((i, depth + 1))
    return out if random else []


def amounts(text):
    """[[amount, icon]] of a dump's cost ("150<gold></gold> 150<mana></mana> ")"""
    return [[int(n), tag] for n, tag in re.findall(r'(\d+)<(\w+)></\w+>', text or '')]


def holders(game, ids):
    """for each id, the resources holding it, found in their raw data: all of it in one buffer, searched for each id's
    high half (few differ) with the low half checked, as an id's two halves are written low first"""
    rids = list(game.raw)
    starts, at = [], 0
    for rid in rids:
        starts.append(at)
        at += len(game.raw[rid])
    data = b''.join(game.raw[rid] for rid in rids)
    by_high = collections.defaultdict(dict)
    for i in ids: by_high[struct.pack('<I', int(i[:8], 16))][struct.pack('<I', int(i[8:], 16))] = i
    out = collections.defaultdict(set)
    for high, lows in by_high.items():
        at = data.find(high, 4)
        while at != -1:
            i = lows.get(data[at - 4:at])
            if i:
                rid = rids[bisect.bisect_right(starts, at - 4) - 1]
                if rid != i: out[i].add(rid)
            at = data.find(high, at + 1)
    return out


class Sources:
    """who gives a player property"""
    def __init__(self, game, T, site, props):
        self.game, self.T = game, T
        read = lambda *p: json.load(open(os.path.join(site, *p), encoding='utf-8-sig'))
        self.tomes = {hexid(t['resid']): t for t in read('Data', 'EN', 'Tomes.json')}
        self.wonders = {w['name'].lower(): w for w in read('Data', 'GEN', 'AncientWonders.json')['wonders']}
        self.happenings = {plain(h['name']).lower(): h for h in read('Data', 'GEN', 'Happenings.json')['happenings']}
        # a small id can turn up in a resource's numbers: its decoded links say whether it really holds one. Not for the
        # others, as a script's links aren't decoded (a story event's AddProperty)
        self.users = {p: {u for u in us if int(p[8:], 16) >= 0x100 or p in {r for w, n, r in stale.refs(game.node(u))}} for p, us in holders(game, props).items()}
        # a tome's skill lists its sub-skills, which give the properties, and names its tome
        self.tome_of_sub = collections.defaultdict(list)
        for rid in game.of_class('TGridSkill'):
            f = fields(game.node(rid))
            for n, group in refs(f.get('Group Link')):
                if group in self.tomes:
                    for m, sub in refs(f.get('Sub Skills')): self.tome_of_sub[sub].append(self.tomes[group])
        # a skill outside the tome's grid that needs a property one of its skills gives is the tome's too: Pirate Bond's
        # six captains, each unlocked by the tome's Conjure Pirate Captain and a House Trade with that captain
        tome_of_prop = {p: tomes for sub, tomes in self.tome_of_sub.items()
                        for n, p in refs(fields(game.node(sub)).get('Side Properties'))}
        for rid in game.of_class('TGridSkill'):
            f = fields(game.node(rid))
            if any(group in self.tomes for n, group in refs(f.get('Group Link'))): continue
            for n, p in refs(f.get('Required Side Properties')):
                for m, sub in refs(f.get('Sub Skills')): self.tome_of_sub[sub].extend(tome_of_prop.get(p, []))
        # the structure whose threshold bonus it is
        self.landmark = {}
        for rid in game.of_class('TStructureR'):
            if not game.name(rid).endswith('- Landmark'): continue
            giver = next((fields(e) for e in (fields(game.node(rid)).get('Entries') or ('l', []))[1] if value(fields(e).get('List')) == 'STRUCTUREPROPERTYGIVER'), {})
            for n, i in refs(giver.get('Sector Properties')) + refs(giver.get('Adjacent Sector Properties')):
                self.landmark[i] = alltexts.plain_name(text_of(T, rid, 'Screen Name'))

    def named(self, rid, *fallback):
        for field in ('Screen Name', 'Name', 'Hero Skill Name', 'NameLink', 'Title'):
            node = fields(self.game.node(rid)).get(field)
            if node and node[0] == 'o' and msgid(node):
                t = alltexts.plain_name(text_of(self.T, rid, field))
                if t: return t
        return fallback[0] if fallback else self.game.name(rid)

    def one(self, u):
        """what a resource holding the property is, as a source"""
        game, cls, name = self.game, self.game.cls(u), self.game.name(u)
        if 'cheat' in name.lower(): return []
        if cls == 'TGridSubSkill':
            return [self.tome(t) for t in self.tome_of_sub.get(u, [])] \
                or ([{'kind': 'Magic Victory', 'name': 'Magic Victory research'}] if 'MagicVictory' in game.pack[u] else [])
        if cls == 'THeroSkill':
            if 'Secret Spell' in name: return [{'kind': 'Secret Spell', 'name': 'Secret Spell'}]
            return [{'kind': 'Hero Skill', 'name': self.named(u, re.sub(r'^HS |\s*- Hero Skill Normal$', '', name))}]
        if cls == 'TTokenDefinition':
            if 'Secret Scroll' in name: return [{'kind': 'Secret Spell', 'name': 'Secret Spell'}]
            return [{'kind': 'Artifact', 'name': self.named(u, name.split(' - ')[0].strip())}]
        if cls == 'TStoryEventResource':
            if 'Arcanum Adventure' in name: return [{'kind': 'Secret Spell', 'name': 'Secret Spell'}]
            m = re.match(r'Exploration Site Event - T\d - \s*(.+?):', name)
            if m: return [self.wonder(m.group(1))]
            m = re.match(r'Minor Happening \(Cosmic\) - (.+?) - ', name)
            if m: return [self.happening(m.group(1))]
            return []
        if cls == 'TSectorBaseR': return [self.wonder(re.sub(r'\s*Structure$', '', name))]
        if cls == 'TSectorThresholdColonyProperty': return [{'kind': 'Landmark', 'name': self.landmark.get(u) or re.sub(r' Landmark - .*$', '', name)}]
        if cls == 'TStructureUpgradeR': return [{'kind': 'City Structure', 'name': self.named(u)}]
        if cls == 'TFactionPropertyGiver': return [{'kind': 'Culture', 'name': re.sub(r'\s*- Faction Property Giver$', '', name).replace(' Subculture - ', ': ')}]
        if cls == 'TRaceTrait': return [{'kind': 'Trait', 'name': self.named(u, name.split(' - ')[0])}]
        if cls == 'TLordTypeTemplate': return [{'kind': 'Ruler', 'name': name.replace('Lord Type Template - ', '').replace(' - ', ': ')}]
        if 'Fey Invasion' in name: return [self.happening('Toll of Seasons')]
        return []

    def wonder(self, name):
        w = self.wonders.get(name.strip().lower())
        return {'kind': 'Ancient Wonder', 'name': w['name'] if w else name.strip(), 'tab': w['tier'] if w else None}

    def happening(self, name):
        h = self.happenings.get(name.strip().lower())
        return {'kind': 'Cosmic Happening', 'name': plain(h['name']) if h else name.strip(), 'tab': h['group'] if h else None}

    def tome(self, t):
        return {'kind': 'Tome', 'name': t['name'], 'id': t['id'], 'tier': t.get('tier'), 'icon': t.get('icon'),
                'affinities': sorted({AFFINITIES[a] for a in re.findall(r'<(\w+)></', t.get('affinities') or '') if a in AFFINITIES})}

    def of(self, props, own):
        out = {}
        for n, p in props:
            for u in self.users.get(p, ()):
                if u == own: continue
                for s in self.one(u): out.setdefault((s['kind'], s['name']), s)
        order = ('Tome', 'Magic Victory', 'Hero Skill', 'Secret Spell', 'Artifact', 'Ancient Wonder', 'Landmark', 'Cosmic Happening', 'City Structure', 'Culture', 'Trait', 'Ruler')
        return sorted(out.values(), key=lambda s: (order.index(s['kind']), s['name']))


def spell_tabs(kind, sources):
    """the page and tabs a spell is on, by its kind ("Damage/Debuff Spell" on both) and, of an enchantment, its tomes"""
    if kind in STRATEGIC: return 'Strategic', [STRATEGIC[kind]]
    if kind in TRANSFORMATION: return 'Transformation', [TRANSFORMATION[kind]]
    if kind == 'Combat Enchantment': return 'Tactical', ['Enchantment']
    if kind == 'Combat Summon Spell': return 'Tactical', ['Summon']
    if kind == 'Unit Enchantment':
        tabs = []
        for s in sources:
            if s['kind'] != 'Tome': continue
            if s['id'] == 'general_research': tabs.append('Generic')
            elif not s['affinities']: tabs.append('Cultures')
            else: tabs += s['affinities']
        return 'Enchantment', list(dict.fromkeys(tabs)) or ['Other']
    parts = re.sub(r' Spell$', '', kind).split('/')
    if all(p in ('Healing', 'Buff', 'Damage', 'Debuff') for p in parts): return 'Tactical', parts
    return None, []


def spell_affinities(sources, f):
    out = {a for s in sources for a in s.get('affinities', [])}
    if not out and not any(s['kind'] == 'Tome' for s in sources):
        found = {m.group(1) for n in names(f.get('Requisites')) for m in [re.match(r'(\w+) Affinity Content', n)] if m}
        if len(found) < 6: out = found    # all six: of no one
    return sorted(out)


def tier_of(sources):
    tiers = [s['tier'] for s in sources if s['kind'] == 'Tome' and s.get('tier')]
    return min(tiers) if tiers else None


def learn_icons(game, dump, cost_of):
    """a resource's icon on the site, from the dump's costs"""
    out = {}
    for s in dump:
        rid = hexid(s['resid'])
        if rid not in game.raw: continue
        written = amounts(s.get('casting_cost') or s.get('cost'))
        for resource, amount in cost_of(rid)[0]:
            same = [tag for n, tag in written if n == amount]
            if len(same) == 1: out.setdefault(resource, same[0])
    return out


def build(game, T, site, icons, warn):
    read = lambda *p: json.load(open(os.path.join(site, *p), encoding='utf-8-sig'))
    dump = {hexid(s['resid']): s for s in read('Data', 'EN', 'Spells.json')}
    siege_dump = {hexid(s['resid']): s for s in read('Data', 'EN', 'SiegeProjects.json')}
    live = [r for c in SPELL_CLASSES for r in game.of_class(c)]
    projects = game.of_class('TSiegeProjectR')
    missing = [r for r in dump if r not in game.raw]
    if missing: warn.append('in the dump, not in the game: ' + ', '.join(dump[r]['name'] for r in missing))

    props = {i for r in live for n, i in refs(fields(game.node(r)).get('Required Unlock Side Properties'))}
    props |= {i for r in projects for n, i in refs(fields(game.node(r)).get('Required Side Properties'))}
    sources = Sources(game, T, site, props)

    def spell_cost(rid):
        link = refs(fields(game.node(rid)).get('Cost Data Link'))
        c = fields(game.node(link[0][1])) if link and link[0][1] in game.raw else {}
        return resources(c.get('Cost')), resources(c.get('Upkeep')), int(float(value(c.get('Operation Point Cost')) or 0))

    def siege_cost(rid):
        return resources(fields(game.node(rid)).get('Cost')), [], 0

    sets_of = {}
    for r in game.of_class('TUnitCategory'):
        f = fields(game.node(r))
        sets = f.get('Sets')
        sets_of.setdefault(value(f.get('CategoryName')), []).extend(value(x) for x in (sets[1] if sets and sets[0] == 'l' else []))

    icon_of = learn_icons(game, dump.values(), spell_cost)
    icon_of.update({k: v for k, v in learn_icons(game, siege_dump.values(), siege_cost).items() if k not in icon_of})
    def worded(rows, name):
        for resource, amount in rows:
            if resource not in icon_of: warn.append('%s: a cost in a resource with no known icon, %s' % (name, resource))
        return [[amount, icon_of.get(resource, 'mana')] for resource, amount in rows]

    # what the dump calls a kind of spell, by the game's screen type
    kind_of_screen = {}
    for rid, s in dump.items():
        if rid in game.raw:
            for n in names(fields(game.node(rid)).get('ScreenTypeLink')): kind_of_screen.setdefault(n, s['spellType'])

    made, left_out = [], []
    spell_dir = os.path.join(site, 'Icons', 'SpellIcons')
    for rid in live:
        f = fields(game.node(rid))
        s = dump.get(rid)
        name = s['name'] if s else alltexts.plain_name(text_of(T, rid, 'Screen Name'))
        unlock = refs(f.get('Required Unlock Side Properties'))
        if s and s['id'] in NOT_SPELLS:
            left_out.append({'name': name, 'why': NOT_SPELLS[s['id']]})
            continue
        if any(OUT_OF_REACH.search(n) for n, i in unlock):
            left_out.append({'name': name, 'why': 'Nothing gives %s' % unlock[0][0].strip()})
            continue
        if not s and STORY.search(game.pack[rid]):
            left_out.append({'name': name, 'why': 'A story realm\'s'})
            continue
        cost, upkeep, op = spell_cost(rid)
        if s:
            kind_html = s['spellType']
            description = dump_text(s.get('description'))
            if LOST_VALUE.search(description):
                filled = text_of(T, rid, 'Screen Description')
                if not LOST_VALUE.search(filled): description = filled
            written = amounts(s.get('casting_cost'))
            if sorted(a for a, t in written) != sorted(a for r, a in cost):
                warn.append('%s: the dump costs %s, the game %s; is the dump older?' % (name, s.get('casting_cost'), [a for r, a in cost]))
            icon = None
            if s.get('icon') and os.path.exists(os.path.join(spell_dir, s['icon'] + '.png')) and s['id'] not in WRONG_ICON: icon = 'SpellIcons/' + s['icon'] + '.png'
            elif os.path.exists(os.path.join(site, 'Icons', 'SummonIcons', s['id'] + '.png')): icon = 'SummonIcons/' + s['id'] + '.png'
        else:
            kind_html = next((kind_of_screen[n] for n in names(f.get('ScreenTypeLink')) if n in kind_of_screen), '')
            description = text_of(T, rid, 'Screen Description')
            key = key_of(name)
            written = icons.write(refs(f.get('Large Icon Link')), spell_dir, key)
            icon = 'SpellIcons/' + written if written else None
        came = sources.of(unlock, rid)
        page, tabs = spell_tabs(plain(kind_html), came)
        if not page:
            warn.append('%s: no page for the kind "%s"' % (name, plain(kind_html)))
            continue
        if not icon: warn.append('%s: no icon' % name)
        made.append({'key': s['id'] if s else key_of(name), 'id': s['id'] if s else key_of(name), 'name': name, 'variant': None, 'page': page, 'tabs': tabs,
                     'type': kind_html, 'tactical': game.cls(rid) == 'TCombatSpecialOperationR', 'cost': worded(cost, name), 'op': op,
                     'upkeep': worded(upkeep, name), 'description': description,
                     'requisites': [e['requisite'] for e in (s or {}).get('enchantment_requisites', [])],
                     'units': [u['slug'] for u in (s or {}).get('summoned_units', [])], 'spawns': spawn_categories(game, rid, sets_of),
                     'sources': came, 'tier': tier_of(came),
                     'affinities': spell_affinities(came, f), 'dlc': dlc_of(game, rid), 'icon': icon, '_label': game.name(rid)})

    # the same spell given by several tomes is one card; different texts under one id tell what they're for
    together = {}
    for e in made:
        same = together.get((e['id'], e['description'], str(e['cost'])))
        if same:
            for x in e['sources']:
                if x not in same['sources']: same['sources'].append(x)
            same['sources'].sort(key=lambda x: x['name'])
            same['tabs'] = list(dict.fromkeys(same['tabs'] + e['tabs']))
            same['affinities'] = sorted(set(same['affinities']) | set(e['affinities']))
            same['tier'] = tier_of(same['sources'])
        else: together[(e['id'], e['description'], str(e['cost']))] = e
    spells = list(together.values())
    by_id = collections.Counter(e['id'] for e in spells)
    for e in spells:
        if by_id[e['id']] > 1:
            part = re.sub(r'\s*\(.*?\)', '', e['_label']).split(' - ')
            e['variant'] = part[1].strip() if len(part) > 2 else None
            e['key'] = e['id'] + '_' + key_of(e['variant'] or e['_label'])
    for e in spells: del e['_label']
    doubled = [k for k, n in collections.Counter(e['key'] for e in spells).items() if n > 1]
    if doubled: warn.append('keys used twice: ' + ', '.join(doubled))
    spells.sort(key=lambda e: (e['name'].lower(), e['key']))

    siege = []
    siege_dir = os.path.join(site, 'Icons', 'SiegeProjectIcons')
    for rid in projects:
        f = fields(game.node(rid))
        s = siege_dump.get(rid)
        name = s['name'] if s else alltexts.plain_name(text_of(T, rid, 'Screen Name'))
        need = refs(f.get('Required Side Properties'))
        if any(OUT_OF_REACH.search(n) for n, i in need):
            left_out.append({'name': name, 'why': 'Nothing gives %s' % need[0][0].strip()})
            continue
        cost = resources(f.get('Cost'))
        if s:
            description = dump_text(s.get('description'))
            icon = s.get('icon') + '.png' if s.get('icon') else None
            if sorted(a for a, t in amounts(s.get('cost'))) != sorted(a for r, a in cost):
                warn.append('%s: the dump costs %s, the game %s; is the dump older?' % (name, s.get('cost'), [a for r, a in cost]))
        else:
            description = text_of(T, rid, 'Screen Description')
            icon = icons.write(refs(f.get('Large Icon Link')), siege_dir, key_of(name))
        if not icon: warn.append('%s: no icon' % name)
        came = sources.of(need, rid)
        if not need: tab = 'Everyone'
        elif any(x['kind'] == 'Tome' for x in came): tab = 'Tomes'
        else: tab = 'Other'
        siege.append({'key': s['id'] if s else key_of(name), 'id': s['id'] if s else key_of(name), 'name': name, 'tab': tab, 'cost': worded(cost, name),
                      'damage': int(float(value(f.get('SiegeHealthDamage')) or 0)), 'description': description, 'sources': came, 'tier': tier_of(came),
                      'affinities': sorted({a for x in came for a in x.get('affinities', [])}), 'dlc': dlc_of(game, rid), 'icon': icon})
    siege.sort(key=lambda e: plain(e['name']).lower())
    left_out.sort(key=lambda e: e['name'])
    return spells, siege, left_out


def unit_sites(site):
    """the wonders and landmarks that give units, for a unit's page, and Lava Prison's Primordials, its adventures' defenders,
    which are to be had nowhere else; and the Oceanic happenings whose choices unlock units (Mating Season's Seamare)"""
    read = lambda *p: json.load(open(os.path.join(site, *p), encoding='utf-8-sig'))
    ids_of = collections.defaultdict(list)
    for u in read('Data', 'EN', 'Units.json'):
        if u['id'] not in ids_of[u['name']]: ids_of[u['name']].append(u['id'])
    out = []
    for w in read('Data', 'GEN', 'AncientWonders.json')['wonders']:
        names_ = w['units'] or (sorted({n for s in w['stories'] for n in s.get('mandatory', [])}) if w['key'] == 'lava_prison' else [])
        units = [i for n in names_ for i in ids_of.get(n, [])]
        if units:
            e = {'id': w['key'], 'name': w['name'], 'type': '<%swonder></%swonder> %s Ancient Wonder' % (w['tier'].lower(), w['tier'].lower(), w['tier']),
                 'link': 'AncientWonders.html?type=%s&' % w['tier'], 'image': 'StructurePics/%s.png' % w['key'], 'units': units}
            if not w['units']: e['other_unlock'] = True
            out.append(e)
    for l in read('Data', 'GEN', 'Landmarks.json')['landmarks']:
        if l['units']:
            # the Leviathan's parts have neither
            image = 'StructurePics/%s.png' % l['picture'] if l['picture'] else 'WorldStructures/%s' % l['icon'] if l['icon'] else None
            out.append({'id': l['key'], 'name': l['name'], 'type': 'Landmark', 'link': 'Landmarks.html', 'image': image, 'units': l['units']})
    for h in read('Data', 'GEN', 'Happenings.json')['happenings']:
        if h.get('units'):
            out.append({'id': h['key'], 'name': h['name'], 'type': 'Oceanic Happening', 'link': 'OceanicHappenings.html?type=%s&' % h['category'],
                        'image': 'CosmicHappenings/%s' % h['icon'], 'units': h['units']})
    return out


def artifacts(game, T, site, dump, warn):
    """Data/GEN/Relics.json as the site reads it, with the game's names, the spells each unlocks and the units it lets cities
    draft (unit_unlock: Heart of the Maer's Ghost Ship, Nimue's Secret Scroll's Direcaster); the ids and pictures stay
    upstream's, found by text id"""
    needed_by = units_needing(game, site)
    old = {e['extraLookup']: e for e in json.load(open(os.path.join(site, 'Data', 'GEN', 'Relics.json'), encoding='utf-8-sig'))}
    spells_of = collections.defaultdict(list)
    for rid, s in dump.items():
        if rid in game.raw and s['id'] not in NOT_SPELLS:
            for n, p in refs(fields(game.node(rid)).get('Required Unlock Side Properties')): spells_of[p].append(s['id'])
    out = {}
    for rid in game.of_class('TTokenDefinition'):
        name = game.name(rid)
        if ' - Artifact' not in name: continue
        f = fields(game.node(rid))
        text = re.sub(r'@NAME$', '', msgid(f.get('Screen Name')) or '')
        e = old.get(text)
        if not e:
            warn.append('%s: an artifact the site has no picture for' % name)
            continue
        spells = list(dict.fromkeys(i for n, p in refs(f.get('Properties')) for i in spells_of.get(p, [])))
        units = units_of([p for n, p in refs(f.get('Properties'))], needed_by)
        out[text] = {**e, 'name': alltexts.plain_name(text_of(T, rid, 'Screen Name')), **({'spell': [{'id': i} for i in spells]} if spells else {}),
                     **({'unit_unlock': [{'id': i} for i in units]} if units else {})}
        if not spells: out[text].pop('spell', None)
        if not units: out[text].pop('unit_unlock', None)
    for text, e in old.items():
        if text not in out: warn.append('%s: in the list, not in the game' % e['name'])
    return [out[t] for t in old if t in out]


# the dump's icon for these is another spell's (Data/src/dataloader.js, incorrectIconOverrideList)
WRONG_ICON = {'summon_zealot', 'summon_lightbringer', 'conjure_divine_beacon', 'summon_lesser_snow_spirit', 'summon_wind_rager', 'summon_balor',
              'summon_lesser_magma_spirit', 'summon_horned_god', 'summon_corrupt_soul', 'summon_lesser_light_spirit', 'summon_blessed_soul'}


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
    game.cfg = cfg
    texts = tooltips.load_texts(cfg['game'], os.path.dirname(acp) if acp else '')
    T = alltexts.Texts(game, texts)
    warn = []
    spells, siege, left_out = build(game, T, site, GameIcons(game), warn)
    dump = {hexid(s['resid']): s for s in json.load(open(os.path.join(site, 'Data', 'EN', 'Spells.json'), encoding='utf-8-sig'))}
    relics = artifacts(game, T, site, dump, warn)
    with open(os.path.join(site, 'Data', 'GEN', 'Relics.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(relics, f, indent=1, ensure_ascii=False)
        f.write('\n')
    out = os.path.join(site, 'Data', 'GEN', 'SpellBook.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
                   'mod': os.path.splitext(os.path.basename(acp))[0] if acp else None, 'spells': spells, 'siege': siege, 'sites': unit_sites(site), 'left_out': left_out},
                  f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%d spells, %d siege projects, %d left out -> %s' % (len(spells), len(siege), len(left_out), out))
    for w in dict.fromkeys(warn): print('  warning:', w)


if __name__ == '__main__':
    main()
