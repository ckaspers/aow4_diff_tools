"""All of the game's texts for an aow4db-style website (Data/EN/all.json, and the concept tooltips in
Data/EN/concept_tooltips.json), read from the live game with a mod on top if one is given: every text the game's or the mod's own, filled in with the values of the resources that show it.

    python alltexts.py --site PATH [--acp MOD.acp | --no-mod] [--dry-run]

all.json holds a text "<id>@<FIELD>" as {"id": <id>, "<field>": text}, one entry per id, sorted by id; an id that
starts with DLC@ also has "DLC": its second part. Texts the game no longer has stay as they are, as pages may still
look them up. Run it after copying a new DUMPALL dump into the website and before magicmaterials.py, which learns how
the site words its links from all.json.

How a text is written for the site:
    {GLOBAL.XConcept.Hyperlink}, {GLOBAL.XConcept}   the concept's own Hyperlink text ("[gold/] [hyperlink,...]Gold[/hyperlink]")
    {GLOBAL.XConcept.Name}                           its Title
    {GLOBAL.PROPERTIES.TAG.Hyperlink} (UNITS, ...)   [hyperlink,GLOBAL.PROPERTIES.TAG]<the Screen Name of the resource tagged TAG>[/hyperlink]
    {LOC.MSGID}                                      that text
    {key}                                            the value typed into the text link, else <eventColor>{key}</eventColor>
    {TAG>Field}, {TAG>AREA>Radius}                   the field of the resource with that DataStoreTag (or its default) when
                                                     it holds one clear whole number, else <eventColor>{TAG>Field}</eventColor>
                                                     (ResolvedStrength: Strength x 10, Radius: Added radius)
    [bulletlist][bullet/]...[/bulletlist]           <bulletlist><bullet>...</bullet></bulletlist>, nested lists inside their bullet
    [br/] [gold/] <<m:text>>                         <br> <gold></gold> text
The GLOBAL names are the keys of the game's tooltip libraries (TEDataStoreResource: "Tooltip Global Library", ...).
A text several resources show with different values takes the values most of them have.
"""
import argparse, collections, json, os, re, sys

import aow4diff, decode, tooltips
from citytree import Game, fields, refs, value, msgid

CONCEPT = 'TDefaultGameConceptDescriptionResource'
# a typed-in value: no GLOBAL./LOC., no data store path, no markup
TYPED = re.compile(r'\{(?!GLOBAL\.|LOC\.)[^{}<>\[\]]+\}')
KEY = re.compile(r'\{[^{}<\[\]]+\}')    # not "{<hp></hp>}": the site names that concept itself
TOKEN = re.compile(rb'[A-Za-z0-9_@]{3,}')
# a data store path's field names the editor names otherwise, with the factor the game shows them by:
# a resistance check's Strength 9 is a base 90% chance, a line's or circle's Added radius its Radius
STORED_AS = {'resolvedstrength': ('strength', 10), 'radius': ('addedradius', 1)}
def needs_resource(s):
    """whether a text has values only the resources showing it can give: typed-in ones, or a data store path
    whose tag several resources share"""
    return bool(TYPED.search(s) or re.search(r'\{[A-Za-z0-9_]+>', s))


def all_refs(node):
    """every (name, id) a decoded resource refers to"""
    out = []
    def walk(n):
        if not n: return
        if n[0] == 'r': out.append((n[1], n[2]))
        elif n[0] == 'o':
            for (l, o, v) in n[2]: walk(v)
        elif n[0] == 'l':
            for x in n[1]: walk(x)
    walk(node)
    return out


def spaced(name):
    """"EventHouse" -> "Event House", as upstream wrote a script's text variables"""
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name)


BULLET_TAG = re.compile(r'(<bulletlist>|\[bulletlist\]|</bulletlist>|\[/bulletlist\]|<bullet>|\[bullet/\]|</bullet>)', re.I)


