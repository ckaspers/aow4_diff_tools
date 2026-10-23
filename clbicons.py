"""Images from the game's content libraries (.clb), for the icons DUMPALL leaves out (combat enchantments,
status effects, world structures, ...). Reads the game folder from config.json.

    python clbicons.py find PATTERN                        images whose name contains PATTERN, in every library
    python clbicons.py list LIBRARY [PATTERN]              the images in one library
    python clbicons.py export LIBRARY NAME OUT.png [--crop]
    python clbicons.py export-all LIBRARY FOLDER [PATTERN] [--crop]

LIBRARY is a path or a library's name (Gameplay_Icons_EldritchRealms). --crop trims the transparent
border, as the website's hand-made icons are (CombatEnchantment_UmbralRamblings: 256x256 -> 218x248).
An atlas the editor shows (CombatEnchantments, 6336x288) is built from its images when the game loads;
the library keeps each image on its own, so export the image, not the atlas. A name that is in two
atlases (47 in Gameplay_Icons, the same size each time) is exported from the first.

The format, as far as it is needed here (checked on all 876 libraries of the game):
    header   'CRL\\0', u32 [3] string table size, [4] records size; strings start at 0x20, the records follow,
             the data area starts at the next 16-byte boundary after them
    string   referenced as (u32 length, u32 offset from 0x20)
    texture  a record: field count, (field id, offset) byte pairs, then the values: 0x14 width, 0x15
             height, 0x19 depth, 0x17 format (83 = 8-bit RGBA), 0x18 flags, 0x16 data: u32 offset in the
             data area, u32 storage (2 = LZ4 block, 0 = as is), u32 size, u32 stored size
    image    a record naming the image, its texture nested a little after; its smaller copies follow as a
             list of textures, each with its class id 41 00 24 in front
Icon images are 8-bit RGBA; their smaller copies and other textures are block-compressed, not handled here.
"""
import argparse, json, os, re, struct, sys, zlib

import aow4diff

TABLE = re.compile(rb'([\x03-\x08])((?:[\x14-\x19].){3,8})', re.S)
LISTED = b'\x24\x00\x41\x00'
RGBA = 83


class Library:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            head = f.read(32)
            if head[:4] != b'CRL\x00': raise ValueError('not a content library: ' + path)
            h = struct.unpack_from('<6I', head)
            self.data = (0x20 + h[3] + h[4] + 15) & ~15
            f.seek(0)
            self.head = f.read(self.data)       # strings and records; the data area is read when needed

    def string(self, length, offset):
        """the string a (length, offset) pair points to, if it is one"""
        s = self.head[0x20 + offset:0x20 + offset + length + 1]
        if len(s) != length + 1 or s[-1] != 0 or not s[:-1].isascii(): return None
        if offset and self.head[0x20 + offset - 1] != 0: return None     # the start of a string, not its tail
        s = s[:-1].decode()
        return s if s.isprintable() else None

    def images(self):
        """(name, texture) for every image: a texture record that is no smaller copy, with the nearest name
        before it"""
        d = self.head
        for m in TABLE.finditer(d, 0, self.data):
            n = m.group(1)[0]
            pairs = m.group(2)[:2 * n]
            if len(pairs) < 2 * n: continue
            fids, offs = pairs[0::2], pairs[1::2]
            if not {0x14, 0x15, 0x16} <= set(fids) or offs[0] != 0 or list(offs) != sorted(offs): continue
            if d[m.start() - 4:m.start()] == LISTED: continue
            body = m.end(1) + 2 * n
            f = dict(zip(fids, offs))
            tex = {'width': struct.unpack_from('<I', d, body + f[0x14])[0],
                   'height': struct.unpack_from('<I', d, body + f[0x15])[0],
                   'format': struct.unpack_from('<I', d, body + f[0x17])[0] if 0x17 in f else None}
            tex['offset'], tex['storage'], tex['size'], tex['stored'] = struct.unpack_from('<4I', d, body + f[0x16])
            name = None
            for back in range(m.start() - 8, max(0, m.start() - 400), -1):
                length, offset = struct.unpack_from('<II', d, back)
                if 0 < length < 200 and (name := self.string(length, offset)) and re.fullmatch(r'[\w .\-]+', name): break
                name = None
            yield name or '?', tex

    def pixels(self, tex):
        with open(self.path, 'rb') as f:
            f.seek(self.data + tex['offset'])
            raw = f.read(tex['stored'] + 16)
        if tex['storage'] == 0: return raw[:tex['size']]
        if tex['storage'] == 2: return lz4_block(raw, tex['size'])
        raise ValueError('storage %d not handled' % tex['storage'])


