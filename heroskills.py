"""Where a hero skill outside the skill trees comes from, for an aow4db-style website, read from the live game with a mod on top
if one is given; and which skills of the dump no player can get.

    python heroskills.py --site PATH [--acp MOD.acp | --no-mod]

Writes Data/GEN/HeroSkillSources.json:

    skills    {skill id: [{kind, name}]}: what gives a skill that no skill tree holds and that isn't a signature or an
              affinity skill. kind: Ascension, Secret Spell, Training Camp, Ruler Background, Ancient Wonder,
              Cosmic Happening, Oceanic Happening, Quest, Event, Hero, Infusion, Signature, Ruler
    left_out  [{id, name, group, why}]: the dump's skills no player can get, which the old Skill List showed

The dump's skills (Data/EN/HeroSkills.json) are in a class's or ruler's skill tree (a THeroSkillTreeR holds them), signature
skills (offered by the hero's property), affinity skills (offered by the empire's affinity: nothing holds their id), or
else given by what holds their id: a Pantheon hero bonus (the Ascensions, the Secret Spells), the Training Camp's shop, a
story event (a quest's or adventure's reward), a ruler's background loadout. A skill held by nothing that gives one (only
by skills, an old tree's prerequisites; by an effect; by nothing) or by a story realm's script alone is left out, as are
nameless ones. The ids are found in the resources' raw data, as a script's links aren't decoded (spells.holders).
"""
import argparse, collections, datetime, json, os, re, sys

import aow4diff, alltexts, tooltips
from ancientwonders import text_of
from citytree import Game, fields, msgid, hexid
from spells import holders

# what holds a skill without giving it: another skill (an old tree's prerequisite), an effect
NOT_GIVERS = {'THeroSkill', 'TFXResource'}
# what holds the skills of a class's or ruler's tree
TREE = 'THeroSkillTreeR'


def plain(s): return re.sub(r'<[^>]+>', '', s or '').strip()


# an exploration site's event: the site's name in the game's files (not always the wonder's: Broken Tower), its story's
SITE_EVENT = re.compile(r'Exploration Site Event - T\d - \s*(.+?):\s*(.+?)(?:\s*#\d+)?\s+-\s')