def rebuild_lists(text):
    """the same rules as RebuildBulletLists in the site's Data/Builder.js: a bullet runs until the next bullet of its
    list, its own closing tag or the list's end, a list opened inside a bullet stays in it, a closing tag straight
    after an opening one is dropped"""
    if 'bullet' not in text.lower(): return text
    out, open_, fresh = [], [False], False
    for part in BULLET_TAG.split(text):
        if not part: continue
        tag = part.lower() if BULLET_TAG.fullmatch(part) else None
        if tag is None:
            if fresh and not part.strip(): continue
            out.append(part.lstrip() if fresh else part)
        elif tag in ('<bulletlist>', '[bulletlist]'):
            out.append('<bulletlist>'); open_.append(False); fresh = True
            continue
        elif tag in ('</bulletlist>', '[/bulletlist]'):
            if len(open_) == 1: continue
            if open_[-1]: out.append('</bullet>')
            out.append('</bulletlist>'); open_.pop()
        elif tag in ('<bullet>', '[bullet/]'):
            if open_[-1]: out.append('</bullet>')
            out.append('<bullet>'); open_[-1] = True; fresh = True
            continue
        else:
            if fresh: continue
            if open_[-1]: out.append('</bullet>')
            open_[-1] = False
        fresh = False
    while True:
        if open_[-1]: out.append('</bullet>')
        if len(open_) == 1: break
        out.append('</bulletlist>'); open_.pop()
    return ''.join(out).replace('<bullet></bullet>', '')


def style_tags(s):
    """the game's style tags as the site's: [helpcolor], [abilityblue]Rare[/abilityblue], [tooltipHeader]... (styled
    in the site's style.css)"""
    s = re.sub(r'\[(/?\w+color)\]', r'<\1>', s, flags=re.I)
    return re.sub(r'\[(/?)(white|abilityblue|abilityred|fireCol|xpCol|runicfont|lorefont|lorefontsize|tooltipHeader)\]',
                  r'<\1\2>', s)


IRREGULAR = {'hero': 'heroes', 'thief': 'thieves', 'willthief': 'willthieves', 'fey': 'fey', 'undead': 'undead'}
NO_PLURAL = '\2'    # left after a name the game marks ^ns or ^p, until the plural forms are made
TITLES = {'Rally of the Lieges'}    # a name the game asks the plural of that has none


def plural_word(w):
    """the English plural of one word, keeping its capital ("City" -> "Cities", "hex" -> "hexes")"""
    low = w.lower()
    if low in IRREGULAR: p = IRREGULAR[low]
    elif low.endswith('s'): return w                  # already plural ("Syrons", "Lieges")
    elif re.search(r'[^aeiou]y$', low): p = low[:-1] + 'ies'
    elif re.search(r'(x|z|ch|sh)$', low): p = low + 'es'
    else: p = low + 's'
    return w[0] + p[1:] if w[:1].isupper() else p


def plural(phrase):
    """the plural of a name or of a link's text: its last word, or the one before "of" ("Beacon of Unity" ->
    "Beacons of Unity"). A name the game marks as plural or as not to be pluralized (^p, ^ns: "Astral Echoes",
    "Arcanium Ore") stays as it is, as does a value left for play."""
    def one(text):
        if NO_PLURAL in text or re.search(r'\^\w*[ps]\b', text) or '\0' in text: return text
        if text.strip() in TITLES: return text
        if ' of ' in text: m = re.match(r'^(.*?)(\w+)( of .*)$', text)
        else: m = re.match(r'^(.*?)(\w+)(\W*)$', text)
        return m.group(1) + plural_word(m.group(2)) + m.group(3) if m else text
    if '[hyperlink,' in phrase:
        return re.sub(r'(\[hyperlink,[^\]]*\])(.*?)(\[/hyperlink\])', lambda m: m.group(1) + one(m.group(2)) + m.group(3),
                      phrase, count=1)
    return one(phrase)


