"""Which of the website's abilities and status effects no player meets in the game, though they are in its files, read from
the live game with a mod on top if one is given.

    python inplay.py --site PATH [--acp MOD.acp | --no-mod] [--verbose]

Writes Data/GEN/AbilitiesInPlay.json:

    left_out  [{slug, name, kind (Active, Status), why}]: what the Abilities pages leave out, and what it comes down to

Only what nothing of the dump in play holds is looked at: an active ability the dump's units, hero skills, items and
infusions don't have (the 68 abilities of LinkedAbilities.json, which effects give), and the status effects (which
effects inflict). Each is traced up what holds its id (`spells.holders`), as the game gives it: a status effect through
its descriptions (TMagicStatusEffectDescription), a description through the effects holding it, an effect through its
targeters, abilities, spells, units... A reference under a filter, an immunity, a requisite, an opposing status effect
or a tooltip names a thing without giving it and is skipped, as are visual effects (TFXResource). Three ways the game
gives a thing without holding its id are followed too: a unit's dynamic property data (TUnitDynamicPropertyGroupData)
names its group and gives its properties to what has that group; an ability plugin shows an ability by its name, turning
another ability into it (the Puppeteer signature skill makes the Eldritch Sovereign's Enthrall Puppeteer's Domination);
and a part nothing holds by id is given if something the site has in play names its whole DataStoreTag (the Forbidden
Tome signature skills' Fire Hex, Mass Plague, Renew...).

It is in play unless every way up ends in something no player meets:
    - a hero skill Data/GEN/HeroSkillSources.json leaves out (the old Dragon Lord Auras, "HS - ... DISABLED")
    - a spell Data/GEN/SpellBook.json leaves out (the Empire Tree's Rites)
    - a deprecated unit (the "DEPRECATED UNIT!" passive)
    - a story realm (its pack is a StoryRealm or Base_<Affinity>Realm one; not the handcrafted regions' TSR_ packs, which
      normal realms have)
    - a resource the game's files name as unused (DISABLED, Invalid, DEPRECATED, Test, Cheat Script...); not "Dummy": a
      property with no mechanics of its own is one, and the game gives it (the Forgotten Temple's souls' Doomed)
    - nothing: a part (an ability, effect, property, targeter, modifier, description, hero skill) nothing holds or names
What else is at the top (a story event, a happening, a map site, a trigger) is a way into the game.
"""
import argparse, collections, datetime, json, os, re, sys

import aow4diff
from citytree import Game, fields, refs, value, hexid, msgid
from spells import holders

# a reference under these fields names a thing without giving it
NOT_GIVING = re.compile(r'Filter|Forbidden|Immun|Opposing|Type Link|Tooltip|Blocked|Overriding|Instant Expire|Requisite|'
                        r'Condition|Exclu|Dispel|Prerequisite|Required|Must|Icon|FX|Visuali|Counter')
NOT_GIVERS = {'TFXResource', 'TGCALayerResource', 'TGameIconR', 'TDefaultGameConceptDescriptionResource', 'TGCNoesisTMLIconTag'}
# not "Dummy": a property with no mechanics of its own is one, and the game gives it (the Forgotten Temple's souls' Doomed)
UNUSED = re.compile(r'DISABLED|\bDisabled\b|Invalid|INVALID|\bUnused\b|UNUSED|DEPRECATED|Deprecated|\bTEST\b|\bTest\b|'
                    r'Cheat Script|SCRAPPED|\bOBSOLETE\b|Never Get|Unreachable')
STORY_PACK = re.compile(r'StoryRealm|/Base_\w+Realm\.rpk')
# a part works only when something holds it, as does a hero skill (heroskills.py: what no tree or source holds is left
# out); anything else at the top is a way into the game
PART = re.compile(r'(Effect|Targeter|Property|PropertyGroup|Modifier|Upgrade|Description|AbilityProperty|Ability|HeroSkill)$')
DEPRECATED_PASSIVES = {'000003e300005fe2', '000003e300005fe5', '000003e300005fe7', '000003e300005fe6'}


def paths(node, target, path=()):
    """the field paths under which a decoded resource refers to target"""
    if not node: return
    if node[0] == 'r':
        if len(node) > 2 and node[2] == target: yield path
    elif node[0] == 'o':
        for label, owner, sub in node[2]: yield from paths(sub, target, path + (label,))
    elif node[0] == 'l':
        for sub in node[1]: yield from paths(sub, target, path)


