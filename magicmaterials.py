"""Magic materials for an aow4db-style website: each set's bonus and what it needs, and each material's
texts, local income and combat enchantment, read from the live game, with a mod on top if one is given.

    python magicmaterials.py --site PATH [--out PATH] [--acp MOD.acp | --no-mod]

Writes Data/GEN/MagicMaterials.json. The site folder is --site, else config.json's "site"; the mod is --acp,
else the first .acp in the Mods folder.

Why not the site's all.json: it comes from upstream, with the numbers of their vanilla game at the time
(Focus Crystals +10% where the game now says +20%) and none of a mod's changes. Here every text is the
live game's or the mod's own, filled in with the values of the resource that shows it.

    sets        key (DataStoreTag), category, name, description: texts; required: how many materials;
                unique: whether they must differ; materials: their keys
    materials   key -> name, description (global effect), lore, income [[resource, value]] (local),
                combat {name, description}, dlc (as the site's dlcMap names it)

Texts are in the site's format: <bulletlist><bullet>, <icon></icon>, [hyperlink,GLOBAL.X]{GLOBAL.X}[/hyperlink]
(the braces ask the site for the concept's own name, as the game does).
"""
import argparse, datetime, json, os, re, sys

import aow4diff, tooltips
from citytree import Game, fields, refs, names, value, tags, msgid

# TTokenSetBonus fields the decoder leaves out at their defaults (schema_db.json: FRequiredAmount 3,
# FRequiresUniqueTokens on)
REQUIRED_DEFAULT = 3
UNIQUE_DEFAULT = True


def replacements(link):
    """{key: value} of a text link's replacements that carry a value"""
    out = {}
    r = fields(link).get('replacements')
    for it in (r[1] if r and r[0] == 'l' else []):
        f = fields(it)
        k, v = value(f.get('key')), value(f.get('value'))
        if k and v is not None: out[k] = v
    return out


def concept_renderings(site):
    """GLOBAL.X -> how the dump writes that concept's link, icon included: the dump's texts are the only
    place the site has them all ("<knowledge></knowledge> [hyperlink,GLOBAL.KnowledgeConcept]Knowledge[/hyperlink]")"""
    seen = {}
    for e in json.load(open(os.path.join(site, 'Data', 'EN', 'all.json'), encoding='utf-8-sig')):
        for v in e.values():
            if not isinstance(v, str) or '[hyperlink,' not in v: continue
            for m in re.finditer(r'((?:<(\w+)></\2> ?)?)\[hyperlink,(GLOBAL\.[\w.]+)\](.*?)\[/hyperlink\]', v):
                if m.group(2) in ('bullet', 'br', 'eventColor'): continue    # not an icon
                if 'eventColor' in m.group(4): continue                      # another text's colouring
                c = seen.setdefault(m.group(3), {})
                c[m.group(0)] = c.get(m.group(0), 0) + 1
    # the most common way, one that names the concept over "{<hp></hp>}" (which leaves the name to the site)
    return {k: max(c, key=lambda r: ('{' not in r, c[r])) for k, c in seen.items()}


class Texts:
    def __init__(self, game, texts, concepts, warn):
        self.game, self.T, self.concepts, self.warn = game, texts, concepts, warn
        self.tagged = {}

    def stored(self, key):
        """{TAG>ADD>MaxRange}: the field of the resource with that DataStoreTag; an ADD takes it from the
        block that adds (Range To Add, not Range Floors)"""
        tag, op, field = key.strip('{}').split('>')
        if tag not in self.tagged:
            # the tag is stored as text: only resources holding it need decoding
            self.tagged[tag] = next((r for r, raw in self.game.raw.items() if tag.encode() in raw
                                     and value(fields(self.game.node(r)).get('DataStoreTag')) == tag), None)
        rid = self.tagged[tag]
        if not rid: self.warn.append('no resource for %s' % key); return None
        found = []
        def walk(n, path):
            if not n: return
            if n[0] == 'o':
                for (l, o, v) in n[2]:
                    if l.replace(' ', '').lower() == field.lower() and value(v) is not None: found.append((path + '/' + l, value(v)))
                    walk(v, path + '/' + l)
            elif n[0] == 'l':
                for x in n[1]: walk(x, path)
        walk(self.game.node(rid), '')
        if op == 'ADD': found = [f for f in found if 'add' in f[0].lower()] or found
        if not found: self.warn.append('no field for %s' % key); return None
        return found[0][1]

    def __call__(self, link):
        """a text link (msgid + replacements) as the site shows texts"""
        mid = msgid(link)
        if not mid: return ''
        s = self.T.get(mid)
        if s is None: self.warn.append('no text for ' + mid); return ''
        vals = replacements(link)
        def fill(m):
            key = m.group(0)
            if key in vals: return vals[key]
            g = re.match(r'^\{(GLOBAL\.[\w.]+?)(?:\.Hyperlink)?\}$', key)
            if g:
                if g.group(1) in self.concepts: return self.concepts[g.group(1)]
                self.warn.append('%s: the dump never links %s' % (mid, g.group(1)))
                return '[hyperlink,%s]{%s}[/hyperlink]' % (g.group(1), g.group(1))
            if key.count('>') == 2:
                v = self.stored(key)
                if v is not None: return v
            self.warn.append('%s: %s left unfilled' % (mid, key))
            return key
        # a value can hold a key of its own ("15 {GLOBAL.SpiritDamageConcept.Hyperlink}"): fill until done
        for _ in range(3):
            filled = re.sub(r'\{[^{}<\[\]]+\}', fill, s)    # not "{<hp></hp>}": the site names that concept
            if filled == s: break
            s = filled
        return site_markup(s)

    def name(self, link):
        """a name without the game's grammar marks ("Arcanium Ore^ns")"""
        return self(link).split('^')[0]