def site_markup(s):
    # the game's own slip: "[hyperlink.RoutingConcept.HeroConcept]<<Routed>>[/hyperlink]" (Immortals)
    s = re.sub(r'\[hyperlink\.(\w+)(?:\.\w+)*\]', r'[hyperlink,GLOBAL.\1]', s)
    s = re.sub(r'<<m:(.*?)>>', lambda m: plural(m.group(1)), s)   # "<<m:{GLOBAL.CityConcept.Hyperlink}>>": Cities
    s = re.sub(r'<<([^<>:]*)>>', r'\1', s)     # a plain form: "<<Routed>>"
    s = s.replace(NO_PLURAL, '')
    s = re.sub(r'\^[A-Za-z]+', '', s)          # grammar marks: "Meds^z", "Arcanium Ore^ns"
    s = rebuild_lists(s.replace('[br/]', '<br>').replace('\n', '<br>'))
    s = s.replace('</bulletlist><br>', '</bulletlist>')    # a list already ends its line
    s = re.sub(r'\[(\w+)/\]', r'<\1></\1>', s)  # icons: [gold/] -> <gold></gold>
    s = style_tags(s)
    # a link's text starts at its first word (the game's Combat Casting Point link opens with a space)
    s = re.sub(r'(\[hyperlink,[^\]]*\])\s+', r'\1', s)
    # an icon before a link, spaced as everywhere else ("[minorcity/][hyperlink,...]Free City")
    s = re.sub(r'(<(\w+)></\2>)(\[hyperlink,)', r'\1 \3', s)
    # an icon the text puts before a link that brings the same one ("3 [turn/] {GLOBAL.TurnConcept.Hyperlink}")
    s = re.sub(r'(<(\w+)></\2>)\s*(\[hyperlink,[^\]]*\])?\s*<\2></\2>', lambda m: m.group(1) + (' ' + m.group(3) if m.group(3) else ''), s)
    # the game draws an icon with room around it, the site does not: "+2 <allegiance></allegiance> Allegiance",
    # not "+2<allegiance></allegiance>Allegiance" (Void Stones)
    s = re.sub(r'([0-9%])((?:\[hyperlink,[^\]]*\])?<(\w+)></\3>)',
               lambda m: m.group(0) if m.group(3).lower() in NOT_ICONS else m.group(1) + ' ' + m.group(2), s)
    return re.sub(r'(<(\w+)></\2>)(?=[A-Za-z])', lambda m: m.group(1) + ('' if m.group(2).lower() in NOT_ICONS else ' '), s)


NOT_ICONS = {'br', 'bullet', 'bulletlist'}


def plain_name(s):
    """a name without the game's grammar marks ("Arcanium Ore^ns")"""
    return re.sub(r'\^\w*', '', s).strip()


