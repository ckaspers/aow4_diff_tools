"""Trigger scripts (TTriggerSettings) as text, so a changed script reads as a line diff.

Type and function names come from the trigger registry in ResourceED.exe (schema_db.json "_trigger")."""
import difflib

SECTIONS = ('Events', 'Conditions', 'Actions', 'Setups')
REG = {}        # type id -> (type name, {function id: [name, category, n params]})
STATEMENT_TYPES = {0, 1, 2, 3, 107}         # Event, Condition, Setup, Action, Input
OPS = {'0': '==', '1': '!=', '2': '<', '3': '<=', '4': '>', '5': '>='}

def fields(node):
    d = {}
    if node and node[0] == 'o':
        for lab, own, v in node[2]:
            d.setdefault(lab, v)
    return d

def val(v):
    if v is None: return None
    if v[0] == 'v': return v[1]
    if v[0] == 'r': return repr(v[1])
    if v[0] == 't': return '{' + ', '.join(v[1]) + '}'
    if v[0] == 'o' and not v[2]: return None
    return None

def children(node):
    dm = fields(node).get('DataMap')
    objs = fields(dm).get('Objects') if dm else None
    return objs[1] if objs and objs[0] == 'l' else []

def is_list(node):
    return node and node[0] == 'o' and node[1] == 'TTriggerDataList'

def names(node):
    f = fields(node)
    try: tid = int(val(f.get('TypeID')) or 0)
    except ValueError: tid = 0
    try: fid = int(val(f.get('FunctionID')) or 0)
    except ValueError: fid = 0
    tname, funcs = REG.get(tid, ('T%d' % tid, {}))
    fn = funcs.get(fid)
    return tid, tname, (fn[0] if fn else ('F%d' % fid if funcs else None))

def literal(node):
    """the value of a preset / link leaf, or None"""
    cls = node[1] or ''
    f = fields(node)
    for key in ('Value', 'Str', 'DataString', 'String', 'Alias', 'VariableName', 'Name'):
        if key in f:
            v = val(f[key])
            if v is not None: return v
    rl = f.get('ResourceLink')
    if rl is not None:
        if rl[0] == 'r': return repr(rl[1])
        rf = fields(rl)
        for key in ('UserName', 'Name'):
            v = val(rf.get(key))
            if v is not None: return v
        return "''"
    if 'TagSet' in cls or 'TagFilter' in cls:
        for lab, own, v in node[2]:
            if v[0] in ('t', 'v') and lab not in ('TypeID', 'ParamID', 'FunctionID'):
                return val(v)
        return '{}'
    if 'Preset' in cls:          # a preset at its default is not stored
        for k, d in (('Integer', '0'), ('Float', '0.0'), ('Boolean', 'false')):
            if k in cls: return d
        return "''"
    if cls == 'TTriggerDataEnum': return '0'
    return None

def join_args(kids):
    """arguments, with a comparison operator node between its two operands"""
    out = []
    i = 0
    while i < len(kids):
        k = kids[i]
        if k[0] == 'o' and k[1] == 'TTriggerDataComparison' and not children(k) and out and i + 1 < len(kids):
            op = OPS.get(val(fields(k).get('OperatorType')) or '0', '?')
            out[-1] = '%s %s %s' % (out[-1], op, expr(kids[i + 1]))
            i += 2; continue
        out.append(expr(k)); i += 1
    return out

def expr(node):
    if node is None: return '?'
    if node[0] != 'o': return val(node) or '?'
    cls = node[1] or ''
    tid, tname, fname = names(node)
    kids = children(node)
    if cls not in ('TTriggerData', 'TTriggerDataComparison', 'TTriggerDataList', '') and not kids:
        lit = literal(node)
        if lit is not None: return lit
    if cls == 'TTriggerDataComparison':
        f = fields(node)
        op = OPS.get(val(f.get('OperatorType')) or '0', '?')
        args = [expr(k) for k in kids]
        if len(args) == 2: return '%s %s %s' % (args[0], op, args[1])
    args = join_args([k for k in kids if not is_list(k)])
    if fname in ('Preset', 'Value') and len(args) == 1: return args[0]
    if len(args) == 1 and len(kids) == 3 and kids[1][0] == 'o' and kids[1][1] == 'TTriggerDataComparison':
        return args[0]
    if fname == 'ResourceLink' and len(args) == 1: return '%s[%s]' % (tname, args[0])
    if fname in (None, '') or fname == tname:
        return '%s[%s]' % (tname, ', '.join(args))
    if tid in STATEMENT_TYPES:
        return '%s[%s]' % (fname, ', '.join(args))
    return '%s[%s]' % (tname, ', '.join([fname] + args))

def block_lines(lst, ind, out):
    f = fields(lst)
    objs = fields(f.get('Objects')).get('Objects') if f.get('Objects') else None
    for it in (objs[1] if objs and objs[0] == 'l' else []):
        statement(it, ind, out)

def statement(it, ind, out):
    p = '    ' * ind
    if is_list(it):
        block_lines(it, ind, out); return
    tid, tname, fname = names(it)
    kids = children(it)
    blocks = [k for k in kids if is_list(k)]
    if fields(it).get('Enabled') and val(fields(it)['Enabled']) == 'false':
        p += '# (disabled) '
    if fname in ('IfThen', 'IfThenElse') and blocks:
        conds = []
        block_lines(blocks[0], 0, conds)
        out.append(p + 'if ' + (' and '.join(c.strip() for c in conds) or 'True') + ':')
        if len(blocks) > 1: block_lines(blocks[1], ind + 1, out)
        if len(blocks) > 2:
            out.append(p + 'else:')
            block_lines(blocks[2], ind + 1, out)
        return
    if (it[1] or '') == 'TTriggerDataComment':
        out.append(p + '# ' + (literal(it) or '').strip("'")); return
    if blocks:
        args = [expr(k) for k in kids if not is_list(k)]
        out.append(p + '%s[%s]:' % (fname or tname, ', '.join(args)))
        for b in blocks: block_lines(b, ind + 1, out)
        return
    out.append(p + expr(it))

def lines(section):
    out = []
    if section is not None: block_lines(section, 0, out)
    return out

def strip_hash(node):
    if node is None: return None
    k = node[0]
    if k == 'o':
        return ('o', node[1], [(l, o, strip_hash(v)) for l, o, v in node[2] if l != 'Hash'])
    if k == 'l':
        return ('l', [strip_hash(x) for x in node[1]])
    return node

def section_diff(name, old, new):
    """-> (kind, unified diff lines); kind: None, 'restamped', 'changed'"""
    if old == new: return None, []
    a = lines(old); b = lines(new)
    if a == b:
        if strip_hash(old) == strip_hash(new): return 'restamped', []
        return 'changed', []
    d = list(difflib.unified_diff(a, b, lineterm='', n=3))[2:]
    return 'changed', ['[%s]' % name.upper()] + d
