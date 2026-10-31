"""Ancient Wonders for an aow4db-style website: each wonder's texts, income and rally units, and each of its stories
with the event's texts, options, combat enchantments and defenders, read from the live game, with a mod on top if one
is given.

    python ancientwonders.py --site PATH [--out PATH] [--acp MOD.acp | --no-mod]

Writes Data/GEN/AncientWonders.json and the enchantments' icons missing from Icons/CombatEnchantments/. The site folder
is --site, else config.json's "site"; the mod is --acp, else the first .acp in the Mods folder.

How the game ties them together: a wonder (TSectorBaseR) names an AdventureCategory, its sets (TAdventureSet) are the
stories, a set's tag is what its Exposition event checks on entering, and the Aftermath event names the Exposition.

    wonders       key, name, tier, dlc, description, lore, counts (INTERFACE@COUNTS_AS's field), income [[resource,
                  value]], units (rally units' names), stories
    stories       key, names (the sites it can appear as), title, text, options, enchantments (on whatever is chosen:
                  the event's own, and those every way into the battle adds), mandatory and pool (defenders' names),
                  aftermath {title, text, options}
    options       text, kind (the button's type), costs and rewards [[resource, level]], adds and removes (enchantment
                  keys), gains (properties given to units), check {affinity, header, success, failure, failure_adds}
    enchantments  key -> name, description, positive, icon (file name in Icons/CombatEnchantments/)

A story is written as if your leader enters: the game's own variant of the text for that, so it says "you" and rarely
needs a name. What only play decides is worded generally and marked <eventColor>: "your Hero", a defender's name.
"""
import argparse, collections, datetime, json, os, re, struct, sys

import aow4diff, alltexts, clbicons, realmtraits, script, spawnsets, tmlicons, tooltips
from citytree import Game, fields, refs, names, value, tags, msgid, income

TIERS = ('Bronze', 'Silver', 'Gold')
COUNTS = {'Conduit': 'conduit', 'Farm': 'farm', 'Forester': 'forester', 'Mines': 'mine', 'Quarry': 'quarry',
          'Research Post': 'research_post'}
# a wonder placed only by a story realm's script has no adventure of its own or repeats the normal one
LEFT_OUT = re.compile(r'APSR\d|Story Realm|DEPRECATED')
# play's values in general words; a defender's name or the wonder's own name comes first
# EventStackLeader is the defenders' leader (Forlorn Puppeteer), EventHeroB a Hero met in the story (the sleeper in Rose
# of Thorns, the rugged stranger), neither of them yours
WORDS = {'EventHero': 'your Hero', 'EventHeroB': 'the Hero', 'EventStackLeader': 'enemy leader', 'PlayerLeader': 'Leader',
         'EventCity': 'your city', 'EventItem': 'Hero Item', 'EventRace': 'race', 'EventHouse': 'their House',
         'EventGod': "(the god's name)", 'EventGodir': "(the god's name)", 'EventAttribute': '(its domain)',
         'EventMount': 'mount', 'EventSpell': 'spell', 'EventRelic': 'relic', 'EventArtifact': 'artifact',
         'EventWeapon': 'weapon', 'EventDragon': 'Dragon', 'EventDragon2': 'Dragon', 'EventGeo': 'land',
         'SilverKnight': 'Silver Knight'}
# whose it is, where the word itself reads badly with an 's
OWNER = {'EventGod': "the god's", 'EventGodir': "the god's", 'PlayerLeader': 'your', 'EventHero': 'your', 'EventHouse': 'their House\'s'}
UNIT_VAR = re.compile(r'(?:Event|Enemy|Oracle|Protect|Random|Giant)\w*Unit\w*$')
MARK, END = '\ue000', '\ue001'
GRAMMAR = re.compile(r'<<([A-Za-z]+):([^<>,]*?)(?:,([^<>]*?))?>>')
CHOICE = re.compile(r'<<\{?\w*\{([^{}/]*)/[^{}]*\}\}?>>')
COST = re.compile(r'(?:Global|Local) (.+?) (Cost|Reward)(?: \(\w+\))? - ([\w ]+?) \[')
RESOURCE = {'Structure Production': 'Production', 'Unit Production': 'Draft', 'Draft': 'Draft'}