class Texts:
    def __init__(self, game, texts):
        self.game, self.T = game, texts
        self.stats = collections.Counter()
        self.unresolved = collections.Counter()
        print('indexing the game\'s resources...', flush=True)
        # which resources hold a text id or a tag: a decode is slow, a byte search is not
        want = {m.encode() for m, s in texts.items() if needs_resource(s)}
        want |= {t.encode() for s in texts.values() for t in re.findall(r'\{(?:GLOBAL\.[A-Z_]+\.)?([A-Za-z0-9_]+)(?:>|\.Hyperlink\}|\.Name\}|\})', s)}
        self.where = collections.defaultdict(list)
        for rid, raw in game.raw.items():
            for tok in set(TOKEN.findall(raw)) & want: self.where[tok.decode()].append(rid)
        self.globals = self._libraries()
        self._users, self._tagged, self._done, self._concepts, self._linked, self._shown_by = {}, {}, {}, {}, {}, {}
        self._defaults = {}
        self.unused = set()    # texts with values to type in that no resource shows

    # ---- what the GLOBAL names stand for ----
    def _libraries(self):
        out = {}
        for rid in self.game.of_class('TEDataStoreResource'):
            count = fields(fields(self.game.node(rid)).get('Data Store')).get('Count')
            for item in (count[1] if count and count[0] == 'l' else []):
                parts = item[1] if item[0] == 'l' else []
                if len(parts) < 2: continue
                key, v = value(parts[0]), parts[1]
                if not key or key in out or v[0] != 'o': continue
                if v[1] == 'TEDataStoreValueResource':
                    r = refs(fields(v).get('Resource'))
                    if r and r[0][1] in self.game.raw: out[key] = ('res', r[0][1])
                elif v[1] in ('TEDataStoreValueInt', 'TEDataStoreValueFloat', 'TEDataStoreValueString'):
                    vals = [value(n) for l, o, n in v[2] if n and n[0] == 'v']
                    out[key] = ('val', vals[0] if vals else ('' if 'String' in v[1] else '0'))
        return out

    def tagged_all(self, tag):
        """the resources whose DataStoreTag is tag: several can share one (three hold STUNNED_EFFECT_DESCRIPTION)"""
        if tag not in self._tagged:
            self._tagged[tag] = [r for r in self.where.get(tag, [])
                                 if value(fields(self.game.node(r)).get('DataStoreTag')) == tag]
        return self._tagged[tag]

    def tagged(self, tag, context=()):
        """the resource with DataStoreTag tag the game would take: the only one, else the only one the text's own
        resources link to (or the resources they link to)"""
        found = self.tagged_all(tag)
        if len(found) <= 1: return found[0] if found else None
        near = [r for r in found if r in self.linked(context)]
        return near[0] if len(near) == 1 else None

    def linked(self, rids):
        """the resources these link to, and the ones those link to"""
        key = tuple(sorted(rids))
        if key not in self._linked:
            out, step = set(rids), set(rids)
            for _ in range(2):
                nxt = set()
                for r in step:
                    if r in self.game.raw: nxt |= {i for n, i in all_refs(self.game.node(r))}
                step = nxt - out
                out |= nxt
            self._linked[key] = out
        return self._linked[key]

    def link_text(self, rid, *names):
        """the text of the first of a resource's text links named in names, filled in with its own values"""
        f = fields(self.game.node(rid))
        for n in names:
            link = f.get(n)
            mid = msgid(link) if link else None
            if mid and mid in self.T:
                found = tooltips.links(link)
                # a key typed in with no value ("{GLOBAL.UnitConcept.Hyperlink}" = "") is filled as usual
                vals = {k: v for k, v in found[0][2] if v not in (None, '')} if found else {}
                return self.fill(self.T[mid], vals, mid)
        return None

    def concept(self, key, part):
        """{GLOBAL.<key>} (part None), {GLOBAL.<key>.Hyperlink} or {GLOBAL.<key>.Name}"""
        k = (key, part)
        if k not in self._concepts:
            self._concepts[k] = '[hyperlink,GLOBAL.%s]{GLOBAL.%s}[/hyperlink]' % (key, key)    # while it is worked out
            self._concepts[k] = self._concept(key, part)
        return self._concepts[k]

    def _concept(self, key, part):
        g = self.globals.get(key)
        if not g:
            self.unresolved['GLOBAL.' + key] += 1
            return '[hyperlink,GLOBAL.%s]{GLOBAL.%s}[/hyperlink]' % (key, key)    # the site names it
        if g[0] == 'val': return g[1]
        rid = g[1]
        if part == 'Name':
            t = self.link_text(rid, 'Title String', 'Screen Name', 'Name String')
            return plain_name(t) if t is not None else key
        t = self.link_text(rid, 'Hyperlink')
        if t: return t
        name = self.link_text(rid, 'Title String', 'Screen Name', 'Name String')
        return '[hyperlink,GLOBAL.%s]%s[/hyperlink]' % (key, plain_name(name) if name else '{GLOBAL.%s}' % key)

    def grouped(self, group, tag, part):
        """{GLOBAL.PROPERTIES.<tag>} and the like: the resource with that DataStoreTag, by its Screen Name"""
        name = next((n for n in (self.link_text(r, 'Screen Name', 'Name', 'Title String') for r in self.tagged_all(tag))
                     if n is not None), None)
        if name is None:
            self.unresolved['GLOBAL.%s.%s' % (group, tag)] += 1
            name = tag.replace('_', ' ').capitalize()
        keep = NO_PLURAL if re.search(r'\^\w*[ps]\b', name) else ''    # "Arcanium Ore^ns" stays singular
        name = plain_name(name)
        return name if part == 'Name' else '[hyperlink,GLOBAL.%s.%s]%s%s[/hyperlink]' % (group, tag, name, keep)

    def defaults(self, cls):
        """{field label without spaces, lower case: value} of a class's whole-number fields, as the game reads the
        ones a resource leaves out (the serializer skips defaults: Pacified's Num Turns, 1)"""
        if cls not in self._defaults:
            S, out = self.game.S, {}
            cid = S.by_name.get(cls)
            for c in S.chain(cid) if cid else ():
                for f in S.cls[c]['f'].values():
                    d = S.display(c, f[2]) if f[2] is not None else None
                    if d and f[1] in decode.INT_SLOTS:
                        out.setdefault(d[0].replace(' ', '').lower(), self.game.D.default_text(f, 4))
            self._defaults[cls] = out
        return self._defaults[cls]

    def stored(self, key, context=()):
        """{TAG>Field} or {TAG>AREA>Radius}: the field of the resource with that DataStoreTag, when it holds one
        clear whole number. Several resources sharing the tag do when they agree (Infernal Blaze's two)."""
        tag = key.strip('{}').split('>')[0]
        rid = self.tagged(tag, context)
        if rid: return self.stored_in(rid, key)
        vals = {self.stored_in(r, key) for r in self.tagged_all(tag)}
        return vals.pop() if len(vals) == 1 else None

    def stored_in(self, rid, key):
        parts = key.strip('{}').split('>')
        hints, field = [p.lower() for p in parts[1:-1]], parts[-1].replace(' ', '').lower()
        v = self.field_value(rid, hints, field)
        if v is None and field in STORED_AS:
            field, factor = STORED_AS[field]
            v = self.field_value(rid, hints, field)
            if v is not None: v = str(int(v) * factor)
        return v

    def field_value(self, rid, hints, field):
        found = []
        def walk(n, path):
            if not n: return
            if n[0] == 'o':
                have = set()
                for (l, o, v) in n[2]:
                    p = path + '/' + l.lower()
                    have.add(l.replace(' ', '').lower())
                    if l.replace(' ', '').lower() == field and v and v[0] == 'v': found.append((p, value(v)))
                    walk(v, p)
                if field not in have:
                    d = self.defaults(n[1]).get(field)
                    # a circle area, a class the schema does not name: its first field is the radius, 1 if left out
                    if d is None and n[1] is None and field == 'addedradius' and path.endswith('/area'):
                        d = value(dict((l, v) for l, o, v in n[2]).get('field_1')) or '1'
                    if d is not None: found.append((path + '/' + field, d))
            elif n[0] == 'l':
                for x in n[1]: walk(x, path)
        walk(self.game.node(rid), '')
        for h in hints:
            narrowed = [f for f in found if h in f[0]]
            if narrowed: found = narrowed
        vals = {v for p, v in found}
        if len(vals) == 1 and re.fullmatch(r'-?\d+', next(iter(vals))): return next(iter(vals))
        return None

    # ---- texts ----
    def users(self, mid):
        """the values typed into the text links that show mid, those most of them have"""
        if mid not in self._users:
            seen, shown_by = collections.Counter(), []
            for rid in self.where.get(mid, []):
                for path, m, kv in tooltips.links(self.game.node(rid)):
                    if m == mid:
                        seen[tuple(sorted((k, v) for k, v in kv if v not in (None, '')))] += 1
                        shown_by.append(rid)
            self._shown_by[mid] = shown_by
            if len(seen) > 1: self.stats['texts whose resources differ'] += 1
            if not seen: self.unused.add(mid)
            self._users[mid] = dict(seen.most_common(1)[0][0]) if seen else {}
        return self._users[mid]

    def fill(self, s, vals, mid='', context=()):
        def one(m):
            key = m.group(0)
            if key in vals: return vals[key]
            g = re.fullmatch(r'\{GLOBAL\.([A-Z_]+)\.([A-Za-z0-9_]+?)(?:\.(Hyperlink|Name))?\}', key)
            if g and g.group(1).isupper() and not g.group(1).endswith('Concept'):
                return self.grouped(g.group(1), g.group(2), g.group(3))
            g = re.fullmatch(r'\{GLOBAL\.([A-Za-z0-9_]+?)(?:\.(Hyperlink|Name))?\}', key)
            if g: return self.concept(g.group(1), g.group(2))
            g = re.fullmatch(r'\{LOC\.([^{}]+)\}', key)
            if g:
                t = self.T.get(g.group(1))
                if t is not None: return t
            if '>' in key:
                v = self.stored(key, context)
                if v is not None: self.stats['stored values filled'] += 1; return v
            # a script's text variables: "{Player.Name}" and "{INT_Previous}" read as Player and Previous
            g = re.fullmatch(r'\{(\w+)\.Name\}|\{INT_(\w+)\}', key)
            if g: return spaced(g.group(1) or g.group(2))
            self.stats['values left for play'] += 1
            return '\0' + key.strip('{}') + '\1'    # left as is; the next round must not fill it again
        for _ in range(4):
            filled = KEY.sub(one, s)
            if filled == s: break
            s = filled
        return s

    def __call__(self, mid):
        if mid not in self._done:
            s = self.T[mid]
            vals = self.users(mid) if needs_resource(s) else {}
            s = self.fill(s, vals, mid, self._shown_by.get(mid, ()))
            s = site_markup(s).replace('\0', '<eventColor>{').replace('\1', '}</eventColor>')
            self._done[mid] = s
        return self._done[mid]


