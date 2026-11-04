"""The world map's infestations, landmarks and happenings for an aow4db-style website, read from the live game, with a
mod on top if one is given.

    python worldmap.py --site PATH [--acp MOD.acp | --no-mod]

Writes Data/GEN/Infestations.json, Landmarks.json and Happenings.json, and the icons missing from Icons/WorldStructures/,
Icons/CombatEnchantments/ and Icons/CosmicHappenings/. The site folder is --site, else config.json's "site"; the mod is
--acp, else the first .acp in the Mods folder.

Pictures aren't in the game's readable icon libraries: the site's own are found through upstream's lists
(Data/GEN/WorldStructures.json, CosmicHappenings.json), whose text ids are the game's.

    infestations  key, name, tier, dlc, water, description, lore, picture, reward {resource, tier}, size {start, max,
                  growth}, pools [{label, units, others}]: a pool per variant (a Mistwalker Camp per season)
    landmarks     key, name, dlc, lore, adjacent, city (the texts for adjacent provinces and for a city with enough of
                  them), enchantments {name, description, icon}, units (ids in Units.json), picture, icon
    happenings    key, name, group (Minor, Major, Intrigue, Special, Oceanic), category (an Oceanic one's Minor or Major), dlc,
                  sub, description, duration (turns, None when play decides), warning (turns of foreshadowing), image, icon,
                  story {text, options}; an Oceanic one's events [{label, text, options [{text, costs, rewards, units}]}]
                  and units (all its options let cities draft)
"""
import argparse, datetime, json, os, re, struct, sys

import aow4diff, alltexts, bc7, clbicons, tmlicons, tooltips
from ancientwonders import Effects, GameIcons, Story, WORDS, bare, text_of as aw_text, unit_pools
from citytree import Game, fields, refs, names, value, tags, msgid, hexid, units_needing

# a structure placed only by a story realm, a tome or a happening repeats the normal one under its name
LEFT_OUT = re.compile(r'^\s*TSR |Lost Soul|^Intrigue Happening|Vision of Promise|Beacon of Light')
TIERS = ('Bronze', 'Silver', 'Gold')
VARIANT = re.compile(r' - (Spring|Summer|Autumn|Winter) - ')
# TSpawner fields the decoder leaves out at their defaults (schema_db.json)
START_DEFAULT, MAX_DEFAULT, GROWTH_DEFAULT = 2, 6, 8
REWARD = re.compile(r'Spawner - (.+?) \+ ')
UNCHECKED = re.compile(r'(?:<br>)*If left unchecked, they will grow in strength, expand their territory,? and send out invasion armies\.\s*', re.I)

HAPPENING_GROUP = [(re.compile(r'^Minor Cosmic Happening - (.+?) - Type'), 'Minor'),
                   (re.compile(r'^Major Cosmic Happening - (.+?) - Type'), 'Major'),
                   (re.compile(r'^Intrigue Happening - (.+?) - Type'), 'Intrigue'),
                   (re.compile(r'^(Minor|Major) Oceanic Happening - Type'), 'Oceanic')]
# happenings outside those families that any game can have: a tome's, a Vision of Ruin's, a realm trait's
SPECIAL = re.compile(r'Vision of Ruin|^Blood Moon|^Warped Seasons')
# a story realm's checkpoints wear the Constellation type
HAPPENING_OUT = re.compile(r'^SotAM|^TSR |^AP\d|^ER\d|Finisher')
# the site's picture for each of the game's five cosmic icons
CATEGORY_ICON = {'Cosmic Constellation - Icon': 'category_icon_Constellation.png', 'Cosmic Astroid - Icon': 'category_icon_Asteroid.png',
                 'Cosmic Entity - Icon': 'category_icon_Entity.png', 'Cosmic Planet Phase - Icon': 'category_icon_PlanetPhase.png',
                 'Cosmic Weather - Icon': 'category_icon_Weather.png', 'Cosmic Major - Icon': 'category_icon_Major.png',
                 'Fey Invasion - Crisis Icon': 'category_icon_TollOfSeasons.png'}
