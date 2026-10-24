"""The game's text icons ([casusbelli/], [unitHero/], [badRelation/], ...) for an aow4db-style website: an image for
each and a style for each the site's own stylesheets lack, read from the live game with a mod on top if one is given.

    python tmlicons.py --site PATH [--acp MOD.acp | --no-mod]

Writes <site>/Icons/TML/<tag>.png for every icon tag the game has, and <site>/Style/tmlicons.css: a rule for each
tag no other stylesheet of the site styles (a page shows a tag without a rule as nothing). A tag the site already
styles by hand keeps its own look.

Where the game keeps them: a text icon is a TGCNoesisTMLIconTag, its tag and an Image: "[<LIBRARY>]TXA\\<n>", the
atlas's name and the image's id in it. LIBRARY is a content library's own name, the first string of its table
(TML_ICONS is Libraries/Interface/TML_Icons.clb; DIPLOMACY_ICONS and UNITYPEICONS hold the relation and unit type
icons). An atlas is one texture (BC7, or plain RGBA) with a record per image: field 1 its id, 2 its position and 3
its size as fractions of the atlas (2 is left out at 0,0), 6 and 7 its size in pixels. A tag's id counts the atlas's
own two entries before its images (Food.png is image 141, [food/] asks for 143). An atlas with no image records is
one image (TML_ICONS TXA\\3, the Eldritch Realms Void Stones icon).
"""
import argparse, glob, os, re, struct, sys

import aow4diff, bc7, clbicons
from citytree import Game, fields, value

BC7 = (191, 192)    # BC7 sRGB, BC7
# a tag's id is its image's id plus this, as checked against the images' names: [food/] asks for 143, Food.png is
# TML_ICONS image 141; [unitRuler/] asks for 40, UnitType_Leader is UNITYPEICONS image 39; [awfulDeal/] 10 is
# Trade_AwfulDeal, DIPLOMACY_ICONS image 9. Where the difference comes from is not known.
SHIFT = {'TML_ICONS': 2}
# tags the texts use that the game does not define, and the icon they clearly mean (typos and old names; the game
# shows nothing for them)
ALIAS = {'bindingfragment': 'bindingfragments', 'imperium': 'influence', 'damageshock': 'damagelightning',
         'firedefense': 'defensefire', 'frostdefense': 'defensefrost', 'movewalk': 'movementwalk', 'thrall': 'thralls',
         'popualtion': 'population', 'soul': 'souls', 'war': 'rs_war', 'relation': 'neutralrelation',
         'relation5': 'relationlvl2', 'fortification': 'garrison'}
TXA = re.compile(rb'\[([A-Z0-9_]+)\]TXA\\(\d+)')


def library_name(path):
    """a content library's own name, the first string of its table ("TML_ICONS")"""
    with open(path, 'rb') as f: head = f.read(0x60)
    m = re.match(rb'[A-Z0-9_]+', head[0x20:])
    return m.group(0).decode() if m else None


def atlases(game_dir, wanted):
    """{(library, atlas number): {'texture': (library, tex), 'images': {id: (name, x, y, w, h)}}} for the libraries
    named in wanted"""
    out = {}
    for path in clbicons.libraries(game_dir):
        libname = library_name(path)
        if libname not in wanted: continue
        lib = clbicons.Library(path)
        h = struct.unpack_from('<6I', lib.head)
        strings = lib.head[0x20:0x20 + h[3]]
        base = 0x20 + h[3]
        recs = lib.head[base:base + h[4]]
        starts = []
        for m in TXA.finditer(strings):
            if m.group(1).decode() != libname: continue
            at = recs.find(struct.pack('<II', len(m.group(0)), m.start()))
            if at >= 0: starts.append((at, int(m.group(2))))
        starts.sort()
        if not starts: continue
        names = {m.start(): m.group(0) for m in re.finditer(rb'[^\x00]+', strings)}
        images = []
        for i in range(len(recs) - 9):
            length, off = struct.unpack_from('<II', recs, i)
            name = names.get(off)
            if not name or len(name) != length or length < 2 or b'\\' in name: continue    # "XP" has two letters
            n = recs[i + 8]
            if not 1 <= n <= 16: continue
            pairs = recs[i + 9:i + 9 + 2 * n]
            fo = dict(zip(pairs[0::2], pairs[1::2]))
            if not all(k in fo for k in (3, 6, 7)): continue
            body = i + 9 + 2 * n
            u, v = struct.unpack_from('<2f', recs, body + fo[2]) if 2 in fo else (0.0, 0.0)
            ident = struct.unpack_from('<I', recs, body + fo[1])[0] if 1 in fo else 0    # id 0 is not written
            images.append((i, name.decode(), ident, u, v,
                           struct.unpack_from('<I', recs, body + fo[6])[0], struct.unpack_from('<I', recs, body + fo[7])[0]))
        textures = []
        for mt in clbicons.TABLE.finditer(lib.head, 0, lib.data):
            k = mt.group(1)[0]
            pairs = mt.group(2)[:2 * k]
            fids, offs = pairs[0::2], pairs[1::2]
            if not {0x14, 0x15, 0x16} <= set(fids) or offs[0] != 0: continue
            body = mt.end(1) + 2 * k
            f = dict(zip(fids, offs))
            w, hh = struct.unpack_from('<II', lib.head, body)
            fmt = struct.unpack_from('<I', lib.head, body + f[0x17])[0] if 0x17 in f else None
            o, storage, size, stored = struct.unpack_from('<4I', lib.head, body + f[0x16])
            textures.append({'at': mt.start() - base, 'width': w, 'height': hh, 'format': fmt,
                             'offset': o, 'storage': storage, 'size': size, 'stored': stored})
        ends = [s[0] for s in starts[1:]] + [len(recs)]
        for (at, number), end in zip(starts, ends):
            texs = [t for t in textures if at <= t['at'] < end and t['format'] in BC7 + (clbicons.RGBA,)]
            if not texs: continue
            tex = max(texs, key=lambda t: t['width'] * t['height'])    # the atlas itself, not its smaller copies
            ims = {}
            for i, name, ident, u, v, w, hh in images:
                if at <= i < end:
                    ims[ident] = (name, round(u * tex['width']), round(v * tex['height']), w, hh)
            out.setdefault((libname, number), {'texture': (lib, tex), 'images': ims, 'data': None})
    return out


