"""Where a hero item comes from, for an aow4db-style website, read from the live game with a mod on top if one is given.

    python heroitems.py --site PATH [--acp MOD.acp | --no-mod] [--verbose]

Writes Data/GEN/HeroItemSources.json:

    items      {item id: [{kind, name}]}: what gives each item of the dump that has a tier. kind:
               Loot              the item rewards of exploration sites, chests, lairs, resource nodes and quests
               Shop              a world map shop (Beast Wrangler, Training Camp, Mystical Seer)
               Dwelling          a dwelling's trade, or its reward when conquered
               Culture Action    a culture's world map action (the Industrious' Prospect Province)
               Starting Loadout  a ruler's or hero's starting equipment
               Culture Default   a culture's default equipment for its heroes (its Default Equipment)
               Ruler Default     a kind of ruler's (the Wizard King's, the Elder Vampires')
               Faction Mount     the mount a faction's heroes ride: a form's, a form trait's, a Pantheon unlock's
               Ancient Wonder, Cosmic Happening, Oceanic Happening, Quest, Event, World Crisis   a story event's reward
    unsourced  [{id, name, tier}]: the items nothing gives

The game draws loot from equipment sets: a set (TEquipmentSet, merged with the others of its SetName) lists items and
sub-sets by name; a category (TEquipmentCategory) lists sets by name; a loot group (TLootGroup) links categories; a reward
set (TRewardSet) links loot groups, and a structure, a pickup, a dwelling or an exploration site holds the reward set. A
story event names a category or a set in its script. The sets of the general rewards (their categories SMALL_, MEDIUM_,
LARGE_ and ANY_) are Loot, whatever gives them; the other sets are what gives them. Besides, a ruler's loadout
(TLoadoutLordPerk), a culture (TFactionPropertyGiver), a kind of ruler (THeroMainType), a faction mount (TFactionMount), a
shop item (THeroEquipmentShopItem), a dwelling's trade (THouseTradeHeroEquipmentOptionR) and a story event hold items
themselves. A story realm's events and scripts give nothing on a normal map, and a unit holding an item only rides it.
"""
import argparse, collections, datetime, json, os, re, struct, sys

import aow4diff, alltexts, stale, tooltips
from citytree import Game, fields, refs, value, hexid
from heroskills import Named
from spells import holders

# the general rewards' categories, by their names
GENERAL = re.compile(r'^(SMALL|MEDIUM|LARGE|ANY)_')
# what holds an id without giving it: an effect, an item's Item Forge type, a unit's look, the AI's random races
NOT_GIVERS = {'TFXResource', 'TEquipmentTypeR', 'TBaseUnitR', 'TRaceGenerateSettingsR', 'TUseableEquipmentResource',
              'TDefaultEquipmentItemResource', 'TEquipmentSet', 'TEquipmentCategory', 'TNameSettings'}
LOOT = {'kind': 'Loot', 'name': 'Loot'}
# what names a category or a set in its script
NAMERS = ('TStoryEventResource', 'TStoryEventAdditionalButtonResource')


def strings(node):
    return [value(v) for v in (node[1] if node and node[0] == 'l' else [])]


class Givers(Named):
    def __init__(self, game, T, site):
        super().__init__(game, T, site)
        self.held = {}
        # a dwelling's name in the game, by the word its resources' names use (the Pirate Dwelling is the Free Captain)
        self.dwellings = {}
        for rid in game.of_class('TCityR'):
            m = re.match(r'(Crystal|Umbral|Bond of Free Captains) Dwelling', game.name(rid))
            if m: self.dwellings[{'Bond of Free Captains': 'Pirate'}.get(m.group(1), m.group(1))] = self.text(rid) or m.group(0)

    def dwelling(self, name):
        m = re.search(r'(Crystal|Umbral|Pirate) Dwelling', name)
        return {'kind': 'Dwelling', 'name': self.dwellings.get(m.group(1), m.group(0))} if m else None

    def holders(self, ids):
        """what holds each id; a small id can turn up in a resource's numbers, so its decoded links must have it"""
        new = sorted({i for i in ids if i not in self.held})
        found = holders(self.game, new) if new else {}
        for i in new:
            us = found.get(i, set())
            if int(i[8:], 16) < 0x100: us = {u for u in us if i in {r for w, n, r in stale.refs(self.game.node(u))}}
            self.held[i] = {u for u in us if self.game.cls(u) not in NOT_GIVERS}
        return {i: self.held[i] for i in ids}

    def story(self, rid):
        """a story event's reward as a source; None for a story realm's"""
        game = self.game
        name = game.name(rid)
        if self.story_realm(rid): return None
        if game.cls(rid) == 'TStoryEventAdditionalButtonResource':
            return {'kind': 'Event', 'name': 'Event'}
        src = super().source(rid)
        if src and src['kind'] == 'Event' and re.search(r'Quest', name): return {'kind': 'Quest', 'name': 'Quest'}
        return src

    def source(self, rid):
        """what a resource giving an item, or the reward set or loot holding it, is, as a source; None for what gives none"""
        game = self.game
        cls, name = game.cls(rid), game.name(rid)
        if cls in NAMERS: return self.story(rid)
        if cls == 'TStructureR':
            if name.startswith('Shop_'): return {'kind': 'Shop', 'name': self.text(rid) or name[5:]}
            return LOOT
        if cls == 'TCityR': return self.dwelling(name)
        if cls == 'TProspectSectorOperationR':
            culture = re.search(r'Culture_(\w+)', game.pack[rid])
            return {'kind': 'Culture Action', 'name': (self.text(rid) or name) + (' (' + culture.group(1) + ')' if culture else '')}
        if cls == 'TScriptedWorldCrisisResource': return {'kind': 'World Crisis', 'name': self.text(rid) or name.split(' - ')[0]}
        if cls in ('TPickupR', 'TSectorBaseR', 'TAdventureSet', 'TSideRewardProperty'): return LOOT
        return None