def build(texts_of, old):
    """all.json's entries with every text the game has written anew, in all.json's own order (upstream's, which is
    no plain sort), new ids after them"""
    entries = {e['id']: dict(e) for e in old}
    order = [e['id'] for e in old]
    changed, added, same, kept = 0, 0, 0, 0
    for mid in sorted(texts_of.T):
        if '@' not in mid or mid.startswith('@'): continue
        id_, field = mid.rsplit('@', 1)
        field = field.lower()
        if field in ('id', 'dlc'): continue
        new = texts_of(mid)
        if id_ not in entries: order.append(id_)
        e = entries.setdefault(id_, {'id': id_})
        # a text no resource shows has no values to fill in: the one the site has keeps its numbers
        if mid in texts_of.unused and TYPED.search(texts_of.T[mid]) and field in e:
            # its wording stays; its lists and plural forms are written as the site's other texts
            text = re.sub(r'\[hyperlink\.(\w+)(?:\.\w+)*\]', r'[hyperlink,GLOBAL.\1]', e[field])
            text = re.sub(r'<<([^<>:]*)>>', r'\1', re.sub(r'<<m:(.*?)>>', lambda m: plural(m.group(1)), text))
            e[field] = style_tags(rebuild_lists(text))
            kept += 1
            continue
        if id_.startswith('DLC@') and 'DLC' not in e: e['DLC'] = id_.split('@')[1]
        if field not in e: added += 1
        elif e[field] != new: changed += 1
        else: same += 1
        e[field] = new
    return [entries[k] for k in order], {'unchanged': same, 'changed': changed, 'added': added,
                                         'kept (no resource shows them)': kept}