# play's values in general words
PLAY = {'IntriguePreviousRuler': 'the former ruler', 'IntrigueVictimPlayer': 'the targeted ruler', 'IntrigueVictimPlayerRace': 'people',
        'IntrigueVictimPlayerLeader': 'the new ruler',
        'IntrigueVictimPlayer.Title': 'ruler', 'IntrigueNewRuler': 'the new ruler', 'REALM_NAME': 'this realm', 'IntrigueUsurper': 'the usurper'}
# a variable's name reads as the unit it holds ("Balor Unit")
UNIT_WORD = re.compile(r'(<eventColor>[^<]*?) Unit(</eventColor>)')


def dlc_of(game, rid):
    dlc = game.pack[rid].split('/')[0]
    return dlc.upper() if dlc not in ('Title', 'MOD') else None


def key_of(name):
    return re.sub(r'[^a-z0-9]+', '_', re.sub(r'<[^>]+>', '', name).lower().replace("'", '')).strip('_')


def text_of(T, rid, field):
    """a resource's text, played values worded; a concept's plural the game wrote without its colon ("<<m{...}>>")"""
    t = aw_text(T, rid, field)
    t = re.sub(r'\bm(\[hyperlink,[^\]]+\])(.*?)(\[/hyperlink\])', lambda m: m.group(1) + alltexts.plural(m.group(2)) + m.group(3), t)
    for k, v in PLAY.items(): t = t.replace('<eventColor>%s</eventColor>' % k, '<eventColor>%s</eventColor>' % v)
    # a concept whose name has a hyphen isn't taken for one
    t = t.replace('<eventColor>GLOBAL.PactOfVassalageLvl-1Concept.Hyperlink</eventColor>', '[hyperlink,GLOBAL.PactOfVassalageLvl-1Concept]Tributary[/hyperlink]')
    # a played value at the start of the text
    return re.sub(r'^(<eventColor>)([a-z])', lambda m: m.group(1) + m.group(2).upper(), t)


def entries(game, rid):
    f = fields(game.node(rid))
    return {value(fields(e).get('List')): fields(e) for e in (f['Entries'][1] if f.get('Entries') and f['Entries'][0] == 'l' else [])}


def pictures(site):
    """upstream's picture for a text id"""
    out = {}
    for e in json.load(open(os.path.join(site, 'Data', 'GEN', 'WorldStructures.json'), encoding='utf-8-sig')):
        if e.get('extraLookup'): out[e['extraLookup']] = e['id']
    return out


def infestations(game, T, texts, site, warn):
    pools, others = unit_pools(game, texts)
    picture_of = pictures(site)
    got = {}
    for rid in game.of_class('TStructureR'):
        f = fields(game.node(rid))
        name_id = msgid(f.get('Screen Name'))
        if 'Infestation Spawner Requisite' not in names(f.get('Requisites')) or LEFT_OUT.search(game.name(rid)): continue
        ent = entries(game, rid)
        tier = re.search(r'@(BRONZE|SILVER|GOLD)$', msgid(f.get('Type Descriptor')) or '')
        if not tier:
            warn.append('%s: no tier' % game.name(rid))
            continue
        name = alltexts.plain_name(text_of(T, rid, 'Screen Name'))
        spawn, inf = ent.get('UNITSPAWNER', {}), ent.get('INFESTATION', {})
        cat = value(spawn.get('UnitPoolCategory')) or ''
        variant = VARIANT.search(game.name(rid))
        pool = {'label': variant.group(1) if variant else '', 'units': pools.get(cat, []), 'others': others.get(cat, [])}
        if not pool['units'] and not pool['others']: warn.append('%s: no units in %s' % (name, cat))
        if name in got:
            got[name]['pools'].append(pool)
            continue
        reward = REWARD.search((names(ent.get('REWARD', {}).get('Rewards Link')) or [''])[0])
        rtier = re.search(r'\d', value(ent.get('REWARD', {}).get('Reward Tier')) or '')
        picture = picture_of.get(re.sub(r'@NAME$', '', name_id or ''))
        if not picture: warn.append('%s: no picture' % name)
        number = lambda field, default: int(value(inf.get(field)) or default)
        got[name] = {'key': key_of(name), 'name': name, 'tier': TIERS[('BRONZE', 'SILVER', 'GOLD').index(tier.group(1))],
                     'dlc': dlc_of(game, rid), 'water': 'Water Infestation Spawner Requisite' in names(f.get('Requisites')),
                     'description': UNCHECKED.sub('', text_of(T, rid, 'Screen Description')),
                     'lore': text_of(T, rid, 'Screen Lore Description'), 'picture': picture,
                     'reward': {'resource': reward.group(1) if reward else None, 'tier': int(rtier.group()) if rtier else None},
                     'size': {'start': number('Starting Sectors', START_DEFAULT), 'max': number('Max Size', MAX_DEFAULT),
                              'growth': number('Growth Speed', GROWTH_DEFAULT)},
                     'pools': [pool]}
    return sorted(got.values(), key=lambda e: (TIERS.index(e['tier']), e['name']))


