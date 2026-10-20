#!/usr/bin/env python3
"""Build schema_db.json from the game's ResourceED.exe: class names and ids, every serialized
field's id, name and kind, editor labels, member types, and the trigger script registry.

  python extract_schema.py --exe "<AoW4 folder>/ResourceED.exe" [--out schema_db.json]

Needs a disassembler: the `capstone` Python package (pip install capstone), or GNU objdump
on PATH (Linux, WSL, MSYS2). Re-run it after every game update; it takes a few minutes.
"""
import argparse, bisect, json, os, re, shutil, struct, subprocess, sys, time
from array import array

# ---- PE file -------------------------------------------------------------------------
class PE:
    def __init__(self, path):
        self.path = path
        self.data = d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        nsec = struct.unpack_from('<H', d, pe + 6)[0]
        opt = struct.unpack_from('<H', d, pe + 20)[0]
        self.base = struct.unpack_from('<Q', d, pe + 24 + 24)[0]
        self.secs = []
        o = pe + 24 + opt
        for _ in range(nsec):
            name = d[o:o + 8].rstrip(b'\0').decode('latin1')
            vs, va, rs, ro = struct.unpack_from('<IIII', d, o + 8)
            self.secs.append((name, va, vs, ro, rs)); o += 40
        self.sec = {s[0]: s for s in self.secs}
        n, va, vs, ro, rs = self.sec['.pdata']
        pd = array('I'); pd.frombytes(d[ro:ro + vs // 12 * 12])
        self.fstart = list(pd[0::3]); self.fend = list(pd[1::3])
    def v2f(self, v):
        r = v - self.base
        for n, va, vs, ro, rs in self.secs:
            if va <= r < va + rs: return ro + r - va
        return None
    def f2v(self, off):
        for n, va, vs, ro, rs in self.secs:
            if ro <= off < ro + rs: return self.base + va + off - ro
        return None
    def q(self, v):
        f = self.v2f(v); return struct.unpack_from('<Q', self.data, f)[0] if f is not None else None
    def d(self, v):
        f = self.v2f(v); return struct.unpack_from('<I', self.data, f)[0] if f is not None else None
    def cstr(self, v):
        f = self.v2f(v) if v else None
        if f is None: return None
        e = self.data.find(b'\0', f, f + 400)
        if e < 0: return None
        b = self.data[f:e]
        if not b or any(c < 32 or c > 126 for c in b): return None
        return b.decode('ascii')
    def in_text(self, v):
        n, va, vs, ro, rs = self.sec['.text']
        return self.base + va <= v < self.base + va + vs
    def func_of(self, v):
        r = v - self.base
        i = bisect.bisect_right(self.fstart, r) - 1
        if i >= 0 and self.fstart[i] <= r < self.fend[i]:
            return self.base + self.fstart[i], self.base + self.fend[i]
        return None
    def vtable(self, vt, n):
        out = []
        for k in range(n):
            p = self.q(vt + 8 * k)
            if p is None or not self.in_text(p): break
            out.append(p)
        return out

# ---- disassembly, as objdump's Intel text ---------------------------------------------
class Disasm:
    def __init__(self, pe, backend=None):
        self.pe = pe; self.cache = {}
        self.backend = backend or ('capstone' if self._have_capstone() else 'objdump' if shutil.which('objdump') else None)
        if self.backend is None:
            sys.exit('No disassembler found: install capstone (pip install capstone) or put GNU objdump on PATH.')
        if self.backend == 'capstone':
            import capstone
            self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    @staticmethod
    def _have_capstone():
        try:
            import capstone  # noqa
            return True
        except Exception:
            return False
    def prepare(self, starts):
        """disassemble these functions (by pdata bounds) into the cache"""
        ranges = []
        for s in set(starts):
            f = self.pe.func_of(s)
            if f and f[0] == s: ranges.append(f)
            elif f: ranges.append((s, f[1]))
            else: ranges.append((s, s + 0x40))        # a leaf function: no unwind entry
        ranges.sort()
        if self.backend == 'capstone':
            for s, e in ranges:
                if s in self.cache: continue
                fo = self.pe.v2f(s)
                code = self.pe.data[fo:fo + (e - s)]
                self.cache[s] = [(i.address, norm(i.address, i.size, i.mnemonic, i.op_str)) for i in self.md.disasm(code, s)]
            return
        # objdump: one pass over .text, keep what falls inside the ranges (they may overlap)
        ranges = [(s, e) for s, e in ranges if s not in self.cache]
        if not ranges: return
        for s, e in ranges: self.cache[s] = []
        cmd = ['objdump', '-d', '-M', 'intel', '--no-show-raw-insn', '-j', '.text', self.pe.path]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors='replace', bufsize=1 << 20)
        pat = re.compile(r'^\s+([0-9a-f]+):\t(.*)$')
        k = 0; active = []
        for line in p.stdout:
            m = pat.match(line)
            if not m: continue
            a = int(m.group(1), 16)
            while k < len(ranges) and ranges[k][0] <= a:
                active.append(ranges[k]); k += 1
            if active:
                active = [r for r in active if a < r[1]]
                t = m.group(2).rstrip()
                for s, e in active:
                    if a >= s: self.cache[s].append((a, t))
            elif k >= len(ranges):
                break
        p.stdout.close(); p.wait()

    def func(self, s):
        if s not in self.cache:
            if self.backend == 'objdump': self._single(s)
            else: self.prepare([s])
        L = self.cache.get(s, [])
        f = self.pe.func_of(s)
        if not f or f[0] != s:            # a leaf without unwind data: it ends at its first ret
            for i, (a, t) in enumerate(L):
                if t.startswith('ret') or t.startswith('jmp'):
                    return L[:i + 1]
        return L
    def _single(self, s):
        f = self.pe.func_of(s)
        e = f[1] if f else s + 0x40
        out = subprocess.run(['objdump', '-d', '-M', 'intel', '--no-show-raw-insn', '--start-address=%#x' % s,
                              '--stop-address=%#x' % e, self.pe.path], capture_output=True, text=True, errors='replace').stdout
        self.cache[s] = [(int(m.group(1), 16), m.group(2).rstrip()) for m in re.finditer(r'^\s+([0-9a-f]+):\t(.*)$', out, re.M)]

_SIZE = {'qword ptr': 'QWORD PTR', 'dword ptr': 'DWORD PTR', 'word ptr': 'WORD PTR', 'byte ptr': 'BYTE PTR',
         'xmmword ptr': 'XMMWORD PTR', 'ymmword ptr': 'YMMWORD PTR', 'tbyte ptr': 'TBYTE PTR'}
def norm(addr, size, mn, ops):
    """capstone text -> objdump's Intel syntax (what the emulator below reads)"""
    target = None
    m = re.search(r'\[rip ([+-]) (0x[0-9a-f]+|\d+)\]', ops)
    if m:
        disp = int(m.group(2), 0) * (1 if m.group(1) == '+' else -1)
        target = addr + size + disp
    for k, v in _SIZE.items(): ops = ops.replace(k, v)
    ops = re.sub(r' ([+-]) ', r'\1', ops)
    ops = re.sub(r'(?<![\w.$])(\d+)(?![\w.])', lambda m: hex(int(m.group(1))), ops)
    ops = ops.replace(', ', ',')
    text = mn + ('    ' + ops if ops else '')
    if target is not None: text += '        # 0x%x' % target
    return text

# ---- a tiny register tracker over one function -------------------------------------
R64 = {'rax': 'rax', 'eax': 'rax', 'ax': 'rax', 'al': 'rax', 'rbx': 'rbx', 'ebx': 'rbx', 'rcx': 'rcx', 'ecx': 'rcx', 'cl': 'rcx',
       'rdx': 'rdx', 'edx': 'rdx', 'dl': 'rdx', 'rsi': 'rsi', 'esi': 'rsi', 'sil': 'rsi', 'rdi': 'rdi', 'edi': 'rdi', 'dil': 'rdi',
       'rbp': 'rbp', 'ebp': 'rbp', 'bpl': 'rbp', 'rsp': 'rsp'}
for _k in range(8, 16):
    for _s in ('', 'd', 'w', 'b'): R64['r%d%s' % (_k, _s)] = 'r%d' % _k
VOL = ('rax', 'rcx', 'rdx', 'r8', 'r9', 'r10', 'r11')
_MEM = re.compile(r'\[(\w+)(?:([+-])0x([0-9a-f]+))?\]$')
_PTR = re.compile(r'^(QWORD|DWORD|WORD|BYTE) PTR ')
def parse_mem(s):
    m = _MEM.match(s)
    if not m: return None
    b = R64.get(m.group(1))
    if b is None: return None
    d = int(m.group(3), 16) if m.group(3) else 0
    return b, (-d if m.group(2) == '-' else d)

def run(insns, init):
    """walk a function's instructions straight through; -> events (calls and stores) with register state"""
    st = dict(init); stack = {}; ev = []
    for a, s in insns:
        parts = s.split(None, 1); mn = parts[0]; args = parts[1] if len(parts) > 1 else ''
        comment = None
        if '# 0x' in args:
            args, c = args.split('# 0x', 1); comment = int(c.split()[0], 16); args = args.strip()
        if mn in ('jmp', 'rex.W') and 'PTR [' in args:
            pm = parse_mem(_PTR.sub('', args.replace('jmp', '').strip()))
            if pm: ev.append(('call', a, ('vcall', st.get(pm[0]), pm[1]), dict(st), dict(stack)))
        ps = [_PTR.sub('', p.strip()) for p in args.split(',')] if args else []
        if mn == 'mov' and len(ps) == 2:
            dst, src = ps; v = None
            if src.startswith('0x'):
                try: v = ('imm', int(src, 16))
                except ValueError: v = None
            elif src in R64: v = st.get(R64[src])
            elif src.startswith('['):
                pm = parse_mem(src)
                if pm and pm[0] == 'rsp': v = stack.get(pm[1])
                elif pm:
                    bv = st.get(pm[0])
                    if bv and bv[0] == 'tag': v = ('deref', bv[1], bv[2]) if pm[1] == 0 else ('load', bv[1], bv[2] + pm[1])
            if dst in R64: st[R64[dst]] = v
            elif dst.startswith('['):
                pm = parse_mem(dst)
                if pm and pm[0] == 'rsp': stack[pm[1]] = v
                elif pm:
                    bv = st.get(pm[0])
                    if bv and bv[0] == 'tag': ev.append(('store', a, (bv[1], bv[2] + pm[1]), v))
        elif mn == 'lea' and len(ps) == 2:
            dst, src = ps; v = None
            if comment is not None and 'rip' in src: v = ('ptr', comment)
            else:
                pm = parse_mem(src)
                if pm:
                    bv = st.get(pm[0])
                    if bv and bv[0] == 'imm': v = ('imm', (bv[1] + pm[1]) & 0xffffffff)
                    elif bv and bv[0] == 'tag': v = ('tag', bv[1], bv[2] + pm[1])
            if dst in R64: st[R64[dst]] = v
        elif mn == 'xor' and len(ps) == 2 and ps[0] == ps[1] and ps[0] in R64:
            st[R64[ps[0]]] = ('imm', 0)
        elif mn == 'or' and len(ps) == 2 and ps[0] in R64 and ps[1] == '0xffffffffffffffff':
            st[R64[ps[0]]] = ('imm', 0xffffffff)
        elif mn == 'call':
            if args.startswith('0x'): tgt = ('direct', int(args, 16))
            else:
                pm = parse_mem(_PTR.sub('', args))
                tgt = ('vcall', st.get(pm[0]) if pm else None, pm[1] if pm else None)
            ev.append(('call', a, tgt, dict(st), dict(stack)))
            for r in VOL: st[r] = None
            stack = {}
        elif ps and ps[0] in R64 and mn not in ('cmp', 'test', 'push', 'nop') and not mn.startswith('j'):
            st[R64[ps[0]]] = None
    return ev

# ---- the extraction ------------------------------------------------------------------
class Extractor:
    def __init__(self, exe, backend=None, log=print):
        self.pe = PE(exe); self.log = log
        self.dis = Disasm(self.pe, backend)

    def rtti(self):
        pe = self.pe; d = pe.data
        n, va, vs, ro, rs = pe.sec['.rdata']
        u = array('I'); u.frombytes(d[ro:ro + rs // 4 * 4])
        cols = {}
        for i in range(len(u) - 6):
            if u[i] != 1 or u[i + 5] != va + i * 4: continue
            f = pe.v2f(pe.base + u[i + 3] + 16)
            if f is None or d[f:f + 4] not in (b'.?AV', b'.?AU'): continue
            e = d.index(b'\0', f)
            cols[pe.base + va + i * 4] = (d[f + 4:e - 2].decode('latin1'), u[i + 1])
        qd = array('Q'); qd.frombytes(d[ro:ro + rs // 8 * 8])
        vts = {}
        for i, x in enumerate(qd):
            c = cols.get(x)
            if c and c[1] == 0:
                vts.setdefault(c[0], pe.base + va + i * 8 + 8)
        return vts

    def ret_imm(self, fn):
        L = self.dis.func(fn)[:2]
        if len(L) == 2 and L[1][1].startswith('ret'):
            m = re.match(r'mov\s+eax,0x([0-9a-f]+)$', L[0][1])
            if m: return int(m.group(1), 16)
            if re.match(r'xor\s+eax,eax$', L[0][1]): return 0
        return None
    def ret_str(self, fn):
        L = self.dis.func(fn)[:2]
        if len(L) == 2 and L[1][1].startswith('ret') and '# 0x' in L[0][1] and L[0][1].startswith('lea'):
            return self.pe.cstr(int(L[0][1].rsplit('# 0x', 1)[1].split()[0], 16))
        return None

    def classes(self, vts):
        pe = self.pe
        tables = {n: pe.vtable(vt, 40) for n, vt in vts.items()}
        small = set()
        for e in tables.values():
            for i in (6, 7, 12, 14, 31):
                if len(e) > i: small.add(e[i])
        self.dis.prepare(small)
        C = {}
        for n, e in tables.items():
            if len(e) < 15: continue
            cid = self.ret_imm(e[6]); par = self.ret_imm(e[7]); nm = self.ret_str(e[12])
            if cid is None or nm is None: continue
            if cid in C and C[cid]['name'] != nm and n != nm: continue
            C[cid] = dict(name=nm, parent=par, ser=e[2], props=e[4], rtti=n,
                          label=self.ret_str(e[31]) if len(e) > 31 else None)
        return C

    def find_scope_fn(self, C):
        """the 'begin class scope' helper every scoped serializer calls first with r9d = its class id"""
        votes = {}
        sample = [ (cid, v) for cid, v in C.items() ][:3000]
        self.dis.prepare(v['ser'] for _, v in sample)
        for cid, v in sample:
            L = self.dis.func(v['ser'])[:12]
            seen = False
            for a, s in L:
                if s.startswith('mov') and re.search(r'r9d,0x%x$' % cid, s): seen = True
                elif seen and s.startswith('call') and re.match(r'call\s+0x[0-9a-f]+$', s):
                    t = int(s.split()[-1], 16); votes[t] = votes.get(t, 0) + 1; break
        return max(votes, key=votes.get) if votes else None

    def serializer(self, fn, scope_fn):
        pe = self.pe
        ev = run(self.dis.func(fn), {'rcx': ('tag', 'this', 0), 'rdx': ('tag', 'stream', 0)})
        def imm(v): return v[1] if v and v[0] == 'imm' else None
        def sname(v): return pe.cstr(v[1]) if v and v[0] == 'ptr' else None
        cid = None; fields = []
        for e in ev:
            if e[0] != 'call': continue
            _, a, tgt, st, sk = e
            if tgt[0] == 'direct' and tgt[1] == scope_fn and cid is None:
                cid = imm(st.get('r9')); continue
            if tgt[0] == 'vcall' and tgt[1] == ('deref', 'stream', 0):
                rdx = st.get('rdx'); off = rdx[2] if rdx and rdx[0] == 'tag' and rdx[1] == 'this' else None
                fid = nm = dflt = None
                # (field id, name, default value) sit in different argument slots per stream method
                for fv, nv, dv in ((st.get('r9'), sk.get(0x20), st.get('r8')), (st.get('r8'), st.get('r9'), None),
                                   (sk.get(0x20), sk.get(0x28), st.get('r9')), (sk.get(0x28), sk.get(0x30), None)):
                    f = imm(fv); n = sname(nv)
                    if f is not None and f < 0x10000 and n: fid, nm, dflt = f, n, imm(dv); break
                if fid is None:
                    f = imm(st.get('r9'))
                    if f is not None and f < 0x10000: fid = f; dflt = imm(st.get('r8'))
                fields.append((fid, nm, str(tgt[2]), off, dflt))
            elif tgt[0] == 'direct' and st.get('rcx') == ('tag', 'stream', 0):
                rdx = st.get('rdx'); off = rdx[2] if rdx and rdx[0] == 'tag' and rdx[1] == 'this' else None
                fid = imm(st.get('r8')); nm = sname(st.get('r9'))
                if fid is None or fid > 0xffffff or nm is None:
                    fid = imm(st.get('r9')); nm = sname(sk.get(0x20))
                fields.append((fid, nm, 'fn%x' % tgt[1], off, None))
            elif tgt[0] == 'direct':
                rcx = st.get('rcx'); rdx = st.get('rdx')
                if rdx == ('tag', 'stream', 0) and rcx and rcx[0] == 'tag' and rcx[1] == 'this' and rcx[2] != 0:
                    fields.append((imm(st.get('r8')), None, 'h%x' % tgt[1], rcx[2], None))
        return cid, fields

    def props(self, fn):
        """editor property names: label -> (member offset, property type id)"""
        pe = self.pe
        L = self.dis.func(fn)
        thisreg = None; regs = {}; lastname = None; awaiting = False; out = {}
        for k, (a, s) in enumerate(L):
            m = re.match(r'mov\s+(\w+),rcx$', s)
            if m and thisreg is None and k < 15: thisreg = m.group(1); continue
            m = re.search(r'lea\s+(\w+),\[rip[+-]0x[0-9a-f]+\]\s+# 0x([0-9a-f]+)', s)
            if m: regs[m.group(1)] = int(m.group(2), 16); continue
            m = re.match(r'mov\s+(r9d|r9),0x([0-9a-f]+)$', s)
            if m: regs['r9'] = int(m.group(2), 16); continue
            m = re.match(r'mov\s+DWORD PTR \[rsp\+0x20\],0x([0-9a-f]+)$', s)
            if m: regs['s20'] = int(m.group(1), 16); continue
            if s.startswith('call') and self._strcmp and s.endswith('0x%x' % self._strcmp):
                lastname = pe.cstr(regs.get('rdx', 0)); awaiting = True; continue
            if s.startswith('call') and self._addprop and s.endswith('0x%x' % self._addprop):
                nm = pe.cstr(regs.get('rdx', 0))
                if nm:
                    out.setdefault(nm, {})['type'] = regs.get('r9')
                    if regs.get('s20', 0) > 0xffff: out[nm]['enum'] = regs['s20']
                regs.pop('s20', None)
                continue
            if awaiting and thisreg and lastname:
                m = re.search(r'\[%s\+0x([0-9a-f]+)\]' % thisreg, s)
                if m: out.setdefault(lastname, {}).setdefault('off', int(m.group(1), 16)); awaiting = False
                elif s.startswith('call') or s.startswith('cmp    DWORD PTR [rbx]') or s.startswith('cmp DWORD PTR [rbx]'): awaiting = False
        return out

    def find_prop_fns(self, C):
        """the string compare and 'describe property' helpers the property functions call"""
        cnt = {}
        fns = list({v['props'] for v in C.values()})[:400]
        self.dis.prepare(fns)
        for fn in fns:
            for a, s in self.dis.func(fn):
                m = re.match(r'call\s+0x([0-9a-f]+)$', s)
                if m: t = int(m.group(1), 16); cnt[t] = cnt.get(t, 0) + 1
        top = sorted(cnt, key=cnt.get, reverse=True)[:6]
        strcmp = addprop = None
        # the describe helper takes a description in r8 and a type id in r9d; strcmp takes (rcx, rdx) only
        for t in top:
            for fn in fns[:200]:
                L = self.dis.func(fn)
                for i, (a, s) in enumerate(L):
                    if s.endswith('0x%x' % t) and s.startswith('call'):
                        prev = ' '.join(x[1] for x in L[max(0, i - 8):i])
                        if 'r9d,0x' in prev and 'lea    r8' in prev.replace('lea r8', 'lea    r8'): addprop = addprop or t
                        break
        for t in top:
            if t != addprop: strcmp = t; break
        return strcmp, addprop

    def member_types(self, vts, C):
        """class name -> {member offset: class name}, from constructors storing vtables"""
        pe = self.pe; d = pe.data
        vt_name = {vt: n for n, vt in vts.items()}
        n, va, vs, ro, rs = pe.sec['.text']
        text = d[ro:ro + vs]
        lea = re.compile(rb'[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d](....)', re.S)
        funcs = set()
        for m in lea.finditer(text):
            disp = struct.unpack('<i', m.group(1))[0]
            tgt = pe.base + va + m.end() + disp
            if tgt in vt_name:
                f = pe.func_of(pe.base + va + m.start())
                if f: funcs.add(f[0])
        self.log('  %d constructor candidates' % len(funcs))
        self.dis.prepare(funcs)
        ctor_of = {}; members = {}; calls = {}
        for fs in funcs:
            ev = run(self.dis.func(fs), {'rcx': ('tag', 'this', 0)})
            st0 = None; mem = {}; cl = []
            for e in ev:
                if e[0] == 'store':
                    _, a, (tag, off), v = e
                    if tag == 'this' and v and v[0] == 'ptr' and v[1] in vt_name:
                        if off == 0: st0 = vt_name[v[1]]
                        else: mem[off] = vt_name[v[1]]
                elif e[0] == 'call':
                    _, a, tgt, st, sk = e
                    rcx = st.get('rcx')
                    if tgt[0] == 'direct' and rcx and rcx[0] == 'tag' and rcx[1] == 'this': cl.append((rcx[2], tgt[1]))
            if st0: ctor_of[fs] = st0
            if mem: members[fs] = mem
            if cl: calls[fs] = cl
        mt = {}
        for fs, cls in ctor_of.items():
            dd = mt.setdefault(cls, {})
            for off, c in members.get(fs, {}).items(): dd[off] = c
            for off, t in calls.get(fs, []):
                if off > 0 and t in ctor_of: dd.setdefault(off, ctor_of[t])
        return mt

    def enums(self):
        """enum id -> {value: name}, from the registration calls (mov r9d,count; lea r8,table; lea rdx,name; mov ecx,id; call)"""
        pe = self.pe; d = pe.data
        n, va, vs, ro, rs = pe.sec['.text']
        text = d[ro:ro + vs]
        pat = re.compile(rb'\x41\xb9(....)\x4c\x8d\x05(....)\x48\x8d\x15(....)\xb9(....)\xe8', re.S)
        out = {}
        for m in pat.finditer(text):
            cnt = struct.unpack('<I', m.group(1))[0]
            if not 0 < cnt < 500: continue
            at = pe.base + va + m.start()
            table = at + 6 + 7 + struct.unpack('<i', m.group(2))[0]
            eid = struct.unpack('<I', m.group(4))[0]
            vals = {}
            for k in range(cnt):
                e = table + 24 * k
                nm = pe.cstr(pe.q(e)); v = pe.d(e + 8)
                if nm is None or v is None: vals = None; break
                vals[str(v)] = nm
            if vals: out['%x' % eid] = vals
        return out

    def trigger_registry(self):
        pe = self.pe; d = pe.data
        qd = array('Q'); qd.frombytes(d[:len(d) // 8 * 8])
        start = None
        evs = {pe.f2v(m.start() + 1) for m in re.finditer(b'\0Event\0', d)} - {None}
        pos = [i for i, x in enumerate(qd) if x in evs]
        for ev in evs:
            for i in [i for i in pos if qd[i] == ev][:50]:
                a = pe.f2v(i * 8)
                if a and pe.d(a + 8) == 0 and pe.cstr(pe.q(a + 32)) == 'Condition' and pe.d(a + 40) == 1:
                    start = a; break
            if start: break
        if not start: return {}
        types = {}; a = start
        while pe.cstr(pe.q(a)):
            name = pe.cstr(pe.q(a)); tid = pe.d(a + 8); cnt = pe.d(a + 16); ft = pe.q(a + 24)
            funcs = {}
            if ft and cnt < 5000:
                for k in range(cnt):
                    e = ft + 48 * k
                    fn = pe.cstr(pe.q(e + 8))
                    if fn is None: break
                    funcs[str(pe.d(e + 24))] = [fn, pe.cstr(pe.q(e)), pe.d(e + 28)]
            types[str(tid)] = [name, funcs]
            a += 32
        return types

    def build(self):
        t0 = time.time()
        self.log('reading class tables (RTTI)')
        vts = self.rtti()
        self.log('  %d classes with vtables' % len(vts))
        C = self.classes(vts)
        self.log('  %d registered classes' % len(C))
        scope_fn = self.find_scope_fn(C)
        self.log('disassembling serializers (%s)' % self.dis.backend)
        self.dis.prepare({v['ser'] for v in C.values()} | {v['props'] for v in C.values()})
        self._strcmp, self._addprop = self.find_prop_fns(C)
        ser = {}; prop = {}
        for v in C.values():
            if v['ser'] not in ser:
                try: ser[v['ser']] = self.serializer(v['ser'], scope_fn)
                except Exception: ser[v['ser']] = (None, [])
            if v['props'] not in prop:
                try: prop[v['props']] = self.props(v['props'])
                except Exception: prop[v['props']] = {}
        self.log('reading constructors for member types')
        MT = self.member_types(vts, C)
        n2c = {v['name']: k for k, v in C.items()}
        db = {}
        for cid, v in C.items():
            par = v['parent']; pv = C.get(par)
            scope, fields = ser[v['ser']]
            own = not (pv and pv['ser'] == v['ser'])
            f = {}
            if own:
                for fid, nm, slot, off, dflt in fields:
                    if fid is None: continue
                    f[str(fid)] = [nm, slot, off] + ([dflt] if dflt not in (None, 0) else [])
            dd = {}
            if not (pv and pv['props'] == v['props']):
                for nm, p in prop[v['props']].items():
                    if 'off' in p: dd[str(p['off'])] = [nm, p.get('type')] + (['%x' % p['enum']] if p.get('enum') else [])
            mt = {str(o): '%x' % n2c[c] for o, c in MT.get(v['name'], {}).items() if c in n2c}
            db['%x' % cid] = dict(n=v['name'], m=mt, l=v.get('label'), p='%x' % par if par else None,
                                  sc=(scope == cid), f=f, d=dd, own=own)
        # enum type of each enum field: from its editor property, else from a field of the same name elsewhere
        byname = {}
        for k, v in db.items():
            offs = {}
            for o, x in v['d'].items():
                if len(x) > 2: offs[int(o)] = x[2]
            e = {}
            for fid, x in v['f'].items():
                if x[2] is not None and x[2] in offs:
                    e[fid] = offs[x[2]]; byname.setdefault(x[0], set()).add(offs[x[2]])
            v['e'] = e
        for k, v in db.items():
            for fid, x in v['f'].items():
                if fid not in v['e'] and x[1] in ('56', '64', '120', '128') and len(byname.get(x[0], ())) == 1:
                    v['e'][fid] = next(iter(byname[x[0]]))
        self.log('reading the trigger script registry')
        db['_trigger'] = self.trigger_registry()
        db['_enums'] = self.enums()
        self.log('  %d enum types' % len(db['_enums']))
        db['_meta'] = {'exe': os.path.basename(self.pe.path), 'size': len(self.pe.data),
                       'mtime': int(os.path.getmtime(self.pe.path)), 'backend': self.dis.backend,
                       'built': time.strftime('%Y-%m-%d %H:%M')}
        self.log('done in %.0fs: %d classes, %d fields' % (time.time() - t0, len(C), sum(len(x['f']) for k, x in db.items() if not k.startswith('_'))))
        return db

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--exe', required=True, help='ResourceED.exe from the game folder')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema_db.json'))
    ap.add_argument('--backend', choices=['capstone', 'objdump'])
    a = ap.parse_args()
    db = Extractor(a.exe, a.backend).build()
    json.dump(db, open(a.out, 'w'), separators=(',', ':'))
    print('wrote', a.out)

if __name__ == '__main__':
    main()