def tree(node):
    """every node inside one"""
    if not node: return
    yield node
    if node[0] == 'o':
        for l, o, v in node[2]: yield from tree(v)
    elif node[0] == 'l':
        for x in node[1]: yield from tree(x)


def link_of(node):
    """the resource a script statement links to directly, of its arguments"""
    for k in script.children(node):
        rl = fields(k).get('ResourceLink') if k[0] == 'o' else None
        if rl is not None:
            i = value(fields(rl).get('ID')) if rl[0] == 'o' else rl[2]
            if i: return i
    return None


def macro_body(game, rid):
    data = fields(game.node(rid)).get('Data')
    out = []
    for n in tree(fields(data).get('field_20')):
        if n[0] == 'o' and n[1] == 'TTriggerData': out.append(n); break
    # the statements are the first object's list: a macro's body is one level of them
    body = fields(data).get('field_20')
    if body and body[0] == 'l':
        out = [x for row in body[1] for x in (row[1] if row[0] == 'l' else [row])]
    return out


# macros every story calls: entering the battle (its Expedition Leader) and a failed check's general penalty
SKIP_MACRO = re.compile(r'Action: Leave |Failed Affinity Check')
ATTACK = re.compile(r'Action: Attack - ')


class Effects:
    """what a script does, macros followed: enchantments added and removed, costs and rewards"""
    def __init__(self, game):
        self.game = game
        self.adds, self.removes, self.costs, self.rewards = [], [], [], []
        self.failed_adds, self.fights = [], False    # what a failed check still adds; whether it enters the battle

    def block(self, section):
        f = fields(section)
        objs = fields(f.get('Objects')).get('Objects') if f.get('Objects') else None
        for it in (objs[1] if objs and objs[0] == 'l' else []): self.statement(it)

    def statement(self, it):
        if script.is_list(it): return self.block(it)
        if value(fields(it).get('Enabled')) == 'false': return
        tid, tname, fname = script.names(it)
        i = link_of(it)
        if fname == 'AddProperty' and i: self.adds.append(i)
        elif fname == 'RemoveProperty' and i: self.removes.append(i)
        elif i and i in self.game.raw and self.game.cls(i).startswith('TTriggerMacro') and ATTACK.search(self.game.name(i)):
            self.fights = True
        elif i and i in self.game.raw and self.game.cls(i).startswith('TTriggerMacro') and not SKIP_MACRO.search(self.game.name(i)):
            m = COST.search(self.game.name(i))
            if m:
                pair = [RESOURCE.get(m.group(1), m.group(1)), m.group(3)]
                (self.costs if m.group(2) == 'Cost' else self.rewards).append(pair)
            for s in macro_body(self.game, i): self.statement(s)
        blocks = [k for k in script.children(it) if script.is_list(k)]
        # an affinity check: what it does on success. Its failure is told by the check's texts
        if fname == 'AffinityCheck':
            if len(blocks) > 1:
                failed = Effects(self.game)
                failed.block(blocks[1])
                self.failed_adds += failed.adds
                self.fights = self.fights or failed.fights
            blocks = blocks[:1]
        for b in blocks: self.block(b)