def landmarks(game, T, icons, site, warn):
    unit_of = {hexid(u['resid']): u for u in json.load(open(os.path.join(site, 'Data', 'EN', 'Units.json'), encoding='utf-8-sig'))}
    needed_by = {}
    for r in game.of_class('TBaseUnitR'):
        if r not in unit_of: continue
        for n, i in refs(fields(game.node(r)).get('Required Structure Properties')): needed_by.setdefault(i, []).append(unit_of[r])
    picture_of = pictures(site)
    ench_dir, icon_dir = os.path.join(site, 'Icons', 'CombatEnchantments'), os.path.join(site, 'Icons', 'WorldStructures')
    got = {}
    for rid in game.of_class('TStructureR'):
        if not game.name(rid).endswith('- Landmark'): continue
        f = fields(game.node(rid))
        name = alltexts.plain_name(text_of(T, rid, 'Screen Name'))
        if name in got: continue    # the Ancient Dragon Hoard's underground copy
        giver = entries(game, rid).get('STRUCTUREPROPERTYGIVER', {})
        props = refs(giver.get('Sector Properties')) + refs(giver.get('Adjacent Sector Properties'))
        enchantments, units = {}, {}
        for n, i in props:
            if i not in game.raw: continue
            pf = fields(game.node(i))
            if game.cls(i).startswith('TCombat') and msgid(pf.get('Screen Name')) and msgid(pf.get('Description')):
                ename = alltexts.plain_name(text_of(T, i, 'Screen Name'))
                enchantments.setdefault(ename, {'name': ename, 'description': text_of(T, i, 'Description'),
                                                'icon': icons.write(refs(pf.get('Icon Link')), ench_dir, key_of(ename))})
            if game.cls(i) == 'TSectorThresholdColonyProperty':
                for p, j in refs(pf.get('Structure Properties')) + refs(pf.get('Colony Properties')):
                    for u in needed_by.get(j, []): units[u['id']] = u
        # "Provinces adjacent to this Landmark gain: ... A City with 3 Provinces ... gains: ..."
        text = text_of(T, rid, 'Screen Description')
        parts = re.split(r'(?<=</bulletlist>)(?:<br>)*(?=A \[hyperlink,GLOBAL\.CityConcept\])', text, maxsplit=1)
        if len(parts) < 2: warn.append('%s: no city part in its text' % name)
        picture = picture_of.get(re.sub(r'@NAME$', '', msgid(f.get('Screen Name')) or ''))
        # the realm-generated Chained Hellion is the story's Perishing Archnemesis to look at
        tag = (value(f.get('DataStoreTag')) or '').lower()
        if not picture and os.path.exists(os.path.join(site, 'Icons', 'StructurePics', tag + '.png')): picture = tag
        icon = icons.write(refs(f.get('Icon')), icon_dir, 'landmark_' + key_of(name))
        if not picture and not icon: warn.append('%s: no picture or icon' % name)
        got[name] = {'key': key_of(name), 'name': name, 'dlc': dlc_of(game, rid), 'lore': text_of(T, rid, 'Screen Lore Description'),
                     'adjacent': parts[0], 'city': parts[1] if len(parts) > 1 else '', 'enchantments': list(enchantments.values()),
                     'units': [u['id'] for u in sorted(units.values(), key=lambda u: int(u['tier']))], 'picture': picture, 'icon': icon}
    return sorted(got.values(), key=lambda e: e['name'])


