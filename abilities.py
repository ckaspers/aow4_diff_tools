"""The unit abilities the game's texts link to that the DUMPALL dump leaves out, for an aow4db-style website: read from
the live game with a mod on top if one is given.

    python abilities.py --site PATH [--acp MOD.acp | --no-mod] [--check]

The dump writes only the abilities units have from the start. One a unit gains in play (Paragon of Strife's Infernal
Blaze) is in no unit's list, so a link to it ("gain the [hyperlink,GLOBAL.PROPERTIES.INFERNAL_BLAZE]Infernal
Blaze[/hyperlink] ability") had only its name and description to show. This writes each such ability as
Data/EN/Abilities.json has it, to Data/GEN/LinkedAbilities.json, and its icon to Icons/UnitIcons/<icon id>.png:

    name, slug (the ability's id), description, damage ("18<damagefire></damagefire> "), accuracy ("100%", "Always
    Hits"), range, actionPoints ("<singleuseaction></singleuseaction>"), requisites, notes (cooldown, once per
    battle, Zone of Control, line of sight), modifiers (none), icon

An ability is a TAbilityProperty; linked means a game text names its DataStoreTag ({GLOBAL.PROPERTIES.<tag>...}).
Texts are filled in as all.json's are (alltexts.Texts). Run it before alltexts.py, which leaves these abilities out of
the concept tooltips: the site opens the ability's own tooltip, as the game does.
--check compares what it would write with the dump's own abilities of the same name, and writes nothing.
"""
import argparse, collections, json, os, re, struct, sys

import aow4diff, alltexts, clbicons, realmtraits, tmlicons, tooltips
from citytree import Game, fields, refs, value, msgid

# AbilityType, left out at its default 0
ACTIONS = {0: 'repeatingaction', 1: 'singleuseaction', 2: 'fullaction', 3: 'fullactioncontinue', 4: 'freeaction',
           5: 'leaveoneaction'}
# a TCombatDamageBlock's fields, in the order the dump writes them, with their text icons
DAMAGE = [('Physical', 'damagephysical'), ('Blight', 'damageblight'), ('Shock', 'damagelightning'),
          ('Fire', 'damagefire'), ('Spirit', 'damagespirit'), ('Frost', 'damagefrost')]
# an accuracy setting's Accuracy left out: the schema has no defaults for fractions, the dump shows 90% (1086 times)
DEFAULT_ACCURACY = 0.9


def dump_links(s):
    """links as the dump writes them ("<hyperlink>Magic</hyperlink>"): the site shows requisites and notes as they are"""
    return re.sub(r'\[hyperlink,[^\]]*\](.*?)\[/hyperlink\]', r'<hyperlink>\1</hyperlink>', s)


def linked_tags(texts):
    return {m.group(1) for s in texts.values() for m in re.finditer(r'\{GLOBAL\.PROPERTIES\.([A-Za-z0-9_]+)', s)}


