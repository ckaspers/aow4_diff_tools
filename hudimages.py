"""Images of the game's interface (the world map HUD's buttons and frames, panels, ...) by the names its UI gives them,
read from the live game. Reads the game folder from config.json.

    python hudimages.py find PATTERN                 the UI's image names that contain PATTERN, with where each is
    python hudimages.py export PATTERN FOLDER        save each of them as FOLDER/<name>.png

Where the game keeps them: Content/Title/UI/Resources/Atlas/*.xaml map each name to an image in an atlas,
    <gc:ImageLink x:Key="EmpireButton_Icon" ID="[INTERFACEASSETS]TXA\\13?221"/>
the content library's own name (INTERFACEASSETS is Libraries/Interface/InterfaceAssets.clb; DLC skins have their own,
INTERFACEASSETS_WAYSOFWAR ...), the atlas's number and the image's id in it. The id is the image record's own (no
shift, unlike a text icon's: image 221 of TopBar's atlas 13 is named EmpireButton_Icon). The atlases are read as
tmlicons.py reads the text icons' (tmlicons.atlases, BC7 decoded by bc7.py). clbicons.py does not see these images: the
library keeps an atlas whole, not image by image.

A HUD button is drawn in layers, each its own image: the round Empire button is ResearchButton_Frame (the steel-teal
ring), EmpireButton_Gem_DEFAULT (the glass) and EmpireButton_Icon (the picture), all 128x128 and centred on each
other; Spells is SpellButton_Frame, _Gem_DEFAULT and _Icon, the Item Forge ForgeButton_Gem_DEFAULT and _Icon_DEFAULT.
States are separate images (_DEFAULT, _OVER, _DOWN, _SELECTED, _DISABLED).
"""
import argparse, glob, os, re, sys

import aow4diff, bc7, clbicons, tmlicons

LINK = re.compile(r'x:Key="([^"]+)"\s+ID="\[([A-Z0-9_]+)\]TXA\\(\d+)\?(\d+)"')


def links(game):
    """[(name, library, atlas number, image id, xaml file)] of every image the UI names"""
    out = []
    for path in sorted(glob.glob(os.path.join(game, 'Content', 'Title', 'UI', 'Resources', 'Atlas', '*.xaml'))):
        with open(path, encoding='utf-8-sig') as f:
            for m in LINK.finditer(f.read()):
                out.append((m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), os.path.basename(path)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('find', help='the UI\'s image names that contain PATTERN'); p.add_argument('pattern')
    p = sub.add_parser('export', help='save the images whose name contains PATTERN as PNG'); p.add_argument('pattern'); p.add_argument('folder')
    args = ap.parse_args()
    game = aow4diff.load_config()['game']
    wanted = [l for l in links(game) if args.pattern.lower() in l[0].lower()]
    if not wanted: sys.exit('no image of the UI is named like "%s"' % args.pattern)
    if args.cmd == 'find':
        for name, lib, number, ident, xaml in wanted:
            print('%-48s [%s]TXA\\%d?%d  (%s)' % (name, lib, number, ident, xaml))
        return
    found = tmlicons.atlases(game, {l[1] for l in wanted})
    os.makedirs(args.folder, exist_ok=True)
    saved, missing = 0, []
    for name, lib, number, ident, xaml in wanted:
        a = found.get((lib, number))
        if not a or ident not in a['images']: missing.append(name); continue
        tex_lib, tex = a['texture']
        if a['data'] is None: a['data'] = tex_lib.pixels(tex)
        _, x, y, w, h = a['images'][ident]
        if tex['format'] == clbicons.RGBA:
            row = tex['width'] * 4
            rgba = b''.join(a['data'][(y + r) * row + x * 4:(y + r) * row + (x + w) * 4] for r in range(h))
        else:
            rgba = bc7.decode(a['data'], tex['width'], tex['height'], x, y, w, h)
        clbicons.write_png(os.path.join(args.folder, name + '.png'), w, h, rgba)
        saved += 1
    print('%d images -> %s' % (saved, args.folder))
    if missing: print('  %d not found in their atlas: %s' % (len(missing), ', '.join(missing)))


if __name__ == '__main__':
    main()