# a happening's story events by the end of their name ("Minor Happening (Cosmic) - Astral Eclipse - Intro", "Blood Moon -
# Intro Event"); DEPRECATED ones are left out
EVENT_LABEL = {'Prelude': 'Prelude', 'Foreshadow': 'Foreshadowing', 'Foreshadowing': 'Foreshadowing', 'Intro': 'Beginning',
               'Triggering': 'Beginning', 'Warning': 'Warning', 'Outro': 'End', 'Aftermath': 'End',
               '1st Prize': "The winner's prize", 'Participation Prize': "The others' prize"}


def happening_events(game, T, rid, rows, needed_by, warn, follow=False):
    """a happening's story: the events its schedule shows, each once, and with follow (the Oceanic ones) those they or it
    hand on to (the prizes), found in its own pack by their id in the raw data, as a script's links aren't decoded; the
    Cosmic ones share a pack, where a chance match would bring in another's. Each option's costs, rewards and the units the
    properties it adds let cities draft (Mating Season's Feed the Spawning Shoals: Nimu, Seamare...)"""
    own = [e for e in game.of_class('TStoryEventResource') if game.pack[e] == game.pack[rid] and 'DEPRECATED' not in game.name(e)]
    def linked(x):
        raw = game.raw[x]
        return [e for e in own if e != x and struct.pack('<II', int(e[8:], 16), int(e[:8], 16)) in raw]
    evs = list(dict.fromkeys(i for r in rows for n, i in refs(r.get('Story Events')) if i in game.raw and 'DEPRECATED' not in game.name(i)))
    if follow:
        for x in [rid] + list(evs): evs += [e for e in linked(x) if e not in evs]
    out, all_units = [], []
    for ev in evs:
        label = EVENT_LABEL.get(re.sub(r' Event$', '', game.name(ev).split(' - ')[-1].strip()))
        if not label:
            warn.append('%s: an event with no label' % game.name(ev))
            continue
        S = Story(T, 'this realm', {})
        f = fields(game.node(ev))
        text = [t for t in (S.text(f[k]) for k in ('Text [Body Header]', 'Text [Body Lore]', 'Text [Body Footer]') if f.get(k)) if t]
        buttons = fields(f.get('Buttons')).get('List') if f.get('Buttons') else None
        options = []
        for b in (buttons[1] if buttons and buttons[0] == 'l' else []):
            bf = fields(b)
            E = Effects(game)
            E.block(fields(bf.get('Script')).get('Actions'))
            units = list(dict.fromkeys(u['id'] for i in E.adds for u in sorted(needed_by.get(i, []), key=lambda u: int(u['tier']))))
            all_units += units
            options.append({'text': UNIT_WORD.sub(r'\1\2', S.text(bf['Text Button'])), 'costs': E.costs, 'rewards': E.rewards, 'units': units})
        if text or options: out.append({'label': label, 'text': [UNIT_WORD.sub(r'\1\2', t) for t in text], 'options': options})
    return out, list(dict.fromkeys(all_units))


# a happening's picture the game has but no resource links: Warped Seasons' own (its events show none; a story realm's
# Warped Seasons event shows DLC10_Event_WarpedSeasons)
UNLINKED_PICTURES = {'Warped Seasons': ('INTERFACEASSETS_SECRETSOFTHEARCHMAGES', 'Happening_WarpedSeasons_[DIFF_DXT1].png')}
# a scene modifier's texture: [INTERFACEASSETS_SILVERSEAS]TX1\5, then the length and name of the file it was made from
PICTURE = re.compile(rb'\[([A-Z0-9_]+)\]TX1.\d+.\x00\x00\x00([^\x00]+?\.png)', re.S)