def build(game, T, site):
    items = [i for i in json.load(open(os.path.join(site, 'Data', 'EN', 'HeroItems.json'), encoding='utf-8-sig')) if i.get('tier_numerals')]
    G = Givers(game, T, site)
    rid_of = {i['id']: hexid(i['resid']) for i in items}

    # the sets, by name: their items, their sub-sets; with the sub-sets' items
    set_items, set_subs = collections.defaultdict(set), collections.defaultdict(set)
    for rid in game.of_class('TEquipmentSet'):
        f = fields(game.node(rid))
        n = value(f.get('SetName'))
        set_items[n] |= {r for w, m, r in stale.refs(f.get('Equipment') or ('l', []))}
        set_subs[n] |= set(strings(f.get('SubSets')))
    def closure(n, seen):
        if n in seen: return set()
        seen.add(n)
        return set_items.get(n, set()).union(*[closure(s, seen) for s in set_subs.get(n, ())])
    in_set = {n: closure(n, set()) for n in set_items}
    cats = {}
    for rid in game.of_class('TEquipmentCategory'):
        f = fields(game.node(rid))
        cats[rid] = (value(f.get('CategoryName')), set().union(*[in_set.get(s, set()) for s in strings(f.get('SetNames'))]))

    # who gives each category: the loot groups linking it, the reward sets holding those, what holds the reward sets; and
    # what holds it itself, or names it
    groups = collections.defaultdict(set)
    for lg in game.of_class('TLootGroup'):
        for w, n, c in stale.refs(game.node(lg)):
            if c in cats: groups[c].add(lg)
    named = collections.defaultdict(set)
    want = {struct.pack('<I', len(n)) + n.encode(): n for n in [c[0] for c in cats.values()] + list(in_set) if n}
    for rid in game.raw:
        if game.cls(rid) in NAMERS:
            for p, n in want.items():
                if p in game.raw[rid]: named[n].add(rid)
    # what holds what, found in one search
    G.holders(list(cats) + list(game.of_class('TLootGroup')) + list(game.of_class('TRewardSet')) + list(rid_of.values()) +
              list(game.of_class('TLoadoutLordPerk')) + list(game.of_class('TFactionMount')) + list(game.of_class('THeroEquipmentShopItem')))
    def givers_of(rid, depth=0):
        """what in the end gives what a loot group, a reward set or a category is linked from"""
        out = set()
        for u in G.holders([rid])[rid]:
            if game.cls(u) in ('TLootGroup', 'TRewardSet') and depth < 3: out |= givers_of(u, depth + 1)
            elif game.cls(u) not in ('TLootGroup', 'TRewardSet'): out.add(u)
        return out

    sources = collections.defaultdict(dict)
    def add(rid_items, src):
        if not src: return
        for i in rid_items: sources[i].setdefault((src['kind'], src['name']), src)
    unknown = collections.Counter()
    for c, (cname, its) in cats.items():
        givers = set().union(*[givers_of(lg) for lg in groups[c]]) | givers_of(c) | named.get(cname, set())
        general = GENERAL.match(cname or '')
        for g in givers:
            if game.cls(g) in NAMERS:
                src = G.story(g)
                if src and general: src = LOOT
            else:
                src = LOOT if general else G.source(g)
                if src is None: unknown['%s | %s (category %s)' % (game.cls(g), game.name(g), cname)] += 1
            add(its, src)
    for n, events in named.items():
        if n in in_set:
            for e in events: add(in_set[n], G.story(e))

    # what holds an item itself
    item_rids = set(rid_of.values())
    for rid, users in G.holders(sorted(item_rids)).items():
        for u in users:
            cls, name = game.cls(u), game.name(u)
            if cls == 'TLoadoutLordPerk':
                # a loadout a ruler type (TLordTypeTemplate, THeroMainType) or a culture's unlocks offer, or a Pantheon unlock;
                # not one a premade ruler alone has (a story realm's too), nor the Free Captain's captains' and pirates' (its
                # house upgrades, its unit pools), nor a Lost Souls hero's
                by = {game.cls(x) for x in G.holders([u])[u]}
                if by & {'TLordTypeTemplate', 'THeroMainType', 'TPropertyUnlockSettings'}: add([rid], {'kind': 'Starting Loadout', 'name': 'Starting loadout'})
                elif 'TPlayerEmpireItem' in by: add([rid], {'kind': 'Starting Loadout', 'name': 'Pantheon'})
            elif cls == 'TFactionPropertyGiver':
                if value(fields(game.node(u)).get('Type')) in (None, 'Culture') or 'Culture' in name:
                    add([rid], {'kind': 'Culture Default', 'name': G.text(u) or name.split(' - ')[0]})
            elif cls == 'THeroMainType':
                add([rid], {'kind': 'Ruler Default', 'name': G.text(u) or name.split(' - ')[-1]})
            elif cls == 'TFactionMount':
                for x in G.holders([u])[u]:
                    xc = game.cls(x)
                    if xc == 'TFactionPropertyGiver': add([rid], {'kind': 'Faction Mount', 'name': (G.text(x) or game.name(x).split(' - ')[0]) + ' form'})
                    elif xc == 'TRaceTrait': add([rid], {'kind': 'Faction Mount', 'name': G.text(x) or game.name(x)})
                    elif xc == 'TPlayerEmpireItem': add([rid], {'kind': 'Faction Mount', 'name': 'Pantheon'})
            elif cls == 'THeroEquipmentShopItem':
                shops = {G.text(x) or game.name(x) for x in G.holders([u])[u] if game.cls(x) == 'TStructureR'}
                for s in shops or {name.split(' - ')[0]}: add([rid], {'kind': 'Shop', 'name': s})
            elif cls == 'THouseTradeHeroEquipmentOptionR':
                add([rid], G.dwelling(name) or {'kind': 'Dwelling', 'name': 'Dwelling'})
            elif cls in NAMERS or cls == 'TScriptedWorldCrisisResource':
                add([rid], G.source(u))
            elif cls not in ('TTriggerR', 'TLordDefinition'):
                unknown['%s | %s (item)' % (cls, name)] += 1

    out, unsourced = {}, []
    for i in items:
        found = sources.get(rid_of[i['id']])
        if found: out[i['id']] = sorted(found.values(), key=lambda x: (x['kind'], x['name']))
        else: unsourced.append({'id': i['id'], 'name': i['name'], 'tier': i['tier_numerals']})
    return out, unsourced, unknown


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--site', help='the website\'s folder (default: "site" in config.json)')
    ap.add_argument('--acp', help='the mod (default: the first .acp in the Mods folder)')
    ap.add_argument('--no-mod', action='store_true', help='read the game alone')
    ap.add_argument('--verbose', action='store_true', help='list what holds an item or its loot that the tool doesn\'t know')
    args = ap.parse_args()
    cfg = aow4diff.load_config()
    site = args.site or cfg.get('site')
    if not site: sys.exit('which website? pass --site or set "site" in config.json')
    acp = None if args.no_mod else args.acp or (aow4diff.mod_acps(cfg) or [None])[0]
    game = Game(cfg, acp)
    texts = tooltips.load_texts(cfg['game'], os.path.dirname(acp) if acp else '')
    T = alltexts.Texts(game, texts)
    items, unsourced, unknown = build(game, T, site)
    if args.verbose:
        for k, n in sorted(unknown.items()): print('  not a source:', k)
    out = os.path.join(site, 'Data', 'GEN', 'HeroItemSources.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
                   'mod': os.path.splitext(os.path.basename(acp))[0] if acp else None, 'items': items, 'unsourced': unsourced},
                  f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%d items with a source, %d with none -> %s' % (len(items), len(unsourced), out))


if __name__ == '__main__':
    main()