def site_markup(s):
    """the game's markup as the dump writes it for the site"""
    s = re.sub(r'<<m:(.*?)>>', r'\1', s)                      # plural forms: the text as is
    s = s.replace('[br/]', '<br>')
    # [bullet/] starts an item: close the one before it, and the last one with its list
    s = s.replace('[bulletlist]', '<bulletlist>').replace('[/bulletlist]', '</bullet></bulletlist>')
    s = s.replace('[bullet/]', '</bullet><bullet>').replace('<bulletlist></bullet>', '<bulletlist>')
    s = re.sub(r'\[(\w+)/\]', r'<\1></\1>', s)                # icons: [xp/] -> <xp></xp>
    return s.replace('\n', '<br>')


def income(game, rid):
    """[[resource, value]] of an income property, values that are not 0"""
    out = []
    def walk(n):
        if not n: return
        if n[0] == 'o':
            f = {l: v for (l, o, v) in n[2]}
            if 'Resource' in f and 'Value' in f and value(f['Value']) not in (None, '0', '0.0'):
                r = refs(f['Resource'])
                res = game.name(r[0][1]) if r and r[0][1] in game.raw else (r[0][0] if r else value(f['Resource']))
                out.append([res, int(float(value(f['Value'])))])
            for (l, o, v) in n[2]: walk(v)
        elif n[0] == 'l':
            for x in n[1]: walk(x)
    walk(game.node(rid))
    return out


def build(game, texts, site):
    warn = []
    text = Texts(game, texts, concept_renderings(site), warn)

    materials, tags_of = {}, {}
    for rid in game.of_class('TStructureR'):
        f = fields(game.node(rid))
        if 'Magic Material' not in names(f.get('LocationTypes')): continue
        key = (value(f.get('DataStoreTag')) or game.name(rid)).lower()
        giver = {}
        for e in (f['Entries'][1] if f.get('Entries') and f['Entries'][0] == 'l' else []):
            if value(fields(e).get('List')) == 'STRUCTUREPROPERTYGIVER': giver = fields(e)
        m = {'name': text.name(f.get('Screen Name')), 'description': text(f.get('Screen Description')),
             'lore': text(f.get('Screen Lore Description')), 'income': []}
        # by id: a reference keeps its old name when the resource is renamed (Silvertongue Fruit's income
        # is "Euphoric Trollstools Node - Sector Income" now)
        for p, i in refs(giver.get('Sector Properties')):
            if i in game.raw and 'Income' in game.cls(i): m['income'] += income(game, i)
        # the combat enchantment: the first structure property with a name (the others are hidden helpers)
        for p, i in refs(giver.get('Structure Properties')):
            cf = fields(game.node(i)) if i in game.raw else {}
            if msgid(cf.get('Screen Name')):
                m['combat'] = {'name': text.name(cf.get('Screen Name')), 'description': text(cf.get('Description'))}
                break
        else:
            warn.append('%s: no combat enchantment' % key)
        dlc = game.pack[rid].split('/')[0]
        if dlc not in ('Title', 'MOD'): m['dlc'] = dlc.upper()
        materials[key] = m
        tags_of[key] = set(tags(f.get('Tags')))

    sets = []
    for rid in game.of_class('TTokenSetBonus'):
        f = fields(game.node(rid))
        need = set(tags(f.get('Required Token Tags')))
        members = sorted(k for k, t in tags_of.items() if t & need)
        if not members: continue
        req, uniq = value(f.get('Required Amount')), value(f.get('Requires Unique Tokens'))
        key = value(f.get('DataStoreTag')) or re.sub(r' - Token Set Bonus$', '', game.name(rid))
        sets.append({'key': re.sub(r'\W+', '_', key).lower(),
                     'category': text.name(f.get('Category Name')), 'name': text.name(f.get('Screen Name')),
                     'description': text(f.get('Screen Description')),
                     'required': int(req) if req is not None else REQUIRED_DEFAULT,
                     'unique': (uniq == 'true') if uniq is not None else UNIQUE_DEFAULT,
                     'materials': members})
    in_a_set = {k for s in sets for k in s['materials']}
    for k in materials:
        if k not in in_a_set: warn.append('%s is in no set' % k)
    return {'sets': sets, 'materials': materials}, warn


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--site', help='the website\'s folder (default: "site" in config.json)')
    ap.add_argument('--out', help='output file (default: <site>/Data/GEN/MagicMaterials.json)')
    ap.add_argument('--acp', help='the mod (default: the first .acp in the Mods folder)')
    ap.add_argument('--no-mod', action='store_true', help='read the game alone')
    args = ap.parse_args()
    cfg = aow4diff.load_config()
    site = args.site or cfg.get('site')
    if not site: sys.exit('which website? pass --site or set "site" in config.json')
    acp = None if args.no_mod else args.acp or (aow4diff.mod_acps(cfg) or [None])[0]
    game = Game(cfg, acp)
    texts = tooltips.load_texts(cfg['game'], os.path.dirname(acp) if acp else '')
    data, warn = build(game, texts, site)
    data = {'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'build': aow4diff.build_label(cfg['game']),
            'mod': os.path.splitext(os.path.basename(acp))[0] if acp else None, **data}
    out = args.out or os.path.join(site, 'Data', 'GEN', 'MagicMaterials.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%d sets, %d materials -> %s' % (len(data['sets']), len(data['materials']), out))
    for s in data['sets']: print('  %-22s needs %d%s: %s' % (s['key'], s['required'], ' different' if s['unique'] else '', ', '.join(s['materials'])))
    for w in dict.fromkeys(warn): print('  warning:', w)


if __name__ == '__main__':
    main()