def build_concepts(texts_of, old):
    """concept_tooltips.json: {"GLOBAL.<key>": {name, icon, globalName, displayHtml, description}} for every concept
    the game's tooltip libraries name. name is the Title's text, icon its icon, displayHtml the wording inside the
    concept's Hyperlink ("<hp></hp> Hit Point"), description its Description String with its links (the site opens a
    tooltip on top of the tooltip, as the game does). Concepts the game no longer has stay as they are."""
    out = dict(old)
    made = 0
    for key, (kind, rid) in sorted(texts_of.globals.items()):
        if kind != 'res' or texts_of.game.cls(rid) != CONCEPT: continue
        f = fields(texts_of.game.node(rid))
        mid = lambda n: msgid(f.get(n)) if f.get(n) else None
        title_mid, desc_mid, link_mid = mid('Title String'), mid('Description String'), mid('Hyperlink')
        if not title_mid or title_mid not in texts_of.T: continue
        title = texts_of(title_mid)
        name = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>|\[/?hyperlink[^\]]*\]', '', title)).strip()
        if not name: continue
        link = texts_of(link_mid) if link_mid and link_mid in texts_of.T else ''
        # the icon: the Title's, else the Hyperlink's, else the one the site had ("personalityAstral02")
        icon = re.search(r'<(\w+)></\1>', title) or re.search(r'<(\w+)></\1>', link)
        icon = icon.group(1) if icon else (old.get('GLOBAL.' + key) or {}).get('icon')
        display = name
        inner = re.search(r'\[hyperlink,[^\]]*\](.*?)\[/hyperlink\]', link)
        if inner and inner.group(1).strip(): display = inner.group(1).strip()
        description = texts_of(desc_mid) if desc_mid and desc_mid in texts_of.T else ''
        out['GLOBAL.' + key] = {'name': name, 'icon': icon or None, 'globalName': 'GLOBAL.' + key,
                                'displayHtml': display, 'description': description}
        made += 1
    return out, made