class InPlay:
    def __init__(self, game, site):
        self.game = game
        read = lambda *p: json.load(open(os.path.join(site, *p), encoding='utf-8-sig'))
        # what the website already judges: (in play, what it is)
        self.judged = {}
        for u in read('Data', 'EN', 'Units.json'):
            dead = u['id'] in ('fracture', 'townsman') or any(x['slug'] in DEPRECATED_PASSIVES for x in u['primary_passives'])
            self.judged[hexid(u['resid'])] = (not dead, ('Deprecated unit: ' if dead else 'Unit: ') + (u['name'] or u['id']))
        out = {s['id'] for s in read('Data', 'GEN', 'HeroSkillSources.json')['left_out']}
        for s in read('Data', 'EN', 'HeroSkills.json'):
            if s.get('resid'):
                alive = bool(s.get('name')) and s['id'] not in out
                self.judged[hexid(s['resid'])] = (alive, ('Hero skill: ' if alive else 'Hero skill no one can get: ') + str(s.get('name')))
        bases = {str(t['base_hero_item_resid']) for t in read('Data', 'EN', 'ItemForgeTypes.json') if t.get('base_hero_item_resid')}
        for i in read('Data', 'EN', 'HeroItems.json'):
            if i.get('tier_numerals') or str(i['resid']) in bases: self.judged[hexid(i['resid'])] = (True, 'Hero item: ' + i['name'])
        for i in read('Data', 'EN', 'ItemForgeUpgrades.json'): self.judged[hexid(i['resid'])] = (True, 'Infusion: ' + i['name'])
        book = read('Data', 'GEN', 'SpellBook.json')
        in_book, out_book = {s['id'] for s in book['spells']}, {s['name'] for s in book['left_out']}
        for s in read('Data', 'EN', 'Spells.json'):
            if s['id'] in in_book: self.judged[hexid(s['resid'])] = (True, 'Spell: ' + s['name'])
            elif s['name'] in out_book: self.judged[hexid(s['resid'])] = (False, 'Spell no one can get: ' + s['name'])
        for s in read('Data', 'EN', 'SiegeProjects.json'): self.judged[hexid(s['resid'])] = (True, 'Siege project: ' + s['name'])
        for t in read('Data', 'EN', 'Tomes.json'): self.judged[hexid(t['resid'])] = (True, 'Tome: ' + t['name'])
        for s in read('Data', 'EN', 'StructureUpgrades.json'): self.judged[hexid(s['resid'])] = (True, 'Structure: ' + s['name'].replace('<br>', ' '))
        # a status effect's descriptions, by the property they apply
        self.descriptions = collections.defaultdict(set)
        for d in game.of_class('TMagicStatusEffectDescription'):
            for label, owner, sub in game.node(d)[2]:
                if label == 'Property Link' and sub and sub[0] == 'r' and len(sub) > 2: self.descriptions[sub[2]].add(d)
        # an ability plugin that shows an ability by its name: it turns another ability into that one, which no one
        # holds by id (the Puppeteer signature skill's makes the Eldritch Sovereign's Enthrall Puppeteer's Domination)
        self.plugins = collections.defaultdict(set)
        for p in game.of_class('TAbilityPluginProperty'):
            name = msgid(fields(game.node(p)).get('ScreenNameOverride'))
            if name: self.plugins[name].add(p)

    def shown_by(self, rid):
        """the ability plugins that show an ability by its name"""
        if self.game.cls(rid) != 'TAbilityProperty': return set()
        return self.plugins.get(msgid(fields(self.game.node(rid)).get('Screen Name')), set())

    def dead_end(self, rid, own=False):
        """why no player meets this resource, or None. Its own name (own: what is traced) says nothing: a holder's does"""
        game = self.game
        if rid in self.judged: return None if self.judged[rid][0] else self.judged[rid][1]
        if STORY_PACK.search(game.pack.get(rid, '')): return "A story realm's: " + game.name(rid)
        if not own and UNUSED.search(game.name(rid)): return 'Unused: ' + game.name(rid)
        return None

    def descendants(self, rid, depth=4):
        """what a resource holds, a few steps down: its own parts, whose texts name its tag"""
        out, frontier = set(), {rid}
        for _ in range(depth):
            nxt = set()
            for r in frontier:
                if r not in self.game.raw: continue
                for i in self._refs(self.game.node(r)):
                    if i not in out and i in self.game.raw: out.add(i); nxt.add(i)
            frontier = nxt
        return out

    def _refs(self, node):
        if not node: return
        if node[0] == 'r':
            if len(node) > 2 and node[2]: yield node[2]
        elif node[0] == 'o':
            for label, owner, sub in node[2]: yield from self._refs(sub)
        elif node[0] == 'l':
            for sub in node[1]: yield from self._refs(sub)

    def namers(self, rid):
        """what the website has in play that names a resource's DataStoreTag, the whole tag, in its data (but its own
        parts): a way the game gives it that isn't by id (a Forbidden Tome signature skill's abilities)"""
        tag = value(fields(self.game.node(rid)).get('DataStoreTag'))
        if not tag: return []
        word, own = re.compile(rb'(?<![A-Za-z0-9_])' + re.escape(tag.encode()) + rb'(?![A-Za-z0-9_])'), self.descendants(rid) | {rid}
        return [r for r, (ok, _) in self.judged.items() if ok and r in self.game.raw and r not in own and word.search(self.game.raw[r])]

    def above(self, rid):
        """what a resource names that holds it: a unit's dynamic property data names its group, and gives its properties
        to what has that group"""
        if self.game.cls(rid) != 'TUnitDynamicPropertyGroupData': return set()
        return {i for n, i in refs(fields(self.game.node(rid)).get('Dynamic Group Link')) if i in self.game.raw}

    def gives(self, holder, target):
        """the field under which holder gives target; None where it only names it"""
        game = self.game
        if game.cls(holder) in NOT_GIVERS: return None
        if holder in self.descriptions.get(target, ()): return 'Property Link'
        if holder in self.shown_by(target): return 'ScreenNameOverride'
        if holder in self.above(target): return 'Dynamic Group Link'
        if game.cls(holder) == 'TMagicStatusEffectDescription': return None    # an opposing or overriding description
        found = ['/'.join(p) for p in paths(game.node(holder), target)]
        if not found: return 'raw'          # a script's links aren't decoded: an AddProperty, an event's reward
        giving = [p for p in found if not NOT_GIVING.search(p)]
        return giving[0] if giving else None

    def trace(self, targets, depth=16):
        """{target: (in play, [what it comes down to])}"""
        game = self.game
        parents = collections.defaultdict(dict)
        frontier, seen = set(targets), set(targets)
        for _ in range(depth):
            if not frontier: break
            h = holders(game, list(frontier))
            nxt = set()
            for child in frontier:
                for p in set(h.get(child, ())) | self.descriptions.get(child, set()) | self.shown_by(child) | self.above(child):
                    field = self.gives(p, child)
                    if field is None: continue
                    parents[child][p] = field
                    if p not in seen and p not in self.judged and not self.dead_end(p):
                        seen.add(p)
                        nxt.add(p)
            frontier = nxt
        # the top: judged, a dead end, or with nothing above. A part nothing holds works only if something in play
        # names its DataStoreTag
        alive = {}
        tops = {}
        targets = set(targets)
        for r in seen | {p for ps in parents.values() for p in ps}:
            dead = self.dead_end(r, own=r in targets)
            if dead: alive[r] = False; tops[r] = dead
            elif r in self.judged: alive[r] = True; tops[r] = self.judged[r][1]
            elif not parents.get(r):
                if PART.search(game.cls(r)):
                    alive[r] = None; tops[r] = 'Nothing gives it: %s [%s]' % (game.name(r), game.cls(r))
                else:
                    alive[r] = True; tops[r] = '%s [%s]' % (game.name(r), game.cls(r))
        # a part nothing holds by id is given if something in play names its tag
        for r in [r for r, a in alive.items() if a is None]:
            by = self.namers(r)
            alive[r] = bool(by)
            if by: tops[r] = 'Named by ' + self.judged[by[0]][1]
        changed = True
        while changed:
            changed = False
            for r, ps in parents.items():
                if alive.get(r) or self.dead_end(r, own=r in targets): continue
                if any(alive.get(p) for p in ps):
                    alive[r] = True
                    changed = True
        out = {}
        for t in targets:
            reasons = set()
            self._tops(t, parents, tops, reasons, set())
            out[t] = (bool(alive.get(t)), sorted(reasons))
        return out

    def _tops(self, r, parents, tops, acc, seen):
        if r in seen: return
        seen.add(r)
        if r in tops: acc.add(tops[r]); return
        for p in parents.get(r, ()): self._tops(p, parents, tops, acc, seen)