class Abilities:
    def __init__(self, game, T):
        self.game, self.T = game, T

    def text(self, mid, vals=None):
        """a text by its id, filled in and written as all.json's are"""
        if mid not in self.T.T: return ''
        s = self.T.fill(self.T.T[mid], vals or {}, mid)
        return alltexts.site_markup(s).replace('\0', '<eventColor>{').replace('\1', '}</eventColor>')

    def link(self, rid, field):
        t = self.T.link_text(rid, field)
        return alltexts.site_markup(t or '').replace('\0', '<eventColor>{').replace('\1', '}</eventColor>')

    def plugin(self, f):
        """the ability's default plugin, which can hold its damage and more requisites (Exsanguinate's Single Shot)"""
        p = refs(f.get('DefaultPluginLink'))
        return fields(fields(self.game.node(p[0][1])).get('Plugin')) if p and p[0][1] in self.game.raw else {}

    def damage(self, f):
        """the primary effect's damage, else that of the default plugin's first effect that deals some"""
        for n, rid in refs(f.get('PrimaryEffectLink')) + refs(self.plugin(f).get('Effects')):
            if rid not in self.game.raw: continue
            block = next((v for l, o, v in self.game.node(rid)[2] if o == 'TCombatDamageBlock' or
                          (v and v[0] == 'o' and v[1] == 'TCombatDamageBlock')), None)
            got = fields(block)
            dealt = ''.join('%s<%s></%s> ' % (value(got[k]), tag, tag) for k, tag in DAMAGE
                            if value(got.get(k)) not in (None, '0'))
            if dealt: return dealt
        return ''

    def entry(self, rid):
        g, f = self.game, fields(self.game.node(rid))
        acc = refs(f.get('AccuracySettingsLink'))
        af = fields(g.node(acc[0][1])) if acc and acc[0][1] in g.raw else {}
        # no accuracy setting (Whirlwind): it hits where the unit stands
        cant_miss = not af or value(af.get('Miss Type')) == "Can't Miss"
        if cant_miss:
            accuracy = self.text('INTERFACE@TEXT@ALWAYS_HITS')
        else:
            accuracy = '%d%%' % round(float(value(af.get('Accuracy')) or DEFAULT_ACCURACY) * 100)
        rng = fields(af.get('Range Data'))
        low, high = int(value(rng.get('Min Range')) or 1), int(value(rng.get('Max Range')) or (7 if af else 0))
        notes = []
        # line of sight changes only the accuracy of an ability that can miss: a Direct Fire one's (the type left out
        # at its default), not an Indirect Fire one's or a ranged one's that ignores it (Bombard)
        los = value(af.get('Line Of Sight Type')) or 'Direct Fire'
        if not cant_miss and los == 'Direct Fire': notes.append(self.text('TACTICAL@ABILITY@AFFECTED_LINE_OF_SIGHT'))
        if not cant_miss and (los == 'Indirect Fire' or los == 'Ignore' and high > 1):
            notes.append(self.text('TACTICAL@ABILITY@NO_LINE_OF_SIGHT_PENALTIES'))
        if value(f.get('BlockedWhenEngaged')) == 'true': notes.append(self.text('TACTICAL@ABILITY@BLOCKED_WHEN_ENGAGED'))
        once = value(f.get('OncePerBattleType'))
        cooldown = value(f.get('Cooldown')) or ('0' if value(f.get('AllowZeroCooldowns')) == 'true' else None)
        if once: notes.append(self.text('INTERFACE@ABILITY@ONLY_USE_ONCE_PER_BATTLE'))
        elif cooldown: notes.append(self.text('INTERFACE@GENERIC@COOLDOWN', {'{INT_turns}': cooldown}))
        start = value(f.get('StartingCooldown'))
        if start:    # its turns only when they differ from the cooldown's
            notes.append(self.text('INTERFACE@ABILITY@STARTS_ON_COOLDOWN') + ('' if start == cooldown else ': %s<turn></turn>' % start))
        requisites = list(dict.fromkeys(self.link(i, 'Screen Name Link') for n, i in
                                        refs(f.get('Requisites')) + refs(self.plugin(f).get('Requisites')) if i in g.raw))
        icon = refs(f.get('Large Icon Link'))
        return {'name': alltexts.plain_name(self.link(rid, 'Screen Name')), 'slug': rid,
                'description': self.link(rid, 'Screen Description'), 'damage': self.damage(f),
                'accuracy': accuracy, 'range': '%d-%d' % (low, high) if 1 < low < high else str(high),
                'actionPoints': '<%s></%s>' % ((ACTIONS[int(value(f.get('AbilityType')) or 0)],) * 2),
                'requisites': [{'requisite': dump_links(r)} for r in requisites if r],
                'notes': [{'note': dump_links(n)} for n in notes],
                'modifiers': [], 'icon': icon[0][1] if icon else None}


def icon_images(cfg):
    """the gameplay icon libraries: {library: (Library, {image: texture}, {atlas: {id: image}})}"""
    out = {}
    for path in clbicons.libraries(cfg['game']):
        lib = tmlicons.library_name(path)
        if lib and lib.startswith('GAMEPLAY_ICONS'):
            library = clbicons.Library(path)
            out[lib] = (library, dict(library.images()), realmtraits.atlas_names(path))
    return out


def icon_image(raw, libraries):
    """an icon resource's image, by its atlas and id (the id counts one more than the atlas's record)"""
    m = tmlicons.TXA.search(raw)
    if not m or m.group(1).decode() not in libraries: return None
    library, images, ids = libraries[m.group(1).decode()]
    at = m.end()
    length = struct.unpack_from('<I', raw, at)[0]
    ident = struct.unpack_from('<I', raw, at + 4 + length)[0] if at + 8 + length <= len(raw) else 0
    name = ids.get(int(m.group(2)), {}).get(ident - 1)
    return (library, images[name]) if name in images else None