DESCRIPTION_FIELDS = ('Screen Description', 'Description', 'Description String', 'Tooltip Description', 'Lore')


def build_grouped(texts_of, texts, out, site):
    """concept tooltips for the links to a property, unit, equipment... ([hyperlink,GLOBAL.PROPERTIES.BURNING]): the
    resource with that DataStoreTag, its Screen Name and description. Left out where the site already shows the
    ability of that name (Data/EN/Abilities.json, Data/GEN/ExtraToolTips.json, and LinkedAbilities.json, which
    abilities.py writes before), whose tooltip has its icon."""
    abilities = set()
    for path in (os.path.join(site, 'Data', 'EN', 'Abilities.json'), os.path.join(site, 'Data', 'GEN', 'ExtraToolTips.json'),
                 os.path.join(site, 'Data', 'GEN', 'LinkedAbilities.json')):
        if os.path.exists(path):
            for a in json.load(open(path, encoding='utf-8-sig')):
                if isinstance(a, dict) and a.get('name'): abilities.add(a['name'].split('^')[0].strip().lower())
    linked = set()
    for e in texts:
        for v in e.values():
            if isinstance(v, str): linked.update(re.findall(r'\[hyperlink,(GLOBAL\.[A-Z_]+\.[A-Za-z0-9_]+)\]', v))
    made = 0
    for key in sorted(linked):
        group, tag = key.split('.')[1:]
        rid = next(iter(texts_of.tagged_all(tag)), None)
        if not rid: continue
        name = texts_of.link_text(rid, 'Screen Name', 'Name', 'Title String')
        if not name: continue
        name = plain_name(re.sub(r'<[^>]+>|\[/?hyperlink[^\]]*\]', '', name))
        if not name or name.lower() in abilities: continue
        description = texts_of.link_text(rid, *DESCRIPTION_FIELDS) or ''
        description = site_markup(description).replace('\0', '<eventColor>{').replace('\1', '}</eventColor>')
        if not description.strip(): continue
        out[key] = {'name': name, 'icon': None, 'globalName': key, 'displayHtml': name, 'description': description}
        made += 1
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--site', help='the website\'s folder (default: "site" in config.json)')
    ap.add_argument('--acp', help='the mod (default: the first .acp in the Mods folder)')
    ap.add_argument('--no-mod', action='store_true', help='read the game alone')
    ap.add_argument('--dry-run', action='store_true', help='report what would change, write nothing')
    args = ap.parse_args()
    cfg = aow4diff.load_config()
    site = args.site or cfg.get('site')
    if not site: sys.exit('which website? pass --site or set "site" in config.json')
    acp = None if args.no_mod else args.acp or (aow4diff.mod_acps(cfg) or [None])[0]
    game = Game(cfg, acp)
    texts = Texts(game, tooltips.load_texts(cfg['game'], os.path.dirname(acp) if acp else ''))
    path = os.path.join(site, 'Data', 'EN', 'all.json')
    old = json.load(open(path, encoding='utf-8-sig'))
    new, counts = build(texts, old)
    print('%d entries: %s' % (len(new), ', '.join('%d %s' % (n, k) for k, n in counts.items())))
    for k, n in texts.stats.items(): print('  %d %s' % (n, k))
    if texts.unresolved:
        print('  %d GLOBAL names the game\'s libraries lack, the most used:' % len(texts.unresolved),
              ', '.join('%s (%d)' % kv for kv in texts.unresolved.most_common(8)))
    cpath = os.path.join(site, 'Data', 'EN', 'concept_tooltips.json')
    old_concepts = json.load(open(cpath, encoding='utf-8-sig')) if os.path.exists(cpath) else {}
    concepts, made = build_concepts(texts, {k: v for k, v in old_concepts.items() if k.count('.') == 1})
    grouped = build_grouped(texts, new, concepts, site)
    print('%d concepts: %d from the game, %d the game no longer has kept, %d links to properties, units and the like'
          % (len(concepts), made, len(concepts) - made - grouped, grouped))
    if args.dry_run: return new, concepts
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(new, f, indent=2, ensure_ascii=False)
    print('->', path)
    with open(cpath, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(concepts, f, indent=2, ensure_ascii=False)
    print('->', cpath)


if __name__ == '__main__':
    main()