def icon_tags(game):
    """[(tag, library, atlas number, image id)] of the game's text icons"""
    out = []
    for rid in game.of_class('TGCNoesisTMLIconTag'):
        tag = value(fields(game.node(rid)).get('field_3'))
        raw = game.raw[rid]
        m = TXA.search(raw)
        if not tag or not m: continue
        at = m.end()
        length = struct.unpack_from('<I', raw, at)[0]          # the atlas's name, then the image's id
        # an id of 0, the default, is not written (the Void Stones icon, alone in its atlas)
        ident = struct.unpack_from('<I', raw, at + 4 + length)[0] if at + 8 + length <= len(raw) else 0
        out.append((tag, m.group(1).decode(), int(m.group(2)), ident))
    return out


def styled_tags(site):
    """the tags the site's own stylesheets style"""
    css = ''
    for p in glob.glob(os.path.join(site, 'Style', '*.css')):
        if os.path.basename(p).lower() != 'tmlicons.css': css += open(p, encoding='utf-8', errors='replace').read()
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    tags = set()
    for sel in re.findall(r'([^{}]+)\{', css):
        for part in sel.split(','):
            m = re.fullmatch(r'\s*([A-Za-z_][\w-]*)\s*', part)
            if m: tags.add(m.group(1).lower())
    return tags


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
    tags = icon_tags(game)
    found = atlases(cfg['game'], {lib for t, lib, n, i in tags})
    folder = os.path.join(site, 'Icons', 'TML')
    os.makedirs(folder, exist_ok=True)
    saved, missing = [], []
    for tag, libname, number, ident in sorted(tags):
        a = found.get((libname, number))
        if not a: missing.append('%s (%s atlas %d)' % (tag, libname, number)); continue
        lib, tex = a['texture']
        if a['data'] is None: a['data'] = lib.pixels(tex)
        shift = SHIFT.get(libname, 1)
        if len(a['images']) == 1:                                  # an atlas of one image (Void Stones)
            name, x, y, w, h = next(iter(a['images'].values()))
        elif a['images']:
            if ident - shift not in a['images']: missing.append('%s (image %d of %s atlas %d)' % (tag, ident, libname, number)); continue
            name, x, y, w, h = a['images'][ident - shift]
        elif tex['width'] <= 128 and tex['height'] <= 128:
            x, y, w, h = 0, 0, tex['width'], tex['height']      # an atlas of one image
        else:
            missing.append('%s (%s atlas %d has no image records)' % (tag, libname, number)); continue
        if tex['format'] == clbicons.RGBA:
            row = tex['width'] * 4
            rgba = b''.join(a['data'][(y + r) * row + x * 4:(y + r) * row + (x + w) * 4] for r in range(h))
        else:
            rgba = bc7.decode(a['data'], tex['width'], tex['height'], x, y, w, h)
        clbicons.write_png(os.path.join(folder, tag.lower() + '.png'), w, h, rgba)
        saved.append(tag.lower())
    own = styled_tags(site)
    rules = ['/* The game\'s text icons the site\'s own stylesheets do not style. Written by aow4_diff_tools/tmlicons.py:',
             '   do not edit, a rule in another stylesheet takes a tag over. */']
    styles = {tag: tag for tag in set(saved) - own}
    styles.update({alias: tag for alias, tag in ALIAS.items() if alias not in own and alias not in saved and tag in saved})
    for tag in sorted(styles):
        rules.append('%s {\n    max-height: 20px;\n    top: 3px;\n    content: url(/evolved/Icons/TML/%s.png);\n    position: relative;\n}' % (tag, styles[tag]))
    with open(os.path.join(site, 'Style', 'tmlicons.css'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(rules) + '\n')
    print('%d icons -> %s; %d styled in Style/tmlicons.css, %d by the site itself' % (len(saved), folder, len(set(saved) - own), len(set(saved) & own)))
    if missing: print('  %d tags whose image the game no longer has: %s' % (len(missing), ', '.join(missing)))


if __name__ == '__main__':
    main()