def plain(s): return re.sub(r'\s+', ' ', re.sub(r'\[/?hyperlink[^\]]*\]|<[^>]+>', '', s or '')).strip()


def check(abilities, dump):
    """the entries of the abilities the dump has by a name only one ability has, against the dump's"""
    by_name = collections.defaultdict(list)
    for rid in abilities.game.of_class('TAbilityProperty'):
        by_name[alltexts.plain_name(abilities.link(rid, 'Screen Name'))].append(rid)
    dumped = collections.defaultdict(list)
    for a in dump: dumped[a.get('name')].append(a)
    same, differ = collections.Counter(), collections.defaultdict(list)
    for name, rids in by_name.items():
        if len(rids) != 1 or len(dumped.get(name, [])) != 1 or 'accuracy' not in dumped[name][0]: continue
        mine, theirs = abilities.entry(rids[0]), dumped[name][0]
        for k in ('damage', 'accuracy', 'range', 'actionPoints', 'icon'):
            if (mine[k] or '').strip() == (theirs.get(k) or '').strip(): same[k] += 1
            else: differ[k].append((name, mine[k], theirs.get(k)))
        for k in ('requisites', 'notes'):
            key = k[:-1]
            a, b = sorted(plain(x[key]) for x in mine[k]), sorted(plain(x[key]) for x in theirs.get(k) or [])
            if a == b: same[k] += 1
            else: differ[k].append((name, a, b))
    for k in ('damage', 'accuracy', 'range', 'actionPoints', 'icon', 'requisites', 'notes'):
        print('%-12s %4d same, %3d differ' % (k, same[k], len(differ[k])), *differ[k][:4], sep='\n    ' if differ[k] else '')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--site', help='the website\'s folder (default: "site" in config.json)')
    ap.add_argument('--acp', help='the mod (default: the first .acp in the Mods folder)')
    ap.add_argument('--no-mod', action='store_true', help='read the game alone')
    ap.add_argument('--check', action='store_true', help='compare with the dump\'s own abilities, write nothing')
    args = ap.parse_args()
    cfg = aow4diff.load_config()
    site = args.site or cfg.get('site')
    if not site: sys.exit('which website? pass --site or set "site" in config.json')
    acp = None if args.no_mod else args.acp or (aow4diff.mod_acps(cfg) or [None])[0]
    game = Game(cfg, acp)
    T = alltexts.Texts(game, tooltips.load_texts(cfg['game'], os.path.dirname(acp) if acp else ''))
    abilities = Abilities(game, T)
    dump = json.load(open(os.path.join(site, 'Data', 'EN', 'Abilities.json'), encoding='utf-8-sig'))
    if args.check: return check(abilities, dump)

    dumped = {a['name'].split('^')[0].strip().lower() for a in dump if a.get('name')}
    tags = linked_tags(T.T)
    entries = {}
    for rid in game.of_class('TAbilityProperty'):
        if value(fields(game.node(rid)).get('DataStoreTag')) not in tags: continue
        e = abilities.entry(rid)
        if e['name'] and e['name'].lower() not in dumped: entries.setdefault(e['name'], e)
    entries = sorted(entries.values(), key=lambda e: e['name'])
    libraries, icons = icon_images(cfg), 0
    for e in entries:
        out = os.path.join(site, 'Icons', 'UnitIcons', '%s.png' % e['icon'])
        found = icon_image(game.raw[e['icon']], libraries) if e['icon'] in game.raw else None
        if not os.path.exists(out) and found and found[1]['format'] == clbicons.RGBA:
            clbicons.write_png(out, found[1]['width'], found[1]['height'], found[0].pixels(found[1]))
            icons += 1
        if not os.path.exists(out): print('  no icon for', e['name'])
    path = os.path.join(site, 'Data', 'GEN', 'LinkedAbilities.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(entries, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%d abilities the texts link to that the dump leaves out (%d icons exported) -> %s' % (len(entries), icons, path))


if __name__ == '__main__':
    main()