def at_risk(site):
    """the abilities and status effects nothing of the dump in play holds: {id: (kind, slug, name)}"""
    read = lambda *p: json.load(open(os.path.join(site, *p), encoding='utf-8-sig'))
    abilities = read('Data', 'EN', 'Abilities.json') + read('Data', 'GEN', 'LinkedAbilities.json')
    status = {e['slug'] for e in read('Data', 'EN', 'StatusEffects.json')}
    held = set()
    for u in read('Data', 'EN', 'Units.json'):
        if u['id'] in ('fracture', 'townsman') or any(x['slug'] in DEPRECATED_PASSIVES for x in u['primary_passives']): continue
        for k in ('abilities', 'primary_passives', 'secondary_passives') + tuple('medal_rewards_%d' % n for n in range(2, 7)):
            held |= {x['slug'] for x in u.get(k) or []}
    out = {s['id'] for s in read('Data', 'GEN', 'HeroSkillSources.json')['left_out']}
    for s in read('Data', 'EN', 'HeroSkills.json'):
        if s.get('resid') and s.get('name') and s['id'] not in out: held |= {x['slug'] for x in s.get('abilities') or []}
    bases = {str(t['base_hero_item_resid']) for t in read('Data', 'EN', 'ItemForgeTypes.json') if t.get('base_hero_item_resid')}
    for i in read('Data', 'EN', 'HeroItems.json'):
        if i.get('tier_numerals') or str(i['resid']) in bases: held |= {x['slug'] for x in i['ability_slugs']}
    for i in read('Data', 'EN', 'ItemForgeUpgrades.json'): held |= {x['slug'] for x in i.get('abilities') or []}
    targets = {}
    for a in abilities:
        if a['slug'] in held or not a.get('name'): continue
        kind = 'Status' if a['slug'] in status else 'Active' if 'actionPoints' in a else None
        if kind and re.fullmatch(r'[0-9A-Fa-f]{16}', a['slug']): targets[a['slug'].upper()] = (kind, a['slug'], a['name'])
    return targets


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--site', help='the website\'s folder (default: "site" in config.json)')
    ap.add_argument('--acp', help='the mod (default: the first .acp in the Mods folder)')
    ap.add_argument('--no-mod', action='store_true', help='read the game alone')
    ap.add_argument('--verbose', action='store_true', help='also list what is in play and what it comes down to')
    args = ap.parse_args()
    cfg = aow4diff.load_config()
    site = args.site or cfg.get('site')
    if not site: sys.exit('which website? pass --site or set "site" in config.json')
    acp = None if args.no_mod else args.acp or (aow4diff.mod_acps(cfg) or [None])[0]
    game = Game(cfg, acp)
    targets = {t: v for t, v in at_risk(site).items() if t in game.raw}
    result = InPlay(game, site).trace(list(targets))
    left_out = []
    for t, (alive, why) in sorted(result.items(), key=lambda x: (targets[x[0]][0], targets[x[0]][2])):
        kind, slug, name = targets[t]
        if args.verbose and alive: print('  in play  %-7s %-32s %s' % (kind, name[:32], '; '.join(why)[:150]))
        if not alive:
            left_out.append({'slug': slug, 'name': name, 'kind': kind, 'why': '; '.join(why[:4]) or 'Nothing gives it'})
    out = os.path.join(site, 'Data', 'GEN', 'AbilitiesInPlay.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
                   'mod': os.path.splitext(os.path.basename(acp))[0] if acp else None, 'left_out': left_out},
                  f, indent=1, ensure_ascii=False)
        f.write('\n')
    c = collections.Counter(e['kind'] for e in left_out)
    print('%d abilities and status effects nothing of the dump holds; left out: %d active, %d status -> %s'
          % (len(targets), c['Active'], c['Status'], out))
    for e in left_out: print('  out  %-7s %-32s %s' % (e['kind'], e['name'][:32], e['why'][:160]))


if __name__ == '__main__':
    main()
