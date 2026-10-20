# AoW4 diff tools: notes for Claude

Tools that read Age of Wonders 4 game data (`.rpk` packs) and a mod's override file (`.acp`) and
write interactive HTML reports. Read `README.md` for usage. This file holds what is not obvious
from the code: the file formats, the editor's meaning of things, and decisions already made.

## The user and the mod

- The user maintains the mod **AoW 4 Evolved** (~1,540 overrides plus its own packs under the mod's
  `Packs/` folder and its text in `Language/AoW 4 Evolved.po`). They edit it in Paradox's
  **ResourceED** editor. Folder paths are in `config.json` (not in Git).
- They know the editor and the game well; trust their account of editor behaviour over a guess,
  and check the data before telling them something is wrong. Explain findings in editor terms
  (field names as ResourceED shows them, "unmod / mod again", "Localized String Dialog").
- After every game update they run `after_update.bat`: schema rebuild if ResourceED.exe changed,
  update report (latest snapshot vs live), mod report, new snapshot. Keep at least the two newest
  snapshots: the previous one is the only way to rebuild the last update report.

## Never commit game data

`snapshots/`, `reports/`, `cache/` hold or are built from Paradox's files and are git-ignored.
`schema_db.json` is committed (field names read out of ResourceED.exe, needed to run). If the repo
goes public, that one is a judgement call the user makes.

## File formats

- **Record**: `[count byte][(fid, off) pairs][bodies]`. High bit in count: a u32 count of
  (u32 fid, u32 off) pairs follows. A body of one `\0` byte is an empty record/list.
  Polymorphic object = `[u32 class id][record]`. Class scopes are nested records keyed by class id
  (fid > 0xffff). **Fields at their default value are not written at all.**
- **.rpk**: header 0xb0; top record field 13 = categories `[cid]{50: FID, 51: lib}`; lib field 21 →
  `{1: {i: item}}`. Resource head fields: 20 FName, 21 FUserName, 24 FID (lo, hi → id hex
  `hi8 + lo8`). Strings are u32 length + UTF-8.
- **.acp**: `CPR\0` + 12 bytes; TOP at 0x24; TOP field 38 = pack blocks (cid 0x401204) each
  `{1: pack name, 2: {1: overrides}}`; override (cid 0x401206) `{1: raw copy, 2: category,
  3: '[#ID]cat\row'}`. **No base copy and no timestamp**: nothing says when or from which build an
  override was copied.
- **Text**: game `Language/EN/EN.MO`; mod `.po` (read the .po, it is what the user edits).

## The schema (`extract_schema.py` → `schema_db.json`)

Read from ResourceED.exe via RTTI vtables: vt[2] save, vt[4] editor props, vt[6] cid, vt[7] parent,
vt[12] name. Props give display names, offsets, enum ids; constructors give member classes; the
trigger registry gives script function names; enum tables give enum value names. Per class:
`f` {fid: [name, kind, off, default?]}, `d` {off: [display, type, enum?]}, `m` member types,
`e` enums. Fields the scan cannot name show as `field_N`; `decode.FIELD_LABELS` names a few by hand
(confirmed with the user from editor screenshots: TCombatDefenseBlock 1 Armor 2 Resistance;
TCombatDamageBlock 1..6 Physical, Blight, Shock, Fire, Spirit, Frost).

## Editor and game semantics (confirmed by the user)

- A missing field means its **default**, which is not always 0: compare against the schema default
  (shown as "(default)"). This bit us once.
- **-1 is not "empty"** in general: Armor/Resistance -1 subtracts; only some fields (e.g. Mind
  Control duration) use -1 for infinite.
- **Tooltips** (`TELocalizedStringLink`: msgid + key/value list). A `{placeholder}` is filled from a
  typed-in value, else from a **data store**: the DataStoreTag of the resource or of resources it
  links to (`{TAG>Field}`). Keys should only be typed in when a value can't be resolved or is a
  manual one (`{amount}`, `{tier}`). `{GLOBAL.…}`/`{LOC.…}` always resolve. The Localized String
  Dialog rebuilds the key list from the text: **clicking Ok + save drops leftover keys**, and it
  does not show leftover keys at all. Text inside trigger scripts (predictions etc.) is filled by
  the script's own Text Variables: skip it.
- Keys left over in the list are harmless (the game ships 170+ itself).
- **Unmod, mod again, re-apply edits** refreshes an override to the current game. It is the fix for
  stale copies (e.g. a Lost Tomb event copy still carried a prediction the game had removed).
- Removing the last entry of a list saves it as an empty list (`\0` body), not as a missing field.

## Code map

- `aow4lib.py` record parsing, `Schema`. `decode.py` `Decoder` → nodes: `('o', cls, [(label,
  owner, node)])`, `('l', items)`, `('v', text[, default])`, `('r', name, id)`, `('t', tags)`,
  `('f', tagfilter)`, `('x', bytes)`. `render()` prints a node tree.
- `differ.py` compares node trees (keyed list matching, defaults, cosmetic labels). `script.py`
  renders trigger scripts as pseudo-code and diffs them.
- `acpread.py` reads overrides. `aow4diff.py` commands, `Model`, `cmd_mod`, `cmd_update`,
  `mark_unmerged` (labels mod differences that undo a change listed in an update report in
  `reports/`).
- `tooltips.py` tooltip key checks. `stale.py` dead links + "entries only your copy has".
- `template.html` the viewer; the report data is injected as JSON at `__DATA__`. Mod-report
  sections: Not merged, Tooltip keys, Possibly old content, Deleted, No target, Layout-only,
  Repeated, Overrides.

## Working on it

- Check a resource with `python aow4diff.py dump "<name>" [--acp "<mod.acp>"]`; diff the two dumps.
- After a change, regenerate with `python aow4diff.py mod` and open the report; check the browser
  console for errors. A whole mod report takes ~30-45 s.
- `AoW 4 Evolved.acp.bak` (in the mod folder) is an older copy of the mod: a useful regression
  input (`--acp ... --out _dev/bak.html`), e.g. it still has the stale Lost Tomb prediction.
- `_dev/` is git-ignored scratch (test scripts, backups).
- Reports must make sense to the user without code knowledge: plain section text, editor names.

## Open ideas the user has not taken up yet

- Old builds via Steam depot downloads to date each override and list game additions a copy lacks.
- A browser version for other modders (GitHub Pages + Pyodide, user picks their own game folder).