class Named:
    def __init__(self, game, T, site):
        self.game, self.T = game, T
        read = lambda *p: json.load(open(os.path.join(site, *p), encoding='utf-8-sig'))
        wonders = read('Data', 'GEN', 'AncientWonders.json')['wonders']
        self.wonders = {w['name'].lower(): w['name'] for w in wonders}
        # a site's name in the files -> the wonders telling its stories, by their titles (Forlorn Puppeteer: Fractured
        # Tower's and Lost Wizard Tower's; "Raiders #01" is Raiders of the Hero Item)
        stories = [(s['title'].lower(), w['name']) for w in wonders for s in w.get('stories', []) if s.get('title')]
        self.site_wonders = collections.defaultdict(set)
        for rid in game.of_class('TStoryEventResource'):
            m = SITE_EVENT.match(game.name(rid))
            if m:
                story = m.group(2).strip().lower()
                self.site_wonders[m.group(1).strip().lower()] |= {w for t, w in stories if t == story or t.startswith(story + ' ')}

    def wonder(self, site):
        """the wonder an exploration site's events are of: by its name, else by its stories' titles"""
        key = site.strip().lower()
        return self.wonders.get(key) or ' or '.join(sorted(self.site_wonders.get(key, ()))) or site.strip()

    def text(self, rid):
        for field in ('Screen Name', 'Name', 'Hero Skill Name', 'NameLink', 'Title'):
            node = fields(self.game.node(rid)).get(field)
            if node and node[0] == 'o' and msgid(node):
                t = alltexts.plain_name(text_of(self.T, rid, field))
                if t: return t
        return None

    def story_realm(self, rid):
        """a story realm's event, which no normal map has: in a story realm's pack (a DLC's StoryRealm, the base game's
        Base_ShadowRealm and the like; not Happenings_Cosmic_Events_EldritchRealms), or named for one"""
        return bool(re.search(r'StoryRealm|Realm_Scripts|/Base_\w+Realm\.rpk', self.game.pack[rid]) or
                    re.match(r'\s*(EnA|Ena|AP\d|ER\d|DD\d|GK\d|EA\d|SotAM?\d|ToB\d)', self.game.name(rid)))

    def source(self, rid):
        """what a resource holding a skill is, as a source; None for what gives none (a story realm's script)"""
        game = self.game
        cls, name = game.cls(rid), game.name(rid)
        if cls == 'TPlayerEmpireHeroBonusTrait':
            if 'Secret Spell' in name: return {'kind': 'Secret Spell', 'name': name.split(' - ')[0].strip()}
            return {'kind': 'Ascension', 'name': re.sub(r'^Ascended ', '', name.split(' - ')[0].strip())}
        if cls == 'THeroSkillShopItem': return {'kind': 'Training Camp', 'name': 'Training Camp'}
        if cls == 'TLoadoutLordPerk':
            if 'Ysariel' in name: return None
            return {'kind': 'Ruler Background', 'name': self.text(rid) or name.split(' - ')[0].strip()}
        if cls == 'TStoryEventResource':
            if self.story_realm(rid): return None
            m = re.match(r'Exploration Site Event - T\d - \s*(.+?):', name)
            if m: return {'kind': 'Ancient Wonder', 'name': self.wonder(m.group(1))}
            m = re.match(r'Exploration Site Event - T\d - \s*(.+?)\'s Arcanum Adventure', name)
            if m: return {'kind': 'Ancient Wonder', 'name': m.group(1).strip() + "'s Arcanum"}
            m = re.match(r'Minor Happening \((Cosmic|Oceanic)\) - (.+?) - ', name)
            if m: return {'kind': m.group(1) + ' Happening', 'name': m.group(2).strip()}
            m = re.match(r'(Primal Subculture Quest)', name)
            if m: return {'kind': 'Quest', 'name': 'Primal Communion quest'}
            if name.startswith('Hero Quest'): return {'kind': 'Quest', 'name': 'Hero quest'}
            return {'kind': 'Event', 'name': 'Event'}
        if cls == 'THeroUnitR': return {'kind': 'Hero', 'name': self.text(rid) or name}
        if cls == 'TEquipmentUpgradeR': return {'kind': 'Infusion', 'name': self.text(rid) or name}
        if cls == 'TSignatureHeroSkill': return {'kind': 'Signature', 'name': name.split(' - ')[-1].strip()}
        if cls in ('TShopItem', 'TLordDefinition'): return {'kind': 'Ruler', 'name': self.text(rid) or name}
        return None


def build(game, T, site):
    skills = json.load(open(os.path.join(site, 'Data', 'EN', 'HeroSkills.json'), encoding='utf-8-sig'))
    held = holders(game, [hexid(s['resid']) for s in skills])
    named = Named(game, T, site)
    sources, left_out = {}, []
    for s in skills:
        rid = hexid(s['resid'])
        by = held.get(rid, set())
        out = lambda why: left_out.append({'id': s['id'], 'name': s['name'], 'group': s.get('group_name'), 'why': why})
        if not s['name'].strip():
            out('No name')
            continue
        if s['type'] == 'signature' or s.get('group_name') == 'Affinity Hero Skill Group': continue
        if any(game.cls(u) == TREE for u in by): continue
        found = {}
        for u in by:
            if game.cls(u) in NOT_GIVERS: continue
            src = named.source(u)
            if src: found.setdefault((src['kind'], src['name']), src)
        if found:
            sources[s['id']] = sorted(found.values(), key=lambda x: (x['kind'], x['name']))
        elif any(game.cls(u) == 'THeroSkill' for u in by):
            out('Only other skills hold it: an old tree\'s prerequisite')
        elif by:
            out('Only a story realm or an effect holds it')
        else:
            out('Nothing gives it' + (' (the game\'s Invalid Hero Skills group)' if s.get('group_name') == 'Invalid Hero Skills' else ''))
    return sources, left_out


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
    T = alltexts.Texts(game, texts)
    sources, left_out = build(game, T, site)
    out = os.path.join(site, 'Data', 'GEN', 'HeroSkillSources.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
                   'mod': os.path.splitext(os.path.basename(acp))[0] if acp else None, 'skills': sources, 'left_out': left_out},
                  f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%d skills outside the trees given by something, %d left out -> %s' % (len(sources), len(left_out), out))


if __name__ == '__main__':
    main()