class Story:
    """an event's texts in the site's format, with play's values worded for this wonder and story"""
    def __init__(self, T, wonder, units, known=()):
        self.T, self.wonder, self.units, self.known = T, wonder, units, set(known)

    def word(self, var):
        var = var.strip('{}').split('.')[0]
        if var == 'EventStructure': return self.wonder
        if var in self.units: return self.units[var]
        if var in WORDS: return WORDS[var]
        if UNIT_VAR.search(var): return self.units.get('EventUnit') or 'enemy'
        return alltexts.spaced(re.sub(r'^Event', '', var))

    def mark(self, s):
        """a word only play decides, marked; the wonder's own name and a story's fixed units are what the game shows"""
        return s if s == self.wonder or s in self.known else MARK + s + END

    def grammar(self, m):
        how, who, third = m.group(1), m.group(2).strip(), m.group(3)
        if how in ('G', 'g'):    # "the <<G:walls^p,EventStructure>>": the owner's noun, the article is the text's own
            noun = re.sub(r'\^\w*', '', who)
            if not third: return noun
            # the text's own "the" comes before it: "the Golden Ziggurat's walls"
            if third.strip('{} ').split('.')[0] == 'EventStructure': return self.wonder + "'s " + noun.strip()
            return self.possessive(third.strip(), False) + ' ' + noun.strip()
        if how == 'm':           # the plural of a noun, a link's too
            if who.startswith('{GLOBAL.') or who.startswith('['): return m.group(0)
            return self.mark(alltexts.plural(self.word(who)))
        cap = how.startswith('C')
        how = how[-1]
        if how == 'o':
            # "points their blade": no gender to go by, and the name again reads badly
            var = who.strip('{}').split('.')[0]
            s = self.possessive(who, cap) if var == 'EventStructure' or var in OWNER else ('Their' if cap else 'their')
        elif how == 'a':
            w = self.word(who)
            s = ('An ' if cap else 'an ') if w[:1].lower() in 'aeiou' else ('A ' if cap else 'a ')
            s += self.mark(w)
        elif how == 'r': s = 'itself' if who.strip('{}').split('.')[0] == 'EventStructure' else 'themselves'
        else: s = self.pronoun(who, cap)
        return s

    def article(self, m):
        """the text's "a" before a word that takes another: "an enemy leader", "the Hero"""
        if m.group(2).startswith(('{GLOBAL.', '{LOC.')): return m.group(0)
        w = self.word(m.group(2))
        if w.startswith(('your ', 'the ', 'their ', '(')): return m.group(2)
        return (m.group(1) + 'n ' if w[:1].lower() in 'aeiou' else m.group(1) + ' ') + m.group(2)

    def thing(self, who):
        var = who.strip('{}').split('.')[0]
        w = self.word(var)
        return w, var

    def pronoun(self, who, cap):
        w, var = self.thing(who)
        if var == 'EventStructure': return 'It' if cap else 'it'
        s = self.mark(w) if w.startswith(('your ', 'the ', 'their ', '(')) else 'the ' + self.mark(w)
        if not cap: return s
        return MARK + s[1].upper() + s[2:] if s[0] == MARK else s[0].upper() + s[1:]

    def possessive(self, who, cap):
        w, var = self.thing(who)
        if var == 'EventStructure': return 'Its' if cap else 'its'
        if var in OWNER:
            s = self.mark(OWNER[var])
            return MARK + s[1].upper() + s[2:] if cap else s
        return self.pronoun(who, cap) + "'s"

    def fill(self, mid, typed=None):
        s = self.T.T.get(mid)
        if s is None: return ''
        s = CHOICE.sub(lambda m: m.group(1), s)
        s = re.sub(r'<<c:(.*?)>>', r'', s)    # a capital the text's own words already have
        s = re.sub(r'\b([Aa])n? (\{[^{}]+\})', self.article, s)
        s = GRAMMAR.sub(self.grammar, s)
        def var(m):
            key = m.group(0)
            if key.startswith(('{GLOBAL.', '{LOC.')) or '>' in key or (typed and key in typed): return key
            return self.mark(self.word(key))
        s = re.sub(r'\{[^{}]+\}', var, s)
        s = self.T.fill(s, typed or {}, mid, ())
        s = re.sub(r'<<c:(.*?)>>', r'\1', s)    # and in what a concept's link brings
        s = alltexts.site_markup(s).replace('\0', '<eventColor>').replace('\1', '</eventColor>')
        s = s.replace(MARK, '<eventColor>').replace(END, '</eventColor>')
        s = re.sub(r'\b(?:[Tt]he|[Aa]n?) ((?:<eventColor>)?(?:your|their)\b)', r'\1', s)    # "the {EventHero}"
        s = re.sub(r'\b([Tt]he) (<eventColor>)?the\b', lambda m: (m.group(2) or '') + m.group(1), s)
        # a word worded for play at a sentence's start
        return re.sub(r'((?:^|[.!?]\s+|\s")<eventColor>)([a-z])', lambda m: m.group(1) + m.group(2).upper(), s)

    def text(self, node):
        """a TStoryEventText: the variant for your leader if it has one"""
        f = fields(node)
        rows = f.get('TextVariants')
        for row in (rows[1] if rows and rows[0] == 'l' else []):
            rf = fields(row)
            if (value(rf.get('field_0')) or '').startswith('HERO_IS_PLAYE'):
                got = msgid(rf.get('TextLink'))
                if got: return self.fill(got)
        return self.fill(msgid(f.get('DEFAULT')) or '')