def lz4_block(src, size):
    """one LZ4 block (no frame) that unpacks to size bytes"""
    out = bytearray()
    pos = 0
    while len(out) < size:
        tok = src[pos]; pos += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[pos]; pos += 1; lit += b
                if b != 255: break
        out += src[pos:pos + lit]; pos += lit
        if len(out) >= size: break
        off = src[pos] | (src[pos + 1] << 8); pos += 2
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[pos]; pos += 1; ml += b
                if b != 255: break
        ml += 4
        start = len(out) - off
        if off == 0 or start < 0: raise ValueError('not an LZ4 block')
        if off >= ml: out += out[start:start + ml]
        else:
            for i in range(ml): out.append(out[start + i])
    return bytes(out[:size])


def write_png(path, w, h, rgba, crop=False):
    if crop:
        rows = [y for y in range(h) if any(rgba[(y * w) * 4 + 3:(y + 1) * w * 4:4])]
        if rows:
            y0, y1 = rows[0], rows[-1] + 1
            cols = [x for x in range(w) if any(rgba[(y * w + x) * 4 + 3] for y in range(y0, y1))]
            x0, x1 = cols[0], cols[-1] + 1
            rgba = b''.join(rgba[(y * w + x0) * 4:(y * w + x1) * 4] for y in range(y0, y1))
            w, h = x1 - x0, y1 - y0
    raw = b''.join(b'\x00' + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    def chunk(t, data): return struct.pack('>I', len(data)) + t + data + struct.pack('>I', zlib.crc32(t + data) & 0xffffffff)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))
    return w, h


def libraries(game):
    for root, _, files in os.walk(os.path.join(game, 'Content')):
        for f in sorted(files):
            if f.lower().endswith('.clb'): yield os.path.join(root, f)


def open_library(game, which):
    if os.path.isfile(which): return Library(which)
    want = os.path.splitext(os.path.basename(which))[0].lower()
    for p in libraries(game):
        if os.path.splitext(os.path.basename(p))[0].lower() == want: return Library(p)
    sys.exit('no library %s under %s' % (which, os.path.join(game, 'Content')))


def describe(name, tex):
    fmt = 'RGBA' if tex['format'] == RGBA else 'format %s' % tex['format']
    return '%-48s %4dx%-4d %s' % (name, tex['width'], tex['height'], fmt)


def export(lib, name, tex, out, crop):
    if tex['format'] != RGBA:
        print('  skipped %s: format %s is block-compressed, not handled' % (name, tex['format'])); return
    px = lib.pixels(tex)
    if len(px) != tex['width'] * tex['height'] * 4: sys.exit('%s: unexpected data size' % name)
    w, h = write_png(out, tex['width'], tex['height'], px, crop)
    print('  %s -> %s (%dx%d)' % (name, out, w, h))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('find', help='images whose name contains PATTERN, in every library'); p.add_argument('pattern')
    p = sub.add_parser('list', help='the images in one library'); p.add_argument('library'); p.add_argument('pattern', nargs='?', default='')
    p = sub.add_parser('export', help='save one image as PNG')
    p.add_argument('library'); p.add_argument('name', help='the image (exact name)'); p.add_argument('out', help='OUT.png')
    p.add_argument('--crop', action='store_true', help='trim the transparent border')
    p = sub.add_parser('export-all', help='save a library\'s images as PNG, named after them')
    p.add_argument('library'); p.add_argument('folder')
    p.add_argument('pattern', nargs='?', default='', help='only images whose name contains this')
    p.add_argument('--crop', action='store_true', help='trim the transparent border')
    args = ap.parse_args()
    game = aow4diff.load_config()['game']

    if args.cmd == 'find':
        pat = args.pattern.lower()
        for path in libraries(game):
            hits = [(n, t) for n, t in Library(path).images() if pat in n.lower()]
            if hits:
                print(os.path.relpath(path, os.path.join(game, 'Content')))
                for n, t in hits: print('  ' + describe(n, t))
    elif args.cmd == 'list':
        lib = open_library(game, args.library)
        for n, t in lib.images():
            if args.pattern.lower() in n.lower(): print(describe(n, t))
    elif args.cmd == 'export-all':
        lib = open_library(game, args.library)
        os.makedirs(args.folder, exist_ok=True)
        for n, t in lib.images():
            if args.pattern.lower() in n.lower(): export(lib, n, t, os.path.join(args.folder, n + '.png'), args.crop)
    else:
        lib = open_library(game, args.library)
        found = [(n, t) for n, t in lib.images() if n == args.name]
        if not found: sys.exit('no image %s in %s (try: list %s %s)' % (args.name, lib.path, args.library, args.name[:12]))
        export(lib, *found[0], args.out, args.crop)


if __name__ == '__main__':
    main()