class Pictures:
    """the picture an event shows, by its Story tags: a scene modifier set (TGCMModifierSet, Events_Scenes.rpk) whose
    Tag Filter names the tag sets the event's background to a texture of an interface library (Mating Season's events,
    OH_NimuMatingSeason: Happening_Oceanic_MatingSeason_[DIFF_DXT1].png in INTERFACEASSETS_SILVERSEAS). Written as a PNG
    half the texture's size (512px of 1024: the cards show 240px), once: one already on the site is kept"""
    def __init__(self, game):
        self.libs = {tmlicons.library_name(p): p for p in clbicons.libraries(game.cfg['game'])}
        self.of_tag = {}
        for r in game.of_class('TGCMModifierSet'):
            m = PICTURE.search(game.raw[r])
            if not m: continue
            for t in re.findall(r"Contains '([^']+)'", str(game.node(r))):
                self.of_tag.setdefault(t, (m.group(1).decode(), m.group(2).decode()))

    def write(self, story_tags, folder, key, unlinked=None):
        """the picture of the first tag that has one, else unlinked: (library, file) the game has but links nowhere"""
        out = os.path.join(folder, key + '.png')
        if os.path.exists(out): return key + '.png'
        lib_name, filename = next((self.of_tag[t] for t in story_tags if t in self.of_tag), unlinked or (None, None))
        if lib_name not in self.libs: return None
        lib = clbicons.Library(self.libs[lib_name])
        tex = lib.texture_from(filename)
        if not tex: return None
        data, w, h = lib.pixels(tex), tex['width'], tex['height']
        if tex['format'] == bc7.BC1: rgba = bc7.decode_bc1(data, w, h)
        elif tex['format'] == clbicons.RGBA: rgba = data[:w * h * 4]
        else: return None
        clbicons.write_png(out, w // 2, h // 2, half(w, h, rgba))
        return key + '.png'


def half(w, h, rgba):
    """RGBA bytes at half the width and height, each pixel the mean of four"""
    out = bytearray((w // 2) * (h // 2) * 4)
    row = w * 4
    for y in range(h // 2):
        a, b, o = 2 * y * row, (2 * y + 1) * row, y * (w // 2) * 4
        for x in range(w // 2):
            i, j = a + 8 * x, b + 8 * x
            for c in range(4):
                out[o + c] = (rgba[i + c] + rgba[i + 4 + c] + rgba[j + c] + rgba[j + 4 + c] + 2) // 4
            o += 4
    return bytes(out)


def happenings(game, T, icons, pictures, site, warn):
    old = json.load(open(os.path.join(site, 'Data', 'GEN', 'CosmicHappenings.json'), encoding='utf-8-sig'))
    image_of = {e['extraLookup']: e['image'] for e in old if e.get('extraLookup')}
    # the Major ones have no text id there: by name, without its affinity
    image_named = {key_of(re.sub(r'\(.*\)', '', e['name'])): e['image'] for e in old}
    folder = os.path.join(site, 'Icons', 'CosmicHappenings')
    needed_by = units_needing(game, site)
    got = {}
    for rid in game.of_class('TScriptedWorldCrisisResource'):
        label = game.name(rid)
        kind = (names(fields(game.node(rid)).get('EventTypeLink')) or [''])[0]
        group, category = None, ''
        if not HAPPENING_OUT.search(label):
            if SPECIAL.search(label): group, category = 'Special', ''
            else:
                for pattern, g in HAPPENING_GROUP:
                    m = pattern.search(kind)
                    if m: group, category = g, m.group(1)
        if not group: continue
        f = fields(game.node(rid))
        # an Oceanic one's kind is in its tags: The Nevermoor's type, icon and crisis type are the Minor ones'
        if group == 'Oceanic': category = 'Major' if 'HappeningScopeType.Happening.MajorHappening' in tags(f.get('Tags')) else 'Minor'
        name = text_of(T, rid, 'Name')
        if name in got: continue    # Toll of Seasons: the manager and the realm trait's
        name_id = msgid(f.get('Name')) or ''
        schedule = fields(f.get('Scheduled Events')).get('Entries')
        rows = [fields(e) for e in (schedule[1] if schedule and schedule[0] == 'l' else [])]
        warning = next((int(value(r.get('Turn'))) for r in rows if value(r.get('Name')) == 'Triggering' and value(r.get('Turn'))), None)
        link = refs(f.get('Icon - Default'))
        icon = CATEGORY_ICON.get(link[0][0]) if link else None
        # the Oceanic ones share their kind's icon
        if group == 'Oceanic' and link: icon = icons.write(link, folder, 'category_icon_' + key_of(link[0][0]))
        icon = icon or icons.write(link, folder, 'icon_' + key_of(name))
        if not icon: warn.append('%s: no icon' % name)
        duration = value(f.get('Duration'))
        e = {'key': key_of(name), 'name': name, 'group': group, 'category': category if group != 'Intrigue' else '',
             'dlc': dlc_of(game, rid), 'sub': text_of(T, rid, 'Sub Header'), 'description': text_of(T, rid, 'Description'),
             'duration': int(duration) if duration else None, 'warning': warning,
             'image': image_of.get(re.sub(r'@NAME$', '', name_id)) or image_named.get(key_of(name)), 'icon': icon}
        # an intrigue's effects are in its opening event: its text and choices
        if group == 'Intrigue':
            intro = next((i for r in rows for n, i in refs(r.get('Story Events'))), None)
            if intro:
                S = Story(T, 'this realm', {})
                ef = fields(game.node(intro))
                parts = [S.text(ef[k]) for k in ('Text [Body Header]', 'Text [Body Lore]', 'Text [Body Footer]') if ef.get(k)]
                buttons = fields(ef.get('Buttons')).get('List') if ef.get('Buttons') else None
                worded = lambda t: UNIT_WORD.sub(r'\1\2', t)
                e['story'] = {'text': [worded(p) for p in parts if p],
                              'options': [worded(S.text(fields(b)['Text Button'])) for b in (buttons[1] if buttons and buttons[0] == 'l' else [])]}
        # its events, each with its choices; an intrigue's are its opening event (above) and one random war event over and over
        if group != 'Intrigue':
            e['events'], e['units'] = happening_events(game, T, rid, rows, needed_by, warn, follow=group == 'Oceanic')
        # its picture: the one its events show (or one the game has unlinked), else upstream's (Toll of Seasons); the
        # intrigues and the Visions of Ruin's ages have none in the game, their events show where they happen
        story_tags = [t for r in rows for n, i in refs(r.get('Story Events')) if i in game.raw for t in tags(fields(game.node(i)).get('Story tags'))]
        upstream = e['image'] if e['image'] and os.path.exists(os.path.join(folder, e['image'])) else None
        e['image'] = pictures.write(story_tags + tags(f.get('Tags')), folder, 'image_' + e['key'], UNLINKED_PICTURES.get(name)) or upstream
        if not e['image'] and group == 'Oceanic': warn.append('%s: no picture' % name)
        got[name] = e
    order = {g: i for i, g in enumerate(('Minor', 'Major', 'Intrigue', 'Special', 'Oceanic'))}
    return sorted(got.values(), key=lambda e: (order[e['group']], e['category'], key_of(e['name'])))


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
    WORDS.update(PLAY)
    icons = GameIcons(game)
    warn = []
    head = {'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
            'mod': os.path.splitext(os.path.basename(acp))[0] if acp else None}
    for name, key, rows in (('Infestations', 'infestations', infestations(game, T, texts, site, warn)),
                            ('Landmarks', 'landmarks', landmarks(game, T, icons, site, warn)),
                            ('Happenings', 'happenings', happenings(game, T, icons, Pictures(game), site, warn))):
        out = os.path.join(site, 'Data', 'GEN', name + '.json')
        with open(out, 'w', encoding='utf-8', newline='\n') as f:
            json.dump({**head, key: rows}, f, indent=1, ensure_ascii=False)
            f.write('\n')
        print('%d %s -> %s' % (len(rows), key, out))
    for w in dict.fromkeys(warn): print('  warning:', w)


if __name__ == '__main__':
    main()