def bare(s): return alltexts.plain_name(re.sub(r'</?eventColor>', '', s))


def spell_links(s):
    """a link to a world map spell has no concept for the site to show ("Summon Vault Guardian"): its name, in white"""
    link = re.compile(r'\[hyperlink,GLOBAL\.SPECIAL_OPERATIONS\.\w+\]((?:(?!\[hyperlink,).)*?)\[/hyperlink\]', re.S)
    while link.search(s):
        s = link.sub(lambda m: m.group(1) if '<span' in m.group(1) else '<span style="color:white">%s</span>' % m.group(1), s)
    return re.sub(r'\[spellref,[^\]]*\](.*?)\[/spellref\]', r'<span style="color:white">\1</span>', s, flags=re.S)


def text_of(T, rid, *field):
    t = T.link_text(rid, *field)
    t = re.sub(r'<<c:(.*?)>>', r'\1', t or '')
    return spell_links(alltexts.site_markup(t)).replace('\0', '<eventColor>').replace('\1', '</eventColor>')


def unit_pools(game, texts):
    """a TUnitPoolCategory's pools by its name: [units] and [what else they draw from]"""
    by_pool = {e['pool']: e for e in spawnsets.build_pools(game, texts)}
    pools, others = collections.defaultdict(list), collections.defaultdict(list)
    for r in game.of_class('TUnitPoolCategory'):
        f = fields(game.node(r))
        for n, i in refs(f.get('UnitPools')):
            e = by_pool.get(n) or by_pool.get(game.name(i) if i in game.raw else n, {})
            cat = value(f.get('CategoryName'))
            for u in e.get('units', []):
                if u not in pools[cat]: pools[cat].append(u)
            for u in e.get('others', []):
                u = re.sub(r' - tag ?filter$', '', u, flags=re.I)
                if u not in others[cat]: others[cat].append(u)
    return pools, others


class GameIcons:
    """TGameIconR images out of the gameplay icon libraries, as realmtraits.py reads them"""
    def __init__(self, game):
        self.game, self.images, self.ids = game, {}, {}
        for path in clbicons.libraries(game.cfg['game']):
            lib = tmlicons.library_name(path)
            if lib and lib.startswith('GAMEPLAY_ICONS'):
                library = clbicons.Library(path)
                self.images[lib] = (library, dict(library.images()))
                self.ids[lib] = realmtraits.atlas_names(path)

    def write(self, link, folder, key):
        """a reference list's first icon as <folder>/<key>.png, written once (an image already on the site is kept);
        the file's name, None if the icon can't be read"""
        out = os.path.join(folder, key + '.png')
        if os.path.exists(out): return key + '.png'
        raw = self.game.raw.get(link[0][1]) if link else None
        m = tmlicons.TXA.search(raw) if raw else None
        if not m or m.group(1).decode() not in self.images: return None
        lib = m.group(1).decode()
        at = m.end()
        length = struct.unpack_from('<I', raw, at)[0]
        ident = struct.unpack_from('<I', raw, at + 4 + length)[0] if at + 8 + length <= len(raw) else 0
        found = self.ids[lib].get(int(m.group(2)), {}).get(ident - 1)
        if not found or found not in self.images[lib][1]: return None
        tex = self.images[lib][1][found]
        if tex['format'] != clbicons.RGBA: return None
        clbicons.write_png(out, tex['width'], tex['height'], self.images[lib][0].pixels(tex))
        return key + '.png'


def build(game, T, site, texts):
    warn = []
    cats = {value(fields(game.node(r)).get('CategoryName')): r for r in game.of_class('TAdventureCategory')}
    pools, others = unit_pools(game, texts)
    events = {e: str(game.node(e)) for e in game.of_class('TStoryEventResource')
              if game.name(e).startswith(('Exploration Site Event', 'Ancient Wonder'))}

    icons = GameIcons(game)
    def icon_of(rid, key):
        return icons.write(refs(fields(game.node(rid)).get('Icon Link')), os.path.join(site, 'Icons', 'CombatEnchantments'), key)

    enchantments = {}
    def named(rid): return alltexts.plain_name(text_of(T, rid, 'Screen Name')) if rid in game.raw else ''
    def is_enchantment(rid):
        return rid in game.raw and bool(named(rid)) and bool(msgid(fields(game.node(rid)).get('Description')))
    def enchant(rid):
        """an enchantment's key, None if it isn't one the game shows: a helper with no name, or a property given to a
        unit (the option's "gains")"""
        if not is_enchantment(rid): return None
        f = fields(game.node(rid))
        mid = msgid(f.get('Screen Name'))
        # what Builder.js makes of a text id for the picture's name: the part after COMBATPROPERTIES_TACTICAL@
        key = '_'.join(re.sub(r'^DLC@\w+?@', '', re.sub(r'@NAME$', '', mid)).split('@')[1:]).lower()
        if key not in enchantments:
            enchantments[key] = {'name': alltexts.plain_name(text_of(T, rid, 'Screen Name')),
                                 'description': text_of(T, rid, 'Description'),
                                 'positive': value(f.get('Is Positive')) == 'true', 'icon': icon_of(rid, key)}
            if not enchantments[key]['icon']: warn.append('%s: no icon' % key)
        return key
    def keys(rids): return list(dict.fromkeys(k for k in map(enchant, rids) if k))

    def made(actions):
        """the variables a script's Create statements give a resource with a name: {EventUnit}, {EventItem}"""
        out = {}
        for n in tree(actions):
            if n[0] != 'o' or n[1] != 'TTriggerData' or not (script.names(n)[2] or '').startswith('Create'): continue
            var, thing = None, None
            for x in tree(n):
                if x[0] != 'o': continue
                if x[1] == 'TTriggerDataVariableNamePreset': var = var or value(fields(x).get('Count'))
                rl = fields(x).get('ResourceLink')
                if rl is not None and rl[0] == 'o' and not thing:
                    i = value(fields(rl).get('ID'))
                    if i in game.raw and named(i): thing = named(i)
            if var and thing: out[var] = thing
        return out

    def script_is_check(button):
        """whether the option's script has an affinity check"""
        for n in tree(fields(fields(button).get('Script')).get('Actions')):
            if n[0] == 'o' and n[1] == 'TTriggerData' and script.names(n)[2] == 'AffinityCheck': yield True

    def options(event, S):
        f = fields(game.node(event))
        rows = fields(f.get('Buttons')).get('List') if f.get('Buttons') else None
        out = []
        for b in (rows[1] if rows and rows[0] == 'l' else []):
            bf = fields(b)
            E = Effects(game)
            E.block(fields(bf.get('Script')).get('Actions'))
            kind = refs(bf.get('Type'))
            o = {'text': S.text(bf['Text Button']),
                 'kind': re.sub(r'^Event Button Type: ', '', kind[0][0]) if kind else '',
                 'costs': E.costs, 'rewards': E.rewards, 'adds': keys(E.adds), 'removes': keys(E.removes),
                 'gains': list(dict.fromkeys(named(i) for i in E.adds if named(i) and not is_enchantment(i)))}
            # for the story's enchantments: on whichever way this option goes, if it comes to a battle
            sure = keys(E.adds) if not any(script_is_check(b)) else [k for k in keys(E.adds) if k in keys(E.failed_adds)]
            o['_fights'], o['_sure'] = E.fights, sure
            # an affinity check's texts: what it tries, and how it ends
            says = {}
            for n in tree(fields(bf.get('Script')).get('Actions')):
                mid = value(fields(n).get('msgid')) if n[0] == 'o' else None
                m = re.search(r'@(HEADER|SUCCESS|FAILURE)$', mid or '')
                if m and m.group(1) not in says:
                    typed = {'{CombatPropertyHyperlink}': enchantments[o['adds'][0]]['name']} if o['adds'] else {}
                    says[m.group(1)] = S.fill(mid, typed)
            if 'Affinity' in o['kind']:
                o['check'] = {'affinity': o['kind'].split()[0], 'header': says.get('HEADER', ''),
                              'success': says.get('SUCCESS', ''), 'failure': says.get('FAILURE', ''),
                              'failure_adds': keys(E.failed_adds)}
            out.append(o)
        return out

    def body(event, S):
        f = fields(game.node(event))
        parts = [S.text(f[k]) for k in ('Text [Body Header]', 'Text [Body Lore]', 'Text [Body Footer]') if f.get(k)]
        return [p for p in parts if p]

    def story(rid, S_args):
        sf = fields(game.node(rid))
        tag_list = tags(sf.get('Tags'))
        first = [e for e, s in events.items() if any(t in s for t in tag_list)]
        first.sort(key=lambda e: 'Exposition' not in game.name(e))
        name_rows = sf.get('Structure Name Override')
        site_names = [alltexts.plain_name(T(msgid(n))) for n in (name_rows[1] if name_rows and name_rows[0] == 'l' else [])
                      if msgid(n) and msgid(n) in T.T]
        mandatory, ids_of = [], {}
        for n, i in refs(sf.get('MandatoryUnits')):
            if i in game.raw:
                u = alltexts.plain_name(text_of(T, i, 'Screen Name'))
                if u and u not in mandatory: mandatory.append(u)
        cat = value(sf.get('Unit Pool Category Name')) or ''
        pool = [u for u in pools.get(cat, []) if u not in mandatory]
        out = {'key': re.sub(r'\W+', '_', tag_list[0].split('.')[-1]).lower() if tag_list else game.name(rid),
               'names': list(dict.fromkeys(site_names)), 'mandatory': mandatory, 'pool': pool + others.get(cat, []),
               'enchantments': keys(i for n, i in refs(sf.get('Combat Properties')))}
        if not first:
            warn.append('%s: no event' % game.name(rid))
            return out
        expo = first[0]
        # who the texts call what: the story's own units
        lead = mandatory[0] if mandatory else (pool[0] if pool else 'enemy')
        rest = mandatory[1:] + pool
        units = {'EnemyUnit': lead, 'EventUnit': lead, 'EventUnitA': lead, 'EventUnitB': rest[0] if rest else lead,
                 'EventUnit2': rest[0] if rest else lead, 'EventUnit3': rest[1] if len(rest) > 1 else lead}
        expo_raw = T.T.get(msgid(fields(fields(game.node(expo)).get('Text [Body Footer]')).get('DEFAULT')) or '', '')
        everything = events[expo]
        if '{EnemyUnit' in everything or 'EnemyUnit' in expo_raw:
            units['EventUnit'] = pool[0] if mandatory and pool else lead
        created = {}
        for e in [expo] + [x for x, s in events.items() if x != expo and expo in s]:
            created.update(made(fields(fields(game.node(e)).get('Script')).get('Actions')))
        units.update(created)
        S = Story(T, S_args, units, mandatory + list(created.values()) + [WORDS['SilverKnight']])
        E = Effects(game)
        E.block(fields(fields(game.node(expo)).get('Script')).get('Actions'))
        f = fields(game.node(expo))
        out.update(title=bare(S.text(f['Text [Title]'])), text=body(expo, S), options=options(expo, S))
        out['enchantments'] = list(dict.fromkeys(out['enchantments'] + keys(E.adds)))
        # an enchantment every way into the battle brings is there whatever you choose (Dark Whispers): the game writes
        # it on each option, the story's list is where it belongs
        fights = [o for o in out['options'] if o['_fights']]
        always = [k for k in (fights[0]['_sure'] if fights else []) if all(k in o['_sure'] for o in fights)]
        out['enchantments'] = list(dict.fromkeys(out['enchantments'] + always))
        for o in out['options']:
            o['adds'] = [k for k in o['adds'] if k not in always]
            if 'check' in o: o['check']['failure_adds'] = [k for k in o['check']['failure_adds'] if k not in always]
            del o['_fights'], o['_sure']
        after = [e for e, s in events.items() if e != expo and expo in s]
        if after:
            af = fields(game.node(after[0]))
            out['aftermath'] = {'title': bare(S.text(af['Text [Title]'])), 'text': body(after[0], S),
                                'options': options(after[0], S)}
            for o in out['aftermath']['options']: del o['_fights'], o['_sure']
        return out

    unit_named = {}
    for r in game.of_class('TBaseUnitR'):
        tag = value(fields(game.node(r)).get('DataStoreTag'))
        if tag and named(r): unit_named.setdefault(tag, [named(r)])
    unit_sets = {}
    for r in game.of_class('TUnitSet'):
        f = fields(game.node(r))
        unit_sets[value(f.get('SetName'))] = [named(i) for n, i in refs(f.get('Units')) if named(i)]
    wonders = []
    for rid in game.of_class('TSectorBaseR'):
        f = fields(game.node(rid))
        req = names(f.get('Requisites'))
        tier = next((t for t in TIERS if t + ' Tier Structure Requisite' in req), None)
        if not tier or LEFT_OUT.search(game.name(rid)): continue
        entries = f['Entries'][1] if f.get('Entries') and f['Entries'][0] == 'l' else []
        by_list = {value(fields(e).get('List')): fields(e) for e in entries}
        cat = value(by_list.get('UNITSPAWNER', {}).get('AdventureCategory'))
        if cat not in cats: continue    # placed by one story realm's script
        # the wonder's Starting Upgrade: what it gives is written there
        up = next((i for n, i in alltexts.all_refs(game.node(rid)) if i in game.raw and game.cls(i) == 'TStructureUpgradeR'), None)
        uf = fields(game.node(up)) if up else {}
        name = alltexts.plain_name(text_of(T, rid, 'Screen Name'))
        key = re.sub(r'[^a-z0-9]+', '_', name.lower().replace("'", '')).strip('_')
        props = names(uf.get('Sector Properties')) + req
        counts = next((v for k, v in COUNTS.items() if any(p.startswith(k + ' - ') for p in props)), None)
        inc = []
        for p, i in refs(uf.get('Sector Properties')):
            if i in game.raw: inc += income(game, i)
        # the Rally of the Lieges: its Military Aid property's units or unit sets, by the units' DataStoreTags
        units = []
        for p, i in refs(by_list.get('STRUCTUREPROPERTYGIVER', {}).get('Side Properties')) + refs(uf.get('Side Properties')):
            if i not in game.raw or 'MilitaryAid' not in game.cls(i): continue
            listed = fields(game.node(i)).get('UnitSetNameList')
            for t in (listed[1] if listed and listed[0] == 'l' else []):
                for u in unit_sets.get(value(t)) or unit_named.get(value(t), []):
                    if u not in units: units.append(u)
        dlc = game.pack[rid].split('/')[0]
        wonders.append({'key': key, 'name': name, 'tier': tier, 'dlc': dlc.upper() if dlc not in ('Title', 'MOD') else None,
                        'description': text_of(T, up, 'Screen Description') if up else text_of(T, rid, 'Screen Description'),
                        'lore': text_of(T, rid, 'Screen Lore Description') or (text_of(T, up, 'Screen Lore Description') if up else ''),
                        'counts': counts, 'income': inc, 'units': units,
                        'stories': [story(i, name) for n, i in refs(fields(game.node(cats[cat])).get('Sets')) if i in game.raw]})
    wonders.sort(key=lambda w: (TIERS.index(w['tier']), w['name']))
    return {'wonders': wonders, 'enchantments': dict(sorted(enchantments.items()))}, warn


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--site', help='the website\'s folder (default: "site" in config.json)')
    ap.add_argument('--out', help='output file (default: <site>/Data/GEN/AncientWonders.json)')
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
    data, warn = build(game, T, site, texts)
    data = {'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
            'mod': os.path.splitext(os.path.basename(acp))[0] if acp else None, **data}
    out = args.out or os.path.join(site, 'Data', 'GEN', 'AncientWonders.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%d wonders, %d stories, %d enchantments -> %s' % (len(data['wonders']), sum(len(w['stories']) for w in data['wonders']), len(data['enchantments']), out))
    for w in dict.fromkeys(warn): print('  warning:', w)


if __name__ == '__main__':
    main()
